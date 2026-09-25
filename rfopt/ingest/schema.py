"""Canonical KPI schema.

Every KPI file, regardless of vendor or operator template, is normalised into a
DataFrame that uses these column names. Downstream engines only ever see these.

A KPI is described by a :class:`KpiDef`:

    direction   "up"   -> higher is better   (throughput, success rates, RSRP)
                "down" -> lower is better    (drop rate, PRB util, BLER, latency)
                "info" -> neutral context    (traffic volume, user count, TA)
    unit        display unit
    category    accessibility | retainability | mobility | integrity |
                availability | utilization | coverage | quality | interference |
                traffic | context
    aggregate   how to roll a KPI up from cell -> sector -> site and hour -> day
                ("mean", "sum", "weighted" -> weighted by `weight_kpi`)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Direction = Literal["up", "down", "info"]
Aggregate = Literal["mean", "sum", "weighted", "min", "max"]

# --------------------------------------------------------------------------- #
# Identity / dimension columns
# --------------------------------------------------------------------------- #
DIM_COLUMNS: list[str] = [
    "datetime",       # pandas Timestamp
    "granularity",    # "hour" | "day"
    "technology",     # "LTE" | "UMTS" | "GSM"
    "region",         # operator cluster / region label, e.g. "R5"
    "site_id",        # eNodeB / NodeB / BSC-BTS identifier
    "cell_id",        # full cell name (unique key with datetime)
    "cell_local_id",  # Local Cell ID / CI / PSC
    "sector_id",      # derived: <site_id>-S<n>
    "band",           # L800 / L1800 / L2100 / U2100 / G900 ...
    "earfcn",         # channel number (optional)
]

KEY_COLUMNS = ["datetime", "technology", "site_id", "cell_id"]


@dataclass(frozen=True)
class KpiDef:
    name: str
    label: str
    direction: Direction
    unit: str
    category: str
    aggregate: Aggregate = "mean"
    weight_kpi: str | None = None      # for aggregate == "weighted"
    description: str = ""
    tech: tuple[str, ...] = ("LTE",)


def _k(*args, **kw) -> KpiDef:      # tiny helper to keep the table compact
    return KpiDef(*args, **kw)


# --------------------------------------------------------------------------- #
# LTE KPI catalogue
# --------------------------------------------------------------------------- #
LTE_KPIS: list[KpiDef] = [
    # ---- Accessibility ---------------------------------------------------- #
    _k("rrc_setup_sr", "RRC Setup Success Rate", "up", "%", "accessibility",
       "weighted", "rrc_setup_att",
       "RRC connection establishment success ratio."),
    _k("erab_setup_sr", "E-RAB Setup Success Rate", "up", "%", "accessibility",
       "weighted", "erab_setup_att",
       "E-RAB establishment success ratio (initial + added)."),
    _k("call_setup_sr", "Call Setup Success Rate", "up", "%", "accessibility",
       "weighted", "rrc_setup_att",
       "End-to-end accessibility: RRC x S1-Sig x E-RAB."),
    _k("rrc_setup_att", "RRC Setup Attempts", "info", "#", "accessibility", "sum"),
    _k("rrc_setup_succ", "RRC Setup Successes", "info", "#", "accessibility", "sum"),
    _k("erab_setup_att", "E-RAB Setup Attempts", "info", "#", "accessibility", "sum"),
    _k("erab_setup_succ", "E-RAB Setup Successes", "info", "#", "accessibility", "sum"),
    _k("rach_setup_sr", "RACH Setup Success Rate", "up", "%", "accessibility",
       "mean", None, "Random access success ratio (contention based)."),

    # ---- Retainability -------------------------------------------------- #
    _k("erab_drop_rate", "E-RAB Drop Rate", "down", "%", "retainability",
       "weighted", "erab_drop_denom",
       "Abnormal E-RAB releases / (abnormal + normal releases)."),
    _k("erab_drop_denom", "E-RAB Release Denominator", "info", "#",
       "retainability", "sum"),
    _k("erab_abnorm_rel", "E-RAB Abnormal Releases", "info", "#",
       "retainability", "sum"),
    _k("ctxt_drop_rate", "UE Context Drop Rate", "down", "%", "retainability",
       "mean", None, "S1/UE-context abnormal release ratio."),

    # ---- Mobility ------------------------------------------------------- #
    _k("ho_sr", "Handover Success Rate", "up", "%", "mobility",
       "weighted", "ho_att", "Overall (prep x exec) handover success ratio."),
    _k("ho_att", "Handover Attempts", "info", "#", "mobility", "sum"),
    _k("ho_succ", "Handover Successes", "info", "#", "mobility", "sum"),
    _k("intra_freq_ho_sr", "Intra-Freq HO Success Rate", "up", "%", "mobility",
       "mean"),
    _k("inter_freq_ho_sr", "Inter-Freq HO Success Rate", "up", "%", "mobility",
       "mean"),
    _k("x2_ho_sr", "X2 Handover Success Rate", "up", "%", "mobility", "mean"),
    _k("s1_ho_sr", "S1 Handover Success Rate", "up", "%", "mobility", "mean"),
    _k("ho_ping_pong_rate", "HO Ping-Pong Rate", "down", "%", "mobility", "mean",
       None, "Return handover within the ping-pong timer."),
    _k("redirect_to_umts", "Redirections to UMTS", "info", "#", "mobility", "sum"),

    # ---- Integrity / throughput / latency ----------------------------- #
    _k("dl_user_thr_mbps", "DL User Throughput", "up", "Mbps", "integrity",
       "weighted", "dl_thr_time_ratio",
       "PDCP DL throughput excluding the last slot of each buffer."),
    _k("ul_user_thr_mbps", "UL User Throughput", "up", "Mbps", "integrity",
       "weighted", "ul_thr_time_ratio"),
    _k("dl_cell_thr_mbps", "DL Cell Throughput", "info", "Mbps", "integrity", "mean"),
    _k("ul_cell_thr_mbps", "UL Cell Throughput", "info", "Mbps", "integrity", "mean"),
    _k("dl_thr_time_ratio", "DL Throughput Sample Weight", "info", "#",
       "integrity", "sum"),
    _k("ul_thr_time_ratio", "UL Throughput Sample Weight", "info", "#",
       "integrity", "sum"),
    _k("dl_latency_ms", "DL Latency", "down", "ms", "integrity", "mean"),
    _k("dl_spectral_eff", "DL Spectral Efficiency", "up", "bit/s/Hz", "integrity",
       "mean"),

    # ---- Coverage / quality ------------------------------------------- #
    _k("avg_rsrp_dbm", "Average RSRP", "up", "dBm", "coverage", "mean", None,
       "MR-averaged serving RSRP."),
    _k("avg_rsrq_db", "Average RSRQ", "up", "dB", "quality", "mean"),
    _k("avg_sinr_db", "Average SINR", "up", "dB", "quality", "mean"),
    _k("avg_cqi", "Average CQI", "up", "index", "quality", "mean"),
    _k("pct_cqi_ge10", "% CQI >= 10", "up", "%", "quality", "mean"),
    _k("dl_bler", "DL Residual BLER", "down", "%", "quality", "mean"),
    _k("ul_bler", "UL Residual BLER", "down", "%", "quality", "mean"),
    _k("pct_rsrp_poor", "% RSRP < -110 dBm", "down", "%", "coverage", "mean",
       None, "Share of MR samples in poor coverage."),
    _k("pct_rsrq_poor", "% RSRQ < -15 dB", "down", "%", "quality", "mean"),
    _k("dl_256qam_ratio", "DL 256QAM Ratio", "up", "%", "quality", "mean"),
    _k("dl_qpsk_ratio", "DL QPSK Ratio", "down", "%", "quality", "mean",
       None, "High QPSK share indicates poor RF / cell edge."),

    # ---- Timing advance / distance ----------------------------------- #
    _k("ta_avg_m", "Average Timing Advance Distance", "info", "m", "coverage",
       "mean", None, "Mean UE distance implied by the TA histogram."),
    _k("ta_p95_m", "P95 Timing Advance Distance", "info", "m", "coverage",
       "mean", None, "95th percentile UE distance (overshoot indicator)."),
    _k("pct_ta_gt_threshold", "% UEs beyond overshoot distance", "down", "%",
       "coverage", "mean"),

    # ---- Utilisation / capacity ------------------------------------- #
    _k("dl_prb_util", "DL PRB Utilisation", "down", "%", "utilization", "mean"),
    _k("ul_prb_util", "UL PRB Utilisation", "down", "%", "utilization", "mean"),
    _k("pdcch_util", "PDCCH CCE Utilisation", "down", "%", "utilization", "mean"),
    _k("rrc_conn_users_avg", "Avg RRC Connected Users", "info", "#",
       "utilization", "mean"),
    _k("rrc_conn_users_max", "Max RRC Connected Users", "info", "#",
       "utilization", "max"),
    _k("active_users_dl_avg", "Avg Active DL Users", "info", "#", "utilization",
       "mean"),
    _k("ue_rejected_rrc", "RRC Rejections (Congestion)", "down", "#",
       "utilization", "sum"),
    _k("dl_traffic_gb", "DL Data Volume", "info", "GB", "traffic", "sum"),
    _k("ul_traffic_gb", "UL Data Volume", "info", "GB", "traffic", "sum"),
    _k("total_traffic_gb", "Total Data Volume", "info", "GB", "traffic", "sum"),

    # ---- Availability ----------------------------------------------- #
    _k("cell_avail_pct", "Cell Availability", "up", "%", "availability", "mean"),
    _k("cell_unavail_min", "Cell Unavailable Time", "down", "min",
       "availability", "sum"),

    # ---- Interference --------------------------------------------- #
    _k("ul_rssi_dbm", "UL RSSI / Interference (PUSCH)", "down", "dBm",
       "interference", "mean", None,
       "Average interference-plus-noise per PRB on PUSCH."),
    _k("ul_rssi_pucch_dbm", "UL RSSI (PUCCH)", "down", "dBm", "interference",
       "mean"),
    _k("pct_prb_high_intf", "% PRBs with high interference", "down", "%",
       "interference", "mean"),
]

# --------------------------------------------------------------------------- #
# UMTS / GSM - lighter catalogue, threshold configs carry the rest
# --------------------------------------------------------------------------- #
UMTS_KPIS: list[KpiDef] = [
    _k("rrc_setup_sr", "RRC Setup Success Rate", "up", "%", "accessibility",
       "mean", tech=("UMTS",)),
    _k("rab_setup_sr_cs", "RAB Setup SR (CS)", "up", "%", "accessibility",
       "mean", tech=("UMTS",)),
    _k("rab_setup_sr_ps", "RAB Setup SR (PS)", "up", "%", "accessibility",
       "mean", tech=("UMTS",)),
    _k("cs_drop_rate", "CS Call Drop Rate", "down", "%", "retainability",
       "mean", tech=("UMTS",)),
    _k("ps_drop_rate", "PS Call Drop Rate", "down", "%", "retainability",
       "mean", tech=("UMTS",)),
    _k("soft_ho_sr", "Soft Handover Success Rate", "up", "%", "mobility",
       "mean", tech=("UMTS",)),
    _k("irat_ho_sr", "IRAT Handover Success Rate", "up", "%", "mobility",
       "mean", tech=("UMTS",)),
    _k("avg_rscp_dbm", "Average RSCP", "up", "dBm", "coverage", "mean",
       tech=("UMTS",)),
    _k("avg_ecno_db", "Average Ec/No", "up", "dB", "quality", "mean",
       tech=("UMTS",)),
    _k("hsdpa_thr_mbps", "HSDPA Throughput", "up", "Mbps", "integrity", "mean",
       tech=("UMTS",)),
    _k("dl_power_util", "DL Power Utilisation (Tx)", "down", "%", "utilization",
       "mean", tech=("UMTS",)),
    _k("ul_rtwp_dbm", "UL RTWP", "down", "dBm", "interference", "mean",
       tech=("UMTS",)),
    _k("cell_avail_pct", "Cell Availability", "up", "%", "availability", "mean",
       tech=("UMTS",)),
    _k("total_traffic_gb", "Total Data Volume", "info", "GB", "traffic", "sum",
       tech=("UMTS",)),
    _k("ipmm_rtt_ms", "IP Path RTT", "down", "ms", "integrity", "mean",
       tech=("UMTS",),
       description="VS.IPPM.Rtt.Means — Iub/transport round-trip time."),
    _k("dl_flowctrl_drops", "DL Flow-Control Drops", "down", "#", "utilization",
       "sum", tech=("UMTS",),
       description="VS.RscGroup.FlowCtrol.DL.DropNum — frames dropped by Iub "
                   "flow control; a transport-congestion signal."),
]

GSM_KPIS: list[KpiDef] = [
    _k("tch_assign_sr", "TCH Assignment Success Rate", "up", "%",
       "accessibility", "mean", tech=("GSM",)),
    _k("sdcch_block_rate", "SDCCH Blocking Rate", "down", "%", "accessibility",
       "mean", tech=("GSM",)),
    _k("tch_block_rate", "TCH Blocking Rate", "down", "%", "utilization",
       "mean", tech=("GSM",)),
    _k("call_drop_rate", "Call Drop Rate (TCH)", "down", "%", "retainability",
       "mean", tech=("GSM",)),
    _k("ho_sr", "Handover Success Rate", "up", "%", "mobility", "mean",
       tech=("GSM",)),
    _k("dl_quality_p_good", "% Good DL RxQual (0-2)", "up", "%", "quality",
       "mean", tech=("GSM",)),
    _k("avg_rxlev_dl_dbm", "Average DL RxLev", "up", "dBm", "coverage", "mean",
       tech=("GSM",)),
    _k("ul_interference_band", "UL Interference Band (avg)", "down", "index",
       "interference", "mean", tech=("GSM",)),
    _k("cell_avail_pct", "Cell Availability", "up", "%", "availability", "mean",
       tech=("GSM",)),
    _k("erlang", "Traffic (Erlang)", "info", "Erl", "traffic", "sum",
       tech=("GSM",)),
]

ALL_KPIS: list[KpiDef] = LTE_KPIS + UMTS_KPIS + GSM_KPIS

# name -> KpiDef, keyed per technology (some names such as rrc_setup_sr repeat)
KPI_INDEX: dict[tuple[str, str], KpiDef] = {}
for _d in ALL_KPIS:
    for _t in _d.tech:
        KPI_INDEX[(_t, _d.name)] = _d


def kpi_def(name: str, technology: str = "LTE") -> KpiDef | None:
    return KPI_INDEX.get((technology, name))


def kpis_for(technology: str) -> list[KpiDef]:
    return [d for d in ALL_KPIS if technology in d.tech]


def numeric_kpi_names(technology: str = "LTE") -> list[str]:
    return [d.name for d in kpis_for(technology)]
