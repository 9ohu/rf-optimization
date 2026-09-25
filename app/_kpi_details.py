"""KPI Details by Site / Cell — the last section of the KPI Analysis Overview.

The site picked in the KPI issues table or on the map: the object and KPI the
row was about, the site's primary and secondary KPIs, its checks above and
within threshold under the page's filters, the RSRP around it, its hour-by-hour
timeline and its Daily Target tickets. Presentation only: every value comes from
`_kpi_health`, as everywhere else on the page.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from rfopt.complaints.noc import fmt
from _kpi_health import issue_order, site_hours, site_status, site_tiles, value_text
from _kpi_region import UNKNOWN
import _kpi_view as V
import _kpi_workspace as W
from _ui import PALETTE, icon_img as _icon, title_html as _title_html

NA = W.NA
TITLE = "KPI Details by Site / Cell"
KEYS = ("ka_sel", "ka_sel_obj", "ka_sel_kpi")


def close() -> None:
    for k in KEYS:
        st.session_state.pop(k, None)


def site_info(site_id: str, regions: pd.DataFrame, sites_info: pd.DataFrame) -> dict:
    out = {"name": NA, "location": NA, "lat": None, "lon": None}
    if site_id in regions.index:
        r = regions.loc[site_id]
        out["location"] = " / ".join(x for x in (r.get("governorate", ""), r["city"],
                                                 r["sup_district"])
                                     if x and x != UNKNOWN) or NA
    if sites_info.empty or site_id not in sites_info.index:
        return out
    r = sites_info.loc[site_id]
    out["name"] = W.clean(r.get("site_name")) or NA
    lat, lon = r.get("latitude"), r.get("longitude")
    if lat is not None and lon is not None and pd.notna(lat) and pd.notna(lon):
        out["lat"], out["lon"] = float(lat), float(lon)
    return out


def _site_rsrp(site_id: str, info: dict):
    """Measured RSRP around the site from the Current Coverage Data."""
    import _resources as R
    cov = R.coverage()
    if not cov:
        return None, None, "No coverage grid in Coverage Data (Data Resources)"
    if info["lat"] is None:
        return None, None, "No coordinates for the site in the EP tracker"
    from rfopt.complaints.correlate import site_area_rsrp
    from rfopt.geo.coverage import band_index, load_bands
    bands, covered = load_bands()
    area = site_area_rsrp(list(cov.values()), {site_id: (info["lat"], info["lon"])},
                          500.0, covered).get(site_id)
    if not area:
        return None, None, "No coverage grids near the site"
    return area, bands[int(band_index(np.array([area["median"]]), bands)[0])], ""


def _picked(rows_site: pd.DataFrame, obj: str | None, kpi: str | None) -> str:
    """The object × KPI the table row was about."""
    if not obj or rows_site is None or rows_site.empty:
        return ""
    hit = rows_site[(rows_site["object_id"] == obj)
                    & ((rows_site["label"] == kpi) if kpi else True)]
    if hit.empty:
        return ""
    r = hit.iloc[0]
    cells = W.clean(r.get("cell_name"))
    head = V.sec("Selected in the table", "target",
                 f"{obj}" + (f" · {cells}" if cells else ""))
    if int(r["sev"]) > 0 and bool(r["judged"]):
        return head + V.evidence(r)
    return head + (f'<div class="ka-box" style="--c:{V.STATE_COLOUR.get(r["state"], PALETTE["nodata"])}">'
                   f'<div class="ka-box-h"><span>{V.esc(r["label"])}</span>'
                   f'<span class="ka-st" style="--c:{V.STATE_COLOUR.get(r["state"], PALETTE["nodata"])}">'
                   f'{V.esc(r["state"])}</span></div>'
                   + V.kv([("Value (window)", value_text(r["value"], r["unit"], r["judged"])),
                           ("Threshold", r["threshold"]), ("Object", r["object_id"])])
                   + "</div>")


def render(sel: str | None, H, hframes, rows_site: pd.DataFrame | None, regions: pd.DataFrame,
           sites_info: pd.DataFrame, obj: str | None = None, kpi: str | None = None) -> None:
    """`rows_site`: the site's rows under the page's KPI, technology and cell
    filters (every status), from `_kpi_filters`."""
    with st.container(key="rf_card_ka_drawer", border=True):
        if not sel:
            st.html(_title_html(TITLE, "layers", subtitle="select a row in the KPI Issues "
                                                          "Table or a site on the map"))
            st.html(V.empty("No site selected: pick a row in the KPI Issues Table, or an "
                            "affected site on the map.", "target"))
            return
        info = site_info(sel, regions, sites_info)
        mine = H.objects[H.objects["site_id"] == sel]
        n_obj = int(mine["object_id"].nunique())
        status = site_status(mine, [sel]).loc[sel]
        sev, state = int(status["sev"]), status["state"]
        primary, secondary = site_tiles(H.objects, sel)
        hours = site_hours(hframes, sel)
        area, band, rsrp_note = _site_rsrp(sel, info)
        last_sev = (int(hours.loc[hours["hour"] == hours["hour"].max(), "sev"].max())
                    if len(hours) else -1)

        st.html(_title_html(TITLE, "layers", subtitle=f"{sel} · {info['name']}"))
        kinds = " · ".join(H.kinds.get(sel, []))
        st.html(
            '<div class="ka-dr"><div class="ka-dr-top">'
            f'<span class="ka-ico">{_icon("tower", PALETTE["cyan"], 22)}</span>'
            f'<div><div class="ka-dr-id">{V.esc(sel)} · {V.esc(info["name"])}</div>'
            f'<div class="ka-dr-sub">{V.esc(kinds)} · {n_obj} '
            f'object{"" if n_obj == 1 else "s"}</div></div>'
            f'{V.badge(state, V.SEV_COLOUR[sev])}</div>'
            '<div class="ka-chipsr">'
            f'<span class="ka-chipr">{_icon("pin", PALETTE["cyan"], 13)}{V.esc(info["location"])}</span>'
            f'<span class="ka-chipr">{_icon("clock", PALETTE["cyan"], 13)}'
            f'{H.start:%d %b %H:%M} → {H.end:%d %b %H:%M}</span></div></div>')

        t_over, t_kpis, t_rsrp, t_time, t_tix = st.tabs(
            ["Overview", "KPIs", "RSRP", "Timeline", "Tickets"])

        with t_over:
            rows = [("Status", V.state_html(sev, state)),
                    ("Primary KPI", V.esc(status["label"] or "—")),
                    ("Checks above threshold", f"{int(status['issues'])}"),
                    ("Latest hour", V.state_html(last_sev, V.PHASE[last_sev])),
                    ("RSRP (site area)",
                     f'<span class="ka-st" style="--c:{band.colour}">{V.esc(band.label)}</span>'
                     if band is not None else NA)]
            st.html('<div class="ka-dr">' + _picked(rows_site, obj, kpi)
                    + V.sec("Primary KPIs", "chart", "worst object · window")
                    + '<div class="ka-tiles">' + "".join(V.tile(t) for t in primary) + "</div>"
                    + (V.sec("Secondary KPIs", "layers") + '<div class="ka-s2w">'
                       + "".join(V.secondary(t) for t in secondary) + "</div>"
                       if secondary else "")
                    + V.sec("Final status", "target")
                    + '<div class="ka-box"><div class="ka-fin">'
                    + "".join(f"<span>{V.esc(k)}</span><b>{v}</b>" for k, v in rows)
                    + "</div></div></div>")

        with t_kpis:
            ordered = issue_order(rows_site if rows_site is not None else mine)
            bad = ordered[ordered["judged"].astype(bool) & (ordered["sev"] > 0)]
            good = ordered.drop(bad.index)
            st.html('<div class="ka-dr">'
                    + V.sec("Above threshold", "alert", f"{len(bad)} · under the page's filters")
                    + ("".join(V.evidence(r) for _, r in bad.head(12).iterrows())
                       or V.empty("Every judged KPI is within its threshold.", "check"))
                    + V.sec("Within threshold", "check", f"{len(good)}")
                    + '<div class="ka-lat">' + "".join(
                        f'<span title="{V.esc(r["object_id"])}">{V.esc(r["label"])} · '
                        f'{V.esc(r["object_id"])}</span>'
                        f'<b>{V.esc(value_text(r["value"], r["unit"], r["judged"]))}</b>'
                        f'<span class="ka-st" style="--c:{V.STATE_COLOUR.get(r["state"], PALETTE["nodata"])}">'
                        f'{V.esc(r["state"])}</span>'
                        for _, r in good.head(40).iterrows()) + "</div></div>")

        with t_rsrp:
            if area:
                glyph = ("✓" if band.key in ("excellent", "good")
                         else "●" if band.key == "very_poor" else "⚠︎")
                body = (f'<div class="ka-box" style="--c:{band.colour}"><div class="ka-rs">'
                        f'<div><div class="ka-rs-v">{area["median"]:.1f} dBm</div>'
                        f'<div class="ka-st" style="--c:{band.colour}">{glyph} {V.esc(band.label.upper())}</div></div>'
                        + V.kv([("Measurement", f"site area ≤{area['radius_m']:.0f} m · MR-weighted median"),
                                ("Weak MRs", f"{area['weak_pct']:.1f}% below {area['weak_dbm']:g} dBm"),
                                ("Grids", f"{area['grids']:,}")]) + "</div></div>")
            else:
                body = (f'<div class="ka-box" style="--c:{PALETTE["nodata"]}"><div class="ka-rs">'
                        '<div><div class="ka-rs-v">—</div>'
                        f'<div class="ka-st" style="--c:{PALETTE["nodata"]}">— NO DATA</div></div>'
                        + V.kv([("Site area RSRP", rsrp_note)]) + "</div></div>")
            st.html('<div class="ka-dr">' + V.sec("RSRP", "signal") + body
                    + V.kv([("Customer location", "Not available on this page")]) + "</div>")

        with t_time:
            if hours.empty:
                st.html(V.empty("No judged KPI hours for this site.", "clock"))
            else:
                # one KPI, named: the one the table row was about, else the site's
                # worst — never several folded into one bar
                labels = list(dict.fromkeys(hours.sort_values(
                    "sev", ascending=False, kind="stable")["label"]))
                first = kpi if kpi in labels else (status["label"] if status["label"] in labels
                                                   else labels[0])
                pick = st.selectbox("Timeline KPI", labels, index=labels.index(first),
                                    key=f"ka_tl_kpi_{sel}",
                                    help="The KPI the timeline shows")
                hk = hours[hours["label"] == pick]
                latest = hours.sort_values("hour").groupby("label").tail(1)
                st.html('<div class="ka-dr">'
                        + V.sec("KPI timeline", "clock", "one KPI, hour by hour · its worst "
                                                          "object")
                        + V.kpi_timeline(hk, pick, hk["threshold"].iloc[0], H.start, H.end)
                        + V.sec("Latest hour", "pulse", "per KPI, worst object")
                        + '<div class="ka-lat">' + "".join(
                            f'<span>{V.esc(r["label"])} · {r["hour"]:%d %b %H:%M}</span>'
                            f'<b>{V.esc(fmt(float(r["value"]), r["unit"]))}</b>'
                            f'{V.state_html(int(r["sev"]), V.PHASE[int(r["sev"])])}'
                            for _, r in latest.iterrows()) + "</div></div>")

        with t_tix:
            _tickets(sel)

        c_map, c_close = st.columns(2, gap="small")
        if c_map.button("View on Map", icon=":material/map:", key="ka_map_site",
                        width="stretch"):
            st.session_state["sm_q"] = sel
            st.switch_page("views/site_map.py")
        c_close.button("Close", icon=":material/close:", key="ka_close", width="stretch",
                       on_click=close)


def _tickets(sel: str) -> None:
    from rfopt.complaints.target_store import load_active
    from rfopt.complaints.ticket_type import TYPES, ticket_type, type_label
    ds = load_active()
    if ds is None:
        st.html(V.empty("No Daily Target in Complaint Data (Data Resources).", "ticket"))
        return
    tk = W.tickets(ds.sha1, ds)
    site_rows = tk[tk["site_id"].astype(str).str.upper() == sel]
    if site_rows.empty:
        st.html(V.empty(f"No ticket for {sel} in {ds.name}.", "ticket"))
        return
    has_type = "reopen" in site_rows.columns
    user = site_rows["user"] if "user" in site_rows.columns else [None] * len(site_rows)
    kinds_t = ([ticket_type(r, u) for r, u in zip(site_rows["reopen"], user)]
               if has_type else [])
    counts = pd.Series([k for k, _ in kinds_t]).value_counts()
    st.html('<div class="ka-dr">'
            + V.sec("Daily Target tickets", "ticket", ds.name)
            + V.chips([("Tickets", len(site_rows), PALETTE["cyan"], ds.name)]
                      + [(t, int(counts.get(t, 0)), c, t) for t, c in
                         zip(TYPES, (PALETTE["cyan"], "#F59E0B", "#A78BFA"))
                         if has_type])
            + '<div class="ka-lat">' + "".join(
                f'<span>{V.esc(r.ticket_id)}</span>'
                f'<b>{"" if pd.isna(r.problem_local) else f"{r.problem_local:%d %b %H:%M}"}</b>'
                f'<span class="ka-st" style="--c:{PALETTE["cyan"]}">'
                f'{V.esc(type_label(*kinds_t[n]) if has_type else "")}</span>'
                for n, r in enumerate(site_rows.head(12).itertuples()))
            + "</div></div>")
    if st.button("Open in Delay Tickets Analysis", icon=":material/report_problem:",
                 key="ka_open_ca", width="stretch"):
        # the ticket table, filtered to this site
        st.session_state["ca_f_site"] = [sel]
        st.session_state.setdefault("ca_keep", {})["ca_f_site"] = [sel]
        st.session_state.pop("ca_sel_tid", None)
        st.session_state["ca_mode_next"] = "Overview"
        st.switch_page("views/complaint_analysis.py")
