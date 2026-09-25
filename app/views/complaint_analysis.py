"""Complaints · Delay Tickets Analysis — the Daily Target tickets, and each one in full.

Two views on one page. Overview, top to bottom: the summary, tickets by
engineer, sup district, governorate and ticket type (donuts whose legends filter
the table), the key KPIs, the top sites, and the ticket table with every column
of the uploaded file. Ticket Details: one ticket — clicked in the table, typed in
the search or picked there — with its record, the NOC analysis, its KPI evidence
charts over the whole period with the Correlation Window shaded, and its KPI
timeline (`_complaints`). A delay ticket is one whose SLA Status is
sla_violation — the only delay the Daily Target records.
"""

from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from _complaints import (DELAYED, DELAY_RULE, ENGINEERS, GOVERNORATES, INSUFFICIENT, NA, NAMES,
                         NOT_RESOLVED, NO_ISSUE, NO_PROBLEM, ON_TIME, OVERVIEW, POSSIBLE, PRIMARY,
                         RESOLVED, SECONDARY, SEV_COLOUR, TECHNICAL, TICKET_VIEW, TYPES,
                         TYPE_COLOUR, UNKNOWN, _esc, _fit,
                         _kpi_cards, _remember, _restore, _title_html, donut_html, open_ticket,
                         open_workspace, render_ticket, site_tickets, tile_counts)

CHART = ["#20BFFF", "#A78BFA", "#FB923C", "#22C55E", "#F472B6", "#FACC15", "#2DD4BF", "#94A3B8"]
ANALYSIS_COLS = ["Ticket ID", "MSISDN", "Site ID", "Site Name", "Site Tickets", "City",
                 "Sup District", "Governorate", "Engineer", "Problem Time", "Ticket Type",
                 "Delay", "KPI Issues", "KPI (window)", "RSRP", "Network Analysis", "Problem",
                 "Site Issue", "Resolution", "Confidence"]
DEFAULT_COLS = ["Ticket ID", "MSISDN", "Site ID", "Site Name", "City", "Sup District",
                "Governorate", "Engineer", "Problem Time", "Ticket Type", "Delay", "SLA Status",
                "KPI Issues", "Network Analysis", "Resolution"]

ctx = open_workspace("Delay Tickets Analysis", search_key="ca_q",
                     placeholder="Search Ticket ID, Complaint ID, MSISDN, Site ID or site name…")
T, n, active, win_label = ctx.T, ctx.n, ctx.active, ctx.win_label
ss = st.session_state


def _pct(k: int, of: int) -> float:
    return 100.0 * k / of if of else 0.0


# --------------------------------------------------------------------------- #
# the two views: every ticket, or one ticket in full
# --------------------------------------------------------------------------- #
IDS = T["Ticket ID"].tolist()
if ctx.q and ss.get("ca_last_q") != ctx.q:
    # a Ticket ID or a Complaint (HPSM) ID typed in the search opens that ticket, once
    exact = T[(T["Ticket ID"].str.upper() == ctx.q.upper())
              | (T["_complaint"].str.upper() == ctx.q.upper())]
    if len(exact):
        open_ticket(exact["Ticket ID"].iloc[0])
ss["ca_last_q"] = ctx.q
_restore("ca_mode", lambda v: v in (OVERVIEW, TICKET_VIEW))
if "ca_mode_next" in ss:
    ss["ca_mode"] = ss.pop("ca_mode_next")
if ss.get("ca_mode") not in (OVERVIEW, TICKET_VIEW):
    ss["ca_mode"] = OVERVIEW
VIEW_ICON = {OVERVIEW: ":material/dashboard:", TICKET_VIEW: ":material/receipt_long:"}
with st.container(key="ca_views", horizontal=True, vertical_alignment="center", gap="small"):
    mode = st.segmented_control("View", [OVERVIEW, TICKET_VIEW], key="ca_mode", required=True,
                                format_func=lambda v: f"{VIEW_ICON[v]} {v}",
                                label_visibility="collapsed")
    _remember("ca_mode", mode)
    tid_now = ss.get("ca_sel_tid")
    if mode == OVERVIEW and tid_now in set(IDS):
        st.caption(f"Last opened ticket: {tid_now}")


# --------------------------------------------------------------------------- #
# Ticket Details: one ticket in full, picked here or opened from the overview
# --------------------------------------------------------------------------- #
def _label(tid, complaint, msisdn, site, pt, kind, cls, delay) -> str:
    parts = [tid, complaint, msisdn if msisdn != NA else "", site if site != NA else "no site",
             f"{pt:%d %b %H:%M}" if pd.notna(pt) else "", kind, cls,
             DELAYED if delay == DELAYED else ""]
    return " · ".join(x for x in parts if x)


def _to_overview() -> None:
    ss["ca_mode"] = OVERVIEW


def _picked() -> None:
    ss["ca_sel_tid"] = ss.get("ca_pick")


def _step(order: list, by: int) -> None:
    cur = ss.get("ca_sel_tid")
    k = order.index(cur) + by if cur in order else 0
    if 0 <= k < len(order):
        ss["ca_sel_tid"] = order[k]


if mode == TICKET_VIEW:
    if not IDS:
        st.html('<div class="rf-note">The Daily Target has no tickets.</div>')
        st.stop()
    id_set = set(IDS)
    if ss.get("ca_sel_tid") not in id_set:
        ss["ca_sel_tid"] = IDS[0]
    tid = ss["ca_sel_tid"]
    # walk the tickets in the order the overview table showed them, filters applied
    shown = [t for t in (ss.get("ca_view_ids") or []) if t in id_set]
    order = shown if tid in shown else IDS
    labels = dict(zip(T["Ticket ID"], map(_label, T["Ticket ID"], T["_complaint"],
                                          T["MSISDN"], T["Site ID"],
                                          T["Problem Time"], T["Ticket Type"],
                                          T["Network Analysis"], T["Delay"])))
    ss["ca_pick"] = tid
    sel = T[T["Ticket ID"] == tid].iloc[0]
    pos = order.index(tid)
    with st.container(key="rf_card_ca_pick", border=True):
        b0, b1, b2, b3, b4 = st.columns([1.3, 4.6, 1.2, 1.1, 1.4], gap="small",
                                        vertical_alignment="center")
        b0.button("Overview", icon=":material/arrow_back:", key="ca_back", width="stretch",
                  on_click=_to_overview, help="Back to the overview")
        b1.selectbox("Ticket", order, key="ca_pick", format_func=lambda t: labels.get(t, t),
                     on_change=_picked, label_visibility="collapsed",
                     placeholder="Search a ticket…")
        b2.button("Previous", icon=":material/chevron_left:", key="ca_prev", width="stretch",
                  disabled=pos <= 0, on_click=_step, args=(order, -1))
        b3.button("Next", icon=":material/chevron_right:", key="ca_next", width="stretch",
                  disabled=pos >= len(order) - 1, on_click=_step, args=(order, 1))
        open_site = b4.button("Site tickets", icon=":material/cell_tower:", key="ca_site_btn_t",
                              width="stretch", disabled=sel["Site ID"] == NA,
                              help="Every ticket of this site")
        st.caption(f"Ticket {pos + 1:,} of {len(order):,} · "
                   + ("Previous / Next follow the overview table and its filters" if order is shown
                      else "Previous / Next follow the Daily Target"))
    render_ticket(ctx, sel)
    if open_site:
        site_tickets(ctx, sel["Site ID"])
    st.stop()


# --------------------------------------------------------------------------- #
# the filters: one value per filter, shown in Filters and by the donut legends
# --------------------------------------------------------------------------- #
eng_opts = ([e for e in ENGINEERS if e in set(T["Engineer"])]
            + sorted(set(T["Engineer"]) - set(ENGINEERS)))
sd_counts = T["Sup District"].value_counts()
gov_counts = T["Governorate"].value_counts()
gov_opts = [g for g in GOVERNORATES if g in gov_counts.index] + sorted(
    set(gov_counts.index) - set(GOVERNORATES))
tt_opts = [t for t in TYPES if t in set(T["_tt"])]
kpi_opts = sorted({k for ks in T["_kpis"] for k in ks})
MULTI = {
    "ca_f_eng_m": ("Engineering", eng_opts),
    "ca_f_sd_m": ("Sup District", sorted(sd_counts.index)),
    "ca_f_gov_m": ("Governorate", gov_opts),
    "ca_f_city": ("City", sorted(set(T["City"]))),
    "ca_f_site": ("Site", sorted(set(T["Site ID"]) - {NA})),
    "ca_f_tt_m": ("Ticket Type", tt_opts),
    "ca_f_sla": ("Status (SLA Status)", sorted(set(T["SLA"]))),
    "ca_f_delay": ("Delay", [DELAYED, ON_TIME] if T["Delay"].ne(NA).any() else []),
    "ca_f_kpi": ("KPI", kpi_opts),
    "ca_f_cls": ("Network analysis", [TECHNICAL, POSSIBLE, NO_ISSUE, INSUFFICIENT]),
    "ca_f_res": ("Resolution", [RESOLVED, NOT_RESOLVED, UNKNOWN, NO_PROBLEM]),
    "ca_f_type": ("Problem type", sorted(set(T["Problem Type"]))),
}
for _k, (_, _opts) in MULTI.items():
    _fit(_k, _opts)
for _k in ("ca_f_tid", "ca_f_msisdn"):
    _restore(_k, lambda v: isinstance(v, str))


def _from_pills(pkey: str, mkey: str, options: tuple) -> None:
    """A donut legend pill toggles the same value as the Filters list."""
    others = [v for v in ss.get(mkey, []) if v not in options]
    ss[mkey] = others + list(ss.get(pkey) or [])
    _remember(mkey, ss[mkey])


def _pills(label: str, pkey: str, mkey: str, options, fmt=None) -> None:
    ss[pkey] = [v for v in ss.get(mkey, []) if v in options]
    st.pills(label, list(options), selection_mode="multi", key=pkey,
             format_func=fmt or (lambda v: v), label_visibility="collapsed",
             on_change=_from_pills, args=(pkey, mkey, tuple(options)))


# --------------------------------------------------------------------------- #
# summary
# --------------------------------------------------------------------------- #
cls_n = T["Network Analysis"].value_counts()
detected = int(T["Problem Detected"].isin(["Yes", "Possible"]).sum())
resolved = int((T["Resolution"] == RESOLVED).sum())
delayed = int((T["Delay"] == DELAYED).sum())
_kpi_cards([
    dict(title="Total Tickets", value=f"{n:,}", icon="file", tone="blue", note=active.name),
    dict(title="Delay Tickets", value=f"{delayed:,}" if T["Delay"].ne(NA).any() else "No data",
         icon="clock", tone="red", pct=_pct(delayed, n) if T["Delay"].ne(NA).any() else None,
         note=DELAY_RULE),
    dict(title="Technical Issue", value=f"{int(cls_n.get(TECHNICAL, 0)):,}", icon="alert",
         tone="red", pct=_pct(int(cls_n.get(TECHNICAL, 0)), n),
         note=f"critical KPI breach · {win_label}"),
    dict(title="Possible Technical", value=f"{int(cls_n.get(POSSIBLE, 0)):,}", icon="info",
         tone="orange", pct=_pct(int(cls_n.get(POSSIBLE, 0)), n), note="warning level only"),
    dict(title="No Network Issue", value=f"{int(cls_n.get(NO_ISSUE, 0)):,}", icon="check",
         tone="blue", pct=_pct(int(cls_n.get(NO_ISSUE, 0)), n),
         note="manual customer verification"),
    dict(title="Insufficient Data", value=f"{int(cls_n.get(INSUFFICIENT, 0)):,}", icon="info",
         tone="gray", pct=_pct(int(cls_n.get(INSUFFICIENT, 0)), n),
         note="no site / no KPI in window"),
    dict(title="Site Issues Resolved", value=f"{resolved:,} / {detected:,}", icon="check",
         tone="green", pct=_pct(resolved, detected), note="KPI back within threshold"),
])

# --------------------------------------------------------------------------- #
# donuts: engineering, sup district, governorate, ticket type
# --------------------------------------------------------------------------- #
d1, d2, d3, d4 = st.columns(4, gap="small")
with d1, st.container(key="rf_card_ca_dn_eng", border=True):
    counts = T["Engineer"].value_counts()
    st.html(donut_html("Tickets by Engineering", "user",
                       [(e, int(counts.get(e, 0)), CHART[k % len(CHART)])
                        for k, e in enumerate(eng_opts)], n))
    _pills("Engineer", "ca_f_eng", "ca_f_eng_m", eng_opts)
with d2, st.container(key="rf_card_ca_dn_sd", border=True):
    known = sd_counts.drop(NA, errors="ignore")
    top5 = known.head(5)
    parts = [(s, int(v), CHART[k]) for k, (s, v) in enumerate(top5.items())]
    if len(known) > 5:
        parts.append((f"Other ({len(known) - 5} districts)", int(known.iloc[5:].sum()), CHART[6]))
    if NA in sd_counts.index:
        parts.append(("No site / not in tracker", int(sd_counts[NA]), CHART[7]))
    st.html(donut_html("Tickets by Sup District", "layers", parts, n, subtitle="top 5"))
    _pills("Sup District", "ca_f_sd", "ca_f_sd_m", list(top5.index))
with d3, st.container(key="rf_card_ca_dn_gov", border=True):
    st.html(donut_html("Tickets by Governorate", "pin",
                       [(g, int(gov_counts.get(g, 0)), CHART[k % len(CHART)])
                        for k, g in enumerate(gov_opts)], n))
    _pills("Governorate", "ca_f_gov", "ca_f_gov_m", gov_opts)
with d4, st.container(key="rf_card_ca_dn_tt", border=True):
    if ctx.has_reopen:
        tt_n = T["_tt"].value_counts()
        st.html(donut_html("Ticket Type", "reopen",
                           [(t, int(tt_n.get(t, 0)), TYPE_COLOUR[t]) for t in TYPES], n,
                           subtitle="Reopen + User fields"))
        _pills("Ticket type", "ca_f_tt", "ca_f_tt_m", tt_opts)
    else:
        st.html(_title_html("Ticket Type", "reopen")
                + '<div class="rf-note">Not available — the Daily Target has no Reopen column, '
                  "so NEW / REOPEN / UP OF SLEEP cannot be told apart.</div>")

# --------------------------------------------------------------------------- #
# key KPIs — how many tickets saw each KPI above its threshold
# --------------------------------------------------------------------------- #
if ctx.tracks or ctx.inds:
    tc = tile_counts(ctx.noc_rows)
    keys = [k for k in PRIMARY + SECONDARY if k in tc] + [k for k in tc
                                                         if k not in PRIMARY + SECONDARY]
    cells = ""
    for k in keys:
        d = tc[k]
        with_data = n - d["nodata"]
        sev = 2 if d["critical"] else 1 if d["warning"] else 0 if with_data else -1
        flag = "" if d["judged"] else "<i>context</i>"
        cells += (f'<div class="ca-tile" style="--c:{SEV_COLOUR[sev]}" '
                  f'title="{_esc(NAMES.get(k, k))}: {d["critical"]} critical, {d["warning"]} '
                  f'above warning, {d["normal"]} normal, {d["nodata"]} without data">'
                  f'<div class="ca-tile-k"><span>{_esc(k)}</span>{flag}</div>'
                  f'<div class="ca-tile-v">{d["critical"] + d["warning"]:,}<small> tickets</small></div>'
                  f'<div class="ca-tile-s">● {d["critical"]} · ⚠︎ {d["warning"]}</div>'
                  f'<div class="ca-tile-n">{with_data:,} with data · {_esc(NAMES.get(k, k))}</div></div>')
    with st.container(key="rf_card_ca_keys", border=True):
        st.html(_title_html("Key KPIs", "pulse",
                            subtitle=f"tickets with the KPI above threshold in their window · "
                                     f"{win_label}")
                + f'<div class="ca-strip">{cells}</div>')

# --------------------------------------------------------------------------- #
# top sites
# --------------------------------------------------------------------------- #
placed = T[T["Site ID"] != NA]
TOP = pd.DataFrame(columns=["Site ID", "Site Name", "City", "Sup District", "Tickets",
                            "Delay Tickets", "Technical", "% of Tickets"])
if len(placed):
    g = placed.groupby("Site ID")
    TOP = pd.DataFrame({
        "Site Name": g["Site Name"].first(),
        "City": g["City"].agg(lambda s: s.value_counts().index[0]),
        "Sup District": g["Sup District"].first(),
        "Tickets": g.size(),
        "Delay Tickets": g["Delay"].agg(lambda s: int((s == DELAYED).sum())),
        "Technical": g["Network Analysis"].agg(lambda s: int(s.isin([TECHNICAL, POSSIBLE]).sum())),
    })
    TOP["% of Tickets"] = (100.0 * TOP["Tickets"] / n).round(1)
    TOP = (TOP.sort_values(["Tickets", "Delay Tickets", "Technical"], ascending=False)
           .head(10).rename_axis("Site ID").reset_index())


def _pick_top_site() -> None:
    ev = ss.get("ca_top_sites")
    rows = list(ev["selection"]["rows"]) if ev else []
    ids = ss.get("ca_top_ids") or []
    ss["ca_f_site"] = [ids[rows[0]]] if rows and rows[0] < len(ids) else []
    _remember("ca_f_site", ss["ca_f_site"])


with st.container(key="rf_card_ca_top", border=True):
    st.html(_title_html("Top Sites", "tower",
                        subtitle="the sites with the most tickets · select one to filter the "
                                 "ticket table"))
    if TOP.empty:
        st.html('<div class="rf-note">No ticket names a site.</div>')
    else:
        ss["ca_top_ids"] = TOP["Site ID"].tolist()
        st.dataframe(TOP, key="ca_top_sites", on_select=_pick_top_site,
                     selection_mode="single-row", hide_index=True, width="stretch",
                     height=min(400, 38 + 35 * len(TOP)),
                     column_config={
                         "% of Tickets": st.column_config.ProgressColumn(
                             "% of Tickets", format="%.1f%%", min_value=0,
                             max_value=max(float(TOP["% of Tickets"].max()), 1.0)),
                         "Delay Tickets": st.column_config.NumberColumn(
                             "Delay Tickets", help=DELAY_RULE),
                         "Technical": st.column_config.NumberColumn(
                             "Technical", help="Technical or possible technical issue")})

# --------------------------------------------------------------------------- #
# the ticket table: every column of the file, and the analysis beside it
# --------------------------------------------------------------------------- #
src = ctx.source.reset_index(drop=True)
src = src.rename(columns={c: f"{c} (file)" for c in src.columns if c in ANALYSIS_COLS})
FULL = pd.concat([T[ANALYSIS_COLS].reset_index(drop=True), src], axis=1)
ALL_COLS = list(FULL.columns)
DEFAULT = [c for c in DEFAULT_COLS if c in ALL_COLS]
_fit("ca_cols", ALL_COLS)
if "ca_cols" not in ss:
    ss["ca_cols"] = DEFAULT


def _cols_all() -> None:
    ss["ca_cols"] = list(ALL_COLS)


def _cols_default() -> None:
    ss["ca_cols"] = list(DEFAULT)


def _on_select() -> None:
    ev_ = ss.get("ca_table")
    rows = list(ev_["selection"]["rows"]) if ev_ else []
    ids = ss.get("ca_view_ids") or []
    if rows and rows[0] < len(ids):
        open_ticket(ids[rows[0]])


open_site = ""
with st.container(key="rf_card_ca_table", border=True):
    head, tools = st.columns([2.6, 2.4], gap="small", vertical_alignment="center")
    with tools, st.container(horizontal=True, horizontal_alignment="right", gap="small"):
        with st.popover("Columns", icon=":material/view_column:"):
            st.multiselect("Visible columns", ALL_COLS, key="ca_cols",
                           placeholder="Pick the columns to show",
                           help="Every column of the uploaded file is here, with the analysis "
                                "columns. Hidden columns stay in the data and in the export.")
            b1, b2 = st.columns(2)
            b1.button("Show all", key="ca_cols_all", on_click=_cols_all, width="stretch")
            b2.button("Default", key="ca_cols_def", on_click=_cols_default, width="stretch")
        _remember("ca_cols", ss.get("ca_cols"))
        with st.popover("Filters", icon=":material/filter_list:"):
            st.text_input("Ticket ID", key="ca_f_tid", placeholder="contains…")
            st.text_input("MSISDN", key="ca_f_msisdn", placeholder="digits, with or without 964 / 0")
            _remember("ca_f_tid", ss.get("ca_f_tid", ""))
            _remember("ca_f_msisdn", ss.get("ca_f_msisdn", ""))
            picks = {}
            for fkey, (label, options) in MULTI.items():
                picks[fkey] = st.multiselect(label, options, key=fkey)
                _remember(fkey, picks[fkey])
            st.caption("Cell: the Daily Target has no cell column, so tickets cannot be "
                       "filtered by cell.")
            days = T["Problem Time"].dropna().dt.date
            date_rng = None
            if len(days):
                d_lo, d_hi = days.min(), days.max()
                _restore("ca_f_date", lambda v: isinstance(v, tuple) and len(v) == 2
                         and d_lo <= v[0] <= v[1] <= d_hi)
                date_rng = st.date_input("Problem date", value=(d_lo, d_hi), min_value=d_lo,
                                         max_value=d_hi, key="ca_f_date")
                if isinstance(date_rng, (tuple, list)) and len(date_rng) == 2:
                    _remember("ca_f_date", tuple(date_rng))

    m = pd.Series(True, index=T.index)
    for fkey, col in (("ca_f_eng_m", "Engineer"), ("ca_f_sd_m", "Sup District"),
                      ("ca_f_gov_m", "Governorate"), ("ca_f_city", "City"),
                      ("ca_f_site", "Site ID"), ("ca_f_tt_m", "_tt"), ("ca_f_sla", "SLA"),
                      ("ca_f_delay", "Delay"), ("ca_f_cls", "Network Analysis"),
                      ("ca_f_res", "Resolution"), ("ca_f_type", "Problem Type")):
        if ss.get(fkey):
            m &= T[col].isin(ss[fkey])
    if ss.get("ca_f_kpi"):
        want = set(ss["ca_f_kpi"])
        m &= T["_kpis"].map(lambda ks: bool(want & set(ks)))
    if (ss.get("ca_f_tid") or "").strip():
        m &= T["Ticket ID"].str.lower().str.contains(ss["ca_f_tid"].strip().lower(), regex=False)
    digits = "".join(ch for ch in (ss.get("ca_f_msisdn") or "") if ch.isdigit())
    if digits:
        m &= T["MSISDN"].str.replace(r"\D", "", regex=True).str.contains(digits.lstrip("0")
                                                                        or digits, regex=False)
    if isinstance(date_rng, (tuple, list)) and len(date_rng) == 2:
        pdays = T["Problem Time"].dt.date
        m &= pdays.isna() | ((pdays >= date_rng[0]) & (pdays <= date_rng[1]))
    q = ctx.q
    if q:
        ql = q.lower()
        m &= (T["Ticket ID"].str.lower().str.contains(ql, regex=False)
              | T["_complaint"].str.lower().str.contains(ql, regex=False)
              | T["MSISDN"].str.lower().str.contains(ql.lstrip("0"), regex=False)
              | T["Site ID"].str.lower().str.contains(ql, regex=False)
              | T["Site Name"].str.lower().str.contains(ql, regex=False))
    view = T[m]

    def _export_bytes(rows: pd.DataFrame) -> bytes:
        out = FULL.loc[rows.index].copy()
        out["Problem Time"] = out["Problem Time"].dt.strftime("%Y-%m-%d %H:%M")
        out["Evidence"] = [ctx.analysis[i].evidence for i in rows["_i"]]
        out["Resolution Evidence"] = [ctx.analysis[i].resolution_evidence for i in rows["_i"]]
        out["Correlation Window"] = [ctx.analysis[i].window for i in rows["_i"]]
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="xlsxwriter") as xw:
            out.to_excel(xw, sheet_name="Delay Tickets Analysis", index=False)
            ws = xw.sheets["Delay Tickets Analysis"]
            ws.freeze_panes(1, 1)
            ws.autofilter(0, 0, max(len(out), 1), len(out.columns) - 1)
            for j, c in enumerate(out.columns):
                width = out[c].astype(str).str.len().quantile(0.9) if len(out) else 12
                ws.set_column(j, j, int(min(60, max(12, (width or 12) + 2))))
        return buf.getvalue()

    with tools, st.container(horizontal=True, horizontal_alignment="right", gap="small"):
        if st.button("Site tickets", icon=":material/cell_tower:", key="ca_site_btn",
                     disabled=ctx.per_site.empty, help="Every ticket of one site"):
            sel_tid = ss.get("ca_sel_tid")
            picked = T.loc[T["Ticket ID"] == sel_tid, "Site ID"] if sel_tid else pd.Series(dtype=str)
            open_site = (picked.iloc[0] if len(picked) and picked.iloc[0] != NA
                         else str(ctx.per_site.index[0]))
        st.download_button("Export", _export_bytes(view),
                           f"Delay_Tickets_Analysis_{active.uploaded_at[:10]}.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           icon=":material/download:", key="ca_export")
    visible = [c for c in (ss.get("ca_cols") or []) if c in ALL_COLS] or ["Ticket ID"]
    with head:
        st.html(_title_html("Tickets", "file",
                            subtitle=f"{len(view):,} of {n:,} · {len(visible)} of "
                                     f"{len(ALL_COLS)} columns shown · window {win_label} · "
                                     "click a ticket to open Ticket Details"))
    ss["ca_view_ids"] = view["Ticket ID"].tolist()
    grid = FULL.loc[view.index].copy()
    grid["Ticket Type"] = [[v] for v in grid["Ticket Type"]]
    st.dataframe(
        grid, key="ca_table", on_select=_on_select, selection_mode="single-row",
        hide_index=True, width="stretch", height=520, column_order=visible,
        column_config={
            "Problem Time": st.column_config.DatetimeColumn("Problem Time",
                                                            format="D MMM, HH:mm"),
            "Site Tickets": st.column_config.NumberColumn(
                "Site Tickets", format="%d", width="small",
                help="Tickets from this site in the Daily Target"),
            "Ticket Type": st.column_config.MultiselectColumn(
                "Ticket Type", options=ctx.tt_options, color=ctx.tt_colours, width=120,
                help="NEW · REOPEN #n (Reopen count) · UP OF SLEEP (no Reopen, User set)"),
            "Delay": st.column_config.TextColumn("Delay", help=DELAY_RULE),
            "Site Issue": st.column_config.TextColumn("Site / Network Analysis", width="large"),
            "KPI (window)": st.column_config.TextColumn("KPI (window)", width="medium"),
        })

if open_site:
    site_tickets(ctx, open_site)
