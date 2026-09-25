"""KPI Analysis · Draw Data — Bulk Draw: a list of sites, a chart for each.

The user uploads an Excel of Site ID + Sector only. The EP tracker says which
cells those are — Site → Sector → Cells — and everything after that is Draw
Data's own: the same panels (`rfopt.kpi.trends.panels_for`), the same chart
(`_charts.kpi_figure`), the same workbook (`rfopt.reports.kpi_pivot`). Single
Draw is untouched; this is a second mode beside it.

A cell the EP tracker does not list keeps the sector the export itself names,
so a new carrier is drawn instead of silently dropped; a 3G export measures the
NodeB, not the sector, so its site object answers for every sector of the site.
"""

from __future__ import annotations

import html
import io
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

import _kpi_workspace as W
import _resources as R
import _shared
from _charts import COPY_JS, kpi_figure
from _shared import load_ep_all
from rfopt.kpi.trends import panels_for
from rfopt.reports.bulk_deck import GRID_PX, WHOLE_PX, Deck, build as build_deck
from rfopt.reports.kpi_pivot import write_pivot_workbook
from rfopt.reports.kpi_report import PANEL, Assets

ASSETS = Path(__file__).resolve().parent / "assets" / "report"
SINGLE, BULK = "Single Draw", "Bulk Draw"
PER_SITE, PER_SECTOR = "Per Site", "Per Sector"
EP_TECH = {"4G": "LTE", "3G": "UMTS", "2G": "GSM"}
PER_PAGE = 12

CSS = """
<style>
.kb-step { display: flex; align-items: center; gap: 9px; margin-bottom: 10px; }
.kb-num { flex: 0 0 26px; height: 26px; border-radius: 50%; display: flex; align-items: center;
    justify-content: center; font: 700 13px 'Segoe UI', system-ui, sans-serif; color: #071525;
    background: var(--c); box-shadow: 0 0 12px color-mix(in srgb, var(--c) 45%, transparent); }
.kb-step b { font-size: 14.5px; font-weight: 700; color: #F1F5F9; }
.kb-note { color: #94A3B8; font-size: 11.5px; margin-top: 6px; line-height: 1.45; }
.kb-bad { color: #FB923C; font-size: 11.5px; margin-top: 6px; line-height: 1.45; }
.kb-ok { color: #22C55E; font-size: 12px; }
.st-key-rf_card_kb_up, .st-key-rf_card_kb_kpi, .st-key-rf_card_kb_by,
.st-key-rf_card_kb_out { min-height: 372px; }
.kb-head { display: flex; align-items: center; justify-content: space-between; gap: 10px;
    margin-bottom: 4px; }
.kb-title { font-size: 16px; font-weight: 700; color: #F1F5F9; }
.kb-title small { font-weight: 500; font-size: 12px; color: #94A3B8; margin-left: 6px; }
.kb-chart-h { display: flex; align-items: baseline; gap: 8px; color: #F1F5F9;
    background: linear-gradient(90deg, rgba(21, 151, 255, .18), rgba(21, 151, 255, .03));
    border: 1px solid #1E3A5F; border-left: 3px solid #20BFFF; border-radius: 8px;
    padding: 5px 10px; font: 700 13px 'Segoe UI', system-ui, sans-serif; }
.kb-chart-h small { font-weight: 500; font-size: 11.5px; color: #94A3B8; }
.kb-cells { color: #64748B; font-size: 11px; margin-top: 5px; line-height: 1.5;
    overflow-wrap: anywhere; }
</style>
"""


def _esc(x) -> str:
    return html.escape(str(x))


def step(n: int, title: str, colour: str) -> str:
    return (f'<div class="kb-step"><span class="kb-num" style="--c:{colour}">{n}</span>'
            f"<b>{_esc(title)}</b></div>")


# --------------------------------------------------------------------------- #
# the uploaded list, and the cells behind it
# --------------------------------------------------------------------------- #
class ListError(ValueError):
    """The uploaded file is not a Site ID + Sector list."""


def read_list(name: str, data: bytes) -> pd.DataFrame:
    """The uploaded Site ID + Sector rows, in the file's own order."""
    buf = _shared.NamedBytes(data, name)
    try:
        raw = (pd.read_csv(buf) if name.lower().endswith((".csv", ".txt"))
               else pd.read_excel(buf))
    except Exception as exc:                                   # unreadable workbook
        raise ListError(f"the file could not be read ({exc})") from exc
    cols = {str(c).strip().lower().replace("_", " "): c for c in raw.columns}

    def find(*names):
        for want in names:
            for low, col in cols.items():
                if low == want:
                    return col
        for want in names:
            for low, col in cols.items():
                if want in low:
                    return col
        return None

    site_col = find("site id", "siteid", "site")
    sect_col = find("sector", "sector id", "sector number")
    if site_col is None:
        raise ListError("no Site ID column in the file (two columns: Site ID, Sector)")
    out = pd.DataFrame({"site_id": raw[site_col].astype(str).str.strip().str.upper()})
    out["sector"] = (pd.to_numeric(raw[sect_col], errors="coerce")
                     if sect_col is not None else np.nan)
    out = out[out["site_id"].ne("") & out["site_id"].ne("NAN")]
    out = out.drop_duplicates(subset=["site_id", "sector"]).reset_index(drop=True)
    if out.empty:
        raise ListError("no Site ID in the file")
    return out


@st.cache_resource(show_spinner=False, max_entries=2)
def _ep_cells(key: tuple) -> pd.DataFrame:
    """The EP tracker's Site → Sector → Cells: one row per cell name."""
    path = W.ep_path()
    ep = load_ep_all(path) if path else pd.DataFrame()
    if ep is None or ep.empty or "cell_name" not in ep.columns:
        return pd.DataFrame(columns=["ep_site", "ep_sector", "band", "tech"])
    d = ep[["cell_name", "site_id", "sector_num", "band_label", "technology"]].dropna(
        subset=["cell_name"])
    out = pd.DataFrame({
        "cell": d["cell_name"].astype(str).str.strip().str.upper(),
        "ep_site": d["site_id"].astype(str).str.strip().str.upper(),
        "ep_sector": pd.to_numeric(d["sector_num"], errors="coerce"),
        "band": d["band_label"].astype(str).str.strip(),
        "tech": d["technology"].astype(str).str.strip(),
    }).set_index("cell")
    return out[~out.index.duplicated(keep="first")]


def ep_cells(tech: str = "") -> pd.DataFrame:
    """The EP tracker's cells, of the technology being drawn when one is named."""
    out = _ep_cells(W.ep_key())
    want = EP_TECH.get(tech)
    return out[out["tech"].eq(want)] if want and len(out) else out


def _sector_of(sector_id: pd.Series) -> pd.Series:
    """The sector the export itself names: "BAS0001-S3" -> 3 (S0 = the site)."""
    return pd.to_numeric(sector_id.astype(str).str.extract(r"-S(\d+)$", expand=False),
                         errors="coerce")


def objects_of(index: pd.DataFrame, ep: pd.DataFrame) -> pd.DataFrame:
    """Every object of the loaded export with the sector the EP tracker gives it
    (the export's own only where the tracker does not list the cell)."""
    d = (index[["object", "site_id", "sector_id"]].dropna(subset=["object"])
         .drop_duplicates("object").copy())
    d["cell"] = d["object"].astype(str).str.strip().str.upper()
    d["site"] = d["site_id"].astype(str).str.strip().str.upper()
    own = _sector_of(d["sector_id"])
    joined = d.join(ep, on="cell") if len(ep) else d.assign(ep_site=np.nan, ep_sector=np.nan,
                                                            band=np.nan)
    d["sector"] = joined["ep_sector"].where(joined["ep_sector"].notna(), own)
    d["from_ep"] = joined["ep_sector"].notna()
    d["band"] = joined.get("band")
    # an object nobody places in a sector — a 3G NodeB is the whole site — answers
    # for every sector of its site; a cell the EP tracker places does not
    d["site_level"] = own.eq(0) & ~d["from_ep"]
    return d


@dataclass
class Group:
    """One chart: the cells of a site, or of a site's sector.

    `cells` is what the EP tracker gives the row; `live` is what the chosen KPI
    is actually measured on, and `site_only` says that is the site's own object
    (the KPI is counted for the site, not per cell)."""
    key: str
    title: str
    site: str
    sector: float | None
    cells: list = field(default_factory=list)
    bands: list = field(default_factory=list)
    from_ep: int = 0
    live: list = field(default_factory=list)
    site_only: bool = False


def groups_for(objects: pd.DataFrame, rows: pd.DataFrame,
               per_sector: bool) -> tuple[list[Group], list[tuple[str, str]]]:
    """A group per row of the list, and the rows nothing was found for."""
    groups: list[Group] = []
    missing: list[tuple[str, str]] = []
    seen: set = set()
    for r in rows.itertuples(index=False):
        site = str(r.site_id)
        sector = float(r.sector) if per_sector and pd.notna(r.sector) else None
        key = f"{site}|{sector:g}" if sector is not None else site
        if key in seen:
            continue
        seen.add(key)
        mine = objects[objects["site"].eq(site)]
        if mine.empty:
            missing.append((site if sector is None else f"{site} · Sector {sector:g}",
                            "no cell of this site in the loaded export"))
            continue
        if sector is not None:
            placed = mine[mine["sector"].eq(sector)]
            if placed.empty and not mine["sector"].gt(0).any():
                # nothing in this export belongs to a sector — a 3G export
                # measures the NodeB — so the site's object answers for it
                placed = mine[mine["site_level"]]
            mine = placed
            if mine.empty:
                missing.append((f"{site} · Sector {sector:g}",
                                "no cell in this sector (EP tracker)"))
                continue
        elif per_sector:
            missing.append((site, "no Sector in the list for this row"))
            continue
        bands = sorted({b for b in mine.get("band", pd.Series(dtype=object)).dropna()
                        if str(b) not in ("", "nan")})
        groups.append(Group(key=key,
                            title=site if sector is None else f"{site} - Sector {sector:g}",
                            site=site, sector=sector,
                            cells=list(mine["object"].astype(str)), bands=bands,
                            from_ep=int(mine["from_ep"].sum())))
    return groups, missing


def plan_for(groups: list, objects: pd.DataFrame, data: pd.DataFrame, kpi: str,
             per_sector: bool) -> tuple[list, list]:
    """What each chart draws, once the KPI's own level is known.

    A KPI measured on cells is drawn over the row's cells. Where only the site's
    own object holds it — a 3G export counts for the NodeB, and some KPIs (flow
    control among them) are only counted there — the site is one series instead.
    Per Sector cannot split such a KPI: it says so rather than repeat the site's
    line under every sector.
    """
    present = (set(data.loc[data[kpi].notna(), "object"].astype(str))
               if kpi in data.columns else set())
    whole = dict(zip(objects["object"].astype(str), objects["site_level"]))
    plan, missing = [], []
    for g in groups:
        live = [c for c in g.cells if c in present]
        cells = [c for c in live if not whole.get(c, False)]
        site = [c for c in live if whole.get(c, False)]
        if cells:                                   # the KPI is measured per cell
            g.live, g.site_only = cells, False
        elif site and not per_sector:                # only the site holds it
            g.live, g.site_only = site, True
        elif site:
            missing.append((g.title, f"{kpi} is a site-level KPI in this export — "
                                     "it cannot be split by sector (draw Per Site)"))
            continue
        else:
            missing.append((g.title, "no KPI data for it in this window"))
            continue
        plan.append(g)
    return plan, missing


# --------------------------------------------------------------------------- #
# the charts: Draw Data's own panels
# --------------------------------------------------------------------------- #
@st.cache_resource(show_spinner="Drawing the charts…", max_entries=3)
def _panels(sig: tuple, _data: pd.DataFrame, kpi: str, _groups: list) -> dict:
    """A Draw Data panel per group (and one for all of them together)."""
    where = _data.groupby(_data["object"].astype(str)).indices
    out: dict = {}

    def panel_of(cells: list, site: str | None = None):
        rows = np.concatenate([where[c] for c in cells if c in where]) if cells else np.array([])
        if not len(rows):
            return None
        part = _data.take(np.sort(rows))
        # a site-level KPI is one series for the site, not a line per object
        panels = (panels_for(part, [kpi], level="Site", obj=site) if site
                  else panels_for(part, [kpi], cells=[c for c in cells if c in where]))
        return panels[0] if panels else None

    for g in _groups:
        cells = g.live or g.cells
        out[g.key] = panel_of(cells, g.site if g.site_only else None)
    every: list = []
    for g in _groups:
        every += [c for c in (g.live or g.cells) if c not in every]
    out["__all__"] = panel_of(every)
    out["__cells__"] = every
    return out


def figure(panel, *, height: int = 300):
    """The Draw Data chart, unchanged."""
    return kpi_figure(panel, height=height, legend_title="Cell Name")


# --------------------------------------------------------------------------- #
# the exports
# --------------------------------------------------------------------------- #
def _safe(text: str) -> str:
    """A file name Windows takes: no reserved character, no run of spaces."""
    return re.sub(r"\s+", "_", re.sub(r'[\\/:*?"<>|]', "_", str(text)).strip())[:80]


def excel(data: pd.DataFrame, kpi: str, cells: list, stem: str) -> str:
    """The Draw Data workbook — same structure, for these cells only."""
    part = data[data["object"].astype(str).isin(set(cells))]
    if part.empty:
        return "No KPI data for the drawn cells."
    target = _shared.DL / f"{_safe(stem)}_{W.book_name([kpi])}"
    try:
        _, sheets, left = write_pivot_workbook(part, [kpi], target)
    except PermissionError:
        return f"**{target.name}** is open in Excel — close it and try again."
    extra = f"  ·  no data for: {', '.join(left)}" if left else ""
    return f"Saved to Downloads: **{target.name}**  ·  {len(sheets)} sheet(s){extra}"


CARD = tuple(int(PANEL[i:i + 2], 16) for i in (0, 2, 4))   # the card a chart sits on


def _lighter(raw: bytes) -> bytes:
    """The chart on the card it will sit on, in 256 colours.

    Draw Data's chart is drawn on nothing — the page shows through it — and a
    picture of it is a quarter of a megabyte of near-identical navy. Laid on
    the deck's own card and given a palette it looks the same and weighs a
    fifth as much, which is the difference between a deck that can be sent and
    one that cannot."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        card = Image.new("RGB", im.size, CARD)
        card.paste(im, mask=im.getchannel("A") if im.mode == "RGBA" else None)
        small = io.BytesIO()
        card.quantize(colors=256, method=Image.Quantize.FASTOCTREE,
                      dither=Image.Dither.NONE).save(small, "PNG", optimize=True)
    except Exception:
        return raw
    return small.getvalue() if len(small.getvalue()) < len(raw) else raw


def chart_pngs(jobs: list) -> list:
    """Pictures of the Draw Data charts themselves — the very figures the page
    draws — for the deck to place. None for each where this machine has no
    image engine for plotly (kaleido), and the deck draws the lines instead.

    `jobs` is (panel, width, height). They are written together: the engine
    opens a browser once, and doing that per chart costs seconds each.
    """
    out: list = [None] * len(jobs)
    live = [(i, j) for i, j in enumerate(jobs) if j[0] is not None]
    if not live:
        return out
    try:
        import plotly.io as pio
        with tempfile.TemporaryDirectory(prefix="rf_bulk_") as tmp:
            files = [Path(tmp) / f"{i}.png" for i, _ in live]
            pio.write_images([figure(j[0], height=j[2]) for _, j in live], files,
                             format="png", width=[j[1] for _, j in live],
                             height=[j[2] for _, j in live], scale=1.5)
            for (i, _), f in zip(live, files):
                out[i] = _lighter(f.read_bytes())
    except Exception:
        return [None] * len(jobs)            # the deck draws the lines itself
    return out


def save_report(charts: list, combined, deck: Deck, stem: str) -> str:
    """The drawn charts as a PowerPoint, six to a slide, into Downloads."""
    target = _shared.DL / f"{_safe(stem)}.pptx"
    try:
        target.write_bytes(build_deck(charts, combined, deck,
                                      Assets(ASSETS / "report_cover.jpg",
                                             ASSETS / "huawei_logo_white.png")))
    except PermissionError:
        return f"**{target.name}** is open in PowerPoint — close it and try again."
    slides = 1 + -(-len(charts) // 6) + (1 if combined else 0)
    return (f"Saved to Downloads: **{target.name}**  ·  {len(charts)} chart(s) on "
            f"{slides} slide(s)")


# --------------------------------------------------------------------------- #
# the page
# --------------------------------------------------------------------------- #
def _sources():
    """The active KPI Data, as Draw Data reads it."""
    return W.combine(R.kpi_groups())


def _window(index: pd.DataFrame) -> tuple:
    t = index["datetime"]
    return (t.min().date(), t.max().date()) if len(t) else (None, None)


def render() -> None:
    """Bulk Draw: the list, the KPI, the cells behind each row, the charts."""
    ss = st.session_state
    st.html(CSS)
    srcs = _sources()
    usable = [(b, i) for b, i in srcs if i.kind in ("4G", "3G", "2G", "Other")]
    if not usable:
        st.html('<div class="rf-card"><div class="kb-note">No KPI Data yet — upload the '
                "hourly exports once in Data Resources (sidebar → Data). Bulk Draw reads "
                "the same files Single Draw does.</div></div>")
        R.link("Open Data Resources")
        return

    c_up, c_kpi, c_by, c_out = st.columns(4, gap="small")

    # ---- 1. the list ------------------------------------------------------ #
    with c_up, st.container(key="rf_card_kb_up", border=True):
        st.html(step(1, "Upload Site & Sector List", "#1597FF"))
        up = st.file_uploader("Excel with two columns: Site ID, Sector",
                              type=["xlsx", "xlsm", "xls", "csv"], key="kb_file")
        rows, bad = None, None
        if up is not None:
            try:
                rows = read_list(up.name, up.getvalue())
            except ListError as exc:
                bad = str(exc)
        if rows is not None:
            ss["kb_rows"] = rows
            ss["kb_name"] = up.name
        rows = ss.get("kb_rows") if rows is None and up is None else rows
        if bad:
            st.html(f'<div class="kb-bad">{_esc(up.name)}: {_esc(bad)}</div>')
        elif rows is not None and len(rows):
            st.html(f'<div class="kb-ok">{len(rows):,} row(s) · '
                    f'{rows["site_id"].nunique():,} site(s)</div>')
            view = rows.head(50).copy()
            view["sector"] = view["sector"].map(lambda v: "—" if pd.isna(v) else f"{v:g}")
            st.dataframe(view.rename(columns={"site_id": "Site ID", "sector": "Sector"}),
                         hide_index=True, width="stretch", height=150)
        else:
            st.html('<div class="kb-note">Two columns only — Site ID and Sector. The cells '
                    "are taken from the EP tracker.</div>")

    # ---- 2. KPI, technology, time ----------------------------------------- #
    with c_kpi, st.container(key="rf_card_kb_kpi", border=True):
        st.html(step(2, "Select KPI & Time", "#22C55E"))
        techs = [t for t in ("4G", "3G", "2G", "Other") if t in {i.kind for _, i in usable}]
        if ss.get("kb_tech") not in techs:
            ss["kb_tech"] = techs[0]
        tech = st.segmented_control("Technology", techs, key="kb_tech", required=True,
                                    width="stretch")
        tech = tech or techs[0]
        mine = [(b, i) for b, i in usable if i.kind == tech]
        catalogue: list = []
        for _, info in mine:
            for k in info.all_kpis:
                if k not in catalogue:
                    catalogue.append(k)
        if ss.get("kb_kpi") not in catalogue:
            ss["kb_kpi"] = catalogue[0] if catalogue else None
        kpi = st.selectbox("KPI", catalogue, key="kb_kpi")
        index = pd.concat([W.index_of(b) for b, _ in mine], ignore_index=True)
        lo, hi = _window(index)
        if lo is not None:
            got = ss.get("kb_period")
            if not (isinstance(got, (tuple, list)) and len(got) == 2
                    and lo <= got[0] <= got[1] <= hi):
                ss["kb_period"] = (lo, hi)
            st.date_input("Time Range", min_value=lo, max_value=hi, key="kb_period",
                          format="DD/MM/YYYY")
        period = ss.get("kb_period")

    # ---- 3. draw by ------------------------------------------------------- #
    with c_by, st.container(key="rf_card_kb_by", border=True):
        st.html(step(3, "Draw By", "#A78BFA"))
        by = st.radio("Draw by", [PER_SITE, PER_SECTOR], key="kb_by",
                      captions=["All cells of each site, in one chart per site "
                                "(the Sector column is ignored)",
                                "The cells of the site's sector, from the EP tracker, "
                                "in one chart per row"],
                      label_visibility="collapsed")

    # ---- 4. output options ------------------------------------------------ #
    with c_out, st.container(key="rf_card_kb_out", border=True):
        st.html(step(4, "Output Options", "#F59E0B"))
        combined = st.checkbox("Draw Combined Chart (All in One)", value=True, key="kb_all")
        to_excel = st.checkbox("Export KPI Data to Excel", key="kb_xlsx")
        to_report = st.checkbox("Export Report", key="kb_report")
        with_info = st.checkbox("Add Site/Cell Information", value=True, key="kb_info")
        auto_open = st.checkbox("Auto Open Charts", value=True, key="kb_auto")
        draw = st.button("Draw Selected Charts", icon=":material/insights:", type="primary",
                         width="stretch", disabled=rows is None or not catalogue)

    if draw:
        ss["kb_run"] = {"tech": tech, "kpi": kpi, "by": by, "period": tuple(period or ()),
                        "combined": combined, "excel": to_excel, "report": to_report,
                        "info": with_info, "auto": auto_open, "name": ss.get("kb_name", "")}
        ss["kb_page"] = 1
        ss.pop("kb_saved", None)
    run = ss.get("kb_run")
    if not run or rows is None:
        return

    # ---- the cells behind each row, then the charts ------------------------ #
    # the drawn charts keep the files of the draw itself: changing the
    # technology afterwards must not look for its KPI in another export
    files = [(b, i) for b, i in usable if i.kind == run["tech"]]
    frames = [W.raw(b, (run["kpi"],)).assign(tech=i.kind) for b, i in files
              if run["kpi"] in set(i.all_kpis)]
    if not frames:
        st.html('<div class="rf-card"><div class="kb-bad">'
                f'<b>{_esc(run["kpi"])}</b> is not in the loaded {_esc(run["tech"])} export — '
                "pick a KPI of this technology and draw again.</div></div>")
        return
    data = W.in_period(pd.concat(frames, ignore_index=True), run["period"] or None)
    if data.empty or data[run["kpi"]].notna().sum() == 0:
        st.html('<div class="rf-card"><div class="kb-bad">No KPI data in the chosen time '
                "range.</div></div>")
        return

    ep = ep_cells(run["tech"])
    objects = objects_of(pd.concat([W.index_of(b) for b, _ in files], ignore_index=True), ep)
    groups, missing = groups_for(objects, rows, run["by"] == PER_SECTOR)
    groups, no_data = plan_for(groups, objects, data, run["kpi"], run["by"] == PER_SECTOR)
    missing += no_data
    if not groups:
        why = ("is measured for the site in this export, not per sector — draw it Per Site"
               if no_data and all("site-level" in b for _, b in no_data)
               else "is not measured on the cells of these rows in this window")
        st.html('<div class="rf-card"><div class="kb-bad">Nothing to draw: '
                f'<b>{_esc(run["kpi"])}</b> {why}.</div></div>')
        _missing_note(missing)
        return

    sig = (tuple(W.src_key(b) for b, _ in files), run["kpi"], run["by"],
           tuple(str(x) for x in (run["period"] or ())),
           tuple((g.key, tuple(g.live), g.site_only) for g in groups))
    panels = _panels(sig, data, run["kpi"], groups)
    drawn = [g for g in groups if panels.get(g.key) is not None]
    empty = [g for g in groups if panels.get(g.key) is None]
    site_only = [g for g in drawn if g.site_only]

    # ---- the exports, once per draw ---------------------------------------- #
    if ss.get("kb_saved") is None and (run["excel"] or run["report"]):
        saved = []
        stem = f"BulkDraw_{'Sector' if run['by'] == PER_SECTOR else 'Site'}"
        if run["excel"]:
            # the objects of the charts themselves — every one of them, once
            cells = list(dict.fromkeys(c for g in drawn for c in (g.live or g.cells)))
            saved.append(("Excel", excel(data, run["kpi"], cells, stem)))
        if run["report"]:
            # the charts in the order they were drawn, the combined one last
            whole = run["combined"] and panels.get("__all__") is not None
            jobs = [(panels[g.key], *GRID_PX) for g in drawn]
            jobs += [(panels["__all__"], *WHOLE_PX)] if whole else []
            with st.spinner("Making the report…"):
                shots = chart_pngs(jobs)
            charts = [(g.title, panels[g.key], png) for g, png in zip(drawn, shots)]
            one = (("Combined Chart (All in One)", panels["__all__"], shots[-1])
                   if whole else None)
            deck = Deck(kpi=run["kpi"], tech=run["tech"], by=run["by"],
                        window=f"{data['datetime'].min():%d %b %Y %H:%M} – "
                               f"{data['datetime'].max():%d %b %Y %H:%M}",
                        charts=len(charts) + (1 if one else 0))
            saved.append(("Report", save_report(charts, one, deck,
                                                f"{stem}_{_safe(run['kpi'])}")))
        ss["kb_saved"] = saved

    # ---- the results ------------------------------------------------------- #
    st.html(COPY_JS, unsafe_allow_javascript=True)
    st.html(f'<div class="kb-head"><div class="kb-title">Drawing Results'
            f'<small>{len(drawn)} chart(s) · {run["kpi"]} · {run["tech"]} · '
            f'{data["datetime"].min():%d %b %H:%M} → {data["datetime"].max():%d %b %H:%M}'
            f"</small></div></div>")
    for kind, note in (ss.get("kb_saved") or []):
        st.caption(f"{kind}: {note}")
    if site_only:
        st.html(f'<div class="kb-note"><b>{_esc(run["kpi"])}</b> is measured for the site in '
                f"this export, not per cell: {len(site_only)} chart(s) draw the site itself "
                "as one line.</div>")
    _missing_note(missing + [(g.title, "no KPI data for its cells in this window")
                             for g in empty])

    tabs = ["Individual Charts", "Combined Chart"] if run["combined"] else ["Individual Charts"]
    picked = st.tabs([f"{tabs[0]} ({len(drawn)})"] + ([f"{tabs[1]} (1)"] if len(tabs) > 1 else []))
    with picked[0]:
        _individual(drawn, panels, run)
    if len(picked) > 1:
        with picked[1]:
            if panels.get("__all__") is None:
                st.html('<div class="kb-note">No data for the combined chart.</div>')
            else:
                with st.container(key="rf_card_kb_all", border=True):
                    st.html(_head("rf_card_kb_all", "Combined Chart (All in One)",
                                  f'{len(panels["__cells__"]):,} cells of {len(drawn):,} charts',
                                  f'Combined_{run["kpi"]}'))
                    st.plotly_chart(figure(panels["__all__"], height=380), width="stretch",
                                    config={"displaylogo": False})


def _cells_note(g: Group, with_info: bool, most: int = 10) -> str:
    """Site / cell information: how many cells, their bands, and their names."""
    if not with_info:
        return ""
    cells = g.live or g.cells
    if g.site_only:
        return f"site-level KPI · {', '.join(cells)}"
    bands = f" · {', '.join(g.bands)}" if g.bands else ""
    names = ", ".join(cells[:most])
    more = f" … +{len(cells) - most} more" if len(cells) > most else ""
    return f"{len(cells)} cell(s){bands}: {names}{more}"


def _missing_note(missing: list) -> None:
    if not missing:
        return
    shown = "; ".join(f"{a} — {b}" for a, b in missing[:12])
    more = f" … and {len(missing) - 12} more" if len(missing) > 12 else ""
    st.html(f'<div class="kb-bad">Not drawn ({len(missing)}): {_esc(shown)}{_esc(more)}</div>')


def _individual(drawn: list, panels: dict, run: dict) -> None:
    """The charts, a page at a time, in Draw Data's own cards."""
    ss = st.session_state
    a, b = st.columns([3, 2], gap="small", vertical_alignment="center")
    q = a.text_input("Search site", key="kb_q", placeholder="Search site…",
                     label_visibility="collapsed")
    shown = [g for g in drawn if q.strip().upper() in g.title.upper()] if q else drawn
    pages = max(1, (len(shown) + PER_PAGE - 1) // PER_PAGE)
    page = min(max(int(ss.get("kb_page", 1)), 1), pages)
    with b, st.container(horizontal=True, horizontal_alignment="right", gap="small",
                         vertical_alignment="center"):
        st.html(f'<div class="kb-note" style="margin:0">Page {page} of {pages}</div>')
        if st.button("‹", key="kb_prev", disabled=page <= 1):
            ss["kb_page"] = page - 1
            st.rerun()
        if st.button("›", key="kb_next", disabled=page >= pages):
            ss["kb_page"] = page + 1
            st.rerun()
    if not shown:
        st.html('<div class="kb-note">No chart matches that search.</div>')
        return
    part = shown[(page - 1) * PER_PAGE:page * PER_PAGE]
    if not run["auto"]:
        with st.expander(f"Show {len(part)} chart(s)", expanded=False):
            _chart_grid(part, panels, run)
    else:
        _chart_grid(part, panels, run)


def _head(card: str, title: str, note: str, stem: str) -> str:
    """A chart's title bar, Draw Data's own — its Copy Chart button included."""
    return (f"<div class='sm-chart-head'><span class='t'>{_esc(title)}</span>"
            f"<span class='sm-verdict'>{_esc(note)}</span>"
            f"<button class='rf-copy' data-card='{card}' data-file='{_esc(_safe(stem))}' "
            "title='Copy this chart as a picture — paste it into email, WhatsApp, "
            "PowerPoint or a report'><span>Copy Chart</span></button></div>")


def _chart_grid(part: list, panels: dict, run: dict) -> None:
    """Two to a row, as Single Draw draws them: a cell legend needs the width."""
    for row in range(0, len(part), 2):
        for col, g in zip(st.columns(2, gap="small"), part[row:row + 2]):
            # the card's key names it in CSS too, so it holds word characters
            # only: Streamlit would write "BAS0438|2" as "BAS0438-2" and the
            # Copy Chart script would not find the card again
            card = "rf_card_kb_" + re.sub(r"\W", "_", g.key)
            name = _safe(f'{g.title}_{run["kpi"]}')
            with col, st.container(key=card, border=True):
                st.html(_head(card, g.title, f'{run["tech"]} · {run["kpi"]}', name))
                st.plotly_chart(figure(panels[g.key]), width="stretch",
                                config={"displaylogo": False,
                                        "toImageButtonOptions": {"format": "png", "scale": 2,
                                                                 "filename": name}},
                                key=f"kb_fig_{g.key}")
                if run["info"]:
                    st.html(f'<div class="kb-cells">{_esc(_cells_note(g, True))}</div>')
