"""Complaints · Sleep Analysis — the sleep tickets, and what the network says.

A ticket closed as Sleep under one of the eight closure codes is checked
against measured data: the serving sector's hourly KPIs (column FB decides the
sector, never the whole site), the 3G flow-control counter, the coverage grid
where the subscriber was, and the site KMZ for a planned site (column BP).
Solve and Not Solve are both earned; a check with nothing to read says Not
Checked rather than counting the ticket as fixed. Every test runs on its own
empty store (conftest), never the user's.
"""

import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from rfopt.sleep import analysis as A  # noqa: E402

# the export's headers at the columns the R5 team named them by
AT = {"A": "Group", "B": "Ticket Status", "H": "City", "I": "Site ID(SD Check_site_id)",
      "M": "User", "N": "Created At", "Z": "Sub District", "AA": "Problem Time",
      "BF": "HPSM Incident ID", "BP": "Site ID(SD Check)",
      "BQ": "Closure Code(Incident Diagnostic)", "BS": "Diagnostic Comment(Incident Diagnostic)",
      "BT": "CreateTime(Incident Diagnostic)", "DG": "Closure Time", "ER": "Status",
      "FB": "Sector Serving", "FC": "Longitude", "FD": "Latitude", "FE": "RF Analysis"}
FIELDS = tuple(AT)
UTIL = "Data - High utilization cells"
PLANNED = "Data - Planned - This area needs a new site/tower"
RTWP = "High RTWP area (interference)"
INDOOR = "Indoor Issue - Urban areas"
FLOW = "Data - High flow control sites"

ROWS = [
    # a full sector still running hot
    ("RF", "Running", "Basrah", "BAS0001", "hw.shams.aldin.ali", "2026-09-01 10:00:00",
     "Zubair center", "2026-09-01T09:00:00.000Z", "IM1", "BAS0001", UTIL, "checked",
     "2026-09-01 10:05:00", None, "Sleep", "BAS0001-2", "47.80", "30.50", "PRB Utilization"),
    # a sector that has come down since
    ("RF", "Running", "Basrah", "BAS0001", "hw.shams.aldin.ali", "2026-09-02 10:00:00",
     "Zubair center", "2026-09-02T09:00:00.000Z", "IM2", "BAS0001", UTIL, "checked",
     "2026-09-02 10:05:00", None, "Sleep", "BAS0001-1", "47.80", "30.50", "PRB Utilization"),
    # a planned site that is not built
    ("RF", "Running", "Basrah", "BAS0001", "hw.mahmoud.dhari.essa", "2026-09-03 10:00:00",
     "Zubair center", "2026-09-03T09:00:00.000Z", "IM3", "BAS9999", PLANNED, "waiting",
     "2026-09-03 10:05:00", None, "Sleep", "BAS0001-3", "47.80", "30.50", "New Site Required"),
    # interference
    ("RF", "Running", "Basrah", "BAS0001", "hw.mahmoud.dhari.essa", "2026-09-04 10:00:00",
     "Zubair center", "2026-09-04T09:00:00.000Z", "IM4", "BAS0001", RTWP, "rtwp",
     "2026-09-04 10:05:00", None, "Sleep", "BAS0001-2", "47.80", "30.50", "Interference"),
    # an indoor complaint, judged where the subscriber was
    ("RF", "Running", "Basrah", "BAS0001", "hw.ahmed.jehad", "2026-09-05 10:00:00",
     "Zubair center", "2026-09-05T09:00:00.000Z", "IM5", "BAS0001", INDOOR, "indoor",
     "2026-09-05 10:05:00", None, "Sleep", "BAS0001-1", "47.8004", "30.5004", ""),
    # closed, not asleep: never in the population
    ("RF", "Completed", "Basrah", "BAS0001", "hw.ahmed.jehad", "2026-09-06 10:00:00",
     "Zubair center", "2026-09-06T09:00:00.000Z", "IM6", "BAS0001", UTIL, "done",
     "2026-09-06 10:05:00", "2026-09-07 10:00:00", "Close", "BAS0001-1", "47.80", "30.50",
     "PRB Utilization"),
    # asleep, but not one of the eight codes
    ("RF", "Running", "Basrah", "BAS0001", "hw.ahmed.jehad", "2026-09-07 10:00:00",
     "Zubair center", "2026-09-07T09:00:00.000Z", "IM7", "BAS0001", "No Problem Found", "-",
     "2026-09-07 10:05:00", None, "Sleep", "BAS0001-1", "47.80", "30.50", "Device Issue"),
]


def _index(letter: str) -> int:
    n = 0
    for ch in letter:
        n = n * 26 + ord(ch) - 64
    return n - 1


def _export() -> pd.DataFrame:
    heads = [f"Filler {j}" for j in range(161)]
    for letter, name in AT.items():
        heads[_index(letter)] = name
    data = {h: [None] * len(ROWS) for h in heads}
    for i, row in enumerate(ROWS):
        for letter, v in zip(FIELDS, row):
            data[heads[_index(letter)]][i] = v
    return pd.DataFrame(data, columns=heads)


def _history() -> pd.DataFrame:
    from rfopt.complaints.history import parse_history
    return parse_history(_export())


def _objects() -> pd.DataFrame:
    """The export's objects, placed in their sector as the EP tracker places
    them: sector 2 has two carriers, sectors 1 and 3 one each."""
    return pd.DataFrame({
        "object": ["L_A_BAS0001-1", "L_A_BAS0001-2", "L21_A_BAS0001-2", "L_A_BAS0001-3"],
        "site": ["BAS0001"] * 4,
        "sector": [1.0, 2.0, 2.0, 3.0],
    })


def _hourly() -> pd.DataFrame:
    """Six hours: sector 2 runs over the critical line, sector 1 does not, and
    the second carrier of sector 2 is the quiet one."""
    hours = pd.date_range("2026-09-17", periods=6, freq="h")
    rows = []
    prb = {"L_A_BAS0001-1": 40.0, "L_A_BAS0001-2": 95.0, "L21_A_BAS0001-2": 30.0,
           "L_A_BAS0001-3": 20.0}
    rssi = {"L_A_BAS0001-1": -115.0, "L_A_BAS0001-2": -100.0, "L21_A_BAS0001-2": -118.0,
            "L_A_BAS0001-3": -119.0}
    for obj, p in prb.items():
        for h, t in enumerate(hours):
            rows.append({"datetime": t, "object": obj, "site_id": "BAS0001",
                         "HW_DL PRB Avg Utilization(%)": p - h,
                         "L.UL.Interference.Avg(dBm)": rssi[obj],
                         "Average User Number": 10.0})
    return pd.DataFrame(rows)


COLS = {A.PRB: "HW_DL PRB Avg Utilization(%)", A.RSSI: "L.UL.Interference.Avg(dBm)",
        A.USERS: "Average User Number"}


class _Grid:
    """A coverage grid of three measured points."""
    name = "BAS_grid.zip"
    lat = np.array([30.5000, 30.5004, 30.6000])
    lon = np.array([47.8000, 47.8004, 47.9000])
    rsrp = np.array([-92.0, -113.0, -80.0])
    mr = np.array([400, 120, 900])


KMZ = pd.DataFrame({"site_id": ["BAS0001", "BAS9999"], "status": ["On Air", "Planned"]})


# --------------------------------------------------------------------------- #
# the population
# --------------------------------------------------------------------------- #
def test_only_sleep_tickets_of_the_eight_closure_codes():
    pop = A.sleep_population(_history(), today="2026-09-25")
    assert list(pop["hpsm_id"]) == ["IM1", "IM2", "IM3", "IM4", "IM5"]
    assert set(A.CLOSURE_CODES) >= set(pop["closure_code"])
    assert len(A.CLOSURE_CODES) == 8


def test_the_serving_sector_is_the_site_and_the_sector_of_column_fb():
    pop = A.sleep_population(_history())
    assert list(pop["serving"]) == ["BAS0001-2", "BAS0001-1", "BAS0001-3", "BAS0001-2",
                                    "BAS0001-1"]
    assert list(pop["site"]) == ["BAS0001"] * 5 and list(pop["sector_num"]) == [2, 1, 3, 2, 1]
    assert A.split_sector("BAS3114 -2") == ("BAS3114", 2.0)     # a stray space
    site, num = A.split_sector("BAS3114")                       # a site with no sector
    assert site == "" and pd.isna(num)


def test_the_plan_site_is_carried_only_where_the_ticket_is_about_one():
    pop = A.sleep_population(_history())
    # column BP names a site on every row; only the planned ticket shows one
    assert list(pop["plan_site"]) == ["", "", "BAS9999", "", ""]
    assert list(pop["check"]) == ["utilization", "utilization", "planned", "interference",
                                  "coverage"]


def test_the_rf_analysis_of_the_ticket_decides_the_check_over_the_code():
    assert A.check_of(UTIL, "Flow Control") == "flow_control"      # the engineer's own finding
    assert A.check_of(UTIL, "") == "utilization"
    assert A.check_of(INDOOR, "") == "coverage"
    assert A.check_of("Not Within Plan", "") == "coverage"
    assert A.check_of("Resolved Successfully", "") == ""


def test_wake_after_counts_down_and_goes_negative():
    assert A.wake_after("2026-09-30", today="2026-09-25") == 5
    assert A.wake_after("2026-09-20", today="2026-09-25") == -5
    assert pd.isna(A.wake_after(None))


# --------------------------------------------------------------------------- #
# the evidence
# --------------------------------------------------------------------------- #
def test_a_sectors_hour_is_its_worst_carrier():
    hours = A.sector_hours(_hourly(), _objects(), COLS)
    two = hours[hours["sector"].eq(2.0)].sort_values("datetime")
    # 95 and 30 in the same hour: the sector's hour is 95, not the average
    assert list(two[A.PRB])[:2] == [95.0, 94.0]
    assert list(two[A.RSSI])[:1] == [-100.0]
    assert list(two[A.USERS])[:1] == [20.0]          # users are the carriers added up
    ev = A.evidence_of(hours, {("BAS0001", 2.0): ["L_A_BAS0001-2", "L21_A_BAS0001-2"]})
    one = ev[("BAS0001", 2.0)]
    assert one.hours == 6 and one.prb_max == 95.0 and one.prb_hours == 6   # 95..90, all over 85
    assert one.rssi_max == -100.0 and one.cells == ["L_A_BAS0001-2", "L21_A_BAS0001-2"]
    assert ev[("BAS0001", 1.0)].prb_max == 40.0 and ev[("BAS0001", 1.0)].prb_hours == 0


def test_the_nearest_measured_grid_answers_for_a_point_and_only_when_it_is_near():
    near = A.rsrp_frame([30.5004, 30.4000], [47.8004, 47.4000], [_Grid()])
    assert round(float(near.loc[0, "rsrp"]), 1) == -113.0      # the grid 0 m away
    assert float(near.loc[0, "metres"]) < 5
    assert pd.isna(near.loc[1, "rsrp"])                        # kilometres away: nothing
    point = A.point_of(near.iloc[0])
    assert point.rsrp == -113.0 and point.mr == 120
    assert A.point_of(near.iloc[1]) is None


def test_a_planned_site_is_on_air_only_when_the_kmz_says_so():
    assert A.plan_site_status("BAS0001", KMZ) == A.ON_AIR
    assert A.plan_site_status("BAS9999", KMZ) == A.NOT_ON_AIR
    assert A.plan_site_status("BAS5555", KMZ) == A.NOT_ON_AIR   # not in the file: not built
    assert A.plan_site_status("", KMZ) == ""


# --------------------------------------------------------------------------- #
# the verdicts
# --------------------------------------------------------------------------- #
def _evidence():
    return A.evidence_of(A.sector_hours(_hourly(), _objects(), COLS),
                         {("BAS0001", 2.0): ["L_A_BAS0001-2", "L21_A_BAS0001-2"]})


def test_a_sector_still_over_the_line_is_not_solved_and_the_comment_says_by_how_much():
    ev = _evidence()[("BAS0001", 2.0)]
    verdict, text = A.judge("utilization", "BAS0001-2", ev, None, metres=312.0)
    assert verdict == A.NOT_SOLVE
    # the description is the R5 team's fixed format, with only the values filled in
    assert text == ("The serving sector is BAS0001-2, with a distance of 312 m from the user "
                    "location. The sector is still experiencing high PRB utilization, with a "
                    f"maximum value of 95.0% and an average value of {ev.prb_avg:.1f}%. "
                    "The RSRP measurement is N/A.")
    for word in ("AI", "artificial intelligence", "the system detected"):
        assert word.lower() not in text.lower()


def test_a_sector_that_has_come_down_is_solved_on_the_same_numbers():
    ev = _evidence()[("BAS0001", 1.0)]
    verdict, text = A.judge("utilization", "BAS0001-1", ev, None)
    assert verdict == A.SOLVE and "maximum value of 40.0%" in text


def test_interference_is_judged_on_the_operators_own_line():
    ev = _evidence()
    bad, text = A.judge("interference", "BAS0001-2", ev[("BAS0001", 2.0)], None)
    assert bad == A.NOT_SOLVE and "maximum RTWP value of -100.0 dBm" in text
    ok, _ = A.judge("interference", "BAS0001-3", ev[("BAS0001", 3.0)], None)
    assert ok == A.SOLVE


def test_coverage_is_judged_where_the_subscriber_was():
    weak = A.Point(rsrp=-113.0, mr=120, metres=3.0)
    fine = A.Point(rsrp=-92.0, mr=400, metres=8.0)
    bad, text = A.judge("coverage", "BAS0001-1", None, weak)
    assert bad == A.NOT_SOLVE and text == (
        "The serving sector is BAS0001-1, with a distance of N/A from the user location. "
        "The area is still experiencing weak coverage, with an RSRP measurement of -113.0 dBm.")
    good, _ = A.judge("coverage", "BAS0001-1", None, fine)
    assert good == A.SOLVE


def test_a_planned_site_not_on_air_is_not_solved_whatever_the_kpi_says():
    verdict, text = A.judge("planned", "BAS0001-3", _evidence()[("BAS0001", 3.0)], None,
                            A.NOT_ON_AIR, "BAS9999")
    assert verdict == A.NOT_SOLVE and text.startswith(
        "The planned site BAS9999 is still not on air. The serving sector is BAS0001-3")
    # on air, the check carries on at the subscriber's point
    on, text = A.judge("planned", "BAS0001-3", None, A.Point(-92.0, 400, 8.0), A.ON_AIR,
                       "BAS9999")
    assert on == A.SOLVE and "BAS9999 is now on air" in text
    assert text.endswith("The current RSRP measurement is -92.0 dBm.")


def test_flow_control_is_judged_on_the_counter_of_the_site():
    bad, text = A.judge("flow_control", "BAS0001-1", None, None, flow=(30, 14499.0, 812.25))
    assert bad == A.NOT_SOLVE and ("Flow Control issues, with a maximum value of 14,499 and "
                                   "an average value of 812.2") in text
    ok, _ = A.judge("flow_control", "BAS0001-1", None, None, flow=(0, 0.0))
    assert ok == A.SOLVE
    none, text = A.judge("flow_control", "BAS0001-1", None, None, flow=None)
    assert none == A.NOT_CHECKED


def test_the_description_is_written_for_the_verdict_not_the_closure_code():
    ev = _evidence()
    # the PRB issue has come down: Solve, and nothing says it is still there
    ok, text = A.judge("utilization", "BAS0001-1", ev[("BAS0001", 1.0)], None)
    assert ok == A.SOLVE and "still" not in text
    assert "The sector is no longer experiencing high PRB utilization" in text
    ok, text = A.judge("interference", "BAS0001-3", ev[("BAS0001", 3.0)], None)
    assert ok == A.SOLVE and "no longer experiencing interference" in text
    ok, text = A.judge("coverage", "BAS0001-1", None, A.Point(-92.0, 400, 8.0))
    assert ok == A.SOLVE and text.endswith(
        "The area is no longer experiencing weak coverage, with an RSRP measurement of "
        "-92.0 dBm.")
    ok, text = A.judge("flow_control", "BAS0001-1", None, None, flow=(0, 0.0, 0.0))
    assert ok == A.SOLVE and "no longer experiencing Flow Control issues" in text
    # nothing to read: not said to be still there, nor gone
    for check in ("utilization", "interference", "coverage", "flow_control"):
        verdict, text = A.judge(check, "BAS0001-9", None, None)
        assert verdict == A.NOT_CHECKED and "could not be verified" in text
        assert "still" not in text and "no longer" not in text
    # a planned site with no status to read is not called "still not on air"
    verdict, text = A.judge("planned", "BAS0001-3", None, None, "", "BAS9999")
    assert verdict == A.NOT_CHECKED and text.startswith(
        "The status of the planned site BAS9999 could not be verified.")


def test_nothing_to_read_is_not_checked_rather_than_solved():
    for check in ("utilization", "interference"):
        verdict, text = A.judge(check, "BAS0001-9", None, None)
        assert verdict == A.NOT_CHECKED and "N/A" in text
    assert A.judge("coverage", "BAS0001-1", None, None)[0] == A.NOT_CHECKED
    assert A.judge("utilization", "", None, None)[0] == A.NOT_CHECKED     # no sector on it


def test_how_far_apart_two_points_are_and_which_way_they_face():
    # a tenth of a degree of latitude is 11.1 km due north
    assert round(A.metres_between(30.5, 47.8, 30.6, 47.8)) == 11132
    assert round(A.bearing(30.5, 47.8, 30.6, 47.8)) == 0
    assert round(A.bearing(30.5, 47.8, 30.5, 47.9)) == 90
    assert A.angle_gap(350, 10) == 20 and A.angle_gap(100, 117) == 17
    assert A.fmt_metres(907) == "907 m" and A.fmt_metres(1140) == "1.14 km"
    assert A.fmt_metres(float("nan")) == "-"
    assert pd.isna(A.metres_between(30.5, 47.8, None, 47.8))


def test_the_comparison_reaches_the_site_next_door_and_leads_with_the_serving_sector():
    import _sleep as S
    facts = {"evidence": A.evidence_of(
        A.sector_hours(_hourly(), _objects(), COLS),
        {("BAS0001", 2.0): ["L_A_BAS0001-2", "L21_A_BAS0001-2"],
         ("BAS0001", 1.0): ["L_A_BAS0001-1"], ("BAS0001", 3.0): ["L_A_BAS0001-3"]})}
    # a neighbour 300 m away, measured the same way
    hours = A.sector_hours(_hourly().assign(object="L_B_BAS0002-1"),
                           pd.DataFrame({"object": ["L_B_BAS0002-1"], "site": ["BAS0002"],
                                         "sector": [1.0]}), COLS)
    facts["evidence"].update(A.evidence_of(hours, {("BAS0002", 1.0): ["L_B_BAS0002-1"]}))
    sites = pd.DataFrame({"site_id": ["BAS0001", "BAS0002"],
                          "latitude": [30.5000, 30.5027], "longitude": [47.8, 47.8]}
                         ).set_index("site_id")
    row = A.sleep_population(_history()).iloc[0]          # IM1: BAS0001-2, utilization
    table = S.comparison(facts, row, sites)
    assert list(table.columns) == ["Sector Type", "Sector", "Cell Name", "KPI Issue",
                                   "Max Value", "Average Value", "Distance",
                                   "Another Issue Impacted", "Status"]
    assert table.loc[0, "Sector Type"] == "Serving Sector"     # the serving one leads
    assert table.loc[0, "Sector"] == "BAS0001-2" and table.loc[0, "Status"] == "Issue"
    assert table.loc[0, "KPI Issue"] == "PRB Utilization" and table.loc[0, "Max Value"] == "95"
    assert "L21_A_BAS0001-2" in table.loc[0, "Cell Name"]
    kinds = list(table["Sector Type"])
    assert kinds.count("Same Site") == 2 and kinds.count("Neighbor Site") == 1
    # the subscriber sits at the serving site, 300 m from the neighbour
    assert table[table["Sector"].eq("BAS0002-1")].iloc[0]["Distance"] == "301 m"
    # the serving row is the green one
    style = S.comparison_style(table).export()
    assert style is not None


def test_the_trend_offers_those_five_kpis_and_no_others():
    import _sleep as S
    assert [t[0] for t in S.TRENDS] == ["PRB Utilization", "4G Availability",
                                        "4G Interference", "Flow Control", "RTWP"]
    # only the ones the loaded exports carry are offered
    assert S.trend_choices({"cols": {A.PRB: "HW_DL PRB Avg Utilization(%)"}}) == \
        [("PRB Utilization", "4G", A.PRB)]
    assert S.trend_choices({"cols": {}}) == []


def test_the_rf_analysis_chart_counts_column_fe():
    import _sleep as S
    pop = A.sleep_population(_history())
    html = S.by_rf(pop)
    assert "PRB Utilization" in html and "New Site Required" in html
    assert "Interference" in html
    # never the closure code
    assert UTIL not in html and PLANNED not in html


# --------------------------------------------------------------------------- #
# the page
# --------------------------------------------------------------------------- #
def _xlsx(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False, sheet_name="sheet1")
    return buf.getvalue()


def test_the_page_says_what_to_upload_when_there_is_no_history():
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    at = AppTest.from_file(str(APP / "views/sleep_analysis.py"), default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    html = " ".join(e.proto.body for e in at.get("html"))
    assert "No ticket history yet" in html
    assert not at.get("file_uploader")                  # nothing is uploaded here


def test_the_page_counts_the_sleep_tickets_and_names_their_closure_codes(put_resource):
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    put_resource("history", "CC Process.xlsx", _xlsx(_export()), "Ticket history")
    at = AppTest.from_file(str(APP / "views/sleep_analysis.py"), default_timeout=240)
    at.run()
    assert not at.exception, at.exception
    html = " ".join(e.proto.body for e in at.get("html"))
    assert "Total Sleep Tickets" in html and ">5<" in html          # the five sleep tickets
    assert UTIL in html and PLANNED in html                        # the closure-code bars
    assert "Still Issue" in html and "Resolved" in html
    # with no KPI, coverage or KMZ loaded the page says what it cannot check
    assert "4G KPI Data" in " ".join(c.value for c in at.caption)


def test_the_table_carries_the_columns_the_team_asked_for():
    import _sleep as S
    # the Ticket ID leads: the table pins its first column, so that one stays
    # in view while the rest scrolls sideways
    assert [c[1] for c in S.COLUMNS] == [
        "Ticket ID", "User", "Site ID", "Cite", "Serving Sector", "Plan Site", "RF Analysis",
        "Closure Code", "Log", "Lat", "Diagnostic Submit Time", "Create Time",
        "Expected Resolution Date", "Wake After", "Problem Time", "Status", "Distance",
        "Site Issue", "Plan Site Status", "RSRP", "Description"]
    kinds = dict((c[0], c[2]) for c in S.COLUMNS)
    assert kinds["verdict"] == "verdict" and kinds["plan_status"] == "air"
    # Status is column ER of the export, which the history reads as `status`,
    # and RF Analysis is column FE
    assert dict((c[1], c[0]) for c in S.COLUMNS)["Status"] == "status"
    assert dict((c[1], c[0]) for c in S.COLUMNS)["RF Analysis"] == "rf_analysis"
    # Tickets Details keeps its own columns
    import _ticket_table as T
    assert T.COLUMNS[0][0] == "hpsm_id" and len(T.COLUMNS) == 24


def test_sleep_analysis_is_a_page_of_the_app_and_the_others_are_untouched():
    home = (APP / "Home.py").read_text(encoding="utf-8")
    assert 'views/sleep_analysis.py", title="Sleep Analysis"' in home
    for page in ("views/ticket_history.py", "views/kpi_draw.py", "views/site_map.py",
                 "views/data_resources.py"):
        assert page in home, page
    # the page reads the store and writes nothing to it
    src = (APP / "_sleep.py").read_text(encoding="utf-8")
    assert "file_uploader" not in src and "store.apply" not in src
