"""Complaints — what the Overview and Ticket Details pages share.

The Daily Target, the KPI exports, the coverage grids and the EP tracker are
the Current versions in Data Resources (`_resources`) — uploaded and applied
there once, kept on disk. Every ticket is matched to its site in the EP tracker
and to that site's hourly KPIs around the problem time ± the correlation window,
judged on the operator's thresholds. Both views open the same workspace
(`open_workspace`): the header, the time correlation and data sidebar, and the
analysed tickets (`ctx.T`). Overview reads them together; Ticket Details reads one ticket as a
worksheet with its NOC incident panel (`ticket_panel`).

Nothing is guessed: a column the file does not carry, a site the tracker does
not know, a window the KPI exports do not cover read "Not available" or
"Insufficient Data".
"""

from __future__ import annotations

import html
import io
from types import SimpleNamespace

import numpy as np
import pandas as pd
import streamlit as st

from rfopt.complaints.correlate import (INSUFFICIENT, NO_ISSUE, NO_PROBLEM, severity,
                                        NOT_RESOLVED, POSSIBLE, RESOLVED,
                                        TECHNICAL, UNKNOWN, analyse_ticket,
                                        build_tracks, judged_kpis, local_time,
                                        r5_governorate, site_area_rsrp)
from rfopt.complaints.noc import (NAMES, PRIMARY, SECONDARY, build_indicators, fmt,
                                  indicator_columns, observe_indicators,
                                  site_timeline, ticket_tiles, tile_counts,
                                  window_series, _CANON_TILE)
from rfopt.complaints.relocate import fmt_metres
from rfopt.complaints.target_store import TargetFormatError, parse_target, source_columns
from rfopt.complaints.ticket_type import (NEW, REOPEN, TYPES, UP_OF_SLEEP,
                                          UP_OF_SLEEP_TEXT, ticket_type, type_label)
import _resources as R
from _shared import load_ep_all
from _ui import (PALETTE, card as _card,
                 header as _header, icon_img as _icon, kpi_cards as _kpi_cards,
                 title_html as _title_html)

NA = "Not available"
ENGINEERS = ("Shams", "Dhari", "Aws")
# the governorates, Sup Districts and cities are the ones KPI Analysis and its
# Report Export use: one placement for every page (`_kpi_region.site_regions`)
from _kpi_region import GOVERNORATES, UNKNOWN as _UNPLACED  # noqa: E402
WINDOWS = {"±30 min": 0.5, "±1 hour": 1.0, "±2 hours": 2.0, "±3 hours": 3.0,
           "Custom": None}
CLASS_COLOUR = {TECHNICAL: PALETTE["critical"], POSSIBLE: PALETTE["poor"],
                NO_ISSUE: PALETTE["cyan"], INSUFFICIENT: PALETTE["nodata"]}
RES_COLOUR = {RESOLVED: PALETTE["excellent"], NOT_RESOLVED: PALETTE["critical"],
              UNKNOWN: PALETTE["nodata"], NO_PROBLEM: PALETTE["cyan"]}
SEV_COLOUR = {2: PALETTE["critical"], 1: PALETTE["warning"], 0: PALETTE["excellent"],
              -1: PALETTE["nodata"]}
GLYPH = {2: "●", 1: "⚠︎", 0: "✓", -1: "—"}
PHASE = {2: "Critical", 1: "Warning", 0: "Normal", -1: "No data"}
TYPE_COLOUR = {NEW: PALETTE["cyan"], REOPEN: "#F59E0B", UP_OF_SLEEP: "#A78BFA",
               NA: PALETTE["nodata"]}
TYPE_ICON = {NEW: "ticket", REOPEN: "reopen", UP_OF_SLEEP: "sleep", NA: "info"}
TYPE_SHORT = {NEW: "NEW", REOPEN: "REOPEN", UP_OF_SLEEP: "UP SLEEP"}

_CSS = """
<style>
.ca-eng { display: grid; grid-template-columns: repeat(auto-fit, minmax(100px, 1fr)); gap: 8px; }
.ca-eng > div { background: #071525; border: 1px solid #1E3A5F; border-radius: 10px; padding: 9px 10px; }
.ca-eng-h { display: flex; align-items: center; gap: 8px; }
.ca-av { flex: 0 0 26px; height: 26px; border-radius: 50%; display: flex; align-items: center;
    justify-content: center; background: rgba(21, 151, 255, .18); border: 1px solid rgba(32, 191, 255, .5);
    color: #20BFFF; font-weight: 700; font-size: 12px; }
.ca-eng-n { font-weight: 700; color: #F1F5F9; font-size: 13px; }
.ca-eng-v { font-size: 21px; font-weight: 700; color: #F8FAFC; margin-top: 4px;
    font-variant-numeric: tabular-nums; }
.ca-eng-v small { font-size: 11px; color: #94A3B8; font-weight: 500; margin-left: 4px; }
.ca-bar { height: 5px; border-radius: 3px; background: #16324F; overflow: hidden; margin: 5px 0 4px; }
.ca-bar i { display: block; height: 100%; background: #1597FF; border-radius: 3px; }
.ca-bar.stk { display: flex; }
.ca-bar.stk i { border-radius: 0; flex: 0 0 auto; }
.ca-eng-s { font-size: 11px; color: #94A3B8; line-height: 1.45; }
.ca-mini { display: flex; flex-direction: column; gap: 2px; margin-top: 6px; padding-top: 6px;
    border-top: 1px solid #16324F; }
.ca-mini div { display: flex; align-items: center; gap: 6px; font-size: 11px; color: #CBD5E1; }
.ca-mini i { flex: 0 0 7px; height: 7px; border-radius: 50%; background: var(--c); }
.ca-mini b { margin-left: auto; font-variant-numeric: tabular-nums; color: #F1F5F9; }
.ca-rows { display: flex; flex-direction: column; gap: 7px; }
.ca-row { display: grid; grid-template-columns: minmax(84px, 1.25fr) 1.6fr 34px 46px; gap: 2px 8px;
    align-items: center; font-size: 12.5px; color: #E2E8F0; }
.ca-row .ca-l { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ca-row .ca-n { text-align: right; font-weight: 600; font-variant-numeric: tabular-nums; }
.ca-row .ca-p { text-align: right; color: #94A3B8; font-size: 11.5px; font-variant-numeric: tabular-nums; }
.ca-row .ca-bar { margin: 0; }
.ca-row .ca-bar i.t { background: #EF4444; }
.ca-row .ca-sub { grid-column: 1 / -1; font-size: 10.5px; color: #94A3B8; margin-top: -2px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ca-sub em { font-style: normal; font-weight: 700; color: var(--c); font-variant-numeric: tabular-nums; }
.ca-ttw { display: flex; align-items: center; gap: 14px; }
.ca-donut { flex: 0 0 104px; height: 104px; border-radius: 50%; position: relative;
    background: conic-gradient(var(--g)); box-shadow: 0 0 18px rgba(32, 191, 255, .12); }
.ca-donut::after { content: ""; position: absolute; inset: 15px; border-radius: 50%; background: #0B1F33; }
.ca-donut-c { position: absolute; inset: 0; z-index: 1; display: flex; flex-direction: column;
    align-items: center; justify-content: center; }
.ca-donut-c b { font-size: 22px; color: #F8FAFC; line-height: 1; font-variant-numeric: tabular-nums; }
.ca-donut-c span { font-size: 9.5px; letter-spacing: .12em; color: #94A3B8; margin-top: 4px; }
.ca-leg { flex: 1 1 auto; display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.ca-leg div { display: grid; grid-template-columns: 10px 1fr auto 40px; gap: 8px; align-items: center;
    font-size: 12.5px; color: #E2E8F0; }
.ca-leg i { width: 9px; height: 9px; border-radius: 50%; background: var(--c);
    box-shadow: 0 0 6px var(--c); }
.ca-leg b { font-variant-numeric: tabular-nums; }
.ca-leg small { color: #94A3B8; text-align: right; font-variant-numeric: tabular-nums; font-size: 11.5px; }
.st-key-ca_f_tt [data-testid="stButtonGroup"] button { border-left: 3px solid var(--ca-tt, #20BFFF) !important; }
.st-key-ca_f_tt [data-testid="stButtonGroup"] button:nth-of-type(2) { --ca-tt: #F59E0B; }
.st-key-ca_f_tt [data-testid="stButtonGroup"] button:nth-of-type(3) { --ca-tt: #A78BFA; }
.ca-strip { display: grid; grid-template-columns: repeat(auto-fit, minmax(104px, 1fr)); gap: 8px; }
.ca-det { background: #0B1F33; border: 1px solid #1E3A5F; border-radius: 12px; padding: 12px 14px;
    display: flex; flex-direction: column; gap: 9px; }
.ca-det h4 { margin: 14px 0 6px; font-size: 11px; letter-spacing: .07em; text-transform: uppercase;
    color: #20BFFF; font-weight: 700; }
.ca-det h4:first-of-type { margin-top: 10px; }
.ca-kv { display: grid; grid-template-columns: auto 1fr; gap: 3px 12px; font-size: 12.5px; }
.ca-kv span { color: #94A3B8; }
.ca-kv b { color: #F1F5F9; font-weight: 600; text-align: right; word-break: break-word; }
.ca-badge { display: inline-flex; align-items: center; gap: 6px; padding: 2px 9px; border-radius: 999px;
    font-size: 11.5px; font-weight: 700; color: var(--c); white-space: nowrap;
    background: color-mix(in srgb, var(--c) 16%, transparent);
    border: 1px solid color-mix(in srgb, var(--c) 45%, transparent); }
.ca-badge i { width: 7px; height: 7px; border-radius: 50%; background: var(--c); }
.ca-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.ca-id { display: flex; align-items: center; gap: 7px; font-size: 16px; font-weight: 700; color: #F8FAFC; }
.ca-note { font-size: 12px; color: #CBD5E1; line-height: 1.5; margin-top: 6px; }
.ca-chk { border-top: 1px solid #16324F; padding: 6px 0; font-size: 12px; }
.ca-chk-h { display: flex; justify-content: space-between; gap: 8px; color: #E2E8F0; font-weight: 600; }
.ca-chk-s { color: #94A3B8; margin-top: 2px; line-height: 1.45; }
/* ---- NOC incident panel ---- */
.ca-chips { display: flex; flex-wrap: wrap; gap: 5px; }
.ca-chip { display: inline-flex; align-items: center; gap: 5px; background: #071525; border: 1px solid #1E3A5F;
    border-radius: 8px; padding: 3px 8px; font-size: 11.5px; color: #CBD5E1; max-width: 100%;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ca-sec { display: flex; align-items: center; gap: 6px; margin: 5px 0 -3px; font-size: 10.5px;
    letter-spacing: .08em; text-transform: uppercase; color: #20BFFF; font-weight: 700; }
.ca-sec small { margin-left: auto; color: #64748B; letter-spacing: 0; text-transform: none; font-weight: 500; }
.ca-st { display: inline-flex; align-items: center; gap: 4px; font-weight: 700; color: var(--c); white-space: nowrap; }
.ca-ttb { display: flex; flex-direction: column; gap: 6px; background: #071525; border: 1px solid #1E3A5F;
    border-left: 4px solid var(--c); border-radius: 10px; padding: 8px 10px; }
.ca-ttb-h { display: flex; align-items: center; gap: 7px; font-size: 14px; font-weight: 800; color: var(--c);
    letter-spacing: .03em; }
.ca-inc { border: 1px solid color-mix(in srgb, var(--c) 50%, transparent); border-left: 4px solid var(--c);
    background: color-mix(in srgb, var(--c) 9%, #071525); border-radius: 10px; padding: 9px 11px; }
.ca-inc-h { display: flex; align-items: center; gap: 8px; font-weight: 800; font-size: 13.5px; color: var(--c);
    letter-spacing: .03em; }
.ca-inc-g { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 12px; margin-top: 8px; }
.ca-inc-g span, .ca-lbl { display: block; font-size: 10px; color: #94A3B8; text-transform: uppercase;
    letter-spacing: .06em; }
.ca-inc-g b { display: block; font-size: 12.5px; color: #F1F5F9; margin-top: 1px; word-break: break-word; }
.ca-inc p { margin: 6px 0 0; font-size: 12.5px; color: #E2E8F0; line-height: 1.45; }
.ca-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(74px, 1fr)); gap: 6px; }
.ca-tile { background: #071525; border: 1px solid #1E3A5F; border-top: 3px solid var(--c); border-radius: 9px;
    padding: 6px 7px 7px; min-width: 0; }
.ca-tile-k { display: flex; justify-content: space-between; align-items: center; gap: 4px; font-size: 11px;
    font-weight: 800; color: #CBD5E1; letter-spacing: .06em; }
.ca-tile-k i { font-style: normal; font-size: 9px; font-weight: 600; color: #64748B; letter-spacing: 0; }
.ca-tile-v { font-size: 16px; font-weight: 700; color: #F8FAFC; margin-top: 2px; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; font-variant-numeric: tabular-nums; }
.ca-tile-v small { font-size: 11px; color: #94A3B8; font-weight: 500; }
.ca-tile-s { font-size: 11px; font-weight: 800; color: var(--c); white-space: nowrap; letter-spacing: .03em; }
.ca-tile-n { font-size: 10px; color: #94A3B8; margin-top: 1px; white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }
.ca-s2w { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 5px; }
.ca-s2 { display: flex; align-items: center; gap: 6px; background: #071525; border: 1px solid #1E3A5F;
    border-left: 3px solid var(--c); border-radius: 8px; padding: 4px 8px; font-size: 11.5px; min-width: 0; }
.ca-s2 span { flex: 1 1 auto; min-width: 0; color: #CBD5E1; font-weight: 700; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap; }
.ca-s2 b { color: #F1F5F9; font-weight: 600; white-space: nowrap; font-variant-numeric: tabular-nums; }
.ca-s2 em { font-style: normal; font-weight: 700; color: var(--c); white-space: nowrap; }
.ca-evc { background: #071525; border: 1px solid #1E3A5F; border-left: 3px solid var(--c); border-radius: 9px;
    padding: 6px 9px; }
.ca-evc + .ca-evc { margin-top: 5px; }
.ca-evc-h { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-weight: 700;
    color: #F1F5F9; font-size: 12px; margin-bottom: 4px; }
.ca-ev { display: grid; grid-template-columns: auto 1fr; gap: 2px 10px; font-size: 11.5px; }
.ca-ev span { color: #94A3B8; }
.ca-ev b { color: #E2E8F0; font-weight: 600; text-align: right; word-break: break-word; }
.ca-tl { position: relative; padding: 16px 0 4px; }
.ca-tl-bar { display: flex; gap: 2px; height: 14px; }
.ca-tl-bar i { flex: 1 1 0; border-radius: 3px; background: var(--c); }
.ca-tl-bar i.out { opacity: .45; }
.ca-tl-win { position: absolute; top: 12px; bottom: 0; border: 1.5px dashed rgba(32, 191, 255, .85);
    border-radius: 5px; pointer-events: none; }
.ca-tl-pt { position: absolute; top: 12px; bottom: 0; width: 2px; margin-left: -1px; background: #F8FAFC;
    box-shadow: 0 0 6px rgba(255, 255, 255, .8); }
.ca-tl-pt span { position: absolute; top: -14px; left: 1px; transform: translateX(-50%); font-size: 10.5px;
    font-weight: 700; color: #F8FAFC; white-space: nowrap; }
.ca-tl-ax { display: flex; justify-content: space-between; gap: 6px; font-size: 10.5px; color: #94A3B8; }
.ca-ph { display: grid; grid-template-columns: repeat(3, 1fr); gap: 5px; margin-top: 6px; }
.ca-ph div { background: #071525; border: 1px solid #1E3A5F; border-top: 2px solid var(--c); border-radius: 8px;
    padding: 4px 7px; font-size: 10px; color: #94A3B8; min-width: 0; }
.ca-ph b { display: block; color: var(--c); font-size: 11.5px; white-space: nowrap; }
.ca-ph small { display: block; font-size: 10px; color: #CBD5E1; white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }
.ca-res { background: #071525; border: 1px solid #1E3A5F; border-left: 4px solid var(--c); border-radius: 10px;
    padding: 7px 10px; }
.ca-res > b { color: var(--c); font-size: 13px; font-weight: 800; letter-spacing: .03em; }
.ca-rl { display: grid; grid-template-columns: 1fr auto; gap: 0 8px; margin-top: 5px; font-size: 11.5px; }
.ca-rl span { color: #E2E8F0; font-weight: 600; }
.ca-rl em { font-style: normal; font-weight: 700; color: var(--c); white-space: nowrap; }
.ca-rl small { grid-column: 1 / -1; color: #94A3B8; font-size: 10.5px; }
.ca-fin { background: #071525; border: 1px solid #1E3A5F; border-radius: 10px; padding: 8px 10px; }
.ca-fin-g { display: grid; grid-template-columns: auto 1fr; gap: 5px 12px; font-size: 12.5px; align-items: center; }
.ca-fin-g > span { color: #94A3B8; }
.ca-fin-g > b { text-align: right; color: #F1F5F9; font-weight: 700; }
.ca-fin-g > b .ca-st, .ca-fin-g > b .ca-badge { justify-content: flex-end; }
.ca-fin p { margin: 8px 0 0; padding-top: 7px; border-top: 1px solid #16324F; font-size: 12px; color: #CBD5E1;
    line-height: 1.45; }
.ca-more summary { cursor: pointer; color: #20BFFF; font-size: 12px; font-weight: 600; margin-top: 2px; }
.ca-dots { position: relative; height: 24px; margin: 12px 8px 0; }
.ca-dots-l { position: absolute; left: 0; right: 0; top: 11px; height: 2px; background: #1E3A5F; }
.ca-dots i { position: absolute; top: 5px; width: 14px; height: 14px; margin-left: -7px; border-radius: 50%;
    background: var(--c); border: 2px solid #0B1F33; box-shadow: 0 0 6px var(--c); }
</style>
"""


def _esc(x) -> str:
    return html.escape(str(x))


def _clean(x) -> str:
    s = "" if x is None else str(x).strip()
    return "" if s.lower() in ("", "nan", "none", "nat") else s


def _eng_name(x) -> str:
    s = _clean(x)
    return s.title() if s else "Unassigned"


def _badge(text: str, colour: str) -> str:
    return f'<span class="ca-badge" style="--c:{colour}"><i></i>{_esc(text)}</span>'


def _kv(rows) -> str:
    return '<div class="ca-kv">' + "".join(
        f"<span>{_esc(k)}</span><b>{_esc(v if _clean(v) else NA)}</b>" for k, v in rows) + "</div>"


# Streamlit forgets a page's widget values when another page is opened. Each
# value is copied here as it is read, and handed back when the page comes back.
_KEEP = "ca_keep"


def _remember(key: str, value) -> None:
    st.session_state.setdefault(_KEEP, {})[key] = value


def _restore(key: str, ok=lambda v: True, *, force: bool = False) -> None:
    """Back from another page: give the widget the value it had, if it still fits."""
    kept = st.session_state.get(_KEEP, {})
    if (force or key not in st.session_state) and key in kept and ok(kept[key]):
        st.session_state[key] = kept[key]


def _fit(key: str, options, multi: bool = True) -> None:
    """Before a filter is drawn: restore it, and drop values no longer offered
    (a new Daily Target can have other districts or engineers)."""
    _restore(key)
    if key not in st.session_state:
        return
    opts, v = list(options), st.session_state[key]
    if multi:
        fitted = [x for x in (v or []) if x in opts]
        if fitted != list(v or []):
            st.session_state[key] = fitted
    elif v is not None and v not in opts:
        st.session_state[key] = None


def _mirror(src: str, dst: str) -> None:
    """The engineer buttons and the Engineer filter are one filter."""
    st.session_state[dst] = list(st.session_state.get(src) or [])


def _ep_path() -> str | None:
    """The Current Site Details Data (EP) of Data Resources."""
    return R.ep_path()


@st.cache_resource(show_spinner=False, max_entries=2)
def _load_source(sha: str, _ds, rows: tuple) -> pd.DataFrame:
    """Every column of the uploaded file, one row per ticket, as the file has it."""
    return source_columns(_ds.read_bytes(), _ds.name, rows)


@st.cache_resource(show_spinner=False, max_entries=2)
def _load_tickets(sha: str, _ds) -> pd.DataFrame:
    t = parse_target(_ds.read_bytes(), _ds.name)
    t["problem_local"] = local_time(t["problem_time"])
    return t


def _first(values) -> str:
    for x in values:
        v = _clean(x)
        if v:
            return v
    return ""


@st.cache_resource(show_spinner=False, max_entries=2)
def _site_table(path: str) -> pd.DataFrame:
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


@st.cache_resource(show_spinner=False, max_entries=2)
def _read_frames(key: tuple, _sources: dict):
    """One read of each export of the active KPI Data: the judged frames and
    the context frames. The files of one technology are read as one export
    (`merge_hourly`), so an hour two files share is one point of the track.
    `key`: the files' content hashes; `_sources`: hash → (name, path), a
    technology's most recent file last (`R.kpi_groups`)."""
    from rfopt.ingest.hourly_kpi import load_hourly_raw, merge_hourly
    by_kind: dict = {}
    for fid, (name, path) in _sources.items():
        if fid not in key:
            continue
        try:
            info = R.kpi_info(path)
        except Exception:
            continue
        by_kind.setdefault(info.kind, []).append((name, path, info))
    frames, extra = [], []
    for kind, group in by_kind.items():
        kpis = []
        for _, _, info in group:
            kpis += [k for k in info.all_kpis if k not in kpis]
        judged = judged_kpis(kpis, kind)
        context = indicator_columns(kpis, kind)
        if not judged and not context:
            continue
        cols = [c for c, _, _ in judged] + [c for c, _, _ in context]
        raw = merge_hourly([load_hourly_raw(path, [c for c in cols if c in set(info.all_kpis)])
                            for _, path, info in group
                            if set(cols) & set(info.all_kpis)])
        name = " + ".join(n for n, _, _ in group)
        if judged:
            frames.append((kind, name, raw, judged))
        if context:
            extra.append((name, raw, context))
    return frames, extra


@st.cache_resource(show_spinner=False, max_entries=2)
def _load_tracks(key: tuple, _sources: dict):
    """The judged KPI tracks (they decide the classification) and the context
    indicators (shown next to them), per site."""
    frames, extra = _read_frames(key, _sources)
    return build_tracks(frames), build_indicators(extra)


@st.cache_resource(show_spinner=False, max_entries=2)
def _load_sector_tracks(key: tuple, _sources: dict):
    """The same judged KPI tracks per sector — what a ticket re-analysed at
    the user's location is judged on (`rfopt.complaints.relocate`)."""
    from rfopt.complaints.relocate import sector_tracks
    return sector_tracks(_read_frames(key, _sources)[0])


def window_setting() -> tuple[str, float]:
    """The Correlation Window chosen on Delay Tickets Analysis (or its default):
    every page that shows a Daily Target ticket judges it on the same window."""
    kept = st.session_state.get(_KEEP, {})
    label = kept.get("ca_win") if kept.get("ca_win") in WINDOWS else "±2 hours"
    if WINDOWS[label] is None:
        h = kept.get("ca_win_custom")
        h = float(h) if isinstance(h, (int, float)) and 0.25 <= h <= 24 else 2.0
        return f"±{h:g} h", h
    return label, float(WINDOWS[label])


@st.cache_resource(show_spinner=False, max_entries=6)
def _analyse(sha: str, kpi_key: tuple, window_h: float, _tickets, _tracks) -> list:
    return [analyse_ticket(t.site_id if isinstance(t.site_id, str) else "",
                           t.problem_local, _tracks, window_h)
            for t in _tickets.itertuples(index=False)]


@st.cache_resource(show_spinner=False, max_entries=6)
def _noc_all(sha: str, kpi_key: tuple, window_h: float, _tickets, _analysis, _inds) -> list:
    return [ticket_tiles(a, observe_indicators(t.site_id if isinstance(t.site_id, str) else "",
                                               t.problem_local, _inds, window_h))
            for t, a in zip(_tickets.itertuples(index=False), _analysis)]


@st.cache_resource(show_spinner=False, max_entries=2)
def _site_rsrp(cov_key: tuple, site_key: tuple, _files: tuple, _sites: dict) -> dict:
    from rfopt.geo.coverage import load_bands
    _, covered = load_bands()
    return site_area_rsrp(list(_files), _sites, 500.0, covered)


# --------------------------------------------------------------------------- #
# the NOC incident panel — presentation of the analysis, nothing decided here
# --------------------------------------------------------------------------- #
def _state_html(sev: int, text: str) -> str:
    return f'<span class="ca-st" style="--c:{SEV_COLOUR[sev]}">{GLYPH[sev]} {_esc(text)}</span>'


def _sec(title: str, icon: str, note: str = "") -> str:
    return (f'<div class="ca-sec">{_icon(icon, PALETTE["cyan"], 13)}<span>{_esc(title)}</span>'
            + (f"<small>{_esc(note)}</small>" if note else "") + "</div>")


def _chip(icon: str, text) -> str:
    return (f'<span class="ca-chip" title="{_esc(text)}">{_icon(icon, PALETTE["cyan"], 13)}'
            f'{_esc(text)}</span>')


def _tip(t) -> str:
    return " · ".join(x for x in (t.name, t.threshold, t.note,
                                  "" if t.judged else "context, not in the classification") if x)


def _value_html(value: str) -> str:
    """'-108 dBm' -> the number large, the unit small."""
    num, _, unit = str(value).partition(" ")
    return _esc(num) + (f"<small> {_esc(unit)}</small>" if unit else "")


def _tile_html(t) -> str:
    return (f'<div class="ca-tile" style="--c:{SEV_COLOUR[t.sev]}" title="{_esc(_tip(t))}">'
            f'<div class="ca-tile-k"><span>{_esc(t.key)}</span>{"" if t.judged else "<i>context</i>"}</div>'
            f'<div class="ca-tile-v">{_value_html(t.value)}</div>'
            f'<div class="ca-tile-s">{GLYPH[t.sev]} {_esc(t.state.upper())}</div>'
            f'<div class="ca-tile-n">{_esc(t.at or t.note or t.name)}</div></div>')


def _secondary_html(t) -> str:
    return (f'<div class="ca-s2" style="--c:{SEV_COLOUR[t.sev]}" title="{_esc(_tip(t))}">'
            f'<span>{_esc(t.key)}</span><b>{_esc(t.value)}</b>'
            f'<em>{GLYPH[t.sev]} {_esc(t.state)}</em></div>')


def _type_html(kind: str, number, user: str, problem_time: str) -> str:
    colour = TYPE_COLOUR.get(kind, PALETTE["nodata"])
    number = int(number) if kind == REOPEN and pd.notna(number) else None
    label = type_label(kind, number) if kind in TYPES else NA
    head = (f'<div class="ca-ttb-h">{_icon(TYPE_ICON.get(kind, "info"), colour, 16)}'
            f'{_esc(label)}</div>')
    if kind == REOPEN:
        body = _kv([("Reopen number", f"#{number}"), ("Original ticket", NA),
                    ("Original problem time", NA), ("Problem time (Daily Target)", problem_time)])
        body += '<div class="ca-lbl">No reopen history in the Daily Target</div>'
    elif kind == UP_OF_SLEEP:
        body = (f'<div class="ca-note" style="margin:0">{_esc(UP_OF_SLEEP_TEXT)}</div>'
                + _kv([("User", user)]))
    elif kind == NEW:
        body = '<div class="ca-lbl">No reopen count and no User on the ticket</div>'
    else:
        body = '<div class="ca-lbl">The Daily Target has no Reopen column</div>'
    return f'<div class="ca-ttb" style="--c:{colour}">{head}{body}</div>'


def _incident_html(a, site_id: str, lead, band) -> str:
    if a.classification in (TECHNICAL, POSSIBLE):
        colour = CLASS_COLOUR[a.classification]
        head = "NETWORK ISSUE DETECTED" if a.classification == TECHNICAL else "POSSIBLE NETWORK ISSUE"
        problem = (a.problem_type or "–") + (f" — {lead.label} {fmt(lead.worst, lead.unit)}"
                                            if lead else "")
        where = site_id + (f" · {lead.worst_obj}" if lead is not None and lead.worst_obj else "")
        window = lead.breach_span if lead is not None and lead.breach_span else a.window
        res = _badge(a.resolution, RES_COLOUR.get(a.resolution, PALETTE["nodata"]))
        body = ('<div class="ca-inc-g">'
                f"<div><span>Primary problem</span><b>{_esc(problem)}</b></div>"
                f"<div><span>Affected site</span><b>{_esc(where)}</b></div>"
                f"<div><span>Problem window</span><b>{_esc(window or NA)}</b></div>"
                f"<div><span>Resolution</span><b>{res}</b></div></div>")
        icon = "alert"
    elif a.classification == NO_ISSUE:
        colour, head, icon = PALETTE["excellent"], "NO NETWORK ISSUE DETECTED", "check"
        rsrp_ok = band is not None and band.key in ("excellent", "good")
        text = ("KPI and site-area RSRP values are within normal operating thresholds."
                if rsrp_ok else "The judged KPIs are within normal operating thresholds "
                                "around the complaint.")
        body = (f"<p>{_esc(text)}</p><p><span class=\"ca-lbl\">Recommendation</span>"
                "Manual customer verification recommended.</p>")
    else:
        colour, head, icon = PALETTE["nodata"], "INSUFFICIENT DATA", "info"
        body = f"<p>{_esc(a.site_issue)}</p>"
    return (f'<div class="ca-inc" style="--c:{colour}"><div class="ca-inc-h">'
            f'{_icon(icon, colour, 16)}{_esc(head)}</div>{body}</div>')


def _timeline_html(tl, win_label: str, window: str) -> str:
    """Hour by hour, the state of the one KPI the timeline is about (named on
    top), from before the problem window to after it, and its peak in the
    window: time, value, threshold and status."""
    hour = pd.Timedelta(hours=1)
    total = (tl.end + hour - tl.start) / hour

    def pos(ts) -> float:
        return max(0.0, min(100.0, 100.0 * ((pd.Timestamp(ts) - tl.start) / hour) / total))

    drivers = tl.drivers or [[] for _ in tl.hours]
    bars = "".join(
        f'<i class="{"" if tl.lo <= h <= tl.hi else "out"}" style="--c:{SEV_COLOUR[s]}" '
        f'title="{h:%d %b %H:%M} · {PHASE[s]}'
        + (f" · {_esc(', '.join(d))}" if d else "") + '"></i>'
        for (h, s), d in zip(tl.hours, drivers))
    left, right = pos(tl.lo), pos(tl.hi)

    def phase(name: str, sev: int, extra: str = "") -> str:
        return (f'<div style="--c:{SEV_COLOUR[sev]}">{_esc(name)}<b>{GLYPH[sev]} {PHASE[sev]}</b>'
                + (f"<small>{_esc(extra)}</small>" if extra else "") + "</div>")

    kpis = ('<div class="ca-tl-k"><span>Timeline KPI' + ("s" if len(tl.kpis) != 1 else "")
            + "</span>" + "".join(f"<em>{_esc(k)}</em>" for k in tl.kpis) + "</div>")
    how = ('<div class="ca-tl-how">Each hour shows this KPI\'s state at the site (its worst '
           "cell that hour, judged on its thresholds). Before = the 2 h before the window · "
           f"Problem window = {_esc(win_label)} around the problem time · After = the 3 h "
           "after it.</div>")
    peak = ""
    if tl.peak:
        pk = tl.peak
        peak = ('<div class="ca-tl-peakg" style="--c:' + SEV_COLOUR[pk["sev"]] + '">'
                + "".join(f"<div><span>{k}</span><b>{v}</b></div>" for k, v in (
                    ("Peak Time", f'{pk["hour"]:%d %b %H:%M}'),
                    ("KPI Value", _esc(pk["value"]) + (f' <small>{_esc(pk["object"])}</small>'
                                                       if pk["object"] else "")),
                    ("Threshold", _esc(pk["threshold"])),
                    ("Status", f'{GLYPH[pk["sev"]]} {PHASE[pk["sev"]]}')))
                + "</div>")
    return (kpis + f'<div class="ca-tl"><div class="ca-tl-bar">{bars}</div>'
            f'<div class="ca-tl-win" style="left:{left:.2f}%;width:{max(right - left, 1):.2f}%"></div>'
            f'<div class="ca-tl-pt" style="left:{pos(tl.problem_time):.2f}%">'
            f'<span>◷ {tl.problem_time:%H:%M}</span></div></div>'
            f'<div class="ca-tl-ax"><span>{tl.start:%d %b %H:%M}</span>'
            f'<span>Problem window {_esc(win_label)}</span>'
            f'<span>{tl.end + hour:%H:%M}</span></div>'
            '<div class="ca-ph">' + phase("Before", tl.before)
            + phase("Problem Window", tl.during, tl.issue_span or window)
            + phase("After", tl.after)
            + "</div>" + peak + how)


def _resolution_html(a, lead_checks) -> str:
    colour = RES_COLOUR.get(a.resolution, PALETTE["nodata"])
    glyph = {RESOLVED: "✓", NOT_RESOLVED: "●", NO_PROBLEM: "✓"}.get(a.resolution, "—")
    lines = "".join(
        f'<div class="ca-rl"><span>{_esc(c.label)}</span>'
        f'<em style="--c:{RES_COLOUR.get(c.resolution, PALETTE["nodata"])}">'
        f'{_esc(c.resolution or UNKNOWN)}</em><small>{_esc(c.resolution_note or c.after)}</small></div>'
        for c in lead_checks[:4])
    if not lead_checks:
        text = ("No KPI breach around the complaint — nothing to recover from."
                if a.classification == NO_ISSUE else a.site_issue)
        lines = f'<div class="ca-rl"><small>{_esc(text)}</small></div>'
    return (f'<div class="ca-res" style="--c:{colour}"><b>{glyph} {_esc(a.resolution.upper())}</b>'
            f"{lines}</div>")


def _final_html(a, lead, band) -> str:
    tech, tcol = {"Yes": ("YES", PALETTE["critical"]), "Possible": ("POSSIBLE", PALETTE["poor"]),
                  "No": ("NO", PALETTE["excellent"])}.get(a.problem_detected,
                                                         ("UNKNOWN", PALETTE["nodata"]))
    if lead is not None:
        kpi = _state_html(lead.sev, lead.label)
    elif a.classification == NO_ISSUE:
        kpi = _state_html(0, "Normal")
    else:
        kpi = _state_html(-1, "No data")
    rsrp = (f'<span class="ca-st" style="--c:{band.colour}">{_esc(band.label)}</span>'
            if band is not None else _esc(NA))
    issue = a.problem_type or ("None" if a.classification == NO_ISSUE else "Unknown")
    rows = [("Technical issue", f'<span class="ca-st" style="--c:{tcol}">{tech}</span>'),
            ("Primary KPI", kpi), ("RSRP (site area)", rsrp), ("Site issue", _esc(issue)),
            ("Resolution", _badge(a.resolution, RES_COLOUR.get(a.resolution, PALETTE["nodata"]))),
            ("Confidence", _esc((a.confidence or "–").upper()))]
    return ('<div class="ca-fin"><div class="ca-fin-g">'
            + "".join(f"<span>{_esc(k)}</span><b>{v}</b>" for k, v in rows)
            + f"</div><p>{_esc(a.site_issue)}</p></div>")




# --------------------------------------------------------------------------- #
# Complaints · Delay Tickets Analysis: one page, one workspace
# --------------------------------------------------------------------------- #
PAGE = "views/complaint_analysis.py"
DELAYED, ON_TIME = "Delayed", "On time"
DELAY_RULE = "SLA Status is sla_violation"


def _area_value(area, key: str):
    """One field of a site-area RSRP record, or None when it is missing or
    empty — a record may carry the median alone (a point's RSRP)."""
    v = area.get(key) if isinstance(area, dict) else None
    try:
        return None if v is None or pd.isna(v) else v
    except (TypeError, ValueError):
        return v


def rsrp_band(area, bands):
    median = _area_value(area, "median")
    if median is None or not bands:
        return None
    from rfopt.geo.coverage import band_index
    return bands[int(band_index(np.array([median]), bands)[0])]


def type_order(label: str) -> tuple:
    if label == NEW:
        return (0, 0)
    if label.startswith(REOPEN):
        digits = "".join(ch for ch in label if ch.isdigit())
        return (1, int(digits) if digits else 0)
    return (2, 0) if label == UP_OF_SLEEP else (3, 0)



def open_workspace(subtitle: str, *, search_key: str, placeholder: str):
    """The header and its search, the sidebar and the analysed Daily Target.
    Stops the page before any upload. The Correlation Window chosen here is the
    one window every analysis, evidence chart and timeline of the page uses."""
    st.html(_CSS)
    st.html(CA2_CSS)
    _restore(search_key, lambda v: isinstance(v, str))
    q = (_header("Complaints", subtitle, search_key=search_key,
                 placeholder=placeholder) or "").strip()
    _remember(search_key, st.session_state.get(search_key, ""))

    with st.sidebar:
        win_box = st.expander("Time correlation", icon=":material/schedule:",
                              expanded=True)

    active = R.target()

    with win_box:
        _restore("ca_win", lambda v: v in WINDOWS)
        win_label = st.selectbox("Correlation window", list(WINDOWS), index=2, key="ca_win")
        _remember("ca_win", win_label)
        if WINDOWS[win_label] is None:
            _restore("ca_win_custom", lambda v: isinstance(v, (int, float)) and 0.25 <= v <= 24)
            window_h = float(st.number_input("Hours either side of the problem time",
                                             0.25, 24.0, 2.0, 0.25, key="ca_win_custom"))
            _remember("ca_win_custom", window_h)
            win_label = f"±{window_h:g} h"
        else:
            window_h = float(WINDOWS[win_label])

    if active is None:
        _card("Daily Target Tickets", [], icon="file",
              note="No Daily Target yet: upload the day's Target Excel once in Data "
                   "Resources → Complaint Data. It is kept until a new version is applied.")
        R.link("Open Data Resources")
        st.stop()
    try:
        return load_workspace(win_label, window_h, q)
    except TargetFormatError as exc:
        st.error(f"The stored Daily Target could not be read: {exc}")
        st.stop()


def load_workspace(win_label: str, window_h: float, q: str = ""):
    """The analysed Daily Target — what `open_workspace` shows, without its
    header or sidebar, so another page (the Sites map) reads the very same
    analysis. None when there is no Daily Target; a ticket whose user location
    was approved is re-analysed at it (`_relocate`, `relocations`)."""
    active = R.target()
    if active is None:
        return None
    kpi_files = {f.sha1: (f.name, path)
                 for _, group in R.kpi_groups() for path, _, f in group}
    cov_kept = R.coverage()

    tickets = _load_tickets(active.sha1, active)

    source = (_load_source(active.sha1, active, tuple(tickets["_row"]))
              if "_row" in tickets.columns else pd.DataFrame(index=tickets.index))
    kpi_key = tuple(sorted(kpi_files))
    tracks, inds = _load_tracks(kpi_key, kpi_files) if kpi_files else ([], [])
    analysis = _analyse(active.sha1, kpi_key, window_h, tickets, tracks)
    noc_rows = _noc_all(active.sha1, kpi_key, window_h, tickets, analysis, inds)
    # the tickets re-analysed at an approved user location replace their general
    # analysis everywhere: the table, the counts, Ticket Details and the Sites map
    re_by_i = relocations(active.sha1, tickets, kpi_key, kpi_files, tracks, window_h, cov_kept)
    if re_by_i:
        analysis, noc_rows = list(analysis), list(noc_rows)
        for i, r in re_by_i.items():
            t = tickets.iloc[i]
            analysis[i] = r.analysis
            noc_rows[i] = ticket_tiles(r.analysis, observe_indicators(
                t["site_id"] if isinstance(t["site_id"], str) else "", t["problem_local"],
                inds, window_h))

    ep_path = _ep_path()
    sites = _site_table(ep_path) if ep_path else pd.DataFrame()

    sid = tickets["site_id"].where(tickets["site_id"].notna(), "").astype(str).str.upper()
    from _kpi_bounds import r5_areas
    from _kpi_region import governorate_name, site_regions
    places = site_regions(sorted(set(sid) - {""}), sites, r5_areas())

    def _place(col: str) -> list:
        got = places[col].reindex(sid.to_numpy()) if len(places) else pd.Series(index=sid)
        return [v if isinstance(v, str) and v and v != _UNPLACED else "" for v in got]

    rsrp: dict = {}
    if cov_kept and not sites.empty and {"latitude", "longitude"} <= set(sites.columns):
        ll = {s: (float(sites.at[s, "latitude"]), float(sites.at[s, "longitude"]))
              for s in sorted(set(sid) - {""})
              if s in sites.index and pd.notna(sites.at[s, "latitude"])
              and pd.notna(sites.at[s, "longitude"])}
        ids = sorted(cov_kept)
        rsrp = _site_rsrp(tuple(ids), tuple(sorted(ll)), tuple(cov_kept[k] for k in ids), ll)

    try:
        from rfopt.geo.coverage import band_index, load_bands
        BANDS = load_bands()[0]
    except Exception:
        BANDS = []


    def _rsrp_band(area):
        if not area or not BANDS:
            return None
        return BANDS[int(band_index(np.array([area["median"]]), BANDS)[0])]


    def _col(frame: pd.DataFrame, name: str) -> pd.Series:
        return frame[name] if name in frame.columns else pd.Series([""] * len(frame), index=frame.index)


    def _info(col: str) -> pd.Series:
        if sites.empty or col not in sites.columns:
            return pd.Series([""] * len(tickets), index=tickets.index)
        return pd.Series(sites.reindex(sid)[col].to_numpy(), index=tickets.index).map(_clean)


    has_reopen = "reopen" in tickets.columns
    _types = ([ticket_type(r, u) for r, u in zip(tickets["reopen"], _col(tickets, "user"))]
              if has_reopen else [(NA, None)] * len(tickets))

    T = pd.DataFrame(index=tickets.index)
    T["Ticket ID"] = tickets["ticket_id"].astype(str)
    T["MSISDN"] = _col(tickets, "msisdn").map(_clean).replace("", NA)
    # the complaint (HPSM incident) ID: searched, never a table column of its own here
    T["_complaint"] = _col(tickets, "hpsm_id").map(_clean)
    T["Site ID"] = sid.replace("", NA)
    T["Site Name"] = _info("site_name").replace("", NA)
    _per_site = T.loc[T["Site ID"] != NA, "Site ID"].value_counts()
    T["Site Tickets"] = T["Site ID"].map(_per_site).astype("Int64")
    T["Sup District"] = [v or NA for v in _place("sup_district")]
    T["Governorate"] = [g or governorate_name(r5_governorate(c, tc)) or NA
                        for g, c, tc in zip(_place("governorate"),
                                            _col(tickets, "city").map(_clean), _info("city"))]
    T["City"] = [v or NA for v in _place("city")]
    T["Engineer"] = _col(tickets, "engineer").map(_eng_name)
    T["Problem Time"] = tickets["problem_local"]
    T["Ticket Type"] = [type_label(k, num) if k in TYPES else NA for k, num in _types]
    T["Problem Type"] = _col(tickets, "affected_service").map(_clean).replace("", NA)
    T["KPI (window)"] = [a.kpi_summary or "–" for a in analysis]
    T["RSRP"] = [f"{rsrp[s]['median']:.0f} dBm (site area)" if s in rsrp else NA for s in sid]
    for i, r in re_by_i.items():
        T.iat[i, T.columns.get_loc("RSRP")] = (f"{r.rsrp:.1f} dBm (user location)"
                                               if r.rsrp is not None else NA)
    # the serving sector: the one at the approved user location, else the
    # sector of the worst cell the general analysis found
    T["Serving Sector"] = [
        (re_by_i[i].sector_id or NA) if i in re_by_i else (general_sector(s, a) or NA)
        for i, (s, a) in enumerate(zip(sid, analysis))]
    T["Distance"] = [fmt_metres(re_by_i[i].server.distance_m)
                     if i in re_by_i and re_by_i[i].server else NA for i in range(len(T))]
    T["Description"] = [re_by_i[i].description if i in re_by_i else a.site_issue
                        for i, a in enumerate(analysis)]
    T["User Location"] = [f"{re_by_i[i].lat:.5f}, {re_by_i[i].lon:.5f}" if i in re_by_i else NA
                          for i in range(len(T))]
    T["Network Analysis"] = [a.classification for a in analysis]
    T["Problem Detected"] = [a.problem_detected for a in analysis]
    T["Problem"] = [a.problem_type or "–" for a in analysis]
    T["Site Issue"] = [a.site_issue for a in analysis]
    T["Resolution"] = [a.resolution for a in analysis]
    T["Confidence"] = [a.confidence or "–" for a in analysis]
    T["SLA"] = _col(tickets, "sla_status").map(_clean).replace("", NA)
    # a delay ticket: its SLA is violated — the only delay the file records
    T["Delay"] = ([NA if v == NA else DELAYED if "violat" in v.lower() else ON_TIME
                   for v in T["SLA"]] if "sla_status" in tickets.columns else NA)
    # the KPIs above threshold (or detected) around the ticket, as its tiles show them
    T["_kpis"] = [sorted({t.name for t in list(p) + list(s_) if t.sev > 0})
                  for p, s_ in noc_rows]
    T["KPI Issues"] = [", ".join(k) if k else "—" for k in T["_kpis"]]
    T["User"] = _col(tickets, "user").map(_clean).replace("", NA)
    T["_tt"] = [k for k, _ in _types]
    T["_reopen"] = pd.Series([num for _, num in _types], index=T.index, dtype=object)
    T["_i"] = range(len(T))
    n = len(T)

    def _type_order(label: str) -> tuple:
        return type_order(label)

    TT_OPTIONS = sorted(set(T["Ticket Type"]), key=_type_order)
    TT_COLOURS = [TYPE_COLOUR[REOPEN] if o.startswith(REOPEN) else TYPE_COLOUR.get(o, PALETTE["nodata"])
                  for o in TT_OPTIONS]
    SITE_NAMES = T[T["Site ID"] != NA].groupby("Site ID")["Site Name"].first()

    return SimpleNamespace(
        q=q, active=active, tickets=tickets, source=source, kept=kpi_files, tracks=tracks, inds=inds,
        analysis=analysis, noc_rows=noc_rows, sites=sites, rsrp=rsrp, cov_kept=cov_kept,
        win_label=win_label, window_h=window_h, bands=BANDS, T=T, n=n,
        has_reopen=has_reopen, per_site=_per_site, site_names=SITE_NAMES,
        tt_options=TT_OPTIONS, tt_colours=TT_COLOURS, re=re_by_i)


def general_sector(site: str, analysis) -> str:
    """The sector of the worst cell behind a general (site-level) analysis:
    the lead KPI's worst cell, named as the hourly loader names sectors."""
    from rfopt.complaints.relocate import lead_check, sector_of
    lead = lead_check(analysis)
    return sector_of(site, lead.worst_obj) if lead is not None and site and site != NA else ""


def relocations(sha: str, tickets, kpi_key: tuple, kpi_files: dict, tracks,
                window_h: float, cov_kept: dict) -> dict:
    """Row -> the re-analysis at the approved user location, for every ticket
    of the Daily Target that has one (`_relocate`)."""
    import _relocate as RL
    saved = RL.load()
    if not saved:
        return {}
    ids = tickets["ticket_id"].astype(str).tolist()
    rows = {i: saved[t] for i, t in enumerate(ids) if t in saved}
    if not rows:
        return {}
    kmz, ep = R.kmz_file(), R.ep_file()
    data_key = (sha, kmz.sha1 if kmz else "", ep.sha1 if ep else "")
    return _relocated(tuple(sorted((ids[i], v["lat"], v["lon"]) for i, v in rows.items())),
                      data_key, kpi_key, float(window_h), tuple(sorted(cov_kept)), tickets,
                      rows, kpi_files, tracks, cov_kept)


@st.cache_resource(show_spinner=False, max_entries=4)
def _relocated(sig: tuple, data_key: tuple, kpi_key: tuple, window_h: float, cov_key: tuple,
               _tickets, _rows: dict, _kpi_files: dict, _tracks, _cov: dict) -> dict:
    import _relocate as RL
    from rfopt.complaints.relocate import reanalyse
    sec_tracks = _load_sector_tracks(kpi_key, _kpi_files) if _kpi_files else []
    sectors = RL.serving_sectors()
    grids = [_cov[k] for k in cov_key]
    return {i: reanalyse(float(v["lat"]), float(v["lon"]), _tickets.iloc[i]["problem_local"],
                         sectors, sec_tracks, _tracks, window_h, grids)
            for i, v in _rows.items()}


CA2_CSS = """
<style>
.ca-tl-k { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; margin: 2px 0 2px; font-size: 11.5px; }
.ca-tl-k span { color: #94A3B8; font-weight: 600; margin-right: 2px; }
.ca-tl-k em { font-style: normal; background: #071525; border: 1px solid #1E3A5F; border-radius: 6px;
    padding: 1px 7px; color: #E2E8F0; font-weight: 600; }
.ca-tl-how { font-size: 10.5px; color: #64748B; margin-top: 6px; line-height: 1.45; }
.ca-tl-peak { margin-top: 6px; font-size: 11.5px; color: #CBD5E1; border-left: 3px solid var(--c);
    padding: 3px 8px; background: #071525; border-radius: 6px; }
.ca-tl-peak b { color: var(--c); }
.ca-tl-peakg { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px;
    margin-top: 8px; }
.ca-tl-peakg div { background: #071525; border: 1px solid #1E3A5F; border-top: 2px solid var(--c);
    border-radius: 8px; padding: 6px 9px; min-width: 0; }
.ca-tl-peakg span { display: block; font-size: 10.5px; color: #94A3B8; }
.ca-tl-peakg b { display: block; font-size: 13px; color: #F1F5F9; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap; }
.ca-tl-peakg b small { color: #94A3B8; font-weight: 500; font-size: 10.5px; }
.ca-tl-peak small { color: #64748B; }
.ca-dn { display: flex; align-items: center; gap: 12px; }
.ca-dn .ca-donut { flex: 0 0 96px; height: 96px; }
.ca-dn .ca-leg { gap: 6px; }
.ca-dn .ca-leg div { grid-template-columns: 10px 1fr auto 38px; font-size: 12px; }
.ca-dn .ca-leg span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ca-ws-top { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.ca-ws-id { font-size: 18px; font-weight: 800; color: #F8FAFC; }
.ca-ws-sub { color: #94A3B8; font-size: 12px; }
.ca-ws-note { color: #64748B; font-size: 11px; margin-top: 6px; }
.ca-evk { display: flex; gap: 12px; align-items: flex-start; }
.ca-evk-ico { flex: 0 0 48px; height: 48px; border-radius: 50%; display: flex; align-items: center;
    justify-content: center; background: color-mix(in srgb, var(--k) 18%, #071525);
    box-shadow: 0 0 18px color-mix(in srgb, var(--k) 35%, transparent); }
.ca-evk-t { font-size: 14px; font-weight: 700; color: #F1F5F9; line-height: 1.3; }
.ca-evk-a { font-size: 12px; color: #CBD5E1; line-height: 1.55; margin-top: 6px; }
.ca-evs { background: #071525; border: 1px solid color-mix(in srgb, var(--c) 60%, #1E3A5F);
    border-radius: 10px; padding: 10px 12px; }
.ca-evs-h { color: var(--c); font-weight: 700; font-size: 13px; }
.ca-evs-v { font-size: 24px; font-weight: 800; color: #F8FAFC; margin: 3px 0 2px;
    font-variant-numeric: tabular-nums; }
.ca-evs-th { font-size: 11.5px; color: #CBD5E1; }
.ca-evs-p { font-size: 12px; color: #E2E8F0; margin-top: 4px; }
.ca-evs-n { font-size: 11px; color: #94A3B8; margin-top: 6px; line-height: 1.45; }
.ca-rsc { position: relative; padding: 26px 4px 2px; }
.ca-rsc-bar { position: relative; height: 12px; border-radius: 6px; overflow: hidden; background: #16324F; }
.ca-rsc-bar i { position: absolute; top: 0; bottom: 0; }
.ca-rsc-m { position: absolute; top: 4px; bottom: 30px; width: 0; border-left: 2px dashed #F8FAFC; z-index: 1; }
.ca-rsc-m b { position: absolute; top: -4px; left: 6px; font-size: 12px; color: #F8FAFC; white-space: nowrap; }
.ca-rsc-ax { display: flex; justify-content: space-between; font-size: 11px; color: #94A3B8; margin-top: 6px; }
.ca-rsc-ax span:last-child { text-align: right; }
@media (max-width: 900px) { .ca-evk-ico { flex-basis: 36px; height: 36px; } }
</style>
"""

# what each evidence KPI measures, in a line
KPI_ABOUT = {
    "cell_avail_pct": "How much of each hour the cell was available; a drop means an outage.",
    "ul_rssi_dbm": "Uplink interference level; a high level degrades access and uplink quality.",
    "dl_prb_util": "Downlink PRB usage; a high share means congestion.",
    "ul_prb_util": "Uplink PRB usage; a high share means uplink congestion.",
    "call_setup_sr": "Call and session setup success; a low rate means failed access.",
    "erab_drop_rate": "Share of dropped connections; a high rate means retainability loss.",
    "dl_user_thr_mbps": "Downlink user throughput; a low rate means slow data.",
    "ul_user_thr_mbps": "Uplink user throughput; a low rate means slow uploads.",
    "ho_sr": "Handover success; a low rate means mobility failures.",
    "ipmm_rtt_ms": "IP path round-trip time; a high value means transport latency.",
    "FLOW": "3G downlink flow-control drops; judged on their 24 h total.",
    "RTWP": "3G received total wideband power; a high level means uplink interference.",
    "S1": "4G S1 signalling failures; counted as context, not judged.",
}


def donut_html(title: str, icon: str, parts, total: int, subtitle: str = "",
               centre: str = "TICKETS") -> str:
    """parts: (label, count, colour). A donut and its legend, one share per part."""
    stops, acc, legend = [], 0.0, ""
    for label, k, colour in parts:
        share = 100.0 * k / total if total else 0.0
        stops.append(f"{colour} {acc:.2f}% {acc + share:.2f}%")
        acc += share
        legend += (f'<div style="--c:{colour}" title="{_esc(label)}: {k:,} tickets">'
                   f'<i></i><span>{_esc(label)}</span><b>{k:,}</b><small>{share:.0f}%</small></div>')
    grad = ", ".join(stops) if total else f"{PALETTE['nodata']} 0% 100%"
    return (_title_html(title, icon, subtitle=subtitle)
            + f'<div class="ca-dn"><div class="ca-donut" style="--g:{grad}">'
              f'<div class="ca-donut-c"><b>{total:,}</b><span>{_esc(centre)}</span></div></div>'
              f'<div class="ca-leg">{legend}</div></div>')


def evidence_items(ctx, sel) -> list[dict]:
    """The KPIs behind a ticket's evidence: the judged KPIs above threshold in the
    Correlation Window, then the context indicators that saw something there.
    Each carries its hourly series over the whole period the exports cover and
    inside the window. Values are the site's worst cell per hour for a level, the
    site's total per hour for a count."""
    site, pt = sel["Site ID"], sel["Problem Time"]
    i = int(sel["_i"])
    re_ = getattr(ctx, "re", {}).get(i)
    # a ticket re-analysed at the user location: its serving sector's own KPIs
    tracks, key = (re_.tracks, re_.key) if re_ is not None else (ctx.tracks, site)
    if (key == NA or not key) or pd.isna(pt):
        return []
    a = ctx.analysis[i]
    items = []
    for c in sorted([c for c in a.checks if c.sev > 0], key=lambda c: (-c.sev, -c.breach_hours)):
        tr = next((t for t in tracks if t.label == c.label and t.source == c.source
                   and key in t.by_site), None)
        if tr is None:
            continue
        times, vals, _ = tr.by_site[key]
        wt, wv, lo, hi = window_series(times, vals, pt, ctx.window_h)
        all_v = np.asarray(vals, dtype=float)
        items.append(dict(key=c.canon, tag=_CANON_TILE.get(c.canon, c.label), name=c.label,
                          unit=c.unit, times=wt, values=wv, all_times=pd.DatetimeIndex(times),
                          all_values=all_v, all_sev=[severity(tr.rule, v) for v in all_v],
                          sev=[severity(tr.rule, v) for v in wv], rule=tr.rule, lo=lo, hi=hi,
                          pt=pt, judged=True, level=True, state=c.status, state_sev=c.sev,
                          value=fmt(c.worst, c.unit), at=c.worst_at, obj=c.worst_obj,
                          threshold=c.threshold, span=c.breach_span, hours=c.hours,
                          resolution=c.resolution, resolution_note=c.resolution_note,
                          before=c.before, note=""))
    for tr, ic in zip(ctx.inds, observe_indicators(site, pt, ctx.inds, ctx.window_h)):
        if site == NA or ic.sev <= 0 or site not in tr.by_site:
            continue
        times, vals = tr.by_site[site]
        wt, wv, lo, hi = window_series(times, vals, pt, ctx.window_h)
        how, rule = tr.defn.how, tr.rule

        def states(vs, how=how, rule=rule):
            return ([severity(rule, v) for v in vs] if how == "worst"
                    else [1 if v > 0 else 0 for v in vs] if how == "count" else [None] * len(vs))

        all_v = np.asarray(vals, dtype=float)
        items.append(dict(key=ic.key, tag=ic.key, name=ic.label, unit=ic.unit, times=wt,
                          values=wv, sev=states(wv), all_times=pd.DatetimeIndex(times),
                          all_values=all_v, all_sev=states(all_v),
                          rule=tr.rule if how == "worst" else None, lo=lo, hi=hi, pt=pt,
                          judged=False, level=how == "worst", state=ic.state, state_sev=ic.sev,
                          value=ic.value_text, at=ic.at, obj="", threshold=ic.threshold,
                          span=ic.observed, hours=ic.hours, resolution="", resolution_note="",
                          before="", note=ic.note))
    return items


# how each KPI is drawn: a utilisation as bars, a drop count as stems, a level
# as a line (3G smoothed), an availability or a success rate as steps, a
# failure count as an area, a latency as dots
CHART_STYLE = {"PRB": "bars", "FLOW": "stems", "INTER": "line", "RTWP": "smooth",
               "AVA": "step", "CSSR": "step", "S1": "area", "IPL": "dots"}
KPI_COLOUR = {"PRB": "#1597FF", "FLOW": "#FACC15", "INTER": "#FB923C", "RTWP": "#A78BFA",
              "AVA": "#2DD4BF", "CSSR": "#20BFFF", "S1": "#22C55E", "IPL": "#F472B6"}
KPI_ICON = {"PRB": "chart", "FLOW": "pulse", "INTER": "tower", "RTWP": "up", "AVA": "check",
            "CSSR": "target", "S1": "signal", "IPL": "clock"}


def _rgba(hex_colour: str, alpha: float) -> str:
    h = hex_colour.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


def evidence_figure(item, win_label: str = ""):
    """One evidence KPI over the whole period the exports cover, the Correlation
    Window shaded, the thresholds and the problem time marked. Hours above
    threshold take their state's colour; the chart type follows the KPI."""
    import plotly.graph_objects as go

    hour = pd.Timedelta(hours=1)
    tag, unit = item["tag"], item["unit"]
    style = CHART_STYLE.get(tag, "line")
    colour = KPI_COLOUR.get(tag, PALETTE["cyan"])
    t, v = pd.DatetimeIndex(item["all_times"]), np.asarray(item["all_values"], dtype=float)
    points = [SEV_COLOUR[x] if x is not None and x > 0 else colour for x in item["all_sev"]]
    hover = "%{x|%d %b %H:%M}<br>%{y:,.2f}" + unit + "<extra></extra>"
    fig = go.Figure()
    if style == "bars":
        fig.add_bar(x=t + hour / 2, y=v, marker_color=points, marker_line_width=0,
                    width=0.72 * hour.total_seconds() * 1000, hovertemplate=hover)
    elif style == "stems":
        fig.add_bar(x=t, y=v, marker_color=points, marker_line_width=0, hoverinfo="skip",
                    width=0.14 * hour.total_seconds() * 1000)
        fig.add_scatter(x=t, y=v, mode="markers", hovertemplate=hover,
                        marker=dict(size=6, color=points))
    elif style == "smooth":
        fig.add_scatter(x=t, y=v, mode="lines", hovertemplate=hover,
                        line=dict(shape="spline", smoothing=0.8, width=2.2, color=colour))
        bad = [k for k, x in enumerate(item["all_sev"]) if x is not None and x > 0]
        if bad:
            fig.add_scatter(x=t[bad], y=v[bad], mode="markers", hovertemplate=hover,
                            marker=dict(size=7, color=[points[k] for k in bad]))
    elif style == "step":
        fig.add_scatter(x=t, y=v, mode="lines", line=dict(shape="hv", width=2, color=colour),
                        fill="tozeroy", fillcolor=_rgba(colour, 0.12), hovertemplate=hover)
        bad = [k for k, x in enumerate(item["all_sev"]) if x is not None and x > 0]
        if bad:
            fig.add_scatter(x=t[bad], y=v[bad], mode="markers", hovertemplate=hover,
                            marker=dict(size=7, color=[points[k] for k in bad]))
    elif style == "area":
        fig.add_scatter(x=t, y=v, mode="lines+markers", fill="tozeroy",
                        fillcolor=_rgba(colour, 0.16), hovertemplate=hover,
                        line=dict(shape="spline", smoothing=0.6, width=2, color=colour),
                        marker=dict(size=5, color=points))
    elif style == "dots":
        fig.add_scatter(x=t, y=v, mode="lines+markers", hovertemplate=hover,
                        line=dict(width=1, color=_rgba(colour, 0.35), dash="dot"),
                        marker=dict(size=7, color=points, line=dict(width=0)))
    else:
        fig.add_scatter(x=t, y=v, mode="lines+markers", hovertemplate=hover,
                        line=dict(width=2, color=colour), marker=dict(size=5, color=points))
    start = min(t[0], item["lo"]) if len(t) else item["lo"]
    end = max(t[-1] + hour, item["hi"]) if len(t) else item["hi"]
    # labels go on the side away from the window, so they never cover it or clip
    right = (item["lo"] + (item["hi"] - item["lo"]) / 2 - start) > (end - start) / 2
    fig.add_vrect(x0=item["lo"], x1=item["hi"], fillcolor="rgba(32,191,255,0.14)",
                  line=dict(width=1, color="rgba(32,191,255,0.7)", dash="dot"), layer="below")
    fig.add_annotation(x=item["lo"] if right else item["hi"], yref="paper", y=1.0,
                       xanchor="right" if right else "left", yanchor="bottom", showarrow=False,
                       text=f"Correlation window {win_label} · problem {item['pt']:%H:%M}",
                       font=dict(size=9, color=PALETTE["cyan"]))
    rule = item["rule"]
    if rule is not None:
        for level, line_colour, glyph in ((rule.warning, PALETTE["warning"], "⚠"),
                                          (rule.critical, PALETTE["critical"], "●")):
            fig.add_hline(y=level, line_dash="dash", line_width=1, line_color=line_colour,
                          annotation_text=f"{glyph} {level:g}{unit}",
                          annotation_font=dict(size=9, color=line_colour),
                          annotation_position="bottom left" if right else "bottom right")
    fig.add_vline(x=item["pt"], line_dash="dot", line_width=1.2, line_color="#F8FAFC")
    fig.update_layout(height=210, margin=dict(l=4, r=8, t=18, b=4), showlegend=False,
                      plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                      font=dict(size=10, color="#CBD5E1"), bargap=0.15, hovermode="x")
    fig.update_xaxes(range=[start, end], showgrid=False, linecolor="#2A4A6F", nticks=7,
                     tickformat="%d %b<br>%H:%M")
    fig.update_yaxes(gridcolor="#16324F", zeroline=False,
                     title=dict(text=unit.strip() or "value", font=dict(size=9)))
    return fig


def evidence_about_html(item) -> str:
    """The KPI, and what it measures."""
    colour = KPI_COLOUR.get(item["tag"], PALETTE["cyan"])
    return (f'<div class="ca-evk" style="--k:{colour}">'
            f'<span class="ca-evk-ico">{_icon(KPI_ICON.get(item["tag"], "chart"), colour, 24)}</span>'
            f'<div><div class="ca-evk-t">{_esc(item["tag"])} - {_esc(item["name"])}</div>'
            f'<div class="ca-evk-a">{_esc(KPI_ABOUT.get(item["key"], ""))}</div></div></div>')


def evidence_status_html(item, win_label: str) -> str:
    """The finding in the Correlation Window, beside the chart."""
    sev = item["state_sev"]
    colour = SEV_COLOUR.get(sev, PALETTE["nodata"])
    rule, unit = item["rule"], item["unit"]
    if rule is not None:
        op = "<" if rule.direction == "up" else ">"
        limits = f"⚠ {op} {rule.warning:g}{unit} · ● {op} {rule.critical:g}{unit}"
    else:
        limits = item["threshold"] or "—"
    when = f"{item['at']:%d %b %H:%M}" if item["at"] is not None and pd.notna(item["at"]) else ""
    lines = [item["span"] or when]
    if item["obj"]:
        lines.append(f"{item['obj']}" + (f" · {when}" if when else ""))
    note = " · ".join(x for x in (
        f"{item['resolution']} — {item['resolution_note']}" if item["resolution"]
        else f"{item['note']} · context, not in the classification" if not item["judged"] else "",
        f"before: {item['before']}" if item["before"] else "") if x)
    period = (f"window {item['lo']:%d %b %H:%M} → {item['hi']:%H:%M} ({win_label}) shaded · "
              "chart: all loaded hours")
    return (f'<div class="ca-evs" style="--c:{colour}">'
            f'<div class="ca-evs-h">{GLYPH.get(sev, "")} {_esc(item["state"])}</div>'
            f'<div class="ca-evs-v">{_esc(item["value"])}</div>'
            f'<div class="ca-evs-th">{_esc(limits)}</div>'
            + "".join(f'<div class="ca-evs-p">{_esc(x)}</div>' for x in lines if x)
            + (f'<div class="ca-evs-n">{_esc(note)}</div>' if note else "")
            + f'<div class="ca-evs-n">{_esc(period)}</div>'
            + "</div>")


def rsrp_row(area, bands, cov_loaded: bool) -> tuple[str, str, str]:
    """RSRP as an evidence row: what it is, the band scale with the site area's
    value on it, and the finding."""
    about = (f'<div class="ca-evk" style="--k:{PALETTE["cyan"]}">'
             f'<span class="ca-evk-ico">{_icon("signal", PALETTE["cyan"], 24)}</span>'
             '<div><div class="ca-evk-t">RSRP</div><div class="ca-evk-a">Site area RSRP<br>'
             "Customer location<br>Sector / distance</div></div></div>")
    lo, hi = -120.0, -70.0
    value = _area_value(area, "median")
    if value is not None:
        lo, hi = min(lo, value - 5), max(hi, value + 5)

    def pos(x: float) -> float:
        return max(0.0, min(100.0, 100.0 * (x - lo) / (hi - lo)))

    segs, top = "", hi
    for b in bands or []:
        floor = b.lo if b.lo is not None else lo
        if floor < top:
            segs += (f'<i style="left:{pos(floor):.2f}%;width:{pos(top) - pos(floor):.2f}%;'
                     f'background:{b.colour}" title="{_esc(b.label)} {_esc(b.text)}"></i>')
        top = min(top, floor)
    marker = (f'<span class="ca-rsc-m" style="left:{pos(value):.2f}%"><b>{value:.0f} dBm</b></span>'
              if value is not None else "")
    scale = (f'<div class="ca-rsc">{marker}<div class="ca-rsc-bar">{segs}</div>'
             f'<div class="ca-rsc-ax"><span>{lo:g} dBm<br>(Worst)</span>'
             f'<span>{hi:g} dBm<br>(Best)</span></div></div>')
    band = rsrp_band(area, bands)
    if value is not None and band is not None:
        colour = band.colour
        radius = _area_value(area, "radius_m")
        weak_pct, weak_dbm = _area_value(area, "weak_pct"), _area_value(area, "weak_dbm")
        grids = _area_value(area, "grids")
        where = (f"site area ≤{radius:.0f} m · MR-weighted median" if radius is not None
                 else "MR-weighted median")
        detail = " · ".join(x for x in (
            f"{weak_pct:.1f}% of MRs below {weak_dbm:g} dBm"
            if weak_pct is not None and weak_dbm is not None else "",
            f"{grids:,} grids" if grids is not None else "") if x)
        body = (f'<div class="ca-evs-h">{_esc(band.label)}</div>'
                f'<div class="ca-evs-v">{value:.1f} dBm</div>'
                f'<div class="ca-evs-p">{_esc(where)}</div>'
                + (f'<div class="ca-evs-n">{_esc(detail)}</div>' if detail else ""))
    else:
        colour = PALETTE["nodata"]
        body = ('<div class="ca-evs-h">— No data</div><div class="ca-evs-p">'
                + ("No coverage grids near the site" if cov_loaded
                   else "No coverage grid in Coverage Data (Data Resources)") + "</div>")
    body += ('<div class="ca-evs-n">Customer location: not available — the Daily Target has '
             "no coordinates. Sector / distance: not available.</div>")
    return about, scale, f'<div class="ca-evs" style="--c:{colour}">{body}</div>'


def _canonical_sources(columns) -> set:
    """The original columns the ticket reader maps to a field of its own."""
    from rfopt.complaints.worklist import _COL, _norm
    norms = {c: _norm(c) for c in columns}
    used = set()
    for cands in _COL.values():
        hit = next((c for c, n in norms.items() if n in cands), None)
        if hit is None:
            hit = next((c for c, n in norms.items() if any(k in n for k in cands)), None)
        if hit is not None:
            used.add(hit)
    return used


def ticket_info_rows(ctx, sel) -> list:
    """The Ticket Information fields of one ticket, as Ticket Details shows
    them — the Sites map's ticket panel reads the same list."""
    i = int(sel["_i"])
    raw = ctx.tickets.iloc[i]
    have = set(ctx.tickets.columns)
    pt_txt = f"{sel['Problem Time']:%d %b %Y %H:%M}" if pd.notna(sel["Problem Time"]) else NA

    def col(name: str, fmt_=None):
        if name not in have:
            return None
        v = raw.get(name)
        if fmt_ is not None:
            return fmt_(v)
        return _clean(v) or "—"

    def when(v) -> str:
        if v is None or pd.isna(v):
            return "—"
        t = local_time(pd.Series([v])).iloc[0]
        return f"{t:%d %b %Y %H:%M}" if pd.notna(t) else "—"

    return [(k, v) for k, v in [
        ("Ticket ID", sel["Ticket ID"]), ("Complaint (HPSM) ID", col("hpsm_id")),
        ("Ticket type", sel["Ticket Type"]), ("Reopen count", col("reopen")),
        ("Ticket type in the file", col("ticket_kind")),
        ("SLA status", col("sla_status")), ("Delay", sel["Delay"] if sel["Delay"] != NA else None),
        ("SLA target time", col("sla_target")), ("Created at", col("create_time", when)),
        ("Problem time", pt_txt), ("Opened by", col("opened_by")), ("User", col("user")),
        ("IS CMC", col("is_cmc")), ("Affected service", col("affected_service"))]
        if v is not None]


def render_ticket(ctx, sel) -> None:
    """One ticket from beginning to end: its record (ticket, complainant,
    network, technical), then the NOC analysis — incident summary, KPI tiles,
    KPI evidence charts over the whole period with the Correlation Window shaded,
    RSRP on its band scale, the KPI timeline, the
    resolution and the final analysis. Only fields the data carries are shown."""
    i = int(sel["_i"])
    a = ctx.analysis[i]
    raw = ctx.tickets.iloc[i]
    src = ctx.source.iloc[i] if len(ctx.source.columns) else pd.Series(dtype=object)
    area = ctx.rsrp.get(sel["Site ID"])
    band = rsrp_band(area, ctx.bands)
    primary, secondary = ctx.noc_rows[i]
    site = sel["Site ID"] if sel["Site ID"] != NA else ""
    lead_checks = sorted([c for c in a.checks if c.sev > 0], key=lambda c: (-c.sev, -c.breach_hours))
    lead = lead_checks[0] if lead_checks else None
    pt_txt = f"{sel['Problem Time']:%d %b %Y %H:%M}" if pd.notna(sel["Problem Time"]) else NA
    have = set(ctx.tickets.columns)

    def col(name: str, fmt_=None):
        if name not in have:
            return None
        v = raw.get(name)
        if fmt_ is not None:
            return fmt_(v)
        return _clean(v) or "—"

    def when(v) -> str:
        if v is None or pd.isna(v):
            return "—"
        t = local_time(pd.Series([v])).iloc[0]
        return f"{t:%d %b %Y %H:%M}" if pd.notna(t) else "—"

    def rows(spec):
        return [(k, v) for k, v in spec if v is not None]

    with st.container(key="rf_card_ca_ws_head", border=True):
        st.html('<div class="ca-ws-top">'
                f'<span class="ca-ws-id">{_esc(sel["Ticket ID"])}</span>'
                f'{_badge(a.classification, CLASS_COLOUR[a.classification])}'
                + (_badge(sel["Delay"], PALETTE["critical"] if sel["Delay"] == DELAYED
                          else PALETTE["excellent"]) if sel["Delay"] != NA else "")
                + f'<span class="ca-ws-sub">{_esc(sel["Ticket Type"])} · {_esc(sel["Site ID"])} · '
                  f'{_esc(sel["Governorate"])} · problem {_esc(pt_txt)} · {_esc(ctx.active.name)}'
                  "</span></div>")

    ticket_rows = ticket_info_rows(ctx, sel)
    complainant_rows = rows([("MSISDN", col("msisdn"))])
    cells = site_cells(site) if site else ""
    info = ctx.sites.loc[site] if site and site in ctx.sites.index else None
    network_rows = rows([
        ("Site ID", sel["Site ID"]), ("Site name", sel["Site Name"]),
        ("Cells on site (EP tracker)", cells or None),
        ("Sup District", sel["Sup District"]), ("Sub District (file)", col("sub_district")),
        ("District (file)", col("district")), ("City (file)", col("city")),
        ("Governorate", sel["Governorate"]),
        ("Site tickets in the Daily Target",
         f"{int(sel['Site Tickets'])}" if pd.notna(sel["Site Tickets"]) else None),
        ("Site position", f"{float(info['latitude']):.5f}, {float(info['longitude']):.5f}"
         if info is not None and pd.notna(info.get("latitude")) and pd.notna(info.get("longitude"))
         else None)])
    technical_rows = [
        ("Network analysis", _badge(a.classification, CLASS_COLOUR[a.classification])),
        ("KPI", _esc(lead.label) if lead else _esc("—")),
        ("Issue", _esc(a.problem_type or "—")),
        ("KPI value", _esc(f"{fmt(lead.worst, lead.unit)} at {lead.worst_at:%d %b %H:%M}"
                           + (f" on {lead.worst_obj}" if lead.worst_obj else "")) if lead else "—"),
        ("Site / network analysis", _esc(a.site_issue)),
        ("Resolution", _esc(a.resolution)), ("Confidence", _esc(a.confidence or "—")),
        ("Evidence", _esc(a.evidence or "—"))]
    re_ = ctx.re.get(i)
    if re_ is not None:
        # approved on the Sites map: the result at the subscriber's location
        technical_rows[:0] = [
            ("User location", _esc(f"{re_.lat:.5f}, {re_.lon:.5f} (approved)")),
            ("Serving sector", _esc(sel["Serving Sector"])),
            ("Distance", _esc(sel["Distance"])),
            ("Azimuth difference",
             _esc(f"{re_.server.az_diff_deg:.0f}°" if re_.server else "—")),
            ("RSRP", _esc(sel["RSRP"])),
            ("Description", _esc(re_.description))]
    if a.resolution_evidence:
        technical_rows.append(("Resolution evidence", _esc(a.resolution_evidence)))
    if "comment" in have:
        technical_rows.append(("Comments", _esc(_clean(raw.get("comment")) or "—")))
    technical_rows.append(("Engineering", _esc(sel["Engineer"])))

    def kv_html(pairs) -> str:
        return '<div class="ca-kv">' + "".join(f"<span>{_esc(k)}</span><b>{v}</b>"
                                               for k, v in pairs) + "</div>"

    c1, c2 = st.columns(2, gap="small")
    with c1:
        with st.container(key="rf_card_ca_ws_ticket", border=True):
            st.html(_title_html("Ticket Information", "ticket") + _kv(ticket_rows)
                    + '<div class="ca-ws-note">The Daily Target carries no ticket status, '
                      "priority or closure time.</div>")
        with st.container(key="rf_card_ca_ws_complainant", border=True):
            st.html(_title_html("Complainant Information", "user")
                    + (_kv(complainant_rows) if complainant_rows else "")
                    + '<div class="ca-ws-note">No complainant name or contact is in the '
                      "file.</div>")
    with c2:
        with st.container(key="rf_card_ca_ws_network", border=True):
            st.html(_title_html("Network Information", "tower") + _kv(network_rows)
                    + '<div class="ca-ws-note">A ticket names its site, not a cell or a '
                      "technology.</div>")
        with st.container(key="rf_card_ca_ws_technical", border=True):
            st.html(_title_html("Technical Information", "pulse",
                                subtitle=f"window {ctx.win_label}") + kv_html(technical_rows)
                    + '<div class="ca-ws-note">No root cause, action taken or closure code is '
                      "in the file.</div>")
    extra = [c for c in ctx.source.columns if c not in _canonical_sources(ctx.source.columns)]
    if extra:
        with st.container(key="rf_card_ca_ws_extra", border=True):
            st.html(_title_html("Other fields in the file", "file")
                    + _kv([(c, _clean(src.get(c)) or "—") for c in extra]))

    with st.container(key="rf_card_ca_ws_noc", border=True):
        st.html('<div class="ca-det">'
                + _sec("Ticket type", "reopen")
                + _type_html(sel["_tt"], sel["_reopen"], sel["User"], pt_txt)
                + _sec("Network incident summary", "pulse")
                + _incident_html(a, sel["Site ID"], lead, band)
                + _sec("Primary KPIs", "chart", f"window {ctx.win_label}")
                + '<div class="ca-tiles">' + "".join(_tile_html(t) for t in primary) + "</div>"
                + (_sec("Secondary KPIs", "layers") + '<div class="ca-s2w">'
                   + "".join(_secondary_html(t) for t in secondary) + "</div>" if secondary else "")
                + _sec("KPI Evidence", "alert", f"whole period · correlation window "
                                               f"{ctx.win_label} shaded")
                + "</div>")
        items = evidence_items(ctx, sel)
        if not items:
            st.html('<div class="ca-note">No KPI above its threshold, and no context indicator '
                    "detected, in the correlation window.</div>")
        for k, item in enumerate(items):
            with st.container(key=f"rf_card_ca_ev_{k}", border=True):
                ca, cc, cd = st.columns([0.95, 2.6, 1.25], gap="small", vertical_alignment="center")
                ca.html(evidence_about_html(item))
                with cc:
                    st.plotly_chart(evidence_figure(item, ctx.win_label), key=f"ca_ev_chart_{k}",
                                    config={"displayModeBar": False}, width="stretch")
                cd.html(evidence_status_html(item, ctx.win_label))
        with st.container(key="rf_card_ca_ev_rsrp", border=True):
            about, scale, status = rsrp_row(area, ctx.bands, bool(ctx.cov_kept))
            ra, rc, rd = st.columns([0.95, 2.6, 1.25], gap="small", vertical_alignment="center")
            ra.html(about)
            rc.html(scale)
            rd.html(status)
        # the timeline is about one KPI, named: the lead evidence KPI unless another
        # of the site's judged KPIs is picked — never several folded into one bar
        site_kpis = [tr.label for tr in ctx.tracks if site and site in tr.by_site]
        site_kpis = list(dict.fromkeys(site_kpis))
        first = lead.label if lead is not None and lead.label in site_kpis else (
            site_kpis[0] if site_kpis else None)
        c_t, c_k = st.columns([2.2, 1.2], gap="small", vertical_alignment="center")
        c_t.html('<div class="ca-det">'
                 + _sec("KPI Timeline", "clock", "one KPI, hour by hour") + "</div>")
        tl_kpi = None
        if site_kpis:
            tl_kpi = c_k.selectbox("Timeline KPI", site_kpis, index=site_kpis.index(first),
                                   key=f"ca_tl_kpi_{sel['Ticket ID']}",
                                   label_visibility="collapsed",
                                   help="The KPI the timeline shows; the lead evidence KPI "
                                        "first")
        tl = (site_timeline(site, sel["Problem Time"], ctx.tracks, ctx.window_h, kpi=tl_kpi)
              if tl_kpi else None)
        st.html('<div class="ca-det">'
                + (_timeline_html(tl, ctx.win_label, a.window) if tl is not None
                   else '<div class="ca-note">No KPI hours for this site around the problem time.</div>')
                + _sec("Resolution", "check") + _resolution_html(a, lead_checks)
                + _sec("Final analysis", "target") + _final_html(a, lead, band)
                + "</div>")


@st.cache_resource(show_spinner=False, max_entries=2)
def _cells_by_site(path: str) -> dict:
    """Per site, its cells per technology in the EP tracker: "4G 12 · 3G 4 · 2G 8"."""
    ep = load_ep_all(path)
    if ep is None or ep.empty or not {"site_id", "technology"} <= set(ep.columns):
        return {}
    names = {"LTE": "4G", "UMTS": "3G", "GSM": "2G"}
    d = pd.DataFrame({"site": ep["site_id"].astype(str).str.upper().str.strip(),
                      "tech": ep["technology"].astype(str).str.upper().map(names)}).dropna()
    n = d.groupby(["site", "tech"]).size()
    out: dict = {}
    for (site, tech), v in sorted(n.items(), key=lambda kv: (kv[0][0], kv[0][1]), reverse=True):
        out.setdefault(site, []).append(f"{tech} {int(v)}")
    return {k: " · ".join(v) for k, v in out.items()}


def site_cells(site: str) -> str:
    path = _ep_path()
    return _cells_by_site(path).get(site, "") if path else ""


OVERVIEW, TICKET_VIEW = "Overview", "Ticket Details"


def open_ticket(tid: str) -> None:
    """Open a ticket in the Ticket Details view. The view switch takes it on the
    next run, before it is drawn, so this works from a callback or a dialog."""
    st.session_state["ca_sel_tid"] = tid
    st.session_state["ca_mode_next"] = TICKET_VIEW


def _dots_html(S: pd.DataFrame) -> str:
    """The site's tickets on one time axis, coloured by network analysis."""
    known = S["Problem Time"].dropna()
    if known.empty:
        return ""
    lo, hi = known.min(), known.max()
    span = (hi - lo).total_seconds()
    dots = "".join(
        f'<i style="left:{(100.0 * (t - lo).total_seconds() / span if span else 50.0):.1f}%;'
        f'--c:{CLASS_COLOUR.get(c, PALETTE["nodata"])}" '
        f'title="{_esc(tid)} · {t:%d %b %H:%M} · {_esc(kind)} · {_esc(c)}"></i>'
        for tid, t, kind, c in zip(S["Ticket ID"], S["Problem Time"], S["Ticket Type"],
                                   S["Network Analysis"]) if pd.notna(t))
    return (f'<div class="ca-dots"><div class="ca-dots-l"></div>{dots}</div>'
            f'<div class="ca-tl-ax"><span>{lo:%d %b %Y %H:%M}</span>'
            f'<span>problem times · colour = network analysis</span>'
            f'<span>{hi:%d %b %Y %H:%M}</span></div>')


@st.dialog("Site tickets", width="large", icon=":material/cell_tower:")
def site_tickets(ctx, start: str) -> None:
    """Every ticket of one site in the Daily Target: many complaints from one
    site read together."""
    T, _per_site, has_reopen = ctx.T, ctx.per_site, ctx.has_reopen
    opts = list(_per_site.index)
    site = st.selectbox(
        "Site", opts, index=opts.index(start) if start in opts else 0,
        format_func=lambda s: (f"{s} · {ctx.site_names.get(s, NA)} · {_per_site[s]} "
                               f"ticket{'s' if _per_site[s] != 1 else ''}"))
    S = T[T["Site ID"] == site].sort_values("Problem Time", ascending=False)
    first = S.iloc[0]
    tc, cc, rc = (S["_tt"].value_counts(), S["Network Analysis"].value_counts(),
                  S["Resolution"].value_counts())
    problems = S.loc[S["Problem Detected"].isin(["Yes", "Possible"]), "Problem"].value_counts()
    area = ctx.rsrp.get(site)
    tiles = [("TICKETS", len(S), PALETTE["cyan"])]
    if has_reopen:
        tiles += [(t, int(tc.get(t, 0)), TYPE_COLOUR[t]) for t in TYPES]
    tiles += [("TECHNICAL", int(cc.get(TECHNICAL, 0)), CLASS_COLOUR[TECHNICAL]),
              ("POSSIBLE", int(cc.get(POSSIBLE, 0)), CLASS_COLOUR[POSSIBLE]),
              ("NO ISSUE", int(cc.get(NO_ISSUE, 0)), PALETTE["excellent"]),
              ("NO DATA", int(cc.get(INSUFFICIENT, 0)), CLASS_COLOUR[INSUFFICIENT]),
              ("RESOLVED", int(rc.get(RESOLVED, 0)), RES_COLOUR[RESOLVED]),
              ("NOT RESOLVED", int(rc.get(NOT_RESOLVED, 0)), RES_COLOUR[NOT_RESOLVED])]
    st.html(
        '<div class="ca-chips">'
        + _chip("tower", f"{site} · {first['Site Name']}")
        + _chip("pin", f"{first['Sup District']} · {first['Governorate']}")
        + _chip("user", ", ".join(S["Engineer"].value_counts().index))
        + _chip("alert", "Problems: " + (", ".join(f"{k} ×{v}" for k, v in problems.items())
                                         or "none detected"))
        + _chip("signal", f"Site area RSRP {area['median']:.0f} dBm" if area
                else "Site area RSRP not available")
        + "</div>"
        + '<div class="ca-strip" style="margin-top:10px">' + "".join(
            f'<div class="ca-tile" style="--c:{c}"><div class="ca-tile-k"><span>{_esc(k)}</span>'
            f'</div><div class="ca-tile-v">{v:,}</div></div>' for k, v, c in tiles) + "</div>"
        + _dots_html(S))
    grid = S[["Ticket ID", "Problem Time", "Ticket Type", "Engineer", "Problem Type",
              "Network Analysis", "Problem", "KPI (window)", "Resolution", "Confidence",
              "SLA"]].copy()
    grid["Ticket Type"] = [[v] for v in grid["Ticket Type"]]
    event = st.dataframe(
        grid, key=f"ca_dlg_{site}", on_select="rerun", selection_mode="single-row",
        hide_index=True, width="stretch", height=min(420, 40 + 35 * len(grid)),
        column_config={
            "Problem Time": st.column_config.DatetimeColumn("Problem Time",
                                                            format="D MMM YYYY, HH:mm"),
            "Ticket Type": st.column_config.MultiselectColumn(
                "Ticket Type", options=ctx.tt_options, color=ctx.tt_colours, width=120),
            "KPI (window)": st.column_config.TextColumn("KPI (window)", width="medium")})
    picked = list(event.selection.rows) if event else []
    tid = S.iloc[picked[0]]["Ticket ID"] if picked and picked[0] < len(S) else None
    c1, c2 = st.columns(2, gap="small")
    if c1.button(f"Open {tid}" if tid else "Select a ticket to open it",
                 icon=":material/open_in_new:", type="primary", disabled=tid is None,
                 width="stretch"):
        open_ticket(tid)
        st.rerun()
    if c2.button("View site on Map", icon=":material/map:", width="stretch"):
        st.session_state["sm_q"] = site
        st.switch_page("views/site_map.py")
