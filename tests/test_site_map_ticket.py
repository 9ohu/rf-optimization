"""The Sites map's ticket panels, and the re-analysis at a user location.

A Daily Target ticket searched on the Sites map shows the same Ticket
Information and the same analysis as Delay Tickets Analysis. Its general
analysis (stage 1) judges the site; a user location approved on the map
(stage 2) re-judges the ticket on the sector that actually serves that point,
and both pages then show that one result for the Ticket ID.
"""

import io
import math
import sys
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from rfopt.complaints import relocate as RL
from rfopt.complaints.correlate import NO_ISSUE, TECHNICAL, build_tracks, judged_kpis

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

PRB = "HW_DL PRB Avg Utilization(%)"
SITE = (30.50, 47.80)


def _at(bearing_deg: float, metres: float) -> tuple[float, float]:
    lat = SITE[0] + metres * math.cos(math.radians(bearing_deg)) / 111_320.0
    lon = SITE[1] + metres * math.sin(math.radians(bearing_deg)) / (
        111_320.0 * math.cos(math.radians(SITE[0])))
    return lat, lon


def _sectors() -> pd.DataFrame:
    return pd.DataFrame({"sector_id": ["BAS0001-S1", "BAS0001-S2", "BAS0001-S3"],
                         "site_id": ["BAS0001"] * 3, "latitude": [SITE[0]] * 3,
                         "longitude": [SITE[1]] * 3, "azimuth_deg": [0.0, 120.0, 240.0]})


def _kpi_csv() -> str:
    """BAS0001 sector 1 congested 15:00-17:00 (problem 16:20); sector 2 normal."""
    head = ("Time,eNodeB Name,Cell FDD TDD Indication,Cell Name,LocalCell Id,"
            f"{PRB},L.UL.Interference.Avg(dBm),LTE_Availability(%)@AB")
    rows = [head]
    for h in pd.date_range("2026-09-13 10:00", "2026-09-13 23:00", freq="h"):
        rows.append(f"{h:%Y-%m-%d %H:%M},Alpha_BAS0001,CELL_FDD,L_Alpha_BAS0001-1,1,"
                    f"{95 if 15 <= h.hour <= 17 else 30},-118,100")
        rows.append(f"{h:%Y-%m-%d %H:%M},Alpha_BAS0001,CELL_FDD,L_Alpha_BAS0001-2,2,"
                    "35,-118,100")
    return "\n".join(rows) + "\n"


def _frames():
    from rfopt.ingest.hourly_kpi import load_hourly_raw
    raw = load_hourly_raw(io.BytesIO(_kpi_csv().encode()),
                          [PRB, "L.UL.Interference.Avg(dBm)", "LTE_Availability(%)@AB"])
    judged = judged_kpis(list(raw.columns), "4G")
    return [("4G", "kpi.csv", raw, judged)]


# --------------------------------------------------------------------------- #
# the re-analysis itself
# --------------------------------------------------------------------------- #
def test_the_serving_sector_is_the_one_pointed_at_the_user():
    lat, lon = _at(120, 300)
    s = RL.best_server(lat, lon, _sectors())
    assert s.sector_id == "BAS0001-S2" and s.site_id == "BAS0001"
    assert abs(s.distance_m - 300) < 3 and s.az_diff_deg < 2
    assert RL.best_server(lat, lon, _sectors().iloc[0:0]) is None
    assert RL.sector_of("BAS0001", "L_Alpha_BAS0001-3") == "BAS0001-S3"
    assert RL.sector_of("BAS0001", "Alpha_BAS0001") == ""
    assert RL.parse_latlon("47.7831, 30.5082") == (30.5082, 47.7831)
    assert RL.parse_latlon("nowhere") is None


def test_the_user_location_is_judged_on_its_own_sector_not_the_sites_worst():
    frames = _frames()
    site_tracks, sec_tracks = build_tracks(frames), RL.sector_tracks(frames)
    pt = pd.Timestamp("2026-09-13 16:20")
    from rfopt.complaints.correlate import analyse_ticket

    # stage 1: the site's worst cell is sector 1, congested
    general = analyse_ticket("BAS0001", pt, site_tracks, 2.0)
    assert general.classification == TECHNICAL
    assert RL.sector_of("BAS0001", RL.lead_check(general).worst_obj) == "BAS0001-S1"

    # stage 2: the user is on sector 2 — its own KPIs are normal
    lat, lon = _at(120, 300)
    r = RL.reanalyse(lat, lon, pt, _sectors(), sec_tracks, site_tracks, 2.0,
                     site_id="BAS0001")
    assert r.sector_id == "BAS0001-S2" and r.analysis.classification == NO_ISSUE
    assert r.how == RL.TICKET
    assert r.description.startswith("The serving sector is BAS0001-S2, with a distance of "
                                     "300 m from the user location and an azimuth difference")
    assert r.description.endswith("The RSRP measurement at the user location is N/A.")
    assert r.rsrp is None and r.rsrp_note == "no coverage grid loaded"

    # on sector 1 the congestion is still its own
    lat, lon = _at(0, 250)
    r = RL.reanalyse(lat, lon, pt, _sectors(), sec_tracks, site_tracks, 2.0,
                     site_id="BAS0001")
    assert r.sector_id == "BAS0001-S1" and r.analysis.classification == TECHNICAL


def test_a_kpi_the_export_only_has_per_site_falls_back_to_the_site():
    frames = _frames()
    site_tracks = build_tracks(frames)
    got = RL.tracks_for("BAS0001-S2", "BAS0001", [], site_tracks)
    assert got and all(set(t.by_site) == {"BAS0001-S2"} for t in got)


# --------------------------------------------------------------------------- #
# the pages: Sites map search -> approve -> Delay Tickets Analysis agrees
# --------------------------------------------------------------------------- #
def _kmz(extra=()) -> bytes:
    """The site KMZ: BAS0001 (three sectors at SITE), plus `extra` sites as
    (site, (lat, lon), [(sector, azimuth), ...])."""
    def balloon(site, sec, az):
        return (f"<b>Alpha_{site}</b><br/>Site Code: {site} &nbsp; Sector: {sec} &nbsp; "
                f"Azimuth: {az}&deg;<br/>Height: 30 m &nbsp; Status: On Air<br/><br/>"
                "<b>4G (LTE) Cells</b><br/>"
                f"<u>L_Alpha_{site}-{sec}</u>: Azimuth={az}&deg;, PCI=9{sec}, EARFCN=1750, "
                "BW=20MHz, Status=Active<br/>")

    def pm(b, at):
        lat, lon = at
        return (f"<Placemark><styleUrl>#onair</styleUrl><description><![CDATA[{b}]]>"
                "</description><Polygon><outerBoundaryIs><LinearRing><coordinates>"
                f"{lon},{lat},0 {lon + 0.001},{lat + 0.001},0 {lon},{lat},0"
                "</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>")

    sites = [("BAS0001", SITE, [(1, 0), (2, 120), (3, 240)]), *extra]
    kml = ("<?xml version='1.0'?><kml><Document>"
           + "".join(pm(balloon(site, sec, az), at)
                     for site, at, secs in sites for sec, az in secs)
           + "</Document></kml>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("doc.kml", kml)
    return buf.getvalue()


def _html(at) -> list[str]:
    return [e.proto.body for e in at.get("html")]


def test_a_ticket_searched_on_the_map_and_re_analysed_at_the_user(tmp_path, monkeypatch,
                                                                    put_resource):
    monkeypatch.setenv("RFOPT_CACHE_DIR", str(tmp_path))
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    from test_complaint_analysis import _target_bytes

    from rfopt.complaints.target_store import save_target
    save_target(_target_bytes(), "Target 13-Sep.xlsx")
    put_resource("kpi", "R5 4G Monitoring Hourly KPI.csv", _kpi_csv().encode(), "4G KPI")
    put_resource("kmz", "R5_Sites.kmz", _kmz(), "Site KMZ")

    at = AppTest.from_file(str(APP / "views/site_map.py"), default_timeout=300)
    at.run()
    assert not at.exception, at.exception
    text = " ".join(_html(at))
    # the sidebar sections are gone; three panels of one size under the map
    for gone in ("Map symbols", "Worst sectors", "Worst Areas", "LTE coverage", "Site status",
                 "Ticket Information", "Main Issue KPI", "Site area RSRP"):
        assert gone not in text, gone
    for part in ("Map layers &amp; Analysis", "Ticket ID", "User Location",
                 "Serving Site KPI", "Neighbour Sector KPI", "Analysis Result"):
        assert part in text, part
    src = (APP / "views" / "site_map.py").read_text(encoding="utf-8")
    assert 'st.columns(3, gap="small")' in src and "height=_PANEL_H" in src
    assert not at.sidebar.get("expandable")               # nothing left in the sidebar
    # no KPI cards over the map any more
    assert 'class="rf-kpi"' not in text

    # stage 1: the ticket, its general analysis, the map on its worst sector
    at.text_input(key="sm_tid_in").set_value("IM1").run()
    assert not at.exception, at.exception
    text = " ".join(_html(at))
    caps = " ".join(c.value for c in at.caption)
    assert "CC-1 · IM1 · BAS0001 · problem 13 Sep 16:20" in caps
    # no user location: the site's cells are analysed, no serving sector is picked
    assert "1 · General analysis (site level)" in text
    assert "no user location: the site&#x27;s cells are analysed" in text or \
        "no user location: the site's cells are analysed" in text
    assert "(worst cell)" not in text
    assert at.session_state.get("sm_sel_sector") != "BAS0001-S1"
    assert len(at.get("plotly_chart")) == 1                # the serving site's KPI only
    assert "Comment" in text and "sector Expansion Needed" in text
    assert "Neighbour sectors are the sectors of other sites facing the user" in " ".join(
        c.value for c in at.caption)
    assert at.button(key="sm_loc_go").disabled

    # a location typed is not analysed until it is approved
    lat, lon = _at(120, 300)
    at.text_input(key="sm_loc_in").set_value(f"{lat:.6f}, {lon:.6f}").run()
    assert "1 · General analysis" in " ".join(_html(at))
    assert not at.button(key="sm_loc_go").disabled
    at.button(key="sm_loc_go").click().run()
    assert not at.exception, at.exception
    text = " ".join(_html(at))
    assert "2 · User location" in text
    assert "<span>Serving sector</span><b>BAS0001-S2</b>" in text
    assert NO_ISSUE in text and at.session_state["sm_sel_sector"] == "BAS0001-S2"
    # the Comment: no network issue at the serving sector, its facts, the advice
    assert "No Network Issue Detected" in text
    assert "customer’s serving site sector BAS0001-S2 with distance 300m" in text
    assert "All technical checks were normal with no faults dedicated" in text
    # neither the neighbour list nor the Description is in the Analysis Result
    assert "<span>Neighbour sectors</span>" not in text
    assert "<span>Description</span>" not in text

    # the same ticket on Delay Tickets Analysis: one result
    ca = AppTest.from_file(str(APP / "views/complaint_analysis.py"), default_timeout=300)
    ca.session_state["ca_sel_tid"] = "CC-1"
    ca.session_state["ca_mode_next"] = "Ticket Details"
    ca.run()
    assert not ca.exception, ca.exception
    text = " ".join(_html(ca))
    assert "<span>Serving sector</span><b>BAS0001-S2</b>" in text
    assert "The serving sector is BAS0001-S2, with a distance of 300 m" in text
    assert NO_ISSUE in text

    # Clear on the map: back to the general analysis on both pages
    at.button(key="sm_loc_clear").click().run()
    assert "1 · General analysis" in " ".join(_html(at))
    ca.run()
    assert "<span>Serving sector</span>" not in " ".join(_html(ca))


# --------------------------------------------------------------------------- #
# the RSRP panel: whatever the coverage record carries, it renders
# --------------------------------------------------------------------------- #
FULL_AREA = {"median": -85.4, "weak_pct": 0.0, "grids": 366, "mrs": 1200.0,
             "radius_m": 500.0, "weak_dbm": -110.0}


def test_the_rsrp_row_reads_every_area_field_optionally():
    import _complaints as C
    from rfopt.geo.coverage import load_bands
    bands = load_bands()[0]
    # the full site-area record: as before
    _, scale, status = C.rsrp_row(FULL_AREA, bands, True)
    assert "-85 dBm" in scale and "-85.4 dBm" in status
    assert "site area ≤500 m · MR-weighted median" in status
    assert "0.0% of MRs below -110 dBm · 366 grids" in status
    # no radius (or no MR detail): the value and its band, without the missing parts
    for area in ({k: v for k, v in FULL_AREA.items() if k != "radius_m"},
                 {**FULL_AREA, "radius_m": None}, {"median": -98.7}):
        _, scale, status = C.rsrp_row(area, bands, True)
        assert "dBm" in scale and "MR-weighted median" in status and "≤" not in status
    # nothing to read: the existing No data state
    for area in (None, {}, {"median": None}, {"median": float("nan")}):
        assert C.rsrp_band(area, bands) is None
        _, _, status = C.rsrp_row(area, bands, True)
        assert "No data" in status


@pytest.mark.parametrize("grid", ["none", "value", "no-grid-here"])
def test_the_comment_reads_the_rsrp_at_the_user_location(tmp_path, monkeypatch,
                                                         put_resource, grid):
    """The Analysis Result's Comment: the RSRP of the grid cell at the approved
    user location; no grid under the point is a Coverage issue."""
    monkeypatch.setenv("RFOPT_CACHE_DIR", str(tmp_path))
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    import _complaints as C
    import _relocate
    from test_complaint_analysis import _target_bytes
    from rfopt.complaints.target_store import save_target

    save_target(_target_bytes(), "Target 13-Sep.xlsx")
    put_resource("kpi", "R5 4G Monitoring Hourly KPI.csv", _kpi_csv().encode(), "4G KPI")
    put_resource("kmz", "R5_Sites.kmz", _kmz(), "Site KMZ")
    _relocate.approve("CC-1", *_at(120, 300))

    real = C.load_workspace

    def with_coverage(*a, **k):
        ctx = real(*a, **k)
        for r in ctx.re.values():
            r.rsrp = -98.7 if grid == "value" else None
            r.no_grid = r.coverage_issue = grid == "no-grid-here"
        return ctx

    monkeypatch.setattr(C, "load_workspace", with_coverage)
    at = AppTest.from_file(str(APP / "views/site_map.py"), default_timeout=300)
    at.session_state["sm_tid"] = "CC-1"
    at.run()
    assert not at.exception, at.exception
    block = next(b for b in _html(at) if 'class="sm-cm-t"' in b)
    comment = block.split('<pre class="sm-cm-t">', 1)[1].split("</pre>", 1)[0]
    assert "customer’s serving site sector BAS0001-S2 with distance 300m" in comment
    lat, lon = _at(120, 300)
    assert f"User Location: {lat:.5f}, {lon:.5f}." in comment
    if grid == "value":
        assert "and the RSRP was -99," in comment and "No Network Issue Detected" in comment
    elif grid == "none":
        assert "and the RSRP was N/A," in comment
    else:
        assert "Root cause: Coverage" in comment
        assert "no RSRP coverage at the user location" in comment
        assert "No Network Issue Detected" not in comment


# --------------------------------------------------------------------------- #
# one ticket, two pages: Sites map <-> Delay Tickets Analysis, no startup again
# --------------------------------------------------------------------------- #
def test_a_ticket_moves_between_the_sites_map_and_delay_tickets_analysis(
        tmp_path, monkeypatch, put_resource):
    monkeypatch.setenv("RFOPT_CACHE_DIR", str(tmp_path))
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    from test_complaint_analysis import _target_bytes
    from rfopt.complaints.target_store import save_target

    save_target(_target_bytes(), "Target 13-Sep.xlsx")
    put_resource("kpi", "R5 4G Monitoring Hourly KPI.csv", _kpi_csv().encode(), "4G KPI")
    put_resource("kmz", "R5_Sites.kmz", _kmz(), "Site KMZ")
    at = AppTest.from_file(str(APP / "Home.py"), default_timeout=300)
    at.run()
    assert at.session_state["_rf_prepared"]
    at.switch_page("views/site_map.py").run()
    assert not at.exception, at.exception
    assert at.button(key="sm_open_ca").disabled                 # no ticket yet
    at.text_input(key="sm_tid_in").set_value("IM1").run()
    assert not at.button(key="sm_open_ca").disabled

    # Sites map -> Delay Tickets Analysis: the same ticket, in Ticket Details
    at.button(key="sm_open_ca").click().run()
    assert not at.exception, at.exception
    assert at.segmented_control(key="ca_mode").value == "Ticket Details"
    assert at.session_state["ca_sel_tid"] == "CC-1"
    assert any("Ticket Information" in b and "CC-1" in b for b in _html(at))
    # the startup screen is not shown again on a page switch
    assert not any("rf-startup" in b for b in _html(at))

    # Delay Tickets Analysis -> Sites map: the same ticket opens there
    at.button(key="ca_open_sm").click().run()
    assert not at.exception, at.exception
    # (AppTest sends back the field's last typed value, "IM1" — CC-1's own
    # HPSM ID; a browser drops a widget's state when its page is left)
    assert "sm_tid_next" not in at.session_state
    caps = " ".join(c.value for c in at.caption)
    assert "CC-1 · IM1 · BAS0001" in caps
    assert "Analysis Result" in " ".join(_html(at))
    assert not any("rf-startup" in b for b in _html(at))


def test_the_neighbour_sector_kpi_panel_draws_each_neighbour_on_all_its_cells(
        tmp_path, monkeypatch, put_resource):
    """A second site beyond the user location, its sector facing the user: the
    Neighbour Sector KPI panel picks it and draws its KPIs, cell by cell."""
    monkeypatch.setenv("RFOPT_CACHE_DIR", str(tmp_path))
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    import _relocate
    from test_complaint_analysis import _target_bytes
    from rfopt.complaints.target_store import save_target

    far = _at(120, 700)                        # 400 m past the user, same bearing
    csv = _kpi_csv().rstrip("\n").split("\n")
    for h in pd.date_range("2026-09-13 10:00", "2026-09-13 23:00", freq="h"):
        csv.append(f"{h:%Y-%m-%d %H:%M},Alpha_BAS0002,CELL_FDD,L_Alpha_BAS0002-1,1,"
                   f"{90 if 15 <= h.hour <= 17 else 40},-118,100")
    save_target(_target_bytes(), "Target 13-Sep.xlsx")
    put_resource("kpi", "R5 4G Monitoring Hourly KPI.csv", ("\n".join(csv) + "\n").encode(),
                 "4G KPI")
    put_resource("kmz", "R5_Sites.kmz",
                 _kmz([("BAS0002", far, [(1, 300), (2, 60), (3, 180)])]), "Site KMZ")
    _relocate.approve("CC-1", *_at(120, 300))

    at = AppTest.from_file(str(APP / "views/site_map.py"), default_timeout=300)
    at.session_state["sm_tid"] = "CC-1"
    at.run()
    assert not at.exception, at.exception
    sector = at.selectbox(key="sm_nb_sec")         # the neighbour sectors facing the user
    assert sector.options == ["BAS0002-S1 · Technical Issue"]
    kpi = at.selectbox(key="sm_nb_pick")           # its KPIs, the issue one first
    assert kpi.options[0] == "PRB - 4G DL PRB · Critical"
    assert len(at.get("plotly_chart")) == 2         # the serving sector's + the neighbour's
    assert "BAS0002-S1 · Critical 90.0%" in " ".join(c.value for c in at.caption)
    # the neighbour is not listed in the Analysis Result
    result = next(b for b in _html(at) if "sm-cm-t" in b)
    assert "BAS0002" not in result and "<span>Neighbour sectors</span>" not in result
