"""Parser for the Google-Earth 'R5 Sites' KMZ (rfopt/ingest/kmz_sites.py)."""

import io
import zipfile
from pathlib import Path

import numpy as np
import pytest

from rfopt.ingest.kmz_sites import (_band_label, _clean, _kv, _parse,
                                    _parse_balloon, _tilt_deg, load_kmz_sites)

REAL_KMZ = Path.home() / "Downloads" / "R5_Sites.kmz"

_BALLOON = (
    "<b>Khrebut6_BAS0011</b><br/>Site Code: BAS0011 &nbsp; Sector: 1 &nbsp; "
    "Azimuth: 350&deg;<br/>Height: 30 m &nbsp; Status: On Air<br/><br/>"
    "<b>2G (GSM) Cells</b><br/>"
    "<u>Khrebut6_BAS0011-1</u>: Azimuth=350&deg;, Band=GSM900, BCCH=81, "
    "BSIC=61, TCH=85<br/>&nbsp;&nbsp;Electrical Downtilt=40, "
    "Power=n/a (not tracked), Antenna=APE4517R2<br/>"
    "&nbsp;&nbsp;BSC=BBASH02, LAC=3031, Status=ACTIVATED<br/><br/>"
    "<b>3G (UMTS) Cells</b><br/>"
    "<u>U_Khrebut6_BAS0011-A2</u>: Azimuth=350&deg;, Tilt=50, Power=460, "
    "SAC=114, DL PSC=22, DL Freq=10737, Status=ACTIVATED<br/><br/>"
    "<b>4G (LTE) Cells</b><br/>"
    "<u>L_Khrebut6_BAS0011-1</u>: Azimuth=350&deg;, PCI=93, EARFCN=1750, "
    "BW=20MHz, TAC=13101, RS Power=182, MAX RET=50, Status=Active<br/>"
    "<u>L21_Khrebut6_BAS0011-1</u>: Azimuth=350&deg;, PCI=155, EARFCN=300, "
    "BW=10MHz, TAC=13101, RS Power=182, MAX RET=50, Status=Active<br/><br/>"
    "<b>RET Actual Tilt</b><br/>Band U900: 40 (0.1&deg; units)<br/>"
    "Band U2100: 40 (0.1&deg; units)<br/>"
)


def _kml(*placemarks: str) -> str:
    return ("<?xml version='1.0'?><kml><Document>"
            + "".join(placemarks) + "</Document></kml>")


def _pm(style: str, lon: float, lat: float, balloon: str) -> str:
    return (f"<Placemark><styleUrl>#{style}</styleUrl>"
            f"<description><![CDATA[{balloon}]]></description>"
            f"<Polygon><outerBoundaryIs><LinearRing><coordinates>"
            f"{lon},{lat},0 {lon+0.001},{lat+0.001},0 {lon},{lat},0"
            f"</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>")


# --- unit: helpers -------------------------------------------------------- #
def test_clean_strips_markup():
    out = _clean("<b>x</b><br/>a=1&nbsp;b<br/>c&deg;")
    assert "<" not in out and "&nbsp;" not in out and "&deg;" not in out
    assert "\n" in out


def test_kv_keeps_parenthesised_commas():
    d = _kv("Power=n/a (not tracked), Antenna=APE4517R2, Status=ACTIVATED")
    assert d["power"] == "n/a (not tracked)"
    assert d["antenna"] == "APE4517R2"
    assert d["status"] == "ACTIVATED"


def test_tilt_deg_tenths():
    assert _tilt_deg(40) == 4.0          # 0.1-deg units
    assert _tilt_deg(3.5) == 3.5         # already degrees
    assert np.isnan(_tilt_deg("n/a"))


def test_band_label():
    assert _band_label("L21_Site_BAS0011-1", "4G", "", 300, None) == "L2100"
    assert _band_label("L_Site_BAS0011-1", "4G", "", 1750, None) == "L1800"
    assert _band_label("L261st_Site_BAS0011-131", "4G", "", 40342, None) \
        == "L2600(TDD)"
    assert _band_label("U9_Site_BAS0011-A1", "3G", "", None, 3088) == "U900"
    assert _band_label("U_Site_BAS0011-A2", "3G", "", None, 10737) == "U2100"
    assert _band_label("Site_BAS0011-1", "2G", "GSM900", None, None) == "G900"
    assert _band_label("Site_BAS0011-5", "2G", "DCS1800", None, None) == "G1800"


# --- unit: one balloon -------------------------------------------------- #
def test_parse_balloon():
    rec = _parse_balloon(_clean(_BALLOON))
    assert rec["site_id"] == "BAS0011"
    assert rec["sector"] == 1
    assert rec["azimuth_deg"] == 350.0
    assert rec["height_m"] == 30.0
    techs = sorted({c["technology"] for c in rec["cells"]})
    assert techs == ["2G", "3G", "4G"]
    g2 = next(c for c in rec["cells"] if c["technology"] == "2G")
    assert g2["bcch"] == "81" and g2["antenna"] == "APE4517R2"
    assert rec["ret_actual"]["U900"] == [40.0]


# --- integration: synthetic KML through _parse + load ------------------- #
def test_parse_synthetic_kml(tmp_path):
    kml = _kml(
        _pm("onair", 47.7586, 30.5128, _BALLOON),
        _pm("planned", 46.25, 31.05,
            _BALLOON.replace("BAS0011", "SAM0099").replace("On Air", "Planned")),
        _pm("onair", 44.0, 32.0,
            _BALLOON.replace("BAS0011", "XYZ1234")),      # non-R5 -> dropped
    )
    kmz = tmp_path / "mini.kmz"
    with zipfile.ZipFile(kmz, "w") as z:
        z.writestr("doc.kml", kml)

    ks = load_kmz_sites(str(kmz), region="R5", use_cache=False)
    assert set(ks.sectors["site_id"]) == {"BAS0011", "SAM0099"}
    assert len(ks.cells) == 8                       # (1+1+2) cells x 2 R5 sectors
    s = ks.sectors.set_index("site_id").loc["BAS0011"]
    assert s["n_2g"] == 1 and s["n_3g"] == 1 and s["n_4g"] == 2
    assert s["antenna_height_m"] == 30.0

    c4 = ks.sector_cells("BAS0011-S1", "4G")
    assert len(c4) == 2
    assert c4["rs_power_dbm"].iloc[0] == pytest.approx(18.2)
    assert c4["max_ret_deg"].iloc[0] == pytest.approx(5.0)
    assert c4["pci"].tolist() == [93.0, 155.0]
    assert set(c4["band_label"]) == {"L1800", "L2100"}
    assert c4["ret_deg"].tolist() == pytest.approx([5.0, 5.0])   # from MAX RET

    # topology filter on the accessor
    assert ks.sector_cells("BAS0011-S1", "2G")["bcch"].iloc[0] == "81"
    assert len(ks.sector_cells("BAS0011-S1")) == 4      # all techs

    # a 4G cell with no MAX RET falls back to the sector's RET-actual tilt
    no_ret = _BALLOON.replace("BAS0011", "BAS0022").replace(
        "MAX RET=50", "MAX RET=").replace("Band U900: 40", "Band U900: 55")
    kmz2 = tmp_path / "noret.kmz"
    with zipfile.ZipFile(kmz2, "w") as z:
        z.writestr("doc.kml", _kml(_pm("onair", 47.7, 30.5, no_ret)))
    ks2 = load_kmz_sites(str(kmz2), region="R5", use_cache=False)
    g4 = ks2.sector_cells("BAS0022-S1", "4G")
    assert g4["max_ret_deg"].isna().all()
    # RET-actual = mean(U900 5.5, U2100 4.0) = 4.75
    assert g4["ret_deg"].tolist() == pytest.approx([4.75, 4.75])
    assert ks2.sectors.set_index("sector_id").loc["BAS0022-S1", "ret_deg"] \
        == pytest.approx(4.75)


def test_every_site_of_the_kmz_reaches_the_sites_map(tmp_path):
    """The KMZ is the R5 site list: a site whose ID does not start BAS / NAS /
    EMA / SAM (USM0729 is in Basrah) is still one of its sites. The region
    filter dropped 12 such sites from the map; the Sites page loads them all."""
    kml = _kml(_pm("onair", 47.7586, 30.5128, _BALLOON),
               _pm("onair", 45.56788, 31.366416,
                   _BALLOON.replace("BAS0011", "USM0729")))
    kmz = tmp_path / "r5.kmz"
    with zipfile.ZipFile(kmz, "w") as z:
        z.writestr("doc.kml", kml)
    assert set(load_kmz_sites(str(kmz), region="R5", use_cache=False)
               .sites["site_id"]) == {"BAS0011"}
    every = load_kmz_sites(str(kmz), region=None, use_cache=False)
    assert set(every.sites["site_id"]) == {"BAS0011", "USM0729"}
    usm = every.sites.set_index("site_id").loc["USM0729"]
    assert usm["latitude"] == pytest.approx(31.366416)

    # the Sites map reads the KMZ through its loader module, unfiltered
    app = Path(__file__).resolve().parents[1] / "app"
    page = (app / "views" / "site_map.py").read_text(encoding="utf-8")
    loaders = (app / "_site_data.py").read_text(encoding="utf-8")
    assert "load_kmz_path as _load_kmz_path" in page
    assert "load_kmz_sites(path, region=None)" in loaders


def test_bytes_buffer_input(tmp_path):
    kml = _kml(_pm("onair", 47.75, 30.51, _BALLOON))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("doc.kml", kml)
    buf.seek(0)
    ks = load_kmz_sites(buf, region="R5", use_cache=False)
    assert list(ks.sites["site_id"]) == ["BAS0011"]


# --- integration: the real file, when present -------------------------- #
@pytest.mark.skipif(not REAL_KMZ.exists(), reason="R5_Sites.kmz not present")
def test_real_kmz():
    ks = load_kmz_sites(str(REAL_KMZ), region="R5", use_cache=False)
    assert ks.sites["site_id"].nunique() > 1000
    assert set(ks.sectors["site_id"].str[:3].unique()) <= {"BAS", "NAS",
                                                           "EMA", "SAM"}
    assert {"2G", "3G", "4G"} <= set(ks.cells["technology"].unique())
    g4 = ks.cells[ks.cells["technology"] == "4G"]
    assert g4["pci"].notna().mean() > 0.95
    assert g4["rs_power_dbm"].dropna().between(5, 30).mean() > 0.9
    # every sector has a location and an azimuth
    assert ks.sectors[["latitude", "longitude"]].notna().all().all()
    assert ks.sectors["azimuth_deg"].notna().mean() > 0.98
