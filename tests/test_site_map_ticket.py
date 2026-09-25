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
    r = RL.reanalyse(lat, lon, pt, _sectors(), sec_tracks, site_tracks, 2.0)
    assert r.sector_id == "BAS0001-S2" and r.analysis.classification == NO_ISSUE
    assert r.description.startswith("The serving sector is BAS0001-S2, with a distance of "
                                     "300 m from the user location and an azimuth difference")
    assert r.description.endswith("The RSRP measurement at the user location is N/A.")
    assert r.rsrp is None and r.rsrp_note == "no coverage grid loaded"

    # on sector 1 the congestion is still its own
    lat, lon = _at(0, 250)
    r = RL.reanalyse(lat, lon, pt, _sectors(), sec_tracks, site_tracks, 2.0)
    assert r.sector_id == "BAS0001-S1" and r.analysis.classification == TECHNICAL


def test_a_kpi_the_export_only_has_per_site_falls_back_to_the_site():
    frames = _frames()
    site_tracks = build_tracks(frames)
    got = RL.tracks_for("BAS0001-S2", "BAS0001", [], site_tracks)
    assert got and all(set(t.by_site) == {"BAS0001-S2"} for t in got)


# --------------------------------------------------------------------------- #
# the pages: Sites map search -> approve -> Delay Tickets Analysis agrees
# --------------------------------------------------------------------------- #
def _kmz() -> bytes:
    def balloon(sec, az):
        return (f"<b>Alpha_BAS0001</b><br/>Site Code: BAS0001 &nbsp; Sector: {sec} &nbsp; "
                f"Azimuth: {az}&deg;<br/>Height: 30 m &nbsp; Status: On Air<br/><br/>"
                "<b>4G (LTE) Cells</b><br/>"
                f"<u>L_Alpha_BAS0001-{sec}</u>: Azimuth={az}&deg;, PCI=9{sec}, EARFCN=1750, "
                "BW=20MHz, Status=Active<br/>")

    def pm(b):
        lon, lat = SITE[1], SITE[0]
        return (f"<Placemark><styleUrl>#onair</styleUrl><description><![CDATA[{b}]]>"
                "</description><Polygon><outerBoundaryIs><LinearRing><coordinates>"
                f"{lon},{lat},0 {lon + 0.001},{lat + 0.001},0 {lon},{lat},0"
                "</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>")

    kml = ("<?xml version='1.0'?><kml><Document>"
           + "".join(pm(balloon(s, az)) for s, az in ((1, 0), (2, 120), (3, 240)))
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
    # the sidebar sections are gone; four panels of one size under the map
    for gone in ("Map symbols", "Worst sectors", "Worst Areas", "LTE coverage", "Site status"):
        assert gone not in text, gone
    for part in ("Map layers &amp; Analysis", "Ticket ID", "User Location", "RSRP",
                 "Ticket Information", "Analysis Result", "Main Issue KPI"):
        assert part in text, part
    src = (APP / "views" / "site_map.py").read_text(encoding="utf-8")
    assert 'st.columns(4, gap="small")' in src and "height=_PANEL_H" in src
    assert not at.sidebar.get("expandable")               # nothing left in the sidebar

    # stage 1: the ticket, its general analysis, the map on its worst sector
    at.text_input(key="sm_tid_in").set_value("IM1").run()
    assert not at.exception, at.exception
    text = " ".join(_html(at))
    assert "CC-1" in text and "IM1" in text
    assert "1 · General analysis (site level)" in text and "BAS0001-S1 (worst cell)" in text
    assert at.session_state["sm_sel_sector"] == "BAS0001-S1"
    assert len(at.get("plotly_chart")) == 1                # the main issue KPI only
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
    # RSRP: no coverage grid here — the panel says so, the customer location is known
    assert "<span>Sector / distance</span><b>BAS0001-S2 · 300 m</b>" in text
    assert "No coverage grid in Coverage Data" in text

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


@pytest.mark.parametrize("approved", [False, True], ids=["no-user-location", "approved"])
@pytest.mark.parametrize("area", [None, FULL_AREA, {"median": -95.2, "grids": 12}],
                         ids=["no-coverage", "with-radius", "without-radius"])
def test_the_site_map_renders_the_rsrp_panel_in_every_case(tmp_path, monkeypatch,
                                                            put_resource, area, approved):
    monkeypatch.setenv("RFOPT_CACHE_DIR", str(tmp_path))
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    import _complaints as C
    import _relocate
    from test_complaint_analysis import _target_bytes
    from rfopt.complaints.target_store import save_target

    save_target(_target_bytes(), "Target 13-Sep.xlsx")
    put_resource("kpi", "R5 4G Monitoring Hourly KPI.csv", _kpi_csv().encode(), "4G KPI")
    put_resource("kmz", "R5_Sites.kmz", _kmz(), "Site KMZ")
    if approved:
        _relocate.approve("CC-1", *_at(120, 300))

    real = C.load_workspace

    def with_coverage(*a, **k):
        # the site-area record as the coverage grid would give it, and the RSRP
        # measured at the approved point
        ctx = real(*a, **k)
        ctx.rsrp = {"BAS0001": dict(area)} if area is not None else {}
        ctx.cov_kept = {"grid": object()} if area is not None else {}
        for r in ctx.re.values():
            r.rsrp = -98.7 if area is not None else None
        return ctx

    monkeypatch.setattr(C, "load_workspace", with_coverage)
    at = AppTest.from_file(str(APP / "views/site_map.py"), default_timeout=300)
    at.session_state["sm_tid"] = "CC-1"
    at.run()
    assert not at.exception, at.exception
    rsrp = next(b for b in _html(at) if "<span>Site area RSRP</span>" in b)
    for part in ("Site area RSRP", "Customer location", "Sector / distance", 'class="ca-rsc"',
                 'class="ca-evs"'):
        assert part in rsrp, part
    if area is None:
        assert "<span>Site area RSRP</span><b>Not available</b>" in rsrp and "No data" in rsrp
    elif "radius_m" in area:
        assert "-85.4 dBm (≤500 m, MR-weighted median)" in rsrp
    else:
        assert "-95.2 dBm (MR-weighted median)" in rsrp
    if approved:
        assert "<span>Sector / distance</span><b>BAS0001-S2 · 300 m</b>" in rsrp
        if area is not None:
            assert "-98.7 dBm" in rsrp and "user location" in rsrp
    else:
        assert "no approved user location" in rsrp
