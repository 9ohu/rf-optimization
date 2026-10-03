"""The Sites map's KPI, judged cell by cell (Site Map only).

A sector used to be shown by the mean of its cells over the whole export, read
on the FDD line: a cell over its line in a few hours, or a TDD cell, was
hidden. Now each cell is judged on its own every hour (or on its own daily
value), on its own line, and a sector is its worst cell.
"""

import html as _html
import io
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import _kpi_cells as KC  # noqa: E402
from _kpi_map import band_scheme, threshold_rule  # noqa: E402

UL = "L.UL.Interference.Avg(dBm)"
PRB = "HW_DL PRB Avg Utilization(%)"
HOURS = pd.date_range("2026-10-01 00:00", periods=48, freq="h")
S3 = "BAS3146-S3"


def _frame(cells, kpi=UL, hours=HOURS):
    """cells: (name, duplex, value(hour) -> float) on sector BAS3146-S3."""
    rows = [(h, name, "BAS3146", S3, dup, fn(h)) for h in hours for name, dup, fn in cells]
    return pd.DataFrame(rows, columns=["datetime", "object", "site_id", "sector_id",
                                       "duplex", kpi])


def _quiet(v):
    return lambda h: v


def _crit(m, kpi=UL):
    return KC.critical_cells(m, "sector", S3, list(m.sec_t.columns), threshold_rule(kpi))


def test_one_cell_over_its_line_in_a_few_hours_makes_the_sector_critical():
    # 1. two quiet cells, one over the FDD line (-105) for 3 hours: the old
    #    sector mean read OK; each cell judged every hour does not
    df = _frame([("L18_X_BAS3146-3", "CELL_FDD", _quiet(-116)),
                 ("L21_X_BAS3146-3", "CELL_FDD", _quiet(-114)),
                 ("L09_X_BAS3146-3", "CELL_FDD",
                  lambda h: -101.0 if h.hour in (9, 10, 11) and h.day == 1 else -113.0)])
    assert df[UL].mean() < -105                           # what the map used to show: OK
    m = KC.evaluate(df, UL, rule=threshold_rule(UL))
    assert m.judged and m.sev_sector[S3] == 2 and m.per_sector[S3] == -101.0
    hours_crit = m.sev_sec_t.loc[S3]
    assert int((hours_crit >= 2).sum()) == 3                # exactly those hours
    assert m.sec_t.loc[S3, pd.Timestamp("2026-10-01 10:00")] == -101.0   # its worst cell
    assert m.sec_t.loc[S3, pd.Timestamp("2026-10-01 15:00")] == -113.0
    crit = _crit(m)
    assert [c["name"] for c in crit] == ["L09_X_BAS3146-3"]
    assert crit[0]["n"] == 3 and crit[0]["layer"] == "FDD" and crit[0]["line"] == -105.0
    assert len(crit[0]["series"]) == len(HOURS) and crit[0]["series"][10] == -101.0


def test_a_tdd_cell_is_judged_on_the_tdd_line_and_an_fdd_cell_on_the_fdd_line():
    rule = threshold_rule(UL)
    # 2. a TDD cell over the TDD line (-100) for 2 hours: critical
    df = _frame([("L21_X_BAS3146-3", "CELL_FDD", _quiet(-115)),
                 ("L261st_X_BAS3146-3", "CELL_TDD",
                  lambda h: -96.0 if h.hour in (13, 14) and h.day == 2 else -108.0)])
    m = KC.evaluate(df, UL, rule=rule)
    assert m.sev_sector[S3] == 2
    crit = _crit(m)
    assert [(c["name"], c["layer"], c["n"], c["line"]) for c in crit] == [
        ("L261st_X_BAS3146-3", "TDD", 2, -100.0)]
    # a TDD cell at -102 is over the FDD line but under its own: not critical
    df = _frame([("L262nd_X_BAS3146-3", "CELL_TDD", _quiet(-102))])
    m = KC.evaluate(df, UL, rule=rule)
    assert m.sev_sector[S3] == 0 and _crit(m) == []
    scheme = band_scheme(pd.Series([-102.0, -110.0]), UL)
    keys = KC.band_keys([-102.0, -110.0], [0, 0], scheme)
    assert not set(keys) & {"critical", "warning"}          # green, though past -105
    # 3. an FDD cell at -102: over its line, critical
    m = KC.evaluate(_frame([("L18_X_BAS3146-3", "CELL_FDD", _quiet(-102))]), UL, rule=rule)
    assert m.sev_sector[S3] == 2 and _crit(m)[0]["line"] == -105.0
    assert list(KC.band_keys([-102.0], [2], scheme)) == ["critical"]


def test_several_cells_critical_each_at_its_own_hours():
    # 4. + 5. three cells, each over its line at different hours; one cell clean
    df = _frame([("L21_N_BAS3146-1", "CELL_FDD", lambda h: -100.0 if h.hour == 2 else -112.0),
                 ("L21_N_BAS3146-2", "CELL_FDD", lambda h: -103.0 if h.hour == 20 else -111.0),
                 ("L262nd_N_BAS3146-3", "CELL_TDD",
                  lambda h: -95.0 if h.hour in (8, 9) else -109.0),
                 ("L_N_BAS3146-3", "CELL_FDD", _quiet(-117))])
    m = KC.evaluate(df, UL, rule=threshold_rule(UL))
    crit = _crit(m)
    assert [(c["name"], c["n"]) for c in crit] == [
        ("L21_N_BAS3146-1", 2), ("L21_N_BAS3146-2", 2), ("L262nd_N_BAS3146-3", 4)]
    # each hour shows the cell that was over its line then
    row, val = m.sev_sec_t.loc[S3], m.sec_t.loc[S3]
    for hour, worst in ((2, -100.0), (20, -103.0), (8, -95.0), (12, -109.0)):
        t = pd.Timestamp(f"2026-10-01 {hour:02d}:00")
        assert val[t] == worst
        assert row[t] == (0 if hour == 12 else 2)
    # 12:00: the TDD cell at -109 is the highest value, but no cell is over its line
    assert int((row >= 2).sum()) == 2 + 2 + 4


def test_a_normal_daily_average_never_hides_an_hourly_breach_per_hour():
    # 6. a cell over its line 2 hours a day, normal the rest: its daily mean is
    #    normal — Per Hour flags it, Per Day reads the cell's own day
    df = _frame([("L18_X_BAS3146-3", "CELL_FDD",
                  lambda h: -98.0 if h.hour in (18, 19) else -112.0),
                 ("L21_X_BAS3146-3", "CELL_FDD", _quiet(-115))])
    rule = threshold_rule(UL)
    daily = df[df["object"] == "L18_X_BAS3146-3"].groupby(df["datetime"].dt.date)[UL].mean()
    assert (daily < -105).all()                              # the daily average is normal
    hourly = KC.evaluate(df, UL, rule=rule, period=KC.PER_HOUR)
    assert hourly.sev_sector[S3] == 2 and _crit(hourly)[0]["n"] == 4
    day = KC.evaluate(df, UL, rule=rule, period=KC.PER_DAY)
    assert list(day.sec_t.columns) == [pd.Timestamp("2026-10-01"), pd.Timestamp("2026-10-02")]
    assert day.sev_sector[S3] == 0 and day.per_sector[S3] == pytest.approx(daily.max())
    # per day, still the cell's own day — never averaged with the other cells
    bad = _frame([("L18_X_BAS3146-3", "CELL_FDD", _quiet(-100)),
                  ("L21_X_BAS3146-3", "CELL_FDD", _quiet(-120)),
                  ("L09_X_BAS3146-3", "CELL_FDD", _quiet(-120))])
    assert bad[UL].mean() < -105
    day = KC.evaluate(bad, UL, rule=rule, period=KC.PER_DAY)
    assert day.sev_sector[S3] == 2 and [c["n"] for c in _crit(day)] == [2]


def test_prb_is_judged_cell_by_cell_too():
    df = _frame([("L18_X_BAS3146-3", "CELL_FDD", lambda h: 92.0 if h.hour == 21 else 40.0),
                 ("L21_X_BAS3146-3", "CELL_FDD", _quiet(20.0))], kpi=PRB)
    assert df[PRB].mean() < 80
    m = KC.evaluate(df, PRB, rule=threshold_rule(PRB))
    assert m.sev_sector[S3] == 2 and _crit(m, PRB)[0]["name"] == "L18_X_BAS3146-3"


def test_the_4g_layer_keeps_only_its_cells():
    df = _frame([("L21_X_BAS3146-3", "CELL_FDD", _quiet(-101)),      # FDD: critical
                 ("L261st_X_BAS3146-3", "CELL_TDD", _quiet(-104))])  # TDD: OK
    rule = threshold_rule(UL)
    assert KC.evaluate(df, UL, rule=rule, layer=KC.ALL).sev_sector[S3] == 2
    tdd = KC.evaluate(df, UL, rule=rule, layer=KC.TDD)
    assert tdd.sev_sector[S3] == 0 and set(tdd.cells["object"]) == {"L261st_X_BAS3146-3"}
    fdd = KC.evaluate(df, UL, rule=rule, layer=KC.FDD)
    assert fdd.sev_sector[S3] == 2 and set(fdd.cells["object"]) == {"L21_X_BAS3146-3"}
    # an export without the FDD / TDD column: the KMZ's band of the cell
    kmz_cells = pd.DataFrame({"cell_name": ["L21_X_BAS3146-3", "L261st_X_BAS3146-3"],
                              "technology": ["4G", "4G"],
                              "band_label": ["L2100", "L2600(TDD)"]})
    bare = df.drop(columns="duplex")
    m = KC.evaluate(bare, UL, rule=rule, layer=KC.TDD,
                    duplex_of=KC.duplex_of_cells(kmz_cells))
    assert m.has_duplex and set(m.cells["object"]) == {"L261st_X_BAS3146-3"}
    assert _crit(KC.evaluate(bare, UL, rule=rule,
                             duplex_of=KC.duplex_of_cells(kmz_cells)))[0]["layer"] == "FDD"


def test_a_kpi_without_a_threshold_keeps_the_mean_of_the_cells():
    kpi = "L.Traffic.User.Avg"
    df = _frame([("A_BAS3146-3", "CELL_FDD", _quiet(10.0)),
                 ("B_BAS3146-3", "CELL_FDD", _quiet(30.0))], kpi=kpi)
    assert threshold_rule(kpi) is None
    m = KC.evaluate(df, kpi, rule=None)
    assert not m.judged and m.per_sector[S3] == 20.0 and m.sev_sector is None
    assert (m.sec_t.loc[S3] == 20.0).all()


def test_the_frames_are_banded_by_the_cell_behind_each_value():
    from _kpi_time import pack_frames, time_labels
    df = _frame([("L262nd_X_BAS3146-3", "CELL_TDD", _quiet(-102))])
    scheme = band_scheme(pd.Series([-102.0, -102.0]), UL)
    order = [k for k, _, _ in scheme.spec] + ["none"]
    # two sectors with the same value: one judged OK (a TDD cell), one critical
    win = np.array([-102.0, -102.0])
    mat = np.array([[-102.0], [-102.0]])
    packed = pack_frames(win, mat, scheme, order, np.array([0.0, 2.0]), np.array([[0.0], [2.0]]))
    keys = [[order[ord(f[packed["src"][i]]) - 48] for f in packed["codes"]] for i in (0, 1)]
    assert "critical" not in keys[0] and keys[1] == ["critical", "critical"]
    # without severities: as before, the value alone
    plain = pack_frames(win, mat, scheme, order)
    assert plain["src"] == [0, 0]
    assert time_labels([pd.Timestamp("2026-10-01")], KC.PER_DAY) == ["2026-10-01 Whole day"]
    assert time_labels([pd.Timestamp("2026-10-01 05:00")]) == ["2026-10-01 05:00"]
    del df


# --------------------------------------------------------------------------- #
# the Sites map page: Current View, the map, the drawer
# --------------------------------------------------------------------------- #
SITE = (30.50, 47.80)


def _kmz() -> bytes:
    """BAS0001: sector 1 with an FDD cell (L1800) and a TDD cell (L2600)."""
    def pm(sec, az, cells):
        b = (f"<b>Alpha_BAS0001</b><br/>Site Code: BAS0001 &nbsp; Sector: {sec} &nbsp; "
             f"Azimuth: {az}&deg;<br/>Height: 30 m &nbsp; Status: On Air<br/><br/>"
             "<b>4G (LTE) Cells</b><br/>" + "".join(
                 f"<u>{n}</u>: Azimuth={az}&deg;, PCI=9{sec}, EARFCN={ea}, BW=20MHz, "
                 "Status=Active<br/>" for n, ea in cells))
        lat, lon = SITE
        return (f"<Placemark><styleUrl>#onair</styleUrl><description><![CDATA[{b}]]>"
                "</description><Polygon><outerBoundaryIs><LinearRing><coordinates>"
                f"{lon},{lat},0 {lon + 0.001},{lat + 0.001},0 {lon},{lat},0"
                "</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>")
    kml = ("<?xml version='1.0'?><kml><Document>"
           + pm(1, 0, [("L_Alpha_BAS0001-1", 1750), ("L261st_Alpha_BAS0001-1", 39150)])
           + pm(2, 120, [("L_Alpha_BAS0001-2", 1750)])
           + "</Document></kml>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("doc.kml", kml)
    return buf.getvalue()


def _kpi_csv() -> str:
    """Sector 1: a quiet FDD cell and a TDD cell over the TDD line 12-14 h on
    1 Oct; sector 2 quiet. Its whole-window mean is normal."""
    head = ("Time,eNodeB Name,Cell FDD TDD Indication,Cell Name,LocalCell Id,"
            f"{UL},{PRB}")
    rows = [head]
    for h in HOURS:
        tdd = -95 if (h.day == 1 and 12 <= h.hour <= 14) else -110
        rows += [f"{h:%Y-%m-%d %H:%M},Alpha_BAS0001,CELL_FDD,L_Alpha_BAS0001-1,1,-116,30",
                 f"{h:%Y-%m-%d %H:%M},Alpha_BAS0001,CELL_TDD,L261st_Alpha_BAS0001-1,11,{tdd},30",
                 f"{h:%Y-%m-%d %H:%M},Alpha_BAS0001,CELL_FDD,L_Alpha_BAS0001-2,2,-117,30"]
    return "\n".join(rows) + "\n"


def _payload(at) -> dict | None:
    for e in at.get("html"):
        b = e.proto.body
        if 'id="rf-drawer-payload"' in b:
            txt = b.split(">", 1)[1].rsplit("</div>", 1)[0]
            return json.loads(_html.unescape(txt)) if txt.strip() else None
    return None


def test_the_sites_map_judges_each_cell_and_lists_the_critical_ones(
        tmp_path, monkeypatch, put_resource):
    monkeypatch.setenv("RFOPT_CACHE_DIR", str(tmp_path))
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    import streamlit_folium

    put_resource("kpi", "R5 4G Hourly KPI.csv", _kpi_csv().encode(), "4G KPI")
    put_resource("kmz", "R5_Sites.kmz", _kmz(), "Site KMZ")
    maps = []
    monkeypatch.setattr(streamlit_folium, "st_folium",
                        lambda fmap, **kw: maps.append(fmap) or {})
    at = AppTest.from_file(str(APP / "views/site_map.py"), default_timeout=300)
    at.session_state["sm_sel_sector"] = "BAS0001-S1"
    at.run()
    assert not at.exception, at.exception
    # Current View: the period, Per Hour first; the layer only for 4G
    period = at.segmented_control(key="sm_period")
    assert period.options == ["Per Hour", "Per Day"] and period.value == "Per Hour"
    assert not [w for w in at.segmented_control if w.key == "sm_layer"]
    at.segmented_control(key="sm_topo").set_value("4G").run()
    layer = at.segmented_control(key="sm_layer")
    assert layer.options == ["TDD", "FDD", "All"] and layer.value == "All"
    # the UL interference KPI on the map
    kpi = next(o for o in at.radio(key="sm_kpi_pick").options if UL in o)
    at.radio(key="sm_kpi_pick").set_value(next(
        k for k in at.radio(key="sm_kpi_pick").options if k == kpi)).run()
    assert not at.exception, at.exception

    def timeline():
        tl = next(c for c in maps[-1]._children.values() if type(c).__name__ == "KpiTimeline")
        return json.loads(tl.cfg)

    cfg = timeline()
    assert cfg["judged"] and cfg["period"] == "Per Hour" and len(cfg["times"]) == 48
    assert "FDD OK ≤ -105" in cfg["ruleText"] and "TDD OK ≤ -100" in cfg["ruleText"]
    assert "worst cell" in cfg["agg"]

    def band(cfg, sector_pos, frame):
        return cfg["bands"][ord(cfg["codes"][frame][cfg["src"][sector_pos]]) - 48]["key"]

    # the whole window: sector 1 critical (its TDD cell), sector 2 not
    assert band(cfg, 0, 0) == "critical" and band(cfg, 1, 0) != "critical"
    noon = cfg["times"].index("2026-10-01 13:00") + 1
    assert band(cfg, 0, noon) == "critical" and band(cfg, 0, noon + 5) != "critical"
    # the drawer: Critical Cells = the cells over their line, with their series
    pay = _payload(at)
    s1 = next(s for s in pay["sectors"] if s["id"] == "BAS0001-S1")
    assert [(c["name"], c["layer"], c["n"], c["line"]) for c in s1["crit"]] == [
        ("L261st_Alpha_BAS0001-1", "TDD", 3, -100.0)]
    assert len(s1["crit"][0]["series"]) == 48 and s1["window"] == -95.0
    assert next(s for s in pay["sectors"] if s["id"] == "BAS0001-S2")["crit"] == []

    # FDD layer: only FDD cells — sector 1 is quiet there
    at.segmented_control(key="sm_layer").set_value("FDD").run()
    assert band(timeline(), 0, 0) != "critical"
    assert next(s for s in _payload(at)["sectors"] if s["id"] == "BAS0001-S1")["crit"] == []
    # TDD layer: only the sectors with a TDD cell, read on the TDD line
    at.segmented_control(key="sm_layer").set_value("TDD").run()
    cfg = timeline()
    assert band(cfg, 0, 0) == "critical" and len(cfg["src"]) == 1    # sector 1 only
    assert cfg["rule"]["critical"] == -100.0 and cfg["ruleText"] is None
    # Per Day: the TDD cell's own day is under its line
    at.segmented_control(key="sm_layer").set_value("All").run()
    at.segmented_control(key="sm_period").set_value("Per Day").run()
    cfg = timeline()
    assert cfg["period"] == "Per Day" and cfg["times"] == ["2026-10-01 Whole day",
                                                           "2026-10-02 Whole day"]
    assert band(cfg, 0, 0) != "critical"
    assert "daily mean" in cfg["agg"]


def test_the_drawer_shows_critical_cells_instead_of_the_data_source():
    js = (APP / "assets" / "sector_drawer.js").read_text(encoding="utf-8")
    kpi_tab = js[js.index("var kpiTab = function"):js.index("var cells = function")]
    assert "['Critical Cells', critCells(s), true]" in kpi_tab
    assert "'Data source'" not in kpi_tab
    assert "k.judged ? 'Worst cell value'" in kpi_tab and "k.agg ||" in kpi_tab
    hist = js[js.index("var cellChart = function"):js.index("var BODY =")]
    # only the critical cells, one colour each, their names under the chart
    assert "s.crit.length" in hist and "cellColour(i)" in hist and "cellColour(j)" in hist
    assert "rf-cc-leg" in hist and "esc(c.name)" in hist
