"""KPI Analysis — the loaded KPI exports as a NOC workspace, and their report.

Two tabs on one page:

* **KPI Analysis**, top to bottom: the filters (technology in the bar; KPI,
  Governorate, Sup Districts, Site and Time Period below it), the KPI rings, KPI
  issues by Governorate, Sup District and City (counts, ranked bars and tables,
  and the map), the one KPI issues table with its search, and the KPI details of
  the site picked in the table or on the map.
* **Report Export** (`_kpi_report`): the same judgement set up as a report —
  its own filters and threshold, a slide-by-slide preview, the two-sheet Excel
  preview, and the PowerPoint and Excel exports.

One filter drives the KPI Analysis tab (`_kpi_filters`): every panel reads the
same filtered rows, so the rings, the tables, the map and the details always
agree. City is where a site is (its district), never a filter. Drawing KPI
charts is its own page, Draw Data. Site health is judged the way the Sites map
judges it (`_kpi_health`) and placed with the EP tracker and the official
boundaries (`_kpi_region`): nothing on this page has a KPI rule of its own.
"""

from __future__ import annotations

from functools import partial

import numpy as np
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from _kpi_filters import STATUSES, Filters, apply, counts, site_frame, worst_sites
from _kpi_health import issue_order
from _kpi_region import (GOVERNORATES, LEVELS, UNKNOWN, add_trend, area_counts,
                         overall_trend, region_table, site_regions)
import _kpi_details as D
import _kpi_view as V
import _kpi_workspace as W
from _ui import (PALETTE, TONES, header as _header, icon_img as _icon, kpi_cards as _kpi_cards,
                 title_html as _title_html)

ALL_GOV, ALL_SITES = "All Governorates", "All Sites"
ANALYSIS, REPORT = "KPI Analysis", "Report Export"
ISSUE_STATES = ["Critical", "Warning"]
F_TEXT = ("ka_f_cell", "ka_f_q")
F_MULTI = ("ka_f_tech", "ka_f_kpi", "ka_f_issue", "ka_f_sd")
TREND_NOTE = "2nd half vs 1st half of the window"
SELECTED_ROW = "background-color: rgba(32, 191, 255, .24); color: #F8FAFC; font-weight: 700"
ss = st.session_state

_header("KPI Analysis", "Analyze network KPIs, identify performance issues and export reports")
st.markdown(W.CHART_CSS, unsafe_allow_html=True)
st.html(V.CSS + V.REGION_CSS + V.RING_CSS)

# --------------------------------------------------------------------------- #
# the two tabs: only the open one is drawn
# --------------------------------------------------------------------------- #
W.restore("ka_view", lambda v: v in (ANALYSIS, REPORT))
if ss.get("ka_view") not in (ANALYSIS, REPORT):
    ss["ka_view"] = ANALYSIS
TAB_ICON = {ANALYSIS: ":material/space_dashboard:", REPORT: ":material/summarize:"}
with st.container(key="ka_views"):
    view = st.segmented_control("View", [ANALYSIS, REPORT], key="ka_view", required=True,
                                format_func=lambda v: f"{TAB_ICON[v]} {v}",
                                label_visibility="collapsed")
W.remember("ka_view", view)

ws = W.open_workspace("KPI Analysis", "Analyze network KPIs, detect issues and generate reports",
                      page="overview")
W.areas_sidebar()

if view == REPORT:
    import _kpi_report
    _kpi_report.render(ws)
    st.stop()

# --------------------------------------------------------------------------- #
# the filters: KPI, Governorate, Sup Districts, Site, Time Period
# --------------------------------------------------------------------------- #
win_lo, win_hi = W.window_of(ws)


def _valid_period(v) -> bool:
    return (isinstance(v, (tuple, list)) and len(v) == 2 and win_lo is not None
            and win_lo <= v[0] <= v[1] <= win_hi)


# the range picker holds one date while the second is being chosen: the page
# keeps judging the last complete period meanwhile
W.restore("ka_f_period_in", _valid_period)
if not isinstance(ss.get("ka_f_period_in"), (tuple, list)) or not ss["ka_f_period_in"]:
    ss["ka_f_period_in"] = (win_lo, win_hi)
elif len(ss["ka_f_period_in"]) == 2 and not _valid_period(ss["ka_f_period_in"]):
    ss["ka_f_period_in"] = (win_lo, win_hi)
_got = ss["ka_f_period_in"]
period = tuple(_got) if _valid_period(_got) else ss.get("ka_period_last", (win_lo, win_hi))
ss["ka_period_last"] = period
whole = period == (win_lo, win_hi)

hkey, hframes, H = W.site_health(ws, None if whole else period)
scope = W.scope_of(ws)
sites_info = W.ep_sites()
areas = W.areas()
regions = site_regions(H.sites, sites_info, areas)
names = sites_info["site_name"] if "site_name" in sites_info.columns else None
cells = W.object_cells(ws)
_base = (hkey, W.ep_key(), bool(areas))


def _rows(part: str, objects, display: bool = True):
    return W.table_rows(_base + (part,), objects, regions, names, cells, display)


rows_all = _rows("all", H.objects)
tdd_all = _rows("tdd", H.tdd)
halves = W.health_halves(hkey, hframes, H.start, H.end)
half_rows = ((_rows("prev", halves[0].objects, False), _rows("prev_tdd", halves[0].tdd, False),
              _rows("last", halves[1].objects, False), _rows("last_tdd", halves[1].tdd, False))
             if halves else None)
sites_all = site_frame(H.sites, regions, names, H.kinds)
sites_in = sites_all if scope is None else sites_all[sites_all.index.isin(scope)]

judged_cols = set(rows_all["column"])
picked_cols = tuple(sorted(c for c in ws.picked if c in judged_cols))
unjudged = sorted(c for c in ws.picked if c not in judged_cols)
bar_tech = ws.tech if ws.tech != "All" else ""

pool = rows_all[rows_all["site_id"].isin(sites_in.index)]
if picked_cols:
    pool = pool[pool["column"].isin(picked_cols)]
for _k in F_TEXT:
    W.restore(_k, lambda v: isinstance(v, str))
gov_opts = ([ALL_GOV] + [g for g in GOVERNORATES if (sites_in["governorate"] == g).any()]
            + sorted(set(sites_in["governorate"]) - set(GOVERNORATES) - {UNKNOWN}))
W.fit("ka_f_gov", gov_opts, multi=False, fallback=ALL_GOV)
gov_pick = "" if ss.get("ka_f_gov", ALL_GOV) == ALL_GOV else ss["ka_f_gov"]
gov_sites = sites_in if not gov_pick else sites_in[sites_in["governorate"] == gov_pick]
sd_opts = sorted(set(gov_sites["sup_district"]) - {UNKNOWN})
kpi_opts = sorted(set(pool["label"]))
tech_opts = sorted(set(pool["kind"]))
issue_opts = sorted(set(pool["issue"]) - {""})
for _k, _o in (("ka_f_sd", sd_opts), ("ka_f_tech", tech_opts), ("ka_f_kpi", kpi_opts),
               ("ka_f_issue", issue_opts)):
    W.fit(_k, _o)
sd_picks = tuple(ss.get("ka_f_sd") or ())
site_pool = gov_sites if not sd_picks else gov_sites[gov_sites["sup_district"].isin(sd_picks)]
site_opts = [ALL_SITES] + sorted(site_pool.index)
W.fit("ka_f_site", site_opts, multi=False, fallback=ALL_SITES)
W.restore("ka_f_state")
if "ka_f_state" not in ss:
    ss["ka_f_state"] = list(ISSUE_STATES)
W.fit("ka_f_state", STATUSES)


def _remember_filters() -> None:
    for k in F_TEXT + F_MULTI + ("ka_f_gov", "ka_f_site", "ka_f_state"):
        if k in ss:
            W.remember(k, ss[k])


def _fresh_views() -> None:
    """New table and map widgets: a stale selection does not linger, and a click
    on the same area counts again."""
    ss["ka_tbl_gen"] = ss.get("ka_tbl_gen", 0) + 1
    ss["ka_map_gen"] = ss.get("ka_map_gen", 0) + 1
    ss.pop("ka_map_tip", None)


def _clear_filters() -> None:
    for k in F_TEXT:
        ss[k] = ""
    for k in F_MULTI:
        ss[k] = []
    ss["ka_f_gov"], ss["ka_f_site"], ss["ka_f_state"] = ALL_GOV, ALL_SITES, list(ISSUE_STATES)
    ss["ka_f_period_in"] = (win_lo, win_hi)
    W.remember("ka_f_period_in", (win_lo, win_hi))
    _remember_filters()
    _fresh_views()


with st.container(key="rf_card_ka_filters", border=True):
    c_kpi, c_gov, c_sd, c_site, c_per = st.columns([1.5, 1.05, 1.9, 1.05, 1.35], gap="small",
                                                   vertical_alignment="bottom")
    with c_kpi:
        st.multiselect("KPI", kpi_opts, key="ka_f_kpi", placeholder="All KPIs")
    with c_gov:
        st.selectbox("Governorate", gov_opts, key="ka_f_gov")
    with c_sd:
        st.multiselect("Sup District", sd_opts, key="ka_f_sd", placeholder="All Sup Districts",
                       help="Pick one Sup District or several at once")
    with c_site:
        st.selectbox("Site", site_opts, key="ka_f_site")
    with c_per:
        if win_lo is not None:
            st.date_input("Time Period", min_value=win_lo, max_value=win_hi,
                          key="ka_f_period_in", format="DD/MM/YYYY",
                          help="The hours judged: every panel of the page uses them")
            if _valid_period(ss.get("ka_f_period_in")):
                W.remember("ka_f_period_in", tuple(ss["ka_f_period_in"]))
_remember_filters()

F = Filters(site="" if ss.get("ka_f_site", ALL_SITES) == ALL_SITES else ss["ka_f_site"],
            cell=ss.get("ka_f_cell", ""), q=ss.get("ka_f_q", ""), governorate=gov_pick,
            sup_districts=sd_picks,
            techs=() if bar_tech else tuple(ss.get("ka_f_tech") or ()),
            kpis=tuple(ss.get("ka_f_kpi") or ()), issues=tuple(ss.get("ka_f_issue") or ()),
            states=tuple(ss.get("ka_f_state") or ()), columns=picked_cols)
F_MEASURED = F.without("states", "issues")

universe, rows_f = apply(rows_all, sites_in, F)
tdd_f = apply(tdd_all, sites_in, F)[1]
now = counts(rows_f, tdd_f, apply(rows_all, sites_in, F_MEASURED)[1],
             apply(tdd_all, sites_in, F_MEASURED)[1])
loaded = counts(rows_all, tdd_all)
if half_rows:
    prev_f, prev_tdd, last_f, last_tdd = (apply(r, sites_in, F)[1] for r in half_rows)
    before, after = counts(prev_f, prev_tdd), counts(last_f, last_tdd)
else:
    prev_f = last_f = before = after = None

# an area table lists the areas to switch to: it leaves out its own pick
_OWN = {"Governorate": ("governorate", "sup_districts", "site"),
        "Sup District": ("sup_districts", "site"), "City": ("site",)}


def _area_table(level: str) -> pd.DataFrame:
    f = F.without(*_OWN[level])
    u, r = apply(rows_all, sites_in, f)
    reg = regions.reindex(u)
    keep = sd_picks if level == "Sup District" else ()
    t = region_table(r, reg, level, keep=keep)
    if half_rows:
        t = add_trend(t, apply(half_rows[0], sites_in, f)[1], apply(half_rows[2], sites_in, f)[1],
                      reg, level)
    return t


tables = {lv: _area_table(lv) for lv in LEVELS}
trend_all = overall_trend(prev_f, last_f, regions.reindex(universe)) if half_rows else None
chosen_of = {"Governorate": gov_pick, "Sup District": sd_picks, "City": ()}


def _set_area(level: str, name: str) -> None:
    if level == "Governorate":
        ss["ka_f_gov"], ss["ka_f_sd"], ss["ka_f_site"] = name, [], ALL_SITES
    elif level == "Sup District":
        cur = list(ss.get("ka_f_sd") or [])
        ss["ka_f_sd"] = cur + [name] if name not in cur else cur
        ss["ka_f_site"] = ALL_SITES
    else:
        # a city is where sites are, not a filter: picking one picks its Sup Districts
        mine = sorted(set(sites_in.loc[sites_in["city"] == name, "sup_district"]) - {UNKNOWN})
        ss["ka_f_sd"] = sorted(set(ss.get("ka_f_sd") or []) | set(mine))
        ss["ka_f_site"] = ALL_SITES
    _remember_filters()
    ss["ka_tbl_gen"] = ss.get("ka_tbl_gen", 0) + 1


def _clear_area() -> None:
    ss["ka_f_gov"], ss["ka_f_sd"], ss["ka_f_site"] = ALL_GOV, [], ALL_SITES
    _remember_filters()
    _fresh_views()


def _pick_area(level: str) -> None:
    ev = ss.get(f"ka_tbl_{LEVELS[level]}_{ss.get('ka_tbl_gen', 0)}")
    rows = list(ev["selection"]["rows"]) if ev else []
    names_ = ss.get(f"ka_names_{LEVELS[level]}") or []
    if rows and rows[0] < len(names_) and names_[rows[0]] != UNKNOWN:
        _set_area(level, names_[rows[0]])


def _pick_row() -> None:
    ev = ss.get("ka_table")
    rows = list(ev["selection"]["rows"]) if ev else []
    keys = ss.get("ka_table_keys") or []
    if rows and rows[0] < len(keys):
        ss["ka_sel"], ss["ka_sel_obj"], ss["ka_sel_kpi"] = keys[rows[0]]


# --------------------------------------------------------------------------- #
# the filters in force
# --------------------------------------------------------------------------- #
active = [(k, v) for k, v in F.active()
          if not (k == "Status" and list(F.states) == ISSUE_STATES)]
if not whole:
    active.append(("Time Period", f"{period[0]:%d %b %Y} → {period[1]:%d %b %Y}"))
notes = []
if picked_cols:
    notes.append(("KPI selection", f"{len(picked_cols)} judged KPI{'s' if len(picked_cols) != 1 else ''}",
                  PALETTE["cyan"], ", ".join(picked_cols)))
if unjudged:
    notes.append(("No threshold", f"{len(unjudged)} ticked KPI{'s' if len(unjudged) != 1 else ''}"
                  + (" · every judged KPI shown" if not picked_cols else ""),
                  PALETTE["nodata"], ", ".join(unjudged)))
if active or notes:
    with st.container(key="rf_card_ka_active", border=True):
        c_chips, c_clear = st.columns([6, 1], gap="small", vertical_alignment="center")
        with c_chips:
            st.html(V.chips([(k, v, PALETTE["warning"], f"{k}: {v}") for k, v in active] + notes))
        if active:
            c_clear.button("Clear filters", icon=":material/filter_alt_off:", key="ka_clear_all",
                           on_click=_clear_filters, width="stretch")

# --------------------------------------------------------------------------- #
# the KPI rings
# --------------------------------------------------------------------------- #
total = len(universe)


def _trend(name: str) -> tuple | None:
    if before is None:
        return None
    b, a = before[name], after[name]
    if name != "issues":
        b, a = (None if b is None else b[0]), (None if a is None else a[0])
    if b is None or a is None:
        return None
    d = a - b
    if d == 0:
        return ("— no change", "flat", TREND_NOTE)
    pct = f" ({100.0 * d / b:+.1f}%)" if b else ""
    return (f"{'↑' if d > 0 else '↓'} {d:+,} sites{pct}", "bad" if d > 0 else "good", TREND_NOTE)


def _kpi_ring(name: str, title: str, icon: str, colour: str, note: str, missing: str) -> dict:
    ring = dict(title=title, label=title, icon=icon, colour=colour, pct=None, note=note)
    if loaded[name] is None:
        return ring | dict(value="No data", note=missing, tip=missing)
    if now[name] is None:
        return ring | dict(value="—", note="none under the current filters",
                           tip=f"{title}: loaded, but no site of it passes the filters")
    n, of = now[name]
    return ring | dict(value=f"{n:,}", pct=100.0 * n / of if of else None,
                       share=f"{100.0 * n / of:.1f}%" if of else "",
                       share_note=f"of {of:,} measured sites", trend=_trend(name),
                       tip=f"{title}: {n:,} of the {of:,} sites it is measured on")


scope_note = "whole network" if not (F.governorate or F.sup_districts or F.site) else (
    F.site or ", ".join(F.sup_districts) or F.governorate)
st.html(V.rings([
    dict(title="Total Sites", label="Total Sites", value=f"{total:,}", icon="tower",
         colour=TONES["blue"], pct=100.0 * total / len(sites_all) if len(sites_all) else None,
         note=scope_note + (" · filtered" if active else ""),
         share=f"{100.0 * total / len(sites_all):.1f}%" if len(sites_all) else "",
         share_note=f"of {len(sites_all):,} loaded sites",
         tip="Sites in the loaded exports that the bar and the filters keep"),
    dict(title="Sites With Issues", label="Sites with Issues", value=f"{now['issues']:,}",
         icon="alert", colour=TONES["red"], pct=100.0 * now["issues"] / total if total else None,
         note="a KPI above its threshold",
         share=f"{100.0 * now['issues'] / total:.1f}%" if total else "",
         share_note=f"of {total:,} sites", trend=_trend("issues"),
         tip="Sites with at least one judged KPI above its threshold"),
    _kpi_ring("prb", "High PRB", "chart", TONES["orange"], "DL / UL PRB", "no PRB KPI loaded"),
    _kpi_ring("flow", "Flow Control", "pulse", TONES["amber"], "3G DL drops per 24 h",
              "no 3G flow-control KPI"),
    _kpi_ring("tdd", "TDD Interference", "signal", "#818CF8", "UL interference, TDD cells",
              "no TDD cells in the exports"),
    _kpi_ring("rtwp", "RTWP High", "up", "#A78BFA", "3G RTWP", "no RTWP KPI loaded"),
]))


# --------------------------------------------------------------------------- #
# KPI issues by Governorate, Sup District and City
# --------------------------------------------------------------------------- #
def _trend_note(key: str, unit: str) -> str:
    if trend_all is None:
        return "no earlier half in the loaded window"
    d = trend_all.get(key, 0)
    arrow = "↑" if d > 0 else "↓" if d < 0 else "→"
    return f"{arrow} {abs(d):,} {unit} vs the window's first half"


_REGION_CONFIG = {
    "#": st.column_config.NumberColumn("#", width=38),
    "Issue Rate": st.column_config.ProgressColumn(
        "Issue Rate", format="%.1f%%", min_value=0, max_value=100, width=110,
        help="sites with an issue out of the area's sites under the filters"),
    "Trend": st.column_config.TextColumn(
        "Trend", help="sites with an issue: the window's second half against its first half"),
}


def _region_grid(t: pd.DataFrame, level: str, chosen):
    trend = ["—" if pd.isna(x) else f"↑ {int(x)}" if x > 0 else f"↓ {int(-x)}" if x < 0
             else "→ 0" for x in t["trend"]]
    cols = {"#": np.arange(1, len(t) + 1), level: t["name"].to_numpy()}
    if level in ("Sup District", "City"):
        cols["Governorate"] = t["governorate"].to_numpy()
    if level == "Sup District":
        cols["City"] = t["city"].to_numpy()
    cols.update({"Issue Sites": t["affected"].to_numpy(), "Sites": t["sites"].to_numpy(),
                 "Issue Rate": t["rate"].round(1).to_numpy(),
                 "Critical": t["critical"].to_numpy(), "Warning": t["warning"].to_numpy(),
                 "KPI Issues": t["issues"].to_numpy(),
                 "Most Common Issue": t["top_issue"].replace("", "—").to_numpy(),
                 "Trend": trend})
    sty = pd.DataFrame(cols).style.format({"Issue Rate": "{:.1f}"})
    picked = {chosen} if isinstance(chosen, str) else set(chosen or ())
    if picked - {""}:
        sty = sty.apply(lambda r: [SELECTED_ROW if r[level] in picked else ""] * len(r), axis=1)
    return sty


def _rank_card(level: str, title: str, icon: str) -> None:
    t = tables[level]
    with st.container(key=f"rf_card_ka_rank_{LEVELS[level]}", border=True):
        n_bad, n_areas = area_counts(t)
        st.html(_title_html(title, icon, subtitle=f"{n_bad} of {n_areas} with issues"))
        if t.empty:
            st.html(V.empty("No site under the filters to place.", "pin"))
            return
        # the ranking at a glance, then the table
        ranked = V.hbars(t[t["name"] != UNKNOWN].head(8), chosen_of[level])
        if ranked:
            st.html(ranked)
        ss[f"ka_names_{LEVELS[level]}"] = t["name"].tolist()
        st.dataframe(_region_grid(t, level, chosen_of[level]),
                     key=f"ka_tbl_{LEVELS[level]}_{ss.get('ka_tbl_gen', 0)}",
                     on_select=partial(_pick_area, level), selection_mode="single-row",
                     hide_index=True, width="stretch", height=min(282, 38 + 35 * len(t)),
                     column_config=_REGION_CONFIG)


with st.container(key="rf_card_ka_region", border=True):
    r_title, r_level, r_area = st.columns([2.0, 1.5, 1.5], gap="small", vertical_alignment="center")
    with r_title:
        st.html(_title_html("KPI Issues by Area", "pin",
                            subtitle="Governorate · Sup District · City"))
    W.restore("ka_region_level", lambda v: v in LEVELS)
    if ss.get("ka_region_level") not in LEVELS:
        ss["ka_region_level"] = "Sup District"
    with r_level:
        level_r = st.segmented_control("Area", list(LEVELS), required=True,
                                       key="ka_region_level", label_visibility="collapsed",
                                       width="stretch")
    W.remember("ka_region_level", level_r)
    with r_area:
        picked_area = " · ".join(x for x in (gov_pick, ", ".join(sd_picks)) if x)
        if picked_area:
            c_chip, c_clear = st.columns([2.2, 1], gap="small", vertical_alignment="center")
            with c_chip:
                st.html(f'<div class="ka-area" title="{V.esc(picked_area)}">'
                        f'{_icon("pin", PALETTE["cyan"], 13)}<span>{V.esc(picked_area)}</span></div>')
            c_clear.button("Clear", icon=":material/close:", key="ka_area_clear", width="stretch",
                           on_click=_clear_area)
        else:
            st.html('<div class="ka-note">Select an area in a table or on the map to filter '
                    'the whole page.</div>')

    t_gov, t_sd, t_city = tables["Governorate"], tables["Sup District"], tables["City"]
    gov_bad, gov_n = area_counts(t_gov)
    sd_bad, sd_n = area_counts(t_sd)
    city_bad, city_n = area_counts(t_city)
    left, right = st.columns([2.35, 1.2], gap="small")
    with left:
        _kpi_cards([
            dict(title="Governorates with Issues", value=f"{gov_bad} / {gov_n}", icon="pin",
                 tone="red", pct=100.0 * gov_bad / gov_n if gov_n else None,
                 note=_trend_note("Governorate", "governorates")),
            dict(title="Sup Districts with Issues", value=f"{sd_bad} / {sd_n}", icon="layers",
                 tone="orange", pct=100.0 * sd_bad / sd_n if sd_n else None,
                 note=_trend_note("Sup District", "districts")),
            dict(title="Cities with Issues", value=f"{city_bad} / {city_n}", icon="pin",
                 tone="amber", pct=100.0 * city_bad / city_n if city_n else None,
                 note=_trend_note("City", "cities")),
            dict(title="Total KPI Issues", value=f"{now['checks']:,}", icon="alert", tone="red",
                 note=_trend_note("issues", "checks")),
            dict(title="Affected Sites", value=f"{now['issues']:,}", icon="tower", tone="blue",
                 pct=100.0 * now["issues"] / total if total else None,
                 note=_trend_note("affected", "sites")),
        ])
        _rank_card("Governorate", "KPI Issues by Governorate", "pin")
        g_sd, g_city = st.columns(2, gap="small")
        with g_sd:
            _rank_card("Sup District", "Top Sup Districts by KPI Issues", "layers")
        with g_city:
            _rank_card("City", "Top Cities by KPI Issues", "pin")

    with right, st.container(key="rf_card_ka_map", border=True):
        m_title, m_dots = st.columns([2.2, 1], gap="small", vertical_alignment="center")
        with m_title:
            st.html(_title_html("KPI Issues Map", "pin",
                                subtitle=f"{level_r} · official boundaries" if areas
                                else f"{level_r} · marker at the middle of its sites"))
        W.restore("ka_map_dots", lambda v: isinstance(v, bool))
        ss.setdefault("ka_map_dots", True)
        with m_dots:
            dots_on = st.toggle("Sites", key="ka_map_dots",
                                help="The affected sites of the current filters")
        W.remember("ka_map_dots", dots_on)
        dots = worst_sites(rows_f, regions) if dots_on else None
        # the picked Sup Districts are outlined whatever level is shown
        highlight = chosen_of[level_r] if level_r != "City" else ()
        clicked = st_folium(V.region_map(tables[level_r], level_r, highlight or None,
                                         areas, dots),
                            key=f"ka_folium_{ss.get('ka_map_gen', 0)}", height=560,
                            use_container_width=True,
                            returned_objects=["last_object_clicked_tooltip"])
        st.html(V.map_legend(bool(areas), dots is not None and len(dots) > 0))
    tip = (clicked or {}).get("last_object_clicked_tooltip")
    if tip and tip != ss.get("ka_map_tip"):
        ss["ka_map_tip"] = tip
        head = str(tip).split(" · ")[0].strip()
        if head.startswith(V.SITE_TIP) and head[len(V.SITE_TIP):] in set(H.sites):
            ss["ka_sel"] = head[len(V.SITE_TIP):]
            ss.pop("ka_sel_obj", None)
            ss.pop("ka_sel_kpi", None)
            st.rerun()
        if head != UNKNOWN and head in set(tables[level_r]["name"]):
            _set_area(level_r, head)
            st.rerun()

# --------------------------------------------------------------------------- #
# the one KPI issues table — its filters are the page's
# --------------------------------------------------------------------------- #
_GRID_CONFIG = {
    "Status": st.column_config.MultiselectColumn(
        "Status", options=list(V.STATE_COLOUR), color=list(V.STATE_COLOUR.values()), width=96),
    "Cell Name": st.column_config.TextColumn("Cell Name", width="medium"),
    "Worst Hour": st.column_config.DatetimeColumn("Worst Hour", format="D MMM, HH:mm"),
    "Threshold": st.column_config.TextColumn("Threshold", width="medium"),
}


def _dash(s: pd.Series) -> np.ndarray:
    return np.where(s.astype(str) == "", "—", s.astype(str))


with st.container(key="rf_card_ka_table", border=True):
    f_title, f_q, f_cell = st.columns([1.7, 1.6, 1], gap="small", vertical_alignment="center")
    with f_q:
        st.text_input("Search", key="ka_f_q", label_visibility="collapsed",
                      placeholder="Search site, cell, governorate, Sup District, city, KPI…")
    with f_cell:
        st.text_input("Cell", key="ka_f_cell", placeholder="Cell, sector or cell ID…",
                      label_visibility="collapsed")
    f_tech, f_issue, f_state = st.columns([0.8, 1.6, 1.9], gap="small",
                                          vertical_alignment="center")
    with f_tech:
        if bar_tech:
            st.selectbox("Technology", [bar_tech], key="ka_f_tech_bar", disabled=True,
                         label_visibility="collapsed", help="Set by the technology in the bar")
        else:
            st.multiselect("Technology", tech_opts, key="ka_f_tech", placeholder="All tech",
                           label_visibility="collapsed")
    with f_issue:
        st.multiselect("Issue", issue_opts, key="ka_f_issue", placeholder="All issues",
                       label_visibility="collapsed")
    with f_state:
        st.pills("Status", list(STATUSES), selection_mode="multi", key="ka_f_state",
                 label_visibility="collapsed")
    _remember_filters()

    table = issue_order(rows_f)
    with f_title:
        st.html(_title_html("KPI Issues Table", "alert",
                            subtitle=f"{len(table):,} of {len(pool):,} checks · the same rows as "
                                     "the rings, the areas and the map · select a row"))
    if table.empty:
        st.html(V.empty("No KPI check matches the filters.", "check"))
    else:
        grid = pd.DataFrame({
            "Status": [[s] for s in table["state"]],
            "Site ID": table["site_id"].to_numpy(),
            "Site Name": _dash(table["site_name"]),
            "Cell ID": _dash(table["cell_id"]),
            "Cell Name": _dash(table["cell_name"]),
            "Governorate": table["governorate"].to_numpy(),
            "Sup District": table["sup_district"].to_numpy(),
            "City": table["city"].to_numpy(),
            "Technology": table["kind"].to_numpy(),
            "KPI": table["label"].to_numpy(),
            "Issue": _dash(table["issue"]),
            "KPI Value": table["value_text"].to_numpy(),
            "Threshold": table["threshold"].to_numpy(),
            "Worst Hour": pd.to_datetime(table["peak_time"]).to_numpy(),
            "Worst Value": table["worst_text"].to_numpy(),
        })
        ss["ka_table_keys"] = list(zip(table["site_id"], table["object_id"], table["label"]))
        st.dataframe(grid, key="ka_table", on_select=_pick_row, selection_mode="single-row",
                     hide_index=True, width="stretch", height=min(460, 38 + 35 * len(grid)),
                     column_config=_GRID_CONFIG)

# --------------------------------------------------------------------------- #
# KPI details by site / cell
# --------------------------------------------------------------------------- #
sel = ss.get("ka_sel")
if sel and sel not in set(H.sites):
    D.close()
    sel = None
rows_site = None
if sel:
    rows_site = apply(rows_all[rows_all["site_id"] == sel], sites_all.loc[[sel]],
                      F.without("states", "issues", "site", "q", "governorate",
                                "sup_districts"))[1]
D.render(sel, H, hframes, rows_site, regions, sites_info, ss.get("ka_sel_obj"),
         ss.get("ka_sel_kpi"))
