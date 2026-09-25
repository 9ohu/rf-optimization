"""Tickets Details — the second tab of History of Tickets.

One ticket of the R5 ticket history (the History ticket resource,
`rfopt.complaints.history`) in full — searched by its HPSM Incident ID,
Service Ticket ID or Site ID: its information as icon cards, its Diagnostic
Comment whole (with a Copy button), its planned site when it has one, its
times; and under it every ticket in the table whose columns filter like Excel
(`_ticket_table`), where a row clicked is the ticket shown.
"""

from __future__ import annotations

import base64
import html

import pandas as pd
import streamlit as st

import _ticket_table as TT
from _ui import ICONS as APP_ICONS

_S = ('viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" '
      'stroke-linejoin="round"')
# the few this page needs that the app has not drawn yet, in the app's stroke style
ICONS = {
    "hash": f'<svg {_S} stroke-width="1.9"><path d="M5 9h14M5 15h14M10 4 8 20M16 4l-2 16"/>'
            "</svg>",
    "list": f'<svg {_S} stroke-width="1.9"><rect x="4" y="3.5" width="16" height="17" rx="2"/>'
            '<path d="M8 8h8M8 12h8M8 16h5"/></svg>',
    "network": f'<svg {_S} stroke-width="1.9"><circle cx="12" cy="5.5" r="2.2"/>'
               '<circle cx="5.5" cy="17.5" r="2.2"/><circle cx="18.5" cy="17.5" r="2.2"/>'
               '<path d="M12 7.7V12m0 0-5 4m5-4 5 4"/></svg>',
    "users": f'<svg {_S} stroke-width="1.9"><circle cx="9" cy="8.5" r="3.2"/>'
             '<path d="M3 19.5a6 6 0 0 1 12 0"/><circle cx="16.5" cy="9" r="2.6"/>'
             '<path d="M15.6 14.2a5 5 0 0 1 5.4 5.3"/></svg>',
    "map": f'<svg {_S} stroke-width="1.9"><path d="m9 4-5 2v14l5-2 6 2 5-2V4l-5 2z"/>'
           '<path d="M9 4v14M15 6v14"/></svg>',
    "grid": f'<svg {_S} stroke-width="1.9"><rect x="4" y="4" width="6.5" height="6.5" rx="1.2"/>'
            '<rect x="13.5" y="4" width="6.5" height="6.5" rx="1.2"/>'
            '<rect x="4" y="13.5" width="6.5" height="6.5" rx="1.2"/>'
            '<rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.2"/></svg>',
    "affected": f'<svg {_S} stroke-width="1.9"><circle cx="11" cy="7.5" r="3.2"/>'
                '<path d="M4.5 20a6.5 6.5 0 0 1 13 0"/><path d="M19.5 4v4.5M19.5 11v.01"/></svg>',
    "antenna": f'<svg {_S} stroke-width="1.9"><path d="M12 11v10"/><path d="m8 21 4-10 4 10"/>'
               '<path d="M8.2 7.3a5.4 5.4 0 0 1 7.6 0"/><path d="M5.6 4.7a9 9 0 0 1 12.8 0"/>'
               '<circle cx="12" cy="10" r="1.3"/></svg>',
    "analysis": f'<svg {_S} stroke-width="1.9"><circle cx="10.5" cy="10.5" r="6.5"/>'
                '<path d="m20 20-4.6-4.6"/><path d="M7 11h1.8l1.2-2.6 1.6 4.4 1-1.8h1.6"/></svg>',
    "tag": f'<svg {_S} stroke-width="1.9"><path d="M3.5 12.2V4.5a1 1 0 0 1 1-1h7.7l8.3 8.3'
           'a1.4 1.4 0 0 1 0 2l-6.3 6.3a1.4 1.4 0 0 1-2 0z"/><circle cx="8" cy="8" r="1.5"/></svg>',
    "send": f'<svg {_S} stroke-width="1.9"><path d="M21 3 11.5 21l-2.3-7.7L2 11z"/>'
            '<path d="M21 3 9.2 13.3"/></svg>',
    "hourglass": f'<svg {_S} stroke-width="1.9"><path d="M7 3h10M7 21h10"/>'
                 '<path d="M8 3c0 4.5 8 5.5 8 9s-8 4.5-8 9M16 3c0 4.5-8 5.5-8 9s8 4.5 8 9"/></svg>',
    "calcheck": f'<svg {_S} stroke-width="1.9"><rect x="3.5" y="5" width="17" height="15" rx="2"/>'
                '<path d="M3.5 9.5h17M8 3v4M16 3v4"/><path d="m9 14.5 2 2 4-4"/></svg>',
    "copy": f'<svg {_S} stroke-width="1.9"><rect x="8" y="8" width="12" height="12" rx="2"/>'
            '<path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>',
}
BLUE, CYAN, GREEN, ORANGE, PURPLE, RED, AMBER, PINK = (
    "#1597FF", "#20BFFF", "#22C55E", "#F59E0B", "#A78BFA", "#EF4444", "#FB923C", "#F472B6")
STATUS_COLOUR = {"close": GREEN, "closed": GREEN, "sleep": PURPLE, "resolve": BLUE,
                 "pending": RED, "reopen": AMBER, "reject": PINK}
SLA_COLOUR = {"normal": GREEN, "sla_violation": RED}

# the ticket information cards: (field, label, icon, colour)
INFO = [
    ("hpsm_id", "HPSM Incident ID", "hash", ORANGE),
    ("service_ticket_id", "Service Ticket ID", "list", PURPLE),
    ("status", "Status", "chart", GREEN),
    ("sla_status", "SLA Status", "check", GREEN),
    ("is_cmc", "Is CMC", "network", BLUE),
    ("reopen", "Reopen Count", "reopen", PURPLE),
    ("group", "Group", "users", GREEN),
    ("user", "User", "user", BLUE),
    ("city", "City", "pin", CYAN),
    ("sup_district", "Sup District", "map", CYAN),
    ("site_id", "Site ID (SD check)", "tower", ORANGE),
    ("planned_site", "Planned Site ID", "grid", BLUE),
    ("affected", "Affected", "affected", BLUE),
    ("sector", "Sector Serving", "antenna", CYAN),
    ("rf_analysis", "RF Analysis", "analysis", AMBER),
    ("closure_code", "Closure Code", "tag", BLUE),
    ("latitude", "Latitude", "pin", BLUE),
    ("longitude", "Longitude", "pin", BLUE),
]
PLANNED = [
    ("closure_code", "Closure Code", "tag", BLUE),
    ("planned_site", "Planned Site ID", "tower", BLUE),
    ("longitude", "Longitude", "pin", BLUE),
    ("latitude", "Latitude", "pin", BLUE),
    ("expected", "Expected Resolution Date", "calendar", PINK),
]
TIMES = [
    ("diag_create", "Create Time", "calendar", BLUE),
    ("diag_submit", "Submit Time", "send", BLUE),
    ("sla_target", "SLA Target Time", "hourglass", PURPLE),
    ("expected", "Expected Resolution Date", "calendar", PINK),
    ("closure_time", "Closure Time", "calcheck", GREEN),
]
NONE = "-"

CSS = """
<style>
.td-sec-t { font-size: 17px; font-weight: 700; color: #F1F5F9; margin: 0 0 10px; }
.td-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(188px, 1fr));
    gap: 8px; }
.td-card { display: flex; align-items: center; gap: 11px; background: #071525;
    border: 1px solid #1E3A5F; border-radius: 10px; padding: 11px 11px; min-width: 0;
    min-height: 66px; }
.td-card > div:last-child { min-width: 0; }
.td-ico { flex: 0 0 38px; height: 38px; border-radius: 9px; display: flex;
    align-items: center; justify-content: center;
    background: linear-gradient(135deg, color-mix(in srgb, var(--c) 34%, #0B1F33),
                                color-mix(in srgb, var(--c) 10%, #071525));
    border: 1px solid color-mix(in srgb, var(--c) 50%, transparent);
    box-shadow: 0 0 14px color-mix(in srgb, var(--c) 28%, transparent); }
.td-lbl { font-size: 11.5px; color: #94A3B8; white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }
.td-val { font-size: 15px; font-weight: 600; color: #F1F5F9; margin-top: 3px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.td-val.hl { color: #D8B4FE; }
.td-pill { display: inline-block; padding: 1px 10px; border-radius: 6px; font-size: 12.5px;
    font-weight: 700; color: #FFFFFF; background: var(--p); }
.td-times { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; }
.td-times .td-card { padding: 11px 12px; gap: 10px; }
.td-times .td-val { font-size: 14.5px; }
@media (max-width: 1250px) { .td-times { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
.td-plan { display: grid; grid-template-columns: minmax(0, 1fr); gap: 8px; }
.td-cm-h { display: flex; align-items: center; justify-content: space-between; gap: 10px;
    margin-bottom: 10px; }
.td-cm-h .td-sec-t { margin: 0; }
.td-copy { display: inline-flex; align-items: center; gap: 7px; padding: 5px 13px;
    border-radius: 8px; border: 1px solid #2A4A6F; background: #0D2945; color: #E2E8F0;
    font: 600 13px 'Segoe UI', system-ui, sans-serif; cursor: pointer; }
.td-copy:hover { border-color: #1597FF; background: #15406B; }
.td-copy[data-state] { border-color: #22C55E; color: #22C55E; }
.td-cm { background: #071525; border: 1px solid #2A4A6F; border-radius: 10px;
    padding: 12px 15px; font-size: 14.5px; line-height: 1.6; color: #E2E8F0;
    white-space: pre-wrap; overflow-wrap: anywhere; overflow-y: auto; max-height: 250px;
    min-height: 120px; }
.td-cm.tall { max-height: 400px; }
.td-cm.empty { color: #64748B; font-style: italic; }
.td-note { color: #94A3B8; font-size: 12.5px; }
.td-none { color: #94A3B8; font-size: 13.5px; padding: 26px 0; text-align: center; }
</style>
"""

COPY_JS = """
<script>
(function () {
  var W = window, D = document;
  if (W.tdCopyReady) return;
  W.tdCopyReady = true;
  D.addEventListener('click', function (ev) {
    var b = ev.target && ev.target.closest && ev.target.closest('button.td-copy');
    if (!b) return;
    ev.preventDefault();
    var text = b.getAttribute('data-copy') || '';
    var label = b.querySelector('span'), idle = label.textContent;
    function show(msg) {
      label.textContent = msg; b.dataset.state = msg;
      setTimeout(function () { label.textContent = idle; delete b.dataset.state; }, 1800);
    }
    function fallback() {
      var t = D.createElement('textarea');
      t.value = text; t.style.position = 'fixed'; t.style.opacity = '0';
      D.body.appendChild(t); t.select();
      try { D.execCommand('copy'); show('Copied'); } catch (e) { show('Not copied'); }
      t.remove();
    }
    if (navigator.clipboard && W.isSecureContext) {
      navigator.clipboard.writeText(text).then(function () { show('Copied'); }, fallback);
    } else {
      fallback();
    }
  }, true);
})();
</script>
"""


def _esc(x) -> str:
    return html.escape(str(x))


def icon(name: str, colour: str, size: int = 20) -> str:
    svg = ICONS.get(name) or APP_ICONS.get(name) or APP_ICONS["info"]
    svg = svg.replace("currentColor", colour).replace(
        "<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1)
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<img src="data:image/svg+xml;base64,{b64}" width="{size}" height="{size}" alt="">'


def text(v, field: str = "") -> str:
    """A value as the page shows it: a time to the minute, an empty one as -."""
    if v is None:
        return NONE
    try:
        if pd.isna(v):                      # NaT, NaN
            return NONE
    except (TypeError, ValueError):
        pass
    if isinstance(v, pd.Timestamp):
        return f"{v:%Y-%m-%d %H:%M}"
    s = str(v).strip()
    if not s:
        return NONE
    if field == "is_cmc":
        return {"true": "Yes", "false": "No"}.get(s.lower(), s)
    return s


def _value(row, field: str) -> str:
    v = text(row.get(field), field)
    if field == "status" and v != NONE:
        return f'<span class="td-pill" style="--p:{STATUS_COLOUR.get(v.lower(), BLUE)}">{_esc(v)}</span>'
    if field == "sla_status" and v != NONE:
        return f'<span class="td-pill" style="--p:{SLA_COLOUR.get(v.lower(), BLUE)}">{_esc(v)}</span>'
    return _esc(v)


def cards(row, items, cls: str = "td-grid", highlight=(), wide=()) -> str:
    """Icon cards: (field, label, icon, colour) of one ticket; `wide` ones take
    a whole row."""
    out = []
    for field, label, ic, colour in items:
        hl = " hl" if field in highlight and text(row.get(field), field) != NONE else ""
        tip = _esc(text(row.get(field), field))
        w = " wide" if field in wide else ""
        out.append(f'<div class="td-card{w}" style="--c:{colour}"><div class="td-ico">'
                   f'{icon(ic, colour, 21)}</div><div><div class="td-lbl">{_esc(label)}</div>'
                   f'<div class="td-val{hl}" title="{tip}">{_value(row, field)}</div></div></div>')
    return f'<div class="{cls}">' + "".join(out) + "</div>"


def comment_box(comment: str, tall: bool = False) -> str:
    """The Diagnostic Comment, whole, in its own box (taller when no planned site
    shares the column), with Copy."""
    c = (comment or "").strip()
    body = (f'<div class="td-cm{" tall" if tall else ""}">{_esc(c)}</div>' if c
            else '<div class="td-cm empty">No Diagnostic Comment for this ticket.</div>')
    button = (f'<button class="td-copy" data-copy="{_esc(c)}" title="Copy the whole comment">'
              f'{icon("copy", "#E2E8F0", 16)}<span>Copy</span></button>' if c else "")
    return (f'<div class="td-cm-h"><div class="td-sec-t">Diagnostic Comment</div>'
            f"{button}</div>{body}")


def is_planned(row) -> bool:
    """A planned-site / Sleep ticket: it names a planned site (BP) or it sleeps."""
    return bool(str(row.get("planned_site") or "").strip()) or \
        str(row.get("status") or "").strip().lower() == "sleep"


# --------------------------------------------------------------------------- #
# finding tickets
# --------------------------------------------------------------------------- #
def search(df: pd.DataFrame, q: str) -> pd.DataFrame:
    """The tickets a search names: an exact HPSM Incident ID, Service Ticket ID or
    Site ID first, else any of the three that contains the text."""
    q = (q or "").strip().upper()
    if not q:
        return df.iloc[0:0]
    keys = [df[f].astype(str).str.upper() for f in ("hpsm_id", "service_ticket_id", "site_id")]
    exact = keys[0].eq(q) | keys[1].eq(q) | keys[2].eq(q)
    if exact.any():
        return df[exact]
    part = (keys[0].str.contains(q, regex=False) | keys[1].str.contains(q, regex=False)
            | keys[2].str.contains(q, regex=False))
    return df[part]


def newest_first(df: pd.DataFrame) -> pd.DataFrame:
    return df.sort_values("create_time", ascending=False, na_position="last", kind="stable")


def render(df: pd.DataFrame, file) -> None:
    """The tab: the search; the ticket — the one picked, else the search's most
    recent, else the file's — in full; every ticket (or those the search found)
    in the table. `file` is the History ticket file `df` was read from."""
    ss = st.session_state
    st.html(CSS)
    st.html(COPY_JS, unsafe_allow_javascript=True)

    def _search() -> None:
        ss["td_q"] = (ss.get("td_q_in") or "").strip()
        ss.pop("td_open", None)

    # the search runs on Enter, on the button, or when the box is left — no
    # form to submit — and the browser's list of earlier entries stays shut
    # (Enter on it fills the box without searching)
    if "td_q_in" not in ss:
        ss["td_q_in"] = ss.get("td_q", "")
    with st.container(key="rf_card_td_search", border=True):
        a, b, c, d = st.columns([1.35, 3.3, 0.5, 4.9], gap="small",
                                vertical_alignment="center")
        a.html('<div class="td-sec-t" style="margin:0;white-space:nowrap">Search Ticket</div>')
        b.text_input("Search", key="td_q_in", label_visibility="collapsed", autocomplete="off",
                     on_change=_search,
                     placeholder="HPSM Incident ID, Service Ticket ID or Site ID")
        c.button(":material/search:", key="td_go", type="primary", width="stretch",
                 on_click=_search, help="Search")
        d.html('<div class="td-note">By HPSM Incident ID (IM…), Service Ticket ID (CC-…) '
               "or Site ID — a site shows its most recent ticket, and all of its tickets "
               "in the table below.</div>")

    q = ss.get("td_q", "")
    found = newest_first(search(df, q)) if q else None
    # a ticket picked in the table is shown only while it is one of the tickets
    # the table holds (the search's, else every one): a new search always shows
    # a ticket it found
    pool = found if q else df
    picked = pool[pool["hpsm_id"] == ss["td_open"]] if ss.get("td_open") else pool.iloc[0:0]
    if ss.get("td_open") and picked.empty:
        ss.pop("td_open", None)
    if len(picked):
        cur = picked.iloc[0]
    elif len(pool):
        cur = pool.iloc[0] if q else newest_first(pool).iloc[0]
    else:
        cur = None
    if q and not len(found):
        st.warning(f"No ticket found for “{q}” — search an HPSM Incident ID, Service Ticket ID "
                   "or Site ID.", icon=":material/search_off:")
    elif q and len(found) > 1 and picked.empty:
        st.caption(f"{len(found):,} tickets match “{q}”: the most recent is shown — pick "
                   "another in the table below.")

    left, right = st.columns([2.6, 1.15], gap="small")
    with left, st.container(key="rf_card_td_info", border=True):
        st.html('<div class="td-sec-t">Ticket Information</div>'
                + (cards(cur, INFO) if cur is not None
                   else '<div class="td-none">No ticket to show.</div>'))
    with right:
        with st.container(key="rf_card_td_comment", border=True):
            st.html(comment_box(cur["comment"] if cur is not None else "",
                                tall=cur is None or not is_planned(cur)))
        if cur is not None and is_planned(cur):
            with st.container(key="rf_card_td_plan", border=True):
                st.html('<div class="td-sec-t">Planned Site Information</div>'
                        + cards(cur, PLANNED, cls="td-plan", highlight=("expected",)))

    with st.container(key="rf_card_td_times", border=True):
        st.html('<div class="td-sec-t">Time Information</div>'
                + (cards(cur, TIMES, cls="td-times", highlight=("sla_target", "expected"))
                   if cur is not None else '<div class="td-none">No ticket to show.</div>'))

    # every ticket, or those the search found: each column filtered in its header
    hits = found is not None and len(found) > 0
    with st.container(key="rf_card_td_table", border=True):
        TT.show(df, file.sha1, found=found if hits else None, query=q if hits else "",
                open_id=cur["hpsm_id"] if cur is not None else None,
                title="All Tickets (Search Results)" if hits else "All Tickets")
