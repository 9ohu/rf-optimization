"""KPI analysis: tables, trends, threshold breaches, busy hour."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from _shared import page_setup, require_result, get_result, style_severity

page_setup("KPI Analysis", "📊")
require_result()
res = get_result()

st.title("📊 KPI Analysis")

tab_kpi, tab_breach, tab_trend, tab_bh = st.tabs(
    ["KPI tables", "Threshold breaches", "Trends & anomalies", "Busy hour"])

# --------------------------------------------------------------------- #
with tab_kpi:
    level = st.radio("Aggregation level", ["cell", "sector", "site"],
                     horizontal=True)
    df = {"cell": res.agg_cell, "sector": res.agg_sector,
          "site": res.agg_site}[level]
    dims = [c for c in ("region", "site_id", "sector_id", "cell_id", "band")
            if c in df.columns]
    kpis = [c for c in df.columns if c not in dims + ["datetime", "n_rows"]]
    pick = st.multiselect("KPIs to show", kpis,
                          default=[k for k in
                                   ["avg_rsrp_dbm", "avg_rsrq_db", "avg_sinr_db",
                                    "dl_user_thr_mbps", "erab_drop_rate", "ho_sr",
                                    "dl_prb_util", "rrc_setup_sr",
                                    "cell_avail_pct", "total_traffic_gb"]
                                   if k in kpis])
    show = df[dims + pick].copy()
    st.dataframe(show, use_container_width=True, height=460)
    st.download_button("Download this table (CSV)",
                       show.to_csv(index=False).encode(),
                       f"kpi_{level}.csv", "text/csv")

    if not res.agg_hourly.empty and pick:
        st.divider()
        st.subheader("Hourly profile")
        cells = sorted(res.agg_hourly["cell_id"].unique())
        sel = st.multiselect("Cells", cells, default=cells[:3])
        ksel = st.selectbox("KPI", pick)
        h = res.agg_hourly[res.agg_hourly["cell_id"].isin(sel)]
        if not h.empty and ksel in h.columns:
            fig = px.line(h.sort_values("datetime"), x="datetime", y=ksel,
                          color="cell_id", markers=False)
            fig.update_layout(height=380, margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig, use_container_width=True)

# --------------------------------------------------------------------- #
with tab_breach:
    bd = res.breaches_df()
    if bd.empty:
        st.success("No KPI threshold breaches.")
    else:
        cc = st.columns(3)
        sev = cc[0].multiselect("Severity", sorted(bd["severity"].unique()),
                                default=sorted(bd["severity"].unique()))
        kf = cc[1].multiselect("KPI", sorted(bd["kpi"].unique()))
        sf = cc[2].text_input("Cell / site contains")
        v = bd[bd["severity"].isin(sev)]
        if kf:
            v = v[v["kpi"].isin(kf)]
        if sf:
            v = v[v["entity"].str.contains(sf, case=False) |
                  v["site"].str.contains(sf, case=False)]
        st.caption(f"{len(v)} breaches")
        st.dataframe(style_severity(v), use_container_width=True, height=460)
        st.download_button("Download breaches (CSV)",
                           v.to_csv(index=False).encode(),
                           "kpi_breaches.csv", "text/csv")

# --------------------------------------------------------------------- #
with tab_trend:
    td = res.trends_df()
    if td.empty:
        st.info("Trend detection needs a time series (hourly data or ≥ 5 days).")
    else:
        only = st.checkbox("Only degrading / step-down", value=True)
        v = td[td["verdict"].isin(["degrading", "step-down"])] if only else td
        v = v.sort_values("pct_change")
        st.dataframe(v, use_container_width=True, height=420)
        if not res.agg_hourly.empty and not v.empty:
            row = v.iloc[0]
            st.caption(f"Worst: {row['entity']} · {row['label']}")
            h = res.agg_hourly[res.agg_hourly["cell_id"] == row["entity"]]
            if row["kpi"] in h.columns:
                fig = px.line(h.sort_values("datetime"), x="datetime",
                              y=row["kpi"], markers=False,
                              title=f"{row['entity']} — {row['label']}")
                fig.update_layout(height=340, margin=dict(l=0, r=0, t=30, b=0))
                st.plotly_chart(fig, use_container_width=True)

# --------------------------------------------------------------------- #
with tab_bh:
    if res.busy_hour.empty:
        st.info("Busy-hour analysis needs hourly data.")
    else:
        st.dataframe(res.busy_hour, use_container_width=True, height=460)
        fig = px.histogram(res.busy_hour, x="busy_hour", nbins=24,
                           title="Distribution of site busy hours")
        fig.update_layout(height=320, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)
