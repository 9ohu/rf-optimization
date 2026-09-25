"""Sleep Analysis — the tickets put to sleep, and what the network says now.

The page reads the History of Tickets export already in Data Resources, keeps
the tickets closed under the eight sleep closure codes, and runs the check
that fits each one against measured data: the serving sector's hourly KPIs
(the same exports Draw Data reads), the 3G flow-control counter, the measured
coverage grid where the subscriber was, and the site KMZ for a planned site.
`rfopt.sleep.analysis` does the judging; this module wires the resources to
it, caches the heavy passes, and draws the page.

Nothing is uploaded here and nothing is written: every dataset comes from Data
Resources, and a ticket whose check has nothing to read says "Not Checked"
rather than being counted as solved.
"""

from __future__ import annotations

import html
import math
import re

import numpy as np
import pandas as pd
import streamlit as st

import _kpi_bulk as B
import _kpi_workspace as W
import _resources as R
import _ticket_table as T
from _charts import kpi_figure
from _ui import kpi_card
from rfopt.kpi.trends import panels_for
from rfopt.sleep import analysis as A

SOLVE, NOT_SOLVE, NOT_CHECKED = A.SOLVE, A.NOT_SOLVE, A.NOT_CHECKED
STATES = (NOT_SOLVE, SOLVE, NOT_CHECKED)
TONE = {SOLVE: "#22C55E", NOT_SOLVE: "#EF4444", NOT_CHECKED: "#F59E0B"}
BAR = ["#F59E0B", "#FBBF24", "#1597FF", "#20BFFF", "#A78BFA", "#22C55E", "#F472B6", "#64748B"]
OPEN = "sl_open"                     # the ticket the detail panel is showing

# the KPI Trend offers these and nothing else; the first three are the serving
# sector's own cells, the last two are counted for the NodeB, so they are the
# site's own line
TRENDS = [("PRB Utilization", "4G", A.PRB), ("4G Availability", "4G", A.AVAIL),
          ("4G Interference", "4G", A.RSSI), ("Flow Control", "3G", A.FLOW),
          ("RTWP", "3G", A.RTWP)]

# the table, in the order the R5 team asked for it
COLUMNS = [
    # the ticket first: the table pins its first column, and the R5 team asked
    # for the Ticket ID to stay in view while the rest scrolls sideways
    ("hpsm_id", "Ticket ID", "text", 146),
    ("user", "User", "text", 172),
    ("site_id", "Site ID", "text", 104),
    ("city", "Cite", "text", 112),
    ("serving", "Serving Sector", "text", 150),
    ("plan_site", "Plan Site", "text", 116),
    ("rf_analysis", "RF Analysis", "text", 166),
    ("closure_code", "Closure Code", "text", 260),
    ("longitude", "Log", "num", 108),
    ("latitude", "Lat", "num", 104),
    ("diag_submit", "Diagnostic Submit Time", "time", 198),
    ("diag_create", "Create Time", "time", 150),
    ("expected", "Expected Resolution Date", "time", 212),
    ("wake_after", "Wake After", "num", 122),
    ("problem_time", "Problem Time", "time", 150),
    ("status", "Status", "text", 110),
    ("distance", "Distance", "text", 118),
    ("verdict", "Site Issue", "verdict", 126),
    ("plan_status", "Plan Site Status", "air", 158),
    ("rsrp", "RSRP", "num", 104),
    ("description", "Description", "comment", 420),
]

CSS = """
<style>
.sl-bars { display: flex; flex-direction: column; gap: 6px; }
.sl-bar { display: grid; grid-template-columns: 1fr 60px; align-items: center; gap: 10px;
    font-size: 11.5px; color: #CBD5E1; }
.sl-bar .sl-t { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sl-track { grid-column: 1 / -1; height: 9px; border-radius: 5px; background: #0D2945;
    border: 1px solid #16324F; overflow: hidden; }
.sl-track i { display: block; height: 100%; background: var(--c); border-radius: 5px; }
.sl-bar b { text-align: right; color: #F1F5F9; font-variant-numeric: tabular-nums; }
.sl-legend { display: flex; gap: 14px; align-items: center; color: #94A3B8; font-size: 11.5px; }
.sl-legend i { width: 8px; height: 8px; border-radius: 50%; display: inline-block;
    margin-right: 5px; background: var(--c); }
.sl-head { display: flex; align-items: center; justify-content: space-between; gap: 12px;
    margin: 2px 0 6px; }
.sl-title { font-size: 16px; font-weight: 700; color: #F1F5F9; }
.sl-title small { font-weight: 500; font-size: 12px; color: #94A3B8; margin-left: 8px; }
.sl-sec { display: flex; align-items: center; gap: 8px; font: 700 12.5px 'Segoe UI', system-ui,
    sans-serif; color: #F1F5F9; margin-bottom: 8px; }
.sl-sec span.n { flex: 0 0 20px; height: 20px; border-radius: 50%; display: flex;
    align-items: center; justify-content: center; font-size: 11px; color: #071525;
    background: #1597FF; }
.sl-sec small { font-weight: 500; color: #94A3B8; }
.sl-id { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.sl-id b { font-size: 21px; color: #F8FAFC; }
.sl-facts { display: flex; flex-wrap: wrap; gap: 4px 22px; margin-top: 6px; }
.sl-facts div { font-size: 11.5px; color: #94A3B8; }
.sl-facts div b { display: block; font-size: 12.5px; font-weight: 600; color: #E2E8F0; }
.sl-chip { display: inline-flex; align-items: center; gap: 6px; padding: 5px 14px;
    border-radius: 8px; font: 700 13px 'Segoe UI', system-ui, sans-serif; color: var(--c);
    background: color-mix(in srgb, var(--c) 15%, transparent);
    border: 1px solid color-mix(in srgb, var(--c) 45%, transparent); }
.sl-kpi { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.sl-kpi > div { background: #0D2945; border: 1px solid #16324F; border-radius: 8px;
    padding: 7px 10px; }
.sl-kpi .k { font-size: 10.5px; color: #94A3B8; white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }
.sl-kpi .v { font-size: 17px; font-weight: 700; color: var(--c, #F8FAFC); }
.sl-kpi .v small { font-size: 11px; font-weight: 600; color: #94A3B8; margin-left: 3px; }
.sl-donut { display: flex; gap: 14px; align-items: center; }
.sl-dleg { display: flex; flex-direction: column; gap: 6px; flex: 1; min-width: 0; }
.sl-dleg > div { display: grid; grid-template-columns: 9px 1fr auto; gap: 8px;
    align-items: baseline; font-size: 11.5px; color: #CBD5E1; }
.sl-dleg i { width: 9px; height: 9px; border-radius: 50%; background: var(--c); }
.sl-dleg span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sl-dleg b { color: #F1F5F9; font-variant-numeric: tabular-nums; }
.sl-dleg small { grid-column: 2 / -1; margin-top: -4px; color: #64748B; font-size: 10.5px; }
.sl-note { color: #94A3B8; font-size: 11.5px; line-height: 1.5; }
.sl-desc { color: #E2E8F0; font-size: 12.5px; line-height: 1.6; }
.sl-desc i { width: 8px; height: 8px; border-radius: 50%; display: inline-block;
    margin-right: 7px; background: var(--c); }
.st-key-sl_map iframe { border-radius: 8px; }
.st-key-sl_map { position: relative; }
.st-key-sl_fsbtn { position: absolute; top: 18px; right: 10px; z-index: 5; width: auto; }
.st-key-sl_fsbtn button { min-height: 32px; height: 32px; width: 32px; padding: 0;
    background: #0D2945; color: #E2E8F0; border: 1px solid #1E3A5F; border-radius: 9px;
    box-shadow: 0 3px 12px rgba(0, 0, 0, .45); }
.st-key-sl_fsbtn button:hover { background: #15406B; color: #F8FAFC; border-color: #1E3A5F; }
</style>
"""


def esc(x) -> str:
    return html.escape(str(x))


# --------------------------------------------------------------------------- #
# the data behind the page
# --------------------------------------------------------------------------- #
def four_g() -> list:
    return [(b, i) for b, i in W.combine(R.kpi_groups()) if i.kind == "4G"]


def three_g() -> list:
    return [(b, i) for b, i in W.combine(R.kpi_groups()) if i.kind == "3G"]


@st.cache_resource(show_spinner=False, max_entries=2)
def problem_times(path: str, _sha: str) -> pd.Series:
    """Ticket -> Problem Time (column AA). The history reader does not carry
    it, so it is read from the same file by its own header, and nothing about
    how the rest of the app reads that file changes."""
    from rfopt.complaints.history import _letter_index, _norm

    try:
        raw = pd.read_excel(path, sheet_name=0, dtype=str, engine="calamine")
    except Exception:
        try:
            raw = pd.read_excel(path, sheet_name=0, dtype=str)
        except Exception:
            return pd.Series(dtype="datetime64[ns]")
    heads = [str(c) for c in raw.columns]
    names = {_norm(h): h for h in heads}
    col = names.get("problem time")
    if col is None:
        j = _letter_index("AA")
        col = heads[j] if j < len(heads) else None
    ids = names.get("hpsm incident id")
    if ids is None:
        k = _letter_index("BF")
        ids = heads[k] if k < len(heads) else None
    if col is None or ids is None:
        return pd.Series(dtype="datetime64[ns]")
    # the column is ISO with a Z on it ("2026-01-02T17:39:27.000Z") while every
    # other time in the file is bare; the clock reads the same, so the marker
    # is dropped rather than shifting the hour
    when = pd.to_datetime(raw[col], errors="coerce", utc=True)
    out = pd.DataFrame({"id": raw[ids].astype(str).str.strip(),
                        "t": when.dt.tz_localize(None)})
    out = out[out["id"].ne("") & out["id"].str.lower().ne("nan")]
    return out.drop_duplicates("id").set_index("id")["t"]


@st.cache_resource(show_spinner="Reading the KPI of every sector…", max_entries=2)
def sector_facts(key: tuple) -> dict:
    """Every serving sector the 4G export measures: its hours, and the cells
    behind it. The EP tracker says which cells a sector holds, exactly as Bulk
    Draw reads it."""
    four = four_g()
    if not four:
        return {"evidence": {}, "cells": {}, "cols": {}, "window": (None, None), "objects": None}
    kpis = []
    for _, info in four:
        kpis += [k for k in info.all_kpis if k not in kpis]
    cols = A.kpi_columns(kpis)
    if not cols:
        return {"evidence": {}, "cells": {}, "cols": {}, "window": (None, None), "objects": None}
    data = pd.concat([W.raw(b, tuple(cols.values())) for b, _ in four], ignore_index=True)
    index = pd.concat([W.index_of(b) for b, _ in four], ignore_index=True)
    objects = B.objects_of(index, B.ep_cells("4G"))
    hours = A.sector_hours(data, objects, cols)
    cells: dict = {}
    for site, num, obj in zip(objects["site"], objects["sector"], objects["object"]):
        if pd.notna(num) and num > 0:
            cells.setdefault((str(site), float(num)), []).append(str(obj))
    window = ((data["datetime"].min(), data["datetime"].max()) if len(data) else (None, None))
    return {"evidence": A.evidence_of(hours, cells), "cells": cells, "cols": cols,
            "window": window, "objects": objects}


def three_columns() -> dict:
    """The 3G export's flow-control counter and its RTWP, by their own names."""
    kpis = []
    for _, info in three_g():
        kpis += [k for k in info.all_kpis if k not in kpis]
    out = {}
    flow = A.flow_control_column(kpis)
    if flow:
        out[A.FLOW] = flow
    rtwp = next((str(k) for k in kpis if "rtwp" in str(k).lower()), None)
    if rtwp:
        out[A.RTWP] = rtwp
    return out


@st.cache_resource(show_spinner=False, max_entries=2)
def flow_facts(key: tuple) -> dict:
    """site -> (hours with a flow-control drop, the worst hour, the average hour), from 3G."""
    three = three_g()
    if not three:
        return {}
    kpis = []
    for _, info in three:
        kpis += [k for k in info.all_kpis if k not in kpis]
    column = A.flow_control_column(kpis)
    if not column:
        return {}
    data = pd.concat([W.raw(b, (column,)) for b, _ in three], ignore_index=True)
    if "site_id" not in data.columns:
        return {}
    return A.site_flow_control(data, column, data["site_id"].astype(str))


@st.cache_resource(show_spinner="Checking the sleep tickets…", max_entries=2)
def analysed(key: tuple, _history: pd.DataFrame, _facts: dict, _flow: dict,
             _grids: list, _kmz: pd.DataFrame | None, _times: pd.Series,
             _sites: pd.DataFrame | None = None) -> pd.DataFrame:
    """Every sleep ticket with its verdict, the RSRP measured where the
    subscriber was, the status of its planned site and the comment that says
    what the check read."""
    pop = A.sleep_population(_history)
    if pop.empty:
        return pop
    near = A.rsrp_frame(pop["latitude"], pop["longitude"], _grids)
    # how far the subscriber was from the site that served them
    where = (_sites if _sites is not None else pd.DataFrame()).reindex(pop["site"])
    pop["site_lat"] = where["latitude"].to_numpy() if len(where.columns) else np.nan
    pop["site_lon"] = where["longitude"].to_numpy() if len(where.columns) else np.nan
    pop["metres"] = [A.metres_between(a, b, c, d) for a, b, c, d in
                     zip(pd.to_numeric(pop["latitude"], errors="coerce"),
                         pd.to_numeric(pop["longitude"], errors="coerce"),
                         pop["site_lat"], pop["site_lon"])]
    pop["distance"] = pop["metres"].map(A.fmt_metres)
    ev = _facts.get("evidence", {})
    plan_cache: dict = {}
    verdicts, why, status, rsrp = [], [], [], []
    for n, r in enumerate(pop.itertuples(index=False)):
        site, num = r.site, r.sector_num
        one = ev.get((site, num)) if site and pd.notna(num) else None
        point = A.point_of(near.iloc[n])
        plan = ""
        if r.plan_site:
            if r.plan_site not in plan_cache:
                plan_cache[r.plan_site] = A.plan_site_status(r.plan_site, _kmz)
            plan = plan_cache[r.plan_site]
        v, text = A.judge(r.check, r.serving, one, point, plan, r.plan_site,
                          _flow.get(site) if site else None, metres=r.metres)
        verdicts.append(v)
        why.append(text)
        status.append(plan)
        rsrp.append(round(point.rsrp, 1) if point is not None else np.nan)
    pop["verdict"] = verdicts
    pop["description"] = why
    pop["plan_status"] = status
    pop["rsrp"] = rsrp
    pop["rsrp_m"] = near["metres"].to_numpy()
    pop["mr"] = near["mr"].to_numpy()
    pop["problem_time"] = (pd.to_datetime(pop["hpsm_id"].map(_times), errors="coerce")
                           if len(_times) else pd.NaT)
    return pop


def page_data():
    """(the analysed tickets, a key for the table, what is missing, the sector
    facts, where every site stands) — the tickets are None where the history
    itself is not in Data Resources yet."""
    hist_file = R.history_file()
    if hist_file is None:
        return None, "", [], {}, None
    history = R.history()
    four, three = four_g(), three_g()
    kpi_key = tuple(W.src_key(b) for b, _ in four)
    facts = sector_facts((kpi_key, W.ep_key()))
    flow = flow_facts(tuple(W.src_key(b) for b, _ in three))
    kept = R.coverage()                 # sha1 -> the measured grid
    grids = list(kept.values())
    kmz_file = R.kmz_file()
    kmz = None if kmz_file is None else _kmz_sites(str(kmz_file.path), kmz_file.sha1)
    times = problem_times(str(hist_file.path), hist_file.sha1)
    sites = site_points(W.ep_path() or "", kmz_file.sha1 if kmz_file else "", kmz)
    key = (hist_file.sha1, kpi_key, W.ep_key(), tuple(sorted(kept)),
           kmz_file.sha1 if kmz_file else "", len(times), len(sites))
    df = analysed(key, history, facts, flow, grids, kmz, times, sites)
    missing = []
    if not four:
        missing.append("4G KPI Data")
    if not grids:
        missing.append("Coverage Data")
    if kmz is None:
        missing.append("KMZ Data")
    if not three:
        missing.append("3G KPI Data")
    return df, hist_file.sha1, missing, facts, sites


@st.cache_resource(show_spinner=False, max_entries=2)
def site_points(ep_path: str, _kmz_sha: str, _kmz: pd.DataFrame | None) -> pd.DataFrame:
    """site -> where it stands. The EP tracker first (it carries every sector's
    own coordinates), the site KMZ for a site the tracker does not list."""
    frames = []
    if ep_path:
        ep = _ep_sectors(ep_path)
        if len(ep):
            frames.append(ep.groupby("site_id", as_index=False)[["latitude", "longitude"]]
                          .first())
    if _kmz is not None and len(_kmz):
        k = _kmz[["site_id", "latitude", "longitude"]].copy()
        k["site_id"] = k["site_id"].astype(str).str.upper()
        frames.append(k)
    if not frames:
        return pd.DataFrame(columns=["site_id", "latitude", "longitude"]).set_index("site_id")
    out = pd.concat(frames, ignore_index=True).dropna(subset=["latitude", "longitude"])
    return out.drop_duplicates("site_id").set_index("site_id")


@st.cache_resource(show_spinner=False, max_entries=2)
def _kmz_sites(path: str, _sha: str) -> pd.DataFrame:
    from rfopt.ingest.kmz_sites import load_kmz_sites
    return load_kmz_sites(path).sites


# --------------------------------------------------------------------------- #
# the filters
# --------------------------------------------------------------------------- #
def _choices(df: pd.DataFrame, field: str) -> list:
    got = df[field].astype(str).str.strip()
    return sorted(x for x in got.unique() if x)


def filter_bar(df: pd.DataFrame) -> dict:
    """The filters over the page, each one a multi-select over what is there."""
    c_user, c_city, c_sup, c_group = st.columns([1.2, 1, 1.2, 1], gap="small")
    c_code, c_rf, c_state, c_date = st.columns([1.5, 1.2, 1.05, 1.25], gap="small")
    picks = {}
    for col, label, field in ((c_user, "User", "user"), (c_city, "City", "city"),
                              (c_sup, "Sup District", "sup_district"),
                              (c_group, "Group", "group"),
                              (c_code, "Closure Code", "closure_code"),
                              (c_rf, "RF Analysis", "rf_analysis")):
        with col:
            picks[field] = st.multiselect(label, _choices(df, field), key=f"sl_{field}",
                                          placeholder="All")
    with c_state:
        picks["verdict"] = st.multiselect("Status", list(STATES), key="sl_state",
                                          placeholder="All")
    when = df["diag_create"].dropna()
    lo = when.min().date() if len(when) else None
    hi = when.max().date() if len(when) else None
    with c_date:
        if lo is not None:
            got = st.session_state.get("sl_dates")
            if not (isinstance(got, (tuple, list)) and len(got) == 2
                    and lo <= got[0] <= got[1] <= hi):
                st.session_state["sl_dates"] = (lo, hi)
            st.date_input("Ticket Date", min_value=lo, max_value=hi, key="sl_dates",
                          format="YYYY-MM-DD")
        else:
            st.date_input("Ticket Date", value=(), disabled=True, key="sl_dates")
    picks["dates"] = st.session_state.get("sl_dates")
    return picks


def apply_filters(df: pd.DataFrame, picks: dict) -> pd.DataFrame:
    out = df
    for field in ("user", "city", "sup_district", "group", "closure_code", "rf_analysis",
                  "verdict"):
        chosen = picks.get(field) or []
        if chosen:
            out = out[out[field].astype(str).isin(chosen)]
    dates = picks.get("dates")
    if isinstance(dates, (tuple, list)) and len(dates) == 2:
        lo, hi = pd.Timestamp(dates[0]), pd.Timestamp(dates[1]) + pd.Timedelta(days=1)
        when = out["diag_create"]
        out = out[when.isna() | ((when >= lo) & (when < hi))]
    return out


def searched(df: pd.DataFrame, query: str) -> pd.DataFrame:
    q = str(query or "").strip().lower()
    if not q:
        return df
    fields = ("hpsm_id", "site_id", "serving", "city", "sup_district", "user",
              "closure_code", "rf_analysis", "plan_site")
    hit = pd.Series(False, index=df.index)
    for f in fields:
        hit |= df[f].astype(str).str.lower().str.contains(q, regex=False, na=False)
    return df[hit]


# --------------------------------------------------------------------------- #
# the summary: the cards, the closure codes, the users
# --------------------------------------------------------------------------- #
def summary_cards(df: pd.DataFrame) -> None:
    """The four counts of the header, as the reference lays them out."""
    n = len(df)
    got = {v: int(df["verdict"].eq(v).sum()) for v in STATES}
    cols = st.columns(4, gap="small")
    specs = [("Total Sleep Tickets", n, "clock", "#FBBF24", "closed as Sleep, eight codes", None),
             ("Resolved", got[SOLVE], "check", TONE[SOLVE], "the check finds no issue now",
              100.0 * got[SOLVE] / n if n else None),
             ("Still Issue", got[NOT_SOLVE], "alert", TONE[NOT_SOLVE],
              "the evidence still shows it", 100.0 * got[NOT_SOLVE] / n if n else None),
             ("Pending (Not Checked)", got[NOT_CHECKED], "info", TONE[NOT_CHECKED],
              "no data to check it against", 100.0 * got[NOT_CHECKED] / n if n else None)]
    for col, (title, value, icon, tone, note, share) in zip(cols, specs):
        with col:
            kpi_card(title, f"{value:,}", icon=icon, tone=tone, note=note, pct=share)


def bar_list(pairs: list, total: int, colours=BAR) -> str:
    """The rows of a horizontal bar chart: the label, its bar and its count."""
    top = max([v for _, v in pairs] or [1])
    out = []
    for k, (label, value) in enumerate(pairs):
        colour = colours[k % len(colours)]
        out.append(f'<div class="sl-bar"><span class="sl-t" title="{esc(label)}">{esc(label)}'
                   f'</span><b>{value:,}</b>'
                   f'<span class="sl-track"><i style="--c:{colour};'
                   f'width:{100.0 * value / top:.1f}%"></i></span></div>')
    return f'<div class="sl-bars">{"".join(out)}</div>'


def by_closure(df: pd.DataFrame) -> str:
    counts = df["closure_code"].value_counts()
    pairs = [(c, int(counts.get(c, 0))) for c in A.CLOSURE_CODES if counts.get(c, 0)]
    pairs.sort(key=lambda p: -p[1])
    return bar_list(pairs, len(df))


def by_rf(df: pd.DataFrame, most: int = 8) -> str:
    """Tickets by RF Analysis — column FE of the export, never the closure code."""
    counts = df[df["rf_analysis"].astype(str).str.strip().ne("")]["rf_analysis"].value_counts()
    return bar_list([(k, int(v)) for k, v in counts.head(most).items()], len(df),
                    ["#20BFFF", "#1597FF", "#A78BFA", "#F472B6", "#22C55E", "#FBBF24",
                     "#FB923C", "#64748B"])


def by_user(df: pd.DataFrame, most: int = 8) -> str:
    counts = df[df["user"].astype(str).str.strip().ne("")]["user"].value_counts().head(most)
    return bar_list([(k, int(v)) for k, v in counts.items()], len(df),
                    ["#1597FF", "#20BFFF", "#A78BFA", "#F472B6", "#22C55E", "#FBBF24",
                     "#FB923C", "#64748B"])


def legend() -> str:
    return ('<div class="sl-legend">' + "".join(
        f'<span><i style="--c:{TONE[v]}"></i>{v}</span>' for v in STATES) + "</div>")


# --------------------------------------------------------------------------- #
# the table
# --------------------------------------------------------------------------- #
def for_table(df: pd.DataFrame) -> pd.DataFrame:
    """The frame the table shows: the columns it names, and nothing invented —
    a value the data does not carry stays empty and the cell reads "-"."""
    out = df.copy()
    out["plan_site"] = out["plan_site"].replace("", pd.NA).fillna("")
    out["plan_status"] = out["plan_status"].replace("", pd.NA).fillna("")
    out["rsrp"] = out["rsrp"].map(lambda v: "" if pd.isna(v) else f"{v:g}")
    out["distance"] = out["distance"].replace("-", "")
    out["wake_after"] = out["wake_after"].map(lambda v: "" if pd.isna(v) else f"{v:.0f}")
    for f in ("longitude", "latitude"):
        out[f] = pd.to_numeric(out[f], errors="coerce").map(
            lambda v: "" if pd.isna(v) else f"{v:.5f}")
    return out


def table(df: pd.DataFrame, sha1: str, found: pd.DataFrame | None, query: str,
          open_id: str | None, title: str = "Sleep Tickets List") -> None:
    T.show(df, f"sleep.{sha1}", found=found, query=query, open_id=open_id,
           title=title, key="sl_table", columns=COLUMNS, open_key=OPEN,
           sort_field="diag_create")


# --------------------------------------------------------------------------- #
# the selected ticket
# --------------------------------------------------------------------------- #
def fact(label: str, value) -> str:
    text = "-" if value in (None, "", "nan") or (isinstance(value, float) and math.isnan(value)) \
        else value
    return f"<div>{esc(label)}<b>{esc(text)}</b></div>"


def when(value, fmt: str = "%Y-%m-%d %H:%M") -> str:
    t = pd.to_datetime(value, errors="coerce")
    return "-" if pd.isna(t) else f"{t:{fmt}}"


def head(row: pd.Series) -> None:
    """Which ticket this is, and what it was closed on."""
    left, right = st.columns([5, 1.2], gap="small", vertical_alignment="center")
    with left:
        st.html(f'<div class="sl-id"><b>{esc(row["serving"] or row["site_id"] or "—")}</b>'
                f'<span class="sl-note">Selected ticket {esc(row["hpsm_id"])}</span></div>'
                '<div class="sl-facts">'
                + fact("Site ID", row["site_id"])
                + fact("Cite", row["city"])
                + fact("Serving Sector", row["serving"])
                + fact("Plan Site", row["plan_site"] or "-")
                + fact("Closure Code", row["closure_code"])
                + fact("User", row["user"])
                + fact("Create Time", when(row["diag_create"]))
                + fact("Expected Resolution", when(row["expected"], "%Y-%m-%d"))
                + fact("Wake After", "-" if pd.isna(row["wake_after"])
                       else f"{row['wake_after']:.0f} day(s)")
                + "</div>")
    with right:
        v = row["verdict"]
        st.html(f'<div class="sl-chip" style="--c:{TONE[v]}">{esc(v)}</div>')


def kpi_state(ev, row: pd.Series) -> list:
    """The judged KPIs of the serving sector: (name, what it reads, is it an
    issue). Only what the loaded exports actually measure."""
    out = []
    for canon in (A.PRB, A.RSSI, A.AVAIL):
        stat = ev.stat(canon) if ev is not None else None
        if stat is None:
            continue
        rule = A.rule_of(canon)
        up = rule is not None and rule.direction == "up"
        value = stat.low if up else stat.top
        unit = "%" if canon in (A.PRB, A.AVAIL) else " dBm"
        dec = 2 if canon == A.AVAIL else (1 if canon == A.RSSI else 0)
        text = f"{value:,.{dec}f}{unit}"
        note = (f"{stat.over} h at/over {rule.critical:g}" if rule is not None and not up
                else (f"{stat.over} h under {rule.critical:g}" if rule is not None else ""))
        out.append((A.KPI_NAME[canon], text, stat.over > 0, note))
    if not pd.isna(row.get("rsrp")):
        rule = A.rule_of(A.RSRP_RULE)
        line = rule.warning if rule is not None else -105.0
        note = ("" if pd.isna(row.get("rsrp_m")) else
                f"{row['rsrp_m']:.0f} m away · {int(row['mr']):,} MRs")
        out.append(("RSRP at the subscriber", f"{row['rsrp']:g} dBm", row["rsrp"] <= line, note))
    return out


def kpi_donut(ev, row: pd.Series) -> str:
    """Panel 1: how many of the serving sector's KPIs are past the operator's
    line and how many are not — the reading of each beside the ring."""
    from _ticket_history import donut

    state = kpi_state(ev, row)
    if not state:
        return ('<div class="sl-note">No KPI data for this serving sector in the loaded '
                "exports.</div>")
    bad = sum(1 for *_, issue, _note in state)
    parts = [("Issue", bad, TONE[NOT_SOLVE]), ("Normal", len(state) - bad, TONE[SOLVE])]
    rows = "".join(
        f'<div style="--c:{TONE[NOT_SOLVE] if issue else TONE[SOLVE]}"><i></i>'
        f'<span title="{esc(name)}">{esc(name)}</span><b>{esc(value)}</b>'
        f'<small>{esc(note)}</small></div>' for name, value, issue, note in state)
    return (f'<div class="sl-donut">{donut(parts, len(state), size=124, thick=24, centre="KPIs")}'
            f'<div class="sl-dleg">{rows}</div></div>')


def comparison(facts: dict, row: pd.Series, sites: pd.DataFrame | None = None, *,
               near_m: float = 2000.0, most: int = 10) -> pd.DataFrame:
    """Panel 2: the serving sector, the rest of its site, and the sectors of
    the sites around it — each on the KPI this ticket is about.

    The KPI compared is the one the ticket's check judges (a PRB ticket is
    compared on PRB); where the loaded export does not carry it, the sector's
    PRB stands in, since that is what it measures.
    """
    ev = facts.get("evidence", {})
    site = str(row["site"] or "")
    if not ev or not site:
        return pd.DataFrame()
    canon = A.CHECK_KPI.get(row.get("check"), A.PRB) or A.PRB
    if not any(canon in e.stats for e in ev.values()):
        canon = A.PRB
    rule = A.rule_of(canon)
    up = rule is not None and rule.direction == "up"
    dec = 2 if canon == A.AVAIL else (1 if canon == A.RSSI else 0)
    here = (pd.to_numeric(row["latitude"], errors="coerce"),
            pd.to_numeric(row["longitude"], errors="coerce"))
    at = sites if sites is not None else pd.DataFrame()

    def where(of_site: str):
        if of_site in getattr(at, "index", ()):
            r = at.loc[of_site]
            return float(r["latitude"]), float(r["longitude"])
        return None

    home = where(site)
    near = []
    for (s_id, num), e in ev.items():
        if s_id == site:
            near.append((0.0, s_id, num, e))
            continue
        spot = where(s_id)
        if spot is None or home is None:
            continue
        gap = A.metres_between(home[0], home[1], spot[0], spot[1])
        if not pd.isna(gap) and 0 < gap <= near_m:
            near.append((gap, s_id, num, e))
    near.sort(key=lambda x: (x[0], x[1], x[2]))

    rows = []
    for gap, s_id, num, e in near[:most + 8]:
        serving = s_id == site and num == row["sector_num"]
        kind = "Serving Sector" if serving else ("Same Site" if s_id == site else "Neighbor Site")
        stat = e.stat(canon)
        spot = where(s_id)
        far = (A.metres_between(here[0], here[1], spot[0], spot[1])
               if spot is not None else float("nan"))
        other = [A.KPI_NAME.get(c, c) for c in e.issues() if c != canon]
        rows.append({
            "Sector Type": kind,
            "Sector": f"{s_id}-{num:g}",
            "Cell Name": ", ".join(e.cells) if e.cells else "-",
            "KPI Issue": A.KPI_NAME.get(canon, canon),
            "Max Value": "-" if stat is None else f"{(stat.low if up else stat.top):,.{dec}f}",
            "Average Value": "-" if stat is None else f"{stat.mean:,.{dec}f}",
            "Distance": A.fmt_metres(far),
            "Another Issue Impacted": ", ".join(other) if other else "-",
            "Status": "Issue" if stat is not None and stat.over > 0 else "Normal",
        })
    rows.sort(key=lambda r: {"Serving Sector": 0, "Same Site": 1}.get(r["Sector Type"], 2))
    return pd.DataFrame(rows[:most])


def comparison_style(table: pd.DataFrame):
    """The serving sector's row in green, the way the page marks it."""
    def paint(r):
        green = r["Sector Type"] == "Serving Sector"
        return [f"background-color: rgba(34,197,94,.16); color: #DCFCE7" if green else ""
                for _ in r]
    return table.style.apply(paint, axis=1)


def trend_choices(facts: dict) -> list:
    """Which of the five the loaded exports can actually draw."""
    four, three = facts.get("cols", {}), three_columns()
    out = []
    for label, tech, canon in TRENDS:
        if (tech == "4G" and canon in four) or (tech == "3G" and canon in three):
            out.append((label, tech, canon))
    return out


def trend_panel(facts: dict, row: pd.Series, pick: tuple):
    """Panel 3: the serving sector's own hours, drawn as Draw Data draws them
    — a line per cell, hour by hour, never a daily average. Flow control and
    RTWP are counted for the NodeB, so those two draw the site's own line."""
    if not pick:
        return None
    _label, tech, canon = pick
    site = str(row["site"] or "")
    if tech == "3G":
        column = three_columns().get(canon)
        srcs = three_g()
        if not column or not srcs or not site:
            return None
        data = pd.concat([W.raw(b, (column,)) for b, _ in srcs], ignore_index=True)
        part = data[data["site_id"].astype(str).str.upper().eq(site)]
        if part.empty or part[column].notna().sum() == 0:
            return None
        panels = panels_for(part, [column], level="Site", obj=site)
        return panels[0] if panels else None
    column = facts.get("cols", {}).get(canon)
    cells = facts.get("cells", {}).get((site, row["sector_num"]), [])
    if not column or not cells:
        return None
    srcs = four_g()
    if not srcs:
        return None
    data = pd.concat([W.raw(b, (column,)) for b, _ in srcs], ignore_index=True)
    part = data[data["object"].astype(str).isin(set(cells))]
    if part.empty or part[column].notna().sum() == 0:
        return None
    panels = panels_for(part, [column], cells=list(cells))
    return panels[0] if panels else None


def chart_head(card: str, title: str, note: str, stem: str) -> str:
    """A chart's title bar with Copy Chart, the one Draw Data uses."""
    return (f"<div class='sm-chart-head'><span class='t'>{esc(title)}</span>"
            f"<span class='sm-verdict'>{esc(note)}</span>"
            f"<button class='rf-copy' data-card='{card}' data-file='{esc(_safe(stem))}' "
            "title='Copy this chart as a picture — paste it into email, WhatsApp, "
            "PowerPoint or a report'><span>Copy Chart</span></button></div>")


def _safe(text: str) -> str:
    return re.sub(r"\s+", "_", re.sub(r'[\\/:*?"<>|]', "_", str(text)).strip())[:80]


def sector_geometry(site: str) -> pd.DataFrame:
    """Where the site is and which way its sectors point, from the EP tracker
    (the same file the rest of the app reads). Empty when it does not carry it."""
    path = W.ep_path()
    if not path or not site:
        return pd.DataFrame()
    ep = _ep_sectors(path)
    return ep[ep["site_id"].eq(str(site).upper())]


def neighbours(site: str, *, near_m: float = 1200.0, most: int = 6) -> pd.DataFrame:
    """The sectors of the sites around this one, nearest first."""
    path = W.ep_path()
    if not path or not site:
        return pd.DataFrame()
    ep = _ep_sectors(path)
    home = ep[ep["site_id"].eq(str(site).upper())]
    if home.empty or ep.empty:
        return pd.DataFrame()
    lat, lon = float(home["latitude"].iloc[0]), float(home["longitude"].iloc[0])
    other = ep[ep["site_id"].ne(str(site).upper())].copy()
    dy = (other["latitude"] - lat) * 111_320.0
    dx = (other["longitude"] - lon) * 111_320.0 * math.cos(math.radians(lat))
    other["metres"] = (dy ** 2 + dx ** 2) ** 0.5
    close = other[other["metres"].le(near_m)].sort_values("metres")
    keep = list(dict.fromkeys(close["site_id"]))[:most]
    return close[close["site_id"].isin(keep)]


@st.cache_resource(show_spinner=False, max_entries=2)
def _ep_sectors(path: str) -> pd.DataFrame:
    from _shared import load_ep_all
    ep = load_ep_all(path)
    if ep is None or ep.empty:
        return pd.DataFrame(columns=["site_id", "sector_num", "azimuth_deg",
                                     "latitude", "longitude"])
    keep = ["site_id", "sector_num", "azimuth_deg", "latitude", "longitude"]
    d = ep[[c for c in keep if c in ep.columns]].copy()
    d["site_id"] = d["site_id"].astype(str).str.upper()
    for c in ("sector_num", "azimuth_deg", "latitude", "longitude"):
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["sector_num", "latitude", "longitude"])
    return d.groupby(["site_id", "sector_num"], as_index=False).first()


def _wedge(lat: float, lon: float, az: float, *, hbw: float = 60.0, metres: float = 260.0,
           steps: int = 14) -> list:
    """The beam of one sector, as the site map draws it: a wedge from the site
    around its azimuth."""
    out = [(lat, lon)]
    scale = math.cos(math.radians(lat)) or 1.0
    for k in range(steps + 1):
        a = math.radians(az - hbw / 2 + hbw * k / steps)
        dy = metres * math.cos(a) / 111_320.0
        dx = metres * math.sin(a) / (111_320.0 * scale)
        out.append((lat + dy, lon + dx))
    out.append((lat, lon))
    return out


SATELLITE, COVERAGE = "Satellite", "Coverage"

# full screen: the map panel — its title, the basemap switch, the map and the
# distance / azimuth note — pinned over the whole window. The map's frame is
# stretched here and the map inside fills its frame, so the view is kept.
FS_CSS = """
<style>
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
header[data-testid="stHeader"], [data-testid="stToolbar"] { display: none !important; }
section[data-testid="stMain"] { overflow: hidden !important; }
.st-key-sl_mapbox {
    position: fixed !important; inset: 0 !important; z-index: 2147483000 !important;
    background: #071525; padding: 12px 16px !important; overflow-y: auto;
    gap: 8px !important;
}
.st-key-sl_mapbox .st-key-sl_map iframe { height: calc(100vh - 128px) !important; }
</style>
"""

# drawn inside the map's own frame: the dark legend and popup cards the Sites
# map uses, kept apart from each other and from the map's controls
MAP_FRAME_CSS = """
html, body { margin: 0; }
#map_div, #map_div2, .folium-map { height: 100vh !important; }
.sm-legend { max-height: calc(100vh - 150px); overflow-y: auto; min-width: 0 !important;
    max-width: calc(100vw - 24px); box-sizing: border-box; font-size: 11px !important;
    padding: 7px 9px 6px !important; }
.sm-legend .sm-lg-t, .sm-legend .sm-lg-row, .sm-legend .sm-lg-note { white-space: normal; }
.sm-legend .sm-lg-r { min-width: 0 !important; }
.sl-pop-open .sm-legend > :not(.sm-lg-t) { display: none; }
.sl-pop-open .sm-legend .sm-lg-t { margin: 0; padding: 0; border: 0; }
.leaflet-popup-content { overflow-wrap: anywhere; max-height: calc(100vh - 90px);
    overflow-y: auto; }
.rf-cov-pop .rf-kvs b { white-space: normal; }
.leaflet-control-attribution { max-width: 45vw; font-size: 10px; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; }
.leaflet-control-attribution:hover { white-space: normal; }
"""


def _popup_fold():
    from branca.element import MacroElement
    from jinja2 import Template

    class PopupFold(MacroElement):
        """While a coverage read-out is open the legend folds to its title, so
        the two never sit on top of each other."""
        _template = Template("""
            {% macro script(this, kwargs) %}
            (function () {
              var m = {{ this._parent.get_name() }}, c = m.getContainer();
              m.on('popupopen', function () { c.classList.add('sl-pop-open'); });
              m.on('popupclose', function () { c.classList.remove('sl-pop-open'); });
            })();
            {% endmacro %}
        """)
    return PopupFold()


def _fs_button(fs: bool) -> None:
    with st.container(key="sl_fsbtn"):
        st.button(":material/fullscreen_exit:" if fs else ":material/fullscreen:",
                  key="sl_fs_toggle", help="Exit full screen" if fs else "Full screen",
                  on_click=lambda: st.session_state.update(
                      sl_fs=not st.session_state.get("sl_fs", False)))


def map_panel(row: pd.Series, height: int = 300, basemap: str = SATELLITE):
    """Panel 4: the site, the sector that served the ticket, the sites around
    it and where the subscriber was — on the satellite view or over the
    measured coverage grid. Every coordinate comes from the data."""
    import folium
    from streamlit_folium import st_folium

    from _map_assets import add_basemap

    sectors = sector_geometry(row["site"])
    lat = pd.to_numeric(row["latitude"], errors="coerce")
    lon = pd.to_numeric(row["longitude"], errors="coerce")
    here = None if pd.isna(lat) or pd.isna(lon) else (float(lat), float(lon))
    site_at = None
    if len(sectors):
        site_at = (float(sectors["latitude"].iloc[0]), float(sectors["longitude"].iloc[0]))
    if here is None and site_at is None:
        if st.session_state.get("sl_fs"):
            _fs_button(True)
        st.html('<div class="sl-note">No location on the ticket and no site in the EP '
                "tracker — nothing to draw.</div>")
        return
    centre = here or site_at
    fmap = folium.Map(location=list(centre), zoom_start=16, tiles=None, control_scale=True,
                      zoom_control=True)
    # the imagery under both, with the measured grid drawn over it on Coverage
    # — the same pair the Sites map offers
    add_basemap(fmap, "Esri.WorldImagery")
    from _kpi_map import LEGEND_CSS
    from _map_ui import MAP_CSS
    fmap.get_root().header.add_child(
        folium.Element(f"<style>{MAP_CSS}{LEGEND_CSS}{MAP_FRAME_CSS}</style>"))
    # while a coverage read-out is open the legend folds to its title, so the
    # two never sit on top of each other
    fmap.add_child(_popup_fold())
    if basemap == COVERAGE:
        kept = R.coverage()
        if kept:
            from _coverage import Coverage, CoverageLayer
            fmap.add_child(CoverageLayer(Coverage(kept).layer_config()))
        else:
            st.html('<div class="sl-note">No Coverage Data in Data Resources, so there is no '
                    "measured grid to draw.</div>")

    # the sites around this one, so the serving sector is seen in its place
    for n in neighbours(row["site"]).itertuples(index=False):
        folium.Polygon(_wedge(float(n.latitude), float(n.longitude),
                              float(n.azimuth_deg) if pd.notna(n.azimuth_deg) else 0.0,
                              metres=200.0),
                       color="#64748B", weight=1, fill=True, fill_color="#64748B",
                       fill_opacity=0.12,
                       tooltip=f"{n.site_id}-{n.sector_num:g} · neighbour").add_to(fmap)
        folium.CircleMarker([float(n.latitude), float(n.longitude)], radius=3,
                            color="#94A3B8", weight=1, fill=True, fill_color="#64748B",
                            fill_opacity=0.9, tooltip=str(n.site_id)).add_to(fmap)

    az = float("nan")
    for sec in sectors.itertuples(index=False):
        serving = float(sec.sector_num) == row["sector_num"]
        colour = "#20BFFF" if serving else "#1597FF"
        if serving:
            az = float(sec.azimuth_deg) if pd.notna(sec.azimuth_deg) else float("nan")
        folium.Polygon(_wedge(float(sec.latitude), float(sec.longitude),
                              float(sec.azimuth_deg) if pd.notna(sec.azimuth_deg) else 0.0),
                       color=colour, weight=2 if serving else 1, fill=True, fill_color=colour,
                       fill_opacity=0.45 if serving else 0.16,
                       tooltip=f"{row['site']}-{sec.sector_num:g}"
                               f"{' · serving' if serving else ''}").add_to(fmap)
    if site_at is not None:
        folium.CircleMarker(list(site_at), radius=5, color="#F8FAFC", weight=2, fill=True,
                            fill_color="#1597FF", fill_opacity=1,
                            tooltip=str(row["site"])).add_to(fmap)
    if here is not None:
        colour = TONE[row["verdict"]]
        folium.CircleMarker(list(here), radius=6, color="#0B1F33", weight=2, fill=True,
                            fill_color=colour, fill_opacity=1,
                            tooltip=(f"Subscriber {here[0]:.5f}, {here[1]:.5f}"
                                     + (f" · RSRP {row['rsrp']:g} dBm"
                                        if not pd.isna(row.get("rsrp")) else ""))).add_to(fmap)
        if site_at is not None:
            folium.PolyLine([list(site_at), list(here)], color="#F8FAFC", weight=1,
                            opacity=0.6, dash_array="4,4").add_to(fmap)
    seen = [list(p) for p in (here, site_at) if p is not None]
    if len(seen) > 1:
        fmap.fit_bounds(seen, padding=(40, 40))
    fs = bool(st.session_state.get("sl_fs"))
    with st.container(key="sl_map"):
        _fs_button(fs)
        st_folium(fmap, height=height, use_container_width=True, returned_objects=[],
                  key=f"sl_map_{row['hpsm_id']}_{basemap}")
    st.html(geometry_note(row, site_at, here, az))


def geometry_note(row: pd.Series, site_at, here, azimuth) -> str:
    """How far the subscriber is from the site, and how far off the serving
    sector's boresight they are."""
    bits = []
    if site_at is not None and here is not None:
        far = A.metres_between(site_at[0], site_at[1], here[0], here[1])
        to_user = A.bearing(site_at[0], site_at[1], here[0], here[1])
        bits.append(f"Distance to the serving site <b>{A.fmt_metres(far)}</b>")
        bits.append(f"bearing to the subscriber <b>{to_user:.0f}°</b>")
        if not pd.isna(azimuth):
            bits.append(f"serving azimuth <b>{azimuth:.0f}°</b> "
                        f"(off boresight <b>{A.angle_gap(azimuth, to_user):.0f}°</b>)")
    if not bits:
        return ('<div class="sl-note">The EP tracker does not carry this site, so the '
                "distance and the azimuth cannot be measured.</div>")
    return ('<div class="sl-note">' + " · ".join(bits) + ". Blue dot: the site · cyan wedge: "
            "the serving sector · grey wedges: the sites around it · the coloured dot: where "
            "the subscriber reported the problem.</div>")


def description_panel(row: pd.Series) -> str:
    return (f'<div class="sl-desc"><i style="--c:{TONE[row["verdict"]]}"></i>'
            f'{esc(row["description"])}</div>')
