"""The KPI Analysis Overview's one filter: what each filter keeps, the rings'
counts, and the places, cells and markers joined onto the judged rows."""

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


@pytest.fixture(autouse=True)
def _app_on_path():
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))


def _objects() -> pd.DataFrame:
    rows = [
        ("BAS0001", "BAS0001-S1", "4G", "PRB", "4G DL PRB", "PRB_COL", 92.0, 2, "Critical"),
        ("BAS0001", "BAS0001-S2", "4G", "PRB", "4G DL PRB", "PRB_COL", 30.0, 0, "Normal"),
        ("BAS0002", "BAS0002-S1", "4G", "INTER", "4G UL interference", "INT_COL", -104.0, 1,
         "Warning"),
        ("NAS0001", "NAS0001", "3G", "FLOW", "3G DL flow-control drops", "FLOW_COL", 72000.0, 1,
         "Warning"),
        ("NAS0001", "NAS0001", "3G", "RTWP", "3G RTWP", "RTWP_COL", -104.0, 0, "Normal"),
    ]
    df = pd.DataFrame(rows, columns=["site_id", "object_id", "kind", "key", "label", "column",
                                     "value", "sev", "state"])
    return df.assign(object_type="Sector", unit="", judged=True, low_is_bad=False,
                     threshold="⚠ > 1", peak=df["value"],
                     peak_time=pd.Timestamp("2026-09-13 10:00"))


REGIONS = pd.DataFrame(
    {"governorate": ["Basrah", "Basrah", "Basrah", "Dhi Qar"],
     "city": ["Basrah", "Zubair", "Basrah", "Nasiriya"],
     "sup_district": ["Markaz Al-Basrah", "Markaz Al-Zubair", "Markaz Al-Basrah",
                      "Markaz Al-Nasiriya"],
     "latitude": [30.5, 30.3, 30.52, 31.05], "longitude": [47.8, 47.7, 47.81, 46.25]},
    index=pd.Index(["BAS0001", "BAS0002", "BAS0003", "NAS0001"], name="site_id"))
NAMES = pd.Series(["Alpha_BAS0001", "Beta_BAS0002", "Delta_BAS0003", "Gamma_NAS0001"],
                  index=REGIONS.index)
KINDS = {"BAS0001": ["4G"], "BAS0002": ["4G"], "BAS0003": ["4G"], "NAS0001": ["3G"]}
INDEX = pd.DataFrame(
    [("4G", "L_Alpha_BAS0001-1", "BAS0001", "BAS0001-S1"),
     ("4G", "L21_Alpha_BAS0001-1", "BAS0001", "BAS0001-S1"),
     ("4G", "L_Alpha_BAS0001-2", "BAS0001", "BAS0001-S2"),
     ("4G", "T_Beta_BAS0002-1", "BAS0002", "BAS0002-S1"),
     ("3G", "Gamma_NAS0001", "NAS0001", "NAS0001-S0")],
    columns=["tech", "object", "site_id", "sector_id"])
CELL_IDS = pd.Series({"L_ALPHA_BAS0001-1": 1.0, "L21_ALPHA_BAS0001-1": 11})


def _rows():
    from _kpi_filters import cell_table, enrich, site_frame
    rows = enrich(_objects(), REGIONS, NAMES, cell_table(INDEX, CELL_IDS))
    return rows, site_frame(list(REGIONS.index), REGIONS, NAMES, KINDS)


def test_the_rows_carry_their_place_name_cells_and_issue():
    rows, _ = _rows()
    r = rows.set_index(["object_id", "key"])
    s1 = r.loc[("BAS0001-S1", "PRB")]
    assert s1["cell_name"] == "L21_Alpha_BAS0001-1, L_Alpha_BAS0001-1"
    assert s1["cell_id"] == "11, 1"                      # from the EP tracker
    assert s1["issue"] == "High DL PRB" and s1["site_name"] == "Alpha_BAS0001"
    assert r.loc[("BAS0001-S2", "PRB"), "issue"] == ""   # within its threshold
    nodeb = r.loc[("NAS0001", "FLOW")]
    assert nodeb["cell_name"] == "Gamma_NAS0001" and nodeb["cell_id"] == ""
    assert nodeb["governorate"] == "Dhi Qar" and nodeb["city"] == "Nasiriya"
    assert nodeb["sup_district"] == "Markaz Al-Nasiriya"


def test_every_filter_keeps_sites_and_rows_together():
    from _kpi_filters import Filters, apply

    rows, sites = _rows()

    def keep(**kw):
        u, r = apply(rows, sites, Filters(**kw))
        return set(u), r

    u, r = keep()
    assert len(u) == 4 and len(r) == 5                   # BAS0003 has no judged row, still a site
    u, r = keep(governorate="Basrah")
    assert u == {"BAS0001", "BAS0002", "BAS0003"} and set(r["site_id"]) == {"BAS0001", "BAS0002"}
    # Governorate + Sup District + KPI: only the matching records
    u, r = keep(governorate="Basrah", sup_districts=("Markaz Al-Zubair",),
                kpis=("4G UL interference",))
    assert u == {"BAS0002"} and len(r) == 1
    # several Sup Districts at once
    u, _ = keep(sup_districts=("Markaz Al-Zubair", "Markaz Al-Nasiriya"))
    assert u == {"BAS0002", "NAS0001"}
    assert keep(techs=("3G",))[0] == {"NAS0001"}
    assert keep(site="NAS0001")[0] == {"NAS0001"}
    # the table's search: every word, anywhere in the row (here a city and a status)
    u, r = keep(q="zubair warning")
    assert u == {"BAS0002"} and list(r["object_id"]) == ["BAS0002-S1"]
    u, r = keep(cell="l21")
    assert u == {"BAS0001"} and set(r["object_id"]) == {"BAS0001-S1"}
    u, r = keep(states=("Critical", "Warning"))
    assert len(u) == 4 and len(r) == 3                   # a status narrows the rows only
    assert list(keep(issues=("High DL PRB",))[1]["object_id"]) == ["BAS0001-S1"]
    assert list(keep(columns=("FLOW_COL",))[1]["key"]) == ["FLOW"]

    f = Filters(governorate="Basrah", sup_districts=("Markaz Al-Basrah", "Markaz Al-Zubair"),
                states=("Critical",), columns=("PRB_COL",))
    assert f.without("sup_districts") == Filters(governorate="Basrah", states=("Critical",),
                                                 columns=("PRB_COL",))
    assert f.active() == [("Governorate", "Basrah"),
                          ("Sup District", "Markaz Al-Basrah, Markaz Al-Zubair"),
                          ("Status", "Critical")]


def test_the_rings_count_breaching_sites_out_of_the_measured_ones():
    from _kpi_filters import Filters, apply, counts

    rows, sites = _rows()
    f = Filters(states=("Critical", "Warning"))
    c = counts(apply(rows, sites, f)[1], None, apply(rows, sites, f.without("states"))[1], None)
    assert c["issues"] == 3 and c["checks"] == 3
    assert c["prb"] == (1, 1) and c["flow"] == (1, 1) and c["rtwp"] == (0, 1)
    assert c["tdd"] is None                              # no TDD judgement given
    # a KPI filter leaves PRB out: none to count, which the page tells from no data
    only = counts(apply(rows, sites, Filters(kpis=("4G UL interference",)))[1], None)
    assert only["prb"] is None and counts(rows, None)["prb"] == (1, 1)


def test_the_map_markers_and_the_governorate_scope():
    from _kpi_filters import worst_sites
    from _kpi_health import scope_sites
    from _kpi_region import UNKNOWN, city_name

    rows, _ = _rows()
    w = worst_sites(rows, REGIONS).set_index("site_id")
    assert set(w.index) == {"BAS0001", "BAS0002", "NAS0001"}
    assert w.loc["BAS0001", "sev"] == 2 and w.loc["BAS0001", "label"] == "4G DL PRB"
    assert w.loc["NAS0001", "latitude"] == 31.05

    govs = pd.Series(["Basrah", "Basrah", "Dhi Qar"], index=["BAS0001", "BAS0002", "NAS0001"])
    index = pd.DataFrame({"site_id": ["BAS0001", "NAS0001"], "object": ["a", "b"]})
    assert scope_sites(index, "Governorate", "Dhi Qar", [], govs) == {"NAS0001"}
    assert scope_sites(index, "Governorate", "Basrah", [], govs) == {"BAS0001"}   # loaded only

    assert city_name("Nassriya") == "Nasiriyah" and city_name("Emarah") == "Amarah"
    assert city_name("", "SAM0101") == "Samawah" and city_name("", "XYZ0001") == UNKNOWN
    from _kpi_region import governorate_name
    assert governorate_name("Nassriya") == "Dhi Qar" and governorate_name("Emarah") == "Maysan"
    assert governorate_name("Al-Muthanna") == "Muthanna" and governorate_name("", "BAS0001") == "Basrah"
