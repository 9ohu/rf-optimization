"""Generate and download the Excel / Markdown optimisation reports."""

from __future__ import annotations

import io

import streamlit as st

from _shared import get_result, page_setup, require_result, settings

page_setup("Reports", "📤")
require_result()
res = get_result()
cfg = settings()

st.title("📤 Reports")

s = res.summary()
st.write(f"**{s['diagnoses']}** diagnoses · **{s['recommendations']}** actions · "
         f"**{s['kpi_breaches']}** breaches · **{s['degrading_trends']}** "
         f"degrading trends")

incl_ai = st.toggle("Include AI narratives (one per action — slower)",
                    value=False)

if st.button("Generate reports", type="primary"):
    narratives = None
    if incl_ai:
        from rfopt.ai import build_narrator
        narrator = build_narrator(use_ai=cfg["use_ai"],
                                  api_key=cfg["api_key"] or None)
        st.caption(f"Narrative engine: {narrator.source}")
        diag_by_cell = {}
        for d in res.diagnoses:
            diag_by_cell.setdefault(d.entity_id, []).append(d)
        narratives = []
        prog = st.progress(0.0)
        for i, r in enumerate(res.recommendations):
            d = next((x for x in diag_by_cell.get(r.cell_id, [])
                      if x.problem_class == r.category),
                     (diag_by_cell.get(r.cell_id) or [None])[0])
            if d is not None:
                narratives.append(narrator.narrate(d, r))
            prog.progress((i + 1) / max(len(res.recommendations), 1))
        prog.empty()

    from rfopt.reports import build_markdown_report
    from rfopt.reports.excel_report import write_excel_report
    import tempfile
    import pathlib

    md = build_markdown_report(res, narratives)
    st.session_state["rep_md"] = md.encode("utf-8")

    tmp = pathlib.Path(tempfile.gettempdir()) / "rf_optimization_report.xlsx"
    write_excel_report(res, tmp, narratives)
    st.session_state["rep_xlsx"] = tmp.read_bytes()
    st.success("Reports ready.")

c1, c2, c3 = st.columns(3)
if st.session_state.get("rep_xlsx"):
    c1.download_button("⬇  Excel workbook", st.session_state["rep_xlsx"],
                       "rf_optimization_report.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       use_container_width=True)
if st.session_state.get("rep_md"):
    c2.download_button("⬇  Markdown report", st.session_state["rep_md"],
                       "rf_optimization_report.md", "text/markdown",
                       use_container_width=True)
    c3.download_button("⬇  Action plan (CSV)",
                       res.recommendations_df().to_csv(index=False).encode(),
                       "action_plan.csv", "text/csv", use_container_width=True)

if st.session_state.get("rep_md"):
    with st.expander("Preview markdown report"):
        st.markdown(st.session_state["rep_md"].decode("utf-8"))
