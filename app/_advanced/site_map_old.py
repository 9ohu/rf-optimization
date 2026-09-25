"""Site / sector map + KMZ export (R5 or any region)."""

from __future__ import annotations

import io
import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# plotly >=6 renamed Scattermapbox -> Scattermap (MapLibre, no token needed)
_ScatterMap = getattr(go, "Scattermap", None) or go.Scattermapbox
_MAP_KEY = "map" if hasattr(go, "Scattermap") else "mapbox"
_STYLE_KEY = "map_style" if _MAP_KEY == "map" else "mapbox_style"

from _shared import get_result, page_setup, require_result, settings

page_setup("Site Map", "🗺️")
require_result()
res = get_result()
cfg = settings()

st.title("🗺️ Site / Sector Map")

if res.site_db is None or not {"latitude", "longitude"} <= set(res.site_db.columns):
    st.warning("Load a site database (with latitude/longitude) on the Home page "
               "to use the map and KMZ export.")
    st.stop()

sdb = res.site_db.copy()
prefix = st.text_input("Region / site-prefix filter", cfg["region"] or "R5")
if prefix:
    mask = sdb["site_id"].astype(str).str.upper().str.startswith(prefix.upper())
    if "region" in sdb.columns:
        mask = mask | sdb["region"].astype(str).str.contains(prefix, case=False,
                                                             na=False)
    sdb = sdb[mask]
if sdb.empty:
    st.error("No sites match that filter.")
    st.stop()

# worst diagnosis per cell -> colour
rank = {"": 0, "ok": 0, "warning": 1, "critical": 2}
worst: dict[str, tuple[str, list[str]]] = {}
for d in res.diagnoses:
    cur = worst.get(d.entity_id)
    line = f"[{d.severity.upper()}] {d.title}"
    if cur is None or rank[d.severity] > rank[cur[0]]:
        worst[d.entity_id] = (d.severity, [*(cur[1] if cur else []), line])
    else:
        cur[1].append(line)
rec_by_cell = {}
for r in res.recommendations:
    rec_by_cell.setdefault(r.cell_id, r)

COL = {"critical": "#e5484d", "warning": "#f5a524", "ok": "#46a758",
       "": "#8b8b8b"}
FILL = {"critical": "rgba(229,72,77,0.28)", "warning": "rgba(245,165,36,0.25)",
        "ok": "rgba(70,167,88,0.18)", "": "rgba(139,139,139,0.15)"}
_M_PER_DEG = 111_320.0


def wedge(lat, lon, az, hbw, radius_m, steps=16):
    xs, ys = [lon], [lat]
    for i in range(steps + 1):
        b = math.radians(az - hbw / 2 + hbw * i / steps)
        ys.append(lat + radius_m * math.cos(b) / _M_PER_DEG)
        xs.append(lon + radius_m * math.sin(b) /
                  (_M_PER_DEG * math.cos(math.radians(lat))))
    xs.append(lon); ys.append(lat)
    return xs, ys


from rfopt.actions.geometry import coverage_edge_distance_m

kpi_by_cell = {r["cell_id"]: r.dropna().to_dict()
               for _, r in res.agg_cell.iterrows()}

fig = go.Figure()
for _, r in sdb.iterrows():
    cid = str(r["cell_id"])
    lat, lon = float(r["latitude"]), float(r["longitude"])
    az = float(r.get("azimuth_deg", 0) or 0)
    hbw = float(r.get("hbw_deg", 65) or 65)
    h = float(r.get("antenna_height_m", 30) or 30)
    tilt = float(r.get("mech_tilt_deg", 0) or 0) + float(r.get("elec_tilt_deg", 3) or 3)
    edge = coverage_edge_distance_m(h, tilt, float(r.get("vbw_deg", 6.5) or 6.5))
    radius = min(max(edge, 150), 3500)
    sev = worst.get(cid, ("", []))[0]
    xs, ys = wedge(lat, lon, az, hbw, radius)
    k = kpi_by_cell.get(cid, {})
    hover = (f"<b>{cid}</b><br>az {az:.0f}° · tilt {tilt:.1f}° · h {h:.0f} m<br>"
             f"3 dB reach ~{edge/1000:.1f} km<br>"
             + "<br>".join(f"{n}: {k[c]:.2f}" for c, n in
                           [("avg_rsrp_dbm", "RSRP"), ("avg_sinr_db", "SINR"),
                            ("dl_prb_util", "PRB"), ("erab_drop_rate", "drop%")]
                           if c in k))
    if worst.get(cid):
        hover += "<br>" + "<br>".join(worst[cid][1])
    fig.add_trace(_ScatterMap(
        lat=ys, lon=xs, mode="lines", fill="toself",
        fillcolor=FILL[sev],
        line=dict(color=COL[sev], width=1), name=cid, hoverinfo="text",
        text=hover, showlegend=False))

sites = sdb.groupby("site_id")[["latitude", "longitude"]].mean().reset_index()
fig.add_trace(_ScatterMap(
    lat=sites["latitude"], lon=sites["longitude"], mode="markers",
    marker=dict(size=8, color="#111"), text=sites["site_id"],
    hoverinfo="text", name="sites", showlegend=False))

fig.update_layout(**{
    _STYLE_KEY: "open-street-map",
    _MAP_KEY: dict(center=dict(lat=sites["latitude"].mean(),
                               lon=sites["longitude"].mean()), zoom=12),
    "height": 620, "margin": dict(l=0, r=0, t=0, b=0)})
st.plotly_chart(fig, use_container_width=True)
st.caption("🔴 critical · 🟠 warning · 🟢 ok — wedge length ≈ modelled 3 dB "
           "coverage reach.")

st.divider()
st.subheader("Export KMZ for Google Earth")
c1, c2 = st.columns([1, 2])
with c1:
    fname = st.text_input("File name", f"{prefix or 'sites'}_rf_map.kmz")
    if st.button("Build KMZ", type="primary"):
        from rfopt.geo import build_site_kmz
        import tempfile, pathlib
        tmp = pathlib.Path(tempfile.gettempdir()) / fname
        build_site_kmz(res.site_db, tmp, region=prefix or None,
                       region_prefix=prefix or None,
                       diagnoses=res.diagnoses,
                       recommendations=res.recommendations,
                       kpi_by_cell=kpi_by_cell, title=f"RF Optimisation — {prefix}")
        st.session_state["kmz_bytes"] = tmp.read_bytes()
        st.session_state["kmz_name"] = fname
with c2:
    if st.session_state.get("kmz_bytes"):
        st.download_button("⬇  Download KMZ", st.session_state["kmz_bytes"],
                           st.session_state["kmz_name"],
                           "application/vnd.google-earth.kmz",
                           use_container_width=True)
        st.caption("Sector beams coloured by health; overshooting cells get a "
                   "red 'actual reach' ray; balloons carry KPIs + the top "
                   "recommended action.")
