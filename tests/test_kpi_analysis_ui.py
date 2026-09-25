"""KPI Analysis: site health judged the way the Sites map judges, grouped by
Sup District and City, and the Overview and Draw Data pages built on it."""

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

PRB = "HW_DL PRB Avg Utilization(%)"
INTER = "L.UL.Interference.Avg(dBm)"
AVA = "LTE_Availability(%)@AB"


@pytest.fixture(autouse=True)
def _app_on_path():
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))


def _lte(hours: int = 24) -> pd.DataFrame:
    rows = []
    for h in pd.date_range("2026-09-13 00:00", periods=hours, freq="h"):
        # BAS0001-S1 is congested, BAS0001-S2 is not
        rows.append(dict(datetime=h, object="L_A_BAS0001-1", site_id="BAS0001",
                         sector_id="BAS0001-S1", duplex="CELL_FDD",
                         **{PRB: 90.0, INTER: -118.0, AVA: 100.0}))
        rows.append(dict(datetime=h, object="L_A_BAS0001-2", site_id="BAS0001",
                         sector_id="BAS0001-S2", duplex="CELL_FDD",
                         **{PRB: 30.0, INTER: -118.0, AVA: 100.0}))
        # BAS0002-S1 has an FDD and a TDD cell; only the TDD one is interfered
        rows.append(dict(datetime=h, object="L_B_BAS0002-1", site_id="BAS0002",
                         sector_id="BAS0002-S1", duplex="CELL_FDD",
                         **{PRB: 20.0, INTER: -120.0, AVA: 100.0}))
        rows.append(dict(datetime=h, object="T_B_BAS0002-1", site_id="BAS0002",
                         sector_id="BAS0002-S1", duplex="CELL_TDD",
                         **{PRB: 20.0, INTER: -104.0, AVA: 100.0}))
    return pd.DataFrame(rows)


def _site_info() -> pd.DataFrame:
    return pd.DataFrame(
        {"site_name": ["Alpha_BAS0001", "Beta_BAS0002"], "city": ["Basrah", "Basrah"],
         "district": ["Basrah center", "Zubair"],
         "sub_district": ["Shat Al-Arab", "Zubair center"],
         "latitude": [30.5248, 30.3900], "longitude": [47.8504, 47.7000]},
        index=pd.Index(["BAS0001", "BAS0002"], name="site_id"))


def test_sites_are_judged_on_the_maps_rule_and_tdd_cells_on_their_own():
    from _kpi_health import build_health, judged_columns, problem_ranking, summary

    judged = judged_columns([PRB, INTER, AVA, "Integrity"], "4G")
    assert {j.key for j in judged} == {"PRB", "INTER", "AVA"}     # Integrity has no rule
    h = build_health([("4G", _lte(), judged)])
    assert h.sites == ["BAS0001", "BAS0002"]

    s = summary(h.objects, h.tdd, h.sites, None)
    assert s["total"] == 2 and s["issues"] == 1 and s["prb"] == 1
    # the sector's cells averaged (-112 dBm) are fine; its TDD cell alone is not
    assert s["tdd"] == 1
    assert s["flow"] is None and s["rtwp"] is None                 # not loaded: no data
    status = s["status"]
    assert status.loc["BAS0001", "state"] == "Critical"
    assert status.loc["BAS0001", "label"] == "4G DL PRB"
    assert status.loc["BAS0002", "state"] == "Normal"
    assert s["distribution"]["Critical"] == 1 and s["distribution"]["Normal"] == 1

    rank = problem_ranking(h.objects)
    assert rank.iloc[0]["label"] == "4G DL PRB"
    assert rank.iloc[0]["sites"] == 1 and rank.iloc[0]["critical"] == 1

    only = summary(h.objects, h.tdd, h.sites, {"BAS0002"})
    assert only["total"] == 1 and only["issues"] == 0 and only["prb"] == 0


def test_flow_control_is_judged_per_24_hours_and_s1_is_only_counted():
    from _kpi_health import build_health, judged_columns, site_status, summary

    flow, rtwp, rtt = ("VS.RscGroup.FlowCtrol.DL.DropNum", "VS.MeanRTWP(dBm)",
                       "VS.IPPM.Rtt.Means(ms)")
    t = pd.date_range("2026-09-12 00:00", periods=48, freq="h")
    umts = pd.DataFrame({"datetime": t, "object": "N_BAS0003", "site_id": "BAS0003",
                         "sector_id": "BAS0003-S0", flow: [100.0] * 24 + [3000.0] * 24,
                         rtwp: -104.0, rtt: 2.0})
    lte = pd.DataFrame({"datetime": t, "object": "L_C_BAS0004-1", "site_id": "BAS0004",
                        "sector_id": "BAS0004-S1", "S1 SIG Failures": 1.0, AVA: 100.0})
    h = build_health([
        ("3G", umts, judged_columns([flow, rtwp, rtt, "3G_Availability@AB"], "3G")),
        ("4G", lte, judged_columns(["S1 SIG Failures", AVA], "4G"))])
    o = h.objects.set_index(["site_id", "key"])

    # the day of 3,000 drops an hour: 72,000 > the 50,000-a-day warning line
    assert o.loc[("BAS0003", "FLOW"), "value"] == 72000.0
    assert o.loc[("BAS0003", "FLOW"), "state"] == "Warning"
    assert o.loc[("BAS0003", "FLOW"), "peak_time"] == pd.Timestamp("2026-09-13 23:00")
    assert o.loc[("BAS0003", "RTWP"), "state"] == "Normal"
    assert o.loc[("BAS0003", "IPL"), "state"] == "Normal"
    assert o.loc[("BAS0004", "S1"), "state"] == "Detected"
    assert o.loc[("BAS0004", "S1"), "value"] == 48.0

    s = summary(h.objects, h.tdd, h.sites, None)
    assert s["flow"] == 1 and s["rtwp"] == 0 and s["tdd"] is None
    assert site_status(h.objects, h.sites).loc["BAS0004", "state"] == "Normal"


def test_a_sites_tiles_and_hours_show_its_worst_object():
    from _kpi_health import build_health, judged_columns, site_hours, site_tiles

    df = _lte(6)
    hit = (df["sector_id"] == "BAS0001-S2") & (df["datetime"] == pd.Timestamp("2026-09-13 03:00"))
    df.loc[hit, PRB] = 99.0
    frames = [("4G", df, judged_columns([PRB, INTER, AVA], "4G"))]
    h = build_health(frames)

    primary, _ = site_tiles(h.objects, "BAS0001")
    assert [t["key"] for t in primary] == ["AVA", "PRB", "INTER", "FLOW"]
    tiles = {t["key"]: t for t in primary}
    assert tiles["PRB"]["state"] == "Critical" and tiles["PRB"]["value"] == "90.0%"
    assert tiles["PRB"]["object"] == "BAS0001-S1"
    assert tiles["FLOW"]["state"] == "No data"

    prb = site_hours(frames, "BAS0001").query("key == 'PRB'").set_index("hour")
    three = pd.Timestamp("2026-09-13 03:00")
    assert prb.loc[three, "value"] == 99.0 and prb.loc[three, "object_id"] == "BAS0001-S2"
    assert (prb["sev"] == 2).all()


def test_issues_are_grouped_by_sup_district_and_city_with_a_half_window_trend():
    from _kpi_health import build_health, judged_columns
    from _kpi_region import (UNKNOWN, add_trend, area_counts, halves, overall_trend,
                             region_table, site_regions)

    df = _lte()
    # BAS0002 is congested in the second half of the day only
    df.loc[(df["site_id"] == "BAS0002") & (df["datetime"] >= "2026-09-13 12:00"), PRB] = 95.0
    frames = [("4G", df, judged_columns([PRB, INTER, AVA], "4G"))]
    h = build_health(frames)
    regions = site_regions(h.sites + ["BAS0009"], _site_info())
    # not in the EP tracker: its ID prefix still names its governorate, nothing below it
    assert regions.loc["BAS0009", "governorate"] == "Basrah"
    assert regions.loc["BAS0009", "city"] == UNKNOWN
    assert regions.loc["BAS0009", "sup_district"] == UNKNOWN
    # without the boundaries: the tracker's city is the governorate, its district the city
    assert regions.loc["BAS0002", "governorate"] == "Basrah"
    assert regions.loc["BAS0002", "city"] == "Zubair"

    prev, last, mid = halves(frames, h.start, h.end)
    assert mid == pd.Timestamp("2026-09-13 11:30")
    hp, hl = build_health(prev), build_health(last)

    sd = region_table(h.objects, regions, "Sup District")
    assert list(sd["name"]) == ["Shat Al-Arab", "Zubair center", UNKNOWN]
    assert area_counts(sd) == (1, 2)
    sd = add_trend(sd, hp.objects, hl.objects, regions, "Sup District").set_index("name")
    assert sd.loc["Shat Al-Arab", "affected"] == 1 and sd.loc["Shat Al-Arab", "critical"] == 1
    assert sd.loc["Shat Al-Arab", "top_issue"] == "4G DL PRB"
    assert sd.loc["Shat Al-Arab", "city"] == "Basrah center"
    assert sd.loc["Shat Al-Arab", "governorate"] == "Basrah"
    # over the whole day BAS0002 averages 57.5 % PRB: fine, but it is new in the second half
    assert sd.loc["Zubair center", "affected"] == 0 and sd.loc["Zubair center", "trend"] == 1.0
    assert sd.loc["Shat Al-Arab", "trend"] == 0.0

    gov = region_table(h.objects, regions, "Governorate").set_index("name")
    assert gov.loc["Basrah", "sites"] == 3 and gov.loc["Basrah", "affected"] == 1
    assert round(float(gov.loc["Basrah", "rate"]), 1) == 33.3
    city = region_table(h.objects, regions, "City").set_index("name")
    assert city.loc["Basrah center", "affected"] == 1 and city.loc["Zubair", "affected"] == 0

    t = overall_trend(hp.objects, hl.objects, regions)
    assert t["affected"] == 1 and t["Sup District"] == 1 and t["City"] == 1
    assert t["Governorate"] == 0
    assert halves(frames, h.start, h.start + pd.Timedelta(hours=3)) is None


def test_the_region_map_draws_an_area_per_marker():
    import folium

    from _kpi_health import build_health, judged_columns
    from _kpi_region import region_table, site_regions
    from _kpi_view import region_map

    h = build_health([("4G", _lte(), judged_columns([PRB, INTER, AVA], "4G"))])
    table = region_table(h.objects, site_regions(h.sites, _site_info()), "Sup District")
    fmap = region_map(table, "Sup District", selected="Shat Al-Arab")
    circles = [c for c in fmap._children.values() if isinstance(c, folium.CircleMarker)]
    tips = [ch.text for c in circles for ch in c._children.values()
            if isinstance(ch, folium.Tooltip)]
    # the tooltip leads with the area's name: that is what a click hands back
    assert [t.split(" · ")[0] for t in tips] == ["Shat Al-Arab", "Zubair center"]
    assert len(circles) == 3                                    # plus the selection ring
    html = fmap.get_root().render()
    assert "World_Dark_Gray_Base" in html and "#EF4444" in html  # dark tiles, a critical area


_CSV_HEAD = ("Time,eNodeB Name,Cell FDD TDD Indication,Cell Name,LocalCell Id,"
             f"{PRB},{INTER},{AVA}\n")


def _export_on_disk(tmp_path, monkeypatch):
    import _kpi_workspace
    import _shared

    desk = tmp_path / "desk"
    desk.mkdir()
    monkeypatch.setattr(_shared, "DL", desk)            # the Excel export's destination
    monkeypatch.setenv("RFOPT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(_kpi_workspace, "ep_sites", _site_info)
    # the EP tracker's sub-districts: the official boundaries have their own tests
    monkeypatch.setattr(_kpi_workspace, "areas", lambda: ())
    lines = [_CSV_HEAD]
    for hh in range(6):
        t = f"2026-09-13 {hh:02d}:00"
        lines.append(f"{t},Alpha_BAS0001,CELL_FDD,L_Alpha_BAS0001-1,1,90,-118,100\n")
        lines.append(f"{t},Alpha_BAS0001,CELL_FDD,L_Alpha_BAS0001-2,2,30,-118,100\n")
        lines.append(f"{t},Beta_BAS0002,CELL_TDD,T_Beta_BAS0002-1,1,20,-104,100\n")
    # the export is the Current KPI Data of Data Resources, as the pages read it
    from rfopt.resources import store
    f = store.put_file("R5 4G Monitoring Hourly KPI.csv", "".join(lines).encode("utf-8"))
    f.role = "4G KPI"
    store.stage("kpi", [f])
    store.apply("kpi")


def _rings(at) -> str:
    return next(e.proto.body for e in at.get("html") if "ka-rgs" in e.proto.body)


def _table(at, column: str):
    return next((d.value for d in at.dataframe if column in d.value.columns), None)


def _area(at, level: str):
    """An area table: its first column after # is the level."""
    return next((d.value for d in at.dataframe
                 if len(d.value.columns) > 1 and d.value.columns[1] == level), None)


def test_the_overview_filters_every_panel_at_once(tmp_path, monkeypatch):
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    _export_on_disk(tmp_path, monkeypatch)

    at = AppTest.from_file(str(APP / "views/kpi_analysis.py"), default_timeout=180)
    at.run()
    assert not at.exception, at.exception
    text = " ".join(e.proto.body for e in at.get("html"))
    for part in ("KPI Issues by Area", "KPI Issues by Governorate", "Top Sup Districts by KPI Issues",
                 "Top Cities by KPI Issues", "KPI Issues Map", "KPI Issues Table",
                 "KPI Details by Site / Cell"):
        assert part in text, part
    assert "Draw Selected Charts" not in [b.label for b in at.button]
    # two tabs; the filters are KPI, Governorate, Sup District, Site, Time Period — no City,
    # no Prefix
    assert at.segmented_control(key="ka_view").options == ["KPI Analysis", "Report Export"]
    labels = {w.label for w in list(at.selectbox) + list(at.multiselect)}
    assert {"KPI", "Governorate", "Sup District", "Site"} <= labels
    assert not labels & {"City", "Prefix"}
    assert at.get("date_input")[0].label == "Time Period"

    # one ring strip — no card view — with the counts of the loaded judgement
    rings = _rings(at)
    assert rings.count('class="ka-rg"') == 6 and "rf-kpi" not in rings
    assert "<b>2</b><span>Total Sites</span>" in rings
    # BAS0001 is congested, BAS0002's only cell (TDD) is interfered
    assert "<b>2</b><span>Sites with Issues</span>" in rings
    assert "<b>1</b><span>High PRB</span>" in rings and "of 2 measured sites" in rings
    assert "<b>1</b><span>TDD Interference</span>" in rings
    assert "No data</b><span>Flow Control</span>" in rings
    assert "No data</b><span>RTWP High</span>" in rings

    sd = _area(at, "Sup District")
    assert list(sd["Sup District"]) == ["Shat Al-Arab", "Zubair center"]
    assert list(sd["Issue Sites"]) == [1, 1]
    assert list(_area(at, "Governorate")["Governorate"]) == ["Basrah"]
    assert set(_area(at, "City")["City"]) == {"Basrah center", "Zubair"}
    tbl = _table(at, "Cell Name")
    assert set(tbl["Site ID"]) == {"BAS0001", "BAS0002"}
    for col in ("Status", "Site ID", "Site Name", "Cell ID", "Cell Name", "Governorate",
                "Sup District", "City", "Technology", "KPI", "Issue", "KPI Value", "Threshold",
                "Worst Hour", "Worst Value"):
        assert col in tbl.columns, col
    prb = tbl[tbl["KPI"] == "4G DL PRB"].iloc[0]
    assert prb["Cell Name"] == "L_Alpha_BAS0001-1" and prb["Governorate"] == "Basrah"
    assert prb["City"] == "Basrah center" and prb["Technology"] == "4G"
    assert prb["Issue"] == "High DL PRB"

    # a Sup District narrows the rings and the table, and leaves the districts listed
    at.multiselect(key="ka_f_sd").set_value(["Zubair center"]).run()
    assert not at.exception, at.exception
    assert "<b>1</b><span>Total Sites</span>" in _rings(at)
    tbl = _table(at, "Cell Name")
    assert set(tbl["Site ID"]) == {"BAS0002"} and set(tbl["KPI"]) == {"4G UL interference"}
    assert list(_area(at, "Sup District")["Sup District"]) == ["Shat Al-Arab", "Zubair center"]
    # both Sup Districts at once
    at.multiselect(key="ka_f_sd").set_value(["Shat Al-Arab", "Zubair center"]).run()
    assert "<b>2</b><span>Total Sites</span>" in _rings(at)
    # the search reaches every column of the table
    at.text_input(key="ka_f_q").set_value("basrah center prb").run()
    assert set(_table(at, "Cell Name")["Site ID"]) == {"BAS0001"}
    at.text_input(key="ka_f_q").set_value("").run()

    # KPI selection drives the page too: only PRB is left to judge
    at.button(key="ka_clear_all").click().run()
    at.checkbox(key=f"kpi_cb_{PRB}").check().run()
    assert not at.exception, at.exception
    assert set(_table(at, "Cell Name")["KPI"]) == {"4G DL PRB"}
    sd = _area(at, "Sup District").set_index("Sup District")
    assert sd.loc["Zubair center", "Issue Sites"] == 0
    assert "none under the current filters" in _rings(at)       # TDD interference is not picked

    # the picked row opens its site's KPI details
    at.session_state["ka_sel"] = "BAS0001"
    at.session_state["ka_sel_obj"] = "BAS0001-S1"
    at.session_state["ka_sel_kpi"] = "4G DL PRB"
    at.run()
    assert not at.exception, at.exception
    details = " ".join(e.proto.body for e in at.get("html") if "ka-dr" in e.proto.body)
    assert "BAS0001" in details and "Alpha_BAS0001" in details and "CRITICAL" in details
    assert "Basrah / Basrah center / Shat Al-Arab" in details
    assert "Selected in the table" in details
    assert "KPI timeline" in details
    # the timeline is about one named KPI — the row's — with its phases and peak
    assert "<span>Timeline KPI</span><em>4G DL PRB</em>" in details
    for part in ("Before", "Problem Window", "After", "Peak Time", "KPI Value", "Threshold",
                 "Status"):
        assert part in details, part
    assert at.selectbox(key="ka_tl_kpi_BAS0001").value == "4G DL PRB"


def test_draw_data_draws_the_picked_kpis(tmp_path, monkeypatch):
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    _export_on_disk(tmp_path, monkeypatch)

    at = AppTest.from_file(str(APP / "views/kpi_draw.py"), default_timeout=180)
    at.run()
    assert not at.exception, at.exception
    assert "Tick KPIs under KPI selection" in " ".join(e.proto.body for e in at.get("html"))

    # arriving from the Overview: the picks and the drawing are in session, the
    # page's own boxes are not
    at = AppTest.from_file(str(APP / "views/kpi_draw.py"), default_timeout=180)
    at.session_state["kpi_pick"] = {PRB}
    at.session_state["kpi_tech"] = "4G"
    at.session_state["kpi_drawn"] = ((PRB,), "Network", None, ())
    at.run()
    assert not at.exception, at.exception
    assert at.checkbox(key=f"kpi_cb_{PRB}").value            # the pick shows ticked
    assert len(at.get("plotly_chart")) == 1
    assert [t.label for t in at.tabs] == ["Charts", "Trend Summary"]
    assert at.dataframe[0].value.iloc[0]["KPI"] == PRB
    html = " ".join(e.proto.body for e in at.get("html"))
    assert 'class=\'rf-copy\'' in html and "Copy Chart" in html
    assert "navigator.clipboard.write" in html and "image/png" in html

    # Clear Charts takes the drawing away, and nothing else
    at.button(key="kpi_clear").click().run()
    assert not at.exception, at.exception
    assert "kpi_drawn" not in at.session_state and not at.get("plotly_chart")
    assert "Tick KPIs under KPI selection" in " ".join(e.proto.body for e in at.get("html"))
    assert at.session_state["kpi_pick"] == {PRB}


def test_draw_data_draws_a_city(tmp_path, monkeypatch):
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    _export_on_disk(tmp_path, monkeypatch)

    at = AppTest.from_file(str(APP / "views/kpi_draw.py"), default_timeout=180)
    at.session_state["kpi_pick"] = {PRB}
    at.session_state["kpi_tech"] = "4G"
    at.session_state["kpi_drawn"] = ((PRB,), "City", "Basrah", ())
    at.run()
    assert not at.exception, at.exception
    assert len(at.get("plotly_chart")) == 1
    assert "charts for <b>Basrah</b>" in " ".join(e.proto.body for e in at.get("html"))


def test_the_picks_carry_between_overview_and_draw_data(tmp_path, monkeypatch):
    """Both KPI pages draw the same sidebar and bar under the same keys: a KPI
    ticked on one is still ticked on the other, and back again."""
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    _export_on_disk(tmp_path, monkeypatch)

    at = AppTest.from_file(str(APP / "Home.py"), default_timeout=300)
    at.run()
    at.switch_page("views/kpi_analysis.py").run()
    assert not at.exception, at.exception
    at.checkbox(key=f"kpi_cb_{PRB}").check().run()
    assert at.session_state["kpi_pick"] == {PRB}

    at.switch_page("views/kpi_draw.py").run()
    assert not at.exception, at.exception
    assert at.checkbox(key=f"kpi_cb_{PRB}").value

    at.switch_page("views/kpi_analysis.py").run()
    assert not at.exception, at.exception
    assert at.checkbox(key=f"kpi_cb_{PRB}").value
    assert at.session_state["kpi_pick"] == {PRB}


def test_the_excel_export_still_writes_its_workbook(tmp_path, monkeypatch):
    import _kpi_workspace as W
    import _shared
    from rfopt.ingest.hourly_kpi import sniff_kpi_export

    _export_on_disk(tmp_path, monkeypatch)
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setattr(_shared, "DL", downloads)
    from rfopt.resources import store
    src = str(store.active_files("kpi")[0].path)             # the stored export

    msg = W.export([PRB], [(src, sniff_kpi_export(src))])
    assert msg.startswith("Saved to Downloads"), msg
    assert len(list(downloads.glob("*.xlsx"))) == 1
    assert W.export(["Not a KPI"], [(src, sniff_kpi_export(src))]).startswith("None of those")
