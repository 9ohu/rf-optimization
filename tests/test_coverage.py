"""LTE coverage grid: reading the DL Coverage Insight export, the MR-weighted
median levels, the value packing in the PNG blocks, the RSRP classes from the
thresholds config, and the Sites page in Coverage view."""

import io
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
HEADER = ["Latitude", "Longitude", "RSRP(All MRs) (dBm)", "MR Count"]


def _xlsx(rows):
    import xlsxwriter

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    ws = wb.add_worksheet("Grid")
    for r, row in enumerate(rows):
        ws.write_row(r, 0, row)
    wb.close()
    return buf.getvalue()


def test_reads_the_grid_export_from_a_zip_of_workbooks():
    """A region spills over Excel's row limit into `_1` workbooks: every
    workbook in the zip is read, and nothing but the grid columns is kept."""
    from rfopt.ingest.coverage_grid import read_coverage_export

    a = _xlsx([HEADER, [30.500023, 47.800262, -99.5, 12],
               [30.500473, 47.800262, -120.0, 3]])
    b = _xlsx([HEADER, [30.500023, 47.800711, -84.0, 40]])
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("BASDLCoverageInsight_LTE_Grid_20260909142303/g.xlsx", a)
        z.writestr("BASDLCoverageInsight_LTE_Grid_20260909142303/g_1.xlsx", b)
    f = read_coverage_export(io.BytesIO(zbuf.getvalue()),
                             "BASDLCoverageInsight_LTE_Grid_20260909142303.zip")
    assert f.rows == 3 and f.mrs == 55
    assert f.rsrp_column == "RSRP(All MRs) (dBm)"
    assert f.time_column is None and f.exported == "2026-09-09 14:23"
    assert sorted(f.rsrp.tolist()) == [-120.0, -99.5, -84.0]


def test_a_csv_with_other_names_and_no_count_reads_too():
    from rfopt.ingest.coverage_grid import read_coverage_export

    csv = ("lat,lng,Avg RSRP,Start Time\n"
           "30.1,47.2,-101.25,2026-09-01 10:00\n")
    f = read_coverage_export(io.BytesIO(csv.encode()), "drive.csv")
    assert f.rows == 1 and float(f.mr[0]) == 1.0      # no count: one sample
    assert f.time_column == "Start Time"


def test_a_file_without_coverage_columns_is_refused():
    from rfopt.ingest.coverage_grid import CoverageFormatError, read_coverage_export

    with pytest.raises(CoverageFormatError):
        read_coverage_export(io.BytesIO(_xlsx([["Site", "Cell"], ["A", "B"]])),
                             "sites.xlsx")


def test_a_coarse_cell_takes_the_mr_weighted_median():
    """Not a mean: a grid measured 50 times outweighs two measured once."""
    from rfopt.geo.coverage import weighted_median

    k, med, tot = weighted_median(np.array([7, 7, 7, 9]),
                                  np.array([-80.0, -120.0, -100.0, -90.0]),
                                  np.array([1.0, 1.0, 10.0, 5.0]))
    assert k.tolist() == [7, 9] and med.tolist() == [-100.0, -90.0]
    assert tot.tolist() == [12.0, 5.0]
    _, med, _ = weighted_median(np.array([1, 1, 1]),
                                np.array([-80.0, -85.0, -110.0]),
                                np.array([1.0, 1.0, 50.0]))
    assert med.tolist() == [-110.0]


def test_pixels_carry_rsrp_and_mr_count():
    from rfopt.geo.coverage import count_code, decode_pixel, rsrp_code

    for rsrp, mr in ((-94.37, 126), (-141.0, 1), (-44.0, 2047), (-110.0, 518_771)):
        q, cc = int(rsrp_code(rsrp)), int(count_code(mr))
        v, n = decode_pixel((q >> 4, ((q & 15) << 4) | (cc >> 8), cc & 255, 255))
        assert abs(v - rsrp) <= 0.025 + 1e-9
        assert n == mr if mr < 2048 else abs(n - mr) / mr < 0.004
    assert decode_pixel((0, 0, 0, 0)) is None


def test_each_grid_lands_in_its_own_pixel_at_the_native_level():
    from PIL import Image

    from rfopt.geo.coverage import build_grid, decode_pixel

    step = 0.00045
    ys, xs = np.meshgrid(np.arange(40), np.arange(30), indexing="ij")
    lat = 30.500023 + ys.ravel() * step
    lon = 47.800262 + xs.ravel() * step
    rsrp = -140.0 + (ys.ravel() * 30 + xs.ravel()) * 0.07
    mr = 1 + xs.ravel()
    g = build_grid(lat, lon, rsrp, mr, block=16)
    lv = g.levels[0]
    assert lv.cells == 1200 and abs(g.step_lat - step) < 1e-9
    for probe in (0, 777, 1199):
        cy = int(np.floor((lat[probe] - g.lat0) / g.step_lat))
        cx = int(np.floor((lon[probe] - g.lon0) / g.step_lon))
        img = np.asarray(Image.open(io.BytesIO(lv.blocks[(cy // 16, cx // 16)])))
        v, n = decode_pixel(img[15 - cy % 16, cx % 16])
        assert abs(v - rsrp[probe]) <= 0.025 and n == mr[probe]
    # coarser levels halve the grid each time, down to one block
    assert [x.cells for x in g.levels] == [1200, 300, 80]


def test_rsrp_classes_reuse_the_kpi_rsrp_thresholds(tmp_path):
    import yaml

    from rfopt.geo.coverage import band_index, grid_stats, load_bands

    cfg_path = ROOT / "config" / "thresholds_lte.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    bands, covered = load_bands()
    rule = cfg["kpis"]["avg_rsrp_dbm"]
    assert [b.key for b in bands] == ["excellent", "good", "fair", "poor",
                                      "very_poor"]
    assert bands[2].lo == rule["warning"] and bands[3].lo == rule["critical"]
    assert covered == cfg["coverage"]["covered_dbm"]
    v = np.array([bands[0].lo, bands[0].lo - 0.01, bands[3].lo, bands[3].lo - 0.01])
    assert band_index(v, bands).tolist() == [0, 1, 3, 4]

    s = grid_stats(np.array([-80.0, -120.0]), np.array([30.0, 10.0]), bands, -110.0)
    assert s["grids"] == 2 and s["mrs"] == 40.0
    assert s["covered_pct"] == 75.0                  # MR-weighted, not per grid
    assert s["bands"][0]["mr_pct"] == 75.0 and s["bands"][4]["mr_pct"] == 25.0

    cfg["kpis"]["avg_rsrp_dbm"]["warning"] = -100    # the KPI line moves ...
    p = tmp_path / "t.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    assert load_bands(p)[0][2].lo == -100            # ... and so does Fair


def test_the_sites_page_draws_the_coverage_view(put_resource):
    """The grid comes from the Current Coverage Data of Data Resources."""
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))

    ys, xs = np.divmod(np.arange(900), 30)
    lines = ["Latitude,Longitude,RSRP(All MRs) (dBm),MR Count"] + [
        f"{30.5 + y * 0.00045:.6f},{47.8 + x * 0.00045:.6f},{r:.2f},5"
        for y, x, r in zip(ys, xs, np.linspace(-130, -70, 900))]
    put_resource("coverage", "BASDLCoverageInsight_LTE_Grid_20260909142303.csv",
                 ("\n".join(lines) + "\n").encode(), "Coverage grid · BAS")
    kmz = Path.home() / "Downloads" / "R5_Sites.kmz"
    if kmz.exists():
        put_resource("kmz", kmz.name, kmz, "Site KMZ")
    at = AppTest.from_file(str(APP / "views/site_map.py"), default_timeout=240)
    at.session_state["sm_basemap"] = "Coverage"
    at.run()
    assert not at.exception, at.exception
    bodies = [e.proto.body for e in at.get("html")]
    if not any("Total Sites" in b for b in bodies):
        pytest.skip("no site KMZ on this machine: the page stops before the map")
    assert any("Weak Coverage" in b for b in bodies)
    assert any("BASDLCoverageInsight" in b for b in bodies)
