"""Sleep Analysis — was the issue a sleep ticket was closed on ever resolved.

A ticket is put to sleep when the fix was not in the engineer's hands that
day: the cell was full, the site was not built, the place had no coverage.
This page takes those tickets out of the History of Tickets export, runs the
check that fits each closure code against the network as it is measured now —
the serving sector's hourly KPIs, the 3G flow-control counter, the coverage
grid where the subscriber was, the site KMZ for a planned site — and answers
Solve or Not Solve, or says plainly that there is nothing to check it against.

The page reads; it never uploads and never writes. `app/_sleep.py` wires the
resources and draws the panels, `rfopt.sleep.analysis` does the judging.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

import _kpi_view as V
import _kpi_workspace as W
import _sleep as S
from _charts import COPY_JS, kpi_figure
from _ui import header as _header
from rfopt.complaints.history import HistoryFormatError

st.html(S.CSS)
st.html(V.CSS)
st.markdown(W.CHART_CSS, unsafe_allow_html=True)      # the Copy Chart button
_header("Sleep Analysis",
        "Monitor and track previously closed (sleep) tickets and check if the issue is "
        "resolved or still exists")

try:
    TICKETS, SHA, MISSING, FACTS, SITES = S.page_data()
except HistoryFormatError as exc:
    st.html('<div class="rf-card"><div class="sl-note">The stored ticket history could not '
            f"be read: {S.esc(exc)}</div></div>")
    st.stop()

if TICKETS is None:
    st.html('<div class="rf-card"><div class="sl-note">No ticket history yet — upload the R5 '
            "CC Process export once in Data Resources (sidebar → Data). Sleep Analysis reads "
            "the same file History of Tickets reads.</div></div>")
    st.stop()

if TICKETS.empty:
    st.html('<div class="rf-card"><div class="sl-note">No ticket in this history is closed as '
            "Sleep under the eight sleep closure codes.</div></div>")
    st.stop()

# --------------------------------------------------------------------------- #
# the filters, and what they leave
# --------------------------------------------------------------------------- #
PICKS = S.filter_bar(TICKETS)
SHOWN = S.apply_filters(TICKETS, PICKS)

# the four counts, then the three charts under them: three charts beside the
# cards leave neither a closure code nor a card room to be read
S.summary_cards(SHOWN)
b_code, b_rf, b_user = st.columns(3, gap="small")
with b_code, st.container(key="sl_card_code", border=True):
    st.html('<div class="sl-sec"><span class="n">1</span>Tickets by Closure Code</div>'
            + (S.by_closure(SHOWN) or '<div class="sl-note">No ticket.</div>'))
with b_rf, st.container(key="sl_card_rf", border=True):
    st.html('<div class="sl-sec"><span class="n">2</span>Tickets by RF Analysis</div>'
            + (S.by_rf(SHOWN) or '<div class="sl-note">No RF Analysis named.</div>'))
with b_user, st.container(key="sl_card_user", border=True):
    st.html('<div class="sl-sec"><span class="n">3</span>Tickets by User <small>top 8'
            "</small></div>"
            + (S.by_user(SHOWN) or '<div class="sl-note">No user named.</div>'))

if MISSING:
    st.caption("Not loaded, so the checks that need it answer Not Checked: "
               + ", ".join(MISSING))

# --------------------------------------------------------------------------- #
# the tickets
# --------------------------------------------------------------------------- #
t_search, t_legend = st.columns([2, 1.2], gap="small", vertical_alignment="center")
with t_search:
    QUERY = st.text_input("Search tickets", key="sl_q", label_visibility="collapsed",
                          placeholder="Search (Ticket ID, Site ID, Sector…)")
with t_legend:
    st.html(S.legend())

FOUND = S.searched(SHOWN, QUERY)
TABLE = S.for_table(TICKETS)

# the ticket shown below: the one clicked, while it is still among the rows
OPENED = st.session_state.get(S.OPEN)
if OPENED not in set(FOUND["hpsm_id"]):
    OPENED = FOUND["hpsm_id"].iloc[0] if len(FOUND) else None
    st.session_state[S.OPEN] = OPENED

S.table(TABLE, SHA, found=TABLE.loc[FOUND.index], query=QUERY, open_id=OPENED,
        title=f"Sleep Tickets List · {len(FOUND):,} of {len(TICKETS):,}")

if OPENED is None:
    st.html('<div class="rf-card"><div class="sl-note">No ticket matches these filters.</div>'
            "</div>")
    st.stop()

# --------------------------------------------------------------------------- #
# the ticket that is open
# --------------------------------------------------------------------------- #
ROW = TICKETS[TICKETS["hpsm_id"].eq(OPENED)].iloc[0]
EV = FACTS.get("evidence", {}).get((str(ROW["site"]), ROW["sector_num"]))

with st.container(key="sl_card_detail", border=True):
    S.head(ROW)
    st.divider()
    p1, p2 = st.columns([1, 1.5], gap="small")
    with p1:
        st.html('<div class="sl-sec"><span class="n">1</span>Current KPI Status '
                f'<small>serving sector {S.esc(ROW["serving"] or "—")}</small></div>')
        st.html(S.kpi_donut(EV, ROW))
        if EV is not None and EV.hours:
            st.html(f'<div class="sl-note">{EV.hours} measured hours, '
                    f'{S.when(EV.start, "%d %b %H:%M")} → {S.when(EV.end, "%d %b %H:%M")}'
                    f'{" · cells " + ", ".join(EV.cells) if EV.cells else ""}</div>')
    with p2:
        st.html('<div class="sl-sec"><span class="n">2</span>Sector Comparison '
                "<small>the serving sector, its site and the sites around it</small></div>")
        TABLE2 = S.comparison(FACTS, ROW, SITES)
        if len(TABLE2):
            st.dataframe(S.comparison_style(TABLE2), hide_index=True, width="stretch",
                         height=min(60 + 35 * len(TABLE2), 300))
        else:
            st.html('<div class="sl-note">No sector of this site is in the loaded KPI '
                    "export.</div>")

    p3, p4 = st.columns([1.15, 1], gap="small")
    with p3:
        c_t, c_k = st.columns([1, 1], gap="small", vertical_alignment="center")
        with c_t:
            st.html('<div class="sl-sec"><span class="n">3</span>KPI Trend '
                    "<small>hour by hour</small></div>")
        CHOICES = S.trend_choices(FACTS)
        with c_k:
            PICK = st.selectbox("KPI", CHOICES, format_func=lambda c: c[0],
                                key="sl_trend_kpi", label_visibility="collapsed") \
                if CHOICES else None
        PANEL = S.trend_panel(FACTS, ROW, PICK) if PICK else None
        with st.container(key="sl_card_trend", border=False):
            if PANEL is not None:
                st.html(S.chart_head("sl_card_trend", f"{ROW['serving'] or ROW['site']} · "
                                     f"{PICK[0]}", PICK[1],
                                     f"{ROW['serving'] or ROW['site']}_{PICK[0]}"))
                st.plotly_chart(kpi_figure(PANEL, height=236, legend_title="Cell Name"),
                                width="stretch", config={"displaylogo": False},
                                key=f"sl_trend_{OPENED}")
            else:
                st.html('<div class="sl-note">No hourly data for this serving sector in the '
                        "loaded export.</div>")
    if st.session_state.get("sl_fs"):
        st.html(S.FS_CSS)
    with p4, st.container(key="sl_mapbox"):
        m_t, m_b = st.columns([1, 1], gap="small", vertical_alignment="center")
        with m_t:
            st.html('<div class="sl-sec"><span class="n">4</span>Map &amp; User Location'
                    "</div>")
        with m_b:
            BASE = st.segmented_control("Basemap", [S.SATELLITE, S.COVERAGE],
                                        key="sl_basemap", default=S.SATELLITE,
                                        label_visibility="collapsed", width="stretch")
        S.map_panel(ROW, height=300, basemap=BASE or S.SATELLITE)

    st.html('<div class="sl-sec"><span class="n">5</span>Description</div>')
    st.html(S.description_panel(ROW))

st.html(COPY_JS, unsafe_allow_javascript=True)
