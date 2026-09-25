"""The official R5 boundaries (OCHA COD-AB): which Sup District a site is in,
the snap to a nearby boundary, and the KPI issues map drawing the areas."""

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


@pytest.fixture(autouse=True)
def _app_on_path():
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))


def _square(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


def _feature(level, name, gov, city, ring, district=""):
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"level": level, "name": name, "name_ar": "", "pcode": name[:6],
                           "district": district, "governorate": gov, "city": city}}


DOC = {"type": "FeatureCollection", "features": [
    _feature(1, "Al-Basrah", "Al-Basrah", "Basrah", _square(47.5, 30.0, 48.0, 30.8)),
    _feature(1, "Maysan", "Maysan", "Emarah", _square(47.0, 31.7, 47.4, 32.1)),
    _feature(3, "Markaz Al-Basrah", "Al-Basrah", "Basrah",
             _square(47.70, 30.40, 47.90, 30.60), "Al-Basrah"),
    _feature(3, "Markaz Al-Zubair", "Al-Basrah", "Basrah",
             _square(47.60, 30.20, 47.80, 30.40), "Al-Zubair"),
    _feature(3, "Al-Msharah", "Maysan", "Emarah", _square(47.10, 31.80, 47.30, 32.00), "Al-Kahla"),
]}
SITES = pd.DataFrame(
    {"city": ["Basrah", "Basrah", "Basrah", "Emarah"],
     "sub_district": ["Manawy Basha", "Shat Al-Arab", "Faw center", "Sob Al-Aysar"],
     # inside Markaz Al-Basrah · ~1 km east of it · ~67 km out to sea · inside Al-Msharah
     "latitude": [30.50, 30.50, 30.50, 31.90],
     "longitude": [47.80, 47.91, 48.60, 47.20]},
    index=pd.Index(["BAS0001", "BAS0002", "BAS0003", "EMA0001"], name="site_id"))


def test_the_boundaries_place_a_point_or_snap_it_within_2_km():
    from _kpi_bounds import area_of, governorates, load_areas, sub_districts

    areas = load_areas(json.dumps(DOC))
    assert [a.name for a in governorates(areas)] == ["Al-Basrah", "Maysan"]
    names, snapped = area_of([47.80, 47.75, 47.20, 47.91, 48.60, np.nan],
                             [30.50, 30.30, 31.90, 30.50, 30.50, 30.50], sub_districts(areas))
    assert list(names) == ["Markaz Al-Basrah", "Markaz Al-Zubair", "Al-Msharah",
                           "Markaz Al-Basrah", "", ""]
    assert snapped[0] == 0 and 900 < snapped[3] < 1000


def test_a_sites_sup_district_is_its_official_sub_district():
    from _kpi_bounds import OUTSIDE, load_areas
    from _kpi_region import UNKNOWN, site_regions

    areas = load_areas(json.dumps(DOC))
    r = site_regions(list(SITES.index) + ["NAS9999"], SITES, areas)
    assert r.loc["BAS0001", "sup_district"] == "Markaz Al-Basrah"
    assert r.loc["BAS0001", "snapped_m"] == 0
    assert r.loc["BAS0002", "sup_district"] == "Markaz Al-Basrah"     # snapped
    assert r.loc["BAS0002", "snapped_m"] > 900
    assert r.loc["BAS0003", "sup_district"] == OUTSIDE
    assert np.isnan(r.loc["BAS0003", "snapped_m"])
    assert r.loc["EMA0001", "sup_district"] == "Al-Msharah"
    # the sub-district's district is the city, its governorate the governorate
    assert r.loc["EMA0001", "city"] == "Kahla"
    assert r.loc["EMA0001", "governorate"] == "Maysan"
    assert r.loc["BAS0001", "governorate"] == "Basrah"
    # outside every sub-district: placed by the EP tracker's city (governorate)
    assert r.loc["BAS0003", "governorate"] == "Basrah"
    assert r.loc["NAS9999", "sup_district"] == UNKNOWN                 # not in the EP tracker
    # without the boundaries the EP tracker's sub-district is kept
    assert site_regions(["BAS0001"], SITES).loc["BAS0001", "sup_district"] == "Manawy Basha"


def test_the_map_draws_every_sub_district_and_governorate_as_its_boundary():
    import folium

    from _kpi_bounds import load_areas
    from _kpi_region import region_table, site_regions
    from _kpi_view import region_map

    areas = load_areas(json.dumps(DOC))
    regions = site_regions(list(SITES.index), SITES, areas)
    objects = pd.DataFrame({"site_id": list(SITES.index), "label": ["4G DL PRB"] * 4,
                            "sev": [2, 0, 1, 0], "judged": [True] * 4})

    def children(fmap, kind):
        return [c for c in fmap._children.values() if isinstance(c, kind)]

    def tips(items):
        return sorted(ch.text.split(" · ")[0] for s in items for ch in s._children.values()
                      if isinstance(ch, folium.Tooltip))

    sd = region_map(region_table(objects, regions, "Sup District"), "Sup District", areas=areas)
    polygons = children(sd, folium.Polygon)
    assert len(polygons) == 3                                   # every sub-district
    assert tips(polygons) == ["Al-Msharah", "Markaz Al-Basrah"]  # Zubair has no site: an outline
    circles = children(sd, folium.CircleMarker)
    assert tips(circles) == ["Outside R5 sub-districts"]
    order = list(sd._children.values())
    assert order.index(circles[0]) < order.index(polygons[0])   # boundaries stay clickable on top

    gov = region_map(region_table(objects, regions, "Governorate"), "Governorate", areas=areas)
    assert tips(children(gov, folium.Polygon)) == ["Basrah", "Maysan"]
    assert "World_Dark_Gray_Base" in gov.get_root().render()
    # a city (district) is drawn as its sub-districts together; several picked areas
    # are all outlined
    city = region_map(region_table(objects, regions, "City"), "City", areas=areas)
    assert tips(children(city, folium.Polygon)) == ["Basrah", "Kahla"]
    many = region_map(region_table(objects, regions, "Sup District"), "Sup District",
                      ["Markaz Al-Basrah", "Al-Msharah"], areas)
    white = [p for p in children(many, folium.Polygon) if p.options.get("color") == "#F8FAFC"]
    assert len(white) == 2


def test_the_bundled_r5_boundaries_are_the_official_areas():
    from _kpi_bounds import ASSET, governorates, r5_areas, sub_districts

    areas = r5_areas()
    assert Counter(a.city for a in sub_districts(areas)) == {
        "Basrah": 18, "Amarah": 12, "Nasiriyah": 18, "Samawah": 8}
    assert sorted(a.name for a in governorates(areas)) == [
        "Al-Basrah", "Al-Muthanna", "Maysan", "Thi Qar"]
    assert {"Markaz Al-Basrah", "Markaz Al-Zubair", "Markaz Al-Nasiriya",
            "Al-Msharah"} <= {a.name for a in sub_districts(areas)}
    doc = json.loads(ASSET.read_text(encoding="utf-8"))
    assert "CC BY-IGO" in doc["license"] and "cod-ab-irq" in doc["source"]
