"""Dashboard — the executive overview of R5, over the data the app already holds.

The sites of the Site KMZ, the KPI judgement of KPI Analysis, the R5 ticket
history and the day's Daily Target, each read through Data Resources by the
same readers their own pages use (`_dashboard`). Nothing is judged, counted or
uploaded here: every panel summarises a page, and the page stays where the
detail is — Sites, KPI Analysis, Delay Tickets Analysis, History of Tickets.
"""

from __future__ import annotations

import streamlit as st

import _dashboard as D
import _resources as R
import _ticket_history as H
from _ui import PALETTE, header, kpi_cards
from rfopt.complaints.history import HistoryFormatError

st.html(H.CSS)
st.html(D.CSS)
header("Dashboard", "Network Performance | KPI | Tickets | Sites")

# --------------------------------------------------------------------------- #
# what there is to show
# --------------------------------------------------------------------------- #
SITES = D.sites()
KPI = D.kpi()
try:
    TICKETS = R.history()
except HistoryFormatError:
    TICKETS = None
T = D.tickets_state(TICKETS) if TICKETS is not None else None
TARGET, TARGET_ROWS = D.target()

if SITES is None and KPI is None and TICKETS is None and TARGET is None:
    st.html('<div class="rf-card"><div class="db-none">No data yet — upload the site KMZ, the '
            "hourly KPI exports, the R5 ticket history and the day's Daily Target once in "
            "Data Resources (sidebar → Data). This page summarises them.</div></div>")
    R.link("Open Data Resources")
    st.stop()


def _when() -> str:
    """When the newest dataset behind this page was applied."""
    stamps = [r.applied_at for r in (R.active(k) for k in ("kmz", "kpi", "history", "complaints"))
              if r is not None and r.applied_at]
    return R.when(max(stamps)) if stamps else "—"


window = (f"{KPI['start']:%d %b} – {KPI['end']:%d %b %Y}"
          if KPI and KPI["start"] is not None else "no KPI window")
held = sum(x is not None for x in (SITES, KPI, TICKETS, TARGET))
st.html(f'<div class="db-strip"><span>⟳ Last Updated: <b>{D._esc(_when())}</b>'
        f' &nbsp;·&nbsp; KPI window: <b>{D._esc(window)}</b></span>'
        f'<span><i class="db-dot"></i>{held} of 4 datasets in Data Resources'
        f' &nbsp;·&nbsp; <b>Shamsaldin Ali</b></span></div>')


# --------------------------------------------------------------------------- #
# the cards
# --------------------------------------------------------------------------- #
def _sites_note() -> str:
    if SITES is None:
        return "No site KMZ yet"
    n = SITES["air"].value_counts()
    return " · ".join(f"{int(n.get(k, 0)):,} {D.AIR_LABEL[k].lower()}"
                      for k in ("onair", "planned", "offair"))


kpi_cards([
    {"title": "Total Sites", "value": f"{len(SITES):,}" if SITES is not None else "—",
     "icon": "tower", "tone": "blue", "note": _sites_note()},
    {"title": "Sites with KPI Issues",
     "value": f"{KPI['issues']:,}" if KPI else "—", "icon": "alert", "tone": "poor",
     "pct": H.pct(KPI["issues"], KPI["sites"]) if KPI and KPI["sites"] else None,
     "note": (f"{KPI['critical']:,} critical · {KPI['warning']:,} warning of "
              f"{KPI['sites']:,} judged" if KPI else "No KPI Data yet")},
    {"title": "Total Tickets", "value": f"{T['total']:,}" if T else "—", "icon": "ticket",
     "tone": "cyan", "note": (f"{T['closed']:,} closed" if T else "No ticket history yet")},
    {"title": "Target Tickets",
     "value": f"{TARGET.records:,}" if TARGET is not None else "—", "icon": "target",
     "tone": "warning",
     "note": (f"{TARGET.name}" if TARGET is not None else "No Daily Target yet")},
    {"title": "Sleep Tickets", "value": f"{T['sleep']:,}" if T else "—", "icon": "sleep",
     "tone": "#A78BFA", "pct": H.pct(T["sleep"], T["total"]) if T and T["total"] else None,
     "note": ("of every ticket in the history" if T else "No ticket history yet")},
])


# --------------------------------------------------------------------------- #
# network health: the three rings
# --------------------------------------------------------------------------- #
st.html('<div class="db-sec">Network Health Overview</div>')
h1, h2, h3 = st.columns(3, gap="small")
with h1, st.container(key="rf_card_db_health", border=True):
    st.html(D.head("KPI Health", f"{KPI['sites']:,} sites judged" if KPI else "")
            + (D.health_ring(KPI["distribution"]) if KPI
               else '<div class="db-none">No KPI Data yet.</div>'))
with h2, st.container(key="rf_card_db_air", border=True):
    st.html(D.head("Site Status", "from the site KMZ")
            + (D.air_ring(SITES) if SITES is not None
               else '<div class="db-none">No site KMZ yet.</div>'))
with h3, st.container(key="rf_card_db_eng", border=True):
    st.html(D.head("Closed Tickets by Engineer", "the whole history")
            + (D.engineer_ring(T["closed_by"]) if T
               else '<div class="db-none">No ticket history yet.</div>'))


# --------------------------------------------------------------------------- #
# KPI issues by type · top Sup Districts · the map
# --------------------------------------------------------------------------- #
a1, a2, a3 = st.columns(3, gap="small")
with a1, st.container(key="rf_card_db_types", border=True):
    st.html(D.head("KPI Issues by Type", "sites breaching")
            + (D.issue_types(KPI["ranking"]) if KPI
               else '<div class="db-none">No KPI Data yet.</div>'))
with a2, st.container(key="rf_card_db_sd", border=True):
    st.html(D.head("Top 10 Sup Districts by KPI Issues", "sites with an issue")
            + (D.districts(KPI["districts"]) if KPI
               else '<div class="db-none">No KPI Data yet.</div>'))
with a3, st.container(key="rf_card_db_map", border=True):
    st.html(D.head("Network Overview Map", "R5 sites"))
    if SITES is None:
        st.html('<div class="db-none">No site KMZ yet.</div>')
    else:
        import folium
        from streamlit_folium import st_folium

        from _map_assets import add_basemap

        bad = set(KPI["status"].index[KPI["status"]["sev"] > 0]) if KPI else set()
        day = (set(TARGET_ROWS["site_id"].dropna().astype(str))
               if TARGET_ROWS is not None and "site_id" in TARGET_ROWS.columns else set())
        fmap = folium.Map(location=[float(SITES["latitude"].mean()),
                                    float(SITES["longitude"].mean())],
                          zoom_start=6, tiles=None, zoom_control=True, max_zoom=16)
        add_basemap(fmap, "https://server.arcgisonline.com/ArcGIS/rest/services/"
                          "Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
                    attr="Tiles © Esri — Esri, DeLorme, HERE", max_zoom=16)

        def _dots(rows, colour, radius, name):
            if not len(rows):
                return
            data = {"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": {"t": f"{r.site_id} · {name}"},
                 "geometry": {"type": "Point", "coordinates": [float(r.longitude),
                                                               float(r.latitude)]}}
                for r in rows.itertuples(index=False)]}
            folium.GeoJson(
                data, name=name, embed=True,
                marker=folium.CircleMarker(radius=radius, weight=1, fill=True, fill_opacity=.85),
                style_function=lambda _f, c=colour: {"color": c, "fillColor": c},
                tooltip=folium.GeoJsonTooltip(fields=["t"], labels=False)).add_to(fmap)

        for air in ("onair", "planned", "offair"):
            _dots(SITES[SITES["air"].eq(air)], D.AIR[air], 2.5, D.AIR_LABEL[air])
        _dots(SITES[SITES["site_id"].isin(bad)], PALETTE["warning"], 4, "KPI issue")
        _dots(SITES[SITES["site_id"].isin(day)], PALETTE["critical"], 5, "Target ticket")
        st_folium(fmap, key="db_map", height=252, use_container_width=True,
                  returned_objects=[])
        legend = [(D.AIR[k], D.AIR_LABEL[k]) for k in ("onair", "planned", "offair")]
        legend += [(PALETTE["warning"], f"KPI issue ({len(bad & set(SITES['site_id'])):,})"),
                   (PALETTE["critical"], f"Target ticket ({len(day & set(SITES['site_id'])):,})")]
        st.html('<div class="th-note" style="display:flex;gap:11px;flex-wrap:wrap">'
                + "".join(f'<span><i style="display:inline-block;width:8px;height:8px;'
                          f'border-radius:50%;background:{c};margin-right:4px"></i>'
                          f"{D._esc(lab)}</span>" for c, lab in legend) + "</div>")


# --------------------------------------------------------------------------- #
# tickets: by city, by group, reopened by user
# --------------------------------------------------------------------------- #
b1, b2, b3 = st.columns(3, gap="small")
with b1, st.container(key="rf_card_db_city", border=True):
    st.html(D.head("Tickets by City", "the whole history"))
    if T:
        s = T["by_city"]
        st.html(D.ring(list(zip(s.index, map(int, s.to_numpy()), H.SERIES)), T["total"],
                       "Tickets"))
    else:
        st.html('<div class="db-none">No ticket history yet.</div>')
with b2, st.container(key="rf_card_db_group", border=True):
    st.html(D.head("Tickets by Group", "the whole history"))
    if T:
        g = H.top_with_others(T["by_group"], 4)
        st.html(D.ring(list(zip(g.index, map(int, g.to_numpy()), H.group_colours(g.index))),
                       T["total"], "Tickets"))
    else:
        st.html('<div class="db-none">No ticket history yet.</div>')
with b3, st.container(key="rf_card_db_reopen", border=True):
    st.html(D.head("Reopened Tickets by User", "reopened at least once")
            + (D.reopened(T["reopened_by"]) if T
               else '<div class="db-none">No ticket history yet.</div>'))


# --------------------------------------------------------------------------- #
# the trends
# --------------------------------------------------------------------------- #
c1, c2 = st.columns(2, gap="small")
part = KPI and KPI["end"] is not None and KPI["end"].hour < 23
with c1, st.container(key="rf_card_db_ktrend", border=True):
    st.html(D.head("KPI Issues Trend", "each day of the KPI window"
                   + (f" · {KPI['end']:%d %b} to {KPI['end']:%H:%M} only" if part else ""))
            + (D.kpi_trend(KPI["by_day"], w=500) if KPI
               else '<div class="db-none">No KPI Data yet.</div>'))
with c2, st.container(key="rf_card_db_ttrend", border=True):
    st.html(D.head("Tickets Trend", "RF group, by the month they were created")
            + (D.ticket_trend(T["per_month"], w=500) if T
               else '<div class="db-none">No ticket history yet.</div>'))


# --------------------------------------------------------------------------- #
# key insights
# --------------------------------------------------------------------------- #
def _insight_items() -> list:
    items = []
    if KPI and not KPI["ranking"].empty:
        top = KPI["ranking"].iloc[0]
        items.append(("Most Affected KPI", str(top["label"]),
                      f"{int(top['sites']):,} sites · {int(top['critical']):,} critical",
                      "chart", PALETTE["critical"]))
    if KPI and len(KPI["districts"]):
        sd = KPI["districts"]
        items.append(("Most Affected Sup District", str(sd.index[0]),
                      f"{int(sd.iloc[0]):,} sites with an issue", "pin", PALETTE["poor"]))
    if T and len(T["by_city"]):
        city = T["by_city"]
        items.append(("Highest Ticket Volume", str(city.index[0]),
                      f"{int(city.iloc[0]):,} tickets · {H.pct(city.iloc[0], T['total']):.1f}%",
                      "ticket", PALETTE["cyan"]))
    if T:
        mine = H.count(TICKETS[TICKETS["user"].eq("Shams Aldin Ali")])
        items.append(("Shams Aldin Ali", f"{mine:,} tickets",
                      f"{T['closed_by'].get('Shams Aldin Ali', 0):,} closed · "
                      f"{T['reopened_by'].get('Shams Aldin Ali', 0):,} reopened",
                      "user", PALETTE["blue"]))
    if T:
        items.append(("SLA Breached (RF)", f"{T['rf_breach']:,} tickets",
                      f"{H.pct(T['rf_breach'], T['rf_total']):.1f}% of {T['rf_total']:,} "
                      "RF tickets", "shield", PALETTE["critical"]))
    return items


items = _insight_items()
if items:
    with st.container(key="rf_card_db_insights", border=True):
        st.html(D.head("Key Insights", "from the data this page shows") + D.insights(items))
