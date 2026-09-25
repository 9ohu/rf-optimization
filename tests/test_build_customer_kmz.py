"""Unit tests for scripts/build_customer_kmz.py (the customer-KMZ regenerator)."""

import importlib.util
import math
import re
import zipfile
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "build_customer_kmz",
    Path(__file__).resolve().parents[1] / "scripts" / "build_customer_kmz.py")
bck = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bck)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def test_site_id_from_name_takes_the_trailing_code():
    assert bck.site_id_from_name("38m_Alhuda_EMA3658") == "EMA3658"
    assert bck.site_id_from_name("Rifia102_NAS0666") == "NAS0666"   # mis-keyed row
    assert bck.site_id_from_name("no code here") == ""


def test_clean_site_name_strips_height_nan_and_offair_suffix():
    assert bck.clean_site_name("38m_Alhuda_EMA3658", "EMA3658") == "Alhuda_EMA3658"
    assert bck.clean_site_name("nannanShlamchaB1_BAS0517", "BAS0517") == "ShlamchaB1_BAS0517"
    assert bck.clean_site_name("BasraSTD1_BAS4240-Currently off air", "BAS4240") == "BasraSTD1_BAS4240"
    assert bck.clean_site_name("X_ARBBtairaRD1_EMA6509", "EMA6509") == "ARBBtairaRD1_EMA6509"


def test_is_arb_matches_border_sites_not_placenames():
    assert bck._is_arb("ARBShebBRDR_EMA6512")
    assert bck._is_arb("X_ARBBtairaRD1_EMA6509")
    assert not bck._is_arb("Marbad4_BAS0320")
    assert not bck._is_arb("Garbi3_SAM0041")
    assert not bck._is_arb("Arbatalaf2_BAS3304")


def test_height_from_name():
    assert bck.height_from_name("28.5m_Foo_BAS1") == 28.5
    assert bck.height_from_name("Foo_BAS1") is None


def test_dedupe_azimuths_merges_near_duplicates_and_wrap():
    assert bck.dedupe_azimuths([0, 120, 120, 240, 240]) == [0, 120, 240]
    assert bck.dedupe_azimuths([5, 350]) == [5]              # 350 is within tol of 5 (wrap)
    assert bck.dedupe_azimuths([10, 130, 250]) == [10, 130, 250]


def test_classify_rules():
    red = {"name": "New_BAS9", "family": {"R"}}
    blue = {"name": "Live_BAS9", "family": {"B"}}
    arb = {"name": "ARBRoad_BAS9", "family": {"B"}}
    active = {"sectors": {1: {"active": True}}}
    dead = {"sectors": {1: {"active": False}}}

    assert bck.classify("BAS9", red, None)[0] == "Planned"
    assert bck.classify("BAS9", blue, None)[0] == "On Air"
    assert bck.classify("BAS9", arb, active)[0] == "Off Air"          # ARB wins
    assert bck.classify("BAS9", red, dead)[0] == "Off Air"            # dead in tracker wins
    assert bck.classify("BAS9", None, active)[0] == "On Air"          # missing from arrows
    assert bck.classify("BAS9", None, dead)[0] == "Off Air"


def test_horizontal_pattern_is_the_3gpp_power_shape():
    assert bck._h_pattern(0) == pytest.approx(1.0)
    assert bck._h_pattern(bck.HBW_DEG / 2) == pytest.approx(0.5, abs=0.01)  # 3 dB point
    assert bck._h_pattern(90) < 0.02                                        # pinched off
    assert bck._h_pattern(180) == pytest.approx(10 ** (-bck.FRONT_BACK_DB / 10))


def test_coverage_reach_tracks_downtilt():
    # steeper tilt -> shorter reach; taller site -> longer reach
    assert bck.coverage_reach_m(30, 9) < bck.coverage_reach_m(30, 4)
    assert bck.coverage_reach_m(45, 6) > bck.coverage_reach_m(20, 6)
    assert bck.LOBE_MIN_M <= bck.coverage_reach_m(30, 15) <= bck.LOBE_MAX_M
    assert bck.coverage_reach_m(60, 0.5) == bck.LOBE_MAX_M          # clamped


def test_lobe_points_the_right_way_and_is_lobe_shaped():
    reach = 500.0
    coords = [c.split(",") for c in bck.lobe_coords(30.0, 47.0, 90, reach).split()]
    apex = (float(coords[0][0]), float(coords[0][1]))
    # the far point of the lobe sits due east of the apex
    far = max(coords[1:-1], key=lambda c: (float(c[0]) - apex[0]) ** 2
              + (float(c[1]) - apex[1]) ** 2)
    assert float(far[0]) > apex[0]
    assert abs(float(far[1]) - apex[1]) < 5e-4
    # and it is a lobe, not a disc: much longer along boresight than across it
    xs = [float(c[0]) - apex[0] for c in coords]
    ys = [float(c[1]) - apex[1] for c in coords]
    along = (max(xs) - min(xs)) / math.cos(math.radians(30.0))
    across = max(ys) - min(ys)
    assert along > across * 1.4


def test_beam_starts_at_the_antenna_and_lands_on_the_ground():
    pts = bck.beam_coords(30.0, 47.0, 180, 400, 35.0).split()
    assert pts[0] == pts[-1]                       # closed ring at the apex
    assert pts[0].endswith(",35.0")                # apex sits at antenna height
    assert all(p.endswith(",0") for p in pts[1:-1])


def test_tower_placemarks_are_a_real_lattice():
    pms = bck.tower_placemarks(30.5, 47.75, 42.0, [0, 120, 240], "Demo_BAS1")
    styles = [re.search(r"#(\w+)", p).group(1) for p in pms]
    assert "tower_steel" in styles and "tower_antenna" in styles
    assert "tower_hut" in styles and "tower_dish" in styles
    steel = pms[styles.index("tower_steel")]
    # 4 legs + 4 zigzag brace runs + platform + mast
    assert steel.count("<LineString>") >= 10
    assert "Demo_BAS1" in steel                    # balloon on the mast
    ants = pms[styles.index("tower_antenna")]
    assert ants.count("<Polygon>") == 3            # one panel per sector

    # 42 m clears the aviation threshold -> banded + beacon
    assert "tower_red" in styles and "tower_beacon" in styles
    # a short mast does not get banding
    assert "tower_red" not in [re.search(r"#(\w+)", p).group(1)
                               for p in bck.tower_placemarks(30.5, 47.75, 18.0, [0])]


def test_tower_is_drawn_at_its_real_height():
    pms = bck.tower_placemarks(30.5, 47.75, 40.0, [0])
    alts = [float(m) for m in re.findall(r",(\d+\.\d)(?=[ <])", pms[0])]
    assert max(alts) == pytest.approx(40.0 * bck.TOWER_SCALE
                                      + max(2.0, 40.0 * bck.TOWER_SCALE * 0.09), abs=0.2)


def test_balloon_matches_the_established_format():
    rec = {
        "gsm": [dict(cell="Alhuda_EMA3658-3", band="GSM900", bcch="83", bsic="67",
                     tch="92", etilt="40", ant="ATR4518R9S", bsc="BEMAH20",
                     lac="3119", status="ACTIVATED", az=220)],
        "umts": [], "lte": [], "ret": [("U900", "40"), ("U2100", "40")],
    }
    html = bck.balloon("Alhuda_EMA3658", "EMA3658", 3, 220, 38.0, "On Air", rec)
    assert "<b>Alhuda_EMA3658</b><br/>Site Code: EMA3658 &nbsp; Sector: 3 &nbsp; Azimuth: 220&deg;" in html
    assert "Height: 38 m &nbsp; Status: On Air" in html
    assert "<u>Alhuda_EMA3658-3</u>: Azimuth=220&deg;, Band=GSM900, BCCH=83" in html
    assert "Power=n/a (not tracked)" in html
    assert "No UMTS cell found.<br/>" in html
    assert "No LTE cell found.<br/>" in html
    assert "Band U900: 40 (0.1&deg; units)<br/>" in html


def test_balloon_handles_missing_height_and_empty_sector():
    html = bck.balloon("New_BAS9", "BAS9", 1, 0, None, "Planned", None)
    assert "Height: n/a &nbsp; Status: Planned" in html
    assert "No GSM record found." in html and "No RET record found." in html


# --------------------------------------------------------------------------- #
# end-to-end against the real inputs, when they are present
# --------------------------------------------------------------------------- #
ARROWS = Path(r"C:/Users/swx1351646/Desktop/KMZ_20260909U_v2.kmz")
TRACKER = Path(r"D:/WeLink_data_files/swx1351646/ReceiveFiles/"
               r"WK37 Engineering Parameter Tracker-06092026.xlsx")


@pytest.mark.skipif(not (ARROWS.exists() and TRACKER.exists()),
                    reason="real R5 inputs not on this machine")
def test_end_to_end_builds_a_valid_kmz(tmp_path):
    import xml.dom.minidom as minidom

    arrows = bck.parse_arrows(ARROWS)
    assert len(arrows) > 1500
    assert "EMA3658" in arrows and "B" in arrows["EMA3658"]["family"]

    out = tmp_path / "out.kmz"
    bck.build(ARROWS, TRACKER, out)
    assert out.exists()

    with zipfile.ZipFile(out) as z:
        kml = z.read("doc.kml").decode("utf-8")
    minidom.parseString(kml.encode("utf-8"))          # must be well-formed

    assert "<name>R5 Sites - Full Visualization</name>" in kml
    for folder in ("On Air sectors (", "Planned sectors (", "Off Air sectors (",
                   "3D coverage beams (", "Telecom Towers (3D lattice) (",
                   "Site Names ("):
        assert folder in kml
    assert "Site Code: EMA3658 &nbsp; Sector: 3" in kml
    assert (out.with_suffix(".review.csv")).exists()
