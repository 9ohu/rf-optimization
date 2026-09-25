"""Dashboard — the numbers and pictures of the overview page.

Nothing here reads a file of its own or judges anything anew. The sites are
the Site KMZ the Sites map draws, the KPI judgement is the one KPI Analysis
shows (`_kpi_health` over the same cached exports), the tickets are the
History ticket resource History of Tickets counts, and the day's worklist is
the Daily Target — every one of them through Data Resources.

The pictures are the app's own: the bars, donuts and legends of History of
Tickets (`_ticket_history`) and the line chart of the report preview
(`_svg_charts`).
"""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st

import _kpi_workspace as W
import _resources as R
import _svg_charts as X
import _ticket_history as H
from _kpi_health import STATE, build_health, judged_columns, problem_ranking, site_status
from _kpi_region import affected_by_area
from _map_ui import AIR, AIR_LABEL
from _ui import PALETTE

# the engineers the user follows on the Dashboard, as the history names them
ENGINEERS = ("Aws Waheeb", "Shams Aldin Ali", "Mahmoud Dhari Essa")
STATE_COLOUR = {"Critical": PALETTE["critical"], "Warning": PALETTE["warning"],
                "Normal": PALETTE["excellent"], "No data": PALETTE["nodata"]}
WHO_COLOUR = ("#1597FF", "#22C55E", "#A78BFA")
BLUE, CYAN, GREEN, AMBER, RED = "#1597FF", "#20BFFF", "#22C55E", "#F59E0B", "#EF4444"

CSS = """
<style>
.db-h { display: flex; align-items: baseline; gap: 8px; font-size: 15.5px; font-weight: 700;
    color: #F1F5F9; line-height: 1.25; margin-bottom: 2px; }
.db-h small { font-weight: 500; font-size: 11.5px; color: #94A3B8; }
.db-sec { font-size: 13px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
    color: #94A3B8; margin: 2px 0 -4px 2px; }
.db-strip { display: flex; align-items: center; justify-content: space-between; gap: 10px;
    flex-wrap: wrap; color: #94A3B8; font-size: 12.5px; padding: 2px 4px 0; }
.db-strip b { color: #CBD5E1; font-weight: 600; }
.db-strip .db-dot { width: 8px; height: 8px; border-radius: 50%; background: #22C55E;
    box-shadow: 0 0 7px #22C55E; display: inline-block; margin-right: 6px; }
.db-none { color: #94A3B8; font-size: 13px; padding: 34px 0; text-align: center; }
.db-note { color: #64748B; font-size: 11px; margin-top: 4px; }
.st-key-rf_card_db_health, .st-key-rf_card_db_air, .st-key-rf_card_db_eng { min-height: 300px; }
.st-key-rf_card_db_types, .st-key-rf_card_db_sd, .st-key-rf_card_db_map { min-height: 330px; }
.st-key-rf_card_db_city, .st-key-rf_card_db_group, .st-key-rf_card_db_reopen { min-height: 286px; }
.st-key-rf_card_db_map iframe { border-radius: 10px; }
.db-ins { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; }
@media (max-width: 1250px) { .db-ins { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 760px) { .db-ins { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.db-tile { display: flex; align-items: center; gap: 11px; background: #071525;
    border: 1px solid #1E3A5F; border-radius: 10px; padding: 11px 12px; min-width: 0; }
.db-tile > div:last-child { min-width: 0; }
.db-ico { flex: 0 0 38px; height: 38px; border-radius: 9px; display: flex; align-items: center;
    justify-content: center;
    background: linear-gradient(135deg, color-mix(in srgb, var(--c) 34%, #0B1F33),
                                color-mix(in srgb, var(--c) 10%, #071525));
    border: 1px solid color-mix(in srgb, var(--c) 50%, transparent);
    box-shadow: 0 0 14px color-mix(in srgb, var(--c) 28%, transparent); }
.db-lbl { font-size: 11.5px; color: #94A3B8; white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }
.db-val { font-size: 15.5px; font-weight: 700; color: #F1F5F9; margin-top: 2px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.db-sub { font-size: 11.5px; color: var(--c); margin-top: 2px; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; }
</style>
"""


def _esc(x) -> str:
    return html.escape(str(x))


# --------------------------------------------------------------------------- #
# the data: every page's own, read through the same cached readers
# --------------------------------------------------------------------------- #
def kpi_frames():
    """(cache key, frames) of the active KPI Data — the KPI Analysis page's."""
    srcs = W.combine(R.kpi_groups())
    judged = [(b, i, judged_columns(i.all_kpis, i.kind)) for b, i in srcs]
    frames = [(i.kind, W.raw(b, tuple(j.column for j in js)), js) for b, i, js in judged if js]
    key = tuple((W.src_key(b), i.kind, tuple(j.column for j in js)) for b, i, js in judged if js)
    return key, frames


@st.cache_resource(show_spinner="Counting the KPI issues of each day…", max_entries=2)
def _by_day(key: tuple, _frames) -> pd.DataFrame:
    """Sites with an issue, and sites critical, on each day of the window —
    each day judged exactly as the whole window is."""
    days = sorted({d for _, f, _ in _frames if f is not None and len(f)
                   for d in f["datetime"].dt.normalize().unique()})
    rows = []
    for day in map(pd.Timestamp, days):
        part = [(k, f[(f["datetime"] >= day) & (f["datetime"] < day + pd.Timedelta(days=1))], js)
                for k, f, js in _frames]
        h = build_health(part)
        status = site_status(h.objects, h.sites)
        rows.append({"day": day, "issues": int((status["sev"] > 0).sum()),
                     "critical": int((status["sev"] == 2).sum())})
    return pd.DataFrame(rows)


@st.cache_resource(show_spinner=False, max_entries=2)
def _areas(key: tuple, _objects, ep: tuple) -> pd.Series:
    """Sites with a KPI issue per Sup District (`_kpi_region`), most first."""
    sites = sorted(_objects["site_id"].dropna().astype(str).unique())
    s = affected_by_area(_objects, W.placed(sites), "Sup District")
    return s.sort_values(ascending=False, kind="stable") if len(s) else s


def kpi() -> dict | None:
    """What the KPI panels show, or None when no KPI Data is applied."""
    key, frames = kpi_frames()
    if not frames:
        return None
    h = W.health(key, frames)
    status = site_status(h.objects, h.sites)
    ranking = problem_ranking(h.objects)
    return {"key": key, "frames": frames, "health": h, "status": status, "ranking": ranking,
            "sites": len(h.sites), "issues": int((status["sev"] > 0).sum()),
            "critical": int((status["sev"] == 2).sum()),
            "warning": int((status["sev"] == 1).sum()),
            "distribution": {s: int((status["sev"] == k).sum()) for k, s in STATE.items()},
            "districts": _areas(key, h.objects, W.ep_key()),
            "by_day": _by_day(key, frames), "start": h.start, "end": h.end}


@st.cache_resource(show_spinner="Reading the site list…", max_entries=2)
def _kmz_sites(path: str) -> pd.DataFrame:
    """Every KMZ site with its air status and position — the Sites map's own."""
    from rfopt.ingest.kmz_sites import load_kmz_sites
    ks = load_kmz_sites(path, region=None)
    s = ks.sectors
    for c in ("latitude", "longitude"):
        s[c] = pd.to_numeric(s[c], errors="coerce")
    out = (s.dropna(subset=["latitude", "longitude"])
           .groupby("site_id", as_index=False)
           .agg(site_name=("site_name", "first"), latitude=("latitude", "mean"),
                longitude=("longitude", "mean"),
                air=("air", lambda v: v.mode().iat[0] if len(v.mode()) else "onair")))
    return out


def sites() -> pd.DataFrame | None:
    path = R.kmz_path()
    return _kmz_sites(path) if path else None


def target():
    """The day's Daily Target: (dataset, its tickets) or (None, None)."""
    ds = R.target()
    if ds is None:
        return None, None
    try:
        return ds, W.tickets(ds.sha1, ds)
    except Exception:                      # a file the parser cannot read
        return ds, None


def tickets_state(df: pd.DataFrame) -> dict:
    """What the ticket panels show, from the History ticket data."""
    rf = df[df["group"].str.upper().eq("RF")]
    reopened = df[pd.to_numeric(df["reopen"], errors="coerce").fillna(0) >= 1]
    closed = df[df["state"].eq("Closed")]
    per_month = (rf.dropna(subset=["create_time"])
                 .groupby(rf["create_time"].dt.to_period("M").astype(str))["hpsm_id"].nunique()
                 if len(rf) else pd.Series(dtype=int))
    return {
        "total": H.count(df), "sleep": H.count(df[df["state"].eq("Sleep")]),
        "closed": H.count(closed), "by_city": H.by(df, "city", keep_empty=True),
        "by_group": H.by(df, "group", keep_empty=True),
        "closed_by": {w: H.count(closed[closed["user"].eq(w)]) for w in ENGINEERS},
        "reopened_by": {w: H.count(reopened[reopened["user"].eq(w)]) for w in ENGINEERS},
        "per_month": per_month.sort_index(),
        "rf_total": H.count(rf),
        "rf_breach": H.count(rf[rf["sla_status"].str.lower().eq("sla_violation")]),
    }


# --------------------------------------------------------------------------- #
# the pictures
# --------------------------------------------------------------------------- #
def head(title: str, note: str = "") -> str:
    return (f'<div class="db-h" title="{_esc(title)}">{_esc(title)}'
            + (f"<small>{_esc(note)}</small>" if note else "") + "</div>")


def ring(parts, total: int, centre: str) -> str:
    """A donut with its legend beside it — History of Tickets' own."""
    if not total:
        return '<div class="db-none">Nothing to show yet.</div>'
    return ('<div class="th-dn">' + H.donut(parts, total, centre=centre)
            + H.legend(parts, total) + "</div>")


def health_ring(dist: dict) -> str:
    parts = [(s, int(dist.get(s, 0)), STATE_COLOUR[s]) for s in ("Normal", "Warning", "Critical")
             if dist.get(s, 0)]
    parts += [(s, int(dist.get(s, 0)), STATE_COLOUR[s]) for s in ("No data",) if dist.get(s, 0)]
    return ring(parts, sum(k for _, k, _ in parts), "Sites")


def air_ring(site_rows: pd.DataFrame) -> str:
    n = site_rows["air"].value_counts()
    parts = [(AIR_LABEL[k], int(n.get(k, 0)), AIR[k]) for k in ("onair", "planned", "offair")
             if int(n.get(k, 0))]
    return ring(parts, int(n.sum()), "Sites")


def engineer_ring(counts: dict) -> str:
    parts = [(w, int(counts.get(w, 0)), WHO_COLOUR[i % len(WHO_COLOUR)])
             for i, w in enumerate(ENGINEERS) if counts.get(w, 0)]
    return ring(parts, sum(k for _, k, _ in parts), "Closed")


def issue_types(ranking: pd.DataFrame, w: int = 330) -> str:
    """The KPI issues of the network by KPI — sites breaching each."""
    if ranking.empty:
        return '<div class="db-none">No KPI breaches in the loaded window.</div>'
    top = ranking.head(8)
    return H.vbars(top["label"], top["sites"].to_numpy(), w=w, h=300, tilt=35)


def districts(counts: pd.Series, w: int = 330) -> str:
    if counts is None or not len(counts):
        return '<div class="db-none">No Sup District to rank yet.</div>'
    top = counts.head(10)
    return H.hbars(top.index, top.to_numpy(), w=w, row=25, label_w=104)


def reopened(counts: dict, w: int = 330) -> str:
    s = pd.Series(counts).sort_values(ascending=False)
    if not s.sum():
        return '<div class="db-none">No reopened ticket for these users.</div>'
    return H.hbars(s.index, s.to_numpy(), w=w, row=34, label_w=130)


def kpi_trend(by_day: pd.DataFrame, w: int = 330) -> str:
    if by_day is None or by_day.empty:
        return '<div class="db-none">No day to trend yet.</div>'
    labels = [f"{d:%b %d}" for d in by_day["day"]]
    return X.line(labels, [("Sites with issues", list(by_day["issues"]), BLUE, False),
                           ("Critical", list(by_day["critical"]), RED, False)],
                  w=w, h=270)


def ticket_trend(per_month: pd.Series, w: int = 330) -> str:
    if per_month is None or not len(per_month):
        return '<div class="db-none">No RF ticket to trend yet.</div>'
    labels = [f"{pd.Period(p, freq='M').to_timestamp():%b %Y}" for p in per_month.index]
    return X.line(labels, [("RF tickets created", list(per_month.to_numpy()), CYAN, False)],
                  w=w, h=270)


def insights(items) -> str:
    """The bottom strip: (label, value, note, icon, colour) tiles."""
    from _ui import icon_img
    tiles = "".join(
        f'<div class="db-tile" style="--c:{c}"><div class="db-ico">{icon_img(ic, c, 21)}</div>'
        f'<div><div class="db-lbl">{_esc(lab)}</div>'
        f'<div class="db-val" title="{_esc(val)}">{_esc(val)}</div>'
        f'<div class="db-sub">{_esc(note)}</div></div></div>'
        for lab, val, note, ic, c in items)
    return f'<div class="db-ins">{tiles}</div>'
