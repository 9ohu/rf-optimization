"""Recommended Action Engine.

Turns a diagnosis + a cell's geometry/KPI context into a concrete, *computed*
recommendation:

    problem -> root cause -> evidence -> action -> parameter -> target value
            -> expected impact -> risks -> KPIs to monitor -> confidence

Nothing here is a fixed lookup: tilt/power/azimuth targets are derived from
distance, antenna height, current tilt, beamwidth, the KPI gap and the
inter-site distance. Every recommendation carries the numbers it used.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from rfopt.actions.geometry import (
    DEFAULT_VBW, AntennaGeometry, angular_offset_deg, bearing_deg,
    coverage_edge_distance_m, elevation_angle_to_point_deg, haversine_m,
    optimal_downtilt_deg, tilt_gain_delta_db,
)
from rfopt.actions.propagation import (
    Environment, cost231_hata_pathloss_db, inter_site_distance_hint_m,
    predict_rsrp_dbm, rs_power_delta_for_target_db,
)

MAX_TILT_STEP_DEG = 3.0        # RF optimisation is iterative - cap one step
RET_RANGE_DEG = (0.0, 10.0)   # typical RET electrical range; override per antenna


# --------------------------------------------------------------------------- #
@dataclass
class CellContext:
    cell_id: str
    site_id: str = ""
    technology: str = "LTE"
    band: str = ""
    # ---- geometry (from a site database) ----
    latitude: float | None = None
    longitude: float | None = None
    antenna_height_m: float = 30.0
    azimuth_deg: float = 0.0
    mech_tilt_deg: float = 0.0
    elec_tilt_deg: float = 3.0
    vbw_deg: float = DEFAULT_VBW
    hbw_deg: float = 65.0
    ret_unit: str = "deg"          # "deg" | "tenths" (RET value in 0.1 deg steps)
    ret_range_deg: tuple[float, float] = RET_RANGE_DEG
    # ---- environment ----
    env: Environment = field(default_factory=Environment)
    inter_site_distance_m: float | None = None
    # ---- current KPIs (aggregated) ----
    kpis: dict[str, float] = field(default_factory=dict)
    # ---- neighbour / capacity context ----
    has_cosite_capacity_layer: bool = False
    sibling_load: dict[str, float] = field(default_factory=dict)   # cell->prb_util
    worst_neighbor: str | None = None

    # -- helpers ------------------------------------------------------------
    @property
    def total_tilt_deg(self) -> float:
        return round(self.mech_tilt_deg + self.elec_tilt_deg, 1)

    def geometry(self) -> AntennaGeometry:
        return AntennaGeometry(self.antenna_height_m, self.azimuth_deg,
                               self.mech_tilt_deg, self.elec_tilt_deg,
                               self.vbw_deg)

    def isd(self) -> float:
        return (self.inter_site_distance_m
                or inter_site_distance_hint_m(self.env.kind))

    def k(self, name: str, default: float | None = None) -> float | None:
        v = self.kpis.get(name)
        return v if v is not None and v == v else default

    def ret_display(self, elec_deg: float) -> str:
        if self.ret_unit == "tenths":
            return f"{round(elec_deg * 10)} (= {elec_deg:.1f} deg)"
        return f"{elec_deg:.1f} deg"


@dataclass
class ComplaintContext:
    cell: CellContext
    complaint_lat: float | None = None
    complaint_lon: float | None = None
    distance_m: float | None = None
    bearing_deg_from_site: float | None = None
    indoor: bool = False
    measured_rsrp_dbm: float | None = None
    measured_rsrq_db: float | None = None
    measured_sinr_db: float | None = None

    def resolve(self) -> tuple[float, float]:
        """Return (distance_m, angular_offset_from_azimuth_deg)."""
        c = self.cell
        dist = self.distance_m
        brg = self.bearing_deg_from_site
        if dist is None and None not in (c.latitude, c.longitude,
                                         self.complaint_lat, self.complaint_lon):
            dist = haversine_m(c.latitude, c.longitude,
                               self.complaint_lat, self.complaint_lon)
            brg = bearing_deg(c.latitude, c.longitude,
                              self.complaint_lat, self.complaint_lon)
        dist = dist or 1000.0
        off = (angular_offset_deg(c.azimuth_deg, brg)
               if brg is not None else 0.0)
        return dist, off


# --------------------------------------------------------------------------- #
@dataclass
class Recommendation:
    cell_id: str
    site_id: str
    problem: str
    category: str
    root_cause: str
    evidence: list[str]
    action_type: str
    parameter: str
    current_value: str
    recommended_value: str
    rationale: str
    expected_impact: str
    risks: list[str]
    monitor_kpis: list[str]
    confidence: float                 # 0..1
    priority: str = "P3"
    math_notes: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["confidence"] = round(self.confidence, 2)
        return d

    def as_text(self) -> str:
        L = [
            f"Problem:  {self.problem}",
            f"Category: {self.category}   Priority: {self.priority}   "
            f"Confidence: {self.confidence:.0%}",
            "",
            f"Root cause:  {self.root_cause}",
            "Evidence:",
            *[f"  - {e}" for e in self.evidence],
            "",
            f"Recommended action:  {self.action_type}",
            f"  Parameter:         {self.parameter}",
            f"  Current value:     {self.current_value}",
            f"  Recommended value: {self.recommended_value}",
            f"  Rationale:         {self.rationale}",
        ]
        if self.math_notes:
            L += ["  Calculation:"] + [f"    {m}" for m in self.math_notes]
        L += [
            "",
            f"Expected impact:  {self.expected_impact}",
            "Risks / side effects:",
            *[f"  - {r}" for r in self.risks],
            "Monitor after change:  " + ", ".join(self.monitor_kpis),
        ]
        if self.alternatives:
            L += ["Alternatives:"] + [f"  - {a}" for a in self.alternatives]
        return "\n".join(L)


# --------------------------------------------------------------------------- #
def _priority(severity: str, traffic_gb: float) -> str:
    sev = (severity or "").lower()
    if sev == "critical" and traffic_gb >= 1:
        return "P1"
    if sev == "critical" or (sev == "warning" and traffic_gb >= 5):
        return "P2"
    if sev == "warning":
        return "P3"
    return "P4"


def _clamp_step(delta: float, cap: float = MAX_TILT_STEP_DEG) -> float:
    return max(-cap, min(cap, delta))


def _elec_from_total(new_total: float, mech: float,
                     rng: tuple[float, float]) -> tuple[float, bool]:
    elec = new_total - mech
    lo, hi = rng
    clamped = min(max(elec, lo), hi)
    return round(clamped, 1), (abs(clamped - elec) > 0.05)


# --------------------------------------------------------------------------- #
# Individual action builders. Each returns a Recommendation or None.
# --------------------------------------------------------------------------- #
def _rec_overshooting(ctx: CellContext, diag) -> Recommendation | None:
    g = ctx.geometry()
    isd = ctx.isd()
    cur_edge = g.coverage_edge_m()
    ta_p95 = ctx.k("ta_p95_m")
    target_edge = 1.15 * isd
    new_total = optimal_downtilt_deg(ctx.antenna_height_m, target_edge,
                                     vbw_deg=ctx.vbw_deg, mode="edge")
    delta = _clamp_step(new_total - g.total_tilt_deg)
    new_total_capped = round(g.total_tilt_deg + delta, 1)
    new_elec, maxed = _elec_from_total(new_total_capped, ctx.mech_tilt_deg,
                                       ctx.ret_range_deg)

    if delta < 0.5:
        return None

    tilt_gain_far = tilt_gain_delta_db(ctx.antenna_height_m, 2 * isd,
                                       g.total_tilt_deg, new_total_capped,
                                       vbw_deg=ctx.vbw_deg)
    ev = [f"Current total downtilt {g.total_tilt_deg:.1f} deg -> vertical 3 dB "
          f"edge reaches ~{cur_edge/1000:.1f} km, but inter-site distance is "
          f"~{isd/1000:.1f} km."]
    if ta_p95:
        ev.append(f"P95 timing-advance distance {ta_p95/1000:.1f} km "
                  f"(> {1.2*isd/1000:.1f} km) confirms UEs served well beyond "
                  f"the intended footprint.")
    for kn, lbl in (("pct_ta_gt_threshold", "% far UEs"),
                    ("inter_freq_ho_sr", "inter-freq HO SR"),
                    ("avg_rsrq_db", "avg RSRQ"), ("ul_rssi_dbm", "UL RSSI")):
        v = ctx.k(kn)
        if v is not None:
            ev.append(f"{lbl} = {v:.1f}.")
    if diag is not None:
        ev.extend(diag.evidence[:2])

    maxed_note = ""
    alts = []
    if maxed:
        maxed_note = (" RET electrical range is exhausted at "
                      f"{ctx.ret_range_deg[1]:.1f} deg; add mechanical downtilt "
                      "or review antenna type/height.")
        alts.append("Add mechanical downtilt to reach the remaining "
                    f"{new_total - new_total_capped:.1f} deg.")
    alts.append("If a neighbour does not fill the pulled-back area, add/repoint "
                "a neighbour or a coverage layer before tilting.")

    return Recommendation(
        cell_id=ctx.cell_id, site_id=ctx.site_id,
        problem="Cell overshooting - serving UEs far beyond its intended area",
        category="overshooting",
        root_cause="Insufficient downtilt for the site geometry: the vertical "
                   "beam illuminates ground well past the first ring of "
                   "neighbours, creating a far-reaching interferer and pilot "
                   "pollution.",
        evidence=ev,
        action_type="Antenna tilt optimisation (RET downtilt)",
        parameter="Electrical antenna tilt (RET) / eNodeB total downtilt",
        current_value=f"total {g.total_tilt_deg:.1f} deg "
                      f"(mech {ctx.mech_tilt_deg:.1f} + elec {ctx.elec_tilt_deg:.1f})",
        recommended_value=f"total ~{new_total_capped:.1f} deg  "
                          f"(set RET to {ctx.ret_display(new_elec)}), "
                          f"step +{delta:.1f} deg this iteration"
                          + maxed_note,
        rationale=f"To pull the 3 dB coverage edge back to ~{target_edge/1000:.1f} "
                  f"km (1.15 x ISD), the required total downtilt is "
                  f"atan({ctx.antenna_height_m:.0f}/{target_edge:.0f}) + "
                  f"{ctx.vbw_deg/2:.1f} = {new_total:.1f} deg. Applied in a "
                  f"capped {delta:.1f} deg step.",
        expected_impact=f"~{abs(tilt_gain_far):.1f} dB less energy toward far "
                        "cells: higher SINR/RSRQ and CQI on this cell and its "
                        "victims, fewer overshoot handovers, lower UL "
                        "interference. Cell-edge RSRP inside the footprint "
                        "changes little.",
        risks=["Coverage holes at the true cell edge if neighbours do not "
               "overlap - verify with the neighbour footprint / drive test.",
               "Possible short-term rise in HO activity while the dominance "
               "map settles.",
               "Over-tilting raises near-in interference and can lift PRB "
               "utilisation on this cell."],
        monitor_kpis=["avg_rsrq_db", "avg_sinr_db", "avg_cqi", "erab_drop_rate",
                      "pct_rsrp_poor", "ho_sr", "ta_p95_m", "dl_user_thr_mbps"],
        confidence=_confidence(ctx, ["ta_p95_m", "avg_rsrq_db",
                                     "pct_ta_gt_threshold"], base=0.55,
                               need_geometry=True),
        priority=_priority(getattr(diag, "severity", "warning"),
                           ctx.k("total_traffic_gb", 0.0)),
        math_notes=[
            f"h={ctx.antenna_height_m:.1f} m, VBW={ctx.vbw_deg:.1f} deg, "
            f"ISD={isd:.0f} m",
            f"current 3 dB edge = h / tan(theta - VBW/2) = {cur_edge:.0f} m",
            f"target edge = 1.15 x ISD = {target_edge:.0f} m",
            f"theta_opt = {new_total:.1f} deg ; capped step -> "
            f"{new_total_capped:.1f} deg ; RET elec -> {new_elec:.1f} deg",
        ],
        alternatives=alts,
    )


def _rec_poor_coverage(ctx: CellContext, diag) -> Recommendation | None:
    g = ctx.geometry()
    isd = ctx.isd()
    rsrp = ctx.k("avg_rsrp_dbm")
    pct_poor = ctx.k("pct_rsrp_poor")
    prb = ctx.k("dl_prb_util", 0.0)
    cur_edge = g.coverage_edge_m()
    target_edge = 0.95 * isd

    # under-coverage -> reduce downtilt toward the cell edge
    new_total = optimal_downtilt_deg(ctx.antenna_height_m, target_edge,
                                     vbw_deg=ctx.vbw_deg, mode="edge")
    delta = _clamp_step(new_total - g.total_tilt_deg)
    uptilt = delta < -0.3
    new_total_capped = round(g.total_tilt_deg + delta, 1)
    new_elec, at_min = _elec_from_total(new_total_capped, ctx.mech_tilt_deg,
                                        ctx.ret_range_deg)

    # RS-power leg: how much gap remains after the tilt change
    tgt_rsrp = -105.0
    tilt_gain = tilt_gain_delta_db(ctx.antenna_height_m, target_edge,
                                   g.total_tilt_deg, new_total_capped,
                                   vbw_deg=ctx.vbw_deg) if uptilt else 0.0
    pwr = rs_power_delta_for_target_db(rsrp if rsrp is not None else -108.0,
                                       tgt_rsrp, ctx.env, tilt_gain_db=tilt_gain)

    ev = []
    if rsrp is not None:
        ev.append(f"Average RSRP {rsrp:.1f} dBm (target >= {tgt_rsrp:.0f}).")
    if pct_poor is not None:
        ev.append(f"{pct_poor:.0f}% of MR samples below -110 dBm.")
    for kn, lbl in (("dl_user_thr_mbps", "DL user throughput"),
                    ("avg_sinr_db", "avg SINR"), ("dl_qpsk_ratio", "DL QPSK %"),
                    ("erab_drop_rate", "E-RAB drop %")):
        v = ctx.k(kn)
        if v is not None:
            ev.append(f"{lbl} = {v:.2f}.")
    ev.append(f"Current 3 dB coverage edge ~{cur_edge/1000:.1f} km vs ISD "
              f"~{isd/1000:.1f} km.")
    if diag is not None:
        ev.extend(diag.evidence[:2])

    action_bits, params, cur_vals, rec_vals, notes, alts = [], [], [], [], [], []
    if uptilt and not at_min:
        action_bits.append("reduce downtilt (uptilt)")
        params.append("Electrical antenna tilt (RET)")
        cur_vals.append(f"elec {ctx.elec_tilt_deg:.1f} deg "
                        f"(total {g.total_tilt_deg:.1f})")
        rec_vals.append(f"RET {ctx.ret_display(new_elec)} "
                        f"(total ~{new_total_capped:.1f} deg, {delta:+.1f} step)")
        notes.append(f"theta for edge at 0.95 x ISD = "
                     f"atan({ctx.antenna_height_m:.0f}/{target_edge:.0f}) + "
                     f"{ctx.vbw_deg/2:.1f} = {new_total:.1f} deg")
    if pwr["applied_db"] > 0:
        action_bits.append(f"raise RS power by {pwr['applied_db']:.1f} dB")
        params.append("Reference signal power (RS EPRE)")
        cur_vals.append(f"{ctx.env.rs_epre_dbm:.1f} dBm/RE")
        rec_vals.append(f"{ctx.env.rs_epre_dbm + pwr['applied_db']:.1f} dBm/RE "
                        f"(+{pwr['applied_db']:.1f} dB)")
        notes.append(f"RSRP gap after tilt = {pwr['needed_db']:.1f} dB; "
                     f"applied {pwr['applied_db']:.1f} dB within "
                     f"{ctx.env.rs_power_up_headroom_db:.1f} dB head-room")
    if not action_bits:
        return None
    if pwr["limited"]:
        alts.append("RS power / tilt cannot fully close the gap - evaluate a "
                    "new site, a low-band coverage layer, or repeater/DAS for "
                    "the weak area.")
    if prb >= 70:
        alts.append(f"PRB utilisation is {prb:.0f}% - uptilting will add load; "
                    "pair with a capacity action or offload first.")

    if uptilt and not at_min:
        root = ("Under-coverage from excessive downtilt: the vertical beam is "
                "steered too far in, so the intended cell edge falls below "
                "usable RSRP and UEs there run at low CQI and drop.")
    elif pwr["applied_db"] > 0 and pwr["limited"]:
        root = ("Coverage-limited by site spacing / path loss: tilt is already "
                "reasonable but the link budget does not reach the edge - "
                "RS power helps only partially and a coverage layer or new "
                "site is likely needed.")
    else:
        root = ("Insufficient radiated reference-signal power for the served "
                "area: the cell edge sits below usable RSRP and UEs there run "
                "at low CQI and drop.")

    return Recommendation(
        cell_id=ctx.cell_id, site_id=ctx.site_id,
        problem="Poor coverage / weak RSRP over the served area",
        category="coverage",
        root_cause=root,
        evidence=ev,
        action_type="Coverage optimisation: " + " + ".join(action_bits),
        parameter=" ; ".join(params),
        current_value=" ; ".join(cur_vals),
        recommended_value=" ; ".join(rec_vals),
        rationale="Lift RSRP at the intended cell edge to the target while "
                  "keeping the footprint within one ISD, so gains do not spill "
                  "into neighbours as interference.",
        expected_impact="Higher RSRP/SINR and CQI at the edge, higher cell-edge "
                        "and average DL throughput, fewer edge drops and "
                        "setup failures, better handover into this cell.",
        risks=["More overlap with neighbours -> potential RSRQ/interference "
               "rise; check MOD3/PCI and neighbour tilts.",
               "Uptilt lengthens the overshoot tail - watch P95 TA and far-cell "
               "HO.",
               "RS-power increase reduces PDSCH power-sharing head-room; verify "
               "Pa/Pb and cell-edge throughput do not regress."],
        monitor_kpis=["avg_rsrp_dbm", "pct_rsrp_poor", "avg_sinr_db", "avg_cqi",
                      "dl_user_thr_mbps", "erab_drop_rate", "rrc_setup_sr",
                      "ta_p95_m"],
        confidence=_confidence(ctx, ["avg_rsrp_dbm", "pct_rsrp_poor",
                                     "dl_user_thr_mbps"], base=0.5,
                               need_geometry=True),
        priority=_priority(getattr(diag, "severity", "warning"),
                           ctx.k("total_traffic_gb", 0.0)),
        math_notes=notes,
        alternatives=alts,
    )


def _rec_interference(ctx: CellContext, diag) -> Recommendation | None:
    ul_rssi = ctx.k("ul_rssi_dbm")
    rsrq = ctx.k("avg_rsrq_db")
    sinr = ctx.k("avg_sinr_db")
    rsrp = ctx.k("avg_rsrp_dbm")
    qpsk = ctx.k("dl_qpsk_ratio")
    g = ctx.geometry()

    ul_driven = ul_rssi is not None and ul_rssi > -108
    dl_overlap = (rsrp is not None and rsrp > -100
                  and rsrq is not None and rsrq < -13)

    ev = []
    for kn, lbl in (("avg_rsrq_db", "avg RSRQ"), ("avg_sinr_db", "avg SINR"),
                    ("ul_rssi_dbm", "UL RSSI/PRB"), ("avg_rsrp_dbm", "avg RSRP"),
                    ("dl_qpsk_ratio", "DL QPSK %"),
                    ("pct_prb_high_intf", "% high-intf PRB")):
        v = ctx.k(kn)
        if v is not None:
            ev.append(f"{lbl} = {v:.1f}.")
    if diag is not None:
        ev.extend(diag.evidence[:2])

    if ul_driven and not dl_overlap:
        rise = ul_rssi - (-115.0)
        return Recommendation(
            cell_id=ctx.cell_id, site_id=ctx.site_id,
            problem="High uplink interference / noise rise",
            category="interference",
            root_cause=f"UL RSSI/PRB at {ul_rssi:.1f} dBm is ~{rise:.0f} dB "
                       "above the thermal floor. Consistent with external "
                       "interference (repeater/jammer/other operator), PIM, or "
                       "a faulty RX path / VSWR - not a parameter issue.",
            evidence=ev,
            action_type="Interference mitigation (hardware + hunting)",
            parameter="RX path integrity / external interference source "
                      "(also review PUSCH power control p0NominalPusch, alpha)",
            current_value=f"UL RSSI {ul_rssi:.1f} dBm/PRB",
            recommended_value="Restore to <= -112 dBm/PRB: VSWR/return-loss "
                              "check on all RX branches, PIM sweep, inspect "
                              "connectors/jumpers; if clean, spectrum-analyser "
                              "hunt and escalate to the interference desk. As "
                              "interim mitigation raise p0NominalPusch by "
                              "1-2 dB and alpha toward 1.0.",
            rationale="A 6-15 dB noise rise across PRBs is an RF-environment or "
                      "hardware fault; tilt/power will not fix it and PUSCH "
                      "power-control only trades coverage for a small SINR gain.",
            expected_impact="Lower UL RSSI, higher UL SINR and throughput, "
                            "fewer UL-driven drops and setup failures, better "
                            "VoLTE MOS.",
            risks=["Raising p0NominalPusch increases UE Tx power and can hurt "
                   "battery and far-UE coverage - interim only.",
                   "If the source is another sector's PIM, the fix may be on a "
                   "different cell."],
            monitor_kpis=["ul_rssi_dbm", "avg_sinr_db", "ul_user_thr_mbps",
                          "erab_drop_rate", "rrc_setup_sr"],
            confidence=_confidence(ctx, ["ul_rssi_dbm", "avg_sinr_db"], base=0.6),
            priority=_priority(getattr(diag, "severity", "warning"),
                               ctx.k("total_traffic_gb", 0.0)),
            alternatives=["Enable/verify UL CoMP or IRC receiver settings.",
                          "If time-correlated with a neighbour, check that "
                          "cell for PIM/overshoot."],
        )

    # DL overlap-driven interference -> reduce overlap
    isd = ctx.isd()
    new_total = optimal_downtilt_deg(ctx.antenna_height_m, 1.05 * isd,
                                     vbw_deg=ctx.vbw_deg, mode="edge")
    delta = _clamp_step(new_total - g.total_tilt_deg)
    new_total_capped = round(g.total_tilt_deg + delta, 1)
    new_elec, maxed = _elec_from_total(new_total_capped, ctx.mech_tilt_deg,
                                       ctx.ret_range_deg)
    do_tilt = delta >= 0.5
    return Recommendation(
        cell_id=ctx.cell_id, site_id=ctx.site_id,
        problem="Poor RSRQ / low SINR from downlink pilot pollution (overlap)",
        category="interference",
        root_cause="RSRP is adequate but RSRQ/SINR are low: too many strong "
                   "co-channel servers in the same area (no clear dominance). "
                   "Driven by overshoot / excessive overlap from this cell "
                   "and/or first-tier neighbours" +
                   (f", worst pair {ctx.worst_neighbor}" if ctx.worst_neighbor
                    else "") + ".",
        evidence=ev,
        action_type=("Interference mitigation: reduce overlap "
                     + ("(downtilt) + " if do_tilt else "")
                     + "dominance / neighbour audit"),
        parameter=("Electrical tilt (RET) of this cell and the overshooting "
                   "neighbour; PCI/MOD3 plan; CIO of the worst neighbour pair; "
                   "RS power of the dominant offender"),
        current_value=(f"total tilt {g.total_tilt_deg:.1f} deg"
                       + (f", elec {ctx.elec_tilt_deg:.1f}" if do_tilt else "")),
        recommended_value=(
            (f"downtilt to total ~{new_total_capped:.1f} deg "
             f"(RET {ctx.ret_display(new_elec)}, {delta:+.1f} step); "
             if do_tilt else "")
            + "verify no MOD3/PCI collision among the top 3 servers; if a "
            "specific neighbour dominates, apply CIO -2 to -3 dB or downtilt "
            "that neighbour; enable/confirm frequency-domain ICIC."),
        rationale="Restoring a single dominant server in each area lifts RSRQ "
                  "(RSRQ ~ RSRP/RSSI) and SINR directly; MOD3 separation "
                  "protects RS from collision.",
        expected_impact="Higher RSRQ and SINR, higher CQI and 64/256QAM share, "
                        "higher DL throughput, fewer drops and RLF, smoother "
                        "handovers.",
        risks=["Downtilt or CIO changes can create edge coverage holes - keep "
               "within one ISD and check neighbour fill-in.",
               "PCI re-plan is service-affecting - schedule in a maintenance "
               "window.",
               "ICIC trades some peak-rate PRBs for cell-edge SINR."],
        monitor_kpis=["avg_rsrq_db", "avg_sinr_db", "avg_cqi", "dl_user_thr_mbps",
                      "erab_drop_rate", "ho_sr", "pct_rsrp_poor"],
        confidence=_confidence(ctx, ["avg_rsrq_db", "avg_sinr_db",
                                     "avg_rsrp_dbm"], base=0.5),
        priority=_priority(getattr(diag, "severity", "warning"),
                           ctx.k("total_traffic_gb", 0.0)),
        alternatives=["If overlap is structural (too many sites), consider a "
                      "permanent PCI/frequency re-farm or sector-power plan."],
    )


def _rec_congestion(ctx: CellContext, diag) -> Recommendation | None:
    prb = ctx.k("dl_prb_util")
    users = ctx.k("rrc_conn_users_avg")
    rej = ctx.k("ue_rejected_rrc", 0.0)
    thr = ctx.k("dl_user_thr_mbps")
    ev = []
    for kn, lbl in (("dl_prb_util", "DL PRB util"), ("ul_prb_util", "UL PRB util"),
                    ("pdcch_util", "PDCCH util"),
                    ("rrc_conn_users_avg", "avg conn users"),
                    ("ue_rejected_rrc", "RRC rejects"),
                    ("dl_user_thr_mbps", "DL user thr")):
        v = ctx.k(kn)
        if v is not None:
            ev.append(f"{lbl} = {v:.1f}.")
    if diag is not None:
        ev.extend(diag.evidence[:2])

    over = max((prb or 0) - 70, 0)
    offload_gb = None
    if ctx.k("total_traffic_gb") and prb:
        offload_gb = ctx.k("total_traffic_gb") * (over / prb)

    can_lb = ctx.has_cosite_capacity_layer or any(
        v < 45 for v in ctx.sibling_load.values()) if ctx.sibling_load else \
        ctx.has_cosite_capacity_layer

    if can_lb:
        action = "Load balancing to a co-sited carrier"
        param = ("Idle-mode: cellReselPriority / qOffsetFreq on the target "
                 "layer; Connected-mode: inter-frequency A5 thresholds "
                 "(interFreqHoA5Thd1Rsrp/Thd2Rsrp) and MLB "
                 "(loadBalancingSwitch, interFreqMlbThd, mlbUeNumThd)")
        rec_val = (f"Move ~{over:.0f} pp of PRB load "
                   + (f"(~{offload_gb:.1f} GB/day) " if offload_gb else "")
                   + "to the capacity layer: raise its reselection priority by "
                   "+2, set qOffsetFreq -4 dB toward it, enable MLB with "
                   "interFreqMlbThd at ~65% and step the A5 Thd2 up by 2 dB "
                   "until this cell settles < 70% at busy hour.")
        alts = ["If sibling layers are also loaded, this is a real capacity "
                "shortfall - go to the capacity action."]
        conf_base = 0.55
    else:
        action = "Capacity expansion"
        param = ("Carrier / sector configuration (add carrier, enable CA, "
                 "sector split, or spectrum re-farm); interim: "
                 "cellDlMaxTxPower share, DlPfSchStrategy, connectedUserNumber "
                 "licence")
        rec_val = (f"Busy-hour PRB ~{prb:.0f}% with "
                   f"{('rejections ' + format(rej, '.0f') + '/h') if rej else 'no headroom'}"
                   f"; add a carrier or enable carrier aggregation on this "
                   f"sector. Interim: confirm all licensed PRBs/CCE and "
                   f"connected-user licence are unlocked and proportional-fair "
                   f"scheduling is active.")
        alts = ["Small-cell offload or sector split if a new macro carrier is "
                "not available.",
                "Traffic-shaping / QCI policy as a stop-gap only."]
        conf_base = 0.6

    return Recommendation(
        cell_id=ctx.cell_id, site_id=ctx.site_id,
        problem="High utilisation / congestion at busy hour",
        category="congestion",
        root_cause="Demand exceeds the served PRB/CCE/connected-user budget at "
                   "busy hour; the scheduler shares fewer PRBs per UE, so "
                   "throughput falls and setup/HO requests are rejected or "
                   "delayed.",
        evidence=ev,
        action_type=action,
        parameter=param,
        current_value=(f"DL PRB {prb:.0f}%" if prb else "n/a")
                      + (f", {users:.0f} users" if users else "")
                      + (f", {rej:.0f} rejects/h" if rej else ""),
        recommended_value=rec_val,
        rationale="Bring busy-hour PRB below ~70% so the scheduler has room; "
                  "prefer moving traffic to existing spectrum before adding "
                  "hardware.",
        expected_impact="Lower PRB/CCE utilisation, higher per-user throughput, "
                        "fewer RRC rejects and setup failures, lower latency, "
                        "fewer congestion-driven drops.",
        risks=["Aggressive offload can push the target layer into congestion or "
               "hand users to a worse-coverage layer - step gradually and "
               "watch its KPIs.",
               "Reselection/HO parameter changes affect the whole cell, not "
               "just heavy users.",
               "MLB ping-pong if thresholds are too tight."],
        monitor_kpis=["dl_prb_util", "ul_prb_util", "pdcch_util",
                      "rrc_conn_users_avg", "ue_rejected_rrc",
                      "dl_user_thr_mbps", "erab_drop_rate", "ho_sr"],
        confidence=_confidence(ctx, ["dl_prb_util", "rrc_conn_users_avg"],
                               base=conf_base),
        priority=_priority(getattr(diag, "severity", "warning"),
                           ctx.k("total_traffic_gb", 0.0)),
        alternatives=alts,
    )


def _rec_low_throughput(ctx: CellContext, diag) -> Recommendation | None:
    thr = ctx.k("dl_user_thr_mbps")
    cqi = ctx.k("avg_cqi")
    prb = ctx.k("dl_prb_util", 0.0)
    sinr = ctx.k("avg_sinr_db")
    if thr is None:
        return None
    rf_limited = (cqi is not None and cqi < 7) or (sinr is not None and sinr < 3)
    cap_limited = prb >= 75

    ev = [f"DL user throughput {thr:.2f} Mbps."]
    for kn, lbl in (("avg_cqi", "avg CQI"), ("avg_sinr_db", "avg SINR"),
                    ("dl_prb_util", "DL PRB util"),
                    ("dl_256qam_ratio", "256QAM %"),
                    ("dl_spectral_eff", "DL spec. eff.")):
        v = ctx.k(kn)
        if v is not None:
            ev.append(f"{lbl} = {v:.2f}.")
    if diag is not None:
        ev.extend(diag.evidence[:2])

    if cap_limited and not rf_limited:
        return None  # handled by congestion
    if rf_limited:
        root = ("RF-limited: low CQI/SINR forces low-order MCS and many "
                "retransmissions, so even with spare PRBs the bit-rate is low. "
                "Root RF cause is coverage or interference (see the linked "
                "diagnosis).")
        action = "Fix the underlying RF (coverage / interference), then re-check"
        param = ("Follow the coverage or interference recommendation for this "
                 "cell (tilt / RS power / overlap). Also verify TM3/TM4 with "
                 "open-loop + closed-loop MIMO and 256QAM are enabled.")
        rec_val = ("Target avg CQI >= 8 and SINR >= 5 dB via the RF action; "
                   "confirm dlHarqMaxTxNum, TM switching and 256QAM/CA feature "
                   "activation.")
        conf = 0.45
    else:
        root = ("Feature/config-limited: RF and load are fine but the cell is "
                "not exploiting available capacity (MIMO layers, CA, 256QAM, "
                "power sharing, or transport).")
        action = "Parameter / feature optimisation"
        param = ("Carrier aggregation (SCell config), transmissionMode (TM4), "
                 "256QAM DL, Pa/Pb power-sharing, dlSchStrategy, "
                 "transport/S1 dimensioning")
        rec_val = ("Enable 2CC+ CA on this sector, set TM4, enable DL 256QAM, "
                   "set Pb per antenna config (e.g. Pb=1 for 2-Tx), and verify "
                   "S1/transport is not the ceiling (compare cell vs user "
                   "throughput).")
        conf = 0.4

    return Recommendation(
        cell_id=ctx.cell_id, site_id=ctx.site_id,
        problem="Low user throughput",
        category="throughput",
        root_cause=root,
        evidence=ev,
        action_type=action,
        parameter=param,
        current_value=f"DL user thr {thr:.2f} Mbps"
                      + (f", CQI {cqi:.1f}" if cqi is not None else ""),
        recommended_value=rec_val,
        rationale="Separate RF-limited from capacity-limited from "
                  "feature-limited before acting; only the matching lever moves "
                  "throughput.",
        expected_impact="Higher DL/UL user throughput and spectral efficiency; "
                        "if RF-limited, also better retainability and "
                        "accessibility.",
        risks=["CA/256QAM gains depend on good SINR - limited benefit at cell "
               "edge.",
               "Power-sharing (Pb) changes shift energy between RS and PDSCH - "
               "validate cell-edge vs peak trade-off."],
        monitor_kpis=["dl_user_thr_mbps", "ul_user_thr_mbps", "avg_cqi",
                      "avg_sinr_db", "dl_256qam_ratio", "dl_prb_util"],
        confidence=_confidence(ctx, ["dl_user_thr_mbps", "avg_cqi"], base=conf),
        priority=_priority(getattr(diag, "severity", "warning"),
                           ctx.k("total_traffic_gb", 0.0)),
    )


def _rec_accessibility(ctx: CellContext, diag) -> Recommendation | None:
    rrc = ctx.k("rrc_setup_sr")
    erab = ctx.k("erab_setup_sr")
    prb = ctx.k("dl_prb_util", 0.0)
    cce = ctx.k("pdcch_util", 0.0)
    rej = ctx.k("ue_rejected_rrc", 0.0)
    rsrp = ctx.k("avg_rsrp_dbm")
    ul_rssi = ctx.k("ul_rssi_dbm")
    rach = ctx.k("rach_setup_sr")

    congestion = prb >= 75 or cce >= 75 or rej > 20
    coverage = (rsrp is not None and rsrp < -108)
    ul_intf = ul_rssi is not None and ul_rssi > -108

    ev = []
    for kn, lbl in (("rrc_setup_sr", "RRC SR"), ("erab_setup_sr", "E-RAB SR"),
                    ("rach_setup_sr", "RACH SR"), ("pdcch_util", "PDCCH util"),
                    ("dl_prb_util", "PRB util"), ("ue_rejected_rrc", "RRC rej/h"),
                    ("avg_rsrp_dbm", "avg RSRP"), ("ul_rssi_dbm", "UL RSSI")):
        v = ctx.k(kn)
        if v is not None:
            ev.append(f"{lbl} = {v:.1f}.")
    if diag is not None:
        ev.extend(diag.evidence[:2])

    if congestion:
        cause = ("Admission/resource congestion: setup requests are rejected or "
                 "time out because PRB/CCE/connected-user resources are "
                 "exhausted at busy hour.")
        param = ("pdcchSymNumSwitch (adaptive PDCCH), maxRrcConnUeNum / "
                 "connected-user licence, RAC thresholds, "
                 "interFreqLoadBalanceSwitch")
        rec = ("Enable adaptive PDCCH symbol number, confirm the connected-user "
               "licence ceiling is above busy-hour demand, enable inter-freq "
               "load balancing, and pursue the capacity action.")
        mons = ["rrc_setup_sr", "erab_setup_sr", "pdcch_util", "dl_prb_util",
                "ue_rejected_rrc"]
        conf = 0.55
    elif ul_intf and (rach is None or rach < 92):
        cause = ("UL interference is corrupting Msg1/Msg3: preambles and "
                 "RRC/E-RAB setup messages fail to decode.")
        param = ("Follow the UL interference recommendation; interim: "
                 "preambleInitRcvTargetPower +2 dB, powerRampingStep 4 dB, "
                 "check PRACH configIndex / high-speed flag / rootSequenceIndex "
                 "collisions with neighbours")
        rec = ("Clear the UL interference (hardware/hunting). Interim: raise "
               "preamble target power by 2 dB, verify PRACH root-sequence and "
               "configIndex do not collide with first-tier neighbours.")
        mons = ["rach_setup_sr", "rrc_setup_sr", "ul_rssi_dbm", "erab_setup_sr"]
        conf = 0.5
    elif coverage:
        cause = ("Coverage-limited access: UEs attempt setup at cell edge with "
                 "RSRP/SINR too low to complete the RRC/E-RAB signalling.")
        param = ("Follow the coverage recommendation (tilt / RS power); review "
                 "RACH: preambleInitRcvTargetPower, contentionResolutionTimer, "
                 " raResponseWindowSize; qRxLevMin admission floor")
        rec = ("Apply the coverage action for this cell. Widen "
               "raResponseWindowSize and contentionResolutionTimer by one step "
               "for the edge population; keep qRxLevMin realistic.")
        mons = ["avg_rsrp_dbm", "rrc_setup_sr", "erab_setup_sr", "rach_setup_sr",
                "pct_rsrp_poor"]
        conf = 0.5
    else:
        cause = ("Signalling/transport or core-side: RRC/S1 setup fails without "
                 "a matching RF or load signal - likely S1/transport, MME pool, "
                 "or a licence/parameter fault.")
        param = ("S1 link / transport health, MME load-balancing weights, "
                 "eNodeB licence state; check emergency / access-class barring "
                 "(acBarringInfo) is not left enabled")
        rec = ("Check S1-MME link and transport KPIs, confirm no access-class "
               "barring is active, and audit recent parameter/licence changes "
               "on this eNodeB.")
        mons = ["rrc_setup_sr", "erab_setup_sr", "s1_ho_sr", "cell_avail_pct"]
        conf = 0.4

    return Recommendation(
        cell_id=ctx.cell_id, site_id=ctx.site_id,
        problem="Accessibility degraded (RRC / E-RAB setup success low)",
        category="accessibility",
        root_cause=cause,
        evidence=ev,
        action_type="Accessibility optimisation",
        parameter=param,
        current_value=(f"RRC SR {rrc:.2f}%" if rrc is not None else "n/a")
                      + (f", E-RAB SR {erab:.2f}%" if erab is not None else ""),
        recommended_value=rec,
        rationale="Match the fix to the failure stage (Msg1 vs RRC vs E-RAB vs "
                  "S1) and its cause (congestion / UL interference / coverage / "
                  "transport).",
        expected_impact="Higher RRC and E-RAB setup success, higher CSSR, fewer "
                        "customer 'no service / call not connecting' complaints.",
        risks=["Raising preamble power adds UL interference to neighbours.",
               "Relaxing admission thresholds can convert blocking into drops "
               "if the cell is truly capacity-short."],
        monitor_kpis=mons,
        confidence=_confidence(ctx, ["rrc_setup_sr", "erab_setup_sr"], base=conf),
        priority=_priority(getattr(diag, "severity", "warning"),
                           ctx.k("total_traffic_gb", 0.0)),
    )


def _rec_retainability(ctx: CellContext, diag) -> Recommendation | None:
    drop = ctx.k("erab_drop_rate")
    rsrp = ctx.k("avg_rsrp_dbm")
    sinr = ctx.k("avg_sinr_db")
    ul_rssi = ctx.k("ul_rssi_dbm")
    ho = ctx.k("ho_sr")
    prb = ctx.k("dl_prb_util", 0.0)
    ta_p95 = ctx.k("ta_p95_m")

    ev = []
    for kn, lbl in (("erab_drop_rate", "E-RAB drop %"), ("avg_rsrp_dbm", "RSRP"),
                    ("avg_sinr_db", "SINR"), ("ul_rssi_dbm", "UL RSSI"),
                    ("ho_sr", "HO SR"), ("dl_prb_util", "PRB util"),
                    ("ta_p95_m", "P95 TA m")):
        v = ctx.k(kn)
        if v is not None:
            ev.append(f"{lbl} = {v:.1f}.")
    if diag is not None:
        ev.extend(diag.evidence[:2])

    edge = (rsrp is not None and rsrp < -108) or (sinr is not None and sinr < 2)
    late_ho = (ho is not None and ho < 95) or (ta_p95 and ta_p95 > 1.3 * ctx.isd())
    ul_intf = ul_rssi is not None and ul_rssi > -107
    cong = prb >= 80

    if ul_intf:
        cause = "UL-interference-driven RLF: UEs lose the uplink before HO."
        param = ("Clear UL interference (hardware/hunting); tune RLF timers "
                 "t310/n310/n311 and UL power control (p0NominalPusch, alpha) "
                 "only as interim")
        rec = ("Pursue the UL interference fix. Interim: p0NominalPusch +2 dB, "
               "alpha -> 1.0, and relax t310 by one step so borderline UEs "
               "recover instead of dropping.")
        conf = 0.5
    elif late_ho or edge:
        cause = ("Mobility/coverage-driven drops: UEs reach the cell edge with "
                 "low RSRP/SINR before a handover completes - late trigger, a "
                 "missing neighbour, or an over-extended footprint.")
        param = ("intraFreqHoA3Offset, timeToTrigger, "
                 "interFreqHoA5Thd1Rsrp/Thd2Rsrp; add missing neighbour "
                 "relations (ANR); coverage/overshoot tilt action")
        rec = (f"Reduce intraFreqHoA3Offset by 1-2 dB and timeToTrigger by one "
               f"step so HO fires ~2-3 dB earlier; run an ANR/neighbour audit "
               f"for this cell (esp. worst pair "
               f"{ctx.worst_neighbor or 'top drop direction'}); if the cell "
               f"overshoots, apply the downtilt action so the edge sits inside "
               f"neighbour overlap.")
        conf = 0.5
    elif cong:
        cause = ("Congestion-driven drops: at busy hour the scheduler/HARQ "
                 "cannot sustain radio bearers, and admission pressure delays "
                 "handovers.")
        param = ("Capacity / load-balancing action; DRB QCI table priorities; "
                 "pre-emption settings for GBR bearers")
        rec = ("Apply the congestion/load-balancing action for this cell; "
               "verify GBR (VoLTE QCI1) pre-emption and ARP are correctly set "
               "so voice bearers are protected under load.")
        conf = 0.5
    else:
        cause = ("No single dominant RF/mobility/load driver - review release "
                 "cause distribution (MME vs radio vs handover) and transport "
                 "stability; possible S1/X2 or hardware intermittency.")
        param = ("Release-cause breakdown (L.E-RAB.AbnormRel.* counters), X2/S1 "
                 "link stability, board/RF alarm history")
        rec = ("Break down abnormal releases by cause. If radio-dominant, "
               "revisit coverage/interference; if handover-dominant, audit "
               "neighbours; if MME/transport-dominant, escalate to core/IP.")
        conf = 0.35

    return Recommendation(
        cell_id=ctx.cell_id, site_id=ctx.site_id,
        problem="Retainability degraded (E-RAB / call drop rate high)",
        category="retainability",
        root_cause=cause,
        evidence=ev,
        action_type="Retainability optimisation",
        parameter=param,
        current_value=(f"E-RAB drop {drop:.2f}%" if drop is not None else "n/a"),
        recommended_value=rec,
        rationale="Drops follow the weakest link: uplink, mobility/coverage, or "
                  "load. Fix that link rather than blanket-relaxing RLF timers.",
        expected_impact="Lower E-RAB/context drop rate, better VoLTE "
                        "retainability and MOS, fewer dropped-call complaints.",
        risks=["Earlier HO triggers raise HO attempts and ping-pong - watch "
               "ho_sr and ho_ping_pong_rate.",
               "Relaxing RLF timers can keep UEs on a dying link longer, "
               "hurting throughput briefly."],
        monitor_kpis=["erab_drop_rate", "ctxt_drop_rate", "ho_sr",
                      "ho_ping_pong_rate", "avg_sinr_db", "ul_rssi_dbm",
                      "avg_rsrp_dbm"],
        confidence=_confidence(ctx, ["erab_drop_rate", "avg_sinr_db"], base=conf),
        priority=_priority(getattr(diag, "severity", "warning"),
                           ctx.k("total_traffic_gb", 0.0)),
    )


def _rec_handover(ctx: CellContext, diag) -> Recommendation | None:
    ho = ctx.k("ho_sr")
    intra = ctx.k("intra_freq_ho_sr")
    inter = ctx.k("inter_freq_ho_sr")
    pp = ctx.k("ho_ping_pong_rate")
    ev = []
    for kn, lbl in (("ho_sr", "HO SR"), ("intra_freq_ho_sr", "intra-f HO SR"),
                    ("inter_freq_ho_sr", "inter-f HO SR"),
                    ("x2_ho_sr", "X2 HO SR"), ("s1_ho_sr", "S1 HO SR"),
                    ("ho_ping_pong_rate", "ping-pong %")):
        v = ctx.k(kn)
        if v is not None:
            ev.append(f"{lbl} = {v:.1f}.")
    if diag is not None:
        ev.extend(diag.evidence[:2])

    ping_pong = pp is not None and pp > 5
    if ping_pong:
        cause = ("Ping-pong / unstable handovers: hysteresis and time-to-trigger "
                 "are too aggressive for the overlap, so UEs bounce between "
                 "servers.")
        param = "hysteresis, timeToTrigger, cellIndividualOffset (worst pair)"
        rec = ("Increase timeToTrigger by one step (e.g. 320 -> 480 ms) and "
               "hysteresis by 0.5-1 dB; for the specific ping-pong pair apply a "
               "small CIO to make one cell clearly dominant in the overlap.")
        mons = ["ho_ping_pong_rate", "ho_sr", "erab_drop_rate", "dl_user_thr_mbps"]
        conf = 0.5
    else:
        prep_issue = ((ctx.k("x2_ho_sr") or 100) < 95 or
                      (ctx.k("s1_ho_sr") or 100) < 95)
        cause = ("Handover execution failures: UEs lose the source before the "
                 "target confirms - late trigger, weak target, or a missing / "
                 "mis-defined neighbour relation."
                 if not prep_issue else
                 "Handover preparation failures: X2/S1 signalling to the target "
                 "fails - missing X2 link, wrong TAC/PCI in the neighbour "
                 "relation, or target admission control rejecting.")
        param = ("ANR / neighbour relation table, X2 link setup, "
                 "intraFreqHoA3Offset, timeToTrigger"
                 if prep_issue else
                 "intraFreqHoA3Offset, timeToTrigger, "
                 "interFreqHoA5Thd1/Thd2Rsrp, neighbour completeness (ANR)")
        rec = ("Audit the neighbour list vs actual dominance (ANR on, remove "
               "stale externals, add missing); ensure X2 is established to all "
               "first-tier eNodeBs."
               if prep_issue else
               "Trigger HO ~2-3 dB earlier: intraFreqHoA3Offset -1 to -2 dB, "
               "timeToTrigger one step shorter; add any missing neighbour in "
               "the dominant drop direction; for inter-freq, raise A5 Thd1 "
               "(serving) by 2 dB so UEs leave earlier.")
        mons = ["ho_sr", "intra_freq_ho_sr", "inter_freq_ho_sr", "x2_ho_sr",
                "erab_drop_rate", "ho_ping_pong_rate"]
        conf = 0.45

    return Recommendation(
        cell_id=ctx.cell_id, site_id=ctx.site_id,
        problem="Handover performance degraded",
        category="handover",
        root_cause=cause,
        evidence=ev,
        action_type="Neighbour / mobility optimisation",
        parameter=param,
        current_value=(f"HO SR {ho:.2f}%" if ho is not None else "n/a")
                      + (f", ping-pong {pp:.1f}%" if pp is not None else ""),
        recommended_value=rec,
        rationale="Separate preparation (signalling/neighbour) from execution "
                  "(radio timing) failures; ping-pong needs the opposite of a "
                  "late-HO fix.",
        expected_impact="Higher HO success, fewer HO-related drops, smoother "
                        "mobility and steadier throughput at cell boundaries.",
        risks=["Earlier/looser triggers increase HO attempts and signalling "
               "load.",
               "CIO changes shift traffic and load between the pair of cells.",
               "Removing a 'stale' neighbour that is actually used causes "
               "drops - validate against handover statistics first."],
        monitor_kpis=mons,
        confidence=_confidence(ctx, ["ho_sr", "ho_ping_pong_rate"], base=conf),
        priority=_priority(getattr(diag, "severity", "warning"),
                           ctx.k("total_traffic_gb", 0.0)),
    )


def _rec_availability(ctx: CellContext, diag) -> Recommendation | None:
    av = ctx.k("cell_avail_pct")
    un = ctx.k("cell_unavail_min")
    ev = []
    if av is not None:
        ev.append(f"Cell availability {av:.2f}%.")
    if un is not None:
        ev.append(f"Unavailable ~{un:.0f} min in the period.")
    if diag is not None:
        ev.extend(diag.evidence[:2])
    return Recommendation(
        cell_id=ctx.cell_id, site_id=ctx.site_id,
        problem="Cell availability below target",
        category="availability",
        root_cause="Not an RF-parameter problem: the cell is administratively "
                   "or operationally down for part of the period - hardware "
                   "(board/RF/antenna line), transmission, power/battery, a "
                   "stuck cell-outage, or an energy-saving / lock left applied.",
        evidence=ev,
        action_type="Fault management (escalate - not RF tuning)",
        parameter="Alarm history, board/RRU/RF-line status, VSWR, DC power, "
                  "transmission link, cell admin state, energy-saving schedule",
        current_value=(f"availability {av:.2f}%" if av is not None else "n/a"),
        recommended_value="Raise a trouble ticket: check active/history alarms "
                          "on the eNodeB, VSWR/return-loss on the affected "
                          "sector, DC power and battery, transmission, and "
                          "confirm the cell is not left in energy-saving or "
                          "locked state. Clear any 'cell unavailable' and "
                          "re-block/unblock if stuck.",
        rationale="Availability gaps are downtime, not degradation - RF "
                  "parameter changes cannot recover a cell that is not on air.",
        expected_impact="Availability restored to >= 99.9%; the traffic and KPI "
                        "loss during downtime is recovered.",
        risks=["A site visit / reset is service-affecting during the work "
               "window.",
               "If it is a recurring hardware intermittency, a permanent fix "
               "(board swap) may be needed."],
        monitor_kpis=["cell_avail_pct", "cell_unavail_min", "rrc_setup_sr",
                      "total_traffic_gb"],
        confidence=0.7 if (av is not None) else 0.4,
        priority=_priority(getattr(diag, "severity", "critical"),
                           ctx.k("total_traffic_gb", 0.0)),
    )


def _rec_traffic_imbalance(ctx: CellContext, diag) -> Recommendation | None:
    if not ctx.sibling_load:
        return None
    me = ctx.k("dl_prb_util")
    loads = {**ctx.sibling_load}
    if me is not None:
        loads.setdefault(ctx.cell_id, me)
    if len(loads) < 2 or me is None:
        return None
    others = [v for c, v in loads.items() if c != ctx.cell_id]
    if not others:
        return None
    lightest = min(others)
    gap = me - lightest
    if gap < 20:
        return None
    move_pp = gap / 2.0
    cio = -round(min(6.0, max(2.0, gap / 10.0)))

    return Recommendation(
        cell_id=ctx.cell_id, site_id=ctx.site_id,
        problem="Traffic / load imbalance between co-sited cells",
        category="traffic_imbalance",
        root_cause=f"This cell carries ~{gap:.0f} pp more PRB load than the "
                   f"lightest co-sited cell ({lightest:.0f}%). Uneven "
                   "reselection/HO borders or antenna coverage let one "
                   "carrier/sector absorb disproportionate traffic while a "
                   "sibling sits under-used.",
        evidence=[f"DL PRB util this cell {me:.0f}% vs siblings "
                  + ", ".join(f"{c.split('-')[-1]}:{v:.0f}%"
                              for c, v in loads.items() if c != ctx.cell_id)
                  + ".",
                  *(diag.evidence[:2] if diag is not None else [])],
        action_type="Load balancing (mobility border tuning)",
        parameter="cellIndividualOffset / Qoffset between the pair, "
                  "interFreqMlbThd + loadBalancingSwitch, or a small "
                  "azimuth/tilt rebalance",
        current_value=f"CIO 0 dB, this cell {me:.0f}% / sibling {lightest:.0f}%",
        recommended_value=f"Apply CIO {cio} dB on this cell toward the "
                          f"under-used sibling (and/or +{abs(cio)} dB the other "
                          f"way), targeting ~{move_pp:.0f} pp of PRB load "
                          f"moved; enable MLB with interFreqMlbThd ~65%. "
                          f"Re-evaluate after 24-48 h and step further only if "
                          f"stable.",
        rationale="A few dB of offset shifts the reselection/HO border so the "
                  "loaded cell sheds edge users to the idle sibling without new "
                  "hardware.",
        expected_impact="More even PRB utilisation, higher minimum per-user "
                        "throughput across the sector, fewer congestion drops "
                        "and rejects on the hot cell.",
        risks=["Over-offset creates a coverage/quality hole at the old border - "
               "watch RSRQ/SINR and drops on both cells.",
               "If the 'idle' sibling has worse coverage, moved users may see "
               "lower throughput.",
               "Ping-pong if CIO and TTT are not consistent."],
        monitor_kpis=["dl_prb_util", "dl_user_thr_mbps", "erab_drop_rate",
                      "ho_sr", "ho_ping_pong_rate", "rrc_conn_users_avg"],
        confidence=_confidence(ctx, ["dl_prb_util"], base=0.5),
        priority=_priority(getattr(diag, "severity", "warning"),
                           ctx.k("total_traffic_gb", 0.0)),
    )


_BUILDERS = {
    "overshooting": _rec_overshooting,
    "coverage": _rec_poor_coverage,
    "poor_rsrp": _rec_poor_coverage,
    "interference": _rec_interference,
    "poor_rsrq": _rec_interference,
    "congestion": _rec_congestion,
    "high_utilization": _rec_congestion,
    "throughput": _rec_low_throughput,
    "low_throughput": _rec_low_throughput,
    "accessibility": _rec_accessibility,
    "retainability": _rec_retainability,
    "handover": _rec_handover,
    "availability": _rec_availability,
    "traffic_imbalance": _rec_traffic_imbalance,
}


# --------------------------------------------------------------------------- #
def _confidence(ctx: CellContext, key_kpis: list[str], *, base: float = 0.5,
                need_geometry: bool = False) -> float:
    score = base
    present = sum(1 for k in key_kpis if ctx.k(k) is not None)
    score += 0.08 * present
    if need_geometry:
        if ctx.latitude is not None and ctx.antenna_height_m and ctx.azimuth_deg is not None:
            score += 0.1
        else:
            score -= 0.15
    traffic = ctx.k("total_traffic_gb", 0.0) or 0.0
    if traffic >= 1:
        score += 0.05
    if (ctx.k("n_rows", 0) or 0) >= 24:
        score += 0.05
    return max(0.15, min(0.95, score))


def recommend_for_cell(ctx: CellContext, diagnoses: list) -> list[Recommendation]:
    """Build one recommendation per diagnosed problem class for a cell."""
    seen: set[str] = set()
    out: list[Recommendation] = []
    # deterministic order: worst first
    order = sorted(diagnoses,
                   key=lambda d: (-getattr(d, "severity_rank", 1),
                                  -getattr(d, "confidence", 0.0)))
    for diag in order:
        pc = getattr(diag, "problem_class", None) or getattr(diag, "category", "")
        builder = _BUILDERS.get(pc)
        if builder is None or pc in seen:
            continue
        try:
            rec = builder(ctx, diag)
        except Exception as exc:                       # never let one rule crash
            rec = None
        if rec is not None:
            out.append(rec)
            seen.add(pc)
    return out


def recommend_for_complaint(cc: ComplaintContext,
                            *, target_mode: str = "boresight") -> Recommendation:
    """Worked RET / tilt / power recommendation for coverage to one location.

    Method: the tilt that puts the most vertical-pattern energy on the target
    aligns the beam boresight with the elevation angle to the target,
    ``theta* = atan(h/d)``. We compare that to the current tilt:

      * current tilt >> theta*  -> over-tilted: reduce RET (the classic
        "RET 70 -> 40" case - the beam is aimed at the near ground and the
        complaint sits in the lower shoulder / nulls).
      * current tilt << theta*  -> the beam overshoots; a little more downtilt
        pulls the peak onto the target.
      * current tilt ~ theta*   -> tilt is already right; the gap is path loss
        -> RS power, then a coverage layer / new site / (if indoor) DAS.
    """
    ctx = cc.cell
    dist, off = cc.resolve()
    g = ctx.geometry()
    h = ctx.antenna_height_m
    vbw = ctx.vbw_deg

    elev = elevation_angle_to_point_deg(h, dist)            # theta*
    cur_tilt = g.total_tilt_deg
    cur_boresight = g.boresight_distance_m()
    margin = vbw / 2.0 if target_mode == "edge" else 0.0
    ideal_total = round(min(16.0, max(0.0, elev + margin)), 1)
    raw_delta = ideal_total - cur_tilt
    off_boresight_now = abs(elev - cur_tilt)

    env = Environment(kind=ctx.env.kind, frequency_mhz=ctx.env.frequency_mhz,
                      rs_epre_dbm=ctx.env.rs_epre_dbm,
                      indoor_loss_db=(cc.indoor and 15.0) or 0.0)
    pl = cost231_hata_pathloss_db(dist, env, bs_height_m=h)

    # is a tilt change actually worth it? judge by the pattern gain the ideal
    # alignment would deliver toward the target, not by raw angle.
    ideal_gain = tilt_gain_delta_db(h, dist, cur_tilt, ideal_total, vbw_deg=vbw)
    tilt_is_ok = ideal_gain < 2.0
    over_tilted = raw_delta < -1.0
    delta = 0.0 if tilt_is_ok else _clamp_step(raw_delta)
    new_total = round(cur_tilt + delta, 1)
    new_elec, clamped = _elec_from_total(new_total, ctx.mech_tilt_deg,
                                         ctx.ret_range_deg)
    gain = tilt_gain_delta_db(h, dist, cur_tilt, new_total, vbw_deg=vbw)

    pred_now = predict_rsrp_dbm(dist, env, bs_height_m=h, total_tilt_deg=cur_tilt,
                                az_offset_deg=off, h_bw_deg=ctx.hbw_deg,
                                vbw_deg=vbw)
    pred_new = predict_rsrp_dbm(dist, env, bs_height_m=h, total_tilt_deg=new_total,
                                az_offset_deg=off, h_bw_deg=ctx.hbw_deg,
                                vbw_deg=vbw)
    base_rsrp = (cc.measured_rsrp_dbm if cc.measured_rsrp_dbm is not None
                 else pred_now)
    pwr = rs_power_delta_for_target_db(base_rsrp, -100.0, env,
                                       tilt_gain_db=max(gain, 0.0))

    az_offset_big = off > ctx.hbw_deg / 3.0
    az_rotate = min(round(off - ctx.hbw_deg / 4.0), 30) if az_offset_big else 0

    # ---- assemble evidence --------------------------------------------
    if off_boresight_now <= vbw / 2 + 0.5:
        beam_pos = "inside the main beam"
    elif off_boresight_now <= vbw:
        beam_pos = "near the 3 dB edge of the beam"
    else:
        beam_pos = "in the lower shoulder / nulls of the beam"
    ev = [f"Distance site -> location ~{dist/1000:.2f} km; antenna height "
          f"{h:.1f} m; location ~{off:.0f} deg off the {ctx.azimuth_deg:.0f} deg "
          f"azimuth.",
          f"Elevation angle to the location is atan({h:.0f}/{dist:.0f}) = "
          f"{elev:.2f} deg. Current total downtilt {cur_tilt:.1f} deg "
          f"(mech {ctx.mech_tilt_deg:.1f} + elec {ctx.elec_tilt_deg:.1f}), so "
          f"the target sits {off_boresight_now:.1f} deg "
          f"{'below' if cur_tilt > elev else 'above'} boresight - {beam_pos}."]
    if cc.measured_rsrp_dbm is not None:
        ev.append("Measured at the location: RSRP "
                  f"{cc.measured_rsrp_dbm:.0f} dBm"
                  + (f", RSRQ {cc.measured_rsrq_db:.0f} dB"
                     if cc.measured_rsrq_db is not None else "")
                  + (f", SINR {cc.measured_sinr_db:.0f} dB"
                     if cc.measured_sinr_db is not None else "") + ".")
    ev.append(f"Modelled RSRP at the location now ~{pred_now:.0f} dBm "
              f"(COST-231 Hata {env.kind}, path loss ~{pl:.0f} dB"
              + (", +15 dB indoor" if cc.indoor else "") + ").")
    for kn, lbl in (("avg_rsrp_dbm", "cell avg RSRP"),
                    ("pct_rsrp_poor", "cell % poor RSRP"),
                    ("dl_user_thr_mbps", "cell DL user thr"),
                    ("ta_p95_m", "cell P95 TA (m)")):
        v = ctx.k(kn)
        if v is not None:
            ev.append(f"{lbl} = {v:.1f}.")

    alts: list[str] = []
    monitor = ["avg_rsrp_dbm", "pct_rsrp_poor", "avg_rsrq_db", "avg_sinr_db",
               "dl_user_thr_mbps", "ta_p95_m", "ho_sr", "erab_drop_rate"]
    math_notes = [
        f"inputs: d={dist:.0f} m, h={h:.1f} m, az_off={off:.0f} deg, "
        f"VBW={vbw:.1f} deg",
        f"theta* (boresight on target) = atan({h:.0f}/{dist:.0f}) = {elev:.2f} deg"
        + (f" ; edge target adds VBW/2 -> {ideal_total:.1f} deg" if margin else ""),
        f"current tilt {cur_tilt:.1f} deg vs ideal {ideal_total:.1f} deg "
        f"-> raw delta {raw_delta:+.1f} deg",
    ]

    # ---- branch: tilt already correct -> power / layer --------------- #
    if tilt_is_ok:
        if az_offset_big:
            action = "Azimuth optimisation (physical) + RS power"
            root = (f"Azimuth mismatch: the location is {off:.0f} deg off the "
                    f"{ctx.azimuth_deg:.0f} deg boresight (beyond ~1/3 of the "
                    f"{ctx.hbw_deg:.0f} deg beamwidth), so it loses "
                    f"{-min(12*(off/ctx.hbw_deg)**2,25):.1f} dB of horizontal "
                    f"pattern gain. Downtilt is already aligned "
                    f"({cur_tilt:.1f} vs {elev:.2f} deg).")
            rec_val = (f"Rotate the sector ~{az_rotate} deg toward bearing "
                       f"{(cc.bearing_deg_from_site if cc.bearing_deg_from_site is not None else (ctx.azimuth_deg + (off if True else 0))):.0f} "
                       f"deg (site visit + drive test), keeping >= 30 deg "
                       f"overlap with the adjacent sector. "
                       + (f"Also raise RS power +{pwr['applied_db']:.1f} dB."
                          if pwr["applied_db"] > 0 else ""))
            param = "Mechanical azimuth; Reference signal power (RS EPRE)"
            monitor = ["avg_rsrp_dbm", "avg_rsrq_db", "dl_user_thr_mbps",
                       "ho_sr", "erab_drop_rate"] + monitor[:2]
        else:
            action = ("RS power optimisation"
                      + (" + coverage layer / new site"
                         if pwr["limited"] else ""))
            root = (f"Tilt and azimuth are already aligned to the location "
                    f"(current {cur_tilt:.1f} deg vs ideal {elev:.2f} deg; "
                    f"{off:.0f} deg off boresight). The deficit is link budget: "
                    f"~{pl:.0f} dB path loss over {dist/1000:.2f} km"
                    + (" plus indoor penetration" if cc.indoor else "")
                    + f" leaves RSRP near {base_rsrp:.0f} dBm.")
            rec_val = (f"Raise RS power by {pwr['applied_db']:.1f} dB "
                       f"({ctx.env.rs_epre_dbm:.1f} -> "
                       f"{ctx.env.rs_epre_dbm + pwr['applied_db']:.1f} dBm/RE), "
                       f"within {ctx.env.rs_power_up_headroom_db:.1f} dB "
                       f"head-room. Residual gap {pwr['residual_db']:.1f} dB.")
            param = "Reference signal power (RS EPRE) / cellRefSignalPwr"
            if pwr["limited"]:
                rec_val += (" Power alone will not close it - plan an L800/L900 "
                            "coverage layer, a new site, or "
                            + ("in-building DAS/small cell."
                               if cc.indoor else "a repeater for the area."))
                alts += ["New site / coverage layer for the complaint area.",
                         "Down-tilt a neighbouring overshooter so this cell can "
                         "take the area with power to spare."]
            math_notes.append(f"RSRP gap to -100 dBm = {pwr['needed_db']:.1f} dB "
                              f"-> applied {pwr['applied_db']:.1f} dB "
                              f"(headroom {ctx.env.rs_power_up_headroom_db:.1f} dB)")
        return Recommendation(
            cell_id=ctx.cell_id, site_id=ctx.site_id,
            problem="Poor coverage toward the complaint location",
            category="coverage", root_cause=root, evidence=ev,
            action_type=action, parameter=param,
            current_value=(f"RET {ctx.ret_display(ctx.elec_tilt_deg)}, "
                           f"azimuth {ctx.azimuth_deg:.0f} deg, RS EPRE "
                           f"{ctx.env.rs_epre_dbm:.1f} dBm/RE"),
            recommended_value=rec_val,
            rationale="Tilt is not the lever here - changing it would steer "
                      "energy away from the target. Address the actual "
                      "limiter (horizontal pattern / link budget).",
            expected_impact=(f"Modelled RSRP at the location "
                             f"{pred_now:.0f} -> ~{pred_now + pwr['applied_db'] + (2 if az_offset_big else 0):.0f} "
                             f"dBm; better CQI/throughput and fewer complaints "
                             f"in that direction."),
            risks=["RS-power increase eats PDSCH power-sharing head-room - check "
                   "Pa/Pb and cell-edge throughput.",
                   "Azimuth change is physical and affects the adjacent "
                   "sector's coverage - drive-test both.",
                   "A power boost also extends the cell everywhere - watch P95 "
                   "TA and neighbour RSRQ."],
            monitor_kpis=monitor,
            confidence=_confidence(ctx, ["avg_rsrp_dbm"], base=0.5,
                                   need_geometry=True)
            + (0.1 if cc.measured_rsrp_dbm is not None else 0.0),
            priority="P2", math_notes=math_notes,
            alternatives=alts + (["Indoor solution (DAS / femto / small cell) "
                                  "if the complaint is deep indoor."]
                                 if cc.indoor else []),
        )

    # ---- branch: tilt change (reduce if over-tilted, else add) ------- #
    direction = "Reduce" if delta < 0 else "Increase"
    if over_tilted:
        root = (f"Excessive downtilt: the cell is at {cur_tilt:.1f} deg total "
                f"but the location only needs {elev:.2f} deg. The main lobe is "
                f"aimed at ~{cur_boresight:.0f} m; the complaint at "
                f"{dist/1000:.2f} km sits {off_boresight_now:.1f} deg below "
                f"boresight, in the lower shoulder where pattern gain is down "
                f"~{-min(12*(off_boresight_now/vbw)**2, 25):.1f} dB.")
    else:
        root = (f"Under-tilt: at {cur_tilt:.1f} deg the beam peak is at "
                f"~{cur_boresight:.0f} m, past the location; a little more "
                f"downtilt ({ideal_total:.1f} deg ideal) brings the peak onto "
                f"the {dist/1000:.2f} km target.")
    if az_offset_big:
        root += (f" The location is also {off:.0f} deg off azimuth - an azimuth "
                 f"nudge of ~{az_rotate} deg should be evaluated alongside.")
        alts.append(f"Azimuth: rotate ~{az_rotate} deg toward the location "
                    f"(physical, drive-test validated).")

    return Recommendation(
        cell_id=ctx.cell_id, site_id=ctx.site_id,
        problem="Poor coverage toward the complaint location",
        category="coverage",
        root_cause=root,
        evidence=ev,
        action_type="Antenna tilt optimisation (RET)"
                    + (" + azimuth review" if az_offset_big else ""),
        parameter="Electrical antenna tilt (RET)  [total downtilt = mech + elec]",
        current_value=(f"total {cur_tilt:.1f} deg (mech {ctx.mech_tilt_deg:.1f} "
                       f"+ elec {ctx.elec_tilt_deg:.1f}); RET "
                       f"{ctx.ret_display(ctx.elec_tilt_deg)}"),
        recommended_value=(
            f"{direction} to total ~{new_total:.1f} deg -> set RET to "
            f"{ctx.ret_display(new_elec)} ({delta:+.1f} deg this step"
            + ("; capped by RET range - add mechanical tilt for the rest"
               if clamped else "")
            + f"). Ideal (uncapped) ~{ideal_total:.1f} deg - iterate."),
        rationale=(f"Align boresight with the target: theta* = atan(h/d) = "
                   f"{elev:.2f} deg"
                   + (f" (+VBW/2 for edge = {ideal_total:.1f})" if margin else "")
                   + f". Move {raw_delta:+.1f} deg, capped at "
                   f"{MAX_TILT_STEP_DEG:.0f} deg/step for a controlled iteration."),
        expected_impact=(
            f"Vertical-pattern gain toward the location changes by "
            f"~{gain:+.1f} dB this step; modelled RSRP there "
            f"{pred_now:.0f} -> ~{pred_new:.0f} dBm"
            + (f", with a further ~{pwr['applied_db']:.1f} dB available from RS "
               f"power if needed" if pwr["applied_db"] > 0 else "")
            + f". Expect better RSRP/CQI/throughput at the location and fewer "
            f"complaints in that direction."),
        risks=[
            ("Reducing downtilt extends the footprint - watch P95 TA, far-cell "
             "handovers and RSRQ for new overshoot / pilot pollution."
             if delta < 0 else
             "Adding downtilt shrinks the footprint - check the far cell edge "
             "and neighbour fill-in for new holes."),
            "If a neighbour already dominates the target area, the tilt change "
            "mainly shifts interference - validate dominance first (drive test "
            "/ MR).",
            "Users on the opposite side of boresight see the inverse RSRP "
            "change.",
            "Electrical tilt is remote and reversible; any azimuth change is a "
            "physical, drive-test-validated activity.",
        ],
        monitor_kpis=monitor,
        confidence=_confidence(ctx, ["avg_rsrp_dbm"], base=0.6,
                               need_geometry=True)
        + (0.12 if cc.measured_rsrp_dbm is not None else 0.0)
        + (0.05 if abs(raw_delta) > vbw / 2 else 0.0),
        priority="P2",
        math_notes=math_notes + [
            f"applied step {delta:+.1f} -> total {new_total:.1f} deg ; "
            f"RET elec -> {new_elec:.1f} deg",
            f"pattern gain toward target {gain:+.2f} dB ; "
            f"modelled RSRP {pred_now:.0f} -> {pred_new:.0f} dBm",
        ],
        alternatives=alts + [
            "RS power: if a small residual RSRP gap remains after the tilt "
            "step, a <=3 dB RS-EPRE boost can finish it (check power-sharing).",
            "If the location is deep indoor, a small cell / DAS is more "
            "effective than macro tilt.",
        ],
    )
