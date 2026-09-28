"""Sleep Analysis — the evidence behind Solve / Not Solve.

A ticket was put to sleep because the issue was not fixed at the time: a cell
was full, a site was not built yet, a place had no coverage. This module takes
those tickets and asks the network what it says *now*.

**Why the ticket slept** comes from three fields of the history: RF Analysis
(FE), Closure Code (BQ) and Diagnostic Comment (BS). Where they agree they
name the problem; where they conflict the Diagnostic Comment wins — it is the
engineer's written explanation (`reason_of`). A comment that says the issue is
not technical makes the ticket **Not Technical**: no KPI check is forced on it.
A comment that asks for a drive test (DT) makes it a coverage case.

**The check that fits the reason** is then run against measured data:

- coverage (weak coverage, Not Within Plan, indoor / deep indoor, a drive
  test, a planned site that is about coverage) -> the RSRP of the RSRP grid
  cell at the subscriber's location; no grid there is poor coverage. The
  planned site's status is shown, never the verdict.
- high utilization / load (a planned site meant to take load too) -> the
  serving sector's PRB, every cell, hour by hour;
- flow control -> the site's flow-control drops, hour by hour (3G, per site);
- interference -> the serving sector's UL interference (every cell, on its FDD
  or TDD line) and the site's 3G RTWP, hour by hour.

A technical check reads the **whole loaded KPI period** (not a window around
the problem time), each hourly value on its own line (config/THRESHOLD_SOURCES.md),
on the serving sector only; the other main KPIs are read beside it. A check
with nothing behind it answers "Not Checked" rather than guessing. Every
description is written from the values the check actually read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# the closure codes a sleep ticket is analysed under — the R5 team's own list
CLOSURE_CODES = (
    "Data - High utilization cells",
    "Data - High flow control sites",
    "Data - Slow Weak Coverage",
    "Data - Planned - This area needs a new site/tower",
    "Planned - This area needs a new site/tower",
    "High RTWP area (interference)",
    "Indoor Issue - Urban areas",
    "Not Within Plan",
)

SOLVE, NOT_SOLVE, NOT_CHECKED = "Solve", "Not Solve", "Not Checked"
NOT_TECHNICAL = "Not Technical"
ON_AIR, NOT_ON_AIR = "On Air", "Still Not On Air"

# the check each closure code asks for
CODE_CHECK = {
    "Data - High utilization cells": "utilization",
    "Data - High flow control sites": "flow_control",
    "Data - Slow Weak Coverage": "coverage",
    "Data - Planned - This area needs a new site/tower": "planned",
    "Planned - This area needs a new site/tower": "planned",
    "High RTWP area (interference)": "interference",
    "Indoor Issue - Urban areas": "coverage",
    "Not Within Plan": "coverage",
}
# and where the RF Analysis of the ticket names a different one, it wins: the
# engineer who closed the ticket said what they found
RF_CHECK = {
    "prb utilization": "utilization",
    "event traffic increase": "utilization",
    "flow control": "flow_control",
    "interference": "interference",
    "new site required": "planned",
    "physical optimization": "coverage",
}

# the KPIs a check reads, by the schema's name for them
PRB, RSSI, AVAIL = "dl_prb_util", "ul_rssi_dbm", "cell_avail_pct"
USERS, THR = "users", "user_thr"           # named by the export, not the schema
RSRP_RULE = "avg_rsrp_dbm"                 # the coverage line, from the same YAML
NEAR_M = 150.0                             # a grid further than this is not this place

FLOW = "flow_drops"                        # the 3G counter, per NodeB
RTWP = "rtwp_dbm"                          # the 3G one, beside 4G's ul_rssi_dbm

# the KPI a check is judged on, and what an engineer calls it
CHECK_KPI = {"utilization": PRB, "interference": RSSI, "flow_control": FLOW,
             "coverage": PRB, "planned": PRB, "not_technical": PRB}
KPI_NAME = {PRB: "PRB Utilization", RSSI: "UL Interference", AVAIL: "Availability",
            USERS: "Connected Users", THR: "DL User Throughput", FLOW: "Flow Control",
            RTWP: "RTWP"}

# a sector is its carriers together, so its hour is: the worst carrier where a
# KPI judges a cell (PRB, interference, availability), the carriers added up
# where the export counts them per cell (users), and their average where the
# number is already a per-user rate (throughput)
HOW = {PRB: "max", RSSI: "max", AVAIL: "min", USERS: "sum", THR: "mean"}


# --------------------------------------------------------------------------- #
# names and thresholds, as the rest of the app reads them
# --------------------------------------------------------------------------- #
def canon_of(kpi: str) -> str | None:
    """The export's own column name -> the schema's (`dl_prb_util`)."""
    from rfopt.ingest.hourly_kpi import _KPI_MAP, _KPI_MAP_3G, _norm

    n = _norm(kpi)
    return _KPI_MAP.get(n) or _KPI_MAP_3G.get(n)


def rule_of(canon: str, tech: str = "LTE"):
    """The operator's warning / critical line for a KPI, or None."""
    from rfopt.kpi.thresholds import load_thresholds

    return load_thresholds(tech).rule(canon)


def columns_for(kpis, wanted: tuple) -> dict:
    """schema name -> the export column that holds it, for the ones it has."""
    out: dict = {}
    for k in kpis:
        canon = canon_of(str(k))
        if canon in wanted and canon not in out:
            out[canon] = str(k)
    return out


def _named(kpis, *words) -> str | None:
    for k in kpis:
        low = str(k).lower()
        if any(w in low for w in words):
            return str(k)
    return None


def kpi_columns(kpis) -> dict:
    """Which export column holds each thing a check reads. The three judged
    ones are found by the schema's name; connected users and user throughput
    have no threshold of their own, so they are found by theirs."""
    out = columns_for(kpis, (PRB, RSSI, AVAIL))
    users = _named(kpis, "average user number", "user number", "rrc connected user")
    thr = _named(kpis, "dl user throughput", "user throughput")
    if users:
        out[USERS] = users
    if thr:
        out[THR] = thr
    return out


def flow_control_column(kpis) -> str | None:
    """The 3G flow-control counter, which has no schema name of its own."""
    for k in kpis:
        low = str(k).lower().replace(".", " ")
        if "flowctrol" in low.replace(" ", "") or ("flow" in low and "ctrol" in low):
            return str(k)
    return None


# --------------------------------------------------------------------------- #
# where things are
# --------------------------------------------------------------------------- #
def metres_between(lat1, lon1, lat2, lon2) -> float:
    """Metres between two points, flat-earth over these distances (R5 spans a
    degree or two, where the error is centimetres)."""
    for v in (lat1, lon1, lat2, lon2):
        if v is None or pd.isna(v):
            return float("nan")
    dy = (float(lat2) - float(lat1)) * 111_320.0
    dx = (float(lon2) - float(lon1)) * 111_320.0 * np.cos(np.radians(float(lat1)))
    return float(np.hypot(dy, dx))


def bearing(lat1, lon1, lat2, lon2) -> float:
    """The compass bearing from the first point to the second, 0-360."""
    for v in (lat1, lon1, lat2, lon2):
        if v is None or pd.isna(v):
            return float("nan")
    dy = (float(lat2) - float(lat1))
    dx = (float(lon2) - float(lon1)) * np.cos(np.radians(float(lat1)))
    return float((np.degrees(np.arctan2(dx, dy)) + 360.0) % 360.0)


def angle_gap(a, b) -> float:
    """How far apart two bearings are, 0-180."""
    if a is None or b is None or pd.isna(a) or pd.isna(b):
        return float("nan")
    d = abs(float(a) - float(b)) % 360.0
    return float(360.0 - d if d > 180.0 else d)


def fmt_metres(m) -> str:
    if m is None or pd.isna(m):
        return "-"
    return f"{m / 1000:.2f} km" if m >= 1000 else f"{m:.0f} m"


# --------------------------------------------------------------------------- #
# the tickets
# --------------------------------------------------------------------------- #
_SECTOR = re.compile(r"^\s*([A-Za-z]{3}\d{3,5})\s*-\s*(\d+)\s*$")


def split_sector(serving) -> tuple[str, float]:
    """"BAS0480-2" -> ("BAS0480", 2.0). Anything else -> ("", nan)."""
    m = _SECTOR.match(str(serving or ""))
    if not m:
        return "", float("nan")
    return m.group(1).upper(), float(m.group(2))


def is_planned(closure_code: str) -> bool:
    return CODE_CHECK.get(str(closure_code)) == "planned"


# what a Diagnostic Comment says, in the words engineers write. Not technical:
# the existing list of non-RF causes (`complaints.worklist._C_NOTRF`), with
# "not technical" itself; the rest name the checks the RF Analysis names.
_C_NOT_TECH = re.compile(r"\b(not? (?:an? )?technical|non[- ]?technical|not connected|no issue|ps side|"
                         r"core side|sim|device|handset|barred|provision\w*|charging|package|"
                         r"balance|switched? off)\b", re.I)
_C_DT = re.compile(r"\b(dt|drive[- ]?test\w*)\b", re.I)
_C_CHECKS = (
    ("flow_control", re.compile(r"\bflow[- ]?contro\w*", re.I)),
    ("interference", re.compile(r"\b(interfe\w*|rtwp|jamm\w*|noise)\b", re.I)),
    ("utilization", re.compile(r"\b(congest\w*|utili[sz]\w*|prb|high traffic|overload\w*|"
                               r"capacity|load)\b", re.I)),
    ("coverage", re.compile(r"\b(weak|coverage|indoor|in-?building|basement|rsrp|"
                            r"no signal|poor signal)\b", re.I)),
    ("planned", re.compile(r"\b(planned? site|new site|new tower|nomination|"
                           r"will be on ?air)\b", re.I)),
)
_LOAD = re.compile(r"\b(congest\w*|utili[sz]\w*|prb|high traffic|overload\w*|capacity|load)\b",
                   re.I)


def comment_check(comment) -> str:
    """The check a Diagnostic Comment names: "not_technical", "coverage" for a
    drive test, one of the checks, or "" when it names none."""
    text = str(comment or "")
    if not text.strip():
        return ""
    if _C_NOT_TECH.search(text):
        return "not_technical"
    if _C_DT.search(text):
        return "coverage"
    for check, pattern in _C_CHECKS:
        if pattern.search(text):
            return check
    return ""


def reason_of(closure_code, rf_analysis, comment=None) -> tuple[str, str]:
    """(the check, the field that decided it). The Diagnostic Comment first —
    it is the written explanation and wins a conflict — then the RF Analysis,
    then the Closure Code. A planned-site reason is a coverage case unless the
    comment or the RF Analysis says it is about load."""
    by_comment = comment_check(comment)
    by_rf = RF_CHECK.get(str(rf_analysis or "").strip().lower(), "")
    by_code = CODE_CHECK.get(str(closure_code), "")
    check, source = ((by_comment, "Diagnostic Comment") if by_comment
                     else (by_rf, "RF Analysis") if by_rf
                     else (by_code, "Closure Code") if by_code else ("", ""))
    if check == "planned":
        load = by_rf == "utilization" or bool(_LOAD.search(str(comment or "")))
        check = "utilization" if load else "coverage"
    return check, source


def check_of(closure_code, rf_analysis, comment=None) -> str:
    """Which check a ticket asks for (`reason_of`)."""
    return reason_of(closure_code, rf_analysis, comment)[0]


def wake_after(expected, today=None) -> float:
    """Days left to the Expected Resolution Date — negative once it has passed."""
    when = pd.to_datetime(expected, errors="coerce")
    if pd.isna(when):
        return float("nan")
    now = pd.Timestamp(today) if today is not None else pd.Timestamp.now()
    return float((when.normalize() - now.normalize()).days)


def sleep_population(history: pd.DataFrame, *, today=None) -> pd.DataFrame:
    """The sleep tickets of the eight closure codes, with what the page needs:
    the site and number of the serving sector, the planned site where the code
    is a planned one, and the days left to the expected resolution."""
    if history is None or history.empty:
        return pd.DataFrame(columns=list(getattr(history, "columns", [])) +
                            ["serving", "site", "sector_num", "plan_site", "wake_after"])
    df = history[history["status"].astype(str).str.strip().str.lower().eq("sleep")]
    df = df[df["closure_code"].astype(str).isin(CLOSURE_CODES)].copy()
    split = df["sector"].map(split_sector)
    df["site"] = [s for s, _ in split]
    df["sector_num"] = [n for _, n in split]
    df["serving"] = [f"{s}-{n:g}" if s else "" for s, n in split]
    comments = df["comment"] if "comment" in df.columns else pd.Series("", index=df.index)
    why = [reason_of(c, r, m) for c, r, m in zip(df["closure_code"], df["rf_analysis"],
                                                   comments)]
    df["check"] = [c for c, _ in why]
    df["reason_from"] = [w for _, w in why]
    # column BP names a site on nearly every ticket; it is the *planned* site
    # only where the ticket is about one, so it is carried only there
    planned = (df["closure_code"].map(is_planned)
               | df["rf_analysis"].astype(str).str.strip().str.lower().eq("new site required"))
    df["plan_site"] = df["planned_site"].astype(str).str.strip().str.upper().where(planned, "")
    df["wake_after"] = df["expected"].map(lambda v: wake_after(v, today))
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# the evidence: what the hourly export says about a serving sector
# --------------------------------------------------------------------------- #
@dataclass
class Stat:
    """One KPI of one sector over the measured window."""
    top: float = float("nan")          # the highest hour
    mean: float = float("nan")
    low: float = float("nan")          # the lowest hour
    over: int = 0                      # hours with an issue: any cell past its line that hour
    worst: float = float("nan")        # the end this KPI goes bad towards
    hours_set: frozenset = frozenset()  # the hours with an issue


@dataclass
class Evidence:
    """One serving sector, measured. A KPI the export does not carry has no
    stat here — which never means the KPI was good."""
    site: str
    sector: float
    cells: list = field(default_factory=list)
    hours: int = 0
    start: object = None
    end: object = None
    stats: dict = field(default_factory=dict)

    @property
    def title(self) -> str:
        return f"{self.site}-{self.sector:g}"

    def stat(self, canon: str) -> Stat | None:
        return self.stats.get(canon)

    def _one(self, canon: str, field_: str, default=float("nan")):
        got = self.stats.get(canon)
        return getattr(got, field_) if got is not None else default

    @property
    def prb_max(self) -> float:
        return self._one(PRB, "top")

    @property
    def prb_avg(self) -> float:
        return self._one(PRB, "mean")

    @property
    def prb_hours(self) -> int:
        return int(self._one(PRB, "over", 0))

    @property
    def rssi_max(self) -> float:
        return self._one(RSSI, "top")

    @property
    def rssi_avg(self) -> float:
        return self._one(RSSI, "mean")

    @property
    def rssi_hours(self) -> int:
        return int(self._one(RSSI, "over", 0))

    @property
    def avail_min(self) -> float:
        return self._one(AVAIL, "low")

    @property
    def users_avg(self) -> float:
        return self._one(USERS, "mean")

    @property
    def thr_avg(self) -> float:
        return self._one(THR, "mean")

    def issues(self) -> list:
        """The KPIs of this sector with an issue hour."""
        return [c for c, st in self.stats.items() if st.over > 0]

    def issue_hours(self, canon: str) -> frozenset:
        got = self.stats.get(canon)
        return got.hours_set if got is not None else frozenset()


def sector_hours(kpi: pd.DataFrame, objects: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """One row per serving sector and hour: the sector's value (its carriers
    together, `HOW`) and, for each judged KPI, whether any of its cells has an
    issue that hour — every cell's hourly value on its own line (a TDD cell's
    UL interference on the TDD line)."""
    if kpi is None or kpi.empty or objects is None or objects.empty or not cols:
        return pd.DataFrame()
    place = objects.dropna(subset=["sector"])
    place = place[place["sector"].gt(0)]
    if place.empty:
        return pd.DataFrame()
    at = dict(zip(place["object"].astype(str), zip(place["site"], place["sector"])))
    obj = kpi["object"].astype(str)
    keep = obj.map(lambda o: o in at)
    if not keep.any():
        return pd.DataFrame()
    part = kpi[keep]
    pairs = obj[keep].map(at)
    out = pd.DataFrame({"site": [p[0] for p in pairs], "sector": [p[1] for p in pairs],
                        "datetime": part["datetime"].to_numpy()})
    how = {}
    from rfopt.kpi.thresholds import hourly_severity
    duplex = part["duplex"].to_numpy() if "duplex" in part.columns else None
    for canon, column in cols.items():
        if column not in part.columns:
            continue
        values = pd.to_numeric(part[column], errors="coerce").to_numpy()
        out[canon] = values
        how[canon] = HOW.get(canon, "max")
        rule = rule_of(canon)
        if rule is not None and rule.judged:
            out[f"issue:{canon}"] = (hourly_severity(rule, values, duplex) > 0).astype(int)
            how[f"issue:{canon}"] = "max"
    if not how:
        return pd.DataFrame()
    return out.groupby(["site", "sector", "datetime"], sort=False).agg(how).reset_index()


def evidence_of(hours: pd.DataFrame, cells: dict | None = None) -> dict:
    """(site, sector) -> Evidence, from the sector-hours of the whole export."""
    out: dict = {}
    if hours is None or hours.empty:
        return out
    measured = [c for c in hours.columns if c not in ("site", "sector", "datetime")
                and not str(c).startswith("issue:")]
    rules = {c: rule_of(c) for c in measured}
    for (site, sector), part in hours.groupby(["site", "sector"], sort=False):
        ev = Evidence(site=str(site), sector=float(sector), hours=int(len(part)),
                      start=part["datetime"].min(), end=part["datetime"].max(),
                      cells=list((cells or {}).get((str(site), float(sector)), [])))
        for canon in measured:
            v = part[canon].dropna()
            if not len(v):
                continue
            rule = rules.get(canon)
            up = rule is not None and rule.direction == "up"
            flag = f"issue:{canon}"
            bad = (frozenset(pd.DatetimeIndex(part.loc[part[flag] > 0, "datetime"]))
                   if flag in part.columns else frozenset())
            ev.stats[canon] = Stat(top=float(v.max()), mean=float(v.mean()),
                                   low=float(v.min()), over=len(bad),
                                   worst=float(v.min() if up else v.max()), hours_set=bad)
        out[(ev.site, ev.sector)] = ev
    return out


def _site_hourly(kpi: pd.DataFrame, column: str, sites: pd.Series, canon: str, tech: str,
                 agg: str) -> dict:
    """site -> (hours with an issue, the worst hour, the average hour, the issue
    hours): the site's value each hour (its rows `agg`-ed), judged hour by hour."""
    if kpi is None or kpi.empty or not column or column not in kpi.columns:
        return {}
    site = sites.reindex(kpi.index) if sites is not None else None
    if site is None:
        return {}
    from rfopt.kpi.thresholds import hourly_severity
    d = pd.DataFrame({"site": site.astype(str).str.upper(), "datetime": kpi["datetime"],
                      "v": pd.to_numeric(kpi[column], errors="coerce")}).dropna()
    if d.empty:
        return {}
    h = d.groupby(["site", "datetime"], sort=True)["v"].agg(agg).reset_index()
    rule = rule_of(canon, tech)
    h["bad"] = (hourly_severity(rule, h["v"].to_numpy()) > 0
                if rule is not None and rule.judged else False)
    out = {}
    for s_id, part in h.groupby("site", sort=False):
        bad = frozenset(pd.DatetimeIndex(part.loc[part["bad"], "datetime"]))
        out[s_id] = (len(bad), float(part["v"].max()), float(part["v"].mean()), bad)
    return out


def site_flow_control(kpi: pd.DataFrame, column: str, sites: pd.Series) -> dict:
    """site -> (hours over the flow-control line, the worst hour, the average
    hour, those hours). Flow control is judged per site: the site's drops of
    each hour added up, over 100,000 an hour is an issue."""
    return _site_hourly(kpi, column, sites, "dl_flowctrl_drops", "UMTS", "sum")


def site_rtwp(kpi: pd.DataFrame, column: str, sites: pd.Series) -> dict:
    """site -> (hours with RTWP past its line, the worst hour, the average hour,
    those hours) from the 3G export — its worst row each hour."""
    return _site_hourly(kpi, column, sites, "ul_rtwp_dbm", "UMTS", "max")


# --------------------------------------------------------------------------- #
# the evidence: measured coverage where the subscriber was
# --------------------------------------------------------------------------- #
@dataclass
class Point:
    rsrp: float
    mr: int
    metres: float
    source: str = ""


def rsrp_at(lat, lon, grids, *, near_m: float = NEAR_M) -> Point | None:
    """The measured RSRP of the coverage grid nearest the subscriber's point,
    or None where no grid is measured within `near_m` of it."""
    lat, lon = pd.to_numeric(lat, errors="coerce"), pd.to_numeric(lon, errors="coerce")
    if pd.isna(lat) or pd.isna(lon) or not grids:
        return None
    best = None
    for f in grids:
        if not len(getattr(f, "lat", ())):
            continue
        dy = (np.asarray(f.lat, dtype=np.float64) - float(lat)) * 111_320.0
        dx = ((np.asarray(f.lon, dtype=np.float64) - float(lon)) * 111_320.0
              * np.cos(np.radians(float(lat))))
        d2 = dy * dy + dx * dx
        i = int(np.argmin(d2))
        metres = float(np.sqrt(d2[i]))
        if best is None or metres < best.metres:
            best = Point(rsrp=float(f.rsrp[i]), mr=int(f.mr[i]), metres=metres,
                         source=getattr(f, "name", ""))
    return best if best is not None and best.metres <= near_m else None


def rsrp_frame(lat, lon, grids, *, near_m: float = NEAR_M) -> pd.DataFrame:
    """The nearest measured grid to each point, all of them in one pass.

    A point at a time walks every grid row again (seconds each over a million
    rows), so the grids are put in a tree once and every subscriber location is
    asked together. The answer is the same one `rsrp_at` gives.
    """
    lat = pd.to_numeric(pd.Series(lat), errors="coerce").to_numpy(dtype=float)
    lon = pd.to_numeric(pd.Series(lon), errors="coerce").to_numpy(dtype=float)
    out = pd.DataFrame({"rsrp": np.full(len(lat), np.nan), "mr": np.zeros(len(lat)),
                        "metres": np.full(len(lat), np.inf), "source": [""] * len(lat)})
    ok = ~(np.isnan(lat) | np.isnan(lon))
    if not ok.any() or not grids:
        return out
    scale = np.cos(np.radians(float(np.nanmean(lat[ok]))))
    want = np.column_stack([lat[ok] * 111_320.0, lon[ok] * 111_320.0 * scale])
    for f in grids:
        if not len(getattr(f, "lat", ())):
            continue
        have = np.column_stack([np.asarray(f.lat, dtype=float) * 111_320.0,
                                np.asarray(f.lon, dtype=float) * 111_320.0 * scale])
        try:
            from scipy.spatial import cKDTree
            d, i = cKDTree(have).query(want, workers=-1)
        except Exception:                      # no scipy: the slow way, once per grid
            d = np.empty(len(want))
            i = np.empty(len(want), dtype=int)
            for k, p in enumerate(want):
                d2 = ((have - p) ** 2).sum(axis=1)
                i[k] = int(np.argmin(d2))
                d[k] = float(np.sqrt(d2[i[k]]))
        better = d < out.loc[ok, "metres"].to_numpy()
        if not better.any():
            continue
        rows = np.flatnonzero(ok)[better]
        out.loc[rows, "rsrp"] = np.asarray(f.rsrp, dtype=float)[i[better]]
        out.loc[rows, "mr"] = np.asarray(f.mr, dtype=float)[i[better]]
        out.loc[rows, "metres"] = d[better]
        out.loc[rows, "source"] = getattr(f, "name", "")
    far = out["metres"] > near_m
    out.loc[far, ["rsrp", "mr"]] = np.nan
    out.loc[far, "source"] = ""
    return out


def point_of(row) -> Point | None:
    """One row of `rsrp_frame` as a Point, or None where nothing is near."""
    if row is None or pd.isna(row.get("rsrp")):
        return None
    return Point(rsrp=float(row["rsrp"]), mr=int(row["mr"] or 0),
                 metres=float(row["metres"]), source=str(row.get("source", "")))


def plan_site_status(site: str, kmz_sites: pd.DataFrame | None, on_air=frozenset()) -> str:
    """On Air / Still Not On Air for a planned site. The EP tracker first: a
    site it lists as active (`on_air`, `rfopt.ingest.site_status`) is On Air
    whatever the KMZ says, since the KMZ may not be updated yet. Otherwise the
    site KMZ; with neither saying anything the status is "" — none is made up."""
    if site and str(site).strip().upper() in on_air:
        return ON_AIR
    if not site or kmz_sites is None or kmz_sites.empty:
        return ""
    row = kmz_sites[kmz_sites["site_id"].astype(str).str.upper().eq(str(site).upper())]
    if row.empty:
        return NOT_ON_AIR                     # not in the site file: not built
    status = str(row.iloc[0].get("status", "")).strip().lower()
    return ON_AIR if status == "on air" else NOT_ON_AIR


# --------------------------------------------------------------------------- #
# the verdict, and the comment that shows its working
# --------------------------------------------------------------------------- #
_NA = "N/A"


def _num(v, fmt: str, unit: str = "") -> str:
    if v is None or pd.isna(v):
        return _NA
    return f"{v:{fmt}}{unit}"


def _dist(metres) -> str:
    return _NA if metres is None or pd.isna(metres) else fmt_metres(metres)


def _rsrp(point) -> str:
    return _NA if point is None else _num(point.rsrp, ".1f", " dBm")


def _flow_parts(flow) -> tuple:
    """(hours with a drop, worst hour, average hour) — an older 2-tuple has no average."""
    if flow is None:
        return 0, float("nan"), float("nan")
    hours, worst = flow[0], flow[1]
    avg = flow[2] if len(flow) > 2 else float("nan")
    return hours, worst, avg


# what each check is about, as its Description names it
_ISSUE = {"utilization": "high PRB utilization", "flow_control": "Flow Control issues",
          "interference": "interference", "coverage": "weak coverage"}
_NOT_VERIFIED = {"utilization": "The PRB utilization", "flow_control": "Flow Control",
                 "interference": "The interference", "coverage": "The coverage"}


def _rtwp_parts(rtwp) -> tuple:
    if rtwp is None:
        return 0, float("nan"), float("nan")
    return rtwp[0], rtwp[1], rtwp[2]


def describe(check: str, serving: str, ev: Evidence | None, point: Point | None,
             plan: str = "", plan_site: str = "", flow: tuple | None = None,
             metres=None, verdict: str = NOT_SOLVE, *, rtwp: tuple | None = None,
             no_grid: bool = False, comment: str = "") -> str:
    """The Description of a ticket, written for its final verdict.

    Not Solve: the R5 team's fixed wording for the check ("still experiencing
    ..."), only the values filled in. Solve: the same sentence with the issue
    "no longer" seen. Not Checked: the check could not be verified. Not
    Technical: what the Diagnostic Comment says. A value the data does not
    carry reads N/A.
    """
    if verdict == NOT_TECHNICAL:
        text = re.sub(r"\s+", " ", str(comment or "")).strip()
        text = text[:200] + ("…" if len(text) > 200 else "")
        return ("Not Technical: the Diagnostic Comment explains the issue as non-technical"
                + (f" (\u201c{text}\u201d)" if text else "") + ". No KPI check is run on it.")
    lead = (f"The serving sector is {serving or _NA}, with a distance of {_dist(metres)} "
            "from the user location.")
    rsrp = ("N/A (no RSRP grid at the user location)" if no_grid and point is None
            else _rsrp(point))
    if check not in _ISSUE:
        check = "coverage"
    if verdict == NOT_CHECKED:
        tail = "" if check == "coverage" else f" The RSRP measurement is {rsrp}."
        return f"{lead} {_NOT_VERIFIED[check]} could not be verified from the loaded data.{tail}"
    how = "is no longer" if verdict == SOLVE else "is still"
    if check == "coverage":
        return (f"{lead} The area {how} experiencing weak coverage, with an RSRP measurement "
                f"of {rsrp}.")
    if check == "utilization":
        mx = _num(ev.prb_max if ev is not None else None, ".1f", "%")
        av = _num(ev.prb_avg if ev is not None else None, ".1f", "%")
        values = f"with a maximum value of {mx} and an average value of {av}"
    elif check == "flow_control":
        _, worst, avg = _flow_parts(flow)
        values = (f"with a maximum value of {_num(worst, ',.0f')} and an average value of "
                  f"{_num(avg, ',.1f')}")
    else:
        if ev is not None and ev.stat(RSSI) is not None:
            mx, av = ev.rssi_max, ev.rssi_avg
        else:
            _, mx, av = _rtwp_parts(rtwp)
        values = (f"with a maximum RTWP value of {_num(mx, '.1f', ' dBm')} and an average value "
                  f"of {_num(av, '.1f', ' dBm')}")
    return (f"{lead} The sector {how} experiencing {_ISSUE[check]}, {values}. "
            f"The RSRP measurement is {rsrp}.")


def judge(check: str, serving: str, ev: Evidence | None, point: Point | None,
          plan: str = "", plan_site: str = "", flow: tuple | None = None,
          metres=None, *, rtwp: tuple | None = None, located: bool = False,
          coverage: bool = False, comment: str = "") -> tuple:
    """(Solve / Not Solve / Not Checked / Not Technical, the Description).

    The verdict comes first, read from the current data against the lines in
    config/THRESHOLD_SOURCES.md — never from the closure code, nor from the
    planned site's status — and the Description is then written for it.
    `located`: the ticket carries the user's location; `coverage`: an RSRP
    grid is loaded (a location it does not cover is poor coverage)."""
    no_grid = located and coverage and point is None
    verdict = _verdict(check, serving, ev, point, flow, rtwp=rtwp, located=located,
                       coverage=coverage)
    return verdict, describe(check, serving, ev, point, plan, plan_site, flow, metres,
                             verdict=verdict, rtwp=rtwp, no_grid=no_grid, comment=comment)


def _verdict(check: str, serving: str, ev: Evidence | None, point: Point | None,
             flow: tuple | None = None, *, rtwp: tuple | None = None,
             located: bool = False, coverage: bool = False) -> str:
    if check == "not_technical":
        return NOT_TECHNICAL

    if check in ("coverage", "planned"):      # a planned site: RSRP decides, never its status
        if not located or not coverage:
            return NOT_CHECKED                # no location: RSRP is ignored
        if point is None:
            return NOT_SOLVE                  # no RSRP grid at the location: poor coverage
        return _coverage_verdict(point)

    if check == "flow_control":
        if flow is None:
            return NOT_CHECKED
        return NOT_SOLVE if _flow_parts(flow)[0] else SOLVE

    if check == "interference":
        have4g = ev is not None and ev.hours and ev.stat(RSSI) is not None
        have3g = rtwp is not None
        if not have4g and not have3g:
            return NOT_CHECKED
        bad = (have4g and ev.rssi_hours > 0) or (have3g and _rtwp_parts(rtwp)[0] > 0)
        return NOT_SOLVE if bad else SOLVE

    if check == "utilization":
        if not serving:
            return NOT_CHECKED
        return _utilization_verdict(ev)

    return NOT_CHECKED


def issue_hours(check: str, ev: Evidence | None, flow: tuple | None = None,
                rtwp: tuple | None = None) -> float:
    """How many hours the ticket's problem had an issue over the analysed
    period (the hourly analysis); NaN where the check has no hours."""
    if check == "utilization":
        return float(len(ev.issue_hours(PRB))) if ev is not None and ev.stat(PRB) else float("nan")
    if check == "flow_control":
        return float(_flow_parts(flow)[0]) if flow is not None else float("nan")
    if check == "interference":
        hours = set(ev.issue_hours(RSSI)) if ev is not None else set()
        if rtwp is not None and len(rtwp) > 3:
            hours |= set(rtwp[3])
        have = (ev is not None and ev.stat(RSSI) is not None) or rtwp is not None
        return float(len(hours)) if have else float("nan")
    return float("nan")


def _utilization_verdict(ev: Evidence | None) -> str:
    if ev is None or not ev.hours or pd.isna(ev.prb_max):
        return NOT_CHECKED
    return NOT_SOLVE if ev.prb_hours else SOLVE


def _coverage_verdict(point: Point) -> str:
    from rfopt.geo.coverage import poor_rsrp_line
    return NOT_SOLVE if point.rsrp <= poor_rsrp_line() else SOLVE


__all__ = ["CHECK_KPI", "CLOSURE_CODES", "CODE_CHECK", "Evidence", "FLOW", "HOW", "KPI_NAME",
           "NOT_CHECKED", "NOT_SOLVE", "NOT_ON_AIR", "NOT_TECHNICAL", "ON_AIR", "Point", "RTWP",
           "SOLVE", "Stat", "comment_check", "issue_hours", "reason_of", "site_rtwp",
           "angle_gap", "bearing", "canon_of", "check_of", "columns_for", "describe", "evidence_of",
           "flow_control_column", "fmt_metres", "is_planned", "judge", "kpi_columns",
           "metres_between", "plan_site_status", "point_of", "rsrp_at", "rsrp_frame", "rule_of",
           "sector_hours", "site_flow_control", "sleep_population", "split_sector", "wake_after"]
