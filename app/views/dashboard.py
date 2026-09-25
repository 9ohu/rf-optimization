"""Dashboard - the daily complaint worklist (R5).

The day's ticket list + the Engineering Parameter Tracker (+ the 4G hourly
KPI Data and the complaint history) — the active data in Data Resources.
Every ticket gets a per-site parameter audit, a KPI-backed verdict, a likely
cause, a concrete next check and a P1-P4 priority.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

import _resources as R
from _shared import (PRIO_HEX as PRIO_COLOR, load_history as _history,
                     load_kpi_files as _load_kpi_files, load_params as _load_params)
from _ui import header as _header, kpi_cards as _kpi_cards

_header("RF Optimization", "Daily complaint worklist")

# --------------------------------------------------------------------------- #
with st.sidebar:
    tech = st.selectbox("Technology", ["LTE", "UMTS", "GSM"], index=0)
    ep_path, target = R.ep_path(), R.target()
    kpi_4g = next((tuple(p for p, _, _ in g) for kind, g in R.kpi_groups()
                   if kind == "4G"), ())
    hist_path = R.history_path()
    run = st.button("Analyse worklist", icon=":material/play_arrow:",
                    type="primary", width="stretch",
                    disabled=ep_path is None or target is None)

if run:
    from rfopt.complaints.worklist import load_worklist, process_worklist

    pl = _load_params(ep_path, tech)
    hist = _history(hist_path) if hist_path else {}
    kpi_load = _load_kpi_files(kpi_4g) if kpi_4g else None
    kpi_df = kpi_load.df if kpi_load is not None else None

    wl = load_worklist(str(target.path))
    df = process_worklist(wl, pl.df, kpi=kpi_df, history_counts=hist)

    st.session_state["wl_df"] = df
    st.session_state["wl_audits"] = df.attrs["audits"]
    st.session_state["wl_params"] = pl
    st.session_state["wl_kpi"] = kpi_load
    st.session_state["wl_name"] = target.name

df = st.session_state.get("wl_df")
if df is None:
    st.info("\u2b05 Add the **Site Details Data (EP)** and the **Complaint Data** (Daily "
            "Target) once in **Data Resources**, then press **Analyse worklist**. The 4G "
            "KPI Data and a CC Process history are used when they are there.")
    R.link("Open Data Resources")
    st.stop()

pl = st.session_state["wl_params"]
audits = st.session_state["wl_audits"]
kl = st.session_state.get("wl_kpi")

# ---- headline ---------------------------------------------------------- #
_n = max(len(df), 1)
_p1 = int((df["priority"] == "P1").sum())
_kc = int(df.get("kpi_verdict", pd.Series(dtype=str)).astype(bool).sum())
_pi = int(df["param_severity"].isin(["warning", "critical"]).sum())
_mr = int(df["param_flags"].str.contains("missing_ret", na=False).sum())
_kpi_cards([
    dict(title="Tickets", value=f"{len(df):,}", icon="file", tone="blue",
         note=f"{df['site_id'].nunique():,} sites"),
    dict(title="P1", value=_p1, icon="x", tone="red", pct=100 * _p1 / _n,
         note="highest priority"),
    dict(title="KPI-confirmed", value=_kc, icon="chart", tone="orange",
         pct=100 * _kc / _n, note="backed by the hourly KPI"),
    dict(title="Param issue", value=_pi, icon="alert", tone="amber",
         pct=100 * _pi / _n, note="warning or critical audit"),
    dict(title="Missing RET", value=_mr, icon="ruler", tone="gray",
         pct=100 * _mr / _n, note="no RET on a serving sector"),
])

with st.expander("Data notes"):
    for n in pl.notes:
        st.caption("\u2022 param file: " + n)
    if kl is not None:
        for n in kl.notes:
            st.caption("\u2022 KPI file: " + n)
        st.caption(f"\u2022 KPI window: **{df['kpi_window'].iloc[0]}** \u2014 if "
                   "this does not cover the tickets' problem hours the verdicts "
                   "use the site's worst hour in the available window.")
    else:
        st.caption("\u2022 No KPI file \u2014 'coverage_or_capacity' tickets stay "
                   "unresolved; add the hourly KPI export to confirm them.")

tab_wl, tab_map, tab_sum, tab_flags, tab_export = st.tabs(
    ["Worklist", "Map", "Summary", "Site flags", "Export"])

# ---- worklist -------------------------------------------------------- #
with tab_wl:
    fc = st.columns([1, 1, 2])
    prio = fc[0].multiselect("Priority", ["P1", "P2", "P3", "P4"],
                             default=["P1", "P2", "P3"])
    causes = sorted(df["likely_cause"].unique())
    cause = fc[1].multiselect("Likely cause", causes, default=causes)
    q = fc[2].text_input("Filter site / ticket / text")
    v = df[df["priority"].isin(prio) & df["likely_cause"].isin(cause)]
    if q:
        m = pd.Series(False, index=v.index)
        for col in ("site_id", "ticket_id", "next_check", "param_flags",
                    "kpi_evidence", "comment"):
            if col in v.columns:
                m |= v[col].astype(str).str.contains(q, case=False, na=False)
        v = v[m]
    show = ["priority", "ticket_id", "site_id", "city", "affected_service",
            "problem_time", "likely_cause", "confidence", "next_check",
            "kpi_verdict", "kpi_evidence", "param_severity", "param_flags",
            "site_complaint_history", "bands", "tilt_summary", "comment"]
    st.dataframe(v[[x for x in show if x in v.columns]],
                 width="stretch", height=430)
    st.download_button("Download worklist (CSV)", v.to_csv(index=False).encode(),
                       "worklist_analysis.csv", "text/csv")

    ids = v["site_id"].dropna().unique().tolist()
    if ids:
        sid = st.selectbox("Inspect a site", ids)
        a = audits.get(sid)
        if a:
            dot = {"critical": "\U0001F534", "warning": "\U0001F7E0"}.get(
                a.severity, "\U0001F7E2")
            st.markdown(f"### {sid} \u2014 {a.enodeb_name}  ·  {dot} {a.severity}")
            st.markdown(
                f"{a.n_sectors} sectors · bands {', '.join(a.bands)} · "
                f"h {a.height_m} m · azimuths "
                f"{', '.join(f'{x:.0f}' for x in a.azimuths)} · "
                f"tilt {a.tilt_summary} · nearest site {a.nearest_site} "
                f"({a.nearest_site_m and round(a.nearest_site_m)} m)")
            if a.flags:
                st.dataframe(pd.DataFrame([f.as_dict() for f in a.flags]),
                             width="stretch")
            else:
                st.success("No parameter issue found for this site.")
            st.dataframe(pd.DataFrame(a.sector_table), width="stretch")

# ---- map ------------------------------------------------------------ #
with tab_map:
    import plotly.graph_objects as go
    _SM = getattr(go, "Scattermap", None) or go.Scattermapbox
    _MK = "map" if hasattr(go, "Scattermap") else "mapbox"
    _SK = "map_style" if _MK == "map" else "mapbox_style"

    sx = (pl.df.dropna(subset=["latitude", "longitude"])
          .groupby("site_id")[["latitude", "longitude"]].mean())
    wl_sites = df.groupby("site_id").agg(
        tickets=("ticket_id", "size"), priority=("priority", "min"),
        cause=("likely_cause", "first"), ev=("kpi_evidence", "first"),
        nxt=("next_check", "first")).reset_index()
    wl_sites = wl_sites.merge(sx.reset_index(), on="site_id", how="inner")

    show_all = st.checkbox(f"Show all {len(sx):,} R5 sites (faint)", value=False)
    fig = go.Figure()
    if show_all:
        fig.add_trace(_SM(lat=sx["latitude"], lon=sx["longitude"], mode="markers",
                          marker=dict(size=4, color="#c9c9c9"),
                          hoverinfo="skip", showlegend=False))
    for p in ("P4", "P3", "P2", "P1"):
        g = wl_sites[wl_sites["priority"] == p]
        if g.empty:
            continue
        fig.add_trace(_SM(
            lat=g["latitude"], lon=g["longitude"], mode="markers+text",
            marker=dict(size=(9 + 3 * g["tickets"].clip(upper=6)),
                        color=PRIO_COLOR[p]),
            text=g["site_id"], textposition="top center",
            textfont=dict(size=9),
            customdata=g[["tickets", "cause", "ev", "nxt"]],
            hovertemplate="<b>%{text}</b><br>%{customdata[0]} ticket(s) \u00b7 "
                          + p + "<br>%{customdata[1]}<br>%{customdata[2]}"
                          "<br><i>%{customdata[3]}</i><extra></extra>",
            name=p))
    ctr = wl_sites if len(wl_sites) else sx.reset_index()
    fig.update_layout(**{
        _SK: "open-street-map",
        _MK: dict(center=dict(lat=ctr["latitude"].mean(),
                              lon=ctr["longitude"].mean()), zoom=10),
        "height": 620, "margin": dict(l=0, r=0, t=0, b=0),
        "legend": dict(orientation="h", yanchor="bottom", y=0.01,
                       bgcolor="rgba(11,31,51,.85)",
                       font=dict(color="#E2E8F0")),
        "paper_bgcolor": "rgba(0,0,0,0)"})
    st.plotly_chart(fig, width="stretch")
    st.caption("\U0001F534 P1 \u00b7 \U0001F7E0 P2 \u00b7 \U0001F7E1 P3 \u00b7 "
               "\U0001F7E2 P4 \u2014 dot size \u221d tickets today. Hover a site "
               "for the cause + next check.")

# ---- summary ------------------------------------------------------ #
with tab_sum:
    s1, s2 = st.columns(2)
    s1.write("**By priority**")
    s1.bar_chart(df["priority"].value_counts().sort_index())
    s2.write("**By likely cause**")
    s2.bar_chart(df["likely_cause"].value_counts())
    st.write("**Sites with the most tickets today**")
    top = (df.groupby("site_id").agg(
        tickets=("ticket_id", "size"), priority=("priority", "min"),
        cause=("likely_cause", "first"), kpi_evidence=("kpi_evidence", "first"),
        history=("site_complaint_history", "first"))
        .sort_values(["tickets", "history"], ascending=False).head(25))
    st.dataframe(top, width="stretch")

# ---- site flags ------------------------------------------------- #
with tab_flags:
    rows = [{"site_id": sid, "enodeb": a.enodeb_name, **f.as_dict()}
            for sid, a in audits.items() for f in a.flags]
    if rows:
        fd = pd.DataFrame(rows)
        pick = st.multiselect("Flag", sorted(fd["code"].unique()),
                              default=sorted(fd["code"].unique()))
        st.dataframe(fd[fd["code"].isin(pick)], width="stretch",
                     height=460)
    else:
        st.info("No parameter flags across the worklist sites.")

# ---- export ---------------------------------------------------- #
with tab_export:
    name = st.session_state.get("wl_name", "Target").rsplit(".", 1)[0]
    e1, e2 = st.columns(2)
    with e1:
        if st.button("Build Excel report", type="primary",
                     width="stretch"):
            from rfopt.complaints.worklist import write_worklist_report
            tmp = Path(tempfile.gettempdir()) / f"{name}_RF_analysis.xlsx"
            write_worklist_report(df, audits, tmp)
            st.session_state["wl_xlsx"] = tmp.read_bytes()
        if st.session_state.get("wl_xlsx"):
            st.download_button(
                "\u2b07 Excel (4 sheets)", st.session_state["wl_xlsx"],
                f"{name}_RF_analysis.xlsx",
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet", width="stretch")
    with e2:
        if st.button("Build worklist KMZ", width="stretch"):
            from rfopt.geo import build_worklist_kmz
            tmp = Path(tempfile.gettempdir()) / f"{name}_worklist.kmz"
            build_worklist_kmz(df, pl.df, tmp, title=f"{name} \u2014 worklist")
            st.session_state["wl_kmz"] = tmp.read_bytes()
        if st.session_state.get("wl_kmz"):
            st.download_button(
                "\u2b07 KMZ (site IDs + priority)", st.session_state["wl_kmz"],
                f"{name}_worklist.kmz",
                "application/vnd.google-earth.kmz", width="stretch")
    st.caption("The KMZ pins every serving site at its exact location, labelled "
               "with the site ID, coloured P1\u2013P4, grouped into priority "
               "folders. Open in Google Earth.")
