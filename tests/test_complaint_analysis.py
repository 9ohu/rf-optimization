"""Complaint Analysis: the persistent Daily Target, and each ticket correlated
with its site's KPIs around the problem time."""

import io
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
HEADER = ["Affected Services", "Ticket ID", "HPSM Incident ID", "Eng", "City",
          "Site ID(SD Check_site_id)", "SLA Status", "Created At", "Problem Time", "MSISDN",
          "Reopen Count", "User"]


def _xlsx(rows):
    import xlsxwriter

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    ws = wb.add_worksheet("Sheet1")
    for r, row in enumerate(rows):
        ws.write_row(r, 0, row)
    wb.close()
    return buf.getvalue()


def _target_bytes():
    return _xlsx([HEADER,
                  ["Data Service", "CC-1", "IM1", "shams", "Basrah", "BAS0001", "normal",
                   "2026-09-13 16:30:00", "2026-09-13T16:20:00.000Z", "9647000000001",
                   None, None],
                  ["Coverage", "CC-2", "IM2", "aws", "Thaiqar / Nassriya", "0", "normal",
                   "2026-09-13 10:05:00", "2026-09-13T10:00:00.000Z", "9647000000002",
                   2, "hw.aws"]])


def test_a_valid_upload_becomes_the_active_daily_target(tmp_path, monkeypatch):
    """A valid upload replaces the active dataset; a broken one never does."""
    monkeypatch.setenv("RFOPT_CACHE_DIR", str(tmp_path))
    from rfopt.complaints.target_store import TargetFormatError, load_active, save_target

    assert load_active() is None
    ds = save_target(_target_bytes(), "Target 13-Sep.xlsx")
    again = load_active()
    assert again.name == "Target 13-Sep.xlsx" and again.records == 2
    assert again.sha1 == ds.sha1 and again.path.is_file()

    with pytest.raises(TargetFormatError):
        save_target(_xlsx([["Name", "Value"], ["a", "b"]]), "wrong.xlsx")
    kept = load_active()
    assert kept.name == "Target 13-Sep.xlsx" and kept.records == 2
    # the broken file never became data; the good one is the active Complaint Data
    from rfopt.resources import store
    res = store.resource("complaints")
    assert [f.name for f in res.files] == ["Target 13-Sep.xlsx"] and not res.pending
    assert len(list((tmp_path / "resources" / "files").iterdir())) == 1


def test_the_target_reads_engineer_governorate_and_local_problem_time():
    from rfopt.complaints.correlate import local_time, r5_governorate
    from rfopt.complaints.target_store import parse_target

    t = parse_target(_target_bytes(), "t.xlsx")
    assert list(t["engineer"]) == ["shams", "aws"]
    # the export writes local clock time with a 'Z': the digits are kept
    assert local_time(t["problem_time"]).iloc[0] == pd.Timestamp("2026-09-13 16:20")
    assert pd.isna(t["site_id"].iloc[1])                  # "0" is no site
    assert r5_governorate("Thaiqar / Nassriya") == "Nassriya"
    assert r5_governorate("Maysan / Emarah") == "Emarah"
    assert r5_governorate("Al-Muthanna / Samawa") == "Samawa"
    assert r5_governorate("", "Basrah") == "Basrah"


def _tracks(values, kpi="LTE_Availability(%)@AB", site="BAS0001", start="2026-09-13 00:00"):
    from rfopt.complaints.correlate import build_tracks, judged_kpis

    t = pd.date_range(start, periods=len(values), freq="h")
    df = pd.DataFrame({"datetime": t, "object": f"L_X_{site}-1", "site_id": site, kpi: values})
    return build_tracks([("4G", "x.zip", df, judged_kpis([kpi], "4G"))])


PT = pd.Timestamp("2026-09-13 16:20")


def test_an_outage_in_the_window_that_recovers_is_technical_and_resolved():
    from rfopt.complaints.correlate import RESOLVED, TECHNICAL, analyse_ticket

    vals = [100.0] * 15 + [40.0, 40.0] + [100.0] * 7        # down 15:00-17:00
    a = analyse_ticket("BAS0001", PT, _tracks(vals), 2.0)
    assert a.classification == TECHNICAL and a.problem_detected == "Yes"
    assert a.problem_type == "Outage" and a.confidence == "High"
    assert a.resolution == RESOLVED
    assert "15:00" in a.evidence and "40.0%" in a.evidence


def test_a_breach_still_there_at_the_last_hour_is_not_resolved():
    from rfopt.complaints.correlate import NOT_RESOLVED, TECHNICAL, analyse_ticket

    vals = [100.0] * 15 + [40.0] * 9
    a = analyse_ticket("BAS0001", PT, _tracks(vals), 2.0)
    assert a.classification == TECHNICAL and a.resolution == NOT_RESOLVED


def test_normal_kpis_flag_the_ticket_for_manual_verification():
    from rfopt.complaints.correlate import NO_ISSUE, NO_PROBLEM, analyse_ticket

    a = analyse_ticket("BAS0001", PT, _tracks([100.0] * 24), 2.0)
    assert a.classification == NO_ISSUE and a.manual_check
    assert a.resolution == NO_PROBLEM and "Manual customer verification" in a.site_issue


def test_the_correlation_window_decides_what_is_looked_at():
    """A breach at 12:00 is outside ±30 min of a 16:20 complaint, inside ±5 h."""
    from rfopt.complaints.correlate import NO_ISSUE, TECHNICAL, analyse_ticket

    vals = [100.0] * 12 + [40.0] + [100.0] * 11
    tracks = _tracks(vals)
    assert analyse_ticket("BAS0001", PT, tracks, 0.5).classification == NO_ISSUE
    wide = analyse_ticket("BAS0001", PT, tracks, 5.0)
    assert wide.classification == TECHNICAL and wide.confidence == "Medium"


def test_missing_data_is_never_turned_into_a_conclusion():
    from rfopt.complaints.correlate import INSUFFICIENT, analyse_ticket

    tracks = _tracks([100.0] * 24)
    assert analyse_ticket("", PT, tracks, 2.0).classification == INSUFFICIENT
    assert analyse_ticket("BAS0001", None, tracks, 2.0).classification == INSUFFICIENT
    assert analyse_ticket("BAS0001", PT, [], 2.0).site_issue == "No KPI export loaded"
    gone = analyse_ticket("NAS9999", PT, tracks, 2.0)
    assert gone.classification == INSUFFICIENT and "not in the loaded" in gone.site_issue
    old = analyse_ticket("BAS0001", pd.Timestamp("2026-05-18 15:53"), tracks, 2.0)
    assert old.classification == INSUFFICIENT and "do not cover" in old.site_issue


def test_only_thresholded_rates_are_judged():
    from rfopt.complaints.correlate import judged_kpis

    got = {canon for _, canon, _ in judged_kpis(
        ["LTE_Availability(%)@AB", "4G Data Volume (GB)", "Integrity",
         "L.UL.Interference.Avg(dBm)"], "4G")}
    assert got == {"cell_avail_pct", "ul_rssi_dbm"}


def test_reopen_and_user_decide_the_ticket_type():
    from rfopt.complaints.ticket_type import (NEW, REOPEN, UP_OF_SLEEP, ticket_type,
                                              type_label)

    # any reopen count is a reopen, however Excel stored it, and it beats User
    assert ticket_type(1, None) == (REOPEN, 1)
    assert ticket_type("2", "") == (REOPEN, 2)
    assert ticket_type(" 3 ", "hw.mahmoud.dhari.essa") == (REOPEN, 3)
    assert ticket_type(2.0, float("nan")) == (REOPEN, 2)
    assert ticket_type("1.0", "nan") == (REOPEN, 1)
    # no reopen count and a User: up of sleep
    assert ticket_type(None, "hw.mahmoud.dhari.essa") == (UP_OF_SLEEP, None)
    assert ticket_type("  ", " hw.shams.aldin.ali ") == (UP_OF_SLEEP, None)
    assert ticket_type(float("nan"), "Shams") == (UP_OF_SLEEP, None)
    # neither: new
    assert ticket_type(float("nan"), float("nan")) == (NEW, None)
    assert ticket_type("", "   ") == (NEW, None)
    assert ticket_type("nan", "None") == (NEW, None)
    assert type_label(REOPEN, 2) == "REOPEN #2" and type_label(NEW) == "NEW"


def test_the_target_file_carries_the_ticket_type_fields():
    from rfopt.complaints.target_store import parse_target
    from rfopt.complaints.ticket_type import ticket_type

    rows = [HEADER]
    for tid, reopen, user in (("CC-1", None, None), ("CC-2", 1, None),
                              ("CC-3", None, "hw.mahmoud.dhari.essa"), ("CC-4", 3, "hw.aws")):
        rows.append(["Data Service", tid, "IM", "dhari", "Basrah", "BAS0001", "normal",
                     "2026-09-13 16:30:00", "2026-09-13T16:20:00.000Z", "964", reopen, user])
    t = parse_target(_xlsx(rows), "t.xlsx")
    assert [ticket_type(r, u) for r, u in zip(t["reopen"], t["user"])] == [
        ("NEW", None), ("REOPEN", 1), ("UP OF SLEEP", None), ("REOPEN", 3)]


def test_the_noc_tiles_show_the_checks_and_the_context_indicators():
    from rfopt.complaints.correlate import analyse_ticket
    from rfopt.complaints.noc import (build_indicators, indicator_columns,
                                      observe_indicators, site_timeline, ticket_tiles)

    vals = [100.0] * 15 + [40.0, 40.0] + [100.0] * 7        # down 15:00-17:00
    tracks = _tracks(vals)
    a = analyse_ticket("BAS0001", PT, tracks, 2.0)

    t = pd.date_range("2026-09-13 00:00", periods=24, freq="h")
    lte = pd.DataFrame({"datetime": t.repeat(2), "object": ["L_X_BAS0001-1", "L_X_BAS0001-2"] * 24,
                        "site_id": "BAS0001", "S1 SIG Failures": [0.0, 0.0] * 16 + [2.0, 1.0] + [0.0, 0.0] * 7})
    umts = pd.DataFrame({"datetime": t, "object": "X_BAS0001", "site_id": "BAS0001",
                         "VS.MeanRTWP(dBm)": [-104.0] * 16 + [-92.0] + [-104.0] * 7,
                         "VS.RscGroup.FlowCtrol.DL.DropNum": [3000.0] * 24})
    inds = build_indicators([
        ("4g.zip", lte, indicator_columns(["S1 SIG Failures", "Integrity"], "4G")),
        ("3g.zip", umts, indicator_columns(["VS.MeanRTWP(dBm)",
                                            "VS.RscGroup.FlowCtrol.DL.DropNum"], "3G"))])
    obs = {c.key: c for c in observe_indicators("BAS0001", PT, inds, 2.0)}
    assert obs["S1"].state == "Detected" and obs["S1"].value == 3
    assert obs["RTWP"].value == -92.0 and obs["RTWP"].state == "High"   # warning > -95 dBm
    assert obs["FLOW"].value == 3000.0 * 19 and obs["FLOW"].sev == 1    # 24 h total, 19 h of data

    primary, secondary = ticket_tiles(a, obs.values())
    tiles = {x.key: x for x in primary + secondary}
    assert [x.key for x in primary] == ["AVA", "PRB", "INTER", "FLOW"]
    assert tiles["AVA"].state == "Critical" and tiles["AVA"].value == "40.0%"
    assert tiles["AVA"].observed.startswith("13 Sep 15:00") and tiles["AVA"].judged
    assert tiles["PRB"].state == "No Data"                         # not in the export
    assert not tiles["FLOW"].judged and not tiles["S1"].judged

    tl = site_timeline("BAS0001", PT, tracks, 2.0)
    assert (tl.before, tl.during, tl.after) == (0, 2, 0)
    assert tl.issue_span == "13 Sep 15:00–17:00"
    assert site_timeline("NAS9999", PT, tracks, 2.0) is None


def _app(at_cls, page: str):
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))
    return at_cls.from_file(str(APP / page), default_timeout=300)


def _html(at) -> list[str]:
    return [e.proto.body for e in at.get("html")]


PRB = "HW_DL PRB Avg Utilization(%)"


def _kpi_export() -> tuple[str, bytes]:
    """BAS0001's cell congested 15:00-17:00 on the day of CC-1 (problem 16:20)."""
    head = ("Time,eNodeB Name,Cell FDD TDD Indication,Cell Name,LocalCell Id,"
            f"{PRB},L.UL.Interference.Avg(dBm),LTE_Availability(%)@AB")
    rows = [head] + [f"{h:%Y-%m-%d %H:%M},Alpha_BAS0001,CELL_FDD,L_Alpha_BAS0001-1,1,"
                     f"{95 if 15 <= h.hour <= 17 else 30},-118,100"
                     for h in pd.date_range("2026-09-13 10:00", "2026-09-13 23:00", freq="h")]
    return "R5 4G Monitoring Hourly KPI.csv", ("\n".join(rows) + "\n").encode()


def test_every_original_column_is_read_back_per_ticket():
    from rfopt.complaints.target_store import parse_target, source_columns

    data = _xlsx([HEADER + ["Remarks from NOC"],
                  ["Data Service", "CC-1", "IM1", "shams", "Basrah", "BAS0001", "normal",
                   "2026-09-13 16:30:00", "2026-09-13T16:20:00.000Z", "9647000000001", None,
                   None, "first"],
                  ["Data Service", None, "IM9", "aws", "Basrah", "BAS0002", "normal",
                   "2026-09-13 16:30:00", "2026-09-13T16:20:00.000Z", "9647000000009", None,
                   None, "no ticket id: dropped"],
                  ["Coverage", "CC-2", "IM2", "aws", "Basrah", "BAS0003", "sla_violation",
                   "2026-09-13 10:05:00", "2026-09-13T10:00:00.000Z", "9647000000002", 2,
                   "hw.aws", "third"]])
    t = parse_target(data, "t.xlsx")
    src = source_columns(data, "t.xlsx", t["_row"])
    assert list(t["ticket_id"]) == ["CC-1", "CC-2"]
    assert list(src.columns) == HEADER + ["Remarks from NOC"]
    assert list(src["Ticket ID"]) == ["CC-1", "CC-2"] and list(src["Remarks from NOC"]) == [
        "first", "third"]


def test_the_timeline_names_its_kpis_and_its_worst_hour():
    from rfopt.complaints.correlate import build_tracks, judged_kpis
    from rfopt.complaints.noc import site_timeline, window_series
    from rfopt.ingest.hourly_kpi import load_hourly_raw, sniff_kpi_export
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))
    from _shared import NamedBytes

    name, data = _kpi_export()
    info = sniff_kpi_export(NamedBytes(data, name))
    judged = judged_kpis(info.all_kpis, info.kind)
    raw = load_hourly_raw(NamedBytes(data, name), [c for c, _, _ in judged])
    tracks = build_tracks([(info.kind, name, raw, judged)])
    pt = pd.Timestamp("2026-09-13 16:20")

    tl = site_timeline("BAS0001", pt, tracks, 2.0)
    assert tl.kpis == ["4G DL PRB", "4G UL interference", "4G Availability"]
    assert tl.peak["hour"] == pd.Timestamp("2026-09-13 15:00") and tl.peak["kpi"] == "4G DL PRB"
    assert tl.peak["value"] == "95.0%" and tl.peak["object"] == "L_Alpha_BAS0001-1"
    hours = dict(zip([h for h, _ in tl.hours], tl.drivers))
    assert hours[pd.Timestamp("2026-09-13 16:00")] == ["4G DL PRB"]
    assert hours[pd.Timestamp("2026-09-13 12:00")] == []          # a normal hour names no KPI

    prb = next(t for t in tracks if t.canon == "dl_prb_util")
    for window, first, n_hours in ((2.0, "2026-09-13 14:00", 5), (0.5, "2026-09-13 15:00", 2)):
        wt, wv, lo, hi = window_series(*prb.by_site["BAS0001"][:2], pt, window)
        assert lo == pd.Timestamp(first) and hi == pt + pd.Timedelta(hours=window)
        assert len(wt) == n_hours


def test_the_delay_tickets_analysis_page(tmp_path, monkeypatch):
    monkeypatch.setenv("RFOPT_CACHE_DIR", str(tmp_path))
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    at = _app(AppTest, "views/complaint_analysis.py")
    at.run()
    assert not at.exception, at.exception                 # nothing uploaded yet

    from rfopt.complaints.target_store import save_target
    save_target(_target_bytes(), "Target 13-Sep.xlsx")
    at.run()
    assert not at.exception, at.exception
    bodies = _html(at)
    text = " ".join(bodies)
    for part in ("Delay Tickets Analysis", "Delay Tickets", "Tickets by Engineering",
                 "Tickets by Sup District", "Tickets by Governorate", "Ticket Type", "Top Sites",
                 "Target 13-Sep.xlsx"):
        assert part in text, part
    assert sum('class="ca-donut"' in b for b in bodies) == 4
    assert not any("Ticket Information" in b for b in bodies)   # nothing clicked yet
    assert at.segmented_control(key="ca_mode").value == "Overview"

    table = next(d.value for d in at.dataframe if "Ticket ID (file)" in d.value.columns)
    # every column of the file is there; the ones the analysis also shows keep "(file)"
    assert {"Ticket ID (file)", "MSISDN (file)", "City (file)", "Affected Services",
            "HPSM Incident ID", "Eng", "SLA Status", "Reopen Count", "User"} <= set(table.columns)
    assert list(table["MSISDN"]) == ["9647000000001", "9647000000002"]
    top = next(d.value for d in at.dataframe if "Delay Tickets" in d.value.columns)
    assert list(top["Site ID"]) == ["BAS0001"] and list(top["Tickets"]) == [1]

    # show / hide columns: a default set, every column on request
    assert "MSISDN" in at.session_state["ca_cols"] and "Eng" not in at.session_state["ca_cols"]
    at.button(key="ca_cols_all").click().run()
    assert not at.exception, at.exception
    assert set(at.session_state["ca_cols"]) == set(table.columns)

    # the filters work together; a donut legend and its Filters list are one filter
    at.multiselect(key="ca_f_eng_m").set_value(["Shams"]).run()
    assert not at.exception, at.exception
    assert at.session_state["ca_f_eng"] == ["Shams"]
    assert any("1 of 2" in b for b in _html(at))
    at.text_input(key="ca_f_msisdn").set_value("07000000002").run()     # CC-2, not Shams'
    assert any("0 of 2" in b for b in _html(at))
    at.multiselect(key="ca_f_eng_m").set_value([]).run()
    assert any("1 of 2" in b for b in _html(at))
    at.text_input(key="ca_f_msisdn").set_value("").run()
    at.text_input(key="ca_f_tid").set_value("cc-2").run()
    assert any("1 of 2" in b for b in _html(at))
    at.text_input(key="ca_f_tid").set_value("").run()

    # a clicked ticket opens in the Ticket Details view, without the overview
    at.session_state["ca_sel_tid"] = "CC-2"
    at.session_state["ca_mode_next"] = "Ticket Details"
    at.run()
    assert not at.exception, at.exception
    assert at.segmented_control(key="ca_mode").value == "Ticket Details"
    bodies = _html(at)
    text = " ".join(bodies)
    for part in ("Ticket Information", "Complainant Information", "Network Information",
                 "Technical Information", "KPI Evidence", "RSRP", "Final analysis"):
        assert part in text, part
    assert "IM2" in text and "9647000000002" in text and "REOPEN #2<" in text
    assert "More details" not in text
    assert "IS CMC" not in text and "<span>Priority</span>" not in text
    assert not any('class="ca-donut"' in b for b in bodies) and not any(
        "Ticket ID (file)" in d.value.columns for d in at.dataframe)
    assert at.selectbox(key="ca_pick").value == "CC-2"

    # walk the tickets, pick one, and go back
    at.button(key="ca_prev").click().run()
    assert not at.exception, at.exception
    assert at.session_state["ca_sel_tid"] == "CC-1" and at.button(key="ca_prev").disabled
    assert any("Ticket Information" in b and "IM1" in b for b in _html(at))
    at.selectbox(key="ca_pick").set_value("CC-2").run()
    assert at.session_state["ca_sel_tid"] == "CC-2" and at.button(key="ca_next").disabled
    at.button(key="ca_back").click().run()
    assert not at.exception, at.exception
    assert at.segmented_control(key="ca_mode").value == "Overview"
    assert not any("Ticket Information" in b for b in _html(at))
    assert sum('class="ca-donut"' in b for b in _html(at)) == 4

    # a Complaint (HPSM) ID typed in the search opens its ticket
    at.text_input(key="ca_q").set_value("IM1").run()
    assert not at.exception, at.exception
    assert at.segmented_control(key="ca_mode").value == "Ticket Details"
    assert at.session_state["ca_sel_tid"] == "CC-1"
    # governorate from the shared placement (no EP here: the site ID's prefix)
    assert any("<span>Governorate</span><b>Basrah</b>" in b for b in _html(at))


def test_the_correlation_window_drives_the_kpi_evidence(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("RFOPT_CACHE_DIR", str(tmp_path))
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    from rfopt.complaints.target_store import save_target
    save_target(_target_bytes(), "Target 13-Sep.xlsx")

    name, data = _kpi_export()
    from rfopt.resources import store
    f = store.put_file(name, data)
    f.role = "4G KPI"
    store.stage("kpi", [f])
    store.apply("kpi")
    at = _app(AppTest, "views/complaint_analysis.py")
    at.session_state["ca_sel_tid"] = "CC-1"
    at.session_state["ca_mode_next"] = "Ticket Details"
    at.run()
    assert not at.exception, at.exception

    def evidence():
        charts = at.get("plotly_chart")
        assert len(charts) == 1
        spec = json.loads(charts[0].proto.spec)
        window = next(sh for sh in spec["layout"]["shapes"] if sh.get("type") == "rect")
        text = " ".join(b for b in _html(at) if 'class="ca-evs"' in b or 'class="ca-evk"' in b)
        return spec, window, text

    # the chart draws the whole period of the export and shades the window
    spec, window, text = evidence()
    assert spec["data"][0]["type"] == "bar"                    # PRB: bars, not a line
    assert "10:00" in spec["layout"]["xaxis"]["range"][0]
    assert "14:00" in window["x0"] and "18:20" in window["x1"]
    assert "4G DL PRB" in text and "Critical" in text and "L_Alpha_BAS0001-1" in text
    assert "window 13 Sep 14:00 → 18:20 (±2 hours) shaded" in text
    assert any("Correlation window ±2 hours · problem 16:20" in a_.get("text", "")
               for a_ in spec["layout"]["annotations"])
    # the timeline is about one named KPI (the lead evidence KPI), with its peak
    timeline = " ".join(b for b in _html(at) if "Timeline KPI" in b)
    assert "<span>Timeline KPI</span><em>4G DL PRB</em>" in timeline
    for part in ("Before", "Problem Window", "After", "Peak Time", "KPI Value", "Threshold",
                 "Status"):
        assert part in timeline, part
    assert "13 Sep 15:00" in timeline and "95.0%" in timeline
    assert at.selectbox(key="ca_tl_kpi_CC-1").value == "4G DL PRB"
    # another KPI of the site: the timeline is redrawn for it alone, still named
    at.selectbox(key="ca_tl_kpi_CC-1").set_value("4G UL interference").run()
    timeline = " ".join(b for b in _html(at) if "Timeline KPI" in b).split('class="ca-res"')[0]
    assert "<span>Timeline KPI</span><em>4G UL interference</em>" in timeline
    assert "4G DL PRB" not in timeline and "Normal" in timeline

    at.selectbox(key="ca_win").set_value("±30 min").run()
    assert not at.exception, at.exception
    spec, window, text = evidence()
    assert "10:00" in spec["layout"]["xaxis"]["range"][0]      # still the whole period
    assert "15:00" in window["x0"] and "16:50" in window["x1"]
    assert "window 13 Sep 15:00 → 16:50 (±30 min) shaded" in text


def test_the_search_and_filters_come_back_after_another_page(tmp_path, monkeypatch):
    """Streamlit forgets a page's widgets when another page is opened: the
    search, the window and the filters are handed back on return."""
    monkeypatch.setenv("RFOPT_CACHE_DIR", str(tmp_path))
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    from rfopt.complaints.target_store import save_target
    save_target(_target_bytes(), "Target 13-Sep.xlsx")

    at = _app(AppTest, "Home.py")
    at.run()
    at.switch_page("views/complaint_analysis.py").run()
    assert not at.exception, at.exception
    at.text_input(key="ca_q").set_value("CC-1").run()           # a Ticket ID opens it
    assert at.segmented_control(key="ca_mode").value == "Ticket Details"
    assert any("Ticket Information" in b and "CC-1" in b for b in _html(at))
    at.selectbox(key="ca_win").set_value("±3 hours").run()
    at.segmented_control(key="ca_mode").set_value("Overview").run()
    at.multiselect(key="ca_f_eng_m").set_value(["Shams"]).run()
    assert not at.exception, at.exception

    at.switch_page("views/kpi_analysis.py").run()
    assert not at.exception, at.exception
    at.switch_page("views/complaint_analysis.py").run()
    assert not at.exception, at.exception
    assert at.text_input(key="ca_q").value == "CC-1"
    assert at.selectbox(key="ca_win").value == "±3 hours"
    assert at.session_state["ca_f_eng"] == ["Shams"]
    assert at.segmented_control(key="ca_mode").value == "Overview"
    at.segmented_control(key="ca_mode").set_value("Ticket Details").run()
    assert not at.exception, at.exception
    assert any("Ticket Information" in b and "CC-1" in b for b in _html(at))


def test_each_evidence_kpi_has_its_own_chart_type():
    """Bars for a utilisation, stems for a drop count, a line for a level, steps
    for a rate, an area for a failure count, dots for a latency — each over the
    whole period, the window shaded."""
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))
    from types import SimpleNamespace
    from _complaints import evidence_figure

    t = pd.date_range("2026-09-13 00:00", "2026-09-13 23:00", freq="h")
    pt = pd.Timestamp("2026-09-13 12:10")
    rule = SimpleNamespace(warning=80.0, critical=90.0, direction="down")

    def item(tag, values, rule=None):
        return dict(tag=tag, unit="%", all_times=t, all_values=values, rule=rule, pt=pt,
                    all_sev=[None] * len(t), lo=pd.Timestamp("2026-09-13 11:00"),
                    hi=pt + pd.Timedelta(hours=1))

    got, fig_types = {}, {}
    for tag in ("PRB", "FLOW", "INTER", "RTWP", "AVA", "S1", "IPL"):
        fig = evidence_figure(item(tag, [50.0] * len(t), rule), "±1 hour")
        tr = fig.data[0]
        fig_types[tag] = [d.type for d in fig.data]
        line = (tr.line.shape, tr.line.dash) if tr.type == "scatter" else (None, None)
        got[tag] = (tr.type, getattr(tr, "fill", None), *line)
        x0, x1 = fig.layout.xaxis.range
        assert pd.Timestamp(x0) == t[0] and pd.Timestamp(x1) == t[-1] + pd.Timedelta(hours=1)
        rect = [s for s in fig.layout.shapes if s.type == "rect"]
        assert len(rect) == 1 and pd.Timestamp(rect[0].x0) == pd.Timestamp("2026-09-13 11:00")
    assert got["PRB"][0] == "bar"
    assert got["FLOW"] == ("bar", None, None, None) and len(fig_types["FLOW"]) == 2
    assert got["INTER"] == ("scatter", None, None, None)
    assert got["RTWP"] == ("scatter", None, "spline", None)
    assert got["AVA"] == ("scatter", "tozeroy", "hv", None)
    assert got["S1"] == ("scatter", "tozeroy", "spline", None)
    assert got["IPL"] == ("scatter", None, None, "dot")
    assert len(set(got.values())) == 6 and fig_types["PRB"] == ["bar"]
