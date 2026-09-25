"""Generate a KMZ (Google Earth) map of sites / sectors.

No third-party dependency: a KMZ is just a ZIP containing ``doc.kml``.

Each sector is drawn as a beam wedge from the site out to its computed 3 dB
coverage distance, spanning the horizontal beamwidth, coloured by health
(green / amber / red from the worst diagnosis on that cell). Overshooting
cells also get a dashed "actual reach" ray to the geometric far edge.
Every placemark's balloon carries the KPIs, diagnoses and the top
recommendation for that cell.
"""

from __future__ import annotations

import math
import zipfile
from dataclasses import dataclass
from html import escape
from pathlib import Path

from rfopt.actions.geometry import coverage_edge_distance_m

_M_PER_DEG_LAT = 111_320.0
_SEV_COLOR = {                       # KML aabbggrr
    "critical": "cc0000ff",
    "warning": "cc00a5ff",
    "ok": "cc44c840",
    "nodata": "80b0b0b0",
}
_SEV_RANK = {"ok": 0, "nodata": 0, "warning": 1, "critical": 2}


@dataclass
class SectorStyle:
    beam_alpha: str = "aa"           # polygon fill alpha (KML hex)
    min_radius_m: float = 120.0
    max_radius_m: float = 4000.0
    overshoot_radius_cap_m: float = 12000.0
    arc_steps: int = 18
    default_elec_tilt_deg: float = 3.0   # used when the site DB has no RET


def _num(v, default: float) -> float:
    try:
        f = float(v)
        return f if f == f else default          # NaN -> default
    except (TypeError, ValueError):
        return default


def _offset(lat: float, lon: float, bearing_deg: float, dist_m: float
            ) -> tuple[float, float]:
    br = math.radians(bearing_deg)
    dlat = (dist_m * math.cos(br)) / _M_PER_DEG_LAT
    dlon = (dist_m * math.sin(br)) / (_M_PER_DEG_LAT *
                                      math.cos(math.radians(lat)) or 1e-9)
    return lat + dlat, lon + dlon


def _wedge_coords(lat: float, lon: float, az: float, hbw: float,
                  radius_m: float, steps: int) -> str:
    pts = [(lon, lat)]
    start, end = az - hbw / 2.0, az + hbw / 2.0
    for i in range(steps + 1):
        b = start + (end - start) * i / steps
        y, x = _offset(lat, lon, b, radius_m)
        pts.append((x, y))
    pts.append((lon, lat))
    return " ".join(f"{x:.6f},{y:.6f},0" for x, y in pts)


def _balloon(title: str, rows: list[tuple[str, str]], notes: list[str]) -> str:
    trs = "".join(
        f"<tr><td style='padding:2px 8px 2px 0;color:#555'>{escape(k)}</td>"
        f"<td style='padding:2px 0'><b>{escape(str(v))}</b></td></tr>"
        for k, v in rows if v not in ("", "None", "nan"))
    note_html = ""
    if notes:
        note_html = ("<div style='margin-top:6px'>"
                     + "".join(f"<div style='margin:3px 0'>{escape(n)}</div>"
                               for n in notes) + "</div>")
    return (f"<![CDATA[<div style='font-family:Segoe UI,Arial,sans-serif;"
            f"font-size:12px;max-width:360px'>"
            f"<div style='font-size:13px;font-weight:700;margin-bottom:4px'>"
            f"{escape(title)}</div><table>{trs}</table>{note_html}</div>]]>")


def _kml_doc(name: str, styles: str, folders: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2">\n<Document>\n'
        f"<name>{escape(name)}</name>\n"
        "<open>1</open>\n"
        f"{styles}\n{folders}\n</Document>\n</kml>\n"
    )


def build_site_kmz(
    site_db,
    out_path: str | Path,
    *,
    region: str | None = None,
    region_prefix: str | None = None,
    diagnoses: list | None = None,
    recommendations: list | None = None,
    kpi_by_cell: dict[str, dict] | None = None,
    title: str = "RF Optimisation - Site Map",
    style: SectorStyle | None = None,
) -> Path:
    """Write a KMZ for the (optionally filtered) site database.

    ``site_db``          DataFrame with cell_id/site_id/latitude/longitude/
                         azimuth_deg/antenna_height_m/(mech|elec)_tilt_deg/
                         vbw_deg/hbw_deg/band.
    ``region``           keep rows whose ``region`` column matches (substring).
    ``region_prefix``    keep rows whose ``site_id`` starts with this (e.g. "R5").
    ``diagnoses``        list[Diagnosis]; worst per cell colours the sector.
    ``recommendations``  list[Recommendation]; top per cell shown in the balloon.
    ``kpi_by_cell``      {cell_id: {kpi: value}} for the balloon.
    """
    import pandas as pd

    st = style or SectorStyle()
    df = site_db.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    if region and "region" in df.columns:
        df = df[df["region"].astype(str).str.contains(region, case=False, na=False)]
    if region_prefix and "site_id" in df.columns:
        df = df[df["site_id"].astype(str).str.upper()
                .str.startswith(region_prefix.upper())]
    df = df.dropna(subset=["latitude", "longitude"])
    if df.empty:
        raise ValueError("No sites left after filtering - check region / prefix.")

    worst: dict[str, tuple[str, list[str]]] = {}
    for d in (diagnoses or []):
        sev = d.severity if d.severity in ("warning", "critical") else "warning"
        cur = worst.get(d.entity_id)
        line = f"[{d.severity.upper()}] {d.title}" + (
            f" - trend: {d.trend_note}" if getattr(d, "trend_note", "") else "")
        if cur is None or _SEV_RANK[sev] > _SEV_RANK[cur[0]]:
            worst[d.entity_id] = (sev, [*(cur[1] if cur else []), line])
        else:
            cur[1].append(line)

    rec_by_cell: dict[str, object] = {}
    for r in (recommendations or []):
        rec_by_cell.setdefault(r.cell_id, r)

    styles = "\n".join(
        f'<Style id="beam_{k}"><LineStyle><color>ff{c[2:]}</color><width>1.3</width>'
        f"</LineStyle><PolyStyle><color>{c}</color></PolyStyle></Style>"
        for k, c in _SEV_COLOR.items()
    ) + (
        # site pin: labelled with the site ID (visible in Google Earth)
        '\n<Style id="site"><IconStyle><scale>0.9</scale><color>ff2266ff</color>'
        "<Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_square.png"
        "</href></Icon></IconStyle>"
        "<LabelStyle><scale>0.85</scale></LabelStyle></Style>"
        '\n<Style id="overshoot"><LineStyle><color>ff0000ff</color><width>2</width>'
        "</LineStyle></Style>"
    )

    # a dedicated 'Site labels' folder so the IDs are always visible
    site_xy = df.groupby("site_id")[["latitude", "longitude"]].mean()
    label_folder = ("<Folder><name>Site IDs</name><open>0</open>" + "".join(
        f"<Placemark><styleUrl>#site</styleUrl>"
        f"<name>{escape(str(sid))}</name>"
        f"<description><![CDATA[Site {escape(str(sid))} — "
        f"{lo:.5f}, {la:.5f}]]></description>"
        f"<Point><coordinates>{lo:.6f},{la:.6f},0</coordinates></Point></Placemark>"
        for sid, (la, lo) in site_xy.iterrows()
        if la == la and lo == lo) + "</Folder>")

    # group sectors under site folders
    folders: list[str] = [label_folder]
    for site_id, g in df.groupby("site_id"):
        slat = float(g["latitude"].mean())
        slon = float(g["longitude"].mean())
        placemarks = [
            "<Placemark><styleUrl>#site</styleUrl>"
            f"<name>{escape(str(site_id))}</name>"
            f"<description><![CDATA[{escape(str(site_id))} — "
            f"{slat:.5f}, {slon:.5f}]]></description>"
            f"<Point><coordinates>{slon:.6f},{slat:.6f},0</coordinates></Point>"
            "</Placemark>"
        ]
        for _, r in g.iterrows():
            cid = str(r.get("cell_id") or r.get("sector_id") or "")
            lat = _num(r.get("latitude"), float("nan"))
            lon = _num(r.get("longitude"), float("nan"))
            if lat != lat or lon != lon:
                continue
            az = _num(r.get("azimuth_deg"), 0.0)
            hbw = _num(r.get("hbw_deg"), 65.0)
            h = _num(r.get("antenna_height_m"), 30.0)
            mt = _num(r.get("mech_tilt_deg"), 0.0)
            et_raw = r.get("elec_tilt_deg")
            et = _num(et_raw, st.default_elec_tilt_deg)
            tilt_assumed = _num(et_raw, float("nan")) != _num(et_raw, float("nan"))
            vbw = _num(r.get("vbw_deg"), 6.5)
            status = str(r.get("status", "") or "")

            edge = coverage_edge_distance_m(h, mt + et, vbw)
            radius = min(max(edge, st.min_radius_m), st.max_radius_m)

            sev, notes = worst.get(cid, ("ok", []))
            if cid not in worst and kpi_by_cell is None and not rec_by_cell:
                sev = "nodata"
            if status.lower().startswith("plan"):
                sev = "nodata"
            color_key = sev if sev in _SEV_COLOR else "ok"

            rows = [("Sector / band", f"{r.get('band','') or ''} "
                                      f"S{r.get('sector','?')} · az {az:.0f}°"),
                    ("Status", status or "—"),
                    ("Antenna height", f"{h:.1f} m"),
                    ("Total downtilt", f"{mt + et:.1f}° (mech {mt:.1f} + elec "
                     f"{et:.1f}{' — assumed' if tilt_assumed else ''})"),
                    ("Modelled 3 dB reach", f"{edge/1000:.1f} km")]
            k = (kpi_by_cell or {}).get(cid, {})
            for kn, lbl in (("avg_rsrp_dbm", "Avg RSRP"),
                            ("avg_rsrq_db", "Avg RSRQ"),
                            ("avg_sinr_db", "Avg SINR"),
                            ("dl_user_thr_mbps", "DL user thr"),
                            ("dl_prb_util", "DL PRB"),
                            ("erab_drop_rate", "E-RAB drop"),
                            ("ho_sr", "HO SR"),
                            ("ta_p95_m", "P95 TA"),
                            ("total_traffic_gb", "Traffic GB")):
                if kn in k and k[kn] == k[kn]:
                    rows.append((lbl, f"{k[kn]:.2f}"))

            balloon_notes = list(notes)
            rec = rec_by_cell.get(cid)
            if rec is not None:
                balloon_notes.append(
                    f"ACTION ({rec.priority}): {rec.action_type} -> "
                    f"{rec.recommended_value}")

            placemarks.append(
                "<Placemark>"
                f"<name>{escape(cid)}</name>"
                f"<styleUrl>#beam_{color_key}</styleUrl>"
                f"<description>{_balloon(cid, rows, balloon_notes)}</description>"
                "<Polygon><outerBoundaryIs><LinearRing><coordinates>"
                f"{_wedge_coords(lat, lon, az, hbw, radius, st.arc_steps)}"
                "</coordinates></LinearRing></outerBoundaryIs></Polygon>"
                "</Placemark>"
            )

            is_overshoot = any(
                d.entity_id == cid and d.problem_class == "overshooting"
                for d in (diagnoses or []))
            if is_overshoot or edge > st.max_radius_m:
                far = min(edge, st.overshoot_radius_cap_m)
                y2, x2 = _offset(lat, lon, az, far)
                placemarks.append(
                    "<Placemark><name>overshoot reach</name>"
                    "<styleUrl>#overshoot</styleUrl><LineString><tessellate>1"
                    f"</tessellate><coordinates>{lon:.6f},{lat:.6f},0 "
                    f"{x2:.6f},{y2:.6f},0</coordinates></LineString></Placemark>"
                )

        folders.append(f"<Folder><name>{escape(str(site_id))}</name>"
                       + "".join(placemarks) + "</Folder>")

    kml = _kml_doc(title, styles, "\n".join(folders))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", kml)
    return out_path


# --------------------------------------------------------------------------- #
_CAT_COLOR = {                     # KML aabbggrr
    "new_site_needed":   "ff0000ff",
    "wrong_server":      "ffff00ff",
    "off_axis_azimuth":  "ff00a5ff",
    "far_coverage":      "ff00d7ff",
    "near_on_axis_other":"ffc0c0c0",
    "interference":      "ffff0000",
    "congestion":        "ffcc6600",
    "indoor":            "ff9370db",
    "availability":      "ff808080",
    "not_rf":            "ffb0b0b0",
    "coverage_or_capacity": "ff30a0e0",
    "insufficient_data": "ffe0e0e0",
}


def build_complaint_kmz(
    result,
    out_path: str | Path,
    *,
    title: str = "RF Complaint Analysis",
    link_to_site: bool = True,
    max_points: int | None = None,
) -> Path:
    """KMZ of analysed complaints: a pin per ticket coloured by category, a
    line to its serving site, and the site pins. ``result`` is a
    :class:`rfopt.complaints.ComplaintResult`.
    """
    import pandas as pd

    findings = result.findings[:max_points] if max_points else result.findings
    sdb = result.site_db
    site_xy = (sdb.dropna(subset=["latitude", "longitude"])
               .groupby("site_id")[["latitude", "longitude"]].mean())

    styles = "".join(
        f'<Style id="c_{k}"><IconStyle><color>{c}</color><scale>0.8</scale>'
        f"<Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png"
        f"</href></Icon></IconStyle><LineStyle><color>80{c[2:]}</color>"
        f"<width>1.4</width></LineStyle></Style>"
        for k, c in _CAT_COLOR.items()
    )

    by_cat: dict[str, list[str]] = {}

    for f in findings:
        if f.latitude is None or f.longitude is None:
            continue
        cat = f.category if f.category in _CAT_COLOR else "insufficient_data"
        rows = [
            ("Ticket", f.ticket_id),
            ("When", f.problem_time or f.create_time),
            ("Area", f"{f.sub_district or ''} {f.city or ''}".strip()),
            ("Serving", f"{f.serving_cell}  ({f.loc_source})"),
            ("Distance", f"{f.distance_m:.0f} m" if f.distance_m else "—"),
            ("Azimuth offset", f"{f.az_offset_deg:.0f} deg"
             if f.az_offset_deg is not None else "—"),
            ("Nearest site", f"{f.nearest_site} "
             f"({f.nearest_distance_m:.0f} m)" if f.nearest_distance_m else "—"),
            ("Category", f"{cat}  (conf {f.confidence:.0%})"),
        ]
        notes = [f.finding]
        if f.recommended_action:
            notes.append(f"ACTION: {f.recommended_action} -> {f.action_detail}")
        if f.engineer_rf_analysis:
            notes.append(f"Engineer: {f.engineer_rf_analysis}"
                         + (f" / {f.engineer_closure_code}"
                            if f.engineer_closure_code else "")
                         + (f"  [{f.agreement}]" if f.agreement != "n/a" else ""))
        line = ""
        if link_to_site and f.serving_site in site_xy.index and \
                f.loc_source in ("ticket", "subdistrict"):
            sy, sx = site_xy.loc[f.serving_site]
            line = ("<LineString><tessellate>1</tessellate><coordinates>"
                    f"{f.longitude:.6f},{f.latitude:.6f},0 "
                    f"{sx:.6f},{sy:.6f},0</coordinates></LineString>")
        pm = ("<Placemark>"
              f"<name>{escape(f.ticket_id)}</name>"
              f"<styleUrl>#c_{cat}</styleUrl>"
              f"<description>{_balloon(f.ticket_id, rows, notes)}</description>"
              "<MultiGeometry>"
              f"<Point><coordinates>{f.longitude:.6f},{f.latitude:.6f},0"
              "</coordinates></Point>"
              f"{line}</MultiGeometry></Placemark>")
        by_cat.setdefault(cat, []).append(pm)

    folders = [f"<Folder><name>{escape(cat)} ({len(pms)})</name>"
               + "".join(pms) + "</Folder>"
               for cat, pms in sorted(by_cat.items(), key=lambda x: -len(x[1]))]

    # site hotspots: pin per serving site, scaled + coloured by ticket count
    site_counts: dict[str, int] = {}
    for f in findings:
        if f.serving_site:
            site_counts[f.serving_site] = site_counts.get(f.serving_site, 0) + 1
    hot_pm = []
    for site, n in sorted(site_counts.items(), key=lambda x: -x[1]):
        if site not in site_xy.index:
            continue
        sy, sx = site_xy.loc[site]
        col = ("ff0000ff" if n >= 20 else "ff0080ff" if n >= 8
               else "ff00c8c8" if n >= 3 else "ff40c840")
        scale = min(0.6 + n / 25.0, 2.2)
        hot_pm.append(
            f'<Placemark><name>{escape(site)}  ({n})</name>'
            f'<Style><IconStyle><color>{col}</color><scale>{scale:.2f}</scale>'
            "<Icon><href>http://maps.google.com/mapfiles/kml/shapes/donut.png"
            "</href></Icon></IconStyle></Style>"
            f"<Point><coordinates>{sx:.6f},{sy:.6f},0</coordinates></Point>"
            "</Placemark>")
    folders.append(f"<Folder><name>Site hotspots ({len(hot_pm)})</name>"
                   + "".join(hot_pm) + "</Folder>")

    kml = _kml_doc(title, styles, "\n".join(folders))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", kml)
    return out_path


# --------------------------------------------------------------------------- #
_PRIO_COLOR = {"P1": "ff0000ff", "P2": "ff0080ff", "P3": "ff00d7ff",
               "P4": "ff40c840"}


def build_worklist_kmz(worklist_df, site_db, out_path: str | Path,
                       *, title: str = "Daily Worklist") -> Path:
    """KMZ of the analysed daily worklist.

    One labelled pin per serving site (name = site ID), coloured by the site's
    best priority, grouped into P1..P4 folders. Balloon carries tickets today,
    likely cause, KPI evidence, parameter flags and the next check.
    """
    import pandas as pd

    sx = (site_db.dropna(subset=["latitude", "longitude"])
          .groupby("site_id")[["latitude", "longitude"]].mean())
    styles = "".join(
        f'<Style id="wp_{k}"><IconStyle><color>{c}</color><scale>1.15</scale>'
        "<Icon><href>http://maps.google.com/mapfiles/kml/shapes/target.png"
        "</href></Icon></IconStyle><LabelStyle><scale>0.85</scale></LabelStyle>"
        "</Style>" for k, c in _PRIO_COLOR.items())

    by_prio: dict[str, list[str]] = {}
    for sid, g in worklist_df.groupby("site_id"):
        sid = str(sid)
        if sid not in sx.index or not sid or sid in ("nan", "(missing)"):
            continue
        la, lo = sx.loc[sid]
        if la != la or lo != lo:
            continue
        prio = sorted(g["priority"].dropna())[0] if g["priority"].notna().any() else "P4"
        r0 = g.iloc[0]
        rows = [
            ("Tickets today", len(g)),
            ("Priority", prio),
            ("Likely cause", r0.get("likely_cause", "")),
            ("KPI", f"{r0.get('kpi_status', '')} — {r0.get('kpi_evidence', '')}"),
            ("Parameters", f"{r0.get('param_severity', '')}  "
                           f"{r0.get('param_flags', '')}"),
            ("Repeat complaints", r0.get("site_complaint_history")),
            ("Tickets", ", ".join(str(t) for t in g["ticket_id"].head(8))),
        ]
        pm = (f"<Placemark><styleUrl>#wp_{prio}</styleUrl>"
              f"<name>{escape(sid)}</name>"
              f"<description>{_balloon(f'{sid}  ({prio})', rows, [str(r0.get('next_check', ''))])}"
              f"</description><Point><coordinates>{lo:.6f},{la:.6f},0"
              f"</coordinates></Point></Placemark>")
        by_prio.setdefault(prio, []).append(pm)

    folders = "".join(
        f"<Folder><name>{p} ({len(by_prio.get(p, []))})</name><open>{1 if p=='P1' else 0}</open>"
        + "".join(by_prio.get(p, [])) + "</Folder>"
        for p in ("P1", "P2", "P3", "P4"))
    kml = _kml_doc(title, styles, folders)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", kml)
    return out_path
