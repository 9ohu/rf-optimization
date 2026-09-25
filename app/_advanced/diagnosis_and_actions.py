"""Diagnoses and the recommended-action engine output."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from _shared import (PRIO_EMOJI, SEV_EMOJI, get_result, page_setup,
                     require_result, settings)

page_setup("Diagnosis & Actions", "🧭")
require_result()
res = get_result()
cfg = settings()

st.title("🧭 Diagnosis & Recommended Actions")

diag_by_cell = {}
for d in res.diagnoses:
    diag_by_cell.setdefault(d.entity_id, []).append(d)


def _narrator():
    from rfopt.ai import build_narrator
    return build_narrator(use_ai=cfg["use_ai"], api_key=cfg["api_key"] or None)


tab_actions, tab_diag = st.tabs(["Action plan", "All diagnoses"])

with tab_actions:
    recs = res.recommendations
    if not recs:
        st.success("No actions generated.")
        st.stop()

    fc = st.columns(4)
    prio = fc[0].multiselect("Priority", ["P1", "P2", "P3", "P4"],
                             default=["P1", "P2", "P3", "P4"])
    cats = sorted({r.category for r in recs})
    cat = fc[1].multiselect("Category", cats, default=cats)
    txt = fc[2].text_input("Cell contains")
    ai = fc[3].toggle("AI narrative", value=cfg["use_ai"],
                      help="Generate an RF-engineer write-up for each action.")

    view = [r for r in recs if r.priority in prio and r.category in cat
            and (not txt or txt.lower() in r.cell_id.lower())]
    st.caption(f"{len(view)} of {len(recs)} actions")

    summ = pd.DataFrame([{
        "priority": r.priority, "cell": r.cell_id, "site": r.site_id,
        "problem": r.problem, "category": r.category,
        "confidence": round(r.confidence, 2),
        "action": r.action_type,
    } for r in view])
    st.dataframe(summ, use_container_width=True, height=240)

    csv_rows = pd.DataFrame([r.as_dict() for r in view])
    st.download_button("Download action plan (CSV)",
                       csv_rows.to_csv(index=False).encode(),
                       "action_plan.csv", "text/csv")

    st.divider()
    narrator = _narrator() if ai else None
    if ai:
        st.caption(f"Narrative engine: **{narrator.source}**")

    for r in view:
        with st.expander(f"{PRIO_EMOJI.get(r.priority,'')} [{r.priority}] "
                         f"{r.cell_id} — {r.problem}  · conf {r.confidence:.0%}"):
            col1, col2 = st.columns([3, 2])
            with col1:
                st.markdown(f"**Root cause** · {r.root_cause}")
                st.markdown("**Evidence**")
                for e in r.evidence:
                    st.markdown(f"- {e}")
                if r.math_notes:
                    st.markdown("**Calculation**")
                    st.code("\n".join(r.math_notes))
            with col2:
                st.markdown(f"**Action** · {r.action_type}")
                st.markdown(f"**Parameter** · {r.parameter}")
                st.markdown(f"**Current** · {r.current_value}")
                st.markdown(f"**Recommended** · {r.recommended_value}")
                st.markdown(f"**Expected impact** · {r.expected_impact}")
            st.markdown("**Risks / side effects**")
            for x in r.risks:
                st.markdown(f"- {x}")
            st.markdown("**Monitor after change** · " + ", ".join(r.monitor_kpis))
            if r.alternatives:
                st.markdown("**Alternatives**")
                for a in r.alternatives:
                    st.markdown(f"- {a}")

            if ai and narrator is not None:
                key = f"{r.cell_id}|{r.category}"
                store = st.session_state.setdefault("narratives", {})
                if key not in store:
                    d = next((x for x in diag_by_cell.get(r.cell_id, [])
                              if x.problem_class == r.category),
                             (diag_by_cell.get(r.cell_id) or [None])[0])
                    if d is not None:
                        with st.spinner("Writing narrative…"):
                            store[key] = narrator.narrate(d, r)
                if store.get(key) is not None:
                    st.divider()
                    st.markdown(store[key].as_markdown())

with tab_diag:
    dd = res.diagnoses_df()
    if dd.empty:
        st.success("No diagnoses.")
    else:
        fc = st.columns(3)
        sev = fc[0].multiselect("Severity", ["critical", "warning"],
                                default=["critical", "warning"])
        pcs = sorted(dd["problem_class"].unique())
        pc = fc[1].multiselect("Problem class", pcs, default=pcs)
        tx = fc[2].text_input("Cell / site contains", key="dtx")
        v = dd[dd["severity"].isin(sev) & dd["problem_class"].isin(pc)]
        if tx:
            v = v[v["entity"].str.contains(tx, case=False) |
                  v["site"].str.contains(tx, case=False)]
        st.caption(f"{len(v)} diagnoses")
        for _, row in v.iterrows():
            st.markdown(f"{SEV_EMOJI.get(row['severity'],'')} "
                        f"**{row['title']}** — {row['entity']} "
                        f"(conf {row['confidence']:.0%})"
                        + (f"  ·  _trend: {row['trend']}_" if row["trend"] else ""))
            for e in str(row["evidence"]).split(" | "):
                if e:
                    st.markdown(f"&nbsp;&nbsp;• {e}")
        st.download_button("Download diagnoses (CSV)",
                           v.to_csv(index=False).encode(),
                           "diagnoses.csv", "text/csv")
