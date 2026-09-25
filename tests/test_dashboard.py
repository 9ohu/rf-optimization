"""Dashboard — the executive overview, over the data the app already holds.

Nothing on the page reads a file of its own: the sites come from the Site KMZ,
the KPI judgement from the same health KPI Analysis shows, the tickets from the
History ticket resource and the day's list from the Daily Target — all through
Data Resources, which stays a working page although the sidebar no longer
lists it. Every test runs on its own empty store (conftest).
"""

import io
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
for _p in (str(APP), str(ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from test_ticket_history import _export, _xlsx  # noqa: E402  (the same history sheet)

TARGET = pd.DataFrame([
    {"Ticket ID": "CC-1", "HPSM Incident ID": "IM1", "Site ID(SD Check_site_id)": "BAS0001",
     "City": "Basrah", "Affected Services": "Data Service", "Eng": "hw.shams.aldin.ali",
     "Ticket type": "Complaint", "Problem Time": "2026-09-19T08:00:00.000Z",
     "SLA Target Time": "2026-09-21T08:00:00.000Z"},
    {"Ticket ID": "CC-2", "HPSM Incident ID": "IM2", "Site ID(SD Check_site_id)": "EMA0001",
     "City": "Maysan / Emarah", "Affected Services": "Voice Service",
     "Eng": "hw.mahmoud.dhari.essa", "Ticket type": "Complaint",
     "Problem Time": "2026-09-19T09:00:00.000Z",
     "SLA Target Time": "2026-09-21T09:00:00.000Z"},
])


def _target_xlsx() -> bytes:
    buf = io.BytesIO()
    TARGET.to_excel(buf, index=False, sheet_name="Target")
    return buf.getvalue()


def _upload_history():
    import _resources as R
    from rfopt.resources import store as S
    _, bad = R.stage_files("history", [("CC Process_20260917175109.xlsx", _xlsx(_export()))])
    assert not bad, bad
    S.apply("history")


def _upload_target():
    import _resources as R
    from rfopt.resources import store as S
    _, bad = R.stage_files("complaints", [("Target 19-Sep.xlsx", _target_xlsx())])
    assert not bad, bad
    S.apply("complaints")


# --------------------------------------------------------------------------- #
# the numbers
# --------------------------------------------------------------------------- #
def test_the_ticket_numbers_are_the_ones_history_of_tickets_counts():
    import _dashboard as D
    import _ticket_history as H
    from rfopt.complaints.history import parse_history
    t = parse_history(_export())
    s = D.tickets_state(t)
    assert s["total"] == H.count(t) == 7 and s["closed"] == 3 and s["sleep"] == 1
    assert s["by_city"].to_dict() == {"Basrah": 4, "Amarah": 1, "Nasiriyah": 1, "Samawah": 1}
    assert s["by_group"].to_dict() == {"RF": 3, "SOC": 2, "No group": 1, "BO": 1}
    # the three engineers the user follows, by name as the history writes them
    assert D.ENGINEERS == ("Aws Waheeb", "Shams Aldin Ali", "Mahmoud Dhari Essa")
    assert s["closed_by"] == {"Aws Waheeb": 0, "Shams Aldin Ali": 2,
                              "Mahmoud Dhari Essa": 0}
    assert s["reopened_by"] == {w: 0 for w in D.ENGINEERS}      # none reopened here
    # RF only, by the month the ticket was created
    assert list(s["per_month"].index) == ["2026-09"] and int(s["per_month"].iloc[0]) == 3
    assert s["rf_total"] == 3 and s["rf_breach"] == 0


def test_a_reopened_ticket_is_one_reopened_at_least_once():
    import _dashboard as D
    t = pd.DataFrame({"hpsm_id": ["IM1", "IM2", "IM3", "IM4"],
                      "user": ["Aws Waheeb", "Aws Waheeb", "Shams Aldin Ali", "Other"],
                      "reopen": ["", "2", "1", "3"], "group": ["RF"] * 4,
                      "state": ["Closed", "Sleep", "Closed", "Closed"],
                      "status": ["Close", "Sleep", "Close", "Close"],
                      "city": ["Basrah"] * 4, "sla_status": ["normal"] * 4,
                      "create_time": pd.to_datetime(["2026-01-05", "2026-02-06",
                                                     "2026-02-07", "2026-03-08"])})
    s = D.tickets_state(t)
    assert s["reopened_by"] == {"Aws Waheeb": 1, "Shams Aldin Ali": 1, "Mahmoud Dhari Essa": 0}
    assert s["closed_by"]["Aws Waheeb"] == 1
    assert list(s["per_month"].index) == ["2026-01", "2026-02", "2026-03"]


def test_the_rings_and_the_insight_tiles_draw_what_they_are_given():
    import _dashboard as D
    ring = D.health_ring({"Normal": 764, "Warning": 530, "Critical": 218, "No data": 0})
    assert "th-dn" in ring and ">764<" in ring and ">218<" in ring
    air = D.air_ring(pd.DataFrame({"air": ["onair"] * 3 + ["planned", "offair"]}))
    assert ">On air<" in air and ">Planned<" in air and ">Off air<" in air
    eng = D.engineer_ring({"Aws Waheeb": 528, "Shams Aldin Ali": 4140,
                           "Mahmoud Dhari Essa": 5067})
    assert ">Aws Waheeb<" in eng and ">5,067<" in eng
    import base64
    svg = base64.b64decode(eng.split("base64,")[1].split('"')[0]).decode("utf-8")
    assert ">9,735<" in svg and ">Closed<" in svg            # the three added up, in the middle
    assert "Nothing to show yet" in D.engineer_ring({w: 0 for w in D.ENGINEERS})
    tiles = D.insights([("Most Affected KPI", "3G DL flow-control drops",
                         "426 sites · 145 critical", "chart", "#EF4444")])
    assert "Most Affected KPI" in tiles and "426 sites" in tiles


def test_the_trends_are_drawn_over_days_and_months():
    import _dashboard as D
    import base64
    by_day = pd.DataFrame({"day": pd.to_datetime(["2026-09-17", "2026-09-18", "2026-09-19"]),
                           "issues": [726, 656, 373], "critical": [188, 195, 60]})
    img = D.kpi_trend(by_day, w=500)
    svg = base64.b64decode(img.split("base64,")[1].split('"')[0]).decode("utf-8")
    assert "Sep 17" in svg and "Sep 19" in svg and "Sites with issues" in svg and "Critical" in svg
    months = pd.Series([332, 559, 1234], index=["2026-01", "2026-02", "2026-03"])
    img = D.ticket_trend(months, w=500)
    svg = base64.b64decode(img.split("base64,")[1].split('"')[0]).decode("utf-8")
    assert "Jan 2026" in svg and "Mar 2026" in svg and "RF tickets created" in svg
    assert "No day to trend yet" in D.kpi_trend(pd.DataFrame())
    assert "No RF ticket to trend yet" in D.ticket_trend(pd.Series(dtype=int))


# --------------------------------------------------------------------------- #
# the page
# --------------------------------------------------------------------------- #
def _page():
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    return AppTest.from_file(str(APP / "views/overview.py"), default_timeout=240)


def _html(at) -> str:
    return " ".join(e.proto.body for e in at.get("html"))


def test_the_page_before_any_upload():
    at = _page()
    at.run()
    assert not at.exception, at.exception
    assert "No data yet" in _html(at)


def test_the_page_summarises_what_is_uploaded():
    _upload_history()
    _upload_target()
    at = _page()
    at.run()
    assert not at.exception, at.exception
    html = _html(at)
    import re
    cards = re.findall(r'rf-kpi-title" title="([^"]+)"[^>]*>[^<]*</div><div class="rf-kpi-row">'
                       r'<span class="rf-kpi-val">([^<]+)</span>', html)
    assert cards == [("Total Sites", "—"), ("Sites with KPI Issues", "—"),
                     ("Total Tickets", "7"), ("Target Tickets", "2"), ("Sleep Tickets", "1")]
    for title in ("KPI Health", "Site Status", "Closed Tickets by Engineer",
                  "KPI Issues by Type", "Top 10 Sup Districts by KPI Issues",
                  "Network Overview Map", "Tickets by City", "Tickets by Group",
                  "Reopened Tickets by User", "KPI Issues Trend", "Tickets Trend",
                  "Key Insights"):
        assert f'class="db-h" title="{title}"' in html, title
    # what has no data yet says so, and nothing is invented
    assert html.count("No KPI Data yet.") == 4 and html.count("No site KMZ yet.") == 2
    assert "Highest Ticket Volume" in html and ">Basrah<" in html
    assert "Shams Aldin Ali" in html and "SLA Breached (RF)" in html
    assert "Last Updated" in html and "2 of 4 datasets" in html


def test_the_dashboard_is_the_landing_page_and_data_resources_is_listed():
    home = (APP / "Home.py").read_text(encoding="utf-8")
    first = home.split('pages = {')[1].split("],")[0]
    assert 'views/overview.py", title="Dashboard"' in first and "default=True" in first
    assert 'views/dashboard.py", title="Daily Worklist"' in first     # the old page, renamed
    for page in ("views/site_map.py", "views/kpi_analysis.py", "views/kpi_draw.py",
                 "views/complaint_analysis.py", "views/ticket_history.py",
                 "views/data_resources.py"):
        assert page in home, page                     # every page still registered
    # the sidebar lists Data Resources itself: nothing hides it any more
    css = (APP / "_ui.py").read_text(encoding="utf-8")
    assert 'a[href$="/data_resources"]' not in css
    # and no page carries a data monitor beside it
    for page in ("views/overview.py", "views/dashboard.py", "views/site_map.py",
                 "views/ticket_history.py", "views/kpi_draw.py"):
        assert "R.source_card(" not in (APP / page).read_text(encoding="utf-8"), page
