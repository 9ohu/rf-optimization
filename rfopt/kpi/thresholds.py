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
from pathlib import Path

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
