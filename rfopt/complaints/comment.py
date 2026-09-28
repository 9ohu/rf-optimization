"""The ticket-ready Comment of a Daily Target ticket (Sites map, Analysis Result).

Written from the analysis alone — the lead KPI check, the problem type, the
resolution read over the hourly KPI data, the serving sector, its distance and
the RSRP at the user location — in the R5 team's fixed wording:

    <fixed prefix of the lead KPI>
    Root cause: <problem type>
    issue description: customer’s serving site sector <sector> with distance
    <n>m, and the RSRP was <dBm>, User Location: <lat, lon>. Since it was
    suffering from <low/high issue>.
    <site issue status>

The fixed prefixes, word for word:

    4G interference      External interference
    3G RTWP              External interference   (root cause: 3G RTWP interference)
    High flow control    Temporary Capacity Limitation
    High PRB             sector Expansion Needed
    Availability         service Recovered       (only once recovered; still down:
                                                  no prefix)
    No network issue     No Network Issue Detected

A KPI without a fixed prefix (a coverage issue) starts at its Root cause. A
value the data does not carry reads N/A; a root cause the analysis could not
determine is left out, never guessed.
"""

from __future__ import annotations

import math

from rfopt.complaints.correlate import INSUFFICIENT, NO_ISSUE, NOT_RESOLVED, RESOLVED
from rfopt.complaints.relocate import lead_check

NA = "N/A"

PREFIX = {
    "ul_rssi_dbm": "External interference",
    "ul_rtwp_dbm": "External interference",          # 3G RTWP: the same as 4G interference
    "dl_flowctrl_drops": "Temporary Capacity Limitation",
    "dl_prb_util": "sector Expansion Needed",
    "ul_prb_util": "sector Expansion Needed",
    "cell_avail_pct": "service Recovered",
}
NO_ISSUE_PREFIX = "No Network Issue Detected"
NO_ISSUE_ADVICE = ("All technical checks were normal with no faults dedicated, please advise "
                   "the customer to check their device at the nearest serving service center.")
SOLVED = "The site issue solved and it is normal new"

# "Since it was suffering from ..." — the KPI condition, low or high
SUFFERING = {
    "dl_prb_util": "high PRB utilization",
    "ul_prb_util": "high UL PRB utilization",
    "ul_rssi_dbm": "high 4G interference",
    "ul_rtwp_dbm": "high RTWP (3G interference)",
    "dl_flowctrl_drops": "high flow control",
    "cell_avail_pct": "low availability",
}
# "... the site suffering from <issue> for XX hours"
STATUS_ISSUE = {
    "dl_prb_util": "High utilization",
    "ul_prb_util": "High utilization",
    "ul_rssi_dbm": "4G interferences",
    "ul_rtwp_dbm": "3G interferences",
    "dl_flowctrl_drops": "High flow control",
    "cell_avail_pct": "low availability",
}
COVERAGE = "Coverage"
# the root cause of a 3G RTWP issue names the 3G problem, never 4G
ROOT_CAUSE = {"ul_rtwp_dbm": "3G RTWP interference"}


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _distance(m) -> str:
    m = _num(m)
    return f"{m:.0f}m" if m is not None else NA


def _rsrp(v) -> str:
    v = _num(v)
    return f"{v:.0f}" if v is not None else NA


def _location(lat, lon) -> str:
    la, lo = _num(lat), _num(lon)
    return f"{la:.5f}, {lo:.5f}" if la is not None and lo is not None else NA


def ticket_comment(analysis, *, sector: str = "", distance_m=None, rsrp=None,
                   lat=None, lon=None, coverage_issue: bool = False) -> str:
    """The Comment, lines joined by newlines. `sector`: the serving sector (the
    ticket's site without a user location); `coverage_issue`: poor or no RSRP
    grid at the user location (`relocate.Relocated.coverage_issue`)."""
    where = (f"customer’s serving site sector {sector or NA} with distance "
             f"{_distance(distance_m)}, and the RSRP was {_rsrp(rsrp)}, "
             f"User Location: {_location(lat, lon)}.")
    if analysis.classification == NO_ISSUE and not coverage_issue:
        return "\n".join([NO_ISSUE_PREFIX, f"issue description: {where}", NO_ISSUE_ADVICE])

    lead = lead_check(analysis)
    lines: list[str] = []
    # "service Recovered" only once the availability has recovered (Resolved,
    # normal to the end of the data); still down: no prefix
    if lead is not None and lead.canon in PREFIX and not (
            lead.canon == "cell_avail_pct" and lead.resolution != RESOLVED):
        lines.append(PREFIX[lead.canon])
    if coverage_issue:
        root = COVERAGE
    elif analysis.classification == INSUFFICIENT:
        root = ""
    else:
        root = ROOT_CAUSE.get(lead.canon if lead is not None else "", analysis.problem_type)
    if root:
        lines.append(f"Root cause: {root}")

    issues = []
    if coverage_issue:
        issues.append("low RSRP (weak coverage)" if _num(rsrp) is not None
                      else "no RSRP coverage at the user location")
    if lead is not None:
        issues.append(SUFFERING.get(lead.canon, f"{lead.label} issue"))
    since = f" Since it was suffering from {' and '.join(issues)}." if issues else ""
    lines.append(f"issue description: {where}{since}")

    if lead is not None:
        if lead.resolution == NOT_RESOLVED:
            lines.append("The site issue still exists and not solved yet, the site suffering "
                         f"from {STATUS_ISSUE.get(lead.canon, lead.label)} for "
                         f"{lead.issue_hours} hours")
        elif lead.resolution == RESOLVED:
            lines.append(SOLVED)
    return "\n".join(lines)


__all__ = ["NO_ISSUE_ADVICE", "NO_ISSUE_PREFIX", "PREFIX", "SOLVED", "ticket_comment"]
