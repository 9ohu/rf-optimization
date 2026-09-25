"""Batch customer-complaint RF analysis + KMZ + audit (R5)."""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from _shared import SAMPLE_DIR, page_setup, settings

page_setup("Complaint Analysis", "\U0001F4DE")
cfg = settings()

st.title("\U0001F4DE Customer-Complaint RF Analysis")
st.caption("Per-ticket geometry triage against the R5 site database: distance, "
           "azimuth offset, nearest / best serving sector → category + "
           "computed action, a KMZ, and (if the file carries the engineer's "
           "verdict) an agreement audit. R5 = site IDs BAS / NAS / EMA / SAM.")


with st.sidebar:
    st.header("1 · Site database")
    sdb_up = st.file_uploader("R5 site DB (.xlsx/.csv)",
                              type=["xlsx", "xls", "csv"], key="csdb")
    sdb_path = None
    if sdb_up is None:
        # the demo database bundled with the app only — never a file of the PC
        cand = SAMPLE_DIR / "r5_site_database.csv"
        if cand.exists():
            sdb_path = str(cand)
            st.caption(f"using {cand.name}")

    st.header("2 · Complaint tickets")
    cmp_up = st.file_uploader("Complaint export (.xlsx/.csv)",
                              type=["xlsx", "xls", "csv"], key="ccmp")
    cmp_path = None             # the export the user uploads above, nothing else

    st.header("3 · Options")
    rf_only = st.toggle("RF-group tickets only", value=True)
    open_only = st.toggle("Open / not-closed only", value=True)
    env_kind = st.selectbox("RF environment", ["urban", "suburban", "rural"],
                            index=["urban", "suburban", "rural"].index(cfg["env_kind"]))
    default_ret = st.number_input(
        "Assumed RET when cell details unknown (degrees)", 0.0, 12.0, 3.0, 0.5,
        help="Used only to size tilt/azimuth actions until you load a cell "
             "parameter file with real RET.")
    limit = st.number_input("Max tickets (0 = all)", 0, 100000, 4000, 500)
    run = st.button("▶ Analyse complaints", type="primary",
                    use_container_width=True,
                    disabled=(sdb_up is None and sdb_path is None)
                    or (cmp_up is None and cmp_path is None))

if run:
    from rfopt.complaints import (analyze_complaints, audit_table,
                                  load_complaints)
    from rfopt.ingest.sitedb import load_site_db, region_summary

    with st.spinner("Loading site database…"):
        sdb = load_site_db(sdb_up or sdb_path, region="R5")
    with st.spinner("Loading complaints…"):
        comp = load_complaints(cmp_up or cmp_path, region="R5",
                               rf_only=rf_only, open_only=open_only)
    with st.spinner(f"Analysing {len(comp)} tickets…"):
        res = analyze_complaints(comp, sdb, env_kind=env_kind,
                                 default_elec_tilt_deg=default_ret,
                                 max_tickets=(int(limit) or None))
    st.session_state["complaint_res"] = res
    st.session_state["complaint_sdb_sum"] = region_summary(sdb)
    st.session_state["complaint_cols"] = comp.attrs.get("_cols")

res = st.session_state.get("complaint_res")
if res is None:
    st.info("⬅ Pick a site DB + a complaint file and press **Analyse**. "
            "The tool looks in `~/Desktop/Audit` for the bundled R5 files.")
    st.stop()

s = res.summary()
sd = st.session_state.get("complaint_sdb_sum", {})
c = st.columns(5)
c[0].metric("Tickets", s["tickets"])
c[1].metric("Exact location", s.get("loc_exact", 0))
c[2].metric("Area centroid", s.get("loc_area_centroid", 0))
c[3].metric("Actions computed", s.get("actions_generated", 0))
c[4].metric("Audit agree+partial",
            f"{s.get('agree_or_partial', 0):.0%}" if "agree_or_partial" in s else "—")

exact = s.get("loc_exact", 0)
if exact == 0 and s["tickets"]:
    st.warning("**No per-ticket coordinates in this file.** Analysis runs at "
               "**site / area level** — complaint hotspots, isolation, "
               "new-site candidates. Per-ticket distance/azimuth/tilt needs the "
               "customer's own lat/long (the full *CC Process* export has it).")
elif exact < 0.5 * s["tickets"]:
    st.info(f"Only {exact}/{s['tickets']} tickets have an exact coordinate — "
            "the rest are analysed at site/area level.")

if cc_cols := st.session_state.get("complaint_cols"):
    with st.expander(f"Column mapping — {cc_cols.summary()}"):
        st.json(cc_cols.resolved)
for n in res.notes:
    st.caption("• " + n)

df = res.df()
tab_hot, tab_area, tab_cat, tab_tickets, tab_audit, tab_kmz = st.tabs(
    ["Site hotspots", "Area hotspots", "Categories", "Tickets", "Audit",
     "KMZ export"])

with tab_hot:
    from rfopt.complaints import aggregate_by_site
    hs = aggregate_by_site(res)
    if hs.empty:
        st.info("No serving-site information on the tickets.")
    else:
        st.caption(f"{len(hs)} serving sites carry complaints. "
                   "`isolated` = nearest on-air neighbour > 2.5 km.")
        f1, f2 = st.columns(2)
        min_t = f1.number_input("Min tickets", 1, 100, 3)
        only_iso = f2.checkbox("Isolated sites only", value=False)
        h = hs[hs["tickets"] >= min_t]
        if only_iso and "isolated" in h.columns:
            h = h[h["isolated"] == True]  # noqa: E712
        st.dataframe(h, use_container_width=True, height=440)
        st.download_button("Download site hotspots (CSV)",
                           hs.to_csv(index=False).encode(),
                           "site_hotspots.csv", "text/csv")

with tab_area:
    from rfopt.complaints import aggregate_by_subdistrict
    ad = aggregate_by_subdistrict(res)
    if ad.empty:
        st.info("No sub-district / area names on the tickets.")
    else:
        st.caption("Areas ranked by complaint volume — candidate "
                   "coverage / new-site zones. Centroid is coarse (±5–12 km).")
        st.dataframe(ad, use_container_width=True, height=440)
        st.download_button("Download area hotspots (CSV)",
                           ad.to_csv(index=False).encode(),
                           "area_hotspots.csv", "text/csv")

with tab_cat:
    vc = df["category"].value_counts().rename_axis("category").reset_index(name="tickets")
    st.bar_chart(vc.set_index("category"))
    st.dataframe(vc, use_container_width=True)
    st.markdown(
        "- **new_site_needed** — too far from any site for a tilt/power fix\n"
        "- **wrong_server** — a much closer, well-pointed sector exists "
        "(dominance / neighbour / repoint)\n"
        "- **off_axis_azimuth** — outside the serving sector's beam; a "
        "better sector is nearby\n"
        "- **far_coverage** — in-beam but far; tilt / RS-power candidate "
        "(needs real RET)\n"
        "- **near_on_axis_other** — close & on-axis; not macro geometry "
        "(indoor / capacity / device) — needs KPI\n"
        "- **interference / congestion / indoor** — from notes; confirm "
        "with KPI\n"
        "- **not_rf / availability / insufficient_data**")

with tab_tickets:
    cats = sorted(df["category"].unique())
    pick = st.multiselect("Category", cats, default=cats)
    only_act = st.checkbox("Only tickets with a computed action", value=False)
    v = df[df["category"].isin(pick)]
    if only_act:
        v = v[v["recommended_action"] != ""]
    show_cols = ["ticket_id", "problem_time", "city", "sub_district",
                 "serving_cell", "distance_m", "az_offset_deg", "nearest_site",
                 "nearest_distance_m", "best_sector_id", "category", "confidence",
                 "recommended_action", "engineer_rf_analysis", "agreement"]
    st.dataframe(v[[c for c in show_cols if c in v.columns]],
                 use_container_width=True, height=430)
    st.download_button("Download findings (CSV)",
                       v.to_csv(index=False).encode(), "complaint_findings.csv",
                       "text/csv")
    if len(v):
        tk = st.selectbox("Inspect a ticket", v["ticket_id"].tolist())
        f = next(x for x in res.findings if x.ticket_id == tk)
        st.markdown(f"**{f.ticket_id}** — {f.city} / {f.sub_district}")
        st.markdown(f"Serving **{f.serving_cell}** · az "
                    f"{f.serving_azimuth_deg}° · h {f.serving_height_m} m "
                    f"· location from *{f.loc_source}*")
        st.markdown(f"Distance **{f.distance_m and round(f.distance_m)} m**, "
                    f"bearing {f.bearing_deg}°, azimuth offset "
                    f"**{f.az_offset_deg}°**")
        st.markdown(f"Nearest other site: **{f.nearest_site}** "
                    f"({f.nearest_distance_m and round(f.nearest_distance_m)} m) "
                    f"· best sector **{f.best_sector_id}** "
                    f"({f.best_sector_offset_deg}° off)")
        st.info(f"**{f.category}** (conf {f.confidence:.0%}) — {f.finding}")
        if f.recommended_action:
            st.success(f"**{f.recommended_action}** — {f.action_detail}")
        if f.engineer_rf_analysis:
            st.caption(f"Engineer's own verdict: {f.engineer_rf_analysis} / "
                       f"{f.engineer_closure_code}  — audit: {f.agreement}")

with tab_audit:
    at = audit_table(res)
    if at.empty:
        st.info("No engineer verdict column in this file — nothing to "
                "audit against. (The slim complaint sample won't have one; the "
                "full 'CC Process' export does.)")
    else:
        st.caption("Rows = engineer's category, columns = tool's category. "
                   "Diagonal = agreement. Tickets the tool read from the "
                   "engineer's own label are excluded.")
        st.dataframe(at, use_container_width=True)
        st.caption(f"agree {s.get('agree', 0):.0%} · agree+partial "
                   f"{s.get('agree_or_partial', 0):.0%} over {s.get('audit_n', 0)} "
                   f"tickets. Geometry-only triage will not match a KPI/MR/"
                   f"drive-test-informed engineer on capacity or subtle "
                   f"coverage calls — it is a screen, not a replacement.")

with tab_kmz:
    st.write("Google-Earth map: a pin per ticket coloured by category, a line "
             "to its serving site, plus the serving-site pins.")
    if st.button("Build complaint KMZ", type="primary"):
        from rfopt.geo import build_complaint_kmz
        tmp = Path(tempfile.gettempdir()) / "R5_complaint_analysis.kmz"
        build_complaint_kmz(res, tmp, title="R5 Complaint Analysis")
        st.session_state["complaint_kmz"] = tmp.read_bytes()
    if st.session_state.get("complaint_kmz"):
        st.download_button("⬇ Download complaint KMZ",
                           st.session_state["complaint_kmz"],
                           "R5_complaint_analysis.kmz",
                           "application/vnd.google-earth.kmz",
                           use_container_width=True)
