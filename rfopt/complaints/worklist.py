"""Process a daily complaint worklist (site id + city only, no coordinates).

Input: the operator's "Target" export - Ticket ID / HPSM Incident ID / City /
Site ID / Problem Time / MSISDN / Affected Services / Diagnostic Comment.

For each ticket:
  * run the per-site parameter audit (rfopt.complaints.site_audit)
  * parse the free-text comment for signals (planned site, RET escalation,
    "not connected" = non-RF, indoor, ...)
  * optionally attach how many historical complaints the site has had
  * emit a ranked "what to check first" line

Output: a tidy DataFrame + an Excel workbook.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from rfopt.complaints.site_audit import SiteAudit, audit_sites
from rfopt.ingest._io import read_excel

_COL = {
    "ticket_id": ["ticket id", "cc id", "cc number"],
    "hpsm_id": ["hpsm incident id", "incident id", "hpsm id"],
    "site_id": ["site id(sd check_site_id)", "site id(sd check)", "site id",
                "serving site", "site"],
    "city": ["city", "governorate", "gouvernorate"],
    "affected_service": ["affected services", "affected service", "service",
                         "product type"],
    "problem_time": ["problem time", "call time", "fault time"],
    "create_time": ["created at", "create time", "createtime"],
    "sla_status": ["sla status", "sla"],
    "sla_target": ["sla target time"],
    "msisdn": ["msisdn", "number", "b number"],
    "opened_by": ["opened by", "operator"],
    "user": ["user", "assignee", "owner"],
    "reopen": ["reopen count", "reopen", "reopened", "reopen number", "reopen times"],
    "engineer": ["eng", "engineer", "assigned engineer", "assigned to"],
    "comment": ["diagnostic comment(incident diagnostic)", "diagnostic comment",
                "field comment", "remarks", "brief description"],
    "sub_district": ["sub district", "subdistrict", "district", "area"],
    # read for the ticket worksheet (Complaints · Ticket Details)
    "ticket_kind": ["ticket type"],
    "is_cmc": ["is cmc"],
    "district": ["district"],
}

_C_PLANNED = re.compile(r"\b(planned? site|new site|q[1-4]\s*20\d\d|will be on ?air|"
                        r"nomination|tower)\b", re.I)
_C_RET = re.compile(r"\b(ret|electrical tilt|tilt|rcu|ale)\b", re.I)
_C_ESCALATED = re.compile(r"\b(escalat|fme|raised to|forwarded to|hw team|swap)\b", re.I)
_C_NOTRF = re.compile(r"\b(not connected|no issue|ps side|core side|sim|device|"
                      r"handset|barred|provision|charging|package|balance|"
                      r"switched? off)\b", re.I)
_C_INDOOR = re.compile(r"\b(indoor|in-?building|inside|basement|home)\b", re.I)
_C_CONG = re.compile(r"\b(congest|utiliz|utilis|prb|flow control|high traffic|"
                     r"overload|capacity)\b", re.I)
_C_ALARM = re.compile(r"\b(alarm|outage|down|not on ?air|vswr|hardware|board|"
                      r"transmission|power fail)\b", re.I)
_C_INTERF = re.compile(r"\b(interfe|rtwp|jammer|jamming|noise)\b", re.I)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def read_source(path_or_buf, *, sheet=0) -> pd.DataFrame:
    """The ticket sheet as the file has it: every column, every value as text."""
    name = getattr(path_or_buf, "name", str(path_or_buf))
    if hasattr(path_or_buf, "seek"):
        path_or_buf.seek(0)
    if Path(name).suffix.lower() in (".csv", ".txt"):
        raw = pd.read_csv(path_or_buf, dtype=str, keep_default_na=False)
    else:
        raw = read_excel(path_or_buf, sheet_name=sheet, dtype=str)
    raw.columns = [str(c).strip() for c in raw.columns]
    return raw.reset_index(drop=True)


def load_worklist(path_or_buf, *, sheet=0) -> pd.DataFrame:
    raw = read_source(path_or_buf, sheet=sheet)
    nmap = {c: _norm(c) for c in raw.columns}
    out = pd.DataFrame(index=raw.index)
    for canon, cands in _COL.items():
        hit = next((c for c, n in nmap.items() if n in cands), None)
        if hit is None:
            hit = next((c for c, n in nmap.items()
                        if any(k in n for k in cands)), None)
        if hit is not None:
            out[canon] = raw[hit].astype(str).str.strip()
    # without a District column of its own, "district" only found "Sub District"
    if "district" in out and "sub_district" in out and out["district"].equals(out["sub_district"]):
        out = out.drop(columns="district")
    for tc in ("problem_time", "create_time"):
        if tc in out:
            out[tc] = pd.to_datetime(out[tc], errors="coerce", utc=True)
    if "site_id" in out:
        out["site_id"] = (out["site_id"].str.upper().str.strip()
                          .str.extract(r"([A-Z]{2,4}\d{3,6})")[0])
    # the ticket's row in the sheet, so every original column can be read back
    out["_row"] = np.arange(len(raw))
    out.attrs["_src_cols"] = list(raw.columns)
    return out.reset_index(drop=True)


# --------------------------------------------------------------------------- #
@dataclass
class TicketResult:
    ticket_id: str = ""
    hpsm_id: str = ""
    site_id: str = ""
    city: str = ""
    affected_service: str = ""
    problem_time: str = ""
    sla_status: str = ""
    comment: str = ""
    site_found: bool = False
    enodeb_name: str = ""
    n_sectors: int = 0
    bands: str = ""
    height_m: float | None = None
    tilt_summary: str = ""
    isolated: bool = False
    nearest_site_m: float | None = None
    param_severity: str = "n/a"
    param_flags: str = ""
    comment_signals: str = ""
    kpi_status: str = "no kpi"
    kpi_window: str = ""
    kpi_verdict: str = ""
    kpi_evidence: str = ""
    kpi_severe: bool = False
    likely_cause: str = ""
    confidence: float = 0.3
    next_check: str = ""
    site_complaint_history: int | None = None

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["confidence"] = round(self.confidence, 2)
        if self.nearest_site_m is not None:
            d["nearest_site_m"] = round(self.nearest_site_m)
        return d


# --------------------------------------------------------------------------- #
class KpiContext:
    """Pre-computed serving-site KPI, ready to look up per ticket."""

    def __init__(self, kpi_df: pd.DataFrame):
        self.raw = kpi_df
        self.window = ""
        self.site_stats: dict[str, dict] = {}
        if kpi_df is None or kpi_df.empty:
            return
        d = kpi_df.copy()
        dr0, dr1 = d["datetime"].min(), d["datetime"].max()
        hrs = sorted(d["datetime"].dt.hour.unique())
        self.window = (f"{dr0:%Y-%m-%d} {hrs[0]:02d}:00-{hrs[-1]:02d}:00"
                       + (f" (+{d['datetime'].dt.date.nunique()-1} more days)"
                          if d['datetime'].dt.date.nunique() > 1 else ""))
        worst = ["dl_prb_util", "ul_prb_util", "rrc_conn_users_avg",
                 "erab_drop_rate", "ul_rssi_dbm"]
        mean = ["call_setup_sr", "ho_sr", "inter_freq_ho_sr", "intra_freq_ho_sr",
                "dl_user_thr_mbps", "ul_user_thr_mbps", "cell_avail_pct"]
        worst = [c for c in worst if c in d.columns]
        mean = [c for c in mean if c in d.columns]
        kcols = worst + mean
        self.pctl = {c: {"p95": float(d[c].quantile(0.95))} for c in kcols}

        d = d.assign(_sec=d["sector"].fillna(0).astype(int))
        agg_spec = {c: "max" for c in worst}
        agg_spec.update({c: "mean" for c in mean})
        if "cell_avail_pct" in d.columns:
            agg_spec["cell_avail_pct"] = "min"
        if "total_traffic_gb" in d.columns:
            agg_spec["total_traffic_gb"] = "sum"
        g = d.groupby(["site_id", "_sec"], sort=False).agg(agg_spec)
        g["hours"] = d.groupby(["site_id", "_sec"], sort=False).size()
        for (site, sec), row in g.iterrows():
            rec = self.site_stats.setdefault(site, {"sectors": {}})
            s = {"traffic_gb": float(row.get("total_traffic_gb", 0) or 0),
                 "hours": int(row["hours"])}
            for c in kcols:
                v = row.get(c)
                if v is not None and v == v:
                    s[c] = float(v)
            rec["sectors"][int(sec)] = s

    @property
    def available(self) -> bool:
        return bool(self.site_stats)

    def verdict(self, site_id: str) -> tuple[str, str, str, bool]:
        """Return (status, verdict_category, evidence, severe) for a site."""
        rec = self.site_stats.get(site_id)
        if not rec or not rec["sectors"]:
            return ("site not in KPI", "", "", False)
        ev: list[str] = []
        cats: list[str] = []
        severe = False
        for sec, s in sorted(rec["sectors"].items()):
            tag = f"S{sec}"
            if s.get("cell_avail_pct", 100) < 99.5:
                cats.append("availability")
                severe = severe or s["cell_avail_pct"] < 95
                ev.append(f"{tag} availability {s['cell_avail_pct']:.1f}%")
            if s.get("traffic_gb", 0) >= 0.3:
                if s.get("erab_drop_rate", 0) >= 2.0:
                    cats.append("retainability")
                    severe = severe or s["erab_drop_rate"] >= 4
                    ev.append(f"{tag} drop {s['erab_drop_rate']:.1f}% (worst hr)")
                if s.get("call_setup_sr", 100) < 97.0:
                    cats.append("accessibility")
                    severe = severe or s["call_setup_sr"] < 93
                    ev.append(f"{tag} CSSR {s['call_setup_sr']:.1f}%")
                prb = s.get("dl_prb_util", 0)
                if prb >= 80:
                    cats.append("congestion")
                    severe = severe or prb >= 92
                    ev.append(f"{tag} DL PRB {prb:.0f}% (worst hr)")
                elif prb >= 65:
                    ev.append(f"{tag} DL PRB {prb:.0f}% (elevated)")
                if s.get("ho_sr", 100) < 95:
                    cats.append("handover")
                    ev.append(f"{tag} HO SR {s['ho_sr']:.1f}%")
                thr = s.get("dl_user_thr_mbps")
                if thr is not None and thr < 5 and prb < 60:
                    cats.append("low_throughput")
                    ev.append(f"{tag} DL thr {thr:.1f} Mbps at low load")
            ul = s.get("ul_rssi_dbm")
            p95 = self.pctl.get("ul_rssi_dbm", {}).get("p95", -100)
            if ul is not None and ul > -100 and ul >= p95:
                cats.append("interference")
                severe = severe or ul > -90
                ev.append(f"{tag} UL RSSI {ul:.0f} dBm (~{ul + 118:.0f} dB "
                          f"above floor, top 5% of R5)")
        if not cats:
            return ("kpi clean (window)", "", "; ".join(ev[:4])
                    or "no threshold breach in the available hours", False)
        from collections import Counter
        top = Counter(cats).most_common(1)[0][0]
        return ("kpi issue", top, "; ".join(ev[:6]), severe)


def _comment_signals(text: str) -> list[str]:
    if not text or text.lower() in ("nan", "none", ""):
        return []
    sig = []
    if _C_NOTRF.search(text):
        sig.append("non_rf")
    if _C_PLANNED.search(text):
        sig.append("planned_site_mentioned")
    if _C_RET.search(text) and _C_ESCALATED.search(text):
        sig.append("ret_issue_escalated")
    elif _C_RET.search(text):
        sig.append("ret_mentioned")
    if _C_ALARM.search(text):
        sig.append("alarm_or_outage")
    if _C_INTERF.search(text):
        sig.append("interference_mentioned")
    if _C_INDOOR.search(text):
        sig.append("indoor_mentioned")
    if _C_CONG.search(text):
        sig.append("congestion_mentioned")
    return sig


_KPI_NEXT = {
    "availability": "KPI shows an availability gap - check active alarms / TX / "
                   "power for the flagged sector and raise to field if not "
                   "self-recovered.",
    "retainability": "KPI shows a high drop rate - check UL interference, "
                    "overshoot and neighbour completeness on the flagged "
                    "sector; pull the release-cause breakdown.",
    "accessibility": "KPI shows low CSSR - check PRACH / admission / UL "
                    "interference and PDCCH load on the flagged sector.",
    "congestion": "KPI confirms high PRB / load - this is capacity: add a "
                 "carrier / enable CA, or load-balance to a co-sited layer.",
    "handover": "KPI shows low HO success - audit the neighbour list and the "
               "A3/TTT settings for the flagged sector.",
    "interference": "KPI shows high UL RSSI (top 5% of R5) - run an interference "
                   "hunt / PIM & VSWR check on the flagged sector.",
    "low_throughput": "KPI shows low throughput at low load - check CA / MIMO / "
                     "256QAM feature activation and transport for the sector.",
}


def _decide(tr: TicketResult, audit: SiteAudit | None) -> None:
    sigs = tr.comment_signals.split(",") if tr.comment_signals else []

    # measured KPI beats inferred parameters
    if tr.kpi_verdict:
        tr.likely_cause = tr.kpi_verdict
        tr.confidence = 0.65
        tr.next_check = _KPI_NEXT.get(tr.kpi_verdict, "Investigate the KPI "
                                     "breach on the flagged sector.")
        if "non_rf" in sigs and tr.kpi_verdict not in ("availability",):
            tr.likely_cause, tr.confidence = "not_rf", 0.5
            tr.next_check = ("Comment says non-RF but the site also has a KPI "
                             "issue (" + tr.kpi_evidence[:80] + ") - verify "
                             "which the customer hit.")
        return

    if "non_rf" in sigs:
        tr.likely_cause, tr.confidence = "not_rf", 0.6
        tr.next_check = ("Comment indicates non-RF (device / core / SIM / not "
                         "connected). Close as non-RF or return to CC.")
        return
    if "alarm_or_outage" in sigs or (audit and any(
            f.code == "site_not_in_params" for f in audit.flags)):
        tr.likely_cause, tr.confidence = "availability", 0.5
        tr.next_check = ("Check active alarms / cell availability for the site "
                         "at the problem time; if a hardware/TX fault, raise to "
                         "field/FME.")
        return
    if "ret_issue_escalated" in sigs:
        tr.likely_cause, tr.confidence = "far_coverage", 0.55
        tr.next_check = ("RET fault already escalated - track the FME/HW ticket; "
                         "once RET is restored, re-measure RSRP in the area.")
        return

    if audit is None or not audit.site_found_ok():
        tr.likely_cause, tr.confidence = "insufficient_data", 0.25
        tr.next_check = (
            "No site ID on the ticket - get the serving cell from the CGI / a "
            "trace, then re-run." if not tr.site_id or tr.site_id == "(missing)"
            else "Site ID not found in the R5 LTE parameter file - check the ID "
            "/ that it is an active LTE site, then check KPI + neighbours.")
        return

    real_flags = [f for f in audit.flags if f.severity != "info"]
    if real_flags:
        top = sorted(real_flags, key=lambda f: -{"warning": 1,
                                                 "critical": 2}[f.severity])[0]
        tr.likely_cause = top.points_to
        tr.confidence = {"critical": 0.55, "warning": 0.45}[top.severity]
        tr.next_check = _NEXT.get(top.code, "Review the flagged parameter and "
                                  "confirm with KPI / a drive test.")
        if "congestion_mentioned" in sigs and any(
                f.points_to == "congestion" for f in audit.flags):
            tr.likely_cause, tr.confidence = "congestion", 0.55
            tr.next_check = ("Comment + narrow-band layout both point to load - "
                             "check busy-hour PRB / active users on the serving "
                             "sector; add carrier or load-balance.")
        if "indoor_mentioned" in sigs:
            tr.likely_cause = "indoor"
            tr.next_check = ("Indoor complaint - a small cell / repeater beats "
                             "macro tuning; " + tr.next_check)
    else:
        info_note = ""
        if audit.flags:
            info_note = (" Minor notes: "
                         + "; ".join(f.code for f in audit.flags) + ".")
        svc = tr.affected_service.lower()
        if "coverage" in svc:
            tr.likely_cause, tr.confidence = "far_coverage", 0.3
            tr.next_check = ("No blocking parameter issue - check the serving "
                             "sector's RSRP MR / weak-coverage samples and the "
                             "neighbour list; likely edge coverage or indoor."
                             + info_note)
        else:
            tr.likely_cause, tr.confidence = "coverage_or_capacity", 0.3
            tr.next_check = ("No blocking parameter issue - check busy-hour PRB "
                             "/ users and RSRP/SINR for the serving sector at "
                             "the problem time; likely capacity or indoor."
                             + info_note)


_NEXT = {
    "missing_ret": "Confirm the RET on the flagged sector(s) via the OSS / RCU; "
                   "a stuck or unread RET often reads as weak coverage. Restore, "
                   "then re-check RSRP.",
    "tilt_imbalance": "Compare the flagged sectors' coverage; align the "
                      "outlier's electrical tilt with its neighbours unless it "
                      "is deliberate, then re-measure.",
    "over_tilt": "The sector is steeply downtilted for the mast - if the "
                 "complaint is at range, reduce RET 1-2 deg and monitor "
                 "overshoot / RSRQ.",
    "azimuth_gap": "There is a wide un-served bearing between sectors - a "
                   "complaint that direction needs a sector re-point or a new "
                   "sector; validate with a drive test.",
    "azimuth_overlap": "Two sectors overlap - check for pilot pollution / "
                       "ping-pong on that boundary and a hole on the opposite "
                       "side.",
    "narrow_band_only": "Only a narrow-band layer - 'Data Service' complaints "
                        "here are usually capacity; check PRB and consider "
                        "adding L1800/L2600.",
    "single_carrier": "Single carrier - no busy-hour offload; check load and "
                      "plan a second carrier.",
    "pci_reuse_site": "PCI reuse on the same carrier at this site - fix the PCI "
                      "plan; it causes RS collision and handover failures.",
    "dup_pci_sector": "A sector lists duplicate/stale cells - clean the "
                      "configuration.",
    "rs_power_imbalance": "RS power differs across sectors - raise the low "
                          "sector to match, then re-check its edge RSRP.",
    "isolated_site": "The site is isolated - edge/'no service' complaints here "
                     "need a new site or a low-band layer, not tuning.",
    "few_sectors": "Few sectors cover a wide area - a complaint off-beam is "
                   "expected; consider a sector addition.",
}


def process_worklist(
    worklist: pd.DataFrame,
    params: pd.DataFrame,
    *,
    kpi: pd.DataFrame | None = None,
    history_counts: dict[str, int] | None = None,
    max_ret_default: float = 10.0,
) -> pd.DataFrame:
    sites = [s for s in worklist.get("site_id", pd.Series(dtype=str)).dropna()
             if s]
    audits = audit_sites(sites, params, max_ret_default=max_ret_default)
    kctx = KpiContext(kpi) if kpi is not None else None

    rows: list[dict] = []
    for _, t in worklist.iterrows():
        sid = str(t.get("site_id") or "").upper().strip()
        if sid in ("NAN", "NONE", "0", ""):
            sid = ""
        aud = audits.get(sid)
        tr = TicketResult(
            ticket_id=str(t.get("ticket_id", "") or ""),
            hpsm_id=str(t.get("hpsm_id", "") or ""),
            site_id=sid or "(missing)",
            city=str(t.get("city", "") or ""),
            affected_service=str(t.get("affected_service", "") or ""),
            problem_time=str(t.get("problem_time", "") or "")[:16],
            sla_status=str(t.get("sla_status", "") or ""),
            comment=str(t.get("comment", "") or "")[:400],
        )
        tr.comment_signals = ",".join(_comment_signals(tr.comment))
        if aud is not None and aud.site_found_ok():
            tr.site_found = True
            tr.enodeb_name = aud.enodeb_name
            tr.n_sectors = aud.n_sectors
            tr.bands = ",".join(aud.bands)
            tr.height_m = aud.height_m
            tr.tilt_summary = aud.tilt_summary
            tr.nearest_site_m = aud.nearest_site_m
            tr.isolated = any(f.code == "isolated_site" for f in aud.flags)
            tr.param_severity = aud.severity
            tr.param_flags = " | ".join(f"{f.code}({f.severity})"
                                        for f in aud.flags)
        else:
            tr.param_severity = "site not in params" if sid else "no site id"
        if history_counts:
            tr.site_complaint_history = history_counts.get(sid)
        if kctx is not None and kctx.available:
            tr.kpi_window = kctx.window
            status, cat, ev, severe = (kctx.verdict(sid) if sid
                                       else ("no site id", "", "", False))
            tr.kpi_status, tr.kpi_verdict = status, cat
            tr.kpi_evidence, tr.kpi_severe = ev, severe
        _decide(tr, aud)
        d = tr.as_dict()
        d["priority"] = _priority(tr)
        rows.append(d)
    df = pd.DataFrame(rows)
    df.attrs["audits"] = audits
    return df


_ACTIONABLE = {"far_coverage": 3, "off_axis_azimuth": 3, "new_site_needed": 2,
               "congestion": 3, "interference": 3, "indoor": 2,
               "coverage_or_capacity": 1, "availability": 2,
               "not_rf": 0, "insufficient_data": 0}


def _priority(tr: "TicketResult") -> str:
    act = _ACTIONABLE.get(tr.likely_cause, 1)
    if act == 0:
        return "P4"
    hist = tr.site_complaint_history or 0
    sev = {"critical": 2, "warning": 1}.get(tr.param_severity, 0)
    score = (act
             + sev
             + (2 if hist >= 20 else 1 if hist >= 8 else 0)
             + (1 if tr.confidence >= 0.6 else 0)
             + (2 if tr.kpi_severe else 1 if tr.kpi_verdict else 0))
    # P1 = a *severe* measured KPI issue, or a critical parameter fault on a
    # chronic site
    p1_ready = tr.kpi_severe or (tr.param_severity == "critical" and hist >= 8)
    if score >= 6 and p1_ready:
        return "P1"
    return "P2" if score >= 5 else "P3" if score >= 3 else "P4"


def run_worklist(worklist_path, params: pd.DataFrame, out_path,
                 *, kpi=None, history_counts=None) -> tuple[pd.DataFrame, Path]:
    wl = load_worklist(worklist_path)
    df = process_worklist(wl, params, kpi=kpi, history_counts=history_counts)
    p = write_worklist_report(df, df.attrs["audits"], out_path)
    return df, p


def write_worklist_report(worklist_df: pd.DataFrame,
                          audits: dict[str, SiteAudit],
                          out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # per-site flag detail
    flag_rows = []
    for sid, a in audits.items():
        for f in a.flags:
            flag_rows.append({"site_id": sid, "enodeb": a.enodeb_name,
                              **f.as_dict()})
    sector_rows = []
    for sid, a in audits.items():
        for s in a.sector_table:
            sector_rows.append({"site_id": sid, **{k: v for k, v in s.items()
                                                   if k != "pcis"},
                                "pcis": ",".join(map(str, s.get("pcis", [])))})

    w0 = worklist_df
    summary = (pd.crosstab(w0["likely_cause"], w0["priority"])
               .reindex(columns=["P1", "P2", "P3", "P4"], fill_value=0))
    summary["total"] = summary.sum(axis=1)
    summary = summary.sort_values("total", ascending=False).reset_index()
    # sites with the most tickets today
    top_sites = (w0.groupby("site_id")
                 .agg(tickets=("ticket_id", "size"),
                      priority=("priority", "min"),
                      likely_cause=("likely_cause", "first"),
                      kpi_evidence=("kpi_evidence", "first"),
                      history=("site_complaint_history", "first"))
                 .sort_values(["tickets", "history"], ascending=False)
                 .head(25).reset_index())

    wl = worklist_df.copy()
    wl["_p"] = wl.get("priority", pd.Series("P3", index=wl.index)).map(
        {"P1": 0, "P2": 1, "P3": 2, "P4": 3}).fillna(2)
    wl = (wl.sort_values(["_p", "site_complaint_history", "confidence"],
                         ascending=[True, False, False]).drop(columns="_p"))
    front = ["priority", "ticket_id", "site_id", "enodeb_name", "city",
             "affected_service", "problem_time", "likely_cause", "confidence",
             "next_check", "kpi_status", "kpi_verdict", "kpi_evidence",
             "param_severity", "param_flags", "comment_signals", "n_sectors",
             "bands", "height_m", "tilt_summary", "isolated", "nearest_site_m",
             "site_complaint_history", "kpi_window", "comment", "hpsm_id",
             "sla_status"]
    wl = wl[[c for c in front if c in wl.columns]
            + [c for c in wl.columns if c not in front]]

    with pd.ExcelWriter(out_path, engine="openpyxl") as xw:
        wl.to_excel(xw, sheet_name="Worklist", index=False)
        summary.to_excel(xw, sheet_name="Summary", index=False)
        top_sites.to_excel(xw, sheet_name="Summary", index=False,
                           startrow=len(summary) + 3)
        pd.DataFrame(flag_rows or [{"info": "no flags"}]).to_excel(
            xw, sheet_name="Site flags", index=False)
        pd.DataFrame(sector_rows or [{"info": "no sectors"}]).to_excel(
            xw, sheet_name="Sector params", index=False)
    try:
        from rfopt.reports.excel_report import _style_header
        from openpyxl import load_workbook
        wb = load_workbook(out_path)
        for ws in wb.worksheets:
            _style_header(ws)
        wb.save(out_path)
    except Exception:
        pass
    return out_path
