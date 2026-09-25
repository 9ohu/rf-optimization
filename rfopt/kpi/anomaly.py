"""Trend and anomaly detection on per-entity KPI time series.

For each (entity, KPI) series we compute:
  * a robust trend  (Theil-Sen slope over the window, % change end-to-end)
  * a level shift    (recent block mean vs baseline block mean, in robust sigmas)
  * point anomalies  (rolling-median + MAD outliers)
  * a verdict        relative to the KPI's "good" direction:
        degrading | improving | stable | volatile | step-down | step-up
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from rfopt.ingest.schema import kpi_def, kpis_for

try:                                    # scipy is a hard dep, but stay safe
    from scipy.stats import theilslopes
except Exception:                       # pragma: no cover
    theilslopes = None

# Headline KPIs worth trending by default (skip raw counts / volumes).
TREND_KPIS: dict[str, list[str]] = {
    "LTE": ["rrc_setup_sr", "erab_setup_sr", "call_setup_sr", "rach_setup_sr",
            "erab_drop_rate", "ctxt_drop_rate", "ho_sr", "intra_freq_ho_sr",
            "inter_freq_ho_sr", "ho_ping_pong_rate", "dl_user_thr_mbps",
            "ul_user_thr_mbps", "dl_latency_ms", "avg_rsrp_dbm", "avg_rsrq_db",
            "avg_sinr_db", "avg_cqi", "dl_bler", "pct_rsrp_poor",
            "dl_qpsk_ratio", "ta_p95_m", "dl_prb_util", "ul_prb_util",
            "pdcch_util", "rrc_conn_users_avg", "ue_rejected_rrc",
            "ul_rssi_dbm", "pct_prb_high_intf", "cell_avail_pct"],
    "UMTS": ["rrc_setup_sr", "rab_setup_sr_cs", "rab_setup_sr_ps", "cs_drop_rate",
             "ps_drop_rate", "soft_ho_sr", "irat_ho_sr", "avg_rscp_dbm",
             "avg_ecno_db", "hsdpa_thr_mbps", "dl_power_util", "ul_rtwp_dbm",
             "cell_avail_pct"],
    "GSM": ["tch_assign_sr", "sdcch_block_rate", "tch_block_rate",
            "call_drop_rate", "ho_sr", "dl_quality_p_good", "avg_rxlev_dl_dbm",
            "ul_interference_band", "cell_avail_pct"],
}


@dataclass
class TrendResult:
    entity_level: str
    entity_id: str
    kpi: str
    label: str
    direction: str
    n: int
    first: float
    last: float
    slope_per_day: float
    pct_change: float
    level_shift_sigma: float
    baseline: float
    recent: float
    volatility_cv: float
    n_point_anomalies: int
    verdict: str
    worst_point: str = ""

    def as_dict(self) -> dict:
        return {
            "level": self.entity_level, "entity": self.entity_id,
            "kpi": self.kpi, "label": self.label, "direction": self.direction,
            "n": self.n, "first": round(self.first, 3),
            "last": round(self.last, 3),
            "pct_change": round(self.pct_change, 1),
            "slope_per_day": round(self.slope_per_day, 4),
            "level_shift_sigma": round(self.level_shift_sigma, 2),
            "baseline": round(self.baseline, 3), "recent": round(self.recent, 3),
            "volatility_cv": round(self.volatility_cv, 3),
            "point_anomalies": self.n_point_anomalies,
            "verdict": self.verdict, "worst_point": self.worst_point,
        }


def _robust_sigma(x: np.ndarray) -> float:
    if len(x) < 2:
        return 0.0
    mad = np.median(np.abs(x - np.median(x)))
    return float(mad * 1.4826) or float(np.std(x))


def _series_trend(t: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Return (slope per day, intercept). t is in days.

    Long series are decimated to <= 180 samples before the (O(n^2)) Theil-Sen
    fit - the robust slope is unchanged for practical purposes and the cost
    drops by ~4x on hourly, two-week data.
    """
    mask = np.isfinite(t) & np.isfinite(y)
    t, y = t[mask], y[mask]
    if len(y) < 3:
        return 0.0, (float(y.mean()) if len(y) else 0.0)
    if len(y) > 180:
        step = int(np.ceil(len(y) / 180))
        t, y = t[::step], y[::step]
    if theilslopes is not None:
        slope, intercept, *_ = theilslopes(y, t)
        return float(slope), float(intercept)
    slope, intercept = np.polyfit(t, y, 1)
    return float(slope), float(intercept)


def _analyse_one(
    level: str, entity: str, kpi: str, s: pd.Series,
    *, recent_frac: float = 0.25, min_points: int = 5,
) -> TrendResult | None:
    s = s.dropna().sort_index()
    if len(s) < min_points:
        return None
    d = kpi_def(kpi, "LTE")
    direction = d.direction if d else "up"
    label = d.label if d else kpi

    idx = s.index
    if isinstance(idx, pd.DatetimeIndex):
        t_days = (idx - idx[0]).total_seconds().to_numpy() / 86400.0
    else:
        t_days = np.arange(len(s), dtype=float)
    y = s.to_numpy(dtype=float)

    slope, _ = _series_trend(t_days, y)

    # windowed endpoints (median of the first/last block) - robust to noise
    # and to diurnal sampling, unlike single first/last points.
    k = max(3, int(round(len(y) * recent_frac)))
    k = min(k, len(y) // 2)
    baseline, recent = y[:-k] if k < len(y) else y[:1], y[-k:]
    first = float(np.median(y[:k]))
    last = float(np.median(y[-k:]))
    denom = abs(np.median(y)) or 1.0
    pct_change = (last - first) / denom * 100.0

    b_mu, r_mu = float(np.median(baseline)), float(np.median(recent))
    sigma = _robust_sigma(baseline) or _robust_sigma(y) or 1e-9
    shift_sigma = (r_mu - b_mu) / sigma

    # de-trend the diurnal cycle before judging volatility: use the residual
    # around a 24-sample rolling median where the series is long enough.
    if len(y) >= 48:
        base = pd.Series(y).rolling(24, center=True, min_periods=6).median()
        vol_series = (y - base.to_numpy())
        vol_series = vol_series[np.isfinite(vol_series)]
        cv = float(_robust_sigma(vol_series) / (abs(np.median(y)) or 1e-9))
    else:
        cv = float(np.std(y) / (abs(np.mean(y)) or 1e-9))

    med = pd.Series(y).rolling(5, center=True, min_periods=2).median()
    resid = y - med.to_numpy()
    rs = _robust_sigma(resid[np.isfinite(resid)]) or 1e-9
    anom_mask = np.abs(resid) > 4 * rs
    n_anom = int(np.nansum(anom_mask))
    worst_point = ""
    if n_anom and isinstance(idx, pd.DatetimeIndex):
        worst_point = str(idx[int(np.nanargmax(np.abs(resid)))])

    # verdict, oriented so "bad" always means "worse for the network"
    sgn = 1.0 if direction == "up" else -1.0
    good_move = sgn * (r_mu - b_mu)
    rel = good_move / (abs(b_mu) or 1.0)
    end_to_end = sgn * (last - first) / denom          # signed, good>0
    span_days = float(t_days[-1] - t_days[0]) or 1.0
    fit_move = sgn * slope * span_days / denom          # fitted-line change, good>0
    if abs(shift_sigma) >= 4 and abs(rel) >= 0.08:
        verdict = "step-up" if good_move > 0 else "step-down"
    elif cv > 0.5 and abs(rel) < 0.12 and abs(end_to_end) < 0.12:
        verdict = "volatile"
    elif end_to_end <= -0.15 and fit_move <= -0.10:
        verdict = "degrading"
    elif end_to_end >= 0.15 and fit_move >= 0.10:
        verdict = "improving"
    else:
        verdict = "stable"

    return TrendResult(
        entity_level=level, entity_id=str(entity), kpi=kpi, label=label,
        direction=direction, n=len(s), first=first, last=last,
        slope_per_day=slope, pct_change=pct_change,
        level_shift_sigma=shift_sigma, baseline=b_mu, recent=r_mu,
        volatility_cv=cv, n_point_anomalies=n_anom, verdict=verdict,
        worst_point=worst_point,
    )


# a sudden spike: an hour this many robust standard deviations away from the
# object's own normal level, in the KPI's bad direction
SPIKE_Z = 6.0
SPIKE_MIN_HOURS = 6        # an object needs this many hours before it has a "normal"
SPIKE_FLAT = 0.01          # a flat series still varies by 1 % of its level
SPIKE_COLS = ["object", "spike_hours", "spike_time", "spike_value", "normal"]


def sudden_spikes(df: pd.DataFrame, column: str, *, low_is_bad: bool, level: float,
                  obj_col: str = "object") -> pd.DataFrame:
    """Sudden abnormal changes of one KPI, object by object (cell or NodeB).

    Each object's hourly series is set against its own normal behaviour: its
    median over the period (the normal level) and 1.4826 x its median absolute
    deviation (the normal variation; a flat series gets 1 % of its level). An
    hour is a spike when it leaves the normal level in the KPI's bad direction
    by more than SPIKE_Z normal variations *and* reaches `level` (the KPI's
    warning level), so a jump that stays harmless is not called one.

    A NodeB that runs at 20-40 flow-control drops an hour and jumps to 100,000
    is a spike whatever the threshold; a daily busy hour, or an object that is
    always high, is not (the threshold judges that one).

    Returns one row per object with a spike (SPIKE_COLS): its spike hours, and
    the time and value of its worst spike against its normal level."""
    d = df.loc[df[column].notna(), [obj_col, "datetime", column]]
    if d.empty:
        return pd.DataFrame(columns=SPIKE_COLS)
    x = d[column].astype(float)
    by = d[obj_col]
    hours = x.groupby(by).transform("size")
    normal = x.groupby(by).transform("median")
    mad = (x - normal).abs().groupby(by).transform("median")
    scale = np.maximum(1.4826 * mad, SPIKE_FLAT * normal.abs())
    away = (normal - x) if low_is_bad else (x - normal)
    reach = (x <= level) if low_is_bad else (x >= level)
    hit = (hours >= SPIKE_MIN_HOURS) & (away > 0) & (away > SPIKE_Z * scale) & reach
    if not hit.any():
        return pd.DataFrame(columns=SPIKE_COLS)
    s = d[hit].assign(_away=away[hit], _normal=normal[hit])
    worst = s.loc[s.groupby(obj_col)["_away"].idxmax()]
    count = s.groupby(obj_col).size()
    return pd.DataFrame({
        "object": worst[obj_col].astype(str).to_numpy(),
        "spike_hours": count.reindex(worst[obj_col]).to_numpy(dtype=int),
        "spike_time": pd.to_datetime(worst["datetime"].to_numpy()),
        "spike_value": worst[column].to_numpy(dtype=float),
        "normal": worst["_normal"].to_numpy(dtype=float),
    })


def detect_anomalies(
    df: pd.DataFrame,
    *,
    level: str = "cell",
    technology: str | None = None,
    kpis: list[str] | None = None,
    only_flagged: bool = True,
) -> list[TrendResult]:
    """Run trend/anomaly detection for every entity and KPI in a time frame."""
    if df.empty or "datetime" not in df or df["datetime"].isna().all():
        return []
    tech = technology or df["technology"].iloc[0]
    id_col = {"cell": "cell_id", "sector": "sector_id", "site": "site_id"}[level]
    kpi_list = kpis or TREND_KPIS.get(tech) or [
        d.name for d in kpis_for(tech) if d.direction != "info"]
    kpi_list = [k for k in kpi_list if k in df.columns]

    out: list[TrendResult] = []
    kpi_list = [k for k in kpi_list if k in df.columns]
    frame = df[[id_col, "datetime", *kpi_list]].sort_values("datetime")
    for entity, g in frame.groupby(id_col, sort=False):
        g = g.set_index("datetime")
        dup = g.index.has_duplicates
        for kpi in kpi_list:
            s = g[kpi]
            if dup:
                s = s.groupby(level=0).mean()
            res = _analyse_one(level, entity, kpi, s)
            if res is None:
                continue
            if only_flagged and res.verdict in ("stable", "improving", "step-up"):
                continue
            out.append(res)
    sev_order = {"step-down": 0, "degrading": 1, "volatile": 2}
    out.sort(key=lambda r: (sev_order.get(r.verdict, 9), -abs(r.pct_change)))
    return out
