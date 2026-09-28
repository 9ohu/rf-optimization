"""Daily Target tickets against the network, around the problem time.

For each ticket: its site's hourly KPIs from the loaded exports, judged on the
thresholds with a documented source (config/THRESHOLD_SOURCES.md) — the main
KPIs: 4G High PRB, Availability, UL interference (FDD / TDD lines); 3G Flow
Control (per site and hour), Availability, RTWP — inside the problem time ± a
configurable window; what the site looked like just before, and every hour
after the window to the end of the KPI data (the resolution); and a conclusion
that never claims more than the data shows.

Every cell of the site is analysed, each hourly value on its own: a KPI's
state is its worst cell-hour, and the check names every cell it read and every
cell that breached. A KPI the export measures per site (a NodeB counter, flow
control) is analysed per site — never spread over cells.

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
from rfopt.kpi.thresholds import hourly_severity, load_thresholds
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
          "ho_sr": "HO success", "ipmm_rtt_ms": "IP RTT", "ul_rtwp_dbm": "RTWP",
          "dl_flowctrl_drops": "DL flow-control drops"}
_UNIT = {"cell_avail_pct": "%", "ul_rssi_dbm": " dBm", "dl_prb_util": "%",
         "ul_prb_util": "%", "call_setup_sr": "%", "erab_drop_rate": "%",
         "dl_user_thr_mbps": " Mbps", "ul_user_thr_mbps": " Mbps", "ho_sr": "%",
         "ipmm_rtt_ms": " ms", "ul_rtwp_dbm": " dBm", "dl_flowctrl_drops": ""}
_PROBLEM = {"cell_avail_pct": "Availability", "ul_rssi_dbm": "Interference",
            "dl_prb_util": "Congestion", "ul_prb_util": "Congestion",
            "call_setup_sr": "Accessibility", "erab_drop_rate": "Retainability",
            "dl_user_thr_mbps": "Throughput", "ul_user_thr_mbps": "Throughput",
            "ho_sr": "Mobility", "ipmm_rtt_ms": "Transport latency",
            "ul_rtwp_dbm": "Interference", "dl_flowctrl_drops": "Flow control"}
# the main KPIs — they lead every analysis
MAIN = ("dl_prb_util", "ul_prb_util", "cell_avail_pct", "ul_rssi_dbm", "ul_rtwp_dbm",
        "dl_flowctrl_drops")
# judged per site and hour whatever the export's level: the cells' drops added up
SITE_HOUR = ("dl_flowctrl_drops",)
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
    """(column, canonical name, rule) for the KPIs the thresholds can judge: a
    line with a documented source, on a rate or level (not a total) — or flow
    control, judged per site and hour."""
    table = _KPI_MAP_3G if kind == "3G" else _KPI_MAP
    try:
        rules = load_thresholds(_RULESET.get(kind, "LTE"))
    except Exception:
        return []
    out, seen = [], set()
    for k in kpis:
        canon = table.get(_norm(k))
        if not canon or canon in seen or (agg_how(k) != "mean" and canon not in SITE_HOUR):
            continue
        rule = rules.rule(canon)
        if rule is None or not rule.judged:
            continue
        seen.add(canon)
        out.append((k, canon, rule))
    # the main KPIs first
    return sorted(out, key=lambda x: x[1] not in MAIN)


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
    # site -> (times, values, objects): every cell-hour of the site (a site-hour
    # for a site-level KPI), sorted by time
    by_site: dict = field(default_factory=dict)
    duplex: dict = field(default_factory=dict)    # site -> each row's duplex (UL interference)
    level: str = "cell"                           # "site" for a KPI judged per site

    def sev(self, site) -> np.ndarray:
        """Each row's state, every hourly value on its own line."""
        _, vals, _ = self.by_site[site]
        return hourly_severity(self.rule, vals, self.duplex.get(site))

    def hourly(self, site) -> pd.DataFrame:
        """Per hour: the most severe row of the site that hour (then the most
        extreme value) — the hour's state, for a timeline or a before / after."""
        times, vals, objs = self.by_site[site]
        up = self.rule.direction == "up"
        d = pd.DataFrame({"t": pd.DatetimeIndex(times), "v": np.asarray(vals, dtype=float),
                          "obj": objs, "sev": self.sev(site)})
        d["_r"] = d["v"] if up else -d["v"]
        return (d.sort_values(["t", "sev", "_r"], ascending=[True, False, True], kind="stable")
                .drop_duplicates("t").drop(columns="_r").reset_index(drop=True))

    def only(self, key, as_key=None):
        """This track for one site (or sector) alone, keyed `as_key`."""
        import dataclasses
        k = as_key or key
        return dataclasses.replace(self, by_site={k: self.by_site[key]},
                                   duplex={k: self.duplex.get(key)})

    @property
    def label(self) -> str:
        return f"{self.kind} {_LABEL.get(self.canon, self.column)}"

    @property
    def unit(self) -> str:
        return _UNIT.get(self.canon, "")

    @property
    def threshold(self) -> str:
        op = "<" if self.rule.direction == "up" else ">"
        if self.rule.warning == self.rule.critical:
            text = f"Issue {op} {self.rule.critical:g}{self.unit} per hour"
            if self.canon == "ul_rssi_dbm":
                text += " (FDD; TDD > -100 dBm)"
            return text + (" at site level" if self.level == "site" else "")
        return (f"warning {op} {self.rule.warning:g}{self.unit}, "
                f"critical {op} {self.rule.critical:g}{self.unit}")


def build_tracks(frames) -> list[KpiTrack]:
    """frames: (kind, source name, frame from load_hourly_raw, judged_kpis).

    Every row is kept — each cell's own hour, never reduced to a worst cell. A
    site-level KPI (flow control) becomes one row per site and hour, the
    site's cells added up; a KPI the export measures per site stays as it is."""
    tracks = []
    for kind, source, df, judged in frames:
        if df is None or df.empty:
            continue
        for col, canon, rule in judged:
            if col not in df.columns:
                continue
            cols = ["site_id", "datetime", "object", col] + (
                ["duplex"] if "duplex" in df.columns else [])
            d = df.loc[df[col].notna() & df["site_id"].notna(), cols]
            if d.empty:
                continue
            d = d.assign(site_id=d["site_id"].astype(str).str.upper())
            level = "site" if canon in SITE_HOUR else "cell"
            if level == "site":
                d = (d.groupby(["site_id", "datetime"], sort=True)[col].sum().reset_index()
                     .assign(object=lambda x: x["site_id"]))
            d = d.sort_values(["site_id", "datetime", "object"], kind="stable")
            tr = KpiTrack(col, canon, kind, rule, source,
                          pd.Timestamp(d["datetime"].min()), pd.Timestamp(d["datetime"].max()),
                          level=level)
            for site, part in d.groupby("site_id", sort=False):
                tr.by_site[site] = (part["datetime"].to_numpy(),
                                    part[col].to_numpy(dtype=float),
                                    part["object"].astype(str).to_numpy())
                tr.duplex[site] = (part["duplex"].astype(str).to_numpy()
                                   if "duplex" in part.columns else None)
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
    cells: list = field(default_factory=list)       # every cell the check read in the window
    bad_cells: list = field(default_factory=list)   # the ones with an issue hour in it
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
        sev_all = tr.sev(sid)
        wv, wt, wo, ws = vals[win], t[win], objs[win], sev_all[win]
        c.hours = int(pd.DatetimeIndex(wt).nunique())
        c.breach_hours = int(pd.DatetimeIndex(wt[ws > 0]).nunique())
        c.sev = int(ws.max())
        c.status = ("OK", "Warning", "Critical")[c.sev]
        c.cells = sorted(set(wo.tolist()))
        c.bad_cells = sorted(set(wo[ws > 0].tolist()))
        # the worst cell-hour: the most severe, then the most extreme value
        rank = wv if tr.rule.direction == "up" else -wv
        j = int(np.lexsort((rank, -ws))[0])
        c.worst, c.worst_at, c.worst_obj = float(wv[j]), wt[j], str(wo[j])
        if c.breach_hours:
            bt = pd.DatetimeIndex(sorted(set(wt[ws > 0])))
            c.breach_span = f"{_t(bt[0])}–{(bt[-1] + pd.Timedelta(hours=1)):%H:%M}"
        h = tr.hourly(sid)                   # the site's state hour by hour
        before = h[(h["t"] >= lo - pd.Timedelta(hours=6)) & (h["t"] < lo)]
        if len(before):
            c.before = ("already breaching before the window" if (before["sev"] > 0).any()
                        else f"normal before ({_v(before['v'].iloc[-1], tr.unit)})")
        else:
            c.before = "no data before the window"
        # the resolution: every hour after the window, to the end of the KPI data
        after = h[h["t"] > hi]
        if len(after):
            av, at, asev = (after["v"].to_numpy(), pd.DatetimeIndex(after["t"]),
                            after["sev"].to_numpy())
            c.after = f"{_v(av[-1], tr.unit)} at {_t(at[-1])}"
            if c.sev > 0:
                n_bad = int((asev > 0).sum())
                seen = (f"{n_bad} issue hour{'s' if n_bad != 1 else ''} in the {len(asev)} h "
                        f"after the window, to the end of the data ({_t(at[-1])})")
                if asev[-1] > 0:
                    c.resolution = NOT_RESOLVED
                    c.resolution_note = (f"still {'critical' if asev[-1] == 2 else 'in warning'} "
                                         f"at {_t(at[-1])} ({_v(av[-1], tr.unit)}) — {seen}")
                else:
                    bad = np.flatnonzero(asev > 0)
                    since = at[bad[-1] + 1] if bad.size else at[0]
                    c.resolution = RESOLVED
                    c.resolution_note = (f"back within threshold from {_t(since)} to the end "
                                         f"of the data — {seen}")
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

    # the main KPIs lead, then the most hours in breach
    ordered = sorted(judged, key=lambda c: (-c.sev, c.canon not in MAIN, -c.breach_hours))
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
        + (f" ({len(c.bad_cells)} of {len(c.cells)} cells)" if len(c.cells) > 1 else "")
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
