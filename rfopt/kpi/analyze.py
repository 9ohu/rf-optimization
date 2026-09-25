"""Roll-ups and threshold evaluation over the canonical KPI frame."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from rfopt.ingest.schema import kpi_def, kpis_for
from rfopt.kpi.thresholds import Severity, ThresholdSet

_LEVEL_KEYS = {
    "cell": ["technology", "region", "site_id", "sector_id", "cell_id", "band"],
    "sector": ["technology", "region", "site_id", "sector_id"],
    "site": ["technology", "region", "site_id"],
}


# --------------------------------------------------------------------------- #
def _traffic_weight(df: pd.DataFrame, kpi: str, weight_kpi: str | None) -> pd.Series:
    if weight_kpi and weight_kpi in df.columns and df[weight_kpi].notna().any():
        w = df[weight_kpi].fillna(0.0).clip(lower=0)
        if w.sum() > 0:
            return w
    for cand in ("total_traffic_gb", "dl_traffic_gb", "rrc_setup_att",
                 "erab_setup_att", "ho_att"):
        if cand in df.columns and df[cand].fillna(0).sum() > 0:
            return df[cand].fillna(0.0).clip(lower=0)
    return pd.Series(1.0, index=df.index)


def _fallback_weight_col(df: pd.DataFrame) -> pd.Series | None:
    for cand in ("total_traffic_gb", "dl_traffic_gb", "rrc_setup_att",
                 "erab_setup_att", "ho_att"):
        if cand in df.columns and df[cand].fillna(0).sum() > 0:
            return df[cand].fillna(0.0).clip(lower=0)
    return None


def aggregate(
    df: pd.DataFrame,
    level: str = "cell",
    period: str = "day",
    technology: str | None = None,
) -> pd.DataFrame:
    """Aggregate to (level x period), vectorised.

    level   "cell" | "sector" | "site"
    period  "hour" | "day" | "all"     (time bucket for the roll-up)
    """
    if df.empty:
        return df.copy()
    tech = technology or (df["technology"].iloc[0] if "technology" in df else "LTE")
    keys = [k for k in _LEVEL_KEYS[level] if k in df.columns]

    work = df
    if period != "all" and "datetime" in work and work["datetime"].notna().any():
        bucket = (work["datetime"].dt.floor("h") if period == "hour"
                  else work["datetime"].dt.strftime("%a %H:00")
                  if period == "dow_hour"
                  else work["datetime"].dt.floor("D"))
        work = work.assign(_bucket=bucket)
        group_keys = keys + ["_bucket"]
    else:
        group_keys = keys

    kpi_names = [d.name for d in kpis_for(tech) if d.name in work.columns]
    by_how: dict[str, list[str]] = {}
    weighted: list[tuple[str, str | None]] = []
    for name in kpi_names:
        d = kpi_def(name, tech)
        how = d.aggregate if d else "mean"
        if how == "weighted":
            weighted.append((name, d.weight_kpi if d else None))
            by_how.setdefault("mean", [])          # mean fallback if no weight
        else:
            by_how.setdefault(how if how in ("sum", "max", "min") else "mean",
                              []).append(name)

    grp = work.groupby(group_keys, dropna=False, sort=False)
    parts: list[pd.DataFrame] = []
    for how, names in by_how.items():
        names = [n for n in names if n in work.columns]
        if not names:
            continue
        if how == "sum":
            parts.append(grp[names].sum(min_count=1))
        elif how == "max":
            parts.append(grp[names].max())
        elif how == "min":
            parts.append(grp[names].min())
        else:
            parts.append(grp[names].mean())
    out = pd.concat(parts, axis=1) if parts else grp.size().to_frame("n_rows")
    out["n_rows"] = grp.size()

    # weighted KPIs: sum(v*w)/sum(w) with a traffic fallback weight
    fb = _fallback_weight_col(work)
    for name, wcol in weighted:
        if name not in work.columns:
            continue
        w = None
        if wcol and wcol in work.columns and work[wcol].fillna(0).sum() > 0:
            w = work[wcol].fillna(0.0).clip(lower=0)
        elif fb is not None:
            w = fb
        if w is None:
            out[name] = grp[name].mean()
            continue
        gk = [work[k] for k in group_keys]
        num = (work[name] * w).groupby(gk, sort=False).sum(min_count=1)
        den = w.groupby(gk, sort=False).sum().replace(0, np.nan)
        wm = num / den
        plain = grp[name].mean()
        out[name] = wm.reindex(out.index).fillna(plain)

    out = out.reset_index()
    if "_bucket" in out.columns:
        out = out.rename(columns={"_bucket": "datetime"})
    # recompute composite accessibility if parts are present
    if {"rrc_setup_sr", "erab_setup_sr"} <= set(out.columns) and \
            "call_setup_sr" in out.columns:
        miss = out["call_setup_sr"].isna()
        out.loc[miss, "call_setup_sr"] = (
            out.loc[miss, "rrc_setup_sr"] / 100 *
            out.loc[miss, "erab_setup_sr"] / 100 * 100)
    return out.reset_index(drop=True)


# --------------------------------------------------------------------------- #
@dataclass
class KpiBreach:
    entity_level: str
    entity_id: str
    site_id: str
    kpi: str
    label: str
    value: float
    severity: Severity
    threshold: float
    direction: str
    unit: str
    period: str
    when: str = ""
    traffic_gb: float = 0.0
    trend: str = ""              # filled by anomaly module: "degrading" / ...
    n_rows: int = 0

    def as_dict(self) -> dict:
        return {
            "level": self.entity_level, "entity": self.entity_id,
            "site": self.site_id, "kpi": self.kpi, "label": self.label,
            "value": round(self.value, 3) if self.value == self.value else None,
            "severity": self.severity.label, "threshold": self.threshold,
            "unit": self.unit, "period": self.period, "when": self.when,
            "traffic_gb": round(self.traffic_gb, 3), "trend": self.trend,
        }


def evaluate_thresholds(
    agg_df: pd.DataFrame,
    thresholds: ThresholdSet,
    *,
    level: str = "cell",
    period: str = "day",
    only_kpis: list[str] | None = None,
) -> list[KpiBreach]:
    """Return every warning/critical breach in an aggregated frame (vectorised)."""
    if agg_df.empty:
        return []
    id_col = {"cell": "cell_id", "sector": "sector_id", "site": "site_id"}[level]
    df = agg_df
    traffic_col = "total_traffic_gb" if "total_traffic_gb" in df.columns else None
    traffic = (pd.to_numeric(df[traffic_col], errors="coerce").fillna(0.0)
               if traffic_col else pd.Series(0.0, index=df.index))
    n_rows = (pd.to_numeric(df["n_rows"], errors="coerce").fillna(0)
              if "n_rows" in df.columns else pd.Series(0, index=df.index))
    ids = df[id_col].astype(str) if id_col in df.columns else pd.Series("", index=df.index)
    sites = df["site_id"].astype(str) if "site_id" in df.columns else pd.Series("", index=df.index)
    when = (df["datetime"].astype(str) if "datetime" in df.columns
            else pd.Series("", index=df.index))

    breaches: list[KpiBreach] = []
    for kpi, rule in thresholds.rules.items():
        if kpi not in df.columns or (only_kpis and kpi not in only_kpis):
            continue
        val = pd.to_numeric(df[kpi], errors="coerce")
        ok_guard = (traffic >= max(rule.min_traffic_gb, thresholds.guard_traffic_gb)) \
            if traffic_col else pd.Series(True, index=df.index)
        ok_rows = (n_rows == 0) | (n_rows >= max(rule.min_samples,
                                                 thresholds.guard_min_rows))
        cand = val.notna() & ok_guard & ok_rows
        if not cand.any():
            continue
        if rule.direction == "up":
            crit = cand & (rule.critical is not None) & (val < (rule.critical
                   if rule.critical is not None else -np.inf))
            warn = cand & ~crit & (rule.warning is not None) & (val < (rule.warning
                   if rule.warning is not None else -np.inf))
        else:
            crit = cand & (rule.critical is not None) & (val > (rule.critical
                   if rule.critical is not None else np.inf))
            warn = cand & ~crit & (rule.warning is not None) & (val > (rule.warning
                   if rule.warning is not None else np.inf))
        d = kpi_def(kpi, thresholds.technology)
        for sev, mask, thr in ((Severity.CRITICAL, crit, rule.critical),
                               (Severity.WARNING, warn, rule.warning)):
            for i in df.index[mask.fillna(False)]:
                breaches.append(KpiBreach(
                    entity_level=level, entity_id=ids[i], site_id=sites[i],
                    kpi=kpi, label=d.label if d else kpi,
                    value=float(val[i]), severity=sev,
                    threshold=float(thr) if thr is not None else float("nan"),
                    direction=rule.direction, unit=rule.unit, period=period,
                    when=when[i] if when[i] not in ("NaT", "nan") else "",
                    traffic_gb=float(traffic[i]), n_rows=int(n_rows[i]),
                ))
    breaches.sort(key=lambda b: (-int(b.severity), -b.traffic_gb))
    return breaches


# --------------------------------------------------------------------------- #
def busy_hour_table(df: pd.DataFrame, by: str = "site") -> pd.DataFrame:
    """Identify the busy hour per entity (max total traffic, else PRB util)."""
    if df.empty or "datetime" not in df or df["datetime"].isna().all():
        return pd.DataFrame()
    metric = "total_traffic_gb" if "total_traffic_gb" in df.columns else \
        ("dl_traffic_gb" if "dl_traffic_gb" in df.columns else "dl_prb_util")
    if metric not in df.columns:
        return pd.DataFrame()
    id_col = {"cell": "cell_id", "sector": "sector_id", "site": "site_id"}[by]
    hourly = df.copy()
    hourly["hour"] = hourly["datetime"].dt.hour
    prof = (hourly.groupby([id_col, "hour"])[metric]
            .mean().reset_index())
    idx = prof.groupby(id_col)[metric].idxmax()
    bh = prof.loc[idx].rename(columns={"hour": "busy_hour",
                                       metric: f"bh_{metric}"})
    return bh.reset_index(drop=True)
