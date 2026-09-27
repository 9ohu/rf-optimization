"""The R5 team's analysis rules, path by path.

* thresholds: main KPIs judged on their documented lines, every hourly value
  on its own; KPIs without a confirmed source are shown, never judged;
* Delay tickets: every cell of the site; flow control per site and hour;
  resolution over every hour after the window to the end of the data;
* user location: the ticket's Site ID first (its sector facing the user), the
  best server only without one (empty / 0); RSRP from the grid cell alone, no
  grid = Coverage Issue; a few neighbour sectors, each on all of its cells;
* Sleep: the reason from the Diagnostic Comment > RF Analysis > Closure Code,
  Not Technical, DT = coverage, planned = coverage unless load, Issue Hours.
"""

import io
import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from rfopt.complaints import relocate as RL
from rfopt.complaints.correlate import (NO_ISSUE, NOT_RESOLVED, RESOLVED, TECHNICAL, UNKNOWN,
                                        analyse_ticket, build_tracks, judged_kpis)
from rfopt.kpi.thresholds import hourly_severity, judged_rule, load_thresholds
from rfopt.sleep import analysis as A

PRB = "HW_DL PRB Avg Utilization(%)"
INTER = "L.UL.Interference.Avg(dBm)"
AVAIL = "LTE_Availability(%)@AB"
RTWP = "VS.MeanRTWP(dBm)"
FLOW = "VS.RscGroup.FlowCtrol.DL.DropNum"
SITE = (30.50, 47.80)
PT = pd.Timestamp("2026-09-13 16:20")


def _at(bearing_deg: float, metres: float, site=SITE) -> tuple[float, float]:
    lat = site[0] + metres * math.cos(math.radians(bearing_deg)) / 111_320.0
    lon = site[1] + metres * math.sin(math.radians(bearing_deg)) / (
        111_320.0 * math.cos(math.radians(site[0])))
    return lat, lon


# --------------------------------------------------------------------------- #
# thresholds
# --------------------------------------------------------------------------- #
def test_the_main_kpis_are_judged_on_the_team_lines_and_only_sourced_rules_judge():
    lte, umts = load_thresholds("LTE"), load_thresholds("UMTS")
    assert (lte.rule("dl_prb_util").warning, lte.rule("dl_prb_util").critical) == (80, 80)
    assert lte.rule("cell_avail_pct").critical == 99
    assert lte.rule("ul_rssi_dbm").critical == -105
    assert lte.rule("ul_rssi_tdd_dbm").critical == -100
    assert umts.rule("ul_rtwp_dbm").critical == -90
    assert umts.rule("dl_flowctrl_drops").critical == 100_000
    for kpi, tech in (("dl_prb_util", "LTE"), ("cell_avail_pct", "LTE"), ("ul_rssi_dbm", "LTE"),
                      ("ul_rtwp_dbm", "UMTS"), ("dl_flowctrl_drops", "UMTS"),
                      ("cell_avail_pct", "UMTS")):
        assert judged_rule(kpi, tech) is not None, kpi
    # no confirmed Huawei source: shown, never judged
    for kpi in ("erab_drop_rate", "rrc_setup_sr", "avg_sinr_db"):
        rule = lte.rule(kpi)
        assert rule is None or not rule.judged, kpi


def test_every_hour_is_judged_on_its_own_never_an_average():
    prb = judged_rule("dl_prb_util")
    # one hour at 81 % among 23 quiet hours: the daily mean is ~32 %, the hour is an issue
    vals = [30.0] * 23 + [81.0]
    assert np.mean(vals) < 80
    assert hourly_severity(prb, vals).tolist() == [0] * 23 + [2]
    assert hourly_severity(prb, [80.0])[0] == 0             # the line itself is not over it
    avail = judged_rule("cell_avail_pct")
    assert hourly_severity(avail, [99.0, 98.9, np.nan]).tolist() == [0, 2, 0]
    flow = judged_rule("dl_flowctrl_drops", "UMTS")
    assert hourly_severity(flow, [100_000, 100_001]).tolist() == [0, 2]
    rtwp = judged_rule("ul_rtwp_dbm", "UMTS")
    assert hourly_severity(rtwp, [-91.0, -89.5]).tolist() == [0, 2]


def test_ul_interference_has_its_own_line_on_a_tdd_cell():
    inter = judged_rule("ul_rssi_dbm")
    vals = [-104.0, -104.0, -99.0, -110.0]
    duplex = ["CELL_FDD", "CELL_TDD", "CELL_TDD", "CELL_FDD"]
    # FDD -104 is past -105; TDD -104 is inside -100, TDD -99 past it
    assert hourly_severity(inter, vals, duplex).tolist() == [2, 0, 2, 0]


# --------------------------------------------------------------------------- #
# Delay tickets: every cell, flow per site-hour, resolution to the end of data
# --------------------------------------------------------------------------- #
def _lte_csv(prb_c2=None, end_hour=23, site="BAS0001", tdd_c3=False) -> str:
    """Three cells of one site; cell 2 congested 15:00-17:00 unless `prb_c2`."""
    head = ("Time,eNodeB Name,Cell FDD TDD Indication,Cell Name,LocalCell Id,"
            f"{PRB},{INTER},{AVAIL}")
    rows = [head]
    for h in pd.date_range("2026-09-13 08:00", f"2026-09-13 {end_hour}:00", freq="h"):
        for c in (1, 2, 3):
            prb = 30
            if c == 2:
                prb = prb_c2(h) if prb_c2 else (95 if 15 <= h.hour <= 17 else 30)
            duplex = "CELL_TDD" if (tdd_c3 and c == 3) else "CELL_FDD"
            inter = -104 if (tdd_c3 and c == 3) else -118
            rows.append(f"{h:%Y-%m-%d %H:%M},Alpha_{site},{duplex},L_Alpha_{site}-{c},{c},"
                        f"{prb},{inter},100")
    return "\n".join(rows) + "\n"


def _umts_csv(drops) -> str:
    """Two 3G cells of one NodeB; `drops(h)` per cell and hour."""
    rows = [f"Time,RNC,NodeB Name,Cell Name,{RTWP},{FLOW}"]
    for h in pd.date_range("2026-09-13 08:00", "2026-09-13 23:00", freq="h"):
        for c in (1, 2):
            rows.append(f"{h:%Y-%m-%d %H:%M},RNC1,U_BAS0001,U_BAS0001-{c},-104,{drops(h)}")
    return "\n".join(rows) + "\n"


def _frame(csv: str, cols, tech: str):
    from rfopt.ingest.hourly_kpi import load_hourly_raw
    raw = load_hourly_raw(io.BytesIO(csv.encode()), cols)
    return (tech, f"{tech}.csv", raw, judged_kpis(list(raw.columns), tech))


def _lte(**k):
    return _frame(_lte_csv(**k), [PRB, INTER, AVAIL], "4G")


def test_a_delay_ticket_without_location_reads_every_cell_of_the_site():
    frames = [_lte()]
    a = analyse_ticket("BAS0001", PT, build_tracks(frames), 2.0)
    assert a.classification == TECHNICAL and a.problem_type
    prb = next(c for c in a.checks if c.canon == "dl_prb_util")
    assert prb.cells == ["L_Alpha_BAS0001-1", "L_Alpha_BAS0001-2", "L_Alpha_BAS0001-3"]
    assert prb.bad_cells == ["L_Alpha_BAS0001-2"] and prb.worst_obj == "L_Alpha_BAS0001-2"
    assert "(1 of 3 cells)" in a.evidence
    # the main KPIs lead the checks
    assert a.checks and RL.lead_check(a).canon == "dl_prb_util"


def test_the_resolution_reads_every_hour_after_the_window_to_the_end_of_the_data():
    # congested in the window, clear for a while, back again at the last hour
    back = _lte(prb_c2=lambda h: 95 if (15 <= h.hour <= 17 or h.hour == 23) else 30)
    a = analyse_ticket("BAS0001", PT, build_tracks([back]), 2.0)
    assert a.resolution == NOT_RESOLVED
    assert "to the end of the data" in a.resolution_evidence
    # clear from 19:00 to the end: resolved, even though 19:00 is past +2 h
    late = _lte(prb_c2=lambda h: 95 if 15 <= h.hour <= 18 else 30)
    a = analyse_ticket("BAS0001", PT, build_tracks([late]), 2.0)
    assert a.resolution == RESOLVED and "from 13 Sep 19:00" in a.resolution_evidence
    # the export ends with the window: nothing after it
    short = _lte(end_hour=18)
    a = analyse_ticket("BAS0001", PT, build_tracks([short]), 2.0)
    assert a.resolution == UNKNOWN


def test_flow_control_is_the_sites_drops_of_each_hour():
    # 60,000 per cell = 120,000 per site in the window hours: an issue per site-hour
    umts = _frame(_umts_csv(lambda h: 60_000 if 15 <= h.hour <= 17 else 1_000),
                  [RTWP, FLOW], "3G")
    tracks = build_tracks([umts])
    flow = next(t for t in tracks if t.canon == "dl_flowctrl_drops")
    assert flow.level == "site"
    a = analyse_ticket("BAS0001", PT, tracks, 2.0)
    fc = next(c for c in a.checks if c.canon == "dl_flowctrl_drops")
    assert fc.sev == 2 and fc.worst == 120_000 and fc.breach_hours == 3
    # 40,000 per cell = 80,000 per site: under the line in every hour
    quiet = _frame(_umts_csv(lambda h: 40_000), [RTWP, FLOW], "3G")
    fc = next(c for c in analyse_ticket("BAS0001", PT, build_tracks([quiet]), 2.0).checks
              if c.canon == "dl_flowctrl_drops")
    assert fc.sev == 0


def test_a_tdd_cell_is_judged_on_the_tdd_interference_line():
    frames = [_lte(prb_c2=lambda h: 30, tdd_c3=True)]
    a = analyse_ticket("BAS0001", PT, build_tracks(frames), 2.0)
    inter = next(c for c in a.checks if c.canon == "ul_rssi_dbm")
    assert inter.worst == -104 and inter.sev == 0          # TDD: -104 is inside -100
    assert a.classification == NO_ISSUE


# --------------------------------------------------------------------------- #
# user location: the ticket's site first, the best server only without one
# --------------------------------------------------------------------------- #
OTHER = (30.503, 47.80)          # a closer site to the north


def _sectors() -> pd.DataFrame:
    rows = []
    for site, (la, lo) in (("BAS0001", SITE), ("BAS0002", OTHER), ("BAS0003", (30.49, 47.81))):
        for s, az in ((1, 0.0), (2, 120.0), (3, 240.0)):
            rows.append({"sector_id": f"{site}-S{s}", "site_id": site, "latitude": la,
                         "longitude": lo, "azimuth_deg": az})
    return pd.DataFrame(rows)


@pytest.mark.parametrize("value, want", [("BAS0001", "BAS0001"), (" bas0001 ", "BAS0001"),
                                         ("0", ""), ("", ""), (None, ""), ("nan", ""),
                                         (0, ""), ("N/A", "")])
def test_the_ticket_site_id_is_used_as_it_is_or_names_none(value, want):
    assert RL.ticket_site(value) == want


def test_a_valid_site_id_serves_its_sector_facing_the_user_not_the_best_server():
    frames = [_lte()]
    site_tracks, sec_tracks = build_tracks(frames), RL.sector_tracks(frames)
    # 250 m north of BAS0001: BAS0002 (84 m away, azimuth 240/120) scores better
    # as a best server, but the ticket names BAS0001
    lat, lon = _at(0, 250)
    best = RL.best_server(lat, lon, _sectors())
    assert best.site_id != "BAS0001"
    r = RL.reanalyse(lat, lon, PT, _sectors(), sec_tracks, site_tracks, 2.0,
                     site_id="BAS0001", site_sectors=_sectors())
    assert r.how == RL.TICKET and r.sector_id == "BAS0001-S1"
    assert "best server" not in r.description


@pytest.mark.parametrize("site_id", ["0", "", None])
def test_without_a_site_id_the_best_server_equation_picks_the_serving_sector(site_id):
    frames = [_lte()]
    lat, lon = _at(120, 300)
    r = RL.reanalyse(lat, lon, PT, _sectors(), RL.sector_tracks(frames), build_tracks(frames),
                     2.0, site_id=site_id)
    assert r.how == RL.BEST
    assert r.server == RL.best_server(lat, lon, _sectors())
    assert "the ticket names no site: the best server" in r.description


def test_the_serving_sector_is_analysed_on_every_one_of_its_cells():
    frames = [_lte()]
    lat, lon = _at(120, 300)
    r = RL.reanalyse(lat, lon, PT, _sectors(), RL.sector_tracks(frames), build_tracks(frames),
                     2.0, site_id="BAS0001")
    assert r.sector_id == "BAS0001-S2" and r.analysis.classification == TECHNICAL
    prb = next(c for c in r.analysis.checks if c.canon == "dl_prb_util")
    assert prb.cells == ["L_Alpha_BAS0001-2"]      # its own cells, no other sector's


def test_the_neighbour_sectors_facing_the_user_each_on_all_of_its_cells():
    frames = [_lte(), _lte(site="BAS0002", prb_c2=lambda h: 30)]
    lat, lon = _at(120, 300)
    r = RL.reanalyse(lat, lon, PT, _sectors(), RL.sector_tracks(frames), build_tracks(frames),
                     2.0, site_id="BAS0001")
    sites = [nb.server.site_id for nb in r.neighbours]
    assert "BAS0001" not in sites and len(set(sites)) == len(sites)   # one per other site
    assert all(nb.server.az_diff_deg <= RL.FACING_DEG for nb in r.neighbours)
    nb2 = next(nb for nb in r.neighbours if nb.server.site_id == "BAS0002")
    assert nb2.analysis.classification == NO_ISSUE
    prb = next(c for c in nb2.analysis.checks if c.canon == "dl_prb_util")
    assert prb.cells == [f"L_Alpha_BAS0002-{int(nb2.sector_id[-1])}"]  # all of its cells
    # the neighbours never change the serving sector's verdict
    assert r.analysis.classification == TECHNICAL


USER = (30.60, 47.90)


def _site_at(site, bearing_from_user, metres, facing_user=True, azimuth=None):
    """One sector of `site`, `metres` from USER in `bearing_from_user`, pointed
    back at the user (or at `azimuth`)."""
    la, lo = _at(bearing_from_user, metres, USER)
    az = (bearing_from_user + 180.0) % 360.0 if facing_user else azimuth
    return {"sector_id": f"{site}-S1", "site_id": site, "latitude": la, "longitude": lo,
            "azimuth_deg": az}


def _pick(rows, serving="SRV0000", server=None):
    return [nb.sector_id for nb in RL.neighbour_sectors(*USER, pd.DataFrame(rows), serving,
                                                         server)]


def test_of_two_sectors_facing_the_user_from_one_direction_only_the_nearest():
    # A 100 m and B 300 m north of the user, both facing it: A only
    rows = [_site_at("AAA0001", 0, 100), _site_at("BBB0001", 0, 300)]
    assert _pick(rows) == ["AAA0001-S1"]
    # a few degrees apart is still the same direction
    rows = [_site_at("AAA0001", 0, 100), _site_at("BBB0001", 12, 300)]
    assert _pick(rows) == ["AAA0001-S1"]


def test_every_direction_around_the_user_keeps_its_nearest_facing_sector():
    rows = [_site_at("NNN0001", 0, 150), _site_at("NNN0002", 5, 600),      # north
            _site_at("EEE0001", 90, 400), _site_at("EEE0002", 95, 900),    # east
            _site_at("SSS0001", 180, 700),                                 # south
            _site_at("WWW0001", 270, 250), _site_at("WWW0002", 265, 1200)]  # west
    assert _pick(rows) == ["NNN0001-S1", "WWW0001-S1", "EEE0001-S1", "SSS0001-S1"]
    # no fixed count: two directions give two, one gives one
    assert len(_pick(rows[:1] + rows[5:6])) == 2
    assert len(_pick(rows[:2])) == 1


def test_a_sector_not_facing_the_user_is_not_a_neighbour_and_hides_nothing():
    # the nearer north site points away from the user: it is not a neighbour,
    # and the farther north site facing the user is kept
    rows = [_site_at("AWY0001", 0, 100, facing_user=False, azimuth=0.0),
            _site_at("BBB0001", 0, 300)]
    assert _pick(rows) == ["BBB0001-S1"]
    # the site's sector that faces the user is the one taken
    la, lo = _at(90, 200, USER)
    rows = [{"sector_id": f"TRI0001-S{i}", "site_id": "TRI0001", "latitude": la,
             "longitude": lo, "azimuth_deg": az} for i, az in ((1, 0.0), (2, 120.0), (3, 240.0))]
    assert _pick(rows) == ["TRI0001-S3"]


def test_a_site_behind_the_serving_sector_is_not_a_neighbour_nor_a_far_one():
    serving = RL.Server("SRV0000-S1", "SRV0000", 200.0, 180.0, 180.0, 0.0)  # 200 m north
    rows = [_site_at("BEH0001", 3, 500),            # behind the serving site
            _site_at("SSS0001", 180, 300),          # the south side: kept
            _site_at("FAR0001", 90, RL.RELEVANT_M + 500)]
    assert _pick(rows, server=serving) == ["SSS0001-S1"]
    # the serving site's own sectors are never neighbours
    rows = [_site_at("SRV0000", 90, 100)]
    assert _pick(rows, server=serving) == []


def _grid(points):
    """A coverage file: 50 m lattice points (lat, lon, rsrp, mr)."""
    la, lo, rs, mr = zip(*points)
    return SimpleNamespace(lat=np.array(la), lon=np.array(lo), rsrp=np.array(rs),
                           mr=np.array(mr, dtype=float))


def _lattice_around(lat, lon, rsrp):
    step = 50 / 111_320.0
    pts = []
    for i in range(-3, 4):
        for j in range(-3, 4):
            pts.append((lat + i * step, lon + j * step, rsrp, 10))
    return [_grid(pts)]


def test_rsrp_comes_from_the_grid_cell_at_the_user_and_poor_or_none_is_coverage():
    frames = [_lte(prb_c2=lambda h: 30)]
    lat, lon = _at(120, 300)
    args = (lat, lon, PT, _sectors(), RL.sector_tracks(frames), build_tracks(frames), 2.0)
    # good coverage: the KPI analysis decides
    r = RL.reanalyse(*args, grids=_lattice_around(lat, lon, -90.0), site_id="BAS0001")
    assert r.rsrp == pytest.approx(-90.0) and not r.coverage_issue
    assert r.analysis.classification == NO_ISSUE
    # poor coverage: a Coverage Issue
    r = RL.reanalyse(*args, grids=_lattice_around(lat, lon, -112.0), site_id="BAS0001")
    assert r.coverage_issue and r.analysis.classification == TECHNICAL
    assert r.analysis.problem_type == "Coverage"
    # a grid loaded, none under the point: Poor Coverage / Coverage Issue
    far = _lattice_around(lat + 0.05, lon, -80.0)
    r = RL.reanalyse(*args, grids=far, site_id="BAS0001")
    assert r.no_grid and r.coverage_issue and r.rsrp is None
    assert r.analysis.problem_type == "Coverage"
    assert "no RSRP grid at the user location" in r.description
    # the RSRP is the grid's, whatever sector serves: same point, other serving site
    r2 = RL.reanalyse(*args, grids=_lattice_around(lat, lon, -90.0), site_id="BAS0003",
                      site_sectors=_sectors())
    assert r2.sector_id.startswith("BAS0003") and r2.rsrp == pytest.approx(-90.0)


# --------------------------------------------------------------------------- #
# Sleep tickets
# --------------------------------------------------------------------------- #
def test_the_sleep_reason_agrees_or_the_comment_wins():
    # all three agree
    assert A.reason_of("Data - High utilization cells", "PRB Utilization",
                       "high PRB on the sector") == ("utilization", "Diagnostic Comment")
    # the comment names nothing: RF Analysis, then the closure code
    assert A.reason_of("Data - High utilization cells", "Interference", "") == (
        "interference", "RF Analysis")
    assert A.reason_of("Data - High flow control sites", "", "") == (
        "flow_control", "Closure Code")
    # a conflict: the comment wins
    check, why = A.reason_of("Data - High utilization cells", "PRB Utilization",
                             "high interference seen on the site")
    assert (check, why) == ("interference", "Diagnostic Comment")


@pytest.mark.parametrize("comment", ["Not technical — customer device issue",
                                     "non technical, billing", "not a technical issue"])
def test_a_comment_that_says_not_technical_is_not_technical(comment):
    assert A.comment_check(comment) == "not_technical"
    check = A.check_of("Data - High utilization cells", "PRB Utilization", comment)
    assert check == "not_technical"
    verdict, text = A.judge(check, "BAS0001-1", None, None, comment=comment)
    assert verdict == A.NOT_TECHNICAL and "No KPI check is run" in text
    assert A.issue_hours(check, None) != A.issue_hours(check, None)   # NaN: no hours


@pytest.mark.parametrize("comment", ["DT done, weak signal", "drive test shows low RSRP"])
def test_a_drive_test_is_a_coverage_case(comment):
    assert A.check_of("Data - High utilization cells", "PRB Utilization", comment) == "coverage"


def test_a_planned_ticket_is_coverage_unless_its_comment_or_rf_says_load():
    code = "Planned - This area needs a new site/tower"
    assert A.check_of(code, "New Site Required", "") == "coverage"
    assert A.check_of(code, "PRB Utilization", "") == "utilization"
    assert A.check_of(code, "New Site Required", "high load, capacity needed") == "utilization"


def test_the_coverage_codes_are_decided_by_rsrp_first():
    for code in ("Not Within Plan", "Indoor Issue - Urban areas",
                 "Data - Slow Weak Coverage"):
        assert A.check_of(code, "", "") == "coverage"
    good, weak = A.Point(-95.0, 100, 5.0), A.Point(-108.0, 100, 5.0)
    kw = dict(located=True, coverage=True)
    assert A.judge("coverage", "BAS0001-1", None, good, **kw)[0] == A.SOLVE
    assert A.judge("coverage", "BAS0001-1", None, weak, **kw)[0] == A.NOT_SOLVE
    # a location the grid does not cover: poor coverage
    assert A.judge("coverage", "BAS0001-1", None, None, **kw)[0] == A.NOT_SOLVE
    # no location: RSRP is ignored
    assert A.judge("coverage", "BAS0001-1", None, good)[0] == A.NOT_CHECKED


def test_issue_hours_count_the_hours_over_the_line_across_the_full_period():
    t = pd.date_range("2026-09-10 00:00", periods=72, freq="h")
    ev = A.Evidence(site="BAS0001", sector=1.0, hours=72, start=t[0], end=t[-1])
    bad = frozenset(t[[5, 30, 31, 70]])
    ev.stats[A.PRB] = A.Stat(top=95.0, mean=40.0, over=4, worst=95.0, hours_set=bad)
    assert A.issue_hours("utilization", ev) == 4
    flow = (3, 150_000.0, 20_000.0, frozenset(t[:3]))
    assert A.issue_hours("flow_control", None, flow) == 3
    rtwp = (2, -85.0, -100.0, frozenset(t[[5, 6]]))
    ev.stats[A.RSSI] = A.Stat(top=-100.0, mean=-110.0, over=1, worst=-100.0,
                              hours_set=frozenset(t[[5]]))
    # interference: the union of the 4G and 3G issue hours
    assert A.issue_hours("interference", ev, rtwp=rtwp) == 2


def test_site_flow_control_and_rtwp_are_judged_per_site_hour():
    t = pd.date_range("2026-09-10 00:00", periods=4, freq="h")
    kpi = pd.DataFrame({"datetime": t.repeat(2), "object": ["U_A-1", "U_A-2"] * 4,
                        "flow": [60_000, 60_000, 10, 10, 40_000, 40_000, 0, 0],
                        "rtwp": [-95, -85, -100, -100, -95, -95, -100, -100]})
    sites = pd.Series("BAS0001", index=kpi.index)
    n, worst, _, hours = A.site_flow_control(kpi, "flow", sites)["BAS0001"]
    assert (n, worst) == (1, 120_000) and hours == frozenset([t[0]])
    n, worst, _, hours = A.site_rtwp(kpi, "rtwp", sites)["BAS0001"]
    assert (n, worst) == (1, -85) and hours == frozenset([t[0]])
