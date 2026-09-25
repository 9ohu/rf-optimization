"""Complaints · History of Tickets — the R5 ticket history, in two tabs.

History of Tickets: the history filtered — City, Sup District, User, Status and
Group, any number of values each, and the Time Period (All Period or a custom
one) — taking effect on Apply, Reset clears them. Under them: Total, Closed,
Pending, In Progress and Sleep tickets; tickets by user, status and group; the
top 20 sites; tickets by city, by Sup District and by RF Analysis. Every number
is counted from the file — tickets by their HPSM Incident ID
(`_ticket_history`) — and nothing else.

Tickets Details: one ticket in full, and every ticket in a table whose columns
filter like Excel (`_ticket_details`).

Both read the History ticket resource of Data Resources: the CC Process export
of every R5 ticket, uploaded there by the user.
"""

from __future__ import annotations

import base64
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import _resources as R
import _ticket_details as T
import _ticket_history as H
from _ui import header
from rfopt.complaints.history import CLOSED, IN_PROGRESS, PENDING, SLEEP, HistoryFormatError

ss = st.session_state
LOGO = Path(__file__).resolve().parents[1] / "assets" / "report" / "huawei_logo_white.png"
HISTORY, DETAILS = "History of Tickets", "Tickets Details"
TAB_ICON = {HISTORY: ":material/history:", DETAILS: ":material/receipt_long:"}
ALL_PERIOD, CUSTOM = "All Period", "Custom Period"
st.html(H.CSS)

try:
    DF = R.history()
except HistoryFormatError as exc:
    header("History of Tickets", "Analysis of R5 tickets")
    st.error(f"The stored History ticket file could not be read: {exc}")
    st.stop()
if DF is None:
    header("History of Tickets", "Analysis of R5 tickets")
    st.html('<div class="rf-card"><div class="th-none">No History ticket data yet — upload the '
            "R5 ticket history (the CC Process Excel export) once in Data Resources "
            "(sidebar → Data). This page reads it from there.</div></div>")
    R.link("Open Data Resources")
    st.stop()

t = DF["create_time"].dropna()
header("History of Tickets",
       f"Analysis of R5 tickets ({t.min():%d %b %Y} – {t.max():%d %b %Y})" if len(t)
       else "Analysis of R5 tickets")


def _footer() -> None:
    """When the export was taken, and the logo."""
    f = R.history_file()
    m = re.search(r"(\d{14})", f.name) if f else None
    try:
        stamp = datetime.strptime(m.group(1), "%Y%m%d%H%M%S") if m else None
    except ValueError:
        stamp = None
    updated = f"{stamp:%d %b %Y %H:%M}" if stamp else R.when(R.active("history").applied_at)
    logo = (f'<img src="data:image/png;base64,{base64.b64encode(LOGO.read_bytes()).decode()}" '
            f'alt="HUAWEI">' if LOGO.is_file() else "")
    st.html(f'<div class="th-foot"><span>⟳ Last Updated: <b>{H._esc(updated)}</b></span>'
            f"<span>RF Ticket Analysis &nbsp;|&nbsp; Huawei{logo}</span></div>")


# --------------------------------------------------------------------------- #
# the two tabs: only the open one is drawn; the tab stays when the user comes
# back from another page
# --------------------------------------------------------------------------- #
if ss.get("th_view") not in (HISTORY, DETAILS):
    ss["th_view"] = ss.get("th_view_kept", HISTORY)
with st.container(key="th_views"):
    view = st.segmented_control("View", [HISTORY, DETAILS], key="th_view", required=True,
                                format_func=lambda v: f"{TAB_ICON[v]} {v}",
                                label_visibility="collapsed")
ss["th_view_kept"] = view

if view == DETAILS:
    T.render(DF, R.history_file())
    _footer()
    st.stop()


# --------------------------------------------------------------------------- #
# the filters: any number of values each, and the time period; they take
# effect on Apply
# --------------------------------------------------------------------------- #
OPTS = {f: H.options(DF, f) for f, _ in H.FILTERS}
DAYS = (t.min().date(), t.max().date()) if len(t) else None


def _period(v) -> tuple | None:
    """A (first day, last day) inside the file's days, else None."""
    if DAYS is None or not isinstance(v, (tuple, list)) or not v:
        return None
    lo, hi = (v[0], v[-1])
    lo, hi = min(lo, hi), max(lo, hi)
    return (lo, hi) if DAYS[0] <= lo and hi <= DAYS[1] else None


def _valid(chosen: dict) -> dict:
    """The filters as applied, with only the values the file still has."""
    out = {}
    for f, _ in H.FILTERS:
        v = chosen.get(f) or []
        if isinstance(v, str):                      # one value, as the filters once were
            v = [] if v == H.ALL else [v]
        out[f] = [x for x in v if x in OPTS[f]]
    out["period"] = _period(chosen.get("period"))
    return out


applied = _valid(ss.get("th_applied") or {})
ss["th_applied"] = applied
for f, _ in H.FILTERS:
    k = f"th_{f}"
    if not isinstance(ss.get(k), list) or any(v not in OPTS[f] for v in ss[k]):
        ss[k] = list(applied[f])
if ss.get("th_period") not in (ALL_PERIOD, CUSTOM):
    ss["th_period"] = CUSTOM if applied["period"] else ALL_PERIOD
if DAYS and _period(ss.get("th_days")) is None:
    ss["th_days"] = applied["period"] or DAYS


def _apply() -> None:
    chosen = {f: list(ss.get(f"th_{f}") or []) for f, _ in H.FILTERS}
    before = ss["th_applied"].get("period")
    days = _period(ss.get("th_days")) or DAYS
    mode = ss.get("th_period", ALL_PERIOD)
    # new dates picked while the period kept its choice: they are the period
    if (mode == CUSTOM) == bool(before) and days != (before or DAYS):
        mode = ALL_PERIOD if days == DAYS else CUSTOM
    chosen["period"] = days if mode == CUSTOM and days else None
    ss["th_period"] = mode
    if mode == ALL_PERIOD:
        ss["th_days"] = DAYS
    ss["th_applied"] = chosen


def _reset() -> None:
    for f, _ in H.FILTERS:
        ss[f"th_{f}"] = []
    ss["th_period"] = ALL_PERIOD
    ss["th_days"] = DAYS
    ss["th_applied"] = {**{f: [] for f, _ in H.FILTERS}, "period": None}


def _period_text(period) -> str:
    if not period:
        return ALL_PERIOD
    lo, hi = period
    if lo == hi:
        return f"{lo:%d %b %Y}"
    if lo.year == hi.year:
        return f"{lo:%d %b} – {hi:%d %b %Y}"
    return f"{lo:%d %b %Y} – {hi:%d %b %Y}"


with st.container(key="rf_card_th_filters", border=True):
    with st.form("th_form", border=False):
        cols = st.columns([0.85, 0.95, 0.95, 0.85, 0.8, 1.15, 0.9, 0.9], gap="small",
                          vertical_alignment="bottom")
        for col, (f, label) in zip(cols, H.FILTERS):
            col.multiselect(label, OPTS[f], key=f"th_{f}", placeholder=H.ALL)
        # the Time Period: All Period, or the custom days picked inside
        with cols[5], st.container(gap=None):
            st.html('<div class="th-lbl">Time Period</div>')
            with st.popover(_period_text(applied["period"]), key="th_period_box",
                            width="stretch", help="The days the tickets were created in"):
                st.radio("Time Period", [ALL_PERIOD, CUSTOM], key="th_period",
                         horizontal=True, label_visibility="collapsed")
                if DAYS:
                    st.date_input("Custom Period", min_value=DAYS[0], max_value=DAYS[1],
                                  key="th_days", format="DD/MM/YYYY")
                st.caption("Takes effect on Apply.")
        cols[6].form_submit_button("Apply", icon=":material/filter_alt:", type="primary",
                                   width="stretch", on_click=_apply)
        cols[7].form_submit_button("Reset", icon=":material/restart_alt:", width="stretch",
                                   on_click=_reset)

D = H.apply_filters(DF, ss["th_applied"])
TOTAL = H.count(D)
GROUPS = ss["th_applied"].get("group") or []


# --------------------------------------------------------------------------- #
# the cards
# --------------------------------------------------------------------------- #
S4 = H.states(D)
st.html(H.kpi_cards([
    ("Total Tickets", "file", "#1597FF", TOTAL, 100.0 if TOTAL else 0.0),
    ("Closed Tickets", "check", H.STATE_COLOUR[CLOSED], S4[CLOSED], H.pct(S4[CLOSED], TOTAL)),
    ("Pending Tickets", "clock", H.STATE_COLOUR[PENDING], S4[PENDING],
     H.pct(S4[PENDING], TOTAL)),
    ("In Progress", "reopen", "#F59E0B", S4[IN_PROGRESS], H.pct(S4[IN_PROGRESS], TOTAL)),
    ("Sleep Tickets", "sleep", H.STATE_COLOUR[SLEEP], S4[SLEEP], H.pct(S4[SLEEP], TOTAL)),
]))


def _share_table(s: pd.Series, name: str) -> pd.DataFrame:
    return pd.DataFrame({name: s.index, "Tickets": s.to_numpy(),
                         "Share (%)": [round(H.pct(v, TOTAL), 1) for v in s.to_numpy()]})


@st.dialog("Tickets by User", width="large")
def _users_dialog() -> None:
    st.dataframe(_share_table(H.by(D, "user", keep_empty=True), "User"),
                 hide_index=True, width="stretch", height=460)


@st.dialog("Ticket Status", width="large")
def _status_dialog() -> None:
    st.dataframe(_share_table(H.by(D, "status", keep_empty=True), "Status"),
                 hide_index=True, width="stretch")


@st.dialog("Tickets by Group", width="large")
def _group_dialog() -> None:
    st.dataframe(_share_table(H.by(D, "group", keep_empty=True), "Group"),
                 hide_index=True, width="stretch")


def _head(title: str, key: str, *, view_all=None, mode: bool = False) -> str:
    """A card's title bar: the title, and View All or Count / Percentage."""
    a, b = st.columns([1.3, 1] if mode else [2.6, 1], gap="small",
                      vertical_alignment="center")
    a.html(f'<div class="th-h" title="{H._esc(title)}">{H._esc(title)}</div>')
    if mode:
        with b:
            return st.segmented_control("Show", ["Count", "Percentage"], key=f"th_mode_{key}",
                                        default="Count", required=True,
                                        label_visibility="collapsed")
    if view_all is not None and b.button("View All", key=f"th_va_{key}", width="stretch"):
        view_all()
    return "Count"


def _missing(field: str) -> str:
    n = H.count(D[D[field].eq("")])
    return (f'<div class="th-note">{n:,} ticket{"s" if n != 1 else ""} with no '
            f'{H.EMPTY[field][3:]}</div>' if n else "")


# --------------------------------------------------------------------------- #
# first row: users, status, group
# --------------------------------------------------------------------------- #
c1, c2, c3 = st.columns(3, gap="small")
with c1, st.container(key="rf_card_th_users", border=True):
    _head("Tickets by User", "users", view_all=_users_dialog)
    top = H.top_with_others(H.by(D, "user"), 7)
    st.html(H.hbars(top.index, top.to_numpy(), row=28) + _missing("user"))

with c2, st.container(key="rf_card_th_status", border=True):
    _head(f"Ticket Status ({H.group_title(GROUPS)})", "status", view_all=_status_dialog)
    s = H.by(D, "status", keep_empty=True)
    parts = [(lab, int(k), H.status_colour(lab, i)) for i, (lab, k) in enumerate(s.items())]
    b = D.groupby("ticket_status")["hpsm_id"].nunique()
    st.html('<div class="th-dn">' + H.donut(parts, TOTAL) + H.legend(parts, TOTAL) + "</div>"
            + '<div class="th-sub">Ticket Status: '
            + " · ".join(f"{k or 'blank'} {v:,}" for k, v in b.sort_values(
                ascending=False).items()) + "</div>")

with c3, st.container(key="rf_card_th_group", border=True):
    _head("Tickets by Group", "group", view_all=_group_dialog)
    g = H.top_with_others(H.by(D, "group", keep_empty=True), 4)
    parts = list(zip(g.index, map(int, g.to_numpy()), H.group_colours(g.index)))
    st.html('<div class="th-dn">' + H.donut(parts, TOTAL) + H.legend(parts, TOTAL) + "</div>")


# --------------------------------------------------------------------------- #
# second row: the top sites; cities and Sup Districts; RF Analysis
# --------------------------------------------------------------------------- #
def _values(s: pd.Series, mode: str) -> list:
    return list(s.to_numpy()) if mode == "Count" else [H.pct(v, TOTAL) for v in s.to_numpy()]


left, right = st.columns([1, 2], gap="small")
with left, st.container(key="rf_card_th_sites", border=True):
    mode = _head("Top 20 Sites by Number of Tickets", "sites", mode=True)
    s = H.by(D, "site_id", drop=("0",)).head(20)
    st.html(H.hbars(s.index, _values(s, mode), row=26, as_pct=mode != "Count",
                    label_w=68))

with right:
    a, b = st.columns(2, gap="small")
    with a, st.container(key="rf_card_th_city", border=True):
        mode = _head("Tickets by City", "city", mode=True)
        s = H.by(D, "city")
        st.html(H.vbars(s.index, _values(s, mode), as_pct=mode != "Count")
                + _missing("city"))
    with b, st.container(key="rf_card_th_sd", border=True):
        mode = _head("Tickets by Sup District", "sd", mode=True)
        s = H.top_with_others(H.by(D, "sup_district"), 7)
        st.html(H.hbars(s.index, _values(s, mode), row=24.5, as_pct=mode != "Count")
                + _missing("sup_district"))
    with st.container(key="rf_card_th_rf", border=True):
        _head("Tickets by RF Analysis", "rf")
        s = H.by(D, "rf_analysis")
        st.html(H.vbars(s.index, s.to_numpy(), w=680, h=300, tilt=35)
                + _missing("rf_analysis"))

_footer()
