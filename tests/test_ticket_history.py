"""Complaints · History of Tickets, and the History ticket resource behind it.

The R5 ticket history (the CC Process export) is uploaded in Data Resources as
History ticket and read by its headers — or by the columns the R5 team named
(B Ticket Status, H City, I Site ID, M User, BF HPSM Incident ID, ER Status)
when a header differs. Tickets are counted by HPSM Incident ID; Closed, Pending,
In Progress and Sleep add up to the total; the filters take any number of
values each and a Time Period, and take effect on Apply; RF Analysis gets its
own column chart; Tickets Details is the page's second tab. Every test runs on
its own empty store (conftest), never the user's.
"""

import io
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from rfopt.complaints.history import (CLOSED, IN_PROGRESS, PENDING, SLEEP,  # noqa: E402
                                      HistoryFormatError, city_name, columns_of,
                                      parse_history, user_name)

# the export's own headers, at the columns they sit in
AT = {"A": "Group", "B": "Ticket Status", "H": "City", "I": "Site ID(SD Check_site_id)",
      "M": "User", "N": "Created At", "Z": "Sub District", "BF": "HPSM Incident ID",
      "DG": "Closure Time", "ER": "Status", "FE": "RF Analysis"}
ROWS = [  # group, ticket status, city, site, user, created, sub district, HPSM, closed, status,
    #       RF analysis
    ("RF", "Completed", "Basrah", "BAS0001", "hw.shams.aldin.ali", "2026-09-01 10:00:00",
     "Zubair center", "IM1", "2026-09-02 10:00:00", "Close", "PRB Utilization"),
    ("RF", "Completed", "Basrah", "BAS0001", "hw.shams.aldin.ali", "2026-09-02 10:00:00",
     "Zubair center", "IM2", "2026-09-03 09:00:00", "Close", "PRB Utilization"),
    ("SOC", "Running", "Maysan / Emarah", "EMA0001", "hw.mahmoud.dhari.essa",
     "2026-09-03 11:00:00", "Maymona center", "IM3", None, "Sleep", "New Site Required"),
    ("SOC", "Running", "Thaiqar / Nassriya", "NAS0001", "hw.mahmoud.dhari.essa",
     "2026-09-04 12:00:00", "Rifaie center", "IM4", None, "Resolve", "Physical Optimization"),
    (None, "Running", "Al-Muthanna / Samawa", "SAM0001", None, "2026-09-05 13:00:00", None,
     "IM5", None, "Pending", None),
    (" BO", "Running", "Basrah", "0", "hw.ahmed.jehad", "2026-09-06 14:00:00",
     "Abu Al-Khaseeb", "IM6", None, "Reopen", "Physical Optimization"),
    (" RF", "Completed", "Basrah", "BAS0002", "hw.ahmed.jehad", "2026-09-07 15:00:00",
     "Abu Al-Khaseeb", "IM7", "2026-09-08 08:00:00", "Close", "Flow Control"),
    (" RF", "Completed", "Basrah", "BAS0002", "hw.ahmed.jehad", "2026-09-07 15:00:00",
     "Abu Al-Khaseeb", "IM7", "2026-09-08 08:00:00", "Close", "Flow Control"),  # twice
]
FIELDS = ("A", "B", "H", "I", "M", "N", "Z", "BF", "DG", "ER", "FE")


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
    data = {h: [None] * len(ROWS) for h in heads}
    for i, row in enumerate(ROWS):
        for letter, v in zip(FIELDS, row):
            data[heads[_index(letter)]][i] = v
    return pd.DataFrame(data, columns=heads)


def _xlsx(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False, sheet_name="sheet1")
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# reading the export
# --------------------------------------------------------------------------- #
def test_the_history_is_read_by_its_headers_and_the_teams_letters():
    t = parse_history(_export())
    assert list(t["hpsm_id"]) == ["IM1", "IM2", "IM3", "IM4", "IM5", "IM6", "IM7", "IM7"]
    assert list(t["city"].unique()) == ["Basrah", "Amarah", "Nasiriyah", "Samawah"]
    assert t.loc[0, "user"] == "Shams Aldin Ali" and t.loc[4, "user"] == ""
    assert list(t["group"]) == ["RF", "RF", "SOC", "SOC", "", "BO", "RF", "RF"]
    assert list(t["state"]) == [CLOSED, CLOSED, SLEEP, IN_PROGRESS, PENDING, IN_PROGRESS,
                                CLOSED, CLOSED]
    assert t.loc[0, "closure_time"] == pd.Timestamp("2026-09-02 10:00")
    assert pd.isna(t.loc[2, "closure_time"])
    # a header written differently is still found at the column the team named
    moved = parse_history(_export({"Ticket Status": "Ticket Status (B)", "Status": "ER"}))
    assert list(moved["status"]) == list(t["status"]) and list(moved["state"]) == list(t["state"])
    cols = columns_of(list(_export({"User": "Closed by"}).columns))
    assert cols["user"] == "Closed by"


def test_a_sheet_that_is_not_the_history_is_refused():
    other = pd.DataFrame({"Site": ["BAS0001"], "Value": [1]})
    with pytest.raises(HistoryFormatError, match="HPSM Incident ID"):
        parse_history(other)                         # the letters never stand in for it
    no_id = _export().drop(columns=["HPSM Incident ID"])
    no_id.columns = [f"x{j}" if j >= 57 else c for j, c in enumerate(no_id.columns)]
    with pytest.raises(HistoryFormatError):
        parse_history(no_id)


def test_cities_and_users_read_as_names():
    assert [city_name(c) for c in ("Basrah", "Maysan / Emarah", "Thaiqar / Nassriya",
                                   "Al-Muthanna / Samawa", "", None)] == [
        "Basrah", "Amarah", "Nasiriyah", "Samawah", "", ""]
    assert user_name("hw.mahmoud.dhari.essa") == "Mahmoud Dhari Essa"
    assert user_name("ayham.thamer") == "Ayham Thamer" and user_name(None) == ""


# --------------------------------------------------------------------------- #
# the counting
# --------------------------------------------------------------------------- #
def test_tickets_are_counted_by_hpsm_id_and_the_cards_add_up():
    import _ticket_history as H
    t = parse_history(_export())
    assert H.count(t) == 7                                    # IM7 twice is one ticket
    s = H.states(t)
    assert s == {CLOSED: 3, PENDING: 1, IN_PROGRESS: 2, SLEEP: 1} and sum(s.values()) == 7
    assert H.by(t, "user").to_dict() == {"Ahmed Jehad": 2, "Mahmoud Dhari Essa": 2,
                                         "Shams Aldin Ali": 2}
    assert H.by(t, "site_id", drop=("0",)).to_dict() == {"BAS0001": 2, "BAS0002": 1,
                                                         "EMA0001": 1, "NAS0001": 1,
                                                         "SAM0001": 1}
    assert H.by(t, "group", keep_empty=True).to_dict() == {"RF": 3, "SOC": 2, "No group": 1,
                                                          "BO": 1}
    assert H.by(t, "city").to_dict() == {"Basrah": 4, "Amarah": 1, "Nasiriyah": 1,
                                         "Samawah": 1}
    assert H.top_with_others(H.by(t, "sup_district"), 1).to_dict() == {
        "Abu Al-Khaseeb": 2, "Others": 4}
    assert H.by(t, "rf_analysis").to_dict() == {"PRB Utilization": 2,
                                                "Physical Optimization": 2,
                                                "New Site Required": 1, "Flow Control": 1}


def test_the_filters_take_any_number_of_values_and_a_time_period():
    import datetime as dt
    import _ticket_history as H
    t = parse_history(_export())
    assert H.options(t, "group") == ["RF", "SOC", "No group", "BO"]      # none picked: all
    assert H.count(H.apply_filters(t, {})) == 7
    assert H.count(H.apply_filters(t, {"city": ["Amarah"]})) == 1
    assert H.count(H.apply_filters(t, {"city": ["Amarah", "Nasiriyah"]})) == 2
    assert H.count(H.apply_filters(t, {"city": ["Basrah"], "user": ["Ahmed Jehad",
                                                                    "Shams Aldin Ali"]})) == 4
    assert H.count(H.apply_filters(t, {"group": ["No group"], "status": ["Pending"]})) == 1
    assert H.count(H.apply_filters(t, {"user": ["Ahmed Jehad"], "status": ["Close"]})) == 1
    assert H.count(H.apply_filters(t, {"city": "Amarah", "group": "All"})) == 1   # one value
    # the period: the days the tickets were created in, both ends included
    sep = lambda a, b: {"period": (dt.date(2026, 9, a), dt.date(2026, 9, b))}  # noqa: E731
    assert sorted(H.apply_filters(t, sep(1, 3))["hpsm_id"]) == ["IM1", "IM2", "IM3"]
    assert H.count(H.apply_filters(t, sep(7, 7))) == 1
    assert H.count(H.apply_filters(t, {**sep(2, 6), "city": ["Basrah"]})) == 2
    assert H.count(H.apply_filters(t, {"period": None})) == 7
    assert H.group_title([]) == "All Groups" and H.group_title(["RF"]) == "RF Group"
    assert H.group_title(["RF", "SOC"]) == "RF, SOC Groups"


def test_rf_analysis_is_a_column_chart_with_its_names_slanted():
    import _ticket_history as H
    import base64
    img = H.vbars(["New Site Required", "PRB Utilization", "Event Traffic Increase"],
                  [2091, 1355, 337], w=680, h=300, tilt=35)
    svg = base64.b64decode(img.split("base64,")[1].split('"')[0]).decode("utf-8")
    assert svg.count("<rect") == 3 and svg.count("rotate(-35") == 3
    assert ">2,091<" in svg and "<title>Event Traffic Increase</title>" in svg
    assert 'viewBox="0 0 680 300"' in svg
    assert "rotate(" not in base64.b64decode(
        H.vbars(["Basrah"], [3]).split("base64,")[1].split('"')[0]).decode("utf-8")


# --------------------------------------------------------------------------- #
# the resource, and the page
# --------------------------------------------------------------------------- #
def _upload():
    import _resources as R
    from rfopt.resources import store as S
    res, bad = R.stage_files("history", [("CC Process_20260917175109.xlsx", _xlsx(_export()))])
    assert not bad
    S.apply("history")
    return res


def test_history_ticket_is_a_data_resource_uploaded_by_hand():
    import _resources as R
    from rfopt.resources import store as S
    assert "history" in S.KINDS and S.SPECS["history"].title == "History ticket"
    assert R.history() is None                                # nothing until uploaded
    res = _upload()
    f = res.pending[0]
    assert f.role == "Ticket history"
    assert f.summary["tickets"] == 7 and f.summary["closed"] == 3
    assert f.summary["start"] == "2026-09-01" and f.summary["end"] == "2026-09-07"
    assert H_count(R.history()) == 7
    _, bad = R.stage_files("history", [("notes.xlsx", _xlsx(pd.DataFrame({"a": [1]})))])
    assert bad and "not the R5 ticket history" in bad[0][1]


def H_count(df) -> int:
    import _ticket_history as H
    return H.count(df)


def _page():
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    return AppTest.from_file(str(APP / "views/ticket_history.py"), default_timeout=240)


def _html(at) -> str:
    return " ".join(e.proto.body for e in at.get("html"))


def _cards(at) -> list:
    import re
    return re.findall(r'th-kpi-t">([^<]+)</div><div class="th-kpi-v">([^<]+)</div>'
                      r'<div class="th-kpi-p">([^<]+)<', _html(at))


def test_the_page_before_any_upload():
    at = _page()
    at.run()
    assert not at.exception, at.exception
    assert "No History ticket data yet" in _html(at)
    assert not at.get("file_uploader")


def _apply(at):
    next(b for b in at.button if b.label == "Apply").click().run()
    assert not at.exception, at.exception


def test_the_page_counts_the_uploaded_history_and_filters_on_apply():
    import datetime as dt
    _upload()
    at = _page()
    at.run()
    assert not at.exception, at.exception
    assert at.segmented_control(key="th_view").value == "History of Tickets"
    assert [m.label for m in at.multiselect] == ["City", "Sup District", "User", "Status",
                                                 "Group"]
    assert all(m.value == [] for m in at.multiselect)          # none picked: all of them
    assert at.radio(key="th_period").value == "All Period"
    assert at.date_input(key="th_days").value == (dt.date(2026, 9, 1), dt.date(2026, 9, 7))
    assert _cards(at) == [("Total Tickets", "7", "100.0%"), ("Closed Tickets", "3", "42.9%"),
                          ("Pending Tickets", "1", "14.3%"), ("In Progress", "2", "28.6%"),
                          ("Sleep Tickets", "1", "14.3%")]
    html = _html(at)
    for title in ("Tickets by User", "Ticket Status (All Groups)", "Tickets by Group",
                  "Top 20 Sites by Number of Tickets", "Tickets by City",
                  "Tickets by Sup District", "Tickets by RF Analysis"):
        assert f">{title}<" in html, title
    assert "Recent Tickets" not in html and "th-tbl" not in html      # no ticket table here
    assert "(M User)" not in html and "(column B)" not in html
    assert "1 ticket with no RF Analysis" in html
    assert "Last Updated: <b>17 Sep 2026 17:51</b>" in html   # the export's own time

    # filters change nothing until Apply; any number of values each
    at.multiselect(key="th_city").set_value(["Amarah", "Nasiriyah"])
    at.run()
    assert _cards(at)[0] == ("Total Tickets", "7", "100.0%")
    _apply(at)
    assert _cards(at)[0] == ("Total Tickets", "2", "100.0%")
    assert _cards(at)[4] == ("Sleep Tickets", "1", "50.0%")
    assert at.multiselect(key="th_city").value == ["Amarah", "Nasiriyah"]
    at.multiselect(key="th_group").set_value(["SOC"])
    _apply(at)
    assert ">Ticket Status (SOC Group)<" in _html(at)

    # the Time Period: days picked are the period, All Period is every day
    next(b for b in at.button if b.label == "Reset").click().run()
    at.date_input(key="th_days").set_value((dt.date(2026, 9, 1), dt.date(2026, 9, 3)))
    _apply(at)
    assert _cards(at)[0] == ("Total Tickets", "3", "100.0%")
    assert at.radio(key="th_period").value == "Custom Period"
    assert at.session_state["th_applied"]["period"] == (dt.date(2026, 9, 1), dt.date(2026, 9, 3))
    at.multiselect(key="th_city").set_value(["Basrah"])
    _apply(at)                                                  # the period stays
    assert _cards(at)[0] == ("Total Tickets", "2", "100.0%")
    at.radio(key="th_period").set_value("All Period")
    _apply(at)
    assert _cards(at)[0] == ("Total Tickets", "4", "100.0%")   # Basrah, every day
    assert at.date_input(key="th_days").value == (dt.date(2026, 9, 1), dt.date(2026, 9, 7))
    next(b for b in at.button if b.label == "Reset").click().run()
    assert at.multiselect(key="th_city").value == [] and at.radio(key="th_period").value == \
        "All Period"
    assert _cards(at)[0] == ("Total Tickets", "7", "100.0%")
    # Count / Percentage
    at.segmented_control(key="th_mode_city").set_value("Percentage").run()
    assert not at.exception, at.exception


def test_history_of_tickets_has_two_tabs():
    _upload()
    at = _page()
    at.run()
    tabs = at.segmented_control(key="th_view")
    assert list(tabs.options) == ["History of Tickets", "Tickets Details"]
    tabs.set_value("Tickets Details").run()
    assert not at.exception, at.exception
    assert at.text_input(key="td_q_in") is not None and at.get_by_key("td_table") is not None
    assert not at.multiselect and 'class="th-kpis"' not in _html(at)   # only the open tab
    assert "Last Updated" in _html(at)
    at.segmented_control(key="th_view").set_value("History of Tickets").run()
    assert _cards(at)[0] == ("Total Tickets", "7", "100.0%")


def test_the_page_sits_under_complaints_in_the_sidebar():
    text = (APP / "Home.py").read_text(encoding="utf-8")
    block = text.split('"Complaints": [')[1].split("],")[0]
    assert 'views/complaint_analysis.py", title="Delay Tickets Analysis"' in block
    assert 'views/ticket_history.py", title="History of Tickets"' in block
    assert "ticket_details" not in block and "Tickets Details" not in block
