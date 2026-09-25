"""Daily Target tickets against the network, around the problem time.

For each ticket: its site's hourly KPIs from the loaded exports, judged on the
operator's own thresholds (config/thresholds_*.yaml), inside the problem time
± a configurable window; what the site looked like just before and after; and
a conclusion that never claims more than the data shows. A site with several
cells is judged on its worst cell in each hour.

Conclusions: Technical Issue (a critical breach in the window), Possible
Technical Issue (warning level only), No Network Issue Detected (every judged
KPI normal — flagged for manual customer verification, never labelled
non-technical outright) and Insufficient Data (no site, no time, no KPI data in
the window).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from rfopt.ingest.hourly_kpi import _KPI_MAP, _KPI_MAP_3G, _norm
from rfopt.kpi.thresholds import load_thresholds
from rfopt.kpi.trends import agg_how

TECHNICAL = "Technical Issue"
POSSIBLE = "Possible Technical Issue"
NO_ISSUE = "No Network Issue Detected"
INSUFFICIENT = "Insufficient Data"
CLASSES = (TECHNICAL, POSSIBLE, NO_ISSUE, INSUFFICIENT)

RESOLVED = "Resolved"
NOT_RESOLVED = "Not Resolved"
UNKNOWN = "Unknown"
NO_PROBLEM = "No network issue"

NO_ISSUE_TEXT = ("Network KPIs appear normal around the complaint — possible "
                 "non-technical issue. Manual customer verification required.")

_RULESET = {"4G": "LTE", "3G": "UMTS", "2G": "GSM"}
_LABEL = {"cell_avail_pct": "Availability", "ul_rssi_dbm": "UL interference",
          "dl_prb_util": "DL PRB", "ul_prb_util": "UL PRB",
          "call_setup_sr": "CSSR", "erab_drop_rate": "Drop rate",
          "dl_user_thr_mbps": "DL throughput", "ul_user_thr_mbps": "UL throughput",
          "ho_sr": "HO success", "ipmm_rtt_ms": "IP RTT"}
_UNIT = {"cell_avail_pct": "%", "ul_rssi_dbm": " dBm", "dl_prb_util": "%",
         "ul_prb_util": "%", "call_setup_sr": "%", "erab_drop_rate": "%",
         "dl_user_thr_mbps": " Mbps", "ul_user_thr_mbps": " Mbps", "ho_sr": "%",
         "ipmm_rtt_ms": " ms"}
_PROBLEM = {"cell_avail_pct": "Availability", "ul_rssi_dbm": "Interference",
            "dl_prb_util": "Congestion", "ul_prb_util": "Congestion",
            "call_setup_sr": "Accessibility", "erab_drop_rate": "Retainability",
            "dl_user_thr_mbps": "Throughput", "ul_user_thr_mbps": "Throughput",
            "ho_sr": "Mobility", "ipmm_rtt_ms": "Transport latency"}
_GOV_KEYS = (("Basrah", ("basra",)),
             ("Samawa", ("samawa", "muthanna")),
             ("Nassriya", ("nassir", "nasir", "thaiqar", "thi qar", "dhi qar", "thiqar")),
             ("Emarah", ("emarah", "amarah", "maysan", "misan")))


def _clean(x) -> str:
    s = "" if x is None else str(x).strip()
    return "" if s.lower() in ("", "nan", "none", "nat") else s


def r5_governorate(city, tracker_city="") -> str:
    """One of the four R5 governorates when the ticket or tracker names it."""
    for text in (city, tracker_city):
        low = _clean(text).lower()
        for gov, keys in _GOV_KEYS:
            if any(k in low for k in keys):
                return gov
    return _clean(city) or _clean(tracker_city)


def local_time(values) -> pd.Series:
    """Problem times as the local clock shows them. The export writes local
    time with a trailing 'Z'; tickets are created minutes after it, which only
    holds if the digits are local."""
    s = pd.to_datetime(values, errors="coerce", utc=True)
    return s.dt.tz_localize(None)


def judged_kpis(kpis, kind: str) -> list[tuple[str, str, object]]:
    """(column, canonical name, rule) for the KPIs the thresholds can judge:
    a threshold is set, and the KPI is a rate or level (not a total)."""
    table = _KPI_MAP_3G if kind == "3G" else _KPI_MAP
    try:
        rules = load_thresholds(_RULESET.get(kind, "LTE"))
    except Exception:
        return []
    out, seen = [], set()
    for k in kpis:
        canon = table.get(_norm(k))
        if not canon or canon in seen or agg_how(k) != "mean":
            continue
        rule = rules.rule(canon)
        if rule is None or rule.warning is None or rule.critical is None:
            continue
        seen.add(canon)
        out.append((k, canon, rule))
    return out


def severity(rule, value) -> int:
    """0 normal, 1 warning, 2 critical — the threshold file's meaning."""
    if value is None or not np.isfinite(value):
        return 0
    if rule.direction == "up":
        return 2 if value < rule.critical else 1 if value < rule.warning else 0
    return 2 if value > rule.critical else 1 if value > rule.warning else 0


@dataclass
class KpiTrack:
    column: str
    canon: str
    kind: str
    rule: object
    source: str
    start: pd.Timestamp
    end: pd.Timestamp
    by_site: dict = field(default_factory=dict)   # site -> (times, values, worst cell)

    @property
    def label(self) -> str:
        return f"{self.kind} {_LABEL.get(self.canon, self.column)}"

    @property
    def unit(self) -> str:
        return _UNIT.get(self.canon, "")

    @property
    def threshold(self) -> str:
        op = "<" if self.rule.direction == "up" else ">"
        return (f"warning {op} {self.rule.warning:g}{self.unit}, "
                f"critical {op} {self.rule.critical:g}{self.unit}")


def build_tracks(frames) -> list[KpiTrack]:
    """frames: (kind, source name, frame from load_hourly_raw, judged_kpis)."""
    tracks = []
    for kind, source, df, judged in frames:
        if df is None or df.empty:
            continue
        for col, canon, rule in judged:
            if col not in df.columns:
                continue
            d = df.loc[df[col].notna(), ["site_id", "datetime", "object", col]]
            if d.empty:
                continue
            d = d.assign(site_id=d["site_id"].astype(str).str.upper())
            g = d.groupby(["site_id", "datetime"], sort=True)[col]
            pick = g.idxmin() if rule.direction == "up" else g.idxmax()
            w = d.loc[pick.to_numpy()].sort_values(["site_id", "datetime"])
            tr = KpiTrack(col, canon, kind, rule, source,
                          pd.Timestamp(d["datetime"].min()), pd.Timestamp(d["datetime"].max()))
            for site, part in w.groupby("site_id", sort=False):
                tr.by_site[site] = (part["datetime"].to_numpy(),
                                    part[col].to_numpy(dtype=float),
                                    part["object"].astype(str).to_numpy())
            tracks.append(tr)
    return tracks


def _v(value, unit="") -> str:
    if value is None or not np.isfinite(value):
        return "–"
    return (f"{value:,.1f}" if abs(value) < 1000 else f"{value:,.0f}") + unit


def _t(ts) -> str:
    return pd.Timestamp(ts).strftime("%d %b %H:%M")


@dataclass
class KpiCheck:
    label: str
    unit: str
    canon: str
    threshold: str
    source: str
    status: str = "No data"            # Critical / Warning / OK / No data
    sev: int = -1
    hours: int = 0
    breach_hours: int = 0
    worst: float | None = None
    worst_at: pd.Timestamp | None = None
    worst_obj: str = ""
    breach_span: str = ""
    before: str = ""
    after: str = ""
    resolution: str = ""
    resolution_note: str = ""


@dataclass
class TicketAnalysis:
    classification: str
    problem_detected: str             # Yes / Possible / No / Unknown
    problem_type: str
    site_issue: str
    evidence: str
    resolution: str
    resolution_evidence: str
    confidence: str
    manual_check: bool
    kpi_summary: str
    window: str
    checks: list = field(default_factory=list)


def _insufficient(reason: str, window: str = "", checks=None) -> TicketAnalysis:
    return TicketAnalysis(INSUFFICIENT, "Unknown", "", reason, "", UNKNOWN, "", "",
                          False, "", window, list(checks or []))


def analyse_ticket(site_id, problem_time, tracks, window_h: float) -> TicketAnalysis:
    """One ticket: its site's KPIs in the window, before and after."""
    sid = _clean(site_id).upper()
    if not sid:
        return _insufficient("No site ID in the ticket")
    if problem_time is None or pd.isna(problem_time):
        return _insufficient("No problem time in the ticket")
    if not tracks:
        return _insufficient("No KPI export loaded")
    pt = pd.Timestamp(problem_time)
    span = pd.Timedelta(hours=float(window_h))
    lo, hi = (pt - span).floor("h"), pt + span
    window = f"{_t(pt - span)} → {_t(hi)}"
    have = [tr for tr in tracks if sid in tr.by_site]
    if not have:
        return _insufficient(f"Site {sid} is not in the loaded KPI exports", window)

    checks: list[KpiCheck] = []
    for tr in have:
        times, vals, objs = tr.by_site[sid]
        t = pd.DatetimeIndex(times)
        c = KpiCheck(tr.label, tr.unit, tr.canon, tr.threshold, tr.source)
        win = np.asarray((t >= lo) & (t <= hi))
        if not win.any():
            checks.append(c)
            continue
        wv, wt, wo = vals[win], t[win], objs[win]
        sev = np.array([severity(tr.rule, v) for v in wv])
        c.hours, c.breach_hours, c.sev = int(win.sum()), int((sev > 0).sum()), int(sev.max())
        c.status = ("OK", "Warning", "Critical")[c.sev]
        j = int(np.argmin(wv)) if tr.rule.direction == "up" else int(np.argmax(wv))
        c.worst, c.worst_at, c.worst_obj = float(wv[j]), wt[j], str(wo[j])
        if c.breach_hours:
            bt = wt[sev > 0]
            c.breach_span = f"{_t(bt[0])}–{(bt[-1] + pd.Timedelta(hours=1)):%H:%M}"
        before = np.asarray((t >= lo - pd.Timedelta(hours=6)) & (t < lo))
        if before.any():
            bsev = max(severity(tr.rule, v) for v in vals[before])
            c.before = ("already breaching before the window" if bsev
                        else f"normal before ({_v(vals[before][-1], tr.unit)})")
        else:
            c.before = "no data before the window"
        after = np.asarray(t > hi)
        if after.any():
            av, at = vals[after], t[after]
            asev = np.array([severity(tr.rule, v) for v in av])
            c.after = f"{_v(av[-1], tr.unit)} at {_t(at[-1])}"
            if c.sev > 0:
                if asev[-1] > 0:
                    c.resolution = NOT_RESOLVED
                    c.resolution_note = (f"still {'critical' if asev[-1] == 2 else 'in warning'} "
                                         f"at {_t(at[-1])} ({_v(av[-1], tr.unit)})")
                elif len(asev) >= 2 and not asev[-2:].any():
                    bad = np.flatnonzero(asev > 0)
                    since = at[bad[-1] + 1] if bad.size else at[0]
                    c.resolution = RESOLVED
                    c.resolution_note = (f"back within threshold from {_t(since)}, "
                                         f"stable to {_t(at[-1])}")
                else:
                    c.resolution = UNKNOWN
                    c.resolution_note = "too little data after the window to tell"
        else:
            c.after = "no data after the window"
            if c.sev > 0:
                c.resolution = UNKNOWN
                c.resolution_note = f"no data after the window (export ends {_t(tr.end)})"
        checks.append(c)

    judged = [c for c in checks if c.sev >= 0]
    if not judged:
        start = min(tr.start for tr in have)
        end = max(tr.end for tr in have)
        return _insufficient(f"The KPI exports ({_t(start)} → {_t(end)}) do not cover "
                             f"the complaint window", window, checks)

    ordered = sorted(judged, key=lambda c: (-c.sev, -c.breach_hours))
    summary = " · ".join(
        f"{c.label} {_v(c.worst, c.unit)}" + (" ✗" if c.sev == 2 else " ⚠" if c.sev == 1 else "")
        for c in ordered[:3])
    crit = [c for c in judged if c.sev == 2]
    warn = [c for c in judged if c.sev == 1]

    if not crit and not warn:
        full = [c for c in judged if c.hours >= max(1, int(round(2 * float(window_h))))]
        return TicketAnalysis(
            NO_ISSUE, "No", "", NO_ISSUE_TEXT,
            "; ".join(f"{c.label} normal over {c.hours} h (worst {_v(c.worst, c.unit)})"
                      for c in ordered[:4]),
            NO_PROBLEM, "", "Medium" if len(full) >= 3 else "Low", True, summary, window,
            checks)

    lead = [c for c in ordered if c.sev > 0]
    top = lead[0]
    ptype = _PROBLEM.get(top.canon, top.label)
    if crit:
        outage = top.canon == "cell_avail_pct" and top.worst is not None and top.worst < 50
        if outage:
            ptype, issue = "Outage", "Site outage around the complaint time"
        else:
            issue = f"{ptype} degradation detected around the complaint time"
        cls, detected = TECHNICAL, "Yes"
        confidence = "High" if (top.breach_hours >= 2 or len(crit) >= 2) else "Medium"
    else:
        issue = f"Possible {ptype.lower()} issue — {top.label} near its threshold"
        cls, detected = POSSIBLE, "Possible"
        confidence = "Medium" if top.breach_hours >= 2 else "Low"

    evidence = "; ".join(
        f"{c.label} {c.status.lower()} {_v(c.worst, c.unit)}"
        + (f" on {c.worst_obj}" if c.worst_obj else "")
        + f" at {c.breach_span} ({c.threshold})"
        + (", already breaching before" if c.before.startswith("already") else "")
        for c in lead[:3])
    states = [c.resolution for c in lead]
    resolution = (NOT_RESOLVED if NOT_RESOLVED in states
                  else RESOLVED if all(s == RESOLVED for s in states) else UNKNOWN)
    res_ev = "; ".join(f"{c.label}: {c.resolution_note}" for c in lead[:3])
    return TicketAnalysis(cls, detected, ptype, issue, evidence, resolution, res_ev,
                          confidence, False, summary, window, checks)


def site_area_rsrp(files, sites: dict, radius_m: float = 500.0,
                   weak_dbm: float = -110.0) -> dict:
    """Measured RSRP around each site from the coverage grid: the MR-weighted
    median of the grids within `radius_m`, and the share of MRs below
    `weak_dbm`. It describes the site's area, not a customer's position."""
    if not files or not sites:
        return {}
    from rfopt.geo.coverage import weighted_median

    lat = np.concatenate([f.lat for f in files])
    lon = np.concatenate([f.lon for f in files])
    rsrp = np.concatenate([f.rsrp for f in files])
    mr = np.concatenate([f.mr for f in files])
    order = np.argsort(lat, kind="stable")
    lat_sorted = lat[order]
    dlat = radius_m / 111320.0
    out = {}
    for sid, (la, lo) in sites.items():
        if la is None or lo is None or not (np.isfinite(la) and np.isfinite(lo)):
            continue
        i0, i1 = np.searchsorted(lat_sorted, [la - dlat, la + dlat])
        if i1 <= i0:
            continue
        idx = order[i0:i1]
        coslat = max(float(np.cos(np.radians(la))), 1e-6)
        idx = idx[np.abs(lon[idx] - lo) <= radius_m / (111320.0 * coslat)]
        if not idx.size:
            continue
        dy = (lat[idx].astype(float) - la) * 111320.0
        dx = (lon[idx].astype(float) - lo) * 111320.0 * coslat
        idx = idx[dx * dx + dy * dy <= radius_m * radius_m]
        if not idx.size:
            continue
        r = rsrp[idx].astype(float)
        w = mr[idx].astype(float)
        _, med, _ = weighted_median(np.zeros(idx.size, dtype=np.int64), r, w)
        total = float(w.sum()) or 1.0
        out[sid] = {"median": float(med[0]), "weak_pct": 100.0 * float(w[r < weak_dbm].sum()) / total,
                    "grids": int(idx.size), "mrs": float(w.sum()), "radius_m": radius_m,
                    "weak_dbm": weak_dbm}
    return out
