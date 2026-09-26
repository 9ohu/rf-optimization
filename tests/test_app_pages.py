"""Headless smoke test: the app entry point + both views render without error."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
_REAL_KMZ = Path.home() / "Downloads" / "R5_Sites.kmz"

VIEWS = ["views/overview.py", "views/site_map.py",
         "views/kpi_analysis.py", "views/data_resources.py"]


@pytest.fixture(autouse=True)
def _app_on_path():
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))


@pytest.mark.parametrize("page", VIEWS)
def test_view_renders_empty(page):
    at = AppTest.from_file(str(APP / page), default_timeout=60)
    at.run()
    assert not at.exception, f"{page}: {at.exception}"


def test_an_upload_reaches_the_cached_loader(tmp_path):
    """A file the user *uploads* has only a bare name, no folder.

    Streamlit's cache hasher treats any file object with a `.name` as a file on
    disk and stats that name, so every cached loader used to die with
    FileNotFoundError on the upload path — before the loader ran. Auto-found
    files hid it: they carry a full path that does exist.
    """
    import io
    import zipfile

    csv = ("\n\n\nSHAMS-3G\nSave Time :2026-09-11 10:51:53\n\n"
           "Time,RNC,NODEBNAME,NodeB ID,Integrity,"
           "VS.RscGroup.FlowCtrol.DL.DropNum,VS.IPPM.Rtt.Means(ms),"
           "3G_Availability@AB\n"
           "2026-09-08 00:00,RBASH01,Tannumah6_BAS0038,38,100%,0,0,100\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("export(Subreport 1).csv", csv)
    src = tmp_path / "payload.zip"
    src.write_bytes(buf.getvalue())

    script = tmp_path / "upload_page.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, r'{APP}')\n"
        "import streamlit as st\n"
        "from _shared import NamedBytes, load_kpi_3g\n"
        # a name that exists in no working directory, exactly like an upload
        f"up = NamedBytes(open(r'{src}', 'rb').read(), 'SHAMS-3G_Q_9999.zip')\n"
        "st.write(len(load_kpi_3g(up).df))\n", encoding="utf-8")

    at = AppTest.from_file(str(script), default_timeout=60)
    at.run()
    assert not at.exception, f"upload path broken: {at.exception}"
    assert "1" in at.markdown[0].value        # the one R5 row came through


def test_kpi_chart_draws_a_line_per_cell():
    """One trace per compared cell, the legend titled the way the deck has it,
    and no fill-to-zero on a dBm axis — that would paint 115 dB of nothing."""
    import pandas as pd

    from _charts import kpi_figure
    from rfopt.kpi.trends import panels_for

    idx = pd.date_range("2026-09-10", periods=12, freq="h")
    rows = []
    for cell, base in (("L_A_BAS0001-1", 40.0), ("L_A_BAS0001-2", 55.0)):
        for i, t in enumerate(idx):
            rows.append({"datetime": t, "object": cell, "site_id": "BAS0001",
                         "prefix": "BAS", "HW_DL PRB Avg Utilization(%)":
                         base + i, "L.UL.Interference.Avg(dBm)": -110.0 - i})
    df = pd.DataFrame(rows)
    cells = ["L_A_BAS0001-1", "L_A_BAS0001-2"]

    prb, intf = panels_for(df, ["HW_DL PRB Avg Utilization(%)",
                                "L.UL.Interference.Avg(dBm)"],
                           level="Site", obj="BAS0001", cells=cells)

    fig = kpi_figure(prb)
    assert [t.name for t in fig.data] == cells
    assert fig.layout.legend.title.text == "Cell Name:"
    # the hover names every line in full (plotly cuts names at 15 characters
    # unless told otherwise) and the legend runs under the plot, where a long
    # cell name has the width it needs
    assert fig.layout.hoverlabel.namelength == -1
    assert all("%{fullData.name}" in t.hovertemplate for t in fig.data)
    assert fig.layout.legend.orientation == "h" and fig.layout.legend.yref == "container"
    assert all(t.fill == "tozeroy" for t in fig.data)
    assert fig.layout.yaxis.title.text.startswith("Average of")

    assert all(t.fill is None for t in kpi_figure(intf).data)


def test_map_kpi_bands_follow_the_operators_thresholds():
    """Green / amber / red on the map have to mean what they mean everywhere
    else — and the thresholds are keyed by the schema's names, so the
    operator's own column has to be translated to find them."""
    import pandas as pd

    from _kpi_map import KPI_BAND, band_sectors, canonical_name, legend_rows

    assert canonical_name("LTE_Availability(%)@AB") == "cell_avail_pct"

    avail = pd.Series({"A-S1": 100.0, "B-S1": 99.5, "C-S1": 96.0,
                       "D-S1": float("nan")})
    bands, spec = band_sectors(avail, "LTE_Availability(%)@AB")
    assert list(bands) == ["ok", "warning", "critical", "none"]
    assert [k for k, _, _ in spec] == ["ok", "warning", "critical"]
    assert spec[0][2] == "[99.9, 100]"       # the YAML's warning line up
    assert spec[1][2] == "[99, 99.9)"

    # a "down" KPI reads the other way round: high PRB is the bad end
    prb = pd.Series({"A-S1": 20.0, "B-S1": 75.0, "C-S1": 95.0})
    bands, spec = band_sectors(prb, "HW_DL PRB Avg Utilization(%)")
    assert list(bands) == ["ok", "warning", "critical"]
    assert spec[2][2] == "(85, 95]"

    rows = legend_rows(pd.Series(["ok", "ok", "critical", "none"]), spec)
    assert rows[0][0] == KPI_BAND["ok"]
    assert rows[0][2] == "(2, 50.00%)"       # count and share, as on the map
    assert rows[-1][1] == "no data" and rows[-1][2] == "(1, 25.00%)"


def test_a_kpi_without_thresholds_is_banded_at_round_numbers():
    """Traffic has no threshold, so the map cuts it at round numbers across
    its real range and shades by magnitude — no good or bad implied."""
    import pandas as pd

    from _kpi_map import RAMP, band_sectors

    traffic = pd.Series({f"S{i}": v for i, v in
                         enumerate([0, 3, 12, 30, 80, 150, 400, 900, 2500])})
    bands, spec = band_sectors(traffic, "4G Data Volume (GB)")
    assert len(spec) > 3                       # a ramp, not three bands
    assert all(c in RAMP for _, c, _ in spec)
    intervals = [i for _, _, i in spec]
    assert intervals[0].startswith("[0, ")
    assert intervals[-1].endswith("+∞)")
    # every sector lands in exactly one band, and none is left unbanded
    assert "none" not in set(bands)
    assert len(set(bands)) == len({b for b in bands})


def test_a_per_nodeb_export_still_colours_the_beams():
    """3G is measured per NodeB, with no sector in it, so a 3G file used to
    leave every beam grey. A sector now falls back to its site's value."""
    import pandas as pd

    from _kpi_map import sector_values

    drawn = pd.DataFrame({"sector_id": ["A-S1", "A-S2", "B-S1", "C-S1"],
                          "site_id": ["A", "A", "B", "C"]})
    per_sector = pd.Series({"A-S1": 99.0})           # a 4G cell of site A
    per_site = pd.Series({"A": 50.0, "B": 97.5})      # the NodeBs of A and B
    v = sector_values(drawn, per_sector, per_site)
    assert v["A-S1"] == 99.0        # its own sector's value wins
    assert v["A-S2"] == 50.0        # no sector value -> the site's
    assert v["B-S1"] == 97.5
    assert pd.isna(v["C-S1"])       # neither -> no data


def test_3g_kpis_use_3g_thresholds_but_totals_are_not_judged():
    """RTT is a 3G KPI with its own thresholds (10 / 20 ms), so it gets
    OK / Warning / Critical. A drop counter summed over a multi-day window must
    not be read against a daily threshold — that one stays a magnitude ramp."""
    import pandas as pd

    from _kpi_map import RAMP, band_sectors

    rtt = pd.Series({"A-S1": 3.0, "B-S1": 15.0, "C-S1": 40.0})
    bands, _ = band_sectors(rtt, "VS.IPPM.Rtt.Means(ms)")
    assert list(bands) == ["ok", "warning", "critical"]

    drops = pd.Series({f"S{i}": float(v)
                       for i, v in enumerate([0, 50, 5000, 900000])})
    _, spec = band_sectors(drops, "VS.RscGroup.FlowCtrol.DL.DropNum")
    assert all(colour in RAMP for _, colour, _ in spec)


def test_the_legend_brings_its_own_styles_into_the_map():
    """st_folium draws the map in an iframe, so the page's CSS never reaches
    it. The legend has to carry its styles into the map document, or it
    renders as loose text with invisible colour squares."""
    import folium

    from _kpi_map import KpiLegend

    m = folium.Map(location=[30.5, 47.8], zoom_start=12, tiles=None)
    m.add_child(KpiLegend("DL PRB", [("#1a7f37", "[0, 20)", "(10, 50.00%)",
                                      "OK")], note="window"))
    doc = m.get_root().render()
    head = doc.split("</head>")[0]
    assert ".sm-legend" in head and ".sm-lg-chip" in head
    assert "background:#1a7f37" in doc       # the square carries its colour


def test_the_healthy_side_is_graded_so_the_map_is_not_one_green():
    import pandas as pd

    from _kpi_map import OK_SHADES, band_sectors

    prb = pd.Series({f"S{i}": v for i, v in
                     enumerate([5, 15, 25, 45, 60, 68, 78, 90])})
    bands, spec = band_sectors(prb, "HW_DL PRB Avg Utilization(%)")
    keys = [k for k, _, _ in spec]
    assert keys[-2:] == ["warning", "critical"]
    ok_keys = keys[:-2]
    assert len(ok_keys) >= 2                     # more than one green
    assert all(c in OK_SHADES for k, c, _ in spec if k.startswith("ok"))
    assert bands["S0"] == ok_keys[0]             # lightest load, darkest green
    assert bands["S6"] == "warning" and bands["S7"] == "critical"

    # a negative range is graded too: UL interference lives below -100 dBm
    ul = pd.Series({f"U{i}": v for i, v in
                    enumerate([-124, -121, -118, -116, -113, -111, -108, -100])})
    bands, spec = band_sectors(ul, "L.UL.Interference.Avg(dBm)")
    ok_keys = [k for k, _, _ in spec if k.startswith("ok")]
    assert len(ok_keys) >= 2
    assert bands["U0"] == ok_keys[0]             # quietest uplink, darkest green
    assert bands["U6"] == "warning" and bands["U7"] == "critical"


def test_an_empty_band_shows_an_open_interval_not_an_inverted_one():
    """With no critical sector on the map the critical row read "(85, 78.07]",
    an interval running backwards. It is open-ended now."""
    import pandas as pd

    from _kpi_map import band_sectors

    prb = pd.Series({"A": 10.0, "B": 40.0, "C": 60.0})       # nothing past 85
    _, spec = band_sectors(prb, "HW_DL PRB Avg Utilization(%)")
    assert {k: t for k, _, t in spec}["critical"] == "(85, +∞)"

    avail = pd.Series({"A": 100.0, "B": 99.95})              # nothing below 99
    _, spec = band_sectors(avail, "LTE_Availability(%)@AB")
    assert {k: t for k, _, t in spec}["critical"] == "(-∞, 99)"


def test_the_map_offers_every_uploaded_exports_kpis():
    """4G and 3G dropped in together: All offers both files' KPIs, labelled by
    technology because their names clash; 4G or 3G offers that file's only.
    The map's upload field used to take one file, so All showed one file."""
    from _kpi_map import kpi_choices

    headers = [("f3", "SHAMS-3G.zip", "3G", ["Integrity", "RTT"]),
               ("f4", "4G Hourly.zip", "4G", ["Integrity", "DL PRB"])]
    every = kpi_choices(headers, "All")
    assert [c.label for c in every.values()] == [
        "Integrity · 4G", "DL PRB · 4G", "Integrity · 3G", "RTT · 3G"]
    assert every["f3::Integrity"].kpi == "Integrity"      # the real column
    assert every["f3::Integrity"].file_id == "f3"          # read from its file
    assert [c.label for c in kpi_choices(headers, "3G").values()] == [
        "Integrity", "RTT"]
    assert kpi_choices(headers, "2G") == {}

    # two exports of one technology: the file name tells them apart
    two = kpi_choices(headers + [("f4b", "4G day2.zip", "4G", ["DL PRB"])], "4G")
    assert [c.label for c in two.values()] == [
        "Integrity · 4G Hourly.zip", "DL PRB · 4G Hourly.zip",
        "DL PRB · 4G day2.zip"]


def test_the_toolbar_strip_does_not_cover_the_header_search():
    """Streamlit's toolbar strip is fixed over the top of the page. With the
    content moved up under it, it swallowed every click on the header's
    search box: no site or location could be searched."""
    import re

    from _ui import _CSS

    top = re.search(r'\[data-testid="stMainBlockContainer"\]\s*\{[^}]*'
                    r'padding-top:\s*([\d.]+)rem', _CSS)
    assert top and float(top.group(1)) >= 3.75     # clears the 3.75rem strip
    strip = re.search(r'header\[data-testid="stHeader"\]\s*\{([^}]*)\}', _CSS)
    assert strip and "pointer-events: none" in strip.group(1)
    assert re.search(r'header\[data-testid="stHeader"\] button[^{]*\{\s*'
                     r'pointer-events: auto', _CSS)   # its own buttons still work


def test_home_entrypoint_navigation():
    at = AppTest.from_file(str(APP / "Home.py"), default_timeout=60)
    at.run()
    assert not at.exception


def test_sites_at_the_same_place_are_set_side_by_side():
    """The KMZ has pairs of sites at the very same coordinates: both are kept,
    and each badge says where it stands among them so neither hides the other."""
    import pandas as pd

    from _map_ui import tower_points

    draw = pd.DataFrame({"site_id": ["A", "B", "C"], "sector_id": ["A-S1", "B-S1", "C-S1"],
                         "sector": [1, 1, 1], "azimuth_deg": [0.0, 90.0, 180.0],
                         "ret_deg": [4.0, 4.0, 4.0], "air": ["onair"] * 3})
    sites = pd.DataFrame({"site_id": ["A", "B", "C"], "site_name": ["Alpha", "Beta", "Gamma"],
                          "latitude": [30.442625, 30.442625, 30.5],
                          "longitude": [47.976717, 47.976717, 47.8], "sectors": [1, 1, 1],
                          "n_2g": [0, 0, 0], "n_3g": [0, 0, 0], "n_4g": [1, 1, 1],
                          "height": [30.0, 30.0, 30.0], "air": ["onair"] * 3,
                          "status": ["On Air"] * 3})
    pts = {p[2]: p for p in tower_points(draw, sites)}
    assert set(pts) == {"A", "B", "C"}
    assert sorted([pts["A"][11], pts["B"][11]]) == [[0, 2], [1, 2]]
    assert pts["C"][11] == [0, 1]


def test_a_tower_takes_its_worst_sectors_colour():
    """A badge shows the sector an engineer should open first, and its card
    lists every sector's value in that sector's own colour."""
    import pandas as pd

    from _kpi_map import band_sectors
    from _map_ui import AIR, tower_points, worst_sectors

    draw = pd.DataFrame({
        "site_id": ["A", "A", "A", "B"],
        "sector_id": ["A-S1", "A-S2", "A-S3", "B-S1"], "sector": [1, 2, 3, 1],
        "azimuth_deg": [0.0, 120.0, 240.0, 90.0],
        "ret_deg": [4.0, float("nan"), 2.0, 3.0],
        "air": ["onair", "onair", "onair", "planned"]})
    sites = pd.DataFrame({
        "site_id": ["A", "B"], "site_name": ["Alpha", None],
        "latitude": [30.5, 30.6], "longitude": [47.8, 47.9], "sectors": [3, 1],
        "n_2g": [3, 0], "n_3g": [3, 0], "n_4g": [6, 1],
        "height": [30.0, float("nan")], "air": ["onair", "planned"],
        "status": ["On Air", "Planned"]})

    kpi = "HW_DL PRB Avg Utilization(%)"
    vals = pd.Series({"A-S1": 20.0, "A-S2": 95.0, "A-S3": 70.0,
                      "B-S1": float("nan")})
    bands, spec = band_sectors(vals, kpi)
    colour = {k: c for k, c, _ in spec}
    pts = {p[2]: p for p in tower_points(
        draw, sites, kpi=kpi, band_of=bands.to_dict(), value_of=vals.to_dict(),
        colour_of=colour, spec=spec)}
    assert pts["A"][4] == colour["critical"]      # 95% PRB is the worst sector
    assert pts["A"][5].startswith("Critical")
    assert [r[0] for r in pts["A"][7]] == ["S1", "S2", "S3"]
    assert pts["A"][7][1][1] == "95" and pts["A"][7][1][3] == colour["critical"]
    assert pts["B"][3] == "B" and pts["B"][5] == "No data for this KPI"

    top = worst_sectors(draw, bands.to_dict(), vals.to_dict(), kpi)
    assert list(top["sector_id"]) == ["A-S2", "A-S3", "A-S1"]

    # no KPI: the air status, and each sector's azimuth
    plain = {p[2]: p for p in tower_points(draw, sites)}
    assert plain["B"][4] == AIR["planned"] and plain["B"][5] == "Planned"
    assert plain["A"][7][0][1] == "az 0° · RET 4.0°"
    assert "4G 6" in plain["A"][6] and "h 30 m" in plain["A"][6]


def test_an_hour_lands_in_the_windows_bands():
    """The time slider colours one hour with the window's bands: the window's
    own values keep their keys, and an hour past its range falls in the open
    outer band instead of turning grey."""
    import pandas as pd

    from _kpi_map import apply_scheme, band_scheme, band_sectors, scheme_segments

    kpi = "HW_DL PRB Avg Utilization(%)"
    prb = pd.Series({f"S{i}": v for i, v in
                     enumerate([5, 15, 25, 45, 60, 68, 78, 90])})
    scheme = band_scheme(prb, kpi)
    bands, spec = band_sectors(prb, kpi)
    assert list(apply_scheme(prb, scheme)) == list(bands) and spec == scheme.spec

    hour = pd.Series([1.0, 99.0, float("nan")])   # quieter and busier than any
    keys = list(apply_scheme(hour, scheme))
    assert keys[0] == bands["S0"]                  # still the best shade
    assert keys[1] == "critical" and keys[2] == "none"

    traffic = pd.Series([0, 3, 12, 30, 80, 150, 400, 900, 2500], dtype=float)
    ramp = band_scheme(traffic, "4G Data Volume (GB)")
    assert list(apply_scheme(pd.Series([-5.0, 9000.0]), ramp)) == [
        "b0", f"b{len(ramp.edges) - 1}"]

    segs = scheme_segments(scheme, 0, 100)
    assert segs[0][0] == 0 and segs[-1][1] == 100
    assert all(a < b for a, b, _ in segs)
    assert segs[-1][2] == spec[-1][1]              # a "down" KPI tops out critical


def test_the_time_frames_come_from_the_exports_own_timestamps():
    """Frames are the file's own timestamps (15 minutes apart here, not assumed
    hourly), a NodeB's hour colours each of its sectors, and the packed codes
    and values read back to the real ones."""
    import numpy as np
    import pandas as pd

    from _kpi_map import band_scheme
    from _kpi_time import (kpi_unit, pack_frames, parse_tip, series_pivots,
                           time_matrix, unpack_value)

    t0, t1 = pd.Timestamp("2026-09-12 14:00"), pd.Timestamp("2026-09-12 14:15")
    kpi = "HW_DL PRB Avg Utilization(%)"
    df = pd.DataFrame({"datetime": [t0, t0, t1, t0, t1],
                       "sector_id": ["A-S1", "A-S1", "A-S1", "B-S0", "B-S0"],
                       "site_id": ["A", "A", "A", "B", "B"],
                       kpi: [20.0, 40.0, 95.0, 50.0, 60.0]})
    sec, site = series_pivots(df, kpi)
    draw = pd.DataFrame({"sector_id": ["A-S1", "B-S1", "B-S2", "C-S1"],
                         "site_id": ["A", "B", "B", "C"]})
    times, matrix = time_matrix(draw, sec, site)
    assert times == [t0, t1]
    assert matrix[0].tolist() == [30.0, 95.0]      # its cells averaged per step
    assert matrix[1].tolist() == matrix[2].tolist() == [50.0, 60.0]
    assert np.isnan(matrix[3]).all()

    window = np.array([62.5, 55.0, 55.0, np.nan])
    scheme = band_scheme(pd.Series(window), kpi)
    order = [k for k, _, _ in scheme.spec] + ["none"]
    packed = pack_frames(window, matrix, scheme, order)
    assert len(packed["codes"]) == 3               # the window, then each step
    assert packed["src"][1] == packed["src"][2]    # one row for the NodeB

    def code(frame, sector):
        return order[ord(packed["codes"][frame][packed["src"][sector]]) - 48]

    assert code(2, 0) == "critical" and code(0, 3) == "none"
    assert abs(unpack_value(packed, 2, 0) - 95.0) <= packed["step"]
    assert unpack_value(packed, 1, 3) is None

    assert parse_tip("BAS3114-S2 · #17") == ("select", "BAS3114-S2")
    assert parse_tip("__close__:1726") == ("close", None)
    assert kpi_unit("Downlink PRB Utilization Rate(%)") == "%"
    assert kpi_unit("4G DL User Throughput mbps_Asiacell") == "Mbps"


def test_the_drawer_shows_each_sectors_own_series():
    """The drawer's KPI tab is that sector's own values at the file's own
    timestamps — never a neighbour's, never a made-up point."""
    import numpy as np
    import pandas as pd

    from _kpi_map import band_scheme
    from _kpi_time import series_pivots
    from _sector_drawer import kpi_config, sector_kpis, table_json

    t0, t1 = pd.Timestamp("2026-09-12 14:00"), pd.Timestamp("2026-09-12 15:00")
    kpi = "HW_DL PRB Avg Utilization(%)"
    df = pd.DataFrame({"datetime": [t0, t1, t0],
                       "sector_id": ["A-S1", "A-S1", "A-S2"],
                       "site_id": ["A", "A", "A"], kpi: [40.0, 90.0, 10.0]})
    sec, site = series_pivots(df, kpi)
    secs = pd.DataFrame({"sector_id": ["A-S1", "A-S2", "A-S3"],
                         "site_id": ["A", "A", "A"]})
    window = np.array([65.0, 10.0, np.nan])
    scheme = band_scheme(pd.Series(window), kpi)

    rows = sector_kpis(secs, sec, site, window, scheme)
    assert rows[0]["series"] == [40.0, 90.0] and rows[0]["src"] == "sector"
    assert rows[1]["series"] == [10.0, None]              # no 15:00 row: a gap
    assert rows[2]["src"] == "none" and rows[2]["stats"] == {"n": 0}
    order = [k for k, _, _ in scheme.spec] + ["none"]
    assert len(rows[0]["codes"]) == 3                     # window + 2 steps
    assert order[ord(rows[0]["codes"][2]) - 48] == "critical"   # 90% at 15:00

    cfg = kpi_config(label=kpi, kpi=kpi, unit="%", how="mean", file_name="x.zip",
                     scheme=scheme, draw=secs, window_vals=window, sec=sec,
                     site=site, store="k")
    assert cfg["times"] == ["2026-09-12 14:00", "2026-09-12 15:00"]
    assert cfg["range"] == [10.0, 90.0] and cfg["rule"]["direction"] == "down"
    assert cfg["bands"][-1]["key"] == "none"

    tbl = table_json(pd.DataFrame({"Cell": ["L1"], "PCI": [212.0],
                                   "RET°": [float("nan")]}))
    assert tbl == {"cols": ["Cell", "PCI", "RET°"], "rows": [["L1", "212", "–"]]}


def test_the_map_reads_its_kpi_exports_from_data_resources(put_resource):
    """The map's KPI list is the Current KPI Data of Data Resources — no upload
    on the page. A file's id is its content hash, so a pick made before leaving
    the page (or closing the app) finds its file again."""
    import io
    import zipfile

    csv = ("\n\n\nSHAMS-3G\nSave Time :2026-09-11 10:51:53\n\n"
           "Time,RNC,NODEBNAME,NodeB ID,Integrity,"
           "VS.RscGroup.FlowCtrol.DL.DropNum,VS.IPPM.Rtt.Means(ms),"
           "3G_Availability@AB\n"
           "2026-09-08 00:00,RBASH01,Tannumah6_BAS0038,38,100%,0,0,100\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("export(Subreport 1).csv", csv)

    f1 = put_resource("kpi", "SHAMS-3G_kept.zip", buf.getvalue(), "3G KPI").sha1[:12]
    at = AppTest.from_file(str(APP / "views/site_map.py"), default_timeout=240)
    # a pick made before leaving the page; the radio's own state is gone
    at.session_state["sm_kpi_pick_keep"] = f"{f1}::3G_Availability@AB"
    at.run()
    assert not at.exception, at.exception
    assert not at.get("file_uploader")                  # nothing is uploaded here
    pick = at.radio(key="sm_kpi_pick")
    assert "VS.IPPM.Rtt.Means(ms)" in pick.options      # the labels it shows
    assert pick.value == f"{f1}::3G_Availability@AB"
    # the file is read, not reported: no page but Data Resources names it
    assert not any("SHAMS-3G_kept.zip" in e.proto.body for e in at.get("html"))


def test_the_select_location_tool_rides_in_the_map():
    """Select Location is a map tool: its window, marker and styles live in
    the map document, it reads coordinates to 6 decimals locally, copies them
    with the clipboard API, gives way to the ruler and the draw tools, and
    sends nothing to Python. The towers carry no location window of their own
    and cannot be dragged."""
    import folium

    from _location_pick import LocationPick

    m = folium.Map(location=[30.5, 47.8], zoom_start=14, tiles=None)
    m.add_child(LocationPick())
    doc = m.get_root().render()
    head = doc.split("</head>")[0]
    assert ".rf-pick-win" in head and ".rf-pick-mk" in head
    assert "Selected Location" in doc and "DEC = 6" in doc
    assert "navigator.clipboard" in doc and "draw:drawstart" in doc
    assert "__GLOBAL_DATA__" not in doc

    towers = (APP / "_map_ui.py").read_text(encoding="utf-8")
    assert "onTowerAdded" not in towers and "showLocation" not in towers
    page = (APP / "views" / "site_map.py").read_text(encoding="utf-8")
    assert "RF.cancelPick" in page and "cancelRuler" in page
    # it IS the marker tool of the right-hand toolbar: the draw plugin's own
    # marker (which reran and reloaded the map) is off, this one takes its place
    assert '"marker": False' in page and '"marker": True' not in page
    assert page.index("Draw(position=") < page.index("_LocationPick()")
    assert "position: 'topright'" in doc


def test_full_screen_kpi_dropdown_drives_the_one_kpi_pick(put_resource):
    """The dropdown on the full-screen map keeps no KPI of its own: it shows
    the sidebar's pick and writes back to it, so leaving full screen finds the
    sidebar list on the KPI chosen inside it."""
    import io
    import zipfile

    csv = ("\n\n\nSHAMS-3G\nSave Time :2026-09-11 10:51:53\n\n"
           "Time,RNC,NODEBNAME,NodeB ID,Integrity,"
           "VS.RscGroup.FlowCtrol.DL.DropNum,VS.IPPM.Rtt.Means(ms),"
           "3G_Availability@AB\n"
           "2026-09-08 00:00,RBASH01,Tannumah6_BAS0038,38,100%,0,0,100\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("export(Subreport 1).csv", csv)

    f1 = put_resource("kpi", "SHAMS-3G_fs.zip", buf.getvalue(), "3G KPI").sha1[:12]
    if _REAL_KMZ.exists():
        put_resource("kmz", _REAL_KMZ.name, _REAL_KMZ, "Site KMZ")
    at = AppTest.from_file(str(APP / "views/site_map.py"), default_timeout=240)
    at.session_state["sm_kpi_pick_keep"] = f"{f1}::3G_Availability@AB"
    at.session_state["sm_fs"] = True
    at.run()
    assert not at.exception, at.exception
    fs_pick = [s for s in at.selectbox if s.key == "sm_kpi_pick_fs"]
    if not fs_pick:
        pytest.skip("no site KMZ on this machine: the page stops before the map")
    assert fs_pick[0].value == f"{f1}::3G_Availability@AB"   # the sidebar's pick

    fs_pick[0].set_value(f"{f1}::VS.IPPM.Rtt.Means(ms)").run()
    assert not at.exception, at.exception
    assert at.radio(key="sm_kpi_pick").value == f"{f1}::VS.IPPM.Rtt.Means(ms)"

    at.session_state["sm_fs"] = False                       # leave full screen
    at.run()
    assert not at.exception, at.exception
    assert at.radio(key="sm_kpi_pick").value == f"{f1}::VS.IPPM.Rtt.Means(ms)"
    assert not [s for s in at.selectbox if s.key == "sm_kpi_pick_fs"]


def test_the_full_screen_kpi_list_opens_above_the_map():
    """Streamlit opens a dropdown's list in a layer on the page body. The
    full-screen map is pinned above everything (z-index 2147483000), so the
    KPI list opened hidden behind it and no KPI could be picked."""
    import re

    src = (APP / "views" / "site_map.py").read_text(encoding="utf-8")
    fs_css = src.split("_FS_CSS = ", 1)[1].split('"""', 2)[1]
    wrap = re.search(r"st-key-sm_mapwrap[^}]*z-index:\s*(\d+)", fs_css)
    lift = re.search(r'stSelectboxVirtualDropdown"\]\s*\{\s*z-index:\s*(\d+)', fs_css)
    assert wrap and lift and int(lift.group(1)) > int(wrap.group(1))


def _sectors_shown(at):
    """The sector count the Total Sites card carries: "4,480 sectors (All)"."""
    import re

    for e in at.get("html"):
        m = re.search(r"([\d,]+) sectors \(All\)", getattr(e.proto, "body", ""))
        if m:
            return m.group(1)
    return None


@pytest.mark.skipif(not _REAL_KMZ.exists(), reason="R5_Sites.kmz not present")
def test_site_map_search_keeps_every_tower(put_resource):
    """A complaint lat/lon search must not filter the sectors off the map."""
    put_resource("kmz", _REAL_KMZ.name, _REAL_KMZ, "Site KMZ")
    at = AppTest.from_file(str(APP / "views/site_map.py"), default_timeout=120)
    at.run()
    assert not at.exception
    full = _sectors_shown(at)
    assert full

    at.text_input(key="sm_q").set_value("30.515, 47.762").run()
    assert not at.exception
    after = _sectors_shown(at)
    assert after == full                       # search only re-centres the map
    assert any("Best-pointed serving sector" in s.value for s in at.success)
