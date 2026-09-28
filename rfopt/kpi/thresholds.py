"""Configurable KPI thresholds.

A :class:`ThresholdRule` says, for one KPI, what counts as *warning* and what
counts as *critical*. Direction comes from the schema: for an "up" KPI the
value must stay **above** the threshold; for a "down" KPI it must stay
**below**. Thresholds live in editable YAML under ``config/`` so an operator
can tune them without touching code.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


class Severity(IntEnum):
    OK = 0
    WARNING = 1
    CRITICAL = 2

    @property
    def label(self) -> str:
        return {0: "OK", 1: "Warning", 2: "Critical"}[int(self)]


@dataclass
class ThresholdRule:
    kpi: str
    warning: float | None
    critical: float | None
    direction: str            # "up" | "down" (from schema, cached here)
    min_samples: int = 1      # ignore entities with less traffic/rows than this
    min_traffic_gb: float = 0.0
    unit: str = ""
    note: str = ""
    source: str = ""          # where the line comes from; empty = not confirmed

    @property
    def judged(self) -> bool:
        """Only a line with a documented source decides an Issue."""
        return bool(self.source) and self.warning is not None and self.critical is not None

    def evaluate(self, value: float) -> Severity:
        if value is None or value != value:      # NaN
            return Severity.OK
        if self.direction == "up":
            if self.critical is not None and value < self.critical:
                return Severity.CRITICAL
            if self.warning is not None and value < self.warning:
                return Severity.WARNING
        else:  # "down"
            if self.critical is not None and value > self.critical:
                return Severity.CRITICAL
            if self.warning is not None and value > self.warning:
                return Severity.WARNING
        return Severity.OK

    def target(self) -> float | None:
        """A reasonable 'good' value to quote as the recovery target."""
        return self.warning


@dataclass
class ThresholdSet:
    technology: str
    rules: dict[str, ThresholdRule]
    guard_traffic_gb: float = 0.0
    guard_min_rows: int = 1

    def rule(self, kpi: str) -> ThresholdRule | None:
        return self.rules.get(kpi)

    def with_overrides(self, overrides: dict[str, dict]) -> "ThresholdSet":
        """Return a copy with per-KPI overrides applied (from the UI)."""
        new = dict(self.rules)
        for kpi, vals in (overrides or {}).items():
            base = new.get(kpi)
            if base is None:
                continue
            new[kpi] = ThresholdRule(
                kpi=kpi,
                warning=vals.get("warning", base.warning),
                critical=vals.get("critical", base.critical),
                direction=base.direction,
                min_samples=vals.get("min_samples", base.min_samples),
                min_traffic_gb=vals.get("min_traffic_gb", base.min_traffic_gb),
                unit=base.unit,
                note=base.note,
                source=base.source,
            )
        return ThresholdSet(self.technology, new, self.guard_traffic_gb,
                            self.guard_min_rows)

    def as_records(self) -> list[dict]:
        return [
            {
                "kpi": r.kpi, "direction": r.direction, "unit": r.unit,
                "warning": r.warning, "critical": r.critical,
                "min_samples": r.min_samples, "min_traffic_gb": r.min_traffic_gb,
                "note": r.note,
            }
            for r in self.rules.values()
        ]


def judged_rule(kpi: str, technology: str = "LTE") -> ThresholdRule | None:
    """The rule a KPI is judged on, or None when its line has no documented
    source (then the KPI is shown, never judged)."""
    rule = load_thresholds(technology).rule(kpi)
    return rule if rule is not None and rule.judged else None


TDD = "CELL_TDD"


@lru_cache(maxsize=1)
def _tdd_rule() -> ThresholdRule | None:
    tdd = load_thresholds("LTE").rule("ul_rssi_tdd_dbm")
    return tdd if tdd is not None and tdd.judged else None


def rule_for_duplex(rule: ThresholdRule | None, duplex) -> ThresholdRule | None:
    """UL interference has its own line on a TDD cell (`ul_rssi_tdd_dbm`)."""
    if rule is None or rule.kpi != "ul_rssi_dbm" or str(duplex).strip().upper() != TDD:
        return rule
    return _tdd_rule() or rule


def _sev(rule: ThresholdRule, v: np.ndarray) -> np.ndarray:
    out = np.zeros(v.shape, dtype=np.int64)
    if rule.direction == "up":
        if rule.warning is not None:
            out[v < rule.warning] = 1
        if rule.critical is not None:
            out[v < rule.critical] = 2
    else:
        if rule.warning is not None:
            out[v > rule.warning] = 1
        if rule.critical is not None:
            out[v > rule.critical] = 2
    return out


def hourly_severity(rule: ThresholdRule, values, duplex=None) -> np.ndarray:
    """0 normal / 1 warning / 2 critical for every hourly value on its own —
    never an average. A missing value is 0. With `duplex`, a TDD cell's UL
    interference is read against the TDD line."""
    v = np.asarray(values, dtype=np.float64)
    with np.errstate(invalid="ignore"):
        out = _sev(rule, v)
        if duplex is not None and rule.kpi == "ul_rssi_dbm" and _tdd_rule() is not None:
            tdd = np.asarray(pd.Series(duplex).astype(str).str.strip().str.upper() == TDD)
            if tdd.any():
                out[tdd] = _sev(_tdd_rule(), v[tdd])
    out[~np.isfinite(v)] = 0
    return out


def load_thresholds(
    technology: str = "LTE",
    *,
    path: str | Path | None = None,
    overrides: dict[str, dict] | None = None,
) -> ThresholdSet:
    from rfopt.ingest.schema import kpi_def

    tech = technology.upper()
    p = Path(path) if path else _CONFIG_DIR / f"thresholds_{tech.lower()}.yaml"
    data = yaml.safe_load(Path(p).read_text(encoding="utf-8")) if Path(p).exists() else {}
    data = data or {}

    guard = data.get("guards", {}) or {}
    rules: dict[str, ThresholdRule] = {}
    for kpi, spec in (data.get("kpis", {}) or {}).items():
        d = kpi_def(kpi, tech)
        direction = (spec or {}).get("direction") or (d.direction if d else "up")
        if direction == "info":
            direction = "up"
        rules[kpi] = ThresholdRule(
            kpi=kpi,
            warning=(spec or {}).get("warning"),
            critical=(spec or {}).get("critical"),
            direction=direction,
            min_samples=(spec or {}).get("min_samples", 1),
            min_traffic_gb=(spec or {}).get("min_traffic_gb", 0.0),
            unit=(d.unit if d else (spec or {}).get("unit", "")),
            note=(spec or {}).get("note", ""),
            source=str((spec or {}).get("source") or ""),
        )

    ts = ThresholdSet(
        technology=tech,
        rules=rules,
        guard_traffic_gb=guard.get("min_traffic_gb", 0.0),
        guard_min_rows=guard.get("min_rows", 1),
    )
    if overrides:
        ts = ts.with_overrides(overrides)
    return ts
