"""Complaints · History of Tickets, its Tickets Details tab: one R5 ticket in
full, and every ticket in a table whose columns filter like Excel.

The ticket history (History ticket resource) read with the columns the R5 team
named — Group A, User M, Site ID I, Planned Site ID BP, Sector Serving FB, RF
Analysis FE, Closure Code BQ, Latitude FD, Longitude FC, Diagnostic Comment BS,
Create Time BT — and the rest by header; the page never writes a column's
letter. Search by HPSM Incident ID, Service Ticket ID or Site ID; the Planned
Site section only for a planned-site / Sleep ticket; the SLA Target Time as the
date and time itself. The table (a custom component, `_ticket_table`) gets the
24 columns, the rows once per file and session, then only which of them a
search found; a row clicked there is the ticket shown. Every test runs on its
own empty store (conftest).
"""

import io
import json
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from rfopt.complaints.history import parse_history  # noqa: E402

AT = {"A": "Group", "B": "Ticket Status", "D": "SubmitTime(incident diagnostic)",
      "F": "Ticket ID", "G": "SLA Target Time", "H": "City",
      "I": "Site ID(SD Check_site_id)", "J": "SLA Status", "K": "IS CMC",
      "L": "Reopen Count", "M": "User", "N": "Created At", "Z": "Sub District",
      "AR": "Affected Services", "BF": "HPSM Incident ID", "BM": "Expected Resolution Time",
      "BP": "Site ID(SD Check)", "BQ": "Closure Code(Incident Diagnostic)",
      "BS": "Diagnostic Comment(Incident Diagnostic)",
      "BT": "CreateTime(incident diagnostic)", "DG": "Closure Time", "ER": "Status",
      "FB": "Sector Serving", "FC": "Longitude", "FD": "Latitude", "FE": "RF Analysis"}
COMMENT = ("Root cause: High Utilization \nCustomer issue: Data speed \nCustomer serving "
           "from site BAS1668-2 which is suffering from High utilization\nActions: pending "
           "to Expansion for serving site\nCustomer location: (30.022275   47.950642)")
TICKETS = [
    dict(A=" RF", B="Running", D="2026-01-03 19:16:54", F="CC-20260102-00000723",
         G="2026-01-04 23:10:29", H="Basrah", I="BAS1668", J="normal", K="false", L=None,
         M="hw.mahmoud.dhari.essa", N="2026-01-02 23:31:03", Z="Zubair center",
         AR="Data Service", BF="IM6317044", BM="2026-06-23 19:04:37", BP="BAS1668",
         BQ="Data - High utilization cells", BS=COMMENT, BT="2026-01-02 23:56:30",
         DG=None, ER="Sleep", FB="BAS1668-2", FC="47.950642", FD="30.022275",
         FE="PRB Utilization"),
    dict(A="SOC", B="Completed", D="2026-03-26 13:15:00", F="CC-20260326-00000001",
         G="2026-03-28 00:49:00", H="Maysan / Emarah", I="EMA0001", J="sla_violation",
         K="true", L="1", M="hw.karzan.abdullah.ali", N="2026-03-26 12:00:00",
         Z="Maymona center", AR="Voice Service", BF="IM6409604", BM=None, BP=None,
         BQ="Resolved Successfully", BS=None, BT="2026-03-26 13:03:00",
         DG="2026-03-29 10:17:00", ER="Close", FB=None, FC=None, FD=None, FE="Others"),
    dict(A=" RF", B="Completed", D=None, F="CC-20260910-00000050", G="2026-09-12 08:00:00",
         H="Basrah", I="BAS1668", J="normal", K="false", L="2", M="hw.shams.aldin.ali",
         N="2026-09-10 08:00:00", Z="Zubair center", AR="Data Service", BF="IM6650000",
         BM=None, BP=None, BQ="No Problem Found", BS="Checked, no RF issue.", BT=None,
         DG="2026-09-11 09:00:00", ER="Close", FB="BAS1668-1", FC="47.95", FD="30.02",
         FE="Physical Optimization"),
]
HEADERS = ["HPSM Incident ID", "Service Ticket ID", "Site ID", "City", "Sup District", "Group",
           "User", "Status", "SLA Status", "Is CMC", "Reopen Count", "Affected",
           "Sector Serving", "RF Analysis", "Longitude", "Latitude", "Planned Site ID",
           "Create Time", "Submit Time", "SLA Target Time", "Expected Resolution Date",
           "Closure Time", "Closure Code", "Diagnostic Comment"]
LETTER = re.compile(r"\((?:[A-Z]{1,2}|M User|column [A-Z]{1,2})\)")    # "(BP)", "(M User)"


def _index(letter: str) -> int:
    n = 0
    for ch in letter:
        n = n * 26 + ord(ch) - 64
    return n - 1


def _export(rename: dict | None = None) -> pd.DataFrame:
    """A CC Process-shaped sheet: 161 columns, the named ones where they sit."""
    heads = [f"Filler {j}" for j in range(161)]
    for letter, name in AT.items():
        heads[_index(letter)] = (rename or {}).get(name, name)
    data = {h: [None] * len(TICKETS) for h in heads}
    for i, t in enumerate(TICKETS):
        for letter, v in t.items():
            data[heads[_index(letter)]][i] = v
    return pd.DataFrame(data, columns=heads)


def _xlsx(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False, sheet_name="sheet1")
    return buf.getvalue()


def _column(payload: dict, field: str) -> list:
    """A column of the rows sent to the browser, as the browser reads it."""
    c = payload["c"][field]
    return c["v"] if "v" in c else [c["d"][i] for i in c["i"]]


# --------------------------------------------------------------------------- #
# the ticket in full
# --------------------------------------------------------------------------- #
def test_a_ticket_is_read_in_full_from_the_named_columns():
    t = parse_history(_export()).set_index("hpsm_id")
    s = t.loc["IM6317044"]
    assert (s["planned_site"], s["closure_code"], s["sector"], s["rf_analysis"]) == (
        "BAS1668", "Data - High utilization cells", "BAS1668-2", "PRB Utilization")
    assert (s["latitude"], s["longitude"]) == ("30.022275", "47.950642")
    assert s["comment"] == COMMENT.strip()
    assert s["diag_create"] == pd.Timestamp("2026-01-02 23:56:30")     # Create Time (BT)
    assert s["diag_submit"] == pd.Timestamp("2026-01-03 19:16:54")
    assert s["sla_target"] == pd.Timestamp("2026-01-04 23:10:29")      # a date, not "48h"
    assert s["expected"] == pd.Timestamp("2026-06-23 19:04:37")
    assert (s["service_ticket_id"], s["sla_status"], s["is_cmc"], s["affected"]) == (
        "CC-20260102-00000723", "normal", "false", "Data Service")
    assert pd.isna(t.loc["IM6409604", "expected"]) and t.loc["IM6409604", "reopen"] == "1"
    # a named column is still found at its letter when its header reads otherwise
    moved = parse_history(_export({"RF Analysis": "RF analysis (team)",
                                   "Site ID(SD Check)": "Planned site"})).set_index("hpsm_id")
    assert moved.loc["IM6317044", "rf_analysis"] == "PRB Utilization"
    assert moved.loc["IM6317044", "planned_site"] == "BAS1668"


def test_the_search_finds_a_ticket_by_any_of_its_three_ids():
    import _ticket_details as T
    t = parse_history(_export())
    assert list(T.search(t, "im6317044")["hpsm_id"]) == ["IM6317044"]
    assert list(T.search(t, "CC-20260326-00000001")["hpsm_id"]) == ["IM6409604"]
    assert list(T.newest_first(T.search(t, "BAS1668"))["hpsm_id"]) == ["IM6650000", "IM6317044"]
    assert list(T.search(t, "6409")["hpsm_id"]) == ["IM6409604"]          # part of an ID
    assert T.search(t, "NOPE").empty and T.search(t, "").empty


def test_values_read_as_the_page_shows_them_with_no_column_letters():
    import _ticket_details as T
    t = parse_history(_export()).set_index("hpsm_id")
    sleep, done = t.loc["IM6317044"], t.loc["IM6409604"]
    assert T.is_planned(sleep) and not T.is_planned(done)
    assert T.text(sleep["sla_target"]) == "2026-01-04 23:10"
    assert T.text(sleep["closure_time"]) == "-" and T.text(sleep["reopen"]) == "-"
    assert T.text(done["is_cmc"], "is_cmc") == "Yes" and T.text(sleep["is_cmc"], "is_cmc") == "No"
    html = T.cards(sleep, T.INFO)
    for label in ("HPSM Incident ID", "Service Ticket ID", "Status", "SLA Status", "Is CMC",
                  "Reopen Count", "Group", "User", "City", "Sup District", "Site ID (SD check)",
                  "Planned Site ID", "Affected", "Sector Serving", "RF Analysis",
                  "Closure Code", "Latitude", "Longitude"):
        assert f'td-lbl">{label}<' in html, label
    labels = [lab for _, lab, _, _ in T.INFO + T.PLANNED + T.TIMES]
    assert not [lab for lab in labels if LETTER.search(lab)], labels
    assert "Create Time" in labels
    box = T.comment_box(sleep["comment"])
    assert ">Diagnostic Comment<" in box and "(BS)" not in box
    assert 'class="td-copy"' in box and "Root cause: High Utilization" in box
    import html as h
    copied = box.split('data-copy="')[1].split('"')[0]
    assert h.unescape(copied) == COMMENT.strip()                      # the whole comment
    assert "No Diagnostic Comment" in T.comment_box("") and "td-copy" not in T.comment_box("")


# --------------------------------------------------------------------------- #
# the table: its columns, and the rows the browser gets
# --------------------------------------------------------------------------- #
def test_the_table_has_every_column_the_team_named_in_order():
    import _ticket_table as TT
    assert [h for _, h, _, _ in TT.COLUMNS] == HEADERS
    assert not [h for h in HEADERS if LETTER.search(h)]
    kinds = {f: k for f, _, k, _ in TT.COLUMNS}
    assert kinds["ticket_status"] == "status"                  # Status is the column B one
    assert [f for f, k in kinds.items() if k == "time"] == [
        "diag_create", "diag_submit", "sla_target", "expected", "closure_time"]
    assert {kinds["reopen"], kinds["longitude"], kinds["latitude"]} == {"num"}
    assert kinds["comment"] == "comment"


def test_the_rows_go_to_the_browser_as_the_table_shows_them():
    import _ticket_table as TT
    t = parse_history(_export())
    payload, place = TT.rows_of(t)
    assert payload["n"] == 3 and set(payload["c"]) == {f for f, *_ in TT.COLUMNS}
    assert _column(payload, "hpsm_id") == ["IM6650000", "IM6409604", "IM6317044"]  # newest first
    assert _column(payload, "ticket_status") == ["Completed", "Completed", "Running"]
    assert _column(payload, "sla_target") == ["2026-09-12 08:00:00", "2026-03-28 00:49:00",
                                              "2026-01-04 23:10:29"]   # the date and time itself
    assert _column(payload, "expected") == ["", "", "2026-06-23 19:04:37"]
    assert _column(payload, "is_cmc") == ["No", "Yes", "No"]
    assert _column(payload, "reopen") == ["2", "1", ""]
    assert _column(payload, "comment")[2] == COMMENT.strip()        # whole, for Copy and export
    assert _column(payload, "city") == ["Basrah", "Amarah", "Basrah"]
    # a value that repeats is sent once, with a number per row
    assert TT._encoded(["a", "b", "a", "a", "a"]) == {"d": ["a", "b"], "i": [0, 1, 0, 0, 0]}
    assert TT._encoded(["a", "b", "c"]) == {"v": ["a", "b", "c"]}
    # where each ticket of the frame sits among the rows
    assert [int(place[i]) for i in t.index] == [2, 1, 0]


def test_a_row_clicked_is_the_ticket_shown(monkeypatch):
    import _ticket_table as TT
    state = {"td_table": {"open": "IM6409604"}, "td_table_sent": "x.1"}
    monkeypatch.setattr(TT.st, "session_state", state)
    TT._opened("td_table")
    assert state["td_open"] == "IM6409604"
    state["td_table"] = {"open": None}
    state["td_open"] = "IM6409604"
    TT._opened("td_table")                        # no click: the ticket stays
    assert state["td_open"] == "IM6409604"
    TT._needed("td_table")                        # the browser lost the rows: send them again
    assert "td_table_sent" not in state


# --------------------------------------------------------------------------- #
# the tab
# --------------------------------------------------------------------------- #
def _upload():
    import _resources as R
    from rfopt.resources import store as S
    _, bad = R.stage_files("history", [("CC Process_20260917175109.xlsx", _xlsx(_export()))])
    assert not bad
    S.apply("history")


def _page():
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    at = AppTest.from_file(str(APP / "views/ticket_history.py"), default_timeout=240)
    at.run()
    assert not at.exception, at.exception
    at.segmented_control(key="th_view").set_value("Tickets Details").run()
    assert not at.exception, at.exception
    return at


def _html(at) -> str:
    return " ".join(e.proto.body for e in at.get("html"))


def _table(at) -> dict:
    return json.loads(at.get_by_key("td_table").proto.json)


def _search(at, q: str):
    at.text_input(key="td_q_in").set_value(q)
    next(b for b in at.button if b.label == ":material/search:").click().run()
    assert not at.exception, at.exception


def test_the_tab_shows_a_ticket_in_full_and_every_ticket_in_the_table():
    _upload()
    at = _page()
    html = _html(at)
    assert 'title="IM6650000"' in html                          # the most recent ticket
    assert "Planned Site Information" not in html                # a normal ticket
    assert ">Time Information<" in html and ">Diagnostic Comment<" in html
    assert not LETTER.search(html)                               # no column letters anywhere
    assert not at.dataframe and not at.selectbox                 # no filter panel, no grid
    data = _table(at)
    assert [c[1] for c in data["cols"]] == HEADERS
    assert data["rows"]["n"] == 3 and data["subset"] is None and data["title"] == "All Tickets"
    assert data["open"] == "IM6650000"                           # the ticket shown, marked
    at.run()                                                     # the browser holds the rows now
    assert _table(at)["rows"] is None and _table(at)["v"] == data["v"]

    _search(at, "IM6317044")
    html = _html(at)
    assert "Planned Site Information" in html and 'title="BAS1668"' in html
    assert 'title="2026-01-04 23:10"' in html and "48" not in html.split(
        "SLA Target Time")[1][:120]                             # the date itself
    assert 'title="2026-06-23 19:04"' in html                   # Expected Resolution Date
    assert "Root cause: High Utilization" in html and 'class="td-copy"' in html
    data = _table(at)
    assert data["subset"] == [2] and data["title"] == "All Tickets (Search Results)"
    assert data["rows"] is None                                  # only which rows, not the rows

    _search(at, "BAS1668")                                      # a site: both its tickets
    assert _table(at)["subset"] == [0, 2] and 'title="IM6650000"' in _html(at)
    assert any("2 tickets match" in c.value for c in at.caption)
    _search(at, "NOPE")
    assert any("No ticket found" in w.value for w in at.warning)
    assert _table(at)["subset"] is None                          # nothing found: every ticket

    _search(at, "")
    at.session_state["td_open"] = "IM6409604"                    # as a row clicked sets it
    at.run()
    assert 'title="IM6409604"' in _html(at) and _table(at)["open"] == "IM6409604"


def test_a_new_search_always_shows_a_ticket_it_found():
    """The user's report: a second search kept showing the first ticket."""
    _upload()
    at = _page()
    box = at.text_input(key="td_q_in")
    assert box.proto.autocomplete == "off"          # no browser list to swallow Enter
    box.set_value("IM6317044").run()                # Enter alone searches, no button
    assert not at.exception, at.exception
    assert _table(at)["open"] == "IM6317044"
    at.text_input(key="td_q_in").set_value("IM6409604").run()
    assert _table(at)["open"] == "IM6409604" and 'title="IM6409604"' in _html(at)
    # a ticket picked in the table before, left over from another search, is
    # never shown in place of what the search found
    at.session_state["td_open"] = "IM6650000"
    at.session_state["td_q"] = "IM6317044"
    at.run()
    assert _table(at)["open"] == "IM6317044" and 'title="IM6317044"' in _html(at)
    assert "td_open" not in at.session_state
    # a ticket picked among the search's own tickets is shown
    _search(at, "BAS1668")
    at.session_state["td_open"] = "IM6317044"
    at.run()
    assert _table(at)["open"] == "IM6317044"
    _search(at, "IM6409604")                        # and a new search replaces it
    assert _table(at)["open"] == "IM6409604"


def test_tickets_details_is_a_tab_of_history_of_tickets_not_a_page():
    text = (APP / "Home.py").read_text(encoding="utf-8")
    block = text.split('"Complaints": [')[1].split("],")[0]
    assert 'title="History of Tickets"' in block and "Tickets Details" not in block
    assert not (APP / "views" / "ticket_details.py").exists()
