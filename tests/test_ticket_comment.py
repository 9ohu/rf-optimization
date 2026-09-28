"""The Sites map's ticket-ready Comment: the R5 team's fixed wording, filled
from the analysis only."""

import pytest

from rfopt.complaints import comment as CM
from rfopt.complaints.correlate import (INSUFFICIENT, NO_ISSUE, NOT_RESOLVED, RESOLVED,
                                        TECHNICAL, UNKNOWN, KpiCheck, TicketAnalysis)


def _check(canon, *, sev=2, resolution=NOT_RESOLVED, issue_hours=7, label="KPI", breach=3):
    c = KpiCheck(label, "", canon, "Issue > 80% per hour", "4G")
    c.sev, c.status, c.breach_hours = sev, "Critical" if sev == 2 else "OK", breach
    c.resolution, c.issue_hours = resolution, issue_hours
    return c


def _analysis(cls, ptype="", checks=()):
    return TicketAnalysis(cls, "Yes" if cls == TECHNICAL else "No", ptype, "", "", "", "",
                          "", False, "", "", list(checks))


WHERE = dict(sector="BAS3315-1", distance_m=100.4, rsrp=-98.2, lat=30.512345, lon=47.81)


@pytest.mark.parametrize("canon, prefix, ptype, since, status", [
    ("ul_rssi_dbm", "External interference", "Interference", "high 4G interference",
     "4G interferences"),
    ("dl_flowctrl_drops", "Temporary Capacity Limitation", "Flow control", "high flow control",
     "High flow control"),
    ("dl_prb_util", "sector Expansion Needed", "Congestion", "high PRB utilization",
     "High utilization"),
])
def test_each_kpi_opens_with_its_fixed_prefix_word_for_word(canon, prefix, ptype, since,
                                                            status):
    a = _analysis(TECHNICAL, ptype, [_check(canon, issue_hours=12)])
    lines = CM.ticket_comment(a, **WHERE).split("\n")
    assert lines[0] == prefix                               # exactly, first
    assert lines[1] == f"Root cause: {ptype}"
    assert lines[2] == ("issue description: customer’s serving site sector BAS3315-1 with "
                        "distance 100m, and the RSRP was -98, User Location: 30.51234, "
                        f"47.81000. Since it was suffering from {since}.")
    assert lines[3] == ("The site issue still exists and not solved yet, the site suffering "
                        f"from {status} for 12 hours")
    assert len(lines) == 4


def test_a_solved_issue_reads_the_fixed_sentence():
    a = _analysis(TECHNICAL, "Congestion", [_check("dl_prb_util", resolution=RESOLVED)])
    assert CM.ticket_comment(a, **WHERE).split("\n")[-1] == \
        "The site issue solved and it is normal new"


def test_no_status_line_when_the_resolution_is_unknown():
    a = _analysis(TECHNICAL, "Congestion", [_check("dl_prb_util", resolution=UNKNOWN)])
    lines = CM.ticket_comment(a, **WHERE).split("\n")
    assert len(lines) == 3 and lines[-1].startswith("issue description:")


def test_no_network_issue():
    a = _analysis(NO_ISSUE, "", [_check("dl_prb_util", sev=0, resolution="")])
    assert CM.ticket_comment(a, **WHERE).split("\n") == [
        "No Network Issue Detected",
        "issue description: customer’s serving site sector BAS3315-1 with distance 100m, "
        "and the RSRP was -98, User Location: 30.51234, 47.81000.",
        "All technical checks were normal with no faults dedicated, please advise the "
        "customer to check their device at the nearest serving service center."]


def test_values_the_data_does_not_carry_read_na_never_invented():
    a = _analysis(TECHNICAL, "Congestion", [_check("dl_prb_util")])
    text = CM.ticket_comment(a, sector="BAS0001")         # no user location
    assert ("customer’s serving site sector BAS0001 with distance N/A, and the RSRP was "
            "N/A, User Location: N/A.") in text
    # the analysis could not decide: no prefix, no root cause
    ins = _analysis(INSUFFICIENT, "")
    text = CM.ticket_comment(ins, **WHERE)
    assert "Root cause" not in text and text.startswith("issue description:")
    for prefix in CM.PREFIX.values():
        assert not text.startswith(prefix)


def test_3g_rtwp_follows_the_4g_interference_comment_as_a_3g_problem():
    a = _analysis(TECHNICAL, "Interference", [_check("ul_rtwp_dbm", issue_hours=9)])
    lines = CM.ticket_comment(a, **WHERE).split("\n")
    assert lines == [
        "External interference",
        "Root cause: 3G RTWP interference",
        "issue description: customer’s serving site sector BAS3315-1 with distance 100m, "
        "and the RSRP was -98, User Location: 30.51234, 47.81000. Since it was suffering "
        "from high RTWP (3G interference).",
        "The site issue still exists and not solved yet, the site suffering from "
        "3G interferences for 9 hours"]
    assert "4G" not in "\n".join(lines)
    solved = _analysis(TECHNICAL, "Interference",
                       [_check("ul_rtwp_dbm", resolution=RESOLVED)])
    lines = CM.ticket_comment(solved, **WHERE).split("\n")
    assert lines[0] == "External interference" and lines[-1] == CM.SOLVED
    # 4G interference keeps its own root cause
    four = _analysis(TECHNICAL, "Interference", [_check("ul_rssi_dbm")])
    assert CM.ticket_comment(four, **WHERE).split("\n")[1] == "Root cause: Interference"


def test_service_recovered_only_once_the_availability_has_recovered():
    up = _analysis(TECHNICAL, "Availability", [_check("cell_avail_pct", resolution=RESOLVED)])
    lines = CM.ticket_comment(up, **WHERE).split("\n")
    assert lines[0] == "service Recovered" and lines[1] == "Root cause: Availability"
    assert lines[2].endswith("Since it was suffering from low availability.")
    assert lines[3] == CM.SOLVED
    # still down: no "service Recovered", the unresolved status
    down = _analysis(TECHNICAL, "Availability",
                     [_check("cell_avail_pct", resolution=NOT_RESOLVED, issue_hours=5)])
    lines = CM.ticket_comment(down, **WHERE).split("\n")
    assert "service Recovered" not in lines
    assert lines[0] == "Root cause: Availability"
    assert lines[1].endswith("Since it was suffering from low availability.")
    assert lines[2] == ("The site issue still exists and not solved yet, the site suffering "
                        "from low availability for 5 hours")
    # not known to have recovered (no data after the window): no prefix either
    unknown = _analysis(TECHNICAL, "Availability",
                        [_check("cell_avail_pct", resolution=UNKNOWN)])
    assert not CM.ticket_comment(unknown, **WHERE).startswith("service Recovered")


def test_poor_coverage_at_the_user_location_is_the_root_cause():
    a = _analysis(TECHNICAL, "Coverage")
    lines = CM.ticket_comment(a, **{**WHERE, "rsrp": -112.0}, coverage_issue=True).split("\n")
    assert lines[0] == "Root cause: Coverage"
    assert lines[1].endswith("Since it was suffering from low RSRP (weak coverage).")
    nogrid = CM.ticket_comment(a, **{**WHERE, "rsrp": None}, coverage_issue=True)
    assert "the RSRP was N/A" in nogrid and "no RSRP coverage at the user location" in nogrid
    # coverage with a KPI issue too: the KPI's prefix and status, coverage the root cause
    both = _analysis(TECHNICAL, "Coverage", [_check("dl_prb_util", issue_hours=4)])
    lines = CM.ticket_comment(both, **{**WHERE, "rsrp": -112.0},
                              coverage_issue=True).split("\n")
    assert lines[0] == "sector Expansion Needed" and lines[1] == "Root cause: Coverage"
    assert "low RSRP (weak coverage) and high PRB utilization" in lines[2]
    assert lines[3].endswith("High utilization for 4 hours")


def test_issue_hours_count_every_hour_from_the_window_to_the_end_of_the_data():
    """KpiCheck.issue_hours: the window's issue hours and every issue hour after
    it, each hour judged on its own."""
    import io

    import pandas as pd

    from rfopt.complaints.correlate import analyse_ticket, build_tracks, judged_kpis
    from rfopt.ingest.hourly_kpi import load_hourly_raw

    prb = "HW_DL PRB Avg Utilization(%)"
    rows = [f"Time,eNodeB Name,Cell FDD TDD Indication,Cell Name,LocalCell Id,{prb}"]
    for h in pd.date_range("2026-09-13 08:00", "2026-09-13 23:00", freq="h"):
        bad = 15 <= h.hour <= 17 or h.hour in (20, 22, 23)
        rows.append(f"{h:%Y-%m-%d %H:%M},A_BAS0001,CELL_FDD,L_A_BAS0001-1,1,{95 if bad else 30}")
    raw = load_hourly_raw(io.BytesIO(("\n".join(rows) + "\n").encode()), [prb])
    tracks = build_tracks([("4G", "k.csv", raw, judged_kpis(list(raw.columns), "4G"))])
    a = analyse_ticket("BAS0001", pd.Timestamp("2026-09-13 16:20"), tracks, 2.0)
    c = next(c for c in a.checks if c.canon == "dl_prb_util")
    assert c.breach_hours == 3 and c.issue_hours == 6 and c.resolution == NOT_RESOLVED
    text = CM.ticket_comment(a, sector="BAS0001")
    assert text.endswith("the site suffering from High utilization for 6 hours")
