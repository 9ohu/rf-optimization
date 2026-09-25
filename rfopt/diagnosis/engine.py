"""Rule engine: aggregated KPI row (+ trends + geometry) -> Diagnosis list.

Every rule is a small function that reads the KPI dict and, if its pattern
fires, returns a Diagnosis with concrete evidence strings. Rules are
deliberately *pattern* based (multiple KPIs corroborating one story), not
single-threshold, so the output reads like an engineer's assessment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from rfopt.actions.geometry import coverage_edge_distance_m, optimal_downtilt_deg
from rfopt.actions.propagation import inter_site_distance_hint_m
from rfopt.diagnosis.models import Diagnosis
from rfopt.kpi.thresholds import Severity, ThresholdSet


@dataclass
class RuleCtx:
    level: str
    entity_id: str
    site_id: str
    technology: str
    k: dict[str, float]
    thresholds: ThresholdSet
    period: str = "day"
    when: str = ""
    isd_m: float | None = None
    geometry: dict | None = None      # from AntennaGeometry.describe()
    env_kind: str = "urban"
    trends: list = None               # list[TrendResult] for this entity

    def g(self, name: str) -> float | None:
        v = self.k.get(name)
        return v if v is not None and v == v else None

    def isd(self) -> float:
        return self.isd_m or inter_site_distance_hint_m(self.env_kind)

    @property
    def has_real_isd(self) -> bool:
        """True only when the inter-site distance came from a site database."""
        return self.isd_m is not None

    def sev_of(self, kpi: str) -> Severity:
        r = self.thresholds.rule(kpi)
        v = self.g(kpi)
        return r.evaluate(v) if (r and v is not None) else Severity.OK

    def traffic(self) -> float:
        return self.g("total_traffic_gb") or 0.0


Rule = Callable[[RuleCtx], Diagnosis | None]
RULES: list[Rule] = []


def rule(fn: Rule) -> Rule:
    RULES.append(fn)
    return fn


def _infer_related_kpis(rc: RuleCtx, evidence: list[str]) -> list[str]:
    """Which KPI series this diagnosis actually leans on (for trend matching)."""
    from rfopt.ingest.schema import kpis_for
    blob = " ".join(evidence).lower()
    found: list[str] = []
    for d in kpis_for(rc.technology):
        if d.name in rc.k and rc.k.get(d.name) is not None:
            toks = [t for t in d.label.lower().replace("/", " ").split()
                    if len(t) > 2]
            hit = d.name.lower() in blob or (
                toks and sum(t in blob for t in toks) >= max(1, len(toks) - 1))
            if hit:
                found.append(d.name)
    return found


def _mk(rc: RuleCtx, problem_class: str, title: str, severity: str,
        confidence: float, evidence: list[str], tags: list[str] | None = None,
        kpi_keys: list[str] | None = None) -> Diagnosis:
    related = kpi_keys or _infer_related_kpis(rc, evidence)
    return Diagnosis(
        entity_level=rc.level, entity_id=rc.entity_id, site_id=rc.site_id,
        technology=rc.technology, problem_class=problem_class, title=title,
        severity=severity, confidence=max(0.15, min(0.97, confidence)),
        evidence=evidence, kpis=dict(rc.k), related_kpis=related,
        when=rc.when, period=rc.period,
        traffic_gb=rc.traffic(), tags=tags or [],
    )


def _worst(*sevs: Severity) -> str:
    return "critical" if any(s == Severity.CRITICAL for s in sevs) else "warning"


# --------------------------------------------------------------------------- #
# LTE rules
# --------------------------------------------------------------------------- #
@rule
def r_overshooting(rc: RuleCtx) -> Diagnosis | None:
    if rc.technology != "LTE":
        return None
    isd = rc.isd()
    real = rc.has_real_isd
    ta95 = rc.g("ta_p95_m")
    ta_far = rc.g("pct_ta_gt_threshold")
    rsrq = rc.g("avg_rsrq_db")
    rsrp = rc.g("avg_rsrp_dbm")
    inter_ho = rc.g("inter_freq_ho_sr")
    ev, score, hard = [], 0.0, False

    # far, polluting server: strong RSRP but poor RSRQ (works without geometry)
    far_server = (rsrp is not None and rsrq is not None
                  and rsrp > -100 and rsrq < -12)

    if ta95 is not None:
        if real and ta95 > 1.25 * isd:
            ev.append(f"P95 timing-advance distance {ta95/1000:.1f} km vs "
                      f"inter-site distance ~{isd/1000:.1f} km ({ta95/isd:.1f}x).")
            score += 0.35 + min(0.25, (ta95 / isd - 1.25) * 0.2)
            hard = True
        elif not real and ta95 > 3500 and (far_server or ta95 > 5500):
            ev.append(f"P95 timing-advance distance {ta95/1000:.1f} km - UEs "
                      f"served far out (no site DB: absolute-distance rule).")
            score += 0.3
            hard = True
    if ta_far is not None and rc.sev_of("pct_ta_gt_threshold") != Severity.OK:
        ev.append(f"{ta_far:.0f}% of UEs beyond the overshoot distance "
                  f"(threshold breached).")
        score += 0.25
        hard = True
    if rc.geometry and real:
        edge = rc.geometry.get("coverage_edge_m", 0)
        if edge and edge > 1.4 * isd:
            ev.append(f"Geometry: total downtilt {rc.geometry['total_tilt_deg']} "
                      f"deg -> 3 dB edge ~{edge/1000:.1f} km, {edge/isd:.1f}x ISD.")
            score += 0.3
            hard = True
    if far_server:
        ev.append(f"Strong RSRP ({rsrp:.0f} dBm) but poor RSRQ ({rsrq:.0f} dB) - "
                  f"the cell is a far, polluting server.")
        score += 0.15
    if inter_ho is not None and inter_ho < 92:
        ev.append(f"Inter-frequency HO success {inter_ho:.1f}% (far, fast-moving "
                  f"UEs failing HO).")
        score += 0.1

    if not hard or score < 0.4:
        return None
    sev = "critical" if (real and ta95 and ta95 > 1.8 * isd) or score > 0.85 \
        else "warning"
    conf = 0.45 + score * 0.4 - (0.0 if real else 0.12)
    return _mk(rc, "overshooting", "Cell overshooting its intended footprint",
              sev, conf, ev, tags=["tilt", "interference"])


@rule
def r_coverage(rc: RuleCtx) -> Diagnosis | None:
    rsrp = rc.g("avg_rsrp_dbm")
    pct_poor = rc.g("pct_rsrp_poor")
    s_rsrp = rc.sev_of("avg_rsrp_dbm")
    s_poor = rc.sev_of("pct_rsrp_poor")
    if s_rsrp == Severity.OK and s_poor == Severity.OK:
        return None
    ev, score = [], 0.0
    if rsrp is not None:
        ev.append(f"Average RSRP {rsrp:.1f} dBm "
                  f"({'critical' if s_rsrp == Severity.CRITICAL else 'below target'}).")
        score += 0.35 if s_rsrp == Severity.CRITICAL else 0.22
    if pct_poor is not None and s_poor != Severity.OK:
        ev.append(f"{pct_poor:.0f}% of MR samples below -110 dBm.")
        score += 0.3 if s_poor == Severity.CRITICAL else 0.18
    for kn, txt, w in (
        ("dl_user_thr_mbps", "DL user throughput {v:.1f} Mbps", 0.12),
        ("avg_sinr_db", "avg SINR {v:.1f} dB", 0.1),
        ("dl_qpsk_ratio", "DL QPSK share {v:.0f}% (low-order MCS at edge)", 0.1),
        ("erab_drop_rate", "E-RAB drop {v:.2f}% (edge RLF)", 0.12),
        ("rrc_setup_sr", "RRC setup {v:.1f}% (edge access failures)", 0.1),
    ):
        v = rc.g(kn)
        if v is None:
            continue
        s = rc.sev_of(kn)
        if s != Severity.OK:
            ev.append(txt.format(v=v) + ".")
            score += w
    if rc.geometry and rc.has_real_isd:
        edge = rc.geometry.get("coverage_edge_m", 0)
        if edge and edge < 0.75 * rc.isd():
            ev.append(f"Geometry: 3 dB edge only ~{edge/1000:.1f} km vs ISD "
                      f"~{rc.isd()/1000:.1f} km - footprint is short.")
            score += 0.15
    sev = _worst(s_rsrp, s_poor)
    return _mk(rc, "coverage", "Poor coverage / weak RSRP over the served area",
              sev, 0.4 + score * 0.45, ev, tags=["coverage", "tilt", "power"])


@rule
def r_dl_interference(rc: RuleCtx) -> Diagnosis | None:
    rsrq = rc.g("avg_rsrq_db")
    sinr = rc.g("avg_sinr_db")
    rsrp = rc.g("avg_rsrp_dbm")
    s_rsrq = rc.sev_of("avg_rsrq_db")
    s_sinr = rc.sev_of("avg_sinr_db")
    if s_rsrq == Severity.OK and s_sinr == Severity.OK:
        return None
    # only "DL interference / pilot pollution" when RSRP is NOT the problem
    if rsrp is not None and rsrp < -108:
        return None
    ev, score = [], 0.0
    if rsrq is not None and s_rsrq != Severity.OK:
        ev.append(f"Average RSRQ {rsrq:.1f} dB with "
                  f"{'adequate' if (rsrp or -120) > -105 else 'usable'} RSRP "
                  f"({rsrp:.0f} dBm) - no dominant server.")
        score += 0.3 if s_rsrq == Severity.CRITICAL else 0.2
    if sinr is not None and s_sinr != Severity.OK:
        ev.append(f"Average SINR {sinr:.1f} dB.")
        score += 0.25 if s_sinr == Severity.CRITICAL else 0.15
    for kn, txt, w in (
        ("dl_qpsk_ratio", "DL QPSK share {v:.0f}%", 0.12),
        ("avg_cqi", "avg CQI {v:.1f}", 0.12),
        ("dl_user_thr_mbps", "DL user throughput {v:.1f} Mbps", 0.1),
        ("pct_prb_high_intf", "{v:.0f}% PRBs with high interference", 0.15),
    ):
        v = rc.g(kn)
        if v is not None and rc.sev_of(kn) != Severity.OK:
            ev.append(txt.format(v=v) + ".")
            score += w
    if not ev:
        return None
    return _mk(rc, "poor_rsrq",
              "Poor RSRQ / low SINR from downlink overlap (pilot pollution)",
              _worst(s_rsrq, s_sinr), 0.4 + score * 0.4, ev,
              tags=["interference", "overlap", "tilt"])


@rule
def r_ul_interference(rc: RuleCtx) -> Diagnosis | None:
    ul = rc.g("ul_rssi_dbm")
    s = rc.sev_of("ul_rssi_dbm")
    if ul is None or s == Severity.OK:
        return None
    rise = ul - (-115.0)
    ev = [f"UL RSSI/PRB {ul:.1f} dBm (~{rise:.0f} dB above the thermal floor)."]
    score = 0.35 if s == Severity.CRITICAL else 0.22
    for kn, txt, w in (
        ("ul_bler", "UL residual BLER {v:.0f}%", 0.12),
        ("ul_user_thr_mbps", "UL user throughput {v:.2f} Mbps", 0.12),
        ("rach_setup_sr", "RACH success {v:.0f}% (Msg1/Msg3 corrupted)", 0.15),
        ("erab_drop_rate", "E-RAB drop {v:.2f}% (UL RLF)", 0.12),
        ("rrc_setup_sr", "RRC setup {v:.1f}%", 0.1),
    ):
        v = rc.g(kn)
        if v is not None and rc.sev_of(kn) != Severity.OK:
            ev.append(txt.format(v=v) + ".")
            score += w
    return _mk(rc, "interference", "High uplink interference / noise rise",
              "critical" if s == Severity.CRITICAL else "warning",
              0.45 + score * 0.4, ev, tags=["interference", "uplink", "hardware"])


@rule
def r_congestion(rc: RuleCtx) -> Diagnosis | None:
    prb = rc.g("dl_prb_util")
    ulprb = rc.g("ul_prb_util")
    cce = rc.g("pdcch_util")
    rej = rc.g("ue_rejected_rrc")
    users = rc.g("rrc_conn_users_avg")
    s_prb = rc.sev_of("dl_prb_util")
    s_cce = rc.sev_of("pdcch_util")
    s_rej = rc.sev_of("ue_rejected_rrc")
    if s_prb == Severity.OK and s_cce == Severity.OK and s_rej == Severity.OK:
        return None
    ev, score = [], 0.0
    if prb is not None and s_prb != Severity.OK:
        ev.append(f"DL PRB utilisation {prb:.0f}%"
                  + (f" (UL {ulprb:.0f}%)" if ulprb is not None else "") + ".")
        score += 0.35 if s_prb == Severity.CRITICAL else 0.22
    if cce is not None and s_cce != Severity.OK:
        ev.append(f"PDCCH CCE utilisation {cce:.0f}% - control-channel limited.")
        score += 0.2
    if rej is not None and s_rej != Severity.OK:
        ev.append(f"{rej:.0f} RRC rejections/period (admission blocking).")
        score += 0.25
    if users is not None:
        ev.append(f"Average RRC-connected users {users:.0f}.")
    for kn, txt, w in (("dl_user_thr_mbps",
                        "DL user throughput {v:.1f} Mbps (resource-shared down)",
                        0.15),
                       ("erab_drop_rate", "E-RAB drop {v:.2f}% at load", 0.1)):
        v = rc.g(kn)
        if v is not None and rc.sev_of(kn) != Severity.OK:
            ev.append(txt.format(v=v) + ".")
            score += w
    return _mk(rc, "high_utilization",
              "High utilisation / congestion", _worst(s_prb, s_cce, s_rej),
              0.45 + score * 0.4, ev, tags=["capacity", "load-balancing"])


@rule
def r_low_throughput(rc: RuleCtx) -> Diagnosis | None:
    thr = rc.g("dl_user_thr_mbps")
    s = rc.sev_of("dl_user_thr_mbps")
    if thr is None or s == Severity.OK:
        return None
    cqi = rc.g("avg_cqi")
    sinr = rc.g("avg_sinr_db")
    prb = rc.g("dl_prb_util") or 0
    ev = [f"DL user throughput {thr:.2f} Mbps "
          f"({'critical' if s == Severity.CRITICAL else 'below target'})."]
    score = 0.3 if s == Severity.CRITICAL else 0.18
    if cqi is not None:
        ev.append(f"Average CQI {cqi:.1f}"
                  + (" (RF-limited)" if cqi < 7 else " (RF adequate)") + ".")
        score += 0.12 if cqi < 7 else 0.0
    if sinr is not None:
        ev.append(f"Average SINR {sinr:.1f} dB.")
    ev.append(f"DL PRB utilisation {prb:.0f}%"
              + (" - capacity-limited" if prb >= 75 else
                 " - spare PRBs, so not purely a load issue") + ".")
    for kn, txt in (("dl_256qam_ratio", "256QAM share {v:.0f}%"),
                    ("dl_spectral_eff", "DL spectral efficiency {v:.2f} b/s/Hz")):
        v = rc.g(kn)
        if v is not None:
            ev.append(txt.format(v=v) + ".")
    return _mk(rc, "low_throughput", "Low user throughput",
              "critical" if s == Severity.CRITICAL else "warning",
              0.4 + score, ev, tags=["throughput", "capacity", "rf"])


@rule
def r_accessibility(rc: RuleCtx) -> Diagnosis | None:
    parts = [("rrc_setup_sr", "RRC setup success"),
             ("erab_setup_sr", "E-RAB setup success"),
             ("call_setup_sr", "call setup success"),
             ("rach_setup_sr", "RACH success")]
    fired = [(kn, lbl, rc.g(kn), rc.sev_of(kn)) for kn, lbl in parts]
    bad = [x for x in fired if x[2] is not None and x[3] != Severity.OK]
    if not bad:
        return None
    ev = [f"{lbl} {v:.2f}% "
          f"({'critical' if s == Severity.CRITICAL else 'below target'})."
          for _, lbl, v, s in bad]
    score = 0.25 + 0.12 * len(bad)
    # correlate
    for kn, txt, w in (("pdcch_util", "PDCCH util {v:.0f}% (congestion)", 0.15),
                       ("dl_prb_util", "PRB util {v:.0f}%", 0.1),
                       ("ue_rejected_rrc", "{v:.0f} RRC rejects", 0.15),
                       ("avg_rsrp_dbm", "avg RSRP {v:.0f} dBm (edge access)", 0.12),
                       ("ul_rssi_dbm", "UL RSSI {v:.0f} dBm (Msg1 fail)", 0.12)):
        v = rc.g(kn)
        if v is not None and rc.sev_of(kn) != Severity.OK:
            ev.append(txt.format(v=v) + ".")
            score += w
    sev = _worst(*[s for *_, s in bad])
    return _mk(rc, "accessibility", "Accessibility degraded (setup success low)",
              sev, 0.4 + score * 0.4, ev, tags=["accessibility"])


@rule
def r_retainability(rc: RuleCtx) -> Diagnosis | None:
    drop = rc.g("erab_drop_rate")
    ctxd = rc.g("ctxt_drop_rate")
    s_d = rc.sev_of("erab_drop_rate")
    s_c = rc.sev_of("ctxt_drop_rate")
    if s_d == Severity.OK and s_c == Severity.OK:
        return None
    ev = []
    if drop is not None and s_d != Severity.OK:
        ev.append(f"E-RAB drop rate {drop:.2f}% "
                  f"({'critical' if s_d == Severity.CRITICAL else 'above target'}).")
    if ctxd is not None and s_c != Severity.OK:
        ev.append(f"UE-context drop rate {ctxd:.2f}%.")
    score = 0.3
    for kn, txt, w in (("avg_sinr_db", "avg SINR {v:.1f} dB", 0.12),
                       ("avg_rsrp_dbm", "avg RSRP {v:.0f} dBm", 0.12),
                       ("ul_rssi_dbm", "UL RSSI {v:.0f} dBm", 0.15),
                       ("ho_sr", "HO success {v:.1f}% (mobility drops)", 0.15),
                       ("dl_prb_util", "PRB util {v:.0f}% (load drops)", 0.1),
                       ("ta_p95_m", "P95 TA {v:.0f} m (edge/overshoot)", 0.1)):
        v = rc.g(kn)
        if v is not None and (rc.sev_of(kn) != Severity.OK or kn == "ta_p95_m"):
            if kn == "ta_p95_m" and not (
                    (rc.has_real_isd and v > 1.3 * rc.isd())
                    or (not rc.has_real_isd and v > 4000)):
                continue
            ev.append(txt.format(v=v) + ".")
            score += w
    return _mk(rc, "retainability", "Retainability degraded (drop rate high)",
              _worst(s_d, s_c), 0.4 + score * 0.4, ev, tags=["retainability"])


@rule
def r_handover(rc: RuleCtx) -> Diagnosis | None:
    checks = [("ho_sr", "overall HO success"),
              ("intra_freq_ho_sr", "intra-frequency HO success"),
              ("inter_freq_ho_sr", "inter-frequency HO success"),
              ("x2_ho_sr", "X2 HO success"), ("s1_ho_sr", "S1 HO success")]
    bad = [(kn, lbl, rc.g(kn), rc.sev_of(kn)) for kn, lbl in checks]
    bad = [x for x in bad if x[2] is not None and x[3] != Severity.OK]
    pp = rc.g("ho_ping_pong_rate")
    s_pp = rc.sev_of("ho_ping_pong_rate")
    if not bad and s_pp == Severity.OK:
        return None
    ev = [f"{lbl} {v:.2f}%." for _, lbl, v, s in bad]
    if pp is not None and s_pp != Severity.OK:
        ev.append(f"Ping-pong handover rate {pp:.1f}%.")
    score = 0.25 + 0.12 * len(bad) + (0.2 if s_pp != Severity.OK else 0)
    sev = _worst(*([s for *_, s in bad] + [s_pp]))
    return _mk(rc, "handover", "Handover performance degraded", sev,
              0.4 + score * 0.4, ev, tags=["mobility", "neighbour"])


@rule
def r_availability(rc: RuleCtx) -> Diagnosis | None:
    av = rc.g("cell_avail_pct")
    un = rc.g("cell_unavail_min")
    s_av = rc.sev_of("cell_avail_pct")
    s_un = rc.sev_of("cell_unavail_min")
    if s_av == Severity.OK and s_un == Severity.OK:
        return None
    ev = []
    if av is not None and s_av != Severity.OK:
        ev.append(f"Cell availability {av:.2f}% "
                  f"({'critical' if s_av == Severity.CRITICAL else 'below target'}).")
    if un is not None and s_un != Severity.OK:
        ev.append(f"Cell unavailable ~{un:.0f} min in the period.")
    return _mk(rc, "availability", "Cell availability below target",
              _worst(s_av, s_un), 0.6, ev, tags=["hardware", "non-rf"])


# --------------------------------------------------------------------------- #
# UMTS / GSM - compact threshold-driven rules
# --------------------------------------------------------------------------- #
_GENERIC = {
    "UMTS": [
        ("accessibility", "Accessibility degraded",
         ["rrc_setup_sr", "rab_setup_sr_cs", "rab_setup_sr_ps"]),
        ("retainability", "Call drop rate high", ["cs_drop_rate", "ps_drop_rate"]),
        ("handover", "Handover / SHO degraded", ["soft_ho_sr", "irat_ho_sr"]),
        ("coverage", "Poor coverage (RSCP)", ["avg_rscp_dbm"]),
        ("poor_rsrq", "Poor Ec/No (pilot pollution)", ["avg_ecno_db"]),
        ("interference", "High UL RTWP", ["ul_rtwp_dbm"]),
        ("high_utilization", "DL power / code congestion", ["dl_power_util"]),
        ("availability", "Cell availability", ["cell_avail_pct"]),
    ],
    "GSM": [
        ("accessibility", "Assignment / SDCCH problems",
         ["tch_assign_sr", "sdcch_block_rate"]),
        ("high_utilization", "TCH congestion", ["tch_block_rate"]),
        ("retainability", "Call drop rate high", ["call_drop_rate"]),
        ("handover", "Handover degraded", ["ho_sr"]),
        ("interference", "UL interference band high", ["ul_interference_band"]),
        ("coverage", "Poor DL RxLev", ["avg_rxlev_dl_dbm"]),
        ("availability", "Cell availability", ["cell_avail_pct"]),
    ],
}


@rule
def r_generic_2g3g(rc: RuleCtx) -> Diagnosis | None:
    if rc.technology not in _GENERIC:
        return None
    diags: list[Diagnosis] = []
    for pclass, title, kpis in _GENERIC[rc.technology]:
        bad = [(kn, rc.g(kn), rc.sev_of(kn)) for kn in kpis
               if rc.g(kn) is not None and rc.sev_of(kn) != Severity.OK]
        if not bad:
            continue
        ev = [f"{kn} = {v:.2f} "
              f"({'critical' if s == Severity.CRITICAL else 'warning'})."
              for kn, v, s in bad]
        sev = _worst(*[s for *_, s in bad])
        diags.append(_mk(rc, pclass, title, sev, 0.55, ev,
                         tags=[rc.technology.lower()]))
    # this rule returns the first; diagnose_entity calls all rules, so emit all
    rc.k.setdefault("_extra_diags", [])
    if diags:
        rc.k["_extra_diags"] = diags[1:]
        return diags[0]
    return None


# --------------------------------------------------------------------------- #
def _apply_trends(diags: list[Diagnosis], trends: list) -> None:
    if not trends:
        return
    by_kpi = {t.kpi: t for t in trends}
    for d in diags:
        keys = set(d.related_kpis) or set(d.kpis)
        related = [t for kn, t in by_kpi.items()
                   if kn in keys and t.verdict in ("degrading", "step-down")]
        if not related:
            continue
        worst = min(related, key=lambda t: (t.verdict != "step-down",
                                            -abs(t.pct_change)))
        d.trend_note = (f"{worst.label} {worst.verdict} "
                        f"({worst.pct_change:+.0f}% over the window, "
                        f"{worst.n} points)")
        d.confidence = min(0.97, d.confidence + 0.08)
        if worst.verdict == "step-down" and d.severity == "warning":
            d.evidence.append(f"Trend: {worst.label} shows a step change "
                              f"({worst.baseline:.2f} -> {worst.recent:.2f}).")


def diagnose_entity(
    row: dict,
    thresholds: ThresholdSet,
    *,
    level: str = "cell",
    isd_m: float | None = None,
    geometry: dict | None = None,
    env_kind: str = "urban",
    trends: list | None = None,
) -> list[Diagnosis]:
    id_col = {"cell": "cell_id", "sector": "sector_id", "site": "site_id"}[level]
    rc = RuleCtx(
        level=level,
        entity_id=str(row.get(id_col, "")),
        site_id=str(row.get("site_id", "")),
        technology=str(row.get("technology", thresholds.technology)),
        k={k: v for k, v in row.items()},
        thresholds=thresholds,
        period=str(row.get("period", "day")),
        when=str(row.get("datetime", "")) if row.get("datetime") is not None else "",
        isd_m=isd_m, geometry=geometry, env_kind=env_kind, trends=trends,
    )
    out: list[Diagnosis] = []
    for fn in RULES:
        try:
            d = fn(rc)
        except Exception:
            d = None
        if d is not None:
            out.append(d)
    out.extend(rc.k.pop("_extra_diags", []) or [])
    _apply_trends(out, trends or [])
    out.sort(key=lambda d: (-d.severity_rank, -d.confidence))
    return out


def diagnose_frame(
    agg_df: pd.DataFrame,
    thresholds: ThresholdSet,
    *,
    level: str = "cell",
    site_db: pd.DataFrame | None = None,
    trends_by_entity: dict[str, list] | None = None,
    env_kind: str = "urban",
    isd_by_site: dict[str, float] | None = None,
) -> list[Diagnosis]:
    """Diagnose every entity in an aggregated frame."""
    if agg_df.empty:
        return []
    id_col = {"cell": "cell_id", "sector": "sector_id", "site": "site_id"}[level]
    geo_lookup: dict[str, dict] = {}
    if site_db is not None and not site_db.empty and level == "cell":
        from rfopt.actions.geometry import AntennaGeometry
        for _, r in site_db.iterrows():
            try:
                g = AntennaGeometry(
                    height_m=float(r.get("antenna_height_m", 30) or 30),
                    azimuth_deg=float(r.get("azimuth_deg", 0) or 0),
                    mech_tilt_deg=float(r.get("mech_tilt_deg", 0) or 0),
                    elec_tilt_deg=float(r.get("elec_tilt_deg", 3) or 3),
                    vbw_deg=float(r.get("vbw_deg", 6.5) or 6.5),
                )
                geo_lookup[str(r.get("cell_id", ""))] = g.describe()
            except Exception:
                continue

    all_diags: list[Diagnosis] = []
    for _, row in agg_df.iterrows():
        eid = str(row.get(id_col, ""))
        d = diagnose_entity(
            row.to_dict(), thresholds, level=level,
            isd_m=(isd_by_site or {}).get(str(row.get("site_id", ""))),
            geometry=geo_lookup.get(eid),
            env_kind=env_kind,
            trends=(trends_by_entity or {}).get(eid),
        )
        all_diags.extend(d)

    # sector-level traffic imbalance from cell rows
    if level == "cell" and "sector_id" in agg_df.columns and \
            "dl_prb_util" in agg_df.columns:
        all_diags.extend(_imbalance_diags(agg_df, thresholds, env_kind))
    all_diags.sort(key=lambda d: (-d.severity_rank, -d.traffic_gb, -d.confidence))
    return all_diags


def _imbalance_diags(agg_df: pd.DataFrame, thresholds: ThresholdSet,
                     env_kind: str) -> list[Diagnosis]:
    out: list[Diagnosis] = []
    df = agg_df.dropna(subset=["dl_prb_util"])
    for sector, g in df.groupby("sector_id"):
        if len(g) < 2:
            continue
        gg = g.sort_values("dl_prb_util", ascending=False)
        hi = gg.iloc[0]
        lo = gg.iloc[-1]
        gap = float(hi["dl_prb_util"]) - float(lo["dl_prb_util"])
        if gap < 25 or float(hi["dl_prb_util"]) < 55:
            continue
        ev = [f"Co-sited cells in sector {sector}: "
              + ", ".join(f"{r['cell_id']} {r['dl_prb_util']:.0f}%"
                          for _, r in gg.iterrows())
              + f" (spread {gap:.0f} pp).",
              f"Heaviest cell {hi['cell_id']} at {hi['dl_prb_util']:.0f}% while "
              f"{lo['cell_id']} sits at {lo['dl_prb_util']:.0f}%."]
        out.append(Diagnosis(
            entity_level="cell", entity_id=str(hi["cell_id"]),
            site_id=str(hi.get("site_id", "")),
            technology=str(hi.get("technology", "LTE")),
            problem_class="traffic_imbalance",
            title="Traffic / load imbalance between co-sited cells",
            severity="critical" if gap > 45 else "warning",
            confidence=0.6,
            evidence=ev,
            kpis={**hi.to_dict(),
                  "_sibling_load": {str(r["cell_id"]): float(r["dl_prb_util"])
                                    for _, r in gg.iterrows()}},
            traffic_gb=float(hi.get("total_traffic_gb", 0) or 0),
            tags=["load-balancing"],
        ))
    return out
