"""Per-ticket RF analysis: geometry triage + optional tilt/azimuth action.

For each complaint we resolve the serving sector's geometry from the site DB,
place the complaint (its own coordinate, else the serving site), then:

  * distance & bearing complaint -> serving site, and the angular offset from
    the serving sector's azimuth
  * the *best* nearby sector (near + on-axis) - is the customer on the wrong
    server?
  * a geometry-only category: new_site_needed / wrong_server / off_axis_azimuth
    / far_coverage / near_on_axis(non-geometry) / interference / indoor /
    congestion / not_rf / insufficient_data
  * when it is a tilt/azimuth/coverage case, the computed action from
    ``recommend_for_complaint``
  * an agreement flag vs the engineer's own RF Analysis / Closure Code
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from rfopt.actions.geometry import (angular_offset_deg, bearing_deg,
                                    haversine_m)
from rfopt.actions.propagation import Environment
from rfopt.actions.recommend import (CellContext, ComplaintContext,
                                     recommend_for_complaint)

# geometry-only categories
CATEGORIES = [
    "new_site_needed", "wrong_server", "off_axis_azimuth", "far_coverage",
    "near_on_axis_other", "interference", "indoor", "congestion",
    "availability", "not_rf", "coverage_or_capacity", "insufficient_data",
]

# map the operator's own labels to our categories (for the audit)
_ENGINEER_MAP = {
    "new site required": "new_site_needed",
    "physical optimization": "far_coverage",       # tilt/azimuth work
    "flow control": "congestion",
    "prb utilization": "congestion",
    "event traffic increase": "congestion",
    "interference": "interference",
    "high rtwp area (interference)": "interference",
    "government interference": "interference",
    "indoor issue - urban areas": "indoor",
    "device issue": "not_rf", "sim issue": "not_rf", "no answer": "not_rf",
    "others": None, "alarms": "availability",
    "availability/outage": "availability",
    "data - high utilization cells": "congestion",
    "data - slow weak coverage": "far_coverage",
    "data - planned - this area needs a new site/tower": "new_site_needed",
    "planned - this area needs a new site/tower": "new_site_needed",
    "customer no answer/switch off/disconnected": "not_rf",
    "resolved successfully": None, "no problem found": None,
}

_TEXT_INTERF = re.compile(r"\b(rtwp|interfe|jammer|jamming|noise rise)\b", re.I)
_TEXT_INDOOR = re.compile(r"\b(indoor|in-?building|basement|inside the|mall)\b", re.I)
_TEXT_CONG = re.compile(r"\b(congest|utiliz|utilis|prb|flow control|overload|"
                        r"capacity|high traffic)\b", re.I)
_TEXT_NEWSITE = re.compile(r"\b(new site|planned site|new tower|need a site|"
                           r"no coverage|null area|far from)\b", re.I)


@dataclass
class ComplaintFinding:
    ticket_id: str
    msisdn: str = ""
    problem_time: str = ""
    create_time: str = ""
    city: str = ""
    sub_district: str = ""
    serving_site: str = ""
    serving_sector: int | None = None
    serving_cell: str = ""
    loc_source: str = "none"          # ticket | subdistrict | serving_site | none
    geo_spread_km: float | None = None      # location uncertainty (subdistrict)
    latitude: float | None = None
    longitude: float | None = None
    distance_m: float | None = None
    bearing_deg: float | None = None
    az_offset_deg: float | None = None
    serving_azimuth_deg: float | None = None
    serving_height_m: float | None = None
    nearest_site: str = ""
    nearest_distance_m: float | None = None
    best_sector_id: str = ""
    best_sector_offset_deg: float | None = None
    best_sector_distance_m: float | None = None
    category: str = "insufficient_data"
    confidence: float = 0.3
    finding: str = ""
    recommended_action: str = ""
    action_detail: str = ""
    monitor_kpis: list[str] = field(default_factory=list)
    engineer_rf_analysis: str = ""
    engineer_closure_code: str = ""
    engineer_root_cause: str = ""
    engineer_category: str | None = None
    agreement: str = "n/a"                   # agree | partial | differ | n/a
    used_engineer_label: bool = False        # excluded from the audit if True
    status: str = ""

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        for k in ("distance_m", "nearest_distance_m", "best_sector_distance_m"):
            if d[k] is not None:
                d[k] = round(d[k])
        for k in ("az_offset_deg", "bearing_deg", "serving_azimuth_deg",
                  "best_sector_offset_deg", "confidence"):
            if d[k] is not None:
                d[k] = round(d[k], 1)
        d["monitor_kpis"] = ",".join(self.monitor_kpis)
        return d


@dataclass
class ComplaintResult:
    findings: list[ComplaintFinding]
    site_db: pd.DataFrame
    notes: list[str] = field(default_factory=list)

    def df(self) -> pd.DataFrame:
        return pd.DataFrame([f.as_dict() for f in self.findings])

    def summary(self) -> dict:
        d = self.df()
        out = {"tickets": len(d)}
        if not d.empty:
            out["by_category"] = d["category"].value_counts().to_dict()
            out["loc_exact"] = int((d["loc_source"] == "ticket").sum())
            out["loc_area_centroid"] = int((d["loc_source"] == "subdistrict").sum())
            out["loc_serving_site_only"] = int(
                d["loc_source"].isin(["serving_site", "none"]).sum())
            out["actions_generated"] = int((d["recommended_action"] != "").sum())
            aud = d[d["agreement"] != "n/a"]
            if len(aud):
                out["audit_n"] = len(aud)
                out["agree"] = round((aud["agreement"] == "agree").mean(), 3)
                out["agree_or_partial"] = round(
                    (aud["agreement"].isin(["agree", "partial"])).mean(), 3)
        return out


# --------------------------------------------------------------------------- #
def _sector_frame(site_db: pd.DataFrame) -> pd.DataFrame:
    df = site_db.copy()
    df = df.dropna(subset=["latitude", "longitude"])
    df["on_air"] = df["status"].astype(str).str.lower().str.startswith("on")
    return df


def _nearest_sites(site_lat, site_lon, sites_xy: np.ndarray, ids: list[str],
                   k: int = 6):
    dlat = (sites_xy[:, 0] - site_lat) * 111_320.0
    dlon = (sites_xy[:, 1] - site_lon) * 111_320.0 * np.cos(np.radians(site_lat))
    d = np.hypot(dlat, dlon)
    order = np.argsort(d)[:k]
    return [(ids[i], float(d[i])) for i in order]


def _classify(f: ComplaintFinding, notes_text: str,
              engineer_text: str, approx: bool = False) -> None:
    """Geometry-first triage. ``notes_text`` = raw investigation notes /
    location (a hint). ``engineer_text`` = the engineer's own verdict - used
    ONLY for the non-RF / outage short-circuit, and that sets
    ``used_engineer_label`` so the ticket is excluded from the audit.
    ``approx`` = the location is a sub-district centroid (~km uncertain):
    coarse buckets only, no azimuth claim, capped confidence."""
    d = f.distance_m
    off = f.az_offset_deg
    near = f.nearest_distance_m
    hbw = 65.0
    txt = notes_text.lower()
    elow = engineer_text.lower()

    # non-RF / outage: only the engineer's field can tell us reliably
    if any(t in elow for t in ("device", "sim ", "no answer", "isp", "vpn",
                               "handset", "charging", "wrong ticket",
                               "switch off", "disconnected")):
        f.category, f.confidence = "not_rf", 0.6
        f.finding = "Non-RF (device / SIM / ISP / no-answer) per the ticket."
        f.used_engineer_label = True
        return
    if any(t in elow for t in ("outage", "availability", "alarm",
                               "site stopped", "site outage")):
        f.category, f.confidence = "availability", 0.6
        f.finding = "Availability / outage - fault management, not RF tuning."
        f.used_engineer_label = True
        return

    if d is None:
        f.category, f.confidence = "insufficient_data", 0.25
        f.finding = ("No complaint coordinate and no geocodable area name - "
                     "location-based triage not possible. Use the serving-site "
                     "hotspot view for this ticket.")
        _apply_text_hints(f, txt)
        return

    if approx:
        spread = f.geo_spread_km or 8.0
        caveat = (f" [location = '{f.sub_district}' area centroid, "
                  f"+-{spread:.0f} km - use the site / area hotspot view, not "
                  f"this as a per-ticket fix]")
        if d > 8000:
            f.category, f.confidence = "new_site_needed", 0.3
            f.finding = (f"The '{f.sub_district}' area is very far (~{d/1000:.0f} "
                         f"km) from the serving site {f.serving_site} - the "
                         f"area probably needs its own coverage." + caveat)
        else:
            f.category, f.confidence = "coverage_or_capacity", 0.28
            f.finding = (f"'{f.sub_district}' is ~{d/1000:.1f} km from "
                         f"{f.serving_site}. Area centroid is too coarse to "
                         f"tell coverage from capacity or to check dominance - "
                         f"needs an exact complaint coordinate or the serving "
                         f"cell's KPI." + caveat)
        _apply_text_hints(f, txt)
        return

    far = d > 2400
    nearest_far = (near is None) or (near > 1500)
    best_close = (f.best_sector_distance_m is not None
                  and f.best_sector_distance_m < 1400
                  and f.best_sector_offset_deg is not None
                  and f.best_sector_offset_deg < 35)
    much_closer = (near is not None) and (near < 0.5 * d) and (near < 1200)
    off_axis = (off is not None) and (off > hbw / 2)

    if far and nearest_far:
        f.category = "new_site_needed"
        f.confidence = 0.6 if d > 3500 else 0.48
        f.finding = (
            f"Complaint ~{d/1000:.1f} km from the serving site; nearest other "
            f"site {'unknown' if near is None else f'~{near/1000:.1f} km'} - "
            f"beyond a tilt/power fix, coverage layer or new site is the "
            f"realistic action.")
        if _TEXT_NEWSITE.search(txt):
            f.confidence = min(0.8, f.confidence + 0.15)
            f.finding += " Investigation notes also point to weak/no coverage."
    elif much_closer and best_close:
        f.category = "wrong_server"
        f.confidence = 0.55
        f.finding = (
            f"A closer site ({f.nearest_site}, ~{near/1000:.1f} km, sector "
            f"{f.best_sector_id} {f.best_sector_offset_deg:.0f} deg off) is far "
            f"nearer than the serving site (~{d/1000:.1f} km). Customer is "
            f"likely on the wrong server - check the {f.serving_site} -> "
            f"{f.nearest_site} neighbour relation and dominance, or repoint "
            f"{f.best_sector_id} toward the area.")
    elif off_axis and best_close:
        f.category = "off_axis_azimuth"
        f.confidence = 0.5
        f.finding = (
            f"Complaint {off:.0f} deg off the serving sector's "
            f"{f.serving_azimuth_deg:.0f} deg azimuth (outside the ~{hbw:.0f} "
            f"deg beam), losing ~{min(12*(off/hbw)**2,25):.0f} dB. Sector "
            f"{f.best_sector_id} points {f.best_sector_offset_deg:.0f} deg off "
            f"at ~{f.best_sector_distance_m/1000:.1f} km - serve from there, or "
            f"rotate the serving sector toward the area.")
    elif off_axis:
        f.category = "far_coverage"
        f.confidence = 0.4
        f.finding = (
            f"Complaint {off:.0f} deg off the serving azimuth and ~{d/1000:.1f} "
            f"km out, with no well-placed alternative sector nearby - likely a "
            f"coverage gap. Azimuth/tilt on the serving sector may help; "
            f"confirm with RSRP MR / drive test, else new site.")
    elif d > 700:
        f.category = "far_coverage"
        f.confidence = 0.5
        f.finding = (
            f"Complaint ~{d/1000:.1f} km out, ~{off:.0f} deg off axis - roughly "
            f"in the serving sector's beam. A tilt or RS-power change toward "
            f"the area is the candidate action; needs the cell's current RET "
            f"to size it.")
    else:
        f.category = "near_on_axis_other"
        f.confidence = 0.45
        f.finding = (
            f"Complaint is close (~{d:.0f} m) and on-axis ({off:.0f} deg off) - "
            f"macro geometry is not the limiter. Most likely indoor "
            f"penetration, capacity, or a device issue; confirm with cell KPI "
            f"(PRB, RSRP MR, RTWP).")

    _apply_text_hints(f, txt)


def _apply_text_hints(f: ComplaintFinding, txt: str) -> None:
    """Override / annotate the category from free-text investigation notes."""
    if _TEXT_INTERF.search(txt):
        f.category, f.confidence = "interference", max(f.confidence, 0.5)
        f.finding = ("Investigation notes mention RTWP / interference - treat "
                     "as uplink interference; confirm with UL RSSI/RTWP KPI and "
                     "an interference hunt. " + f.finding)
    elif _TEXT_INDOOR.search(txt) and f.category in (
            "near_on_axis_other", "far_coverage", "coverage_or_capacity",
            "insufficient_data"):
        f.category, f.confidence = "indoor", max(f.confidence, 0.5)
        f.finding = ("Notes mention indoor - an in-building solution (small "
                     "cell / DAS / repeater) likely beats macro tuning. "
                     + f.finding)
    elif _TEXT_CONG.search(txt) and f.category in (
            "near_on_axis_other", "coverage_or_capacity", "insufficient_data"):
        f.category, f.confidence = "congestion", max(f.confidence, 0.45)
        f.finding = ("Notes point to utilisation / flow control - a capacity / "
                     "load-balancing case; confirm with busy-hour PRB & "
                     "connected-user KPI. " + f.finding)


def _agreement(tool_cat: str, eng_cat: str | None) -> str:
    if eng_cat is None:
        return "n/a"
    if tool_cat == eng_cat:
        return "agree"
    partial = {
        ("wrong_server", "far_coverage"), ("far_coverage", "wrong_server"),
        ("off_axis_azimuth", "far_coverage"), ("far_coverage", "off_axis_azimuth"),
        ("near_on_axis_other", "indoor"), ("indoor", "near_on_axis_other"),
        ("near_on_axis_other", "congestion"), ("congestion", "near_on_axis_other"),
        ("new_site_needed", "far_coverage"), ("far_coverage", "new_site_needed"),
    }
    if (tool_cat, eng_cat) in partial:
        return "partial"
    return "differ"


# --------------------------------------------------------------------------- #
def analyze_complaints(
    complaints: pd.DataFrame,
    site_db: pd.DataFrame,
    *,
    env_kind: str = "urban",
    frequency_mhz: float = 1800.0,
    default_elec_tilt_deg: float = 3.0,
    ret_unit: str = "tenths",
    compute_actions: bool = True,
    max_tickets: int | None = None,
) -> ComplaintResult:
    sf = _sector_frame(site_db)
    site_xy = (sf.groupby("site_id")[["latitude", "longitude"]].mean())
    site_ids = site_xy.index.tolist()
    site_arr = site_xy.to_numpy()
    on_air_mask = sf.groupby("site_id")["on_air"].any()

    # sector lookup: (site_id, sector) -> row
    sf["_key"] = list(zip(sf["site_id"], sf["sector"].fillna(0).astype(int)))
    sec_lookup = {k: r for k, r in zip(sf["_key"], sf.to_dict("records"))}
    by_site = {s: g for s, g in sf.groupby("site_id")}

    df = complaints if max_tickets is None else complaints.head(max_tickets)
    findings: list[ComplaintFinding] = []

    for _, row in df.iterrows():
        f = ComplaintFinding(
            ticket_id=str(row.get("ticket_id", "")),
            msisdn=str(row.get("msisdn", "")),
            problem_time=str(row.get("problem_time", "") or ""),
            create_time=str(row.get("create_time", "") or ""),
            city=str(row.get("city", "") or ""),
            sub_district=str(row.get("sub_district", "") or ""),
            serving_site=str(row.get("site_id", "") or ""),
            serving_cell=str(row.get("cell_id", "") or ""),
            status=str(row.get("status", "") or ""),
            engineer_rf_analysis=str(row.get("rf_analysis", "") or ""),
            engineer_closure_code=str(row.get("closure_code", "") or ""),
            engineer_root_cause=str(row.get("root_cause", "") or ""),
        )
        sec = row.get("sector")
        try:
            f.serving_sector = int(float(sec)) if pd.notna(sec) else None
        except (TypeError, ValueError):
            f.serving_sector = None
        f.serving_cell = f.serving_cell or (
            f"{f.serving_site}-S{f.serving_sector}" if f.serving_sector
            else f.serving_site)

        srow = sec_lookup.get((f.serving_site, f.serving_sector or 0))
        if srow is None and f.serving_site in by_site:
            g = by_site[f.serving_site]
            srow = g.iloc[0].to_dict()
        s_lat = srow["latitude"] if srow else None
        s_lon = srow["longitude"] if srow else None
        if srow:
            f.serving_azimuth_deg = float(srow.get("azimuth_deg")) \
                if pd.notna(srow.get("azimuth_deg")) else None
            f.serving_height_m = float(srow.get("antenna_height_m")) \
                if pd.notna(srow.get("antenna_height_m")) else None

        c_lat = row.get("complaint_lat")
        c_lon = row.get("complaint_lon")
        csrc = str(row.get("coord_source", "") or "")
        f.geo_spread_km = (float(row["geo_spread_km"])
                           if pd.notna(row.get("geo_spread_km")) else None)
        if pd.notna(c_lat) and pd.notna(c_lon) and csrc in ("ticket", "subdistrict"):
            f.latitude, f.longitude = float(c_lat), float(c_lon)
            f.loc_source = csrc
        elif s_lat is not None:
            f.latitude, f.longitude, f.loc_source = float(s_lat), float(s_lon), "serving_site"

        approx = f.loc_source == "subdistrict"
        if f.loc_source == "ticket" and s_lat is not None:
            # full per-ticket geometry
            f.distance_m = haversine_m(s_lat, s_lon, f.latitude, f.longitude)
            f.bearing_deg = bearing_deg(s_lat, s_lon, f.latitude, f.longitude)
            if f.serving_azimuth_deg is not None:
                f.az_offset_deg = angular_offset_deg(f.serving_azimuth_deg,
                                                     f.bearing_deg)
            near = _nearest_sites(f.latitude, f.longitude, site_arr, site_ids, k=8)
            for sid, dist in near:
                if sid == f.serving_site or not bool(on_air_mask.get(sid, False)):
                    continue
                f.nearest_site, f.nearest_distance_m = sid, dist
                break
            (f.best_sector_id, f.best_sector_offset_deg,
             f.best_sector_distance_m) = _best_sector(
                f.latitude, f.longitude, near, by_site)
        elif approx and s_lat is not None:
            # only the (rough) area-to-site distance is meaningful; direction,
            # nearest-site and dominance are NOT (the point is +-km).
            f.distance_m = haversine_m(s_lat, s_lon, f.latitude, f.longitude)

        notes_text = " ".join(str(row.get(c, "")) for c in
                              ("diag_comment", "sub_district", "title"))
        eng_text = " ".join(str(row.get(c, "")) for c in
                            ("rf_analysis", "closure_code", "root_cause",
                             "diag_action", "status"))
        _classify(f, notes_text=notes_text, engineer_text=eng_text,
                  approx=approx)

        f.engineer_category = _ENGINEER_MAP.get(
            f.engineer_rf_analysis.strip().lower(),
            _ENGINEER_MAP.get(f.engineer_closure_code.strip().lower()))
        f.agreement = ("n/a" if f.used_engineer_label
                       else _agreement(f.category, f.engineer_category))

        if compute_actions and f.category in ("far_coverage", "off_axis_azimuth") \
                and f.loc_source == "ticket" and f.serving_height_m:
            _attach_action(f, default_elec_tilt_deg, ret_unit, env_kind,
                           frequency_mhz)
        findings.append(f)

    notes = list(complaints.attrs.get("_notes", []))
    notes.append(f"analysed {len(findings)} tickets against "
                 f"{site_xy.shape[0]} R5 sites")
    return ComplaintResult(findings=findings, site_db=site_db, notes=notes)


def _best_sector(lat, lon, near_sites, by_site):
    best = ("", None, None)
    best_score = 1e9
    for sid, dist in near_sites[:5]:
        g = by_site.get(sid)
        if g is None:
            continue
        for _, s in g.iterrows():
            if pd.isna(s.get("azimuth_deg")):
                continue
            brg = bearing_deg(s["latitude"], s["longitude"], lat, lon)
            off = angular_offset_deg(float(s["azimuth_deg"]), brg)
            d = haversine_m(s["latitude"], s["longitude"], lat, lon)
            score = d * (1 + (off / 65.0) ** 2)          # near + on-axis
            if score < best_score:
                best_score = score
                best = (str(s.get("sector_id", f"{sid}-S{s.get('sector','?')}")),
                        off, d)
    return best


def _attach_action(f: ComplaintFinding, default_tilt, ret_unit, env_kind, freq):
    ctx = CellContext(
        cell_id=f.serving_cell or f"{f.serving_site}-S{f.serving_sector}",
        site_id=f.serving_site,
        latitude=f.latitude, longitude=f.longitude,   # not used for dist here
        antenna_height_m=f.serving_height_m or 30.0,
        azimuth_deg=f.serving_azimuth_deg or 0.0,
        mech_tilt_deg=0.0, elec_tilt_deg=default_tilt, ret_unit=ret_unit,
        env=Environment(kind=env_kind, frequency_mhz=freq),
    )
    cc = ComplaintContext(cell=ctx, distance_m=f.distance_m,
                          bearing_deg_from_site=f.bearing_deg)
    try:
        rec = recommend_for_complaint(cc)
        f.recommended_action = rec.action_type
        f.action_detail = rec.recommended_value
        f.monitor_kpis = rec.monitor_kpis
        if "assumed" not in f.action_detail.lower():
            f.action_detail += "  (RET assumed " \
                f"{default_tilt:.1f}° — verify actual value)"
    except Exception:
        pass


# --------------------------------------------------------------------------- #
def aggregate_by_site(res: ComplaintResult, *, top_categories: int = 3
                      ) -> pd.DataFrame:
    """Serving-site complaint hotspots (works with or without coordinates).

    Per site: ticket count, which sectors complain, dominant tool category,
    plus site-DB context - isolation (nearest on-air neighbour), whether a
    planned site sits nearby.
    """
    d = res.df()
    if d.empty:
        return pd.DataFrame()
    sdb = res.site_db.dropna(subset=["latitude", "longitude"]).copy()
    site_xy = sdb.groupby("site_id")[["latitude", "longitude"]].mean()
    on_air = sdb.assign(_on=sdb["status"].str.lower().str.startswith("on")) \
                .groupby("site_id")["_on"].any()
    planned = sdb.assign(_pl=sdb["status"].str.lower().str.startswith("plan")) \
                 .groupby("site_id")["_pl"].any()
    xy = site_xy.to_numpy()
    ids = site_xy.index.tolist()
    n_sec = sdb.groupby("site_id")["sector"].nunique()

    rows = []
    for site, g in d.groupby("serving_site"):
        if not site:
            continue
        cats = g["category"].value_counts()
        rec = {
            "serving_site": site,
            "tickets": len(g),
            "sectors_complained": ",".join(
                sorted({str(int(s)) for s in g["serving_sector"].dropna()})),
            "dominant_category": cats.index[0] if len(cats) else "",
            "top_categories": "; ".join(f"{k}:{v}" for k, v in
                                        cats.head(top_categories).items()),
            "n_actions": int((g["recommended_action"] != "").sum()),
            "with_exact_loc": int((g["loc_source"] == "ticket").sum()),
            "median_distance_m": (round(g["distance_m"].dropna().median())
                                  if g["distance_m"].notna().any() else None),
        }
        if site in site_xy.index:
            la, lo = site_xy.loc[site]
            rec["latitude"], rec["longitude"] = round(la, 6), round(lo, 6)
            rec["site_sectors"] = int(n_sec.get(site, 0))
            rec["status"] = "on-air" if bool(on_air.get(site, False)) else "not-on-air"
            # nearest OTHER on-air site
            dlat = (xy[:, 0] - la) * 111_320.0
            dlon = (xy[:, 1] - lo) * 111_320.0 * np.cos(np.radians(la))
            dist = np.hypot(dlat, dlon)
            nb = [(ids[i], dist[i]) for i in np.argsort(dist)
                  if ids[i] != site and bool(on_air.get(ids[i], False))][:1]
            if nb:
                rec["nearest_site"] = nb[0][0]
                rec["nearest_site_m"] = round(nb[0][1])
                rec["isolated"] = nb[0][1] > 2500
            planned_near = int(sum(
                1 for i in np.argsort(dist)[:12]
                if ids[i] != site and dist[i] < 2500
                and bool(planned.get(ids[i], False))))
            rec["planned_sites_within_2.5km"] = planned_near
        rows.append(rec)

    out = pd.DataFrame(rows).sort_values("tickets", ascending=False)
    return out.reset_index(drop=True)


def aggregate_by_subdistrict(res: ComplaintResult) -> pd.DataFrame:
    """Area (sub-district) complaint hotspots - candidate coverage / new-site
    areas. Coarse centroid, fine for a heat map / prioritisation list."""
    d = res.df()
    if d.empty or "sub_district" not in d.columns:
        return pd.DataFrame()
    d = d[d["sub_district"].astype(str).str.strip().ne("")
          & ~d["sub_district"].astype(str).str.lower().isin(["nan", "none", "0"])]
    if d.empty:
        return pd.DataFrame()
    rows = []
    for area, g in d.groupby("sub_district"):
        cats = g["category"].value_counts()
        rec = {
            "sub_district": area,
            "city": g["city"].mode().iat[0] if g["city"].notna().any() else "",
            "tickets": len(g),
            "serving_sites": g["serving_site"].nunique(),
            "top_serving_sites": ",".join(
                g["serving_site"].value_counts().head(3).index),
            "dominant_category": cats.index[0] if len(cats) else "",
            "new_site_or_coverage": int(g["category"].isin(
                ["new_site_needed", "far_coverage", "coverage_or_capacity",
                 "wrong_server"]).sum()),
        }
        ll = g[["latitude", "longitude"]].dropna()
        if len(ll):
            rec["latitude"] = round(ll["latitude"].median(), 6)
            rec["longitude"] = round(ll["longitude"].median(), 6)
        rows.append(rec)
    return pd.DataFrame(rows).sort_values("tickets", ascending=False
                                          ).reset_index(drop=True)


# --------------------------------------------------------------------------- #
def audit_table(res: ComplaintResult) -> pd.DataFrame:
    """Confusion matrix: engineer category (rows) vs tool category (cols).

    Tickets the tool categorised *from* the engineer's own label are excluded.
    """
    d = res.df()
    d = d[(d["agreement"] != "n/a")
          & d["engineer_category"].notna() & (d["engineer_category"] != "None")]
    if d.empty:
        return pd.DataFrame()
    return (pd.crosstab(d["engineer_category"], d["category"])
            .rename_axis("engineer \\ tool"))
