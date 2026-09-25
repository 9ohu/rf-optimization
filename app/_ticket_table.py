"""The ticket table: every ticket, each column filtered like Excel.

A custom component (`assets/ticket_table.js` and `.css`). Every column has its
own filter in its header — sort it, search its values, tick the ones to keep —
the table scrolls sideways under its header, shows a page at a time and
exports the rows it holds as an Excel file. A row clicked is the ticket shown
beside the table; a comment column opens whole, with Copy.

Tickets Details shows `COLUMNS`; another page passes its own to `show()` and
says which session key the clicked ticket goes to. Nothing else differs.

The rows go to the browser once per file and session; after that only which of
them a search found. Filtering, sorting, paging and the export all happen in
the browser and never wait on Python.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path

import pandas as pd
import streamlit as st

_ASSETS = Path(__file__).resolve().parent / "assets"

# the columns, in this order: (field, header, kind, width in px). kind: text,
# num (sorts as a number), time (a date and time; filtered by its day), status,
# sla, verdict and air (coloured labels), comment (opens whole)
COLUMNS = [
    ("hpsm_id", "HPSM Incident ID", "text", 172),
    ("service_ticket_id", "Service Ticket ID", "text", 186),
    ("site_id", "Site ID", "text", 108),
    ("city", "City", "text", 108),
    ("sup_district", "Sup District", "text", 164),
    ("group", "Group", "text", 106),
    ("user", "User", "text", 184),
    ("ticket_status", "Status", "status", 118),
    ("sla_status", "SLA Status", "sla", 132),
    ("is_cmc", "Is CMC", "text", 110),
    ("reopen", "Reopen Count", "num", 152),
    ("affected", "Affected", "text", 136),
    ("sector", "Sector Serving", "text", 154),
    ("rf_analysis", "RF Analysis", "text", 184),
    ("longitude", "Longitude", "num", 128),
    ("latitude", "Latitude", "num", 116),
    ("planned_site", "Planned Site ID", "text", 158),
    ("diag_create", "Create Time", "time", 152),
    ("diag_submit", "Submit Time", "time", 152),
    ("sla_target", "SLA Target Time", "time", 164),
    ("expected", "Expected Resolution Date", "time", 220),
    ("closure_time", "Closure Time", "time", 152),
    ("closure_code", "Closure Code", "text", 236),
    ("comment", "Diagnostic Comment", "comment", 380),
]
LAYOUT = 1          # how the rows are sent: a change sends them anew

_NAME = "rf_ticket_table"
_PARTS = {"html": '<div class="tg">\n</div>\n',
          "css": (_ASSETS / "ticket_table.css").read_text(encoding="utf-8"),
          "js": (_ASSETS / "ticket_table.js").read_text(encoding="utf-8")}
_TABLE = st.components.v2.component(_NAME, **_PARTS)


def _table():
    """The component, declared once at import — and again only when the running
    app does not hold it (a runtime started after the import, as every AppTest
    starts its own)."""
    global _TABLE
    try:
        from streamlit.components.v2.get_bidi_component_manager import (
            get_bidi_component_manager)
        if get_bidi_component_manager().get(_NAME) is None:
            _TABLE = st.components.v2.component(_NAME, **_PARTS)
    except ImportError:
        pass
    return _TABLE


def _strings(s: pd.Series, field: str) -> list[str]:
    """A column as the browser gets it: a time to the second, Is CMC as Yes /
    No, an empty value as ""."""
    if pd.api.types.is_datetime64_any_dtype(s):
        return s.dt.strftime("%Y-%m-%d %H:%M:%S").fillna("").tolist()
    out = s.fillna("").astype(str).str.strip()
    if field == "is_cmc":
        out = out.map(lambda v: {"true": "Yes", "false": "No"}.get(v.lower(), v))
    return out.tolist()


def _encoded(values: list[str]) -> dict:
    """A column whose values repeat goes as its values once and a number per row."""
    codes, uniques = pd.factorize(pd.Series(values, dtype=object))
    if 2 * len(uniques) < len(values):
        return {"d": [str(u) for u in uniques], "i": codes.tolist()}
    return {"v": values}


def rows_of(df: pd.DataFrame, columns: list | None = None,
            sort_field: str = "create_time") -> tuple[dict, pd.Series]:
    """The table's rows, the most recent ticket first, column by column; and
    where each row of `df` (by its index) sits among them."""
    columns = columns or COLUMNS
    ordered = df.sort_values(sort_field, ascending=False, na_position="last", kind="stable")
    payload = {"n": len(ordered),
               "c": {field: _encoded(_strings(ordered[field], field)) for field, *_ in columns}}
    return payload, pd.Series(range(len(ordered)), index=ordered.index)


@st.cache_resource(show_spinner=False, max_entries=4)
def _rows(sha1: str, _df: pd.DataFrame, _columns: tuple | None = None,
          sort_field: str = "create_time") -> tuple[dict, pd.Series]:
    return rows_of(_df, list(_columns) if _columns else None, sort_field)


def _opened(key: str, open_key: str = "td_open") -> None:
    """A row clicked: that ticket is the one the page shows."""
    ticket = (st.session_state.get(key) or {}).get("open")
    if ticket:
        st.session_state[open_key] = ticket


def _needed(key: str) -> None:
    """The browser does not hold the rows (its page was reloaded): send them."""
    st.session_state.pop(f"{key}_sent", None)


def show(df: pd.DataFrame, sha1: str, *, found: pd.DataFrame | None = None, query: str = "",
         open_id: str | None = None, title: str = "All Tickets", key: str = "td_table",
         columns: list | None = None, open_key: str = "td_open",
         sort_field: str = "create_time") -> None:
    """Every ticket of `df` (the file with this sha1), or only those `found` by
    the search; the ticket `open_id` marked."""
    columns = columns or COLUMNS
    rows, place = _rows(sha1, df, tuple(tuple(c) for c in columns), sort_field)
    version = f"{sha1[:16]}.{LAYOUT}"
    held = st.session_state.get(f"{key}_sent") == version
    subset = sorted(int(p) for p in place.reindex(found.index).dropna()) if found is not None \
        else None
    _table()(key=key,
             data={"key": key, "v": version, "cols": [list(c) for c in columns],
                   "idf": "hpsm_id", "rows": None if held else rows, "subset": subset,
                   "q": query, "open": open_id, "title": title},
             on_open_change=partial(_opened, key, open_key),
             on_need_change=partial(_needed, key), width="stretch")
    st.session_state[f"{key}_sent"] = version
