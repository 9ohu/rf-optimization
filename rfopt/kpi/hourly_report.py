"""Daily 4G + 3G hourly-KPI report.

Takes the two operator exports the R5 team pulls every morning — the 4G
per-cell hourly file and the 3G per-NodeB hourly file — and turns them into
the workbook an optimisation engineer actually wants:

* the **hourly pivots** the team already works from (one sheet per KPI, a row
  per cell / NodeB, a column per hour),
* plus the reading of them: per-cell roll-ups, busy hour, and a ranked
  **action list** — what is wrong, how bad, the numbers that say so, and the
  next check to run.

The diagnosis itself is the existing engine (`rfopt.diagnosis`) over the
canonical KPI frame, so the Dashboard and this page call the same rules and
the same YAML thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from rfopt.diagnosis.engine import diagnose_frame
from rfopt.ingest.schema import kpi_def
from rfopt.kpi.analyze import aggregate, busy_hour_table, evaluate_thresholds
from rfopt.kpi.thresholds import ThresholdSet, load_thresholds

# canonical KPI -> sheet name. The first five are the sheets the team's own
# pivot workbook already carries, kept in that order on purpose.
PIVOTS_4G: list[tuple[str, str]] = [
    ("ul_rssi_dbm", "UL_Interference"),
    ("total_traffic_gb", "Data_Volume_GB"),
    ("cell_avail_pct", "LTE_Availability"),
    ("dl_prb_util", "DL_PRB_Utilization"),
    ("dl_user_thr_mbps", "DL_Throughput"),
    ("call_setup_sr", "CSSR"),
    ("erab_drop_rate", "Drop_Rate"),
    ("ul_prb_util", "UL_PRB_Utilization"),
    ("ul_user_thr_mbps", "UL_Throughput"),
    ("ho_sr", "HO_Success_Rate"),
    ("rrc_conn_users_avg", "Avg_Users"),
]
PIVOTS_3G: list[tuple[str, str]] = [
    ("cell_avail_pct", "3G_Availability"),
    ("ipmm_rtt_ms", "RTT_ms"),
    ("dl_flowctrl_drops", "DL_Drop_Count"),
]
# 12k cells x 34 hours is ~400k cells a sheet, so only the five the team
# already uses are written unless more are asked for.
DEFAULT_PIVOTS_4G = ["UL_Interference", "Data_Volume_GB", "LTE_Availability",
                     "DL_PRB_Utilization", "DL_Throughput"]

# the KPIs quoted on the summary sheet, in reading order
HEADLINE_4G = ["cell_avail_pct", "call_setup_sr", "erab_drop_rate",
               "dl_user_thr_mbps", "ul_user_thr_mbps", "dl_prb_util",
               "ul_prb_util", "ul_rssi_dbm", "ho_sr", "total_traffic_gb",
               "rrc_conn_users_avg"]
HEADLINE_3G = ["cell_avail_pct", "ipmm_rtt_ms", "dl_flowctrl_drops"]

# what to do about each problem the diagnosis engine can raise. Written as the
# next concrete check, not as a generic "investigate".
_ACTION: dict[str, str] = {
    "high_utilization":
        "Capacity: confirm the busy-hour PRB and user count, then load-balance "
        "to the co-sited L2100 / L2600 carrier (idle-mode priority + A4/A5), "
        "or plan a carrier / sector add. Re-tilt only if the cell is also "
        "over-shooting.",
    "congestion":
        "Capacity: check RRC rejects and PRB at the busy hour; load-balance to "
        "the co-sited carrier, review admission thresholds, then plan capacity.",
    "interference":
        "UL interference: sweep UL RSSI per PRB over the day. Rule out the "
        "site itself first (PIM / faulty RRU / loose jumper — check whether "
        "all sectors rise together), then look for an external source or a "
        "repeater in the sector.",
    "overshooting":
        "Overshoot: compare TA / distance with the inter-site distance, then "
        "tilt down or reduce power in steps of 1-2 degrees and re-measure. "
        "Check the neighbour list for far-away relations first.",
    "coverage":
        "Coverage: check RSRP / TA distribution and the antenna's real tilt "
        "and azimuth against the design. Consider up-tilt or a power / RS "
        "boost only after ruling out a hardware fault.",
    "poor_rsrp": "Coverage: verify antenna tilt / azimuth and feeder health.",
    "poor_rsrq":
        "Quality: pilot pollution — check overlapping dominant cells, tighten "
        "the worst overshooting neighbour, review PCI / mod3 collisions.",
    "low_throughput":
        "Throughput: split the cause — if PRB is high it is load, if PRB is "
        "low it is RF (CQI / SINR / interference) or transport. Check the "
        "Iub / S1 backhaul rate before touching RF.",
    "throughput":
        "Throughput: separate load from RF — high PRB means capacity, low PRB "
        "with poor CQI means coverage or interference.",
    "accessibility":
        "Accessibility: split the RRC / E-RAB failure counters. NoRadioRes = "
        "congestion, NoReply = UL coverage or interference, MME / TNL = core "
        "or transport. Check licence and admission thresholds.",
    "retainability":
        "Drops: split the abnormal-release causes. Radio = coverage or UL "
        "interference, HO failure = neighbour / mobility, MME / TNL = core or "
        "transport. Cross-check the drop hours against UL RSSI.",
    "handover":
        "Mobility: audit the failing relation (ANR / neighbour list), then "
        "check CIO, hysteresis and time-to-trigger. Look for a missing "
        "neighbour on the dominant overlapping cell.",
    "availability":
        "Availability: this is an outage, not an RF problem — pull the alarm "
        "history for the window, check transmission and site power, and "
        "escalate to O&M with the exact hours.",
    "traffic_imbalance":
        "Imbalance: compare the sibling sectors' azimuth, tilt and feeder "
        "connections — a swapped or tilted-down sector shows exactly this. "
        "Verify against the site's engineering parameters.",
    "antenna_tilt":
        "Antenna: verify the real tilt / azimuth against the design sheet and "
        "check RET feedback; a stuck RET reads correct in the parameters.",
}
_ACTION_3G: dict[str, str] = {
    "cell_avail_pct":
        "NodeB outage — pull alarms for the window, check transmission and "
        "site power, escalate to O&M with the exact hours.",
    "ipmm_rtt_ms":
        "Transport latency on the Iub — check the microwave / fibre path, "
        "look for errored seconds and queueing on the backhaul, and compare "
        "with the co-sited LTE S1 latency.",
    "dl_flowctrl_drops":
        "Iub DL flow control is dropping frames — the backhaul is the "
        "bottleneck, not the air interface. Check the Iub bandwidth profile "
        "and the transport utilisation at the busy hour before any RF change.",
}
_PROBLEM_3G = {"cell_avail_pct": "NodeB availability",
               "ipmm_rtt_ms": "Iub transport latency",
               "dl_flowctrl_drops": "Iub transport congestion"}


# --------------------------------------------------------------------------- #
def _label(kpi: str, tech: str = "LTE") -> str:
    d = kpi_def(kpi, tech)
    return d.label if d else kpi


def _unit(kpi: str, tech: str = "LTE") -> str:
    d = kpi_def(kpi, tech)
    return d.unit if d else ""


def _priority(severity: str, traffic_gb: float, busy_gb: float) -> str:
    """P1-P4, the same ladder the Dashboard worklist uses.

    Severity says how broken the cell is, traffic says how many customers are
    behind it — `busy_gb` is this report's own median cell, so the split keeps
    working whether the file holds 6 hours or a week.
    """
    busy = traffic_gb >= busy_gb
    if severity == "critical":
        return "P1" if busy else "P2"
    return "P3" if busy else "P4"


def hourly_pivot(df: pd.DataFrame, kpi: str, *, id_col: str = "cell_id",
                 extra_col: str | None = "duplex",
                 how: str = "mean") -> pd.DataFrame:
    """One row per cell / NodeB, one column per hour — the team's own layout."""
    if df.empty or kpi not in df.columns:
        return pd.DataFrame()
    work = df[df[kpi].notna()]
    if work.empty:
        return pd.DataFrame()
    piv = pd.pivot_table(work, index=id_col, columns="datetime", values=kpi,
                         aggfunc=how, observed=True)
    piv.columns = [pd.Timestamp(c).strftime("%m/%d %H:%M") for c in piv.columns]
    piv = piv.round(2).reset_index()
    if extra_col and extra_col in df.columns:
        side = (df.groupby(id_col, observed=True)[extra_col].first()
                .reset_index())
        piv = side.merge(piv, on=id_col, how="right")
        piv = piv[[id_col, extra_col] + [c for c in piv.columns
                                         if c not in (id_col, extra_col)]]
    return piv.rename(columns={id_col: "Cell" if id_col == "cell_id" else "NodeB"})


# --------------------------------------------------------------------------- #
@dataclass
class KpiAnalysis:
    """Everything the report needs for one technology."""
    tech: str
    hourly: pd.DataFrame                     # the canonical frame as loaded
    per_entity: pd.DataFrame = field(default_factory=pd.DataFrame)
    per_day: pd.DataFrame = field(default_factory=pd.DataFrame)
    per_site: pd.DataFrame = field(default_factory=pd.DataFrame)
    busy_hour: pd.DataFrame = field(default_factory=pd.DataFrame)
    network_profile: pd.DataFrame = field(default_factory=pd.DataFrame)
    findings: pd.DataFrame = field(default_factory=pd.DataFrame)
    headline: pd.DataFrame = field(default_factory=pd.DataFrame)
    meta: dict = field(default_factory=dict)

    @property
    def n_p1(self) -> int:
        if self.findings.empty or "Priority" not in self.findings:
            return 0
        return int((self.findings["Priority"] == "P1").sum())


def _network_profile(df: pd.DataFrame, kpis: list[str]) -> pd.DataFrame:
    """Network-wide hourly shape — the curve you read before anything else."""
    if df.empty:
        return pd.DataFrame()
    have = [k for k in kpis if k in df.columns]
    if not have:
        return pd.DataFrame()
    how = {k: ("sum" if (kpi_def(k, df["technology"].iloc[0]) or
                         kpi_def(k, "LTE")) and
               (kpi_def(k, df["technology"].iloc[0]) or
                kpi_def(k, "LTE")).aggregate == "sum" else "mean")
           for k in have}
    prof = df.groupby("datetime", observed=True).agg(how).round(3)
    prof.insert(0, "cells", df.groupby("datetime", observed=True).size())
    return prof.reset_index()


def _headline_table(per_entity: pd.DataFrame, kpis: list[str], tech: str,
                    ts: ThresholdSet) -> pd.DataFrame:
    rows = []
    for k in kpis:
        if k not in per_entity.columns:
            continue
        vals = pd.to_numeric(per_entity[k], errors="coerce").dropna()
        if vals.empty:
            continue
        rule = ts.rule(k)
        d = kpi_def(k, tech)
        agg_sum = bool(d and d.aggregate == "sum")
        value = float(vals.sum() if agg_sum else vals.mean())
        worst = float(vals.max() if (rule and rule.direction == "down")
                      else vals.min())
        status = "—"
        if rule is not None and not agg_sum:
            status = rule.evaluate(value).label
        rows.append({
            "KPI": _label(k, tech), "Unit": _unit(k, tech),
            "Network": round(value, 2),
            "Worst cell": round(worst, 2),
            "Warning": rule.warning if rule else None,
            "Critical": rule.critical if rule else None,
            "Status": status,
            "Cells below target": int(
                ((vals > rule.warning) if rule and rule.direction == "down"
                 else (vals < rule.warning)).sum())
            if (rule and rule.warning is not None) else 0,
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
def analyse_4g(df: pd.DataFrame, *, thresholds: ThresholdSet | None = None,
               env_kind: str = "urban") -> KpiAnalysis:
    """Roll the 4G hourly frame up and diagnose every cell."""
    ts = thresholds or load_thresholds("LTE")
    if df.empty:
        return KpiAnalysis(tech="LTE", hourly=df)

    per_cell = aggregate(df, "cell", "all", "LTE")
    per_day = aggregate(df, "cell", "day", "LTE")
    per_site = aggregate(df, "site", "day", "LTE")
    bh = busy_hour_table(df, "cell")
    prof = _network_profile(df, HEADLINE_4G)

    # busy-hour capacity view: the daily mean hides a cell that saturates for
    # three hours every evening, which is exactly the cell worth fixing.
    hourly_cell = df
    for col, peak in (("dl_prb_util", "bh_dl_prb_util"),
                      ("ul_prb_util", "bh_ul_prb_util"),
                      ("rrc_conn_users_avg", "bh_users")):
        if col in hourly_cell.columns:
            top = (hourly_cell.groupby("cell_id", observed=True)[col]
                   .quantile(0.95).round(2).rename(peak))
            per_cell = per_cell.merge(top, on="cell_id", how="left")

    diags = diagnose_frame(per_cell, ts, level="cell", env_kind=env_kind)
    busy_gb = float(pd.to_numeric(per_cell.get("total_traffic_gb"),
                                  errors="coerce").median() or 0.0)
    worst_hour = _worst_hours(df)
    findings = _findings_table(diags, per_cell, worst_hour, busy_gb)
    findings = _add_busy_hour_findings(findings, per_cell, df, ts, worst_hour,
                                       busy_gb)

    headline = _headline_table(per_cell, HEADLINE_4G, "LTE", ts)
    meta = {
        "cells": int(per_cell["cell_id"].nunique()) if len(per_cell) else 0,
        "sites": int(df["site_id"].nunique()),
        "hours": int(df["datetime"].nunique()),
        "from": str(df["datetime"].min()), "to": str(df["datetime"].max()),
        "traffic_gb": float(df.get("total_traffic_gb", pd.Series(dtype=float))
                            .sum()),
    }
    return KpiAnalysis(tech="LTE", hourly=df, per_entity=per_cell,
                       per_day=per_day, per_site=per_site, busy_hour=bh,
                       network_profile=prof, findings=findings,
                       headline=headline, meta=meta)


_BH_KPIS = {"dl_prb_util": "bh_dl_prb_util", "ul_prb_util": "bh_ul_prb_util",
            "rrc_conn_users_avg": "bh_users"}


def _add_busy_hour_findings(findings: pd.DataFrame, per_cell: pd.DataFrame,
                            df: pd.DataFrame, ts: ThresholdSet,
                            worst_hour: dict, busy_gb: float) -> pd.DataFrame:
    """Catch the cell that only saturates in the evening.

    The daily mean of a cell sitting at 95% PRB for five hours and 30% for the
    rest is ~46% — under every threshold, while the customers on it spend the
    whole evening queueing. So the capacity KPIs get judged a second time on
    their busy-hour (95th-percentile) value.
    """
    have = {k: v for k, v in _BH_KPIS.items() if v in per_cell.columns}
    if not have:
        return findings
    bh = per_cell.copy()
    for kpi, col in have.items():
        bh[kpi] = bh[col]
    breaches = evaluate_thresholds(bh, ts, level="cell", period="busy hour",
                                   only_kpis=list(have))
    daily = per_cell.set_index("cell_id")
    rows = []
    for b in breaches:
        mean_val = daily[b.kpi].get(b.entity_id, float("nan"))
        if mean_val == mean_val and mean_val >= b.value:
            continue                     # the daily rule already caught this
        rows.append({
            "Priority": _priority(b.severity.name.lower(), b.traffic_gb,
                                  busy_gb),
            "Cell": b.entity_id, "Site": b.site_id,
            "Problem": "Busy-hour congestion",
            "Class": "high_utilization",
            "Severity": b.severity.label,
            "Confidence": 0.8,
            "Traffic GB": round(b.traffic_gb, 2),
            "Worst hour": worst_hour.get("dl_prb_util", {}).get(b.entity_id, ""),
            "KPIs": f"{_label(b.kpi)} {b.value:,.1f} at the busy hour",
            "Evidence": (f"{_label(b.kpi)} reaches {b.value:,.1f}{b.unit} in "
                         f"the busy hour against a {mean_val:,.1f}{b.unit} "
                         f"daily mean (threshold {b.threshold:g})."),
            "Recommended action": _ACTION["high_utilization"],
        })
    if not rows:
        return findings
    out = pd.concat([findings, pd.DataFrame(rows)], ignore_index=True) \
        if findings is not None and not findings.empty else pd.DataFrame(rows)
    order = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
    return (out.assign(_o=out["Priority"].map(order))
            .sort_values(["_o", "Traffic GB"], ascending=[True, False])
            .drop_duplicates(["Cell", "Class"], keep="first")
            .drop(columns="_o").reset_index(drop=True))


# problem class -> (KPI, which end of it is the bad one)
_WORST_BY_CLASS = {
    "availability": ("cell_avail_pct", "min"),
    "high_utilization": ("dl_prb_util", "max"),
    "congestion": ("dl_prb_util", "max"),
    "interference": ("ul_rssi_dbm", "max"),
    "retainability": ("erab_drop_rate", "max"),
    "accessibility": ("call_setup_sr", "min"),
    "handover": ("ho_sr", "min"),
    "low_throughput": ("dl_user_thr_mbps", "min"),
    "throughput": ("dl_user_thr_mbps", "min"),
    "traffic_imbalance": ("total_traffic_gb", "max"),
}


def _worst_hours(df: pd.DataFrame) -> dict[str, dict[str, str]]:
    """Per KPI, the hour each cell looked worst — so a finding can point at the
    window to open the counters on, not just at the day."""
    out: dict[str, dict[str, str]] = {}
    if df.empty or "datetime" not in df.columns:
        return out
    for kpi, end in dict(_WORST_BY_CLASS.values()).items():
        if kpi not in df.columns:
            continue
        s = df[["cell_id", "datetime", kpi]].dropna()
        if s.empty:
            continue
        g = s.groupby("cell_id", observed=True)[kpi]
        idx = g.idxmax() if end == "max" else g.idxmin()
        when = s.loc[idx.values, "datetime"].dt.strftime("%m/%d %H:%M")
        out[kpi] = dict(zip(idx.index.astype(str), when))
    return out


def _kpi_bit(kpi: str, value) -> str:
    """One "<KPI> <value>" chunk — the engine's kpi dict also holds ids and
    other text, which simply don't belong in the numbers column."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    if not np.isfinite(v) or kpi_def(kpi, "LTE") is None:
        return ""
    return f"{_label(kpi)} {v:,.2f}"


def _findings_table(diags: list, per_entity: pd.DataFrame,
                    worst_hour: dict[str, dict[str, str]],
                    busy_gb: float) -> pd.DataFrame:
    if not diags:
        return pd.DataFrame()
    site_of = {}
    if "cell_id" in per_entity.columns:
        site_of = dict(zip(per_entity["cell_id"].astype(str),
                           per_entity.get("site_id", "").astype(str)))
    rows = []
    for d in diags:
        # only the KPIs this diagnosis actually leans on — the whole row would
        # be 13 numbers wide and say nothing
        keys = list(dict.fromkeys(d.related_kpis or ["total_traffic_gb"]))[:5]
        kpi_bits = "  ".join(b for b in (_kpi_bit(k, d.kpis.get(k))
                                         for k in keys) if b)
        wk = _WORST_BY_CLASS.get(d.problem_class, ("total_traffic_gb", "max"))[0]
        rows.append({
            "Priority": _priority(d.severity, d.traffic_gb, busy_gb),
            "Cell": d.entity_id,
            "Site": d.site_id or site_of.get(str(d.entity_id), ""),
            "Problem": d.title,
            "Class": d.problem_class,
            "Severity": d.severity.title(),
            "Confidence": round(d.confidence, 2),
            "Traffic GB": round(d.traffic_gb, 2),
            "Worst hour": worst_hour.get(wk, {}).get(str(d.entity_id), ""),
            "KPIs": kpi_bits,
            "Evidence": " | ".join(d.evidence),
            "Recommended action": _ACTION.get(d.problem_class,
                                              "Review the cell's KPI trend "
                                              "and the site's parameters."),
        })
    out = pd.DataFrame(rows)
    order = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
    return (out.assign(_o=out["Priority"].map(order))
            .sort_values(["_o", "Traffic GB"], ascending=[True, False])
            .drop(columns="_o").reset_index(drop=True))


# --------------------------------------------------------------------------- #
def analyse_3g(df: pd.DataFrame,
               *, thresholds: ThresholdSet | None = None) -> KpiAnalysis:
    """The 3G export is per NodeB and carries three KPIs — availability plus
    two transport signals — so it gets its own, simpler reading."""
    ts = thresholds or load_thresholds("UMTS")
    if df.empty:
        return KpiAnalysis(tech="UMTS", hourly=df)

    per_nb = aggregate(df, "site", "all", "UMTS")
    per_day = aggregate(df, "site", "day", "UMTS")
    prof = _network_profile(df, HEADLINE_3G)

    breaches = evaluate_thresholds(per_day, ts, level="site", period="day")
    name_of = dict(zip(df["site_id"].astype(str), df["cell_id"].astype(str)))
    rnc_of = dict(zip(df["site_id"].astype(str), df["rnc"].astype(str))) \
        if "rnc" in df.columns else {}

    rows = []
    for b in breaches:
        sid = str(b.site_id or b.entity_id)
        hours = _bad_hours(df, sid, b.kpi, b.threshold, b.direction, b.when)
        rows.append({
            "Priority": "P1" if b.severity.name == "CRITICAL" else "P3",
            "NodeB": name_of.get(sid, sid),
            "Site": sid,
            "RNC": rnc_of.get(sid, ""),
            "Day": str(b.when)[:10],
            "Problem": _PROBLEM_3G.get(b.kpi, _label(b.kpi, "UMTS")),
            "Severity": b.severity.label,
            "Value": round(b.value, 2),
            "Target": b.threshold,
            "Unit": b.unit or _unit(b.kpi, "UMTS"),
            "Worst hours": hours,
            "Recommended action": _ACTION_3G.get(
                b.kpi, "Check the NodeB's alarms and transport."),
        })
    findings = pd.DataFrame(rows)
    if not findings.empty:
        order = {"P1": 0, "P3": 1}
        findings = (findings.assign(_o=findings["Priority"].map(order))
                    .sort_values(["_o", "Value"],
                                 ascending=[True, False])
                    .drop(columns="_o").reset_index(drop=True))

    headline = _headline_table(per_nb, HEADLINE_3G, "UMTS", ts)
    meta = {
        "nodebs": int(df["site_id"].nunique()),
        "hours": int(df["datetime"].nunique()),
        "from": str(df["datetime"].min()), "to": str(df["datetime"].max()),
        "rncs": int(df["rnc"].nunique()) if "rnc" in df.columns else 0,
    }
    return KpiAnalysis(tech="UMTS", hourly=df, per_entity=per_nb,
                       per_day=per_day, network_profile=prof,
                       findings=findings, headline=headline, meta=meta)


def _bad_hours(df: pd.DataFrame, site_id: str, kpi: str, threshold: float,
               direction: str, day: str, cap: int = 4) -> str:
    """The hours that actually broke the threshold, so the engineer can open
    the alarm log on the right window instead of the whole day."""
    if kpi not in df.columns or not np.isfinite(threshold):
        return ""
    d = df[(df["site_id"].astype(str) == site_id) & df[kpi].notna()]
    if day and len(day) >= 10:
        d = d[d["datetime"].dt.strftime("%Y-%m-%d") == day[:10]]
    bad = d[d[kpi] > threshold] if direction == "down" else d[d[kpi] < threshold]
    if bad.empty:
        return ""
    hrs = sorted(bad["datetime"].dt.strftime("%H:00").unique())
    txt = ", ".join(hrs[:cap])
    return txt + (f" (+{len(hrs) - cap} more)" if len(hrs) > cap else "")


# --------------------------------------------------------------------------- #
# workbook
# --------------------------------------------------------------------------- #
_HDR_FILL = "#305496"
_SEV_FILL = {"P1": "#FFC7CE", "P2": "#FFD9A5", "P3": "#FFF2CC", "P4": "#E2EFDA",
             "Critical": "#FFC7CE", "Warning": "#FFEB9C", "OK": "#E2EFDA"}


def _write_sheet(xw, df: pd.DataFrame, sheet: str, *, freeze: tuple = (1, 0),
                 shade: str | None = None, heat: bool = False) -> None:
    """One sheet, formatted: styled header, frozen panes, filter, and — for the
    hourly pivots — a red-to-green heat map so the bad hours jump out."""
    if df is None or df.empty:
        df = pd.DataFrame({"info": ["nothing to report"]})
    sheet = sheet[:31]
    df.to_excel(xw, sheet_name=sheet, index=False)
    wb, ws = xw.book, xw.sheets[sheet]

    hdr = wb.add_format({"bold": True, "font_color": "white", "font_size": 10,
                         "bg_color": _HDR_FILL, "align": "center",
                         "valign": "vcenter", "text_wrap": True, "border": 1})
    for j, col in enumerate(df.columns):
        ws.write(0, j, str(col), hdr)
    ws.set_row(0, 30)
    ws.freeze_panes(*freeze)
    if not heat:
        ws.autofilter(0, 0, len(df), max(len(df.columns) - 1, 0))

    for j, col in enumerate(df.columns):
        if heat and j > 1:
            width = 11
        else:
            body = df[col].astype(str).str.len()
            width = min(max(int(body.quantile(0.92) if len(body) else 10),
                            len(str(col)), 8) + 2, 58)
        ws.set_column(j, j, width)

    if shade and shade in df.columns:
        j = list(df.columns).index(shade)
        for val, colour in _SEV_FILL.items():
            ws.conditional_format(1, j, len(df), j, {
                "type": "cell", "criteria": "==", "value": f'"{val}"',
                "format": wb.add_format({"bg_color": colour})})

    if heat and len(df) and len(df.columns) > 2:
        ws.conditional_format(1, 2, len(df), len(df.columns) - 1, {
            "type": "3_color_scale", "min_type": "percentile", "min_value": 5,
            "mid_type": "percentile", "mid_value": 50,
            "max_type": "percentile", "max_value": 95,
            "min_color": "#F8696B", "mid_color": "#FFEB84",
            "max_color": "#63BE7B"})


def _summary_frame(res4: KpiAnalysis, res3: KpiAnalysis, meta: dict) -> pd.DataFrame:
    rows = [("Report generated", pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")),
            ("Region", meta.get("region", "R5"))]
    if res4.meta:
        rows += [("", ""), ("4G file", meta.get("file_4g", "")),
                 ("4G window", f"{res4.meta['from']} -> {res4.meta['to']}"),
                 ("4G hours", res4.meta["hours"]),
                 ("4G cells", res4.meta["cells"]),
                 ("4G sites", res4.meta["sites"]),
                 ("4G data volume (GB)", round(res4.meta["traffic_gb"], 1)),
                 ("4G cells to action (critical)",
                  len(_sev(res4.findings, "Critical"))),
                 ("4G P1 (critical on a busy cell)", res4.n_p1),
                 ("4G watchlist (warning)", len(_sev(res4.findings, "Warning")))]
        rows += [(f"4G — {k}", int(v)) for k, v in _class_counts(res4).items()]
    if res3.meta:
        rows += [("", ""), ("3G file", meta.get("file_3g", "")),
                 ("3G window", f"{res3.meta['from']} -> {res3.meta['to']}"),
                 ("3G hours", res3.meta["hours"]),
                 ("3G NodeBs", res3.meta["nodebs"]),
                 ("3G NodeBs to action (critical)",
                  len(_sev(res3.findings, "Critical"))),
                 ("3G watchlist (warning)", len(_sev(res3.findings, "Warning")))]
        rows += [(f"3G — {k}", int(v)) for k, v in _class_counts(res3).items()]
    return pd.DataFrame(rows, columns=["Item", "Value"])


def _class_counts(res: KpiAnalysis) -> dict:
    col = "Class" if "Class" in res.findings.columns else "Problem"
    if res.findings.empty or col not in res.findings.columns:
        return {}
    return res.findings[col].value_counts().to_dict()


def build_kpi_workbook(out_path: str | Path, *, res4: KpiAnalysis | None = None,
                       res3: KpiAnalysis | None = None,
                       meta: dict | None = None,
                       pivots_4g: list[str] | None = None,
                       pivots_3g: list[str] | None = None) -> Path:
    """Write the whole report to one workbook and return its path."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    res4 = res4 or KpiAnalysis(tech="LTE", hourly=pd.DataFrame())
    res3 = res3 or KpiAnalysis(tech="UMTS", hourly=pd.DataFrame())
    meta = meta or {}
    want4 = set(pivots_4g) if pivots_4g is not None else set(DEFAULT_PIVOTS_4G)
    want3 = set(pivots_3g) if pivots_3g is not None else {s for _, s in PIVOTS_3G}

    with pd.ExcelWriter(out_path, engine="xlsxwriter") as xw:
        _write_sheet(xw, _summary_frame(res4, res3, meta), "Summary")
        if not res4.headline.empty:
            _write_sheet(xw, res4.headline, "4G Network KPIs", shade="Status")
        # criticals are the day's work; warnings are the watchlist behind it
        _write_sheet(xw, _sev(res4.findings, "Critical"), "4G Action List",
                     shade="Priority")
        if not _sev(res4.findings, "Warning").empty:
            _write_sheet(xw, _sev(res4.findings, "Warning"), "4G Watchlist",
                         shade="Priority")
        if not res4.per_entity.empty:
            _write_sheet(xw, _round(res4.per_entity), "4G Cell Summary")
        if not res4.per_day.empty:
            _write_sheet(xw, _round(res4.per_day), "4G Cell by Day")
        if not res4.per_site.empty:
            _write_sheet(xw, _round(res4.per_site), "4G Site by Day")
        if not res4.busy_hour.empty:
            _write_sheet(xw, _round(res4.busy_hour), "4G Busy Hour")
        if not res4.network_profile.empty:
            _write_sheet(xw, _round(res4.network_profile), "4G Hourly Profile")

        if not res3.headline.empty:
            _write_sheet(xw, res3.headline, "3G Network KPIs", shade="Status")
        _write_sheet(xw, _sev(res3.findings, "Critical"), "3G Action List",
                     shade="Priority")
        if not _sev(res3.findings, "Warning").empty:
            _write_sheet(xw, _sev(res3.findings, "Warning"), "3G Watchlist",
                         shade="Priority")
        if not res3.per_entity.empty:
            _write_sheet(xw, _round(res3.per_entity), "3G NodeB Summary")
        if not res3.per_day.empty:
            _write_sheet(xw, _round(res3.per_day), "3G NodeB by Day")

        for kpi, sheet in PIVOTS_4G:
            if sheet not in want4:
                continue
            piv = hourly_pivot(res4.hourly, kpi, id_col="cell_id",
                               extra_col="duplex")
            if not piv.empty:
                _write_sheet(xw, piv, sheet, freeze=(1, 2), heat=True)
        for kpi, sheet in PIVOTS_3G:
            if sheet not in want3:
                continue
            piv = hourly_pivot(res3.hourly, kpi, id_col="cell_id",
                               extra_col="rnc",
                               how="sum" if kpi == "dl_flowctrl_drops" else "mean")
            if not piv.empty:
                _write_sheet(xw, piv, sheet, freeze=(1, 2), heat=True)
    return out_path


def _sev(findings: pd.DataFrame, severity: str) -> pd.DataFrame:
    if findings is None or findings.empty or "Severity" not in findings:
        return pd.DataFrame()
    return findings[findings["Severity"] == severity].reset_index(drop=True)


def _round(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_float_dtype(out[c]):
            out[c] = out[c].round(2)
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%Y-%m-%d %H:%M")
    return out
