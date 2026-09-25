"""KPI Analysis · Draw Data — the selected KPIs as trend charts.

Two modes: Single Draw, the page as it has always been, and Bulk Draw
(`_kpi_bulk`), a list of Site ID + Sector drawn a chart at a time. Bulk Draw
adds nothing to the drawing itself — it uses these panels, this chart and this
workbook.

The drawing half of KPI Analysis: tick KPIs in the sidebar, choose the object
in the bar, Draw Selected Charts. One chart per KPI, each with a robust trend
line, the verdict (increasing, decreasing or holding) and the percentage behind
it, and the Trend Summary table with its export. The exports, the object and
the KPI picks are the ones the Overview page uses (`_kpi_workspace`).

Clear Charts removes the drawing only; Copy Chart puts one chart on the
clipboard as a picture (its title included) for email, WhatsApp or PowerPoint,
and saves it as a PNG where the browser will not write images to the clipboard.
"""

from __future__ import annotations

import html
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from rfopt.kpi.trends import STABLE_PCT, summary_table, trends_for
from _charts import COPY_JS, kpi_figure
import _kpi_bulk as B
import _kpi_view as V
import _kpi_workspace as W
from _ui import header as _header, kpi_cards as _kpi_cards

_header("KPI Analysis", "Draw Data · selected KPI charts and their trend data")
st.markdown(W.CHART_CSS, unsafe_allow_html=True)
st.html(V.CSS)

# --------------------------------------------------------------------------- #
# the two modes: Single Draw is this page as it was, Bulk Draw is the list
# --------------------------------------------------------------------------- #
MODE_ICON = {B.SINGLE: ":material/show_chart:", B.BULK: ":material/library_add:"}
W.restore("kd_mode", lambda v: v in (B.SINGLE, B.BULK))
if st.session_state.get("kd_mode") not in (B.SINGLE, B.BULK):
    st.session_state["kd_mode"] = B.SINGLE
with st.container(key="kd_modes"):
    mode = st.segmented_control("Draw mode", [B.SINGLE, B.BULK], key="kd_mode", required=True,
                                format_func=lambda v: f"{MODE_ICON[v]} {v}",
                                label_visibility="collapsed")
W.remember("kd_mode", mode)

if mode == B.BULK:
    B.render()
    st.stop()

ws = W.open_workspace("Draw Data", "Selected KPI charts, their trends and the data behind them",
                      page="draw")

with ws.draw_col:
    draw = st.button("Draw Selected Charts", icon=":material/insights:",
                     type="primary", width="stretch", disabled=not ws.picked)
W.restore("kpi_nmax", lambda v: isinstance(v, (int, float)) and 1 <= v <= 120)
with ws.max_col:
    n_max = st.number_input("Max charts", 1, 120, 20, 1, key="kpi_nmax",
                            label_visibility="collapsed")
W.remember("kpi_nmax", n_max)
if draw:
    st.session_state["kpi_drawn"] = (tuple(sorted(ws.picked)), ws.level, ws.obj,
                                     tuple(ws.cells))


def _clear_drawing() -> None:
    """Back to the page before Draw: the charts and their export go, the loaded
    exports, the KPI picks and the other pages stay as they are."""
    for k in ("kpi_drawn", "kpi_trend_xlsx"):
        st.session_state.pop(k, None)


trend = W.trend_state(ws)
if "panels" not in trend:
    if "warn" in trend:
        st.warning(trend["warn"])
    else:
        st.html('<div class="rf-card">' + V.empty(trend["note"], "chart") + "</div>")
    st.stop()

panels, data, who = trend["panels"], trend["data"], trend["who"]
c_info, c_clear = st.columns([5, 1], gap="small", vertical_alignment="center")
with c_info:
    st.html(f"<div class='ka-win'><b>{len(panels)}</b> charts for <b>{V.esc(who)}</b> · "
            f"{data['datetime'].min():%Y-%m-%d %H:%M} → "
            f"{data['datetime'].max():%Y-%m-%d %H:%M}</div>")
c_clear.button("Clear Charts", icon=":material/delete_sweep:", key="kpi_clear",
               on_click=_clear_drawing, width="stretch",
               help="Remove the drawn charts. The loaded KPI data and the picks stay.")
st.html(COPY_JS, unsafe_allow_javascript=True)

# how the drawn KPIs moved over the window, at a glance
_verdicts = [p.overall.verdict for p in panels if p.overall is not None]
_nv = max(len(_verdicts), 1)
_kpi_cards([
    dict(title="Charts", value=len(panels), icon="chart", tone="blue",
         note=who),
    dict(title="Increasing", value=_verdicts.count("Increasing"), icon="up",
         tone="blue", pct=100 * _verdicts.count("Increasing") / _nv,
         note=f"trend above +{STABLE_PCT:g}% over the window"),
    dict(title="Decreasing", value=_verdicts.count("Decreasing"), icon="down",
         tone="blue", pct=100 * _verdicts.count("Decreasing") / _nv,
         note=f"trend below -{STABLE_PCT:g}% over the window"),
    dict(title="Stable", value=_verdicts.count("Stable"), icon="flat",
         tone="gray", pct=100 * _verdicts.count("Stable") / _nv,
         note=f"within ±{STABLE_PCT:g}%"),
])

# a KPI with nothing behind it gets no chart — say which, or it looks lost
_empty = [k for k in trend["kpis"] if k not in {p.kpi for p in panels}]
if _empty:
    st.caption(f"Not measured on {who}: {', '.join(_empty)}")

tab_charts, tab_summary = st.tabs(["Charts", "Trend Summary"])

with tab_charts:
    shown = panels[:int(n_max)]
    for row in range(0, len(shown), 2):          # two charts to a row
        for j, (col, panel) in enumerate(zip(st.columns(2), shown[row:row + 2])):
            t = panel.overall
            card = f"rf_card_chart_{row + j}"
            file_name = "".join(c if c.isalnum() or c in "-_" else "_"
                                for c in f"{panel.kpi}_{who}")[:80]
            with col, st.container(border=True, key=card):
                st.html(
                    f"<div class='sm-chart-head'><span class='t'>{html.escape(panel.kpi)}</span>"
                    f"<span class='sm-verdict'>{t.arrow} {t.signed_pct}</span>"
                    f"<button class='rf-copy' data-card='{card}' "
                    f"data-file='{html.escape(file_name)}' title='Copy this chart as a picture "
                    f"— paste it into email, WhatsApp, PowerPoint or a report'>"
                    f"<span>Copy Chart</span></button></div>")
                st.plotly_chart(kpi_figure(panel, legend_title=f"{ws.unit} Name"),
                                width="stretch",
                                config={"displaylogo": False,
                                        "toImageButtonOptions": {"format": "png", "scale": 2,
                                                                 "filename": file_name}})

with tab_summary:
    tbl = summary_table([p.overall for p in panels] if trend["cells"] else
                        trends_for(data, list(trend["kpis"]), level=trend["level"],
                                   obj=trend["obj"]))
    st.dataframe(tbl, hide_index=True, width="stretch", height=420)

    if st.button("⬇ Export charts data (.xlsx)"):
        wide = pd.DataFrame({f"{p.kpi} · {name}": s
                             for p in panels for name, s in p.lines.items()})
        wide.index.name = "datetime"
        safe = "".join(c for c in who if c.isalnum() or c in "-_") or "R5"
        out = Path(tempfile.gettempdir()) / f"KPI_Trends_{safe}.xlsx"
        with pd.ExcelWriter(out, engine="xlsxwriter") as xw:
            tbl.to_excel(xw, sheet_name="Trend Summary", index=False)
            wide.reset_index().to_excel(xw, sheet_name="Hourly Data", index=False)
        st.session_state["kpi_trend_xlsx"] = (out.name, out.read_bytes())
    if st.session_state.get("kpi_trend_xlsx"):
        nm, blob = st.session_state["kpi_trend_xlsx"]
        st.download_button(f"⬇ {nm}", blob, nm,
                           "application/vnd.openxmlformats-officedocument."
                           "spreadsheetml.sheet")
