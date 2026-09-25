"""Site-DB loader + complaint analysis. Uses the real Audit files when present,
else synthetic fixtures so the suite always runs."""

import math
from pathlib import Path

import pandas as pd
import pytest

from rfopt.complaints import analyze_complaints, load_complaints
from rfopt.complaints.analyze import _agreement
from rfopt.ingest.sitedb import R5_PREFIXES, load_site_db, region_summary

AUDIT = Path.home() / "Desktop" / "Audit"
REAL_SDB = AUDIT / "Copy of DB R5.xlsx u.xlsx"
REAL_CC = sorted(AUDIT.glob("CC Process*.xlsx"))


@pytest.fixture(scope="module")
def real_sdb():
    if not REAL_SDB.exists():
        pytest.skip("real R5 site DB not present")
    return load_site_db(str(REAL_SDB), region="R5")


@pytest.fixture(scope="module")
def real_complaints():
    if not REAL_CC:
        pytest.skip("real complaint file not present")
    return load_complaints(str(REAL_CC[-1]), region="R5", rf_only=True)


@pytest.fixture
def synth_sdb(tmp_path):
    rows = []
    for i, sid in enumerate(["BAS0001", "BAS0002", "NAS0003", "XYZ9999"]):
        for s, az in enumerate([0, 120, 240], start=1):
            rows.append({"Foldr Name": f"Loc{i}_{sid}", "Name": sid,
                         "Longitude": 47.80 + i * 0.01, "latitude": 30.50 + i * 0.01,
                         "Direction": az, "Sector": s, "Function": az,
                         "Status": "On Air", "Height": 30})
    p = tmp_path / "sdb.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


def test_sitedb_r5_prefix_filter(synth_sdb):
    sdb = load_site_db(synth_sdb, region="R5")
    assert set(sdb["prefix"].unique()) <= set(R5_PREFIXES)
    assert "XYZ9999" not in sdb["site_id"].values
    assert (sdb["vbw_deg"] == 6.5).all()
    assert sdb["sector_id"].str.match(r"^(BAS|NAS)\d+-S\d$").all()


def test_sitedb_swaps_reversed_latlon(tmp_path):
    p = tmp_path / "s.csv"
    pd.DataFrame([{"Name": "BAS0009", "Longitude": 30.51, "latitude": 47.75,
                   "Direction": 10, "Sector": 1, "Status": "On Air",
                   "Height": 25}]).to_csv(p, index=False)
    sdb = load_site_db(p, region="R5")
    assert 28 < sdb.iloc[0]["latitude"] < 34
    assert 43 < sdb.iloc[0]["longitude"] < 50


def test_agreement_logic():
    assert _agreement("new_site_needed", "new_site_needed") == "agree"
    assert _agreement("wrong_server", "far_coverage") == "partial"
    assert _agreement("interference", "not_rf") == "differ"
    assert _agreement("far_coverage", None) == "n/a"


def test_complaint_geometry_far_is_new_site(synth_sdb, tmp_path):
    sdb = load_site_db(synth_sdb, region="R5")
    # a complaint ~4 km north of BAS0001, far from everything
    cp = tmp_path / "c.csv"
    pd.DataFrame([{
        "Ticket ID": "T1", "MSISDN": "9647000",
        "Sector Serving": "BAS0001-1",
        "Problem Time": "2026-09-05T19:04:20.000Z",
        "Create Time": "2026-09-05T20:00:00.000Z",
        "Latitude": 30.536, "Longitude": 47.80, "City": "Basrah",
    }]).to_csv(cp, index=False)
    comp = load_complaints(cp, region="R5")
    assert comp.iloc[0]["site_id"] == "BAS0001"
    res = analyze_complaints(comp, sdb)
    f = res.findings[0]
    assert f.distance_m and f.distance_m > 3000
    assert f.category in ("new_site_needed", "far_coverage")
    assert f.bearing_deg is not None


def test_real_r5_site_db_loads(real_sdb):
    s = region_summary(real_sdb)
    assert s["sites"] > 1000
    assert set(s["prefixes"]) <= set(R5_PREFIXES)
    assert 28 < s["lat_range"][0] and s["lat_range"][1] < 34
    assert real_sdb["latitude"].notna().all() and real_sdb["longitude"].notna().all()


def test_real_complaint_pipeline_runs(real_sdb, real_complaints):
    from rfopt.complaints.analyze import CATEGORIES
    assert len(real_complaints) > 500
    assert real_complaints["prefix"].isin(R5_PREFIXES).all()
    res = analyze_complaints(real_complaints, real_sdb, max_tickets=800)
    d = res.df()
    assert len(d) == 800
    assert d["category"].isin(CATEGORIES).all()
    assert (d["distance_m"].notna()).mean() > 0.7


def test_real_complaint_kmz(real_sdb, real_complaints):
    import tempfile
    import zipfile

    from rfopt.geo import build_complaint_kmz
    res = analyze_complaints(real_complaints, real_sdb, max_tickets=400)
    p = build_complaint_kmz(res, Path(tempfile.gettempdir()) / "t.kmz")
    with zipfile.ZipFile(p) as z:
        kml = z.read("doc.kml").decode()
    assert "<kml" in kml and "MultiGeometry" in kml


# --- slim file (site id + sub-district only, no per-ticket coords) ---------
def test_slim_file_geocode_fallback_and_hotspots(synth_sdb, tmp_path):
    from rfopt.complaints import (aggregate_by_site, aggregate_by_subdistrict,
                                  analyze_complaints, load_complaints)
    sdb = load_site_db(synth_sdb, region="R5")
    cp = tmp_path / "slim.csv"
    pd.DataFrame([
        {"Ticket ID": f"T{i}", "Problem Time": "2026-09-05T19:04:20.000Z",
         "Create Time": "2026-09-05T20:00:00.000Z", "Site ID": "BAS0001",
         "Sub District": "Manawy Basha", "City": "Basrah"}
        for i in range(6)
    ] + [
        {"Ticket ID": "T99", "Problem Time": "2026-09-05T19:04:20.000Z",
         "Create Time": "2026-09-05T20:00:00.000Z", "Site ID": "NAS0003",
         "Sub District": "Totally Unknown Place", "City": "x"}
    ]).to_csv(cp, index=False)
    comp = load_complaints(cp, region="R5")
    assert "coord_source" in comp.columns
    # the known area geocodes; the unknown one falls through to serving-site
    assert (comp["coord_source"] == "subdistrict").sum() >= 5
    res = analyze_complaints(comp, sdb)
    cats = {f.category for f in res.findings}
    assert cats <= set(
        __import__("rfopt.complaints.analyze", fromlist=["CATEGORIES"]).CATEGORIES)
    # no spurious per-ticket claims from a centroid
    assert all(f.az_offset_deg is None for f in res.findings
               if f.loc_source == "subdistrict")
    hs = aggregate_by_site(res)
    assert hs.iloc[0]["serving_site"] == "BAS0001"
    assert hs.iloc[0]["tickets"] == 6
    sd = aggregate_by_subdistrict(res)
    assert "Manawy Basha" in sd["sub_district"].values
