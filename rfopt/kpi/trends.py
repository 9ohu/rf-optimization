"""Per-KPI trend over the loaded window.

One KPI, one object (the whole region, a prefix, a site or a single cell) ->
its hourly series, a robust trend line through it, and the verdict an engineer
reads first: is this going up, going down, or holding?

The fit is Theil-Sen (`rfopt.kpi.anomaly._series_trend`), not least squares, so
one dead hour or a spike does not swing the arrow.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from rfopt.kpi.anomaly import _series_trend

# below this the movement is noise, not a trend
STABLE_PCT = 3.0

# a KPI is either a level (average it over objects) or a count (add it up)
_MEAN_HINTS = ("rate", "%", "ratio", "avg", "average", "mean", "dbm", "mbps",
               "throughput", "utilization", "utilisation", "availability",
               "integrity", "(ms)", "index", "power")
_SUM_HINTS = ("volume", "traffic", "num", "count", "times", "att", "succ",
               "fail", "drop", "erab", "rrc", "attempt", "request")


def agg_how(kpi: str) -> str:
    """How this column aggregates across objects — mean for a level, sum for a
    counter. Rates win over counters: "Drop Call Rate" is a level."""
    c = str(kpi).lower()
    if any(t in c for t in _MEAN_HINTS):
        return "mean"
    if any(t in c for t in _SUM_HINTS):
        return "sum"
    return "mean"


@dataclass
class Trend:
    kpi: str
    points: int
    first: float
    last: float
    pct_change: float
    verdict: str                       # Increasing | Decreasing | Stable
    how: str = "mean"
    series: pd.Series = field(default_factory=pd.Series)
    fit: pd.Series = field(default_factory=pd.Series)

    @property
    def arrow(self) -> str:
        return {"Increasing": "↑", "Decreasing": "↓"}.get(
            self.verdict, "→")

    @property
    def signed_pct(self) -> str:
        return f"{self.pct_change:+.1f}%"


def object_options(df: pd.DataFrame, level: str) -> list[str]:
    """The pickable objects at this level, busiest first where that is known."""
    if df.empty:
        return []
    if level == "Prefix":
        return sorted(df["prefix"].dropna().unique().tolist())
    if level == "Site":
        return sorted(df["site_id"].dropna().unique().tolist())
    if level == "Cell":
        return sorted(df["object"].dropna().unique().tolist())
    return []


def series_for(df: pd.DataFrame, kpi: str, *, level: str = "Network",
               obj: str | None = None) -> pd.Series:
    """The hourly series for one KPI over the chosen object."""
    if df.empty or kpi not in df.columns:
        return pd.Series(dtype=float)
    d = df
    if level == "Prefix" and obj:
        d = d[d["prefix"] == obj]
    elif level == "Site" and obj:
        d = d[d["site_id"] == obj]
    elif level == "Cell" and obj:
        d = d[d["object"] == obj]
    # only the hours this KPI was actually measured in. Without this, summing a
    # counter over an hour that belongs to another file's window returns 0 (sum
    # of nothing), and a 4G KPI picks up a flat zero run wherever the 3G file
    # reaches further back — which reads as a huge false "increase".
    d = d[d[kpi].notna()]
    if d.empty:
        return pd.Series(dtype=float)
    how = agg_how(kpi)
    s = d.groupby("datetime", observed=True)[kpi].agg(how).sort_index()
    return s.dropna()


def trend_of(series: pd.Series, kpi: str) -> Trend:
    """Fit the series and call it: increasing, decreasing or stable."""
    s = series.dropna()
    if len(s) < 2:
        return Trend(kpi=kpi, points=len(s),
                     first=float(s.iloc[0]) if len(s) else float("nan"),
                     last=float(s.iloc[-1]) if len(s) else float("nan"),
                     pct_change=0.0, verdict="Stable", how=agg_how(kpi),
                     series=s, fit=s)

    t = np.asarray(s.index.astype("int64"), dtype=float) / 1e9 / 86400.0
    slope, intercept = _series_trend(t, s.to_numpy(dtype=float))
    fit = pd.Series(intercept + slope * t, index=s.index)

    # measure the move along the fitted line, not between two noisy endpoints
    start, end = float(fit.iloc[0]), float(fit.iloc[-1])
    base = abs(start) if abs(start) > 1e-9 else abs(float(s.mean())) or 1.0
    pct = (end - start) / base * 100.0
    if not np.isfinite(pct):
        pct = 0.0
    verdict = ("Stable" if abs(pct) < STABLE_PCT
               else "Increasing" if pct > 0 else "Decreasing")
    return Trend(kpi=kpi, points=len(s), first=float(s.iloc[0]),
                 last=float(s.iloc[-1]), pct_change=float(pct),
                 verdict=verdict, how=agg_how(kpi), series=s, fit=fit)


def trends_for(df: pd.DataFrame, kpis: list[str], *, level: str = "Network",
               obj: str | None = None) -> list[Trend]:
    out = []
    for k in kpis:
        s = series_for(df, k, level=level, obj=obj)
        if len(s):
            out.append(trend_of(s, k))
    return out


@dataclass
class Panel:
    """One chart: a KPI, the lines on it, and the verdict for the whole lot."""
    kpi: str
    lines: dict[str, pd.Series] = field(default_factory=dict)
    overall: Trend | None = None

    @property
    def compared(self) -> bool:
        return len(self.lines) > 1

    @property
    def all_positive(self) -> bool:
        return all(float(s.min()) >= 0 for s in self.lines.values() if len(s))


def cells_of(df: pd.DataFrame, site_id: str) -> list[str]:
    if df.empty or not site_id:
        return []
    return sorted(df.loc[df["site_id"] == site_id, "object"].dropna().unique())


def panels_for(df: pd.DataFrame, kpis: list[str], *,
               level: str = "Network", obj: str | None = None,
               cells: list[str] | None = None,
               by_tech: bool = False) -> list[Panel]:
    """A chart per KPI.

    With `cells` given, each cell is its own line and the header verdict comes
    from the cells taken together — which is the point of comparing them: you
    want to see one sector drifting away from its siblings without losing what
    the site as a whole did.

    With `by_tech`, each technology gets its own line instead. That matters
    when several exports are loaded at once: a column name like "Integrity"
    exists in more than one of them, and averaging 4G and 3G into a single
    number would be meaningless.
    """
    out: list[Panel] = []
    for k in kpis:
        if cells:
            lines = {c: series_for(df, k, level="Cell", obj=c) for c in cells}
        elif by_tech and "tech" in df.columns:
            lines = {t: series_for(df[df["tech"] == t], k, level=level, obj=obj)
                     for t in sorted(df["tech"].dropna().unique())}
        else:
            lines = {obj or "all R5": series_for(df, k, level=level, obj=obj)}
        lines = {n: s for n, s in lines.items() if len(s)}
        if not lines:
            continue
        if cells:
            whole = series_for(df[df["object"].isin(cells)], k)
        else:
            # the busiest line speaks for the panel; with one line, it is it
            whole = max(lines.values(), key=len)
        out.append(Panel(kpi=k, lines=lines, overall=trend_of(whole, k)))
    return out


def summary_table(trends: list[Trend]) -> pd.DataFrame:
    """One row per KPI — the table to read when 65 charts is too many."""
    if not trends:
        return pd.DataFrame()
    rows = [{
        "KPI": t.kpi,
        "Trend": f"{t.arrow} {t.verdict}",
        "Change %": round(t.pct_change, 1),
        "First": round(t.first, 3),
        "Last": round(t.last, 3),
        "Min": round(float(t.series.min()), 3),
        "Max": round(float(t.series.max()), 3),
        "Average": round(float(t.series.mean()), 3),
        "Points": t.points,
        "Aggregated": t.how,
    } for t in trends]
    df = pd.DataFrame(rows)
    return df.reindex(df["Change %"].abs().sort_values(ascending=False).index
                      ).reset_index(drop=True)
