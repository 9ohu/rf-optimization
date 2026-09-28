"""What the two KPI Analysis pages share: the loaded exports, the sidebar, the
workspace bar and the site health.

Overview and Draw Data are one tool on two pages, so what a user sets up here —
the technology and object, the KPI picks — carries from one page to the other.
The exports are the active KPI Data of Data Resources (`_resources`): uploaded
and applied there once, kept on disk, read by every page. KPI Data can hold
many files; the files of one technology are read as one export (`Combined`),
so an hour of a cell that two files share is judged and drawn once. The bar's
choices are copied aside, because Streamlit forgets a page's widgets when
another page opens.

The file type does not matter: the header is sniffed, the technology and the
KPI list come out of the file itself, and only the columns in use are ever
read — which is what keeps a 140 MB export responsive.
"""

from __future__ import annotations

import dataclasses
import os
import re
from dataclasses import dataclass

import pandas as pd
import streamlit as st

import _kpi_view as V
import _resources as R
import _shared
from rfopt.ingest.hourly_kpi import (KpiFileInfo, load_hourly_raw, merge_hourly,
                                     sniff_kpi_export)
from rfopt.kpi.trends import cells_of, object_options, panels_for
from rfopt.reports.kpi_pivot import order_kpis, spec_for, write_pivot_workbook
from _kpi_health import build_health, judged_columns
from _shared import NamedBytes, SRC_HASH, load_ep_all
from _ui import file_status

KEEP = "ka_keep"             # widget values copied aside across pages
PAGE = "ka_page"             # the KPI page that ran last
NA = "Not available"

CHART_CSS = """
<style>
/* the KPI tick list scrolls like a real picker instead of pushing the page */
.st-key-kpi_list [data-testid="stVerticalBlock"] { gap: 0 !important; }
.st-key-kpi_list label p { font-size: 12px; line-height: 1.25; }
.st-key-kpi_list [data-testid="stCheckbox"] { margin-bottom: -6px; }
/* a chart's title bar: the KPI on the left, its trend verdict on the right */
.sm-chart-head {
    position: relative; color: #F1F5F9; text-align: left; margin: 0;
    background: linear-gradient(90deg, rgba(21, 151, 255, .18), rgba(21, 151, 255, .03));
    border: 1px solid #1E3A5F; border-left: 3px solid #20BFFF; border-radius: 8px;
    padding: 5px 210px 5px 10px; font: 700 12px system-ui, sans-serif; line-height: 1.4;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.sm-chart-head .sm-verdict {
    position: absolute; right: 112px; top: 5px; font-weight: 700;
    font-size: 11px; color: #20BFFF;
}
/* Copy Chart: the picture of this chart, for email / WhatsApp / PowerPoint */
.sm-chart-head .rf-copy {
    position: absolute; right: 5px; top: 3px; height: 22px; padding: 0 9px;
    background: #0D2945; color: #E2E8F0; border: 1px solid #1E3A5F; border-radius: 6px;
    font: 600 11px system-ui, sans-serif; cursor: pointer;
}
.sm-chart-head .rf-copy:hover { background: #15406B; border-color: #1597FF; }
.sm-chart-head .rf-copy[data-state] { color: #22C55E; border-color: #22C55E; }
</style>
"""


# --------------------------------------------------------------------------- #
# loaders
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Combined:
    """The KPI files of one technology, read as one export: one row per object
    and hour (`merge_hourly`). `paths` go from the oldest data to the most
    recent, whose value is used where two files hold the same hour."""
    paths: tuple

    def __str__(self) -> str:
        return " + ".join(os.path.basename(p) for p in self.paths)


def _union(lists) -> list:
    out: list = []
    seen: set = set()
    for items in lists:
        for x in items:
            if x not in seen:
                seen.add(x)
                out.append(x)
    return out


def combined_info(paths: tuple) -> KpiFileInfo:
    """What several files of one technology hold together: every KPI of each."""
    infos = [R.kpi_info(p) for p in paths]
    return dataclasses.replace(
        infos[0], name=" + ".join(i.name for i in infos),
        columns=_union(i.columns for i in infos), kpis=_union(i.kpis for i in infos),
        all_kpis=_union(i.all_kpis for i in infos),
        first_time=min((i.first_time for i in infos if i.first_time), default=""))


def combine(groups) -> list:
    """[(technology, [(path, KpiFileInfo, StoredFile)])] (`R.kpi_groups`) →
    [(source, KpiFileInfo)], one per technology: its one file's path, or a
    `Combined` of its files."""
    out = []
    for kind, group in groups:
        if len(group) == 1 or kind not in KpiFileInfo.USABLE:
            out += [(path, info) for path, info, _ in group]
        else:
            paths = tuple(path for path, _, _ in group)
            out.append((Combined(paths), combined_info(paths)))
    return out


@st.cache_resource(show_spinner=False, hash_funcs=SRC_HASH)
def _sniff_buffer(src):
    return sniff_kpi_export(src)


def sniff(src):
    """A stored export is read once for every page (`_resources`)."""
    if isinstance(src, Combined):
        return combined_info(src.paths)
    return R.kpi_info(src) if isinstance(src, str) else _sniff_buffer(src)


@st.cache_resource(show_spinner=False,
                   hash_funcs=SRC_HASH)
def _index_buffer(src):
    if hasattr(src, "seek"):
        src.seek(0)
    return load_hourly_raw(src, [])


@st.cache_resource(show_spinner=False, max_entries=4)
def _combined_index(paths: tuple):
    return merge_hourly([R.kpi_index(p) for p in paths])


def index_of(src):
    """Objects and hours only — no KPI column, so it is quick even on 140 MB."""
    if isinstance(src, Combined):
        return _combined_index(src.paths)
    return R.kpi_index(src) if isinstance(src, str) else _index_buffer(src)


@st.cache_resource(show_spinner=False,
                   hash_funcs=SRC_HASH)
def _raw(src, kpis: tuple):
    # an upload is one buffer read several times in a run (objects, site
    # health, charts): each read starts from its beginning
    if hasattr(src, "seek"):
        src.seek(0)
    return load_hourly_raw(src, list(kpis))


@st.cache_resource(show_spinner=False,
                   max_entries=12)
def _combined_raw(paths: tuple, kpis: tuple):
    """The KPIs out of each file that has them, as one export. A file with none
    of them adds nothing (its cells would only come in empty)."""
    parts = []
    for p in paths:
        have = set(R.kpi_info(p).all_kpis)
        want = [k for k in kpis if k in have]
        if kpis and not want:
            continue
        parts.append(load_hourly_raw(p, want))
    return merge_hourly(parts) if parts else load_hourly_raw(paths[0], [])


def raw(src, kpis: tuple):
    """The picked KPI columns of an export (or of a technology's `Combined` files)."""
    if isinstance(src, Combined):
        return _combined_raw(src.paths, tuple(kpis))
    return _raw(src, tuple(kpis))


def src_key(src) -> tuple:
    """What a cached judgement is keyed on: an upload's name and size, a found
    file's path and modification time (each file's, for `Combined` files)."""
    if isinstance(src, Combined):
        return tuple(src_key(p) for p in src.paths)
    if isinstance(src, NamedBytes):
        return (src.name, src.getbuffer().nbytes)
    try:
        return (str(src), os.path.getmtime(src))
    except OSError:
        return (str(src), 0.0)


@st.cache_resource(show_spinner=False,
                   max_entries=4)
def health(key: tuple, _frames):
    return build_health(_frames)


@st.cache_resource(show_spinner=False, max_entries=4)
def health_halves(key: tuple, _frames, start, end):
    """Site health over the window's first and second half, or None."""
    from _kpi_region import halves
    cut = halves(_frames, start, end)
    if cut is None:
        return None
    prev, last, mid = cut
    return build_health(prev), build_health(last), mid


def ep_path() -> str | None:
    """The Current Site Details Data (EP) of Data Resources."""
    return R.ep_path()


def clean(x) -> str:
    s = "" if x is None else str(x).strip()
    return "" if s.lower() in ("", "nan", "none", "nat") else s


def _first(values) -> str:
    for x in values:
        v = clean(x)
        if v:
            return v
    return ""


@st.cache_resource(show_spinner=False, max_entries=2)
def site_table(path: str) -> pd.DataFrame:
    ep = load_ep_all(path)
    if ep is None or ep.empty or "site_id" not in ep.columns:
        return pd.DataFrame()
    d = ep.assign(site_id=ep["site_id"].astype(str).str.upper().str.strip())
    agg = {}
    if "enodeb_name" in d.columns:
        agg["site_name"] = ("enodeb_name", _first)
    for col in ("city", "district", "sub_district"):
        if col in d.columns:
            agg[col] = (col, _first)
    for col in ("latitude", "longitude"):
        if col in d.columns:
            d[col] = pd.to_numeric(d[col], errors="coerce")
            agg[col] = (col, "median")
    return d.groupby("site_id").agg(**agg) if agg else pd.DataFrame()


def ep_sites() -> pd.DataFrame:
    """Name, city, district, sub-district and position per site, or empty."""
    path = ep_path()
    return site_table(path) if path else pd.DataFrame()


def ep_key() -> tuple:
    """What a table joined with the EP tracker is cached on: its path and time."""
    path = ep_path()
    try:
        return (path, os.path.getmtime(path)) if path else (None, 0.0)
    except OSError:
        return (path, 0.0)


def placed(sites) -> pd.DataFrame:
    """Governorate, Sup District, city and position of each site: the one
    placement every page uses (`_kpi_region.site_regions`)."""
    from _kpi_region import site_regions
    return site_regions(sorted({str(s) for s in sites}), ep_sites(), areas())


def site_governorates(sites) -> pd.Series:
    """Each site's governorate (Basrah, Dhi Qar, Maysan, Muthanna)."""
    return placed(sites)["governorate"]


def governorate_site_ids(index: pd.DataFrame, governorate: str) -> set:
    govs = site_governorates(index["site_id"].dropna().unique())
    return set(govs.index[govs == governorate])


def scope_of(ws) -> set | None:
    """The sites the bar's selection covers; None is the whole network."""
    from _kpi_health import scope_sites
    govs = (site_governorates(ws.index["site_id"].dropna().unique())
            if ws.level == "Governorate" and ws.obj else None)
    return scope_sites(ws.index, ws.level, ws.obj, ws.cells, govs)


@st.cache_resource(show_spinner=False, max_entries=2)
def cell_ids(path: str) -> pd.Series:
    """EP tracker cell name (upper case) -> cell ID."""
    ep = load_ep_all(path)
    if ep is None or ep.empty or not {"cell_name", "cell_id"} <= set(ep.columns):
        return pd.Series(dtype=object)
    d = ep[["cell_name", "cell_id"]].dropna()
    s = pd.Series(d["cell_id"].to_numpy(),
                  index=d["cell_name"].astype(str).str.strip().str.upper())
    return s[~s.index.duplicated(keep="first")]


@st.cache_resource(show_spinner=False, max_entries=4)
def _object_cells(key: tuple, _index, _ids):
    from _kpi_filters import cell_table
    return cell_table(_index, _ids)


def object_cells(ws) -> pd.DataFrame:
    """The cells under each judged object, with their EP-tracker cell IDs."""
    path = ep_path()
    ids = cell_ids(path) if path else None
    key = (tuple((src_key(b), i.kind) for b, i in ws.usable), ep_key())
    return _object_cells(key, ws.index, ids)


@st.cache_resource(show_spinner=False, max_entries=12)
def table_rows(key: tuple, _objects, _regions, _names, _cells, display: bool = True):
    """The judged rows with what the Overview filters and shows (`_kpi_filters`)."""
    from _kpi_filters import enrich
    return enrich(_objects, _regions, _names, _cells, display=display)


def areas() -> tuple:
    """The official R5 governorate and sub-district boundaries bundled with the app."""
    from _kpi_bounds import r5_areas
    return r5_areas()


def areas_sidebar() -> None:
    """Where the Sup District and City boundaries come from."""
    from _kpi_bounds import ATTRIBUTION, governorates, sub_districts
    with st.sidebar.expander("Area boundaries", icon=":material/map:", expanded=False):
        found = areas()
        if found:
            file_status("r5_admin_boundaries.geojson",
                        source=f"{len(sub_districts(found))} sub-districts · "
                               f"{len(governorates(found))} governorates · bundled")
            st.caption(ATTRIBUTION)
        else:
            file_status(None, empty="Boundary file missing: areas are drawn as markers")


@st.cache_resource(show_spinner=False, max_entries=2)
def tickets(sha: str, _ds) -> pd.DataFrame:
    from rfopt.complaints.correlate import local_time
    from rfopt.complaints.target_store import parse_target
    t = parse_target(_ds.read_bytes(), _ds.name)
    t["problem_local"] = local_time(t["problem_time"])
    return t


def book_name(kpis: list[str]) -> str:
    """Name the file after what is in it — one KPI, its own sheet name."""
    sheets = [spec_for(k).sheet for k in order_kpis(kpis)]
    stem = "_".join(sheets[:3]) if len(sheets) <= 3 else "4G KPIs Hourly"
    return re.sub(r'[\\/:*?"<>|]', "_", stem)[:80] + ".xlsx"


def export(kpis: list[str], loaded) -> str:
    """Write the workbook into the user's Downloads folder. The tool runs on
    their own machine, so the file simply lands there — no second click."""
    frames = [raw(src, tuple(k for k in kpis if k in set(info.all_kpis)))
              .assign(tech=info.kind)
              for src, info in loaded if set(kpis) & set(info.all_kpis)]
    if not frames:
        return "None of those KPIs is in the loaded file(s)."
    target = _shared.DL / book_name(kpis)
    try:
        _, sheets, left = write_pivot_workbook(
            pd.concat(frames, ignore_index=True), kpis, target)
    except PermissionError:
        return f"**{target.name}** is open in Excel — close it and try again."
    extra = f"  ·  no data for: {', '.join(left)}" if left else ""
    return f"Saved to Downloads: **{target.name}**  ·  {len(sheets)} sheet(s)" \
           f"{extra}"


# --------------------------------------------------------------------------- #
# widget values across pages
# --------------------------------------------------------------------------- #
def remember(key: str, value) -> None:
    st.session_state.setdefault(KEEP, {})[key] = value


def restore(key: str, ok=lambda v: True, *, force: bool = False) -> None:
    """Back from another page: give the widget the value it had, if it still fits.

    `force` is for a widget both KPI pages draw: arriving from the other page,
    that page's state is still stored under the key, and this page's widget
    would start from its default instead of the value last chosen."""
    kept = st.session_state.get(KEEP, {})
    if (force or key not in st.session_state) and key in kept and ok(kept[key]):
        st.session_state[key] = kept[key]


def fit(key: str, options, *, multi: bool = True, fallback=None,
        force: bool = False) -> None:
    """Restore a choice, and drop what the current options no longer offer."""
    restore(key, force=force)
    if key not in st.session_state:
        return
    opts, v = list(options), st.session_state[key]
    if multi:
        fitted = [x for x in (v or []) if x in opts]
        if fitted != list(v or []):
            st.session_state[key] = fitted
    elif v not in opts:
        st.session_state[key] = fallback


# --------------------------------------------------------------------------- #
# the workspace
# --------------------------------------------------------------------------- #
@dataclass
class Workspace:
    files: list
    usable: list
    tech: str
    unit: str
    index: pd.DataFrame
    level: str
    obj: str | None
    cells: list
    picked: set
    catalogue: list
    draw_col: object
    max_col: object


def _files() -> list:
    """The active KPI Data: [(source, KpiFileInfo)], one per technology (`combine`).

    The files are uploaded, reviewed and applied in Data Resources; no page but
    that one says anything about them."""
    srcs = R.kpi_sources()
    res = R.active("kpi")
    ver = tuple(f.sha1 for _, _, f in srcs) if res else None
    before = st.session_state.get("kpi_data_ver")
    st.session_state["kpi_data_ver"] = ver
    if before is not None and before != ver:
        # other KPI Data applied: charts drawn from the replaced files mean nothing
        for k in ("kpi_drawn", "kpi_trend_xlsx"):
            st.session_state.pop(k, None)
    return combine(R.kpi_groups())


def open_workspace(title: str, subtitle: str, icon: str = "chart",
                   page: str = "overview") -> Workspace:
    """The sidebar and the bar, with the exports loaded and the object chosen.
    Stops the page when there is nothing to work on."""
    moved = st.session_state.get(PAGE) != page     # arrived from the other KPI page
    st.session_state[PAGE] = page
    with st.sidebar:
        pick_box = st.expander("KPI selection", icon=":material/analytics:", expanded=True)
        xls_box = st.expander("Excel export", icon=":material/table_view:", expanded=False)
    files = _files()

    bar = st.container(key="rf_card_ka_bar", border=True)
    with bar:
        b_title, b_tech, b_level = st.columns([1.3, 1.15, 1.55], gap="medium",
                                              vertical_alignment="center")
        with b_title:
            st.html(V.title_block(title, subtitle, icon))

    # stop here rather than render half a workflow over nothing
    if not files:
        st.html('<div class="rf-card">' + V.empty(
            "No KPI Data yet — upload the 4G / 3G hourly exports once in Data Resources "
            "(sidebar → Data). Every KPI page reads them from there.", "file") + "</div>")
        R.link("Open Data Resources")
        st.stop()
    loaded = [(b, i) for b, i in files if i.kind in KpiFileInfo.USABLE]
    if not loaded:
        st.warning("No raw hourly export among the added files.")
        st.stop()

    # One technology at a time by default, because nothing about the exports
    # lines up: 4G is per cell over one window, 3G per NodeB over another, they
    # share a column name ("Integrity") that means different things, and a
    # site's 4G cells and its 3G NodeB are not comparable objects. `All` puts
    # them side by side anyway — each technology keeps its own line.
    order = ["4G", "3G", "2G", "Other"]
    techs = [t for t in order if t in {i.kind for _, i in loaded}]
    options = techs + (["All"] if len(techs) > 1 else [])
    fit("ka_tech", options, multi=False, fallback=options[0], force=moved)
    with b_tech:
        tech = st.segmented_control("Technology", options, default=options[0],
                                    required=True, key="ka_tech",
                                    label_visibility="collapsed", width="stretch")
    remember("ka_tech", tech)
    if tech != st.session_state.get("kpi_tech"):
        # a drawing from another technology means nothing here
        st.session_state["kpi_tech"] = tech
        st.session_state.pop("kpi_drawn", None)

    usable = loaded if tech == "All" else [(b, i) for b, i in loaded if i.kind == tech]
    index = pd.concat([index_of(b).assign(tech=i.kind) for b, i in usable],
                      ignore_index=True)
    unit = {"3G": "NodeB", "All": "Object", "Other": "Object"}.get(tech, "Cell")

    levels = ["Network", "Governorate", "Site", "Cell"]
    if page == "overview":
        # the Overview picks its area with its own filters (governorate, Sup
        # Districts, site): the bar only sets the technology
        level = "Network"
        with b_level:
            st.html('<div class="ka-note">Filter by governorate, Sup District, site and '
                    "time period below</div>")
    else:
        fit("ka_level", levels, multi=False, fallback="Network", force=moved)
        with b_level:
            level = st.segmented_control("Object type", levels, default="Network",
                                         required=True, key="ka_level",
                                         format_func=lambda v: unit if v == "Cell" else v,
                                         label_visibility="collapsed", width="stretch")
        remember("ka_level", level)
    # only Draw Data draws, so only it keeps room for the draw controls
    draw_page = page == "draw"
    with_obj = level in ("Governorate", "Site")
    widths = (([1.15] if with_obj else []) + [2.45 if draw_page else 3.6]
              + ([1.3, 0.6] if draw_page else []))
    with bar:
        cols = st.columns(widths, gap="small", vertical_alignment="bottom")
    o_obj = cols[0] if with_obj else None
    o_cells = cols[1] if with_obj else cols[0]
    o_draw, o_max = (cols[-2], cols[-1]) if draw_page else (None, None)
    obj, cells = None, []
    if level == "Governorate":
        from _kpi_region import GOVERNORATES
        fit("ka_obj_Gov", list(GOVERNORATES), multi=False, fallback=GOVERNORATES[0],
            force=moved)
        with o_obj:
            obj = st.selectbox("Governorate", list(GOVERNORATES), key="ka_obj_Gov")
        remember("ka_obj_Gov", obj)
    elif level == "Site":
        opts = object_options(index, level)
        key = f"ka_obj_{level}"
        fit(key, opts, multi=False, fallback=opts[0] if opts else None, force=moved)
        with o_obj:
            obj = st.selectbox(level, opts, index=0 if opts else None, key=key)
        remember(key, obj)
    if level == "Site" and obj:
        # every sector of the site on one chart, so a drifting one shows up
        # against its siblings
        site_cells = cells_of(index, obj)
        key = f"ka_cells_{obj}"
        fit(key, site_cells, force=moved)
        with o_cells:
            cells = st.multiselect(f"{unit}s to compare", site_cells, default=site_cells,
                                   key=key)
        remember(key, cells)
    elif level == "Cell":
        all_cells = object_options(index, "Cell")
        fit("ka_cells", all_cells, force=moved)
        with o_cells:
            cells = st.multiselect(f"{unit}s to compare", all_cells, max_selections=10,
                                   key="ka_cells")
        remember("ka_cells", cells)
    else:
        shown = (index[index["site_id"].isin(governorate_site_ids(index, obj))]
                 if level == "Governorate" and obj else index)
        with o_cells:
            if shown.empty:
                st.html(f'<div class="ka-win">No site of <b>{V.esc(obj)}</b> in the loaded '
                        'exports</div>')
            else:
                st.html(f'<div class="ka-win"><b>{shown["site_id"].nunique():,}</b> sites · '
                        f'<b>{shown["object"].nunique():,}</b> {unit.lower()}s · '
                        f'{shown["datetime"].min():%Y-%m-%d %H:%M} → '
                        f'{shown["datetime"].max():%Y-%m-%d %H:%M}</div>')

    with pick_box:
        catalogue: list[str] = []
        for _, info in usable:
            for k in info.all_kpis:
                if k not in catalogue:
                    catalogue.append(k)

        picked: set = st.session_state.setdefault("kpi_pick", set())
        picked &= set(catalogue)                  # a removed file drops its KPIs

        q = st.text_input("Search", label_visibility="collapsed")
        visible = [k for k in catalogue if q.lower() in k.lower()] if q else catalogue

        b1, b2, b3 = st.columns(3)
        bulk = False
        if b1.button("All", width="stretch"):
            picked |= set(visible)
            bulk = True
        if b2.button("None", width="stretch"):
            picked -= set(visible)
            bulk = True
        if b3.button("Invert", width="stretch"):
            picked ^= set(visible)
            bulk = True
        if bulk:
            # a bulk button owns the boxes for this run only. Seeding them on
            # every run instead would overwrite the tick the user just made — the
            # click arrives as widget state, and `picked` does not know about it.
            for k in visible:
                st.session_state[f"kpi_cb_{k}"] = k in picked
        for k in visible:
            # arriving from the other KPI page, a box still holds that page's
            # state (or none, back from elsewhere): the pick decides what it shows
            if moved:
                st.session_state[f"kpi_cb_{k}"] = k in picked
            else:
                st.session_state.setdefault(f"kpi_cb_{k}", k in picked)

        with st.container(key="kpi_list", height=300, border=True):
            for k in visible:
                if st.checkbox(k, key=f"kpi_cb_{k}"):
                    picked.add(k)
                else:
                    picked.discard(k)
        st.caption(f"**{len(picked)}** of {len(catalogue)} KPIs selected")
        st.session_state["kpi_pick"] = picked

    with xls_box:
        # ---- the emailed workbook, straight into Downloads --------------- #
        fit("kpi_xls_pick", catalogue, force=moved)
        x_kpis = st.multiselect("KPIs for the workbook", catalogue, key="kpi_xls_pick")
        remember("kpi_xls_pick", x_kpis)
        if st.button("Export to Downloads", icon=":material/download:", width="stretch",
                     disabled=not x_kpis):
            with st.spinner("Building the workbook…"):
                st.session_state["kpi_book"] = export(x_kpis, usable)
        done = st.session_state.get("kpi_book")
        if done:
            st.caption(done)

    return Workspace(files, usable, tech, unit, index, level, obj, list(cells), picked,
                     catalogue, o_draw, o_max)


def in_period(df: pd.DataFrame, period) -> pd.DataFrame:
    """The hours of a frame inside `period` (first day, last day), both days whole."""
    if not period or df is None or df.empty:
        return df
    lo = pd.Timestamp(period[0])
    hi = pd.Timestamp(period[1]) + pd.Timedelta(days=1)
    t = df["datetime"]
    return df[(t >= lo) & (t < hi)]


def window_of(ws: Workspace) -> tuple:
    """(first day, last day) the loaded exports cover."""
    t = ws.index["datetime"]
    return (t.min().date(), t.max().date()) if len(t) and t.notna().any() else (None, None)


def site_health(ws: Workspace, period=None):
    """(cache key, frames, Health) for the workspace's technology, judged over the
    hours of `period` (first day, last day) — the whole window when None."""
    judged = [(b, i, judged_columns(i.all_kpis, i.kind)) for b, i in ws.usable]
    frames = [(i.kind, in_period(raw(b, tuple(j.column for j in js)), period), js)
              for b, i, js in judged if js]
    key = tuple((src_key(b), i.kind, tuple(j.column for j in js)) for b, i, js in judged if js)
    if period:
        key += (("period", str(period[0]), str(period[1])),)
    return key, frames, health(key, frames)


def trend_state(ws: Workspace) -> dict:
    """The drawn charts, or why there are none."""
    drawn = st.session_state.get("kpi_drawn")
    if not drawn:
        return {"note": "Tick KPIs under KPI selection, then Draw Selected Charts."}
    kpis, d_level, d_obj, d_cells = drawn
    frames = [raw(b, tuple(k for k in kpis if k in set(i.all_kpis))).assign(tech=i.kind)
              for b, i in ws.usable if set(kpis) & set(i.all_kpis)]
    if not frames:
        return {"warn": "None of the selected KPIs is in the loaded file(s)."}
    data = pd.concat(frames, ignore_index=True)
    if d_level in ("Governorate", "City") and d_obj:
        # a governorate's charts are the network's, over its sites only
        data = data[data["site_id"].isin(governorate_site_ids(ws.index, d_obj))]
        d_level = "Network"
    panels = panels_for(data, list(kpis), level=d_level, obj=d_obj,
                        cells=list(d_cells), by_tech=ws.tech == "All")
    n = f"{len(d_cells)} {ws.unit}{'s' if len(d_cells) != 1 else ''}"
    who = (f"{d_obj} · {n}" if d_cells and d_obj
           else n if d_cells else d_obj or "all R5")
    if not panels:
        return {"warn": f"No data for {who} in the selected KPIs."}
    return {"kpis": kpis, "level": d_level, "obj": d_obj, "cells": d_cells,
            "data": data, "panels": panels, "who": who}
