"""The NOC view of one ticket: KPI tiles, context indicators and a timeline.

Presentation model only. The classification, resolution and confidence stay
those of `rfopt.complaints.correlate`: this module arranges its per-KPI checks
into the tiles an engineer scans first (AVA · PRB · INTER · FLOW, then the
secondary indicators) and reads three indicators the exports carry that the
classification does not judge: 3G DL flow-control drops, 3G RTWP and 4G S1
signalling failures. Those are context — shown on the tiles, never counted in
the verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from rfopt.complaints.correlate import severity
from rfopt.ingest.hourly_kpi import _norm
from rfopt.kpi.thresholds import load_thresholds

PRIMARY = ("AVA", "PRB", "INTER", "FLOW")
SECONDARY = ("CSSR", "IPL", "RTWP", "S1")
NAMES = {"AVA": "Availability", "PRB": "PRB utilisation", "INTER": "UL interference",
         "FLOW": "DL flow-control drops", "CSSR": "Call setup success",
         "IPL": "IP path RTT (IPPM)", "RTWP": "RTWP", "S1": "S1 signalling failures"}
_CANON_TILE = {"cell_avail_pct": "AVA", "dl_prb_util": "PRB", "ul_prb_util": "PRB",
               "ul_rssi_dbm": "INTER", "call_setup_sr": "CSSR", "ipmm_rtt_ms": "IPL"}


def _t(ts) -> str:
    return pd.Timestamp(ts).strftime("%d %b %H:%M")


def fmt(value, unit: str = "") -> str:
    """A tile value: short enough to read at a glance."""
    if value is None or not np.isfinite(value):
        return "—"
    a = abs(value)
    if a >= 1e6:
        s = f"{value / 1e6:.1f}M"
    elif a >= 1e4:
        s = f"{value / 1e3:.1f}k"
    elif a >= 100:
        s = f"{value:,.0f}"
    else:
        s = f"{value:,.1f}"
    return s + unit


def state(sev: int, low_is_bad: bool = False) -> str:
    return {-1: "No Data", 0: "Normal", 2: "Critical"}.get(sev, "Low" if low_is_bad else "High")


def _span(times) -> str:
    return f"{_t(times[0])}–{(pd.Timestamp(times[-1]) + pd.Timedelta(hours=1)):%H:%M}"


# --------------------------------------------------------------------------- #
# context indicators
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class IndicatorDef:
    key: str
    label: str
    kind: str
    headers: tuple
    ruleset: str | None
    canon: str | None
    unit: str
    how: str            # worst: worst hour · count: failures summed · day_sum: 24 h total


INDICATORS = (
    IndicatorDef("FLOW", "3G DL flow-control drops", "3G", ("vs rscgroup flowctrol dl dropnum",),
                 "UMTS", "dl_flowctrl_drops", "", "day_sum"),
    IndicatorDef("RTWP", "3G RTWP", "3G", ("vs meanrtwp dbm",), "UMTS", "ul_rtwp_dbm",
                 " dBm", "worst"),
    IndicatorDef("S1", "4G S1 signalling failures", "4G", ("s1 sig failures",), None, None,
                 "", "count"),
)


def indicator_columns(columns, kind: str) -> list[tuple[str, IndicatorDef, object]]:
    """(column, definition, rule) for the context indicators an export carries.
    One that needs a threshold and has none in the threshold file is left out."""
    out, seen = [], set()
    for col in columns:
        n = _norm(col)
        for d in INDICATORS:
            if d.key in seen or d.kind != kind or n not in d.headers:
                continue
            rule = None
            if d.canon:
                try:
                    rule = load_thresholds(d.ruleset).rule(d.canon)
                except Exception:
                    rule = None
                if rule is None or rule.warning is None or rule.critical is None:
                    continue
            seen.add(d.key)
            out.append((col, d, rule))
    return out


@dataclass
class IndicatorTrack:
    defn: IndicatorDef
    column: str
    rule: object
    source: str
    start: pd.Timestamp
    end: pd.Timestamp
    by_site: dict = field(default_factory=dict)   # site -> (times, values)


def build_indicators(frames) -> list[IndicatorTrack]:
    """frames: (source name, frame from load_hourly_raw, indicator_columns).
    A site's hour is its worst cell for a level, the sum of its cells for a count."""
    out = []
    for source, df, cols in frames:
        if df is None or df.empty:
            continue
        for col, d, rule in cols:
            if col not in df.columns:
                continue
            x = df.loc[df[col].notna() & df["site_id"].notna(), ["site_id", "datetime", col]]
            if x.empty:
                continue
            x = x.assign(site_id=x["site_id"].astype(str).str.upper())
            g = x.groupby(["site_id", "datetime"], sort=True)[col]
            if d.how == "worst":
                s = g.min() if rule.direction == "up" else g.max()
            else:
                s = g.sum()
            tr = IndicatorTrack(d, col, rule, source, pd.Timestamp(x["datetime"].min()),
                                pd.Timestamp(x["datetime"].max()))
            for site, part in s.groupby(level=0, sort=False):
                tr.by_site[site] = (part.index.get_level_values(1).to_numpy(),
                                    part.to_numpy(dtype=float))
            out.append(tr)
    return out


@dataclass
class IndicatorCheck:
    key: str
    label: str
    unit: str
    threshold: str
    sev: int = -1
    state: str = "No Data"
    value: float | None = None
    value_text: str = "—"
    at: pd.Timestamp | None = None
    hours: int = 0
    observed: str = ""
    note: str = ""


def _threshold_text(d: IndicatorDef, rule) -> str:
    if rule is None:
        return "any failure in the window"
    op = "<" if rule.direction == "up" else ">"
    per = " per 24 h" if d.how == "day_sum" else ""
    return f"⚠ {op} {rule.warning:,g}{d.unit} · ● {op} {rule.critical:,g}{d.unit}{per}"


def observe_indicators(site_id, problem_time, indicators, window_h: float) -> list[IndicatorCheck]:
    """Each loaded indicator for the ticket's site, around the problem time."""
    sid = ("" if site_id is None else str(site_id)).strip().upper()
    if not sid or sid in ("NAN", "NONE") or problem_time is None or pd.isna(problem_time):
        return []
    pt = pd.Timestamp(problem_time)
    span = pd.Timedelta(hours=float(window_h))
    lo, hi = (pt - span).floor("h"), pt + span
    out = []
    for tr in indicators:
        d = tr.defn
        c = IndicatorCheck(d.key, d.label, d.unit, _threshold_text(d, tr.rule))
        if sid not in tr.by_site:
            c.note = "site not in this export"
            out.append(c)
            continue
        times, vals = tr.by_site[sid]
        t = pd.DatetimeIndex(times)
        win = np.asarray((t >= lo) & (t <= hi))
        if not win.any():
            c.note = "no data in the window"
            out.append(c)
            continue
        wv, wt = vals[win], t[win]
        c.hours = int(win.sum())
        j = int(np.argmax(wv))
        if d.how == "count":
            total = float(np.nansum(wv))
            c.value, c.at, c.value_text = total, wt[j], f"{total:,.0f}"
            c.sev = 1 if total > 0 else 0
            c.state = "Detected" if total > 0 else "Normal"
            if total > 0:
                c.observed = _span(wt[wv > 0])
                c.note = f"peak {wv[j]:,.0f} at {_t(wt[j])}"
            else:
                c.note = f"none in {c.hours} h"
        elif d.how == "day_sum":
            end = wt[-1]
            day = np.asarray((t > end - pd.Timedelta(hours=24)) & (t <= end))
            total = float(np.nansum(vals[day]))
            n = int(day.sum())
            c.value, c.at, c.value_text = total, end, fmt(total)
            c.sev = severity(tr.rule, total)
            c.state = state(c.sev)
            if c.sev > 0:
                c.observed = f"{_t(end - pd.Timedelta(hours=23))} – {_t(end + pd.Timedelta(hours=1))}"
            c.note = (f"24 h total to {_t(end)}" + (f" ({n} h of data)" if n < 24 else "")
                      + f" · window peak {wv[j]:,.0f} at {_t(wt[j])}")
        else:
            up = tr.rule.direction == "up"
            sev = np.array([severity(tr.rule, v) for v in wv])
            j = int(np.argmin(wv)) if up else j
            c.value, c.at, c.value_text = float(wv[j]), wt[j], fmt(float(wv[j]), d.unit)
            c.sev = int(sev.max())
            c.state = state(c.sev, up)
            if (sev > 0).any():
                c.observed = _span(wt[sev > 0])
            c.note = f"worst at {_t(wt[j])} · {c.hours} h in window"
        out.append(c)
    return out


# --------------------------------------------------------------------------- #
# tiles
# --------------------------------------------------------------------------- #
@dataclass
class Tile:
    key: str
    name: str
    value: str = "—"
    sev: int = -1
    state: str = "No Data"
    at: str = ""
    threshold: str = ""
    observed: str = ""
    cell: str = ""
    note: str = ""
    judged: bool = True
    resolution: str = ""
    resolution_note: str = ""


def _low_is_bad(check) -> bool:
    return "<" in check.threshold


def _rank(check):
    if check.worst is None or not np.isfinite(check.worst):
        bad = -np.inf
    else:
        bad = -check.worst if _low_is_bad(check) else check.worst
    return (check.sev, check.breach_hours, bad)


def _from_check(key: str, c) -> Tile:
    t = Tile(key, c.label, judged=True)
    t.threshold = c.threshold.replace("warning ", "⚠ ").replace(", critical ", " · ● ")
    if c.sev < 0:
        t.note = "no data in the window"
        return t
    t.value = fmt(c.worst, c.unit)
    t.sev, t.state = c.sev, state(c.sev, _low_is_bad(c))
    t.at = _t(c.worst_at) if c.worst_at is not None else ""
    t.observed, t.cell = c.breach_span, c.worst_obj
    t.note = f"{c.hours} h in window"
    t.resolution, t.resolution_note = c.resolution, c.resolution_note
    return t


def _from_indicator(c: IndicatorCheck) -> Tile:
    return Tile(c.key, c.label, c.value_text, c.sev, c.state,
                _t(c.at) if c.at is not None else "", c.threshold, c.observed, "",
                c.note, judged=False)


def ticket_tiles(analysis, indicator_checks=()) -> tuple[list[Tile], list[Tile]]:
    """(primary, secondary) tiles for one ticket. A tile that several checks
    feed (4G and 3G availability, DL and UL PRB) shows the worst of them and
    names the others in its note."""
    groups: dict[str, list] = {}
    for c in analysis.checks:
        groups.setdefault(_CANON_TILE.get(c.canon, c.label), []).append(c)
    tiles: dict[str, Tile] = {}
    for key, cs in groups.items():
        best = max(cs, key=_rank)
        t = _from_check(key, best)
        others = [o for o in cs if o is not best and o.sev >= 0]
        if others:
            t.note = " · ".join(f"{o.label} {fmt(o.worst, o.unit)}" for o in others)
        tiles[key] = t
    by_key: dict[str, IndicatorCheck] = {}
    for c in indicator_checks:
        if c.key not in by_key or (c.sev, c.hours) > (by_key[c.key].sev, by_key[c.key].hours):
            by_key[c.key] = c
    for key, c in by_key.items():
        tiles.setdefault(key, _from_indicator(c))
    primary = [tiles.get(k) or Tile(k, NAMES[k], note="not in the loaded KPI exports")
               for k in PRIMARY]
    secondary = ([tiles[k] for k in SECONDARY if k in tiles]
                 + [t for k, t in tiles.items() if k not in PRIMARY and k not in SECONDARY])
    return primary, secondary


def tile_counts(rows) -> dict[str, dict]:
    """Across tickets: how many had each tile critical / high / normal / no data."""
    out: dict[str, dict] = {}
    for primary, secondary in rows:
        for t in list(primary) + list(secondary):
            d = out.setdefault(t.key, {"critical": 0, "warning": 0, "normal": 0, "nodata": 0,
                                       "judged": t.judged})
            d[{2: "critical", 1: "warning", 0: "normal"}.get(t.sev, "nodata")] += 1
    return out


# --------------------------------------------------------------------------- #
# timeline
# --------------------------------------------------------------------------- #
@dataclass
class Timeline:
    start: pd.Timestamp
    end: pd.Timestamp                 # the last hour shown (its hour runs to end + 1 h)
    lo: pd.Timestamp
    hi: pd.Timestamp
    problem_time: pd.Timestamp
    hours: list                       # (hour, worst severity of the judged KPIs, -1 no data)
    before: int
    during: int
    after: int
    issue_span: str
    kpis: list = field(default_factory=list)      # the judged KPIs it reads, by name
    drivers: list = field(default_factory=list)   # per hour: the KPIs at that worst state
    peak: dict | None = None          # the worst hour in the window: when, state, KPI, value


def window_series(times, values, problem_time, window_h: float):
    """The hours of one series inside the correlation window — the same cut as
    `analyse_ticket`: from the hour of (problem time - window) to problem time +
    window."""
    pt = pd.Timestamp(problem_time)
    span = pd.Timedelta(hours=float(window_h))
    lo, hi = (pt - span).floor("h"), pt + span
    t = pd.DatetimeIndex(times)
    win = np.asarray((t >= lo) & (t <= hi))
    return t[win], np.asarray(values, dtype=float)[win], lo, hi


def _worst(sev: np.ndarray) -> int:
    return int(sev.max()) if sev.size else -1


def site_timeline(site_id, problem_time, tracks, window_h: float,
                  before_h: int = 2, after_h: int = 3, kpi: str | None = None) -> Timeline | None:
    """Hour by hour, from a little before the correlation window to a little
    after it: one KPI's state (`kpi`, a track label such as "4G DL PRB"), or
    without it the worst state of the site's judged KPIs, each hour naming the
    KPI that made it. With one KPI the peak is that KPI's worst hour in the
    window — reported even when it stayed normal."""
    sid = ("" if site_id is None else str(site_id)).strip().upper()
    if not sid or problem_time is None or pd.isna(problem_time) or not tracks:
        return None
    if kpi:
        tracks = [tr for tr in tracks if tr.label == kpi]
        if not tracks:
            return None
    pt = pd.Timestamp(problem_time)
    span = pd.Timedelta(hours=float(window_h))
    lo, hi = (pt - span).floor("h"), pt + span
    start = lo - pd.Timedelta(hours=before_h)
    end = hi.floor("h") + pd.Timedelta(hours=after_h)
    grid = pd.date_range(start, end, freq="h")
    sev = np.full(len(grid), -1, dtype=int)
    per: list[tuple] = []             # (track, its severity per hour, its value per hour)
    seen = False
    for tr in tracks:
        if sid not in tr.by_site:
            continue
        seen = True
        times, vals, objs = tr.by_site[sid]
        idx = grid.get_indexer(pd.DatetimeIndex(times))
        ok = idx >= 0
        s_tr = np.full(len(grid), -1, dtype=int)
        v_tr = np.full(len(grid), np.nan)
        o_tr = np.full(len(grid), "", dtype=object)
        if ok.any():
            s = np.array([severity(tr.rule, v) for v in vals[ok]], dtype=int)
            np.maximum.at(sev, idx[ok], s)
            s_tr[idx[ok]], v_tr[idx[ok]], o_tr[idx[ok]] = s, vals[ok], objs[ok]
            per.append((tr, s_tr, v_tr, o_tr))
    if not seen or (sev < 0).all():
        return None
    before = np.asarray(grid < lo)
    during = np.asarray((grid >= lo) & (grid <= hi))
    after = np.asarray(grid > hi)
    bad = grid[during & (sev > 0)]
    # which KPI made each hour's state (only a warning or critical hour names one)
    drivers = [[tr.label for tr, s_tr, _, _ in per if sev[i] > 0 and s_tr[i] == sev[i]]
               for i in range(len(grid))]
    peak = None
    if kpi and per and during.any():
        # one KPI: its worst value in the window, in its own bad direction
        tr, s_tr, v_tr, o_tr = per[0]
        vals_in = np.where(during, v_tr, np.nan)
        if np.isfinite(vals_in).any():
            up = getattr(tr.rule, "direction", "down") == "up"
            i = int(np.nanargmin(vals_in) if up else np.nanargmax(vals_in))
            peak = {"hour": grid[i], "sev": int(s_tr[i]), "kpi": tr.label,
                    "value": fmt(float(v_tr[i]), tr.unit), "object": str(o_tr[i]),
                    "threshold": tr.threshold}
    elif during.any() and sev[during].max() > 0:
        i = int(np.flatnonzero(during & (sev == sev[during].max()))[0])
        tr, s_tr, v_tr, o_tr = next(x for x in per if x[1][i] == sev[i])
        peak = {"hour": grid[i], "sev": int(sev[i]), "kpi": tr.label,
                "value": fmt(float(v_tr[i]), tr.unit), "object": str(o_tr[i]),
                "threshold": tr.threshold}
    return Timeline(start, end, lo, hi, pt, list(zip(grid, sev.tolist())),
                    _worst(sev[before]), _worst(sev[during]), _worst(sev[after]),
                    _span(bad) if len(bad) else "",
                    kpis=[tr.label for tr, *_ in per], drivers=drivers, peak=peak)
