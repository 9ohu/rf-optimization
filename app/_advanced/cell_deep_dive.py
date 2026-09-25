"""Per-cell deep dive + the geometry-based RET / tilt / azimuth calculator."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from _shared import get_result, page_setup, require_result, settings

page_setup("Cell Deep Dive", "🔬")
require_result()
res = get_result()
cfg = settings()

st.title("🔬 Cell Deep-Dive & RET / Tilt Calculator")

cells = sorted(res.agg_cell["cell_id"].dropna().unique())
cell = st.selectbox("Cell", cells)
row = res.agg_cell[res.agg_cell["cell_id"] == cell].iloc[0]
sdb_row = None
if res.site_db is not None and "cell_id" in res.site_db.columns:
    m = res.site_db[res.site_db["cell_id"] == cell]
    if not m.empty:
        sdb_row = m.iloc[0]

# --------------------------------------------------------------------- #
c1, c2, c3 = st.columns(3)
with c1:
    st.subheader("Snapshot KPIs")
    for kn, lbl, u in [("avg_rsrp_dbm", "RSRP", " dBm"),
                       ("avg_rsrq_db", "RSRQ", " dB"),
                       ("avg_sinr_db", "SINR", " dB"),
                       ("dl_user_thr_mbps", "DL user thr", " Mbps"),
                       ("erab_drop_rate", "E-RAB drop", " %"),
                       ("ho_sr", "HO SR", " %"),
                       ("dl_prb_util", "DL PRB", " %"),
                       ("ta_p95_m", "P95 TA", " m"),
                       ("total_traffic_gb", "Traffic", " GB")]:
        if kn in row and pd.notna(row[kn]):
            st.metric(lbl, f"{row[kn]:.2f}{u}")
with c2:
    st.subheader("Geometry")
    if sdb_row is not None:
        for k, lbl in [("antenna_height_m", "Height (m)"),
                       ("azimuth_deg", "Azimuth (°)"),
                       ("mech_tilt_deg", "Mech tilt (°)"),
                       ("elec_tilt_deg", "Elec tilt / RET (°)"),
                       ("vbw_deg", "V beamwidth (°)")]:
            if k in sdb_row and pd.notna(sdb_row[k]):
                st.write(f"**{lbl}**: {float(sdb_row[k]):.1f}")
        isd = res.isd_by_site.get(str(row.get("site_id", "")))
        if isd:
            st.write(f"**Mean ISD**: {isd/1000:.2f} km")
    else:
        st.info("No site-database row for this cell — enter geometry manually "
                "in the calculator below.")
with c3:
    st.subheader("Diagnoses")
    ds = [d for d in res.diagnoses if d.entity_id == cell]
    if not ds:
        st.success("No problems diagnosed.")
    for d in ds:
        st.markdown(f"{'🔴' if d.severity=='critical' else '🟠'} **{d.title}** "
                    f"(conf {d.confidence:.0%})")
        if d.trend_note:
            st.caption(f"trend: {d.trend_note}")

# hourly trend
if not res.agg_hourly.empty:
    h = res.agg_hourly[res.agg_hourly["cell_id"] == cell].sort_values("datetime")
    if not h.empty:
        kk = st.multiselect("Plot KPIs over time",
                            [c for c in h.columns if c not in
                             ("cell_id", "site_id", "sector_id", "datetime",
                              "n_rows", "region", "band")],
                            default=[c for c in ["avg_rsrp_dbm", "avg_sinr_db",
                                                 "dl_user_thr_mbps",
                                                 "erab_drop_rate", "dl_prb_util"]
                                     if c in h.columns])
        if kk:
            plot = h.melt(id_vars="datetime", value_vars=kk,
                          var_name="KPI", value_name="value")
            fig = px.line(plot, x="datetime", y="value", facet_row="KPI")
            fig.update_yaxes(matches=None)
            fig.update_layout(height=110 * len(kk) + 80, showlegend=False,
                              margin=dict(l=0, r=0, t=20, b=0))
            st.plotly_chart(fig, use_container_width=True)

st.divider()
# ==================================================================== #
st.header("RET / Tilt / Azimuth Recommendation")
st.caption("Computes the action from geometry + link budget + KPIs — nothing "
           "fixed. Use it for a complaint location or to sanity-check a cell's "
           "tilt against its site spacing.")

from rfopt.actions.propagation import Environment
from rfopt.actions.recommend import (CellContext, ComplaintContext,
                                     recommend_for_complaint)
from rfopt.actions.geometry import (AntennaGeometry, elevation_angle_to_point_deg,
                                    vertical_pattern_gain_db)

g1, g2, g3 = st.columns(3)
with g1:
    height = st.number_input("Antenna height (m)", 5.0, 120.0,
                             float(sdb_row["antenna_height_m"]) if sdb_row is not None
                             and pd.notna(sdb_row.get("antenna_height_m")) else 30.0)
    azimuth = st.number_input("Azimuth (° from north)", 0.0, 360.0,
                              float(sdb_row["azimuth_deg"]) if sdb_row is not None
                              and pd.notna(sdb_row.get("azimuth_deg")) else 0.0)
    mech = st.number_input("Mechanical tilt (°)", 0.0, 20.0,
                           float(sdb_row["mech_tilt_deg"]) if sdb_row is not None
                           and pd.notna(sdb_row.get("mech_tilt_deg")) else 0.0)
with g2:
    ret_unit = st.selectbox("RET unit", ["deg", "tenths"],
                            index=0 if cfg["ret_unit"] == "deg" else 1)
    ret_raw = st.number_input(
        f"Current RET / electrical tilt ({ret_unit})", 0.0, 150.0,
        (float(sdb_row["elec_tilt_deg"]) * (10 if ret_unit == "tenths" else 1))
        if sdb_row is not None and pd.notna(sdb_row.get("elec_tilt_deg")) else
        (30.0 if ret_unit == "tenths" else 3.0))
    elec = ret_raw / 10.0 if ret_unit == "tenths" else ret_raw
    vbw = st.number_input("Vertical beamwidth (°)", 3.0, 20.0, 6.5)
    hbw = st.number_input("Horizontal beamwidth (°)", 30.0, 120.0, 65.0)
with g3:
    env_kind = st.selectbox("Environment", ["urban", "suburban", "rural"],
                            index=["urban", "suburban", "rural"].index(cfg["env_kind"]))
    freq = st.number_input("Carrier frequency (MHz)", 400.0, 3800.0, 1800.0)
    isd_default = res.isd_by_site.get(str(row.get("site_id", "")), 1000.0)
    isd = st.number_input("Inter-site distance (m)", 100.0, 20000.0,
                          float(isd_default))
    mode = st.radio("Target mode", ["boresight", "edge"], horizontal=True,
                    help="boresight = aim beam peak at the location; "
                         "edge = treat the location as the cell edge")

st.markdown("**Target location** — give distance + bearing, or lat/lon for both "
            "the site and the location.")
t1, t2, t3, t4 = st.columns(4)
distance = t1.number_input("Distance site→location (m)", 0.0, 30000.0, 1500.0)
bearing = t2.number_input("Bearing site→location (°)", 0.0, 360.0, float(azimuth))
indoor = t3.checkbox("Indoor location")
meas_rsrp = t4.number_input("Measured RSRP at location (dBm, optional)",
                            -140.0, -40.0, -110.0)

use_latlon = st.checkbox("Use coordinates instead")
site_lat = site_lon = comp_lat = comp_lon = None
if use_latlon:
    p1, p2, p3, p4 = st.columns(4)
    site_lat = p1.number_input("Site lat", -90.0, 90.0,
                               float(sdb_row["latitude"]) if sdb_row is not None
                               and "latitude" in sdb_row else 24.71, format="%.6f")
    site_lon = p2.number_input("Site lon", -180.0, 180.0,
                               float(sdb_row["longitude"]) if sdb_row is not None
                               and "longitude" in sdb_row else 46.67, format="%.6f")
    comp_lat = p3.number_input("Location lat", -90.0, 90.0, 24.726, format="%.6f")
    comp_lon = p4.number_input("Location lon", -180.0, 180.0, 46.673, format="%.6f")

if st.button("Compute recommendation", type="primary"):
    kpis = {k: float(row[k]) for k in
            ("avg_rsrp_dbm", "avg_rsrq_db", "avg_sinr_db", "dl_user_thr_mbps",
             "dl_prb_util", "total_traffic_gb", "ta_p95_m", "pct_rsrp_poor")
            if k in row and pd.notna(row[k])}
    ctx = CellContext(
        cell_id=cell, site_id=str(row.get("site_id", "")),
        antenna_height_m=height, azimuth_deg=azimuth, mech_tilt_deg=mech,
        elec_tilt_deg=elec, vbw_deg=vbw, hbw_deg=hbw, ret_unit=ret_unit,
        env=Environment(kind=env_kind, frequency_mhz=freq),
        inter_site_distance_m=isd, kpis=kpis,
        latitude=site_lat, longitude=site_lon,
    )
    cc = ComplaintContext(
        cell=ctx,
        complaint_lat=comp_lat, complaint_lon=comp_lon,
        distance_m=None if use_latlon else distance,
        bearing_deg_from_site=None if use_latlon else bearing,
        indoor=indoor,
        measured_rsrp_dbm=meas_rsrp if meas_rsrp < -40 else None,
    )
    rec = recommend_for_complaint(cc, target_mode=mode)
    st.session_state["deepdive_rec"] = rec

rec = st.session_state.get("deepdive_rec")
if rec is not None and rec.cell_id == cell:
    st.success(f"**{rec.problem}** · priority {rec.priority} · "
               f"confidence {rec.confidence:.0%}")
    a, b = st.columns([3, 2])
    with a:
        st.markdown(f"**Root cause**\n\n{rec.root_cause}")
        st.markdown("**Evidence**")
        for e in rec.evidence:
            st.markdown(f"- {e}")
        st.markdown("**Calculation**")
        st.code("\n".join(rec.math_notes))
    with b:
        st.markdown(f"**Action** · {rec.action_type}")
        st.markdown(f"**Parameter** · {rec.parameter}")
        st.markdown(f"**Current** · {rec.current_value}")
        st.markdown(f"**Recommended** · {rec.recommended_value}")
        st.markdown(f"**Expected impact** · {rec.expected_impact}")
    st.markdown("**Risks**")
    for r in rec.risks:
        st.markdown(f"- {r}")
    st.markdown("**Monitor** · " + ", ".join(rec.monitor_kpis))
    if rec.alternatives:
        st.markdown("**Alternatives**")
        for al in rec.alternatives:
            st.markdown(f"- {al}")

    # vertical-pattern illustration
    st.divider()
    st.subheader("Vertical pattern gain vs distance")
    d = np.linspace(100, max(distance * 2, 4 * isd), 240)
    elevs = np.degrees(np.arctan2(height - 1.5, d))
    cur_t = mech + elec
    try:
        new_t = float(rec.math_notes[-2].split("total ")[1].split(" ")[0]) \
            if any("total" in m for m in rec.math_notes) else cur_t
    except Exception:
        new_t = cur_t
    gcur = [vertical_pattern_gain_db(abs(e - cur_t), vbw) for e in elevs]
    gnew = [vertical_pattern_gain_db(abs(e - new_t), vbw) for e in elevs]
    pf = pd.DataFrame({"distance_m": d,
                       f"current ({cur_t:.1f}°)": gcur,
                       f"recommended ({new_t:.1f}°)": gnew})
    fig = px.line(pf.melt("distance_m", var_name="tilt", value_name="gain_dB"),
                  x="distance_m", y="gain_dB", color="tilt")
    fig.add_vline(x=distance, line_dash="dash",
                  annotation_text="target")
    fig.add_vline(x=isd, line_dash="dot", annotation_text="ISD")
    fig.update_layout(height=360, margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, use_container_width=True)
