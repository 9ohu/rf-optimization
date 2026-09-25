"""The KPI Analysis page's NOC look: its styles, and the HTML its panels use.

Presentation only — every value comes in from the page, which takes it from
`_kpi_health` and the trend functions.
"""

from __future__ import annotations

import html

import pandas as pd

from _ui import PALETTE, icon_img

SEV_COLOUR = {2: PALETTE["critical"], 1: PALETTE["warning"], 0: PALETTE["excellent"],
              -1: PALETTE["nodata"]}
STATE_COLOUR = {"Critical": PALETTE["critical"], "Warning": PALETTE["warning"],
                "Normal": PALETTE["excellent"], "No data": PALETTE["nodata"],
                "Detected": "#A78BFA"}
GLYPH = {2: "●", 1: "⚠︎", 0: "✓", -1: "—"}
PHASE = {2: "Critical", 1: "Warning", 0: "Normal", -1: "No data"}

CSS = """
<style>
/* ---- workspace bar ---- */
.st-key-rf_card_ka_bar { background: linear-gradient(180deg, #0D2945 0%, #0B1F33 100%) !important; }
.ka-title { display: flex; align-items: center; gap: 11px; min-width: 0; }
.ka-ico { flex: 0 0 38px; height: 38px; border-radius: 10px; display: flex; align-items: center;
    justify-content: center; background: rgba(21, 151, 255, .14); border: 1px solid rgba(32, 191, 255, .35);
    box-shadow: 0 0 14px rgba(32, 191, 255, .18); }
.ka-title b { display: block; font-size: 17px; color: #F1F5F9; line-height: 1.15; white-space: nowrap; }
.ka-title span { display: block; font-size: 12px; color: #94A3B8; white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }
.ka-win { font-size: 11.5px; color: #94A3B8; padding: 6px 2px; white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }
.ka-win b { color: #E2E8F0; font-weight: 600; }
/* ---- trend card ---- */
.ka-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.ka-chip { display: inline-flex; align-items: center; gap: 6px; background: #071525; border: 1px solid #1E3A5F;
    border-left: 3px solid var(--c, #20BFFF); border-radius: 8px; padding: 3px 9px; font-size: 11.5px; color: #CBD5E1; }
.ka-chip b { color: #F8FAFC; font-variant-numeric: tabular-nums; }
.ka-note { font-size: 12px; color: #94A3B8; line-height: 1.5; }
.ka-empty { display: flex; align-items: center; gap: 10px; padding: 18px 6px; color: #94A3B8; font-size: 12.5px; }
/* ---- distribution ---- */
.ka-dist { display: flex; align-items: center; gap: 14px; padding: 6px 0; flex-wrap: wrap; }
.ka-donut { flex: 0 0 112px; height: 112px; border-radius: 50%; position: relative;
    background: conic-gradient(var(--g)); box-shadow: 0 0 22px rgba(32, 191, 255, .14); }
.ka-donut::after { content: ""; position: absolute; inset: 16px; border-radius: 50%; background: #0B1F33; }
.ka-donut-c { position: absolute; inset: 0; z-index: 1; display: flex; flex-direction: column;
    align-items: center; justify-content: center; }
.ka-donut-c b { font-size: 25px; color: #F8FAFC; line-height: 1; font-variant-numeric: tabular-nums; }
.ka-donut-c span { font-size: 10px; color: #94A3B8; margin-top: 4px; letter-spacing: .05em; }
.ka-leg { flex: 1 1 120px; display: flex; flex-direction: column; gap: 9px; min-width: 0; }
.ka-leg div { display: grid; grid-template-columns: 10px 1fr; gap: 0 8px; align-items: center;
    font-size: 12.5px; color: #E2E8F0; }
.ka-leg i { width: 9px; height: 9px; border-radius: 50%; background: var(--c); box-shadow: 0 0 6px var(--c); }
.ka-leg small { grid-column: 2; color: #94A3B8; font-size: 11.5px; font-variant-numeric: tabular-nums; }
/* ---- ranking ---- */
.ka-rank { display: flex; flex-direction: column; gap: 11px; padding-top: 2px; }
.ka-rank > div { display: grid; grid-template-columns: minmax(96px, 1.25fr) 1.2fr 34px 46px; gap: 8px;
    align-items: center; font-size: 12.5px; color: #E2E8F0; }
.ka-rank span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ka-bar { height: 7px; border-radius: 4px; background: #16324F; overflow: hidden; display: flex; }
.ka-bar i { display: block; height: 100%; flex: 0 0 auto; }
.ka-rank b { text-align: right; font-variant-numeric: tabular-nums; }
.ka-rank small { text-align: right; color: #94A3B8; font-size: 11.5px; font-variant-numeric: tabular-nums; }
/* ---- network details drawer ---- */
.st-key-rf_card_ka_drawer { background: #0B1F33 !important; }
.ka-dr { display: flex; flex-direction: column; gap: 9px; }
.ka-dr-top { display: flex; align-items: flex-start; gap: 10px; }
.ka-dr-id { font-size: 15px; font-weight: 800; color: #F8FAFC; line-height: 1.2; word-break: break-word; }
.ka-dr-sub { font-size: 11.5px; color: #94A3B8; margin-top: 2px; }
.ka-dr-top .ka-badge { margin-left: auto; }
.ka-badge { display: inline-flex; align-items: center; gap: 6px; padding: 2px 9px; border-radius: 999px;
    font-size: 11.5px; font-weight: 700; color: var(--c); white-space: nowrap;
    background: color-mix(in srgb, var(--c) 16%, transparent);
    border: 1px solid color-mix(in srgb, var(--c) 45%, transparent); }
.ka-badge i { width: 7px; height: 7px; border-radius: 50%; background: var(--c); }
.ka-chipsr { display: flex; flex-wrap: wrap; gap: 5px; }
.ka-chipr { display: inline-flex; align-items: center; gap: 5px; background: #071525; border: 1px solid #1E3A5F;
    border-radius: 8px; padding: 3px 8px; font-size: 11.5px; color: #CBD5E1; max-width: 100%;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ka-sec { display: flex; align-items: center; gap: 6px; margin: 6px 0 -3px; font-size: 10.5px;
    letter-spacing: .08em; text-transform: uppercase; color: #20BFFF; font-weight: 700; }
.ka-sec small { margin-left: auto; color: #64748B; letter-spacing: 0; text-transform: none; font-weight: 500; }
.ka-st { display: inline-flex; align-items: center; gap: 4px; font-weight: 700; color: var(--c); white-space: nowrap; }
.ka-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(74px, 1fr)); gap: 6px; }
.ka-tile { background: #071525; border: 1px solid #1E3A5F; border-top: 3px solid var(--c); border-radius: 9px;
    padding: 6px 7px 7px; min-width: 0; }
.ka-tile-k { display: flex; justify-content: space-between; align-items: center; gap: 4px; font-size: 11px;
    font-weight: 800; color: #CBD5E1; letter-spacing: .06em; }
.ka-tile-k i { font-style: normal; font-size: 9px; font-weight: 600; color: #64748B; letter-spacing: 0; }
.ka-tile-v { font-size: 16px; font-weight: 700; color: #F8FAFC; margin-top: 2px; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; font-variant-numeric: tabular-nums; }
.ka-tile-v small { font-size: 11px; color: #94A3B8; font-weight: 500; }
.ka-tile-s { font-size: 11px; font-weight: 800; color: var(--c); white-space: nowrap; letter-spacing: .03em; }
.ka-tile-n { font-size: 10px; color: #94A3B8; margin-top: 1px; white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }
.ka-s2w { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 5px; }
.ka-s2 { display: flex; align-items: center; gap: 6px; background: #071525; border: 1px solid #1E3A5F;
    border-left: 3px solid var(--c); border-radius: 8px; padding: 4px 8px; font-size: 11.5px; min-width: 0; }
.ka-s2 span { flex: 1 1 auto; min-width: 0; color: #CBD5E1; font-weight: 700; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap; }
.ka-s2 b { color: #F1F5F9; font-weight: 600; white-space: nowrap; font-variant-numeric: tabular-nums; }
.ka-s2 em { font-style: normal; font-weight: 700; color: var(--c); white-space: nowrap; }
.ka-kv { display: grid; grid-template-columns: auto 1fr; gap: 3px 12px; font-size: 12px; }
.ka-kv span { color: #94A3B8; }
.ka-kv b { color: #F1F5F9; font-weight: 600; text-align: right; word-break: break-word; }
.ka-box { background: #071525; border: 1px solid #1E3A5F; border-left: 4px solid var(--c, #1E3A5F);
    border-radius: 10px; padding: 8px 10px; }
.ka-box + .ka-box { margin-top: 6px; }
.ka-box-h { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-weight: 700;
    color: #F1F5F9; font-size: 12px; margin-bottom: 4px; }
.ka-rs { display: flex; gap: 12px; align-items: center; }
.ka-rs-v { font-size: 21px; font-weight: 800; color: #F8FAFC; white-space: nowrap; font-variant-numeric: tabular-nums; }
.ka-rs .ka-kv { flex: 1 1 auto; font-size: 11.5px; }
.ka-tl { display: flex; gap: 1px; height: 16px; margin-top: 4px; }
.ka-tlw { position: relative; }
.ka-tlw .ka-tl-win { position: absolute; top: -3px; bottom: -3px; border: 1.5px dashed rgba(32, 191, 255, .9);
    border-radius: 4px; pointer-events: none; }
.ka-tlk { display: flex; align-items: center; gap: 6px; font-size: 11.5px; margin: 2px 0 4px; }
.ka-tlk span { color: #94A3B8; font-weight: 600; }
.ka-tlk em { font-style: normal; background: #071525; border: 1px solid #1E3A5F; border-radius: 6px;
    padding: 1px 8px; color: #E2E8F0; font-weight: 700; }
.ka-ph3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; margin-top: 8px; }
.ka-ph3 div, .ka-pk div { background: #071525; border: 1px solid #1E3A5F; border-top: 2px solid var(--c);
    border-radius: 8px; padding: 5px 9px; font-size: 11px; color: #94A3B8; min-width: 0; }
.ka-ph3 b, .ka-pk b { display: block; font-size: 12.5px; color: #F1F5F9; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; }
.ka-ph3 small { display: block; color: #64748B; font-size: 10.5px; }
.ka-pk { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px; margin-top: 6px; }
.ka-tl i { flex: 1 1 0; background: var(--c); border-radius: 2px; min-width: 1px; }
.ka-tl-ax { display: flex; justify-content: space-between; gap: 6px; font-size: 10.5px; color: #94A3B8;
    margin-top: 3px; }
.ka-lat { display: grid; grid-template-columns: 1fr auto auto; gap: 3px 10px; font-size: 11.5px;
    align-items: center; margin-top: 6px; }
.ka-lat span { color: #CBD5E1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ka-lat b { color: #F1F5F9; font-weight: 600; text-align: right; font-variant-numeric: tabular-nums; }
.ka-fin { display: grid; grid-template-columns: auto 1fr; gap: 5px 12px; font-size: 12.5px; align-items: center; }
.ka-fin > span { color: #94A3B8; }
.ka-fin > b { text-align: right; color: #F1F5F9; font-weight: 700; }
</style>
"""


def esc(x) -> str:
    return html.escape(str(x))


def badge(text: str, colour: str) -> str:
    return f'<span class="ka-badge" style="--c:{colour}"><i></i>{esc(text)}</span>'


def state_html(sev: int, text: str) -> str:
    return f'<span class="ka-st" style="--c:{SEV_COLOUR[sev]}">{GLYPH[sev]} {esc(text)}</span>'


def title_block(title: str, subtitle: str, icon: str = "chart") -> str:
    return (f'<div class="ka-title"><span class="ka-ico">{icon_img(icon, PALETTE["cyan"], 22)}</span>'
            f'<div><b>{esc(title)}</b><span>{esc(subtitle)}</span></div></div>')


def chips(items) -> str:
    """(label, value, colour, tooltip) — small counters in a row."""
    return '<div class="ka-chips">' + "".join(
        f'<span class="ka-chip" style="--c:{c}" title="{esc(tip)}">{esc(k)} <b>{esc(v)}</b></span>'
        for k, v, c, tip in items) + "</div>"


def empty(text: str, icon: str = "info") -> str:
    return f'<div class="ka-empty">{icon_img(icon, "#64748B", 20)}<span>{esc(text)}</span></div>'


def donut(parts, total: int, centre: str = "Total Sites") -> str:
    """parts: (label, count, colour). The ring and its legend."""
    acc, stops, legend = 0.0, [], ""
    for label, k, colour in parts:
        share = 100.0 * k / total if total else 0.0
        stops.append(f"{colour} {acc:.2f}% {acc + share:.2f}%")
        acc += share
        legend += (f'<div style="--c:{colour}"><i></i><span>{esc(label)}</span>'
                   f'<small>{k:,} ({share:.1f}%)</small></div>')
    grad = ", ".join(stops) if total else f"{PALETTE['nodata']} 0% 100%"
    return (f'<div class="ka-dist"><div class="ka-donut" style="--g:{grad}">'
            f'<div class="ka-donut-c"><b>{total:,}</b><span>{esc(centre)}</span></div></div>'
            f'<div class="ka-leg">{legend}</div></div>')


def ranking(rows, total: int) -> str:
    """rows: (label, sites, critical sites). A bar per KPI, critical part in red."""
    if not rows:
        return empty("No KPI above its threshold in the selected area.", "check")
    top = max(s for _, s, _ in rows) or 1
    out = '<div class="ka-rank">'
    for label, sites, crit in rows:
        w_c = 100.0 * crit / top
        w_w = 100.0 * (sites - crit) / top
        out += (f'<div title="{esc(label)}: {sites} sites above threshold, {crit} critical">'
                f'<span>{esc(label)}</span><span class="ka-bar">'
                f'<i style="width:{w_c:.1f}%;background:{PALETTE["critical"]}"></i>'
                f'<i style="width:{w_w:.1f}%;background:{PALETTE["warning"]}"></i></span>'
                f'<b>{sites:,}</b><small>{(100.0 * sites / total if total else 0):.1f}%</small></div>')
    return out + "</div>"


def sec(title: str, icon: str, note: str = "") -> str:
    return (f'<div class="ka-sec">{icon_img(icon, PALETTE["cyan"], 13)}<span>{esc(title)}</span>'
            + (f"<small>{esc(note)}</small>" if note else "") + "</div>")


def kv(rows) -> str:
    return '<div class="ka-kv">' + "".join(
        f"<span>{esc(k)}</span><b>{esc(v)}</b>" for k, v in rows) + "</div>"


def _value_html(value: str) -> str:
    num, _, unit = str(value).partition(" ")
    return esc(num) + (f"<small> {esc(unit)}</small>" if unit else "")


def tile(t: dict) -> str:
    colour = STATE_COLOUR["Detected"] if t["state"] == "Detected" else SEV_COLOUR[t["sev"]]
    glyph = "●" if t["state"] == "Detected" else GLYPH[t["sev"]]
    sub = (pd.Timestamp(t["peak_time"]).strftime("%d %b %H:%M")
           if t.get("peak_time") is not None and pd.notna(t["peak_time"]) else t["note"])
    tip = " · ".join(x for x in (t["name"], t["threshold"], t["note"]) if x)
    flag = "" if t["judged"] else "<i>count</i>"
    return (f'<div class="ka-tile" style="--c:{colour}" title="{esc(tip)}">'
            f'<div class="ka-tile-k"><span>{esc(t["key"])}</span>{flag}</div>'
            f'<div class="ka-tile-v">{_value_html(t["value"])}</div>'
            f'<div class="ka-tile-s">{glyph} {esc(t["state"].upper())}</div>'
            f'<div class="ka-tile-n">{esc(sub)}</div></div>')


def secondary(t: dict) -> str:
    colour = STATE_COLOUR["Detected"] if t["state"] == "Detected" else SEV_COLOUR[t["sev"]]
    glyph = "●" if t["state"] == "Detected" else GLYPH[t["sev"]]
    tip = " · ".join(x for x in (t["name"], t["threshold"], t["note"]) if x)
    return (f'<div class="ka-s2" style="--c:{colour}" title="{esc(tip)}"><span>{esc(t["key"])}</span>'
            f'<b>{esc(t["value"])}</b><em>{glyph} {esc(t["state"])}</em></div>')


def evidence(r) -> str:
    """One object × KPI above its threshold, as a small card."""
    sev = int(r["sev"])
    word = ("Low" if r["low_is_bad"] else "High") if sev == 1 else r["state"]
    peak = (pd.Timestamp(r["peak_time"]).strftime("%d %b %H:%M")
            if pd.notna(r["peak_time"]) else "—")
    from rfopt.complaints.noc import fmt
    return (f'<div class="ka-box" style="--c:{SEV_COLOUR[sev]}"><div class="ka-box-h">'
            f'<span>{esc(r["label"])}</span>{state_html(sev, word)}</div>'
            + kv([("Value (window)", fmt(float(r["value"]), r["unit"])),
                  ("Threshold", r["threshold"]), ("Object", r["object_id"]),
                  ("Worst hour", f"{peak} · {fmt(float(r['peak']), r['unit'])}")])
            + "</div>")


def kpi_timeline(h: pd.DataFrame, label: str, threshold: str, start, end) -> str:
    """One KPI hour by hour at a site (`h`: its rows of `site_hours`), named on
    top. The problem window is its breach span — first to last hour beyond the
    threshold — with the state before, inside and after it, and its peak."""
    if h is None or h.empty:
        return empty(f"No {label} hours for this site.", "clock")
    h = h.sort_values("hour")
    span = pd.date_range(pd.Timestamp(start).floor("h"), pd.Timestamp(end).floor("h"), freq="h")
    by = h.set_index("hour")
    seq = [(t, int(by["sev"].get(t, -1))) for t in span]
    bad = h[h["sev"] > 0]
    total = len(span) or 1
    low = bool(h["low"].iloc[0]) if "low" in h.columns else False

    def worst(part: pd.DataFrame) -> int:
        return int(part["sev"].max()) if len(part) else -1

    if len(bad):
        lo, hi = bad["hour"].min(), bad["hour"].max()
        i0, i1 = span.get_loc(lo), span.get_loc(hi)
        win = (f'<div class="ka-tl-win" style="left:{100.0 * i0 / total:.2f}%;'
               f'width:{100.0 * (i1 - i0 + 1) / total:.2f}%"></div>')
        phases = [("Before", worst(h[h["hour"] < lo]), f"until {lo:%d %b %H:%M}"),
                  ("Problem Window", worst(h[(h["hour"] >= lo) & (h["hour"] <= hi)]),
                   f"{lo:%d %b %H:%M} → {hi:%d %b %H:%M} · {len(bad)} h beyond"),
                  ("After", worst(h[h["hour"] > hi]),
                   f"from {hi + pd.Timedelta(hours=1):%d %b %H:%M}")]
    else:
        win = ""
        phases = [("Before", -1, "—"), ("Problem Window", 0, "no hour beyond the threshold"),
                  ("After", -1, "—")]
    pk = h.loc[h["value"].idxmin() if low else h["value"].idxmax()]
    bars = "".join(
        f'<i style="--c:{SEV_COLOUR[sv]}" title="{t:%d %b %H:%M} · {PHASE[sv]}"></i>'
        for t, sv in seq)
    mid = span[0] + (span[-1] - span[0]) / 2
    from rfopt.complaints.noc import fmt
    return (f'<div class="ka-tlk"><span>Timeline KPI</span><em>{esc(label)}</em></div>'
            f'<div class="ka-tlw"><div class="ka-tl">{bars}</div>{win}</div>'
            f'<div class="ka-tl-ax"><span>{span[0]:%d %b %H:%M}</span><span>{mid:%d %b %H:%M}</span>'
            f'<span>{span[-1]:%d %b %H:%M}</span></div>'
            '<div class="ka-ph3">' + "".join(
                f'<div style="--c:{SEV_COLOUR[sv]}">{esc(n)}<b>{PHASE[sv]}</b>'
                f"<small>{esc(note)}</small></div>" for n, sv, note in phases) + "</div>"
            '<div class="ka-pk">' + "".join(
                f'<div style="--c:{SEV_COLOUR[int(pk["sev"])]}">{esc(k)}<b>{v}</b></div>'
                for k, v in (("Peak Time", f'{pk["hour"]:%d %b %H:%M}'),
                             ("KPI Value", esc(fmt(float(pk["value"]), pk["unit"]))
                              + f' <small>{esc(pk["object_id"])}</small>'),
                             ("Threshold", esc(threshold)),
                             ("Status", PHASE[int(pk["sev"])]))) + "</div>")


def timeline(hours: list, start, end) -> str:
    """(hour, worst state) across the export window, one bar per hour."""
    if not hours:
        return empty("No judged KPI hours for this site.")
    bars = "".join(
        f'<i style="--c:{SEV_COLOUR[s]}" title="{pd.Timestamp(h):%d %b %H:%M} · {PHASE[s]}"></i>'
        for h, s in hours)
    mid = pd.Timestamp(start) + (pd.Timestamp(end) - pd.Timestamp(start)) / 2
    return (f'<div class="ka-tl">{bars}</div><div class="ka-tl-ax">'
            f'<span>{pd.Timestamp(start):%d %b %H:%M}</span><span>{mid:%d %b %H:%M}</span>'
            f'<span>{pd.Timestamp(end):%d %b %H:%M}</span></div>')


# --------------------------------------------------------------------------- #
# the KPI rings
# --------------------------------------------------------------------------- #
RING_CSS = """
<style>
[data-testid="stHtml"]:has(> .ka-rgs) { container-type: inline-size; }
.ka-rgs { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr));
    background: linear-gradient(180deg, #0D2945 0%, #0B1F33 100%); border: 1px solid #1E3A5F;
    border-radius: 14px; padding: 16px 4px; box-shadow: 0 0 26px rgba(32, 191, 255, .07); }
.ka-rg { display: flex; align-items: center; gap: 12px; padding: 2px 14px; min-width: 0;
    border-left: 1px solid rgba(30, 58, 95, .75); }
.ka-rg:first-child { border-left: 0; }
.ka-rg-ring { position: relative; flex: 0 0 112px; width: 112px; height: 112px; }
.ka-rg-ring > img.ka-rg-img { display: block; width: 112px; height: 112px; }
.ka-rg-c { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center;
    justify-content: center; text-align: center; gap: 1px; }
.ka-rg-c b { font-size: 22px; font-weight: 800; color: #F8FAFC; line-height: 1.1; margin-top: 3px;
    white-space: nowrap; font-variant-numeric: tabular-nums; }
.ka-rg-c b.nd { font-size: 15px; }
.ka-rg-c span { font-size: 9.5px; color: #CBD5E1; max-width: 80px; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap; }
.ka-rg-t { flex: 1 1 auto; min-width: 0; }
.ka-rg-h { font-size: 13px; font-weight: 700; color: #F1F5F9; white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }
.ka-rg-d { font-size: 11px; color: #94A3B8; line-height: 1.3; margin-top: 2px; }
.ka-rg-p { font-size: 12.5px; font-weight: 800; color: var(--c); margin-top: 8px;
    font-variant-numeric: tabular-nums; }
.ka-rg-p small { font-weight: 500; color: #94A3B8; font-size: 10.5px; }
.ka-rg-tr { margin-top: 4px; font-size: 12px; line-height: 1.25; }
.ka-rg-tr b { font-weight: 700; font-variant-numeric: tabular-nums; }
.ka-rg-tr small { display: block; font-size: 10.5px; color: #94A3B8; }
.ka-rg-tr.bad b { color: #EF4444; }
.ka-rg-tr.good b { color: #22C55E; }
.ka-rg-tr.flat b { color: #94A3B8; }
@container (max-width: 1500px) {
    .ka-rg { flex-direction: column; text-align: center; gap: 6px; padding: 2px 8px; }
    .ka-rg-ring { flex-basis: auto; width: 100px; height: 100px; }
    .ka-rg-ring > img.ka-rg-img { width: 100px; height: 100px; }
    .ka-rg-t { width: 100%; }
    .ka-rg-c b { font-size: 20px; }
}
@container (max-width: 900px) {
    .ka-rgs { grid-template-columns: repeat(3, minmax(0, 1fr)); row-gap: 16px; }
    .ka-rg:nth-child(3n+1) { border-left: 0; }
}
@container (max-width: 560px) {
    .ka-rgs { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .ka-rg { border-left: 0 !important; }
}
</style>
"""


def _mix(colour: str, other: str, t: float) -> str:
    a, b = colour.lstrip("#"), other.lstrip("#")
    if len(a) != 6 or len(b) != 6:
        return colour
    ca = [int(a[i:i + 2], 16) for i in (0, 2, 4)]
    cb = [int(b[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(ca, cb))


def ring_img(pct: float | None, colour: str) -> str:
    """A glowing ring filled to `pct`; only its faint track when there is no share."""
    import base64
    import math

    r, w = 50.0, 8.0
    c = 2 * math.pi * r
    body = (f'<circle cx="60" cy="60" r="{r}" fill="none" stroke="{colour}" '
            f'stroke-opacity=".16" stroke-width="{w}"/>')
    if pct is not None and pct > 0:
        arc = c * min(float(pct), 100.0) / 100.0
        body += (f'<circle cx="60" cy="60" r="{r}" fill="none" stroke="url(#g)" '
                 f'stroke-width="{w}" stroke-linecap="round" '
                 f'stroke-dasharray="{arc:.2f} {c:.2f}" transform="rotate(-90 60 60)" '
                 'filter="url(#f)"/>')
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" width="120" '
           'height="120"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
           f'<stop offset="0" stop-color="{_mix(colour, "#FFFFFF", .35)}"/>'
           f'<stop offset="1" stop-color="{colour}"/></linearGradient>'
           '<filter id="f" x="-25%" y="-25%" width="150%" height="150%">'
           '<feGaussianBlur stdDeviation="2.4" result="b"/><feMerge><feMergeNode in="b"/>'
           f'<feMergeNode in="SourceGraphic"/></feMerge></filter></defs>{body}</svg>')
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<img class="ka-rg-img" src="data:image/svg+xml;base64,{b64}" alt="">'


def rings(items: list[dict]) -> str:
    """The KPI summary: one ring per KPI in a single strip.

    item: title, value, label (under the value), note, icon, colour, pct (ring
    fill, None for none), share (the percentage text), trend ((text, bad/good/
    flat, note) or None), tip."""
    out = []
    for it in items:
        colour = it["colour"]
        nd = ' class="nd"' if not str(it["value"])[:1].isdigit() else ""
        share = (f'<div class="ka-rg-p">{esc(it["share"])}'
                 + (f' <small>{esc(it["share_note"])}</small>' if it.get("share_note") else "")
                 + "</div>") if it.get("share") else ""
        tr = it.get("trend")
        trend = (f'<div class="ka-rg-tr {tr[1]}"><b>{esc(tr[0])}</b><small>{esc(tr[2])}</small>'
                 "</div>") if tr else ""
        out.append(
            f'<div class="ka-rg" style="--c:{colour}" title="{esc(it.get("tip", ""))}">'
            f'<div class="ka-rg-ring">{ring_img(it.get("pct"), colour)}<div class="ka-rg-c">'
            f'{icon_img(it["icon"], colour, 19)}<b{nd}>{esc(it["value"])}</b>'
            f'<span>{esc(it["label"])}</span></div></div>'
            f'<div class="ka-rg-t"><div class="ka-rg-h">{esc(it["title"])}</div>'
            f'<div class="ka-rg-d">{esc(it["note"])}</div>{share}{trend}</div></div>')
    return '<div class="ka-rgs">' + "".join(out) + "</div>"


# --------------------------------------------------------------------------- #
# KPI issues by Sup District and City
# --------------------------------------------------------------------------- #
# the Sites page's dark basemap: key-free Esri tiles
ESRI_DARK = ("https://server.arcgisonline.com/ArcGIS/rest/services/"
             "Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}")
MAX_LABELS = 12

REGION_CSS = """
<style>
.ka-area { display: inline-flex; align-items: center; gap: 6px; max-width: 100%; background: rgba(21, 151, 255, .14);
    border: 1px solid rgba(32, 191, 255, .45); border-radius: 8px; padding: 4px 9px; font-size: 12px;
    font-weight: 700; color: #E2E8F0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.ka-mleg { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 11.5px; color: #CBD5E1; padding-top: 2px; }
.ka-mleg span { display: inline-flex; align-items: center; gap: 6px; }
.ka-mleg i { width: 9px; height: 9px; border-radius: 50%; background: var(--c); box-shadow: 0 0 6px var(--c); }
.ka-mleg small { flex-basis: 100%; color: #64748B; font-size: 10.5px; }
.st-key-rf_card_ka_map [data-testid="stPlotlyChart"] { border-radius: 10px; overflow: hidden;
    border: 1px solid #1E3A5F; }
.ka-hbs { display: flex; flex-direction: column; gap: 5px; margin: 2px 0 8px; }
.ka-hb { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, 2fr) 34px; gap: 8px;
    align-items: center; font-size: 11.5px; color: #CBD5E1; }
.ka-hb span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ka-hb i { height: 9px; border-radius: 5px; background: #16324F; overflow: hidden; }
.ka-hb i b { display: block; height: 100%; border-radius: 5px; background: var(--c); }
.ka-hb em { font-style: normal; font-weight: 700; color: #F1F5F9; text-align: right;
    font-variant-numeric: tabular-nums; }
.ka-hb.on span { color: #F8FAFC; font-weight: 700; }
.ka-hb.on i { outline: 1px solid #F8FAFC; }
</style>
"""


def map_legend(boundaries: bool = False, sites: bool = False) -> str:
    from _kpi_bounds import ATTRIBUTION
    note = (f"Governorates, Cities (districts) and Sup Districts are the official "
            f"administrative boundaries. "
            f"{ATTRIBUTION}." if boundaries else
            "The boundary file is missing: an area is one circle at the median position "
            "of its sites.")
    dot = ('<span style="--c:#F8FAFC"><i style="width:6px;height:6px"></i>'
           "affected site (its worst check)</span>") if sites else ""
    return ('<div class="ka-mleg">'
            + "".join(f'<span style="--c:{c}"><i></i>{t}</span>'
                      for t, c in (("Critical", PALETTE["critical"]),
                                   ("Warning", PALETTE["warning"]),
                                   ("Normal", PALETTE["excellent"])))
            + dot + f"<small>{esc(note)}</small></div>")


def hbars(t: pd.DataFrame, chosen=()) -> str:
    """Areas as bars of their sites with an issue, coloured by their worst state:
    the ranked table at a glance. A picked area is outlined."""
    picked = {chosen} if isinstance(chosen, str) else set(chosen or ())
    if t is None or t.empty:
        return ""
    top = max(int(t["affected"].max()), 1)
    rows = []
    for r in t.itertuples(index=False):
        colour = SEV_COLOUR[int(r.worst)] if r.worst > 0 else PALETTE["excellent"]
        rows.append(f'<div class="ka-hb{" on" if r.name in picked else ""}" '
                    f'style="--c:{colour}" title="{esc(_tip(r))}"><span>{esc(r.name)}</span>'
                    f'<i><b style="width:{100.0 * r.affected / top:.1f}%"></b></i>'
                    f"<em>{int(r.affected)}</em></div>")
    return '<div class="ka-hbs">' + "".join(rows) + "</div>"


def _tip(r) -> str:
    return (f"{r.name} · {r.affected} of {r.sites} sites with an issue · "
            f"{r.critical} critical · {r.warning} warning"
            + (f" · most common: {r.top_issue}" if r.top_issue else ""))


SITE_TIP = "Site "


def region_map(table: pd.DataFrame, level: str, selected=None, areas=None,
               sites: pd.DataFrame | None = None):
    """The areas of one level on the Sites page's dark basemap, coloured by their
    worst state, and the affected sites of the current filter. `selected` is one
    area's name or several (the Sup Districts picked together): each is outlined
    in white.

    With the official boundaries (`_kpi_bounds`) each sub-district (Sup District
    level), district (City level: its sub-districts together) or governorate is
    drawn as its own boundary; one no filtered site falls in is a faint outline. An area without a boundary here —
    the sites outside R5, or every area when the file is missing — is a circle at
    the median position of its sites, drawn first so a boundary it overlaps stays
    clickable. `sites` (site_id, sev, label, issues, latitude, longitude) are small
    markers on top, at the site's EP-tracker position. Every clickable shape's
    tooltip starts with the area's name, or "Site <id>": the click answer the page
    reads. The view fits the areas that hold data."""
    import math

    import folium

    from _kpi_bounds import governorates, sub_districts
    from _kpi_region import UNKNOWN

    from _kpi_region import _GOV_OF_AREA, town_name

    if areas:
        shapes = governorates(areas) if level == "Governorate" else sub_districts(areas)
    else:
        shapes = []

    def key(a) -> str:
        if level == "Sup District":
            return a.name
        if level == "City":
            return town_name(a.district)
        return _GOV_OF_AREA.get(a.governorate, a.governorate)

    picked = ({selected} if isinstance(selected, str) else set(selected or ())) - {""}

    rows = table[table["name"] != UNKNOWN].reset_index(drop=True)
    from _map_assets import TileGuard, add_basemap, use_local_libraries

    fmap = folium.Map(location=[31.0, 46.6], zoom_start=7, tiles=None, control_scale=True,
                      max_zoom=16)
    add_basemap(fmap, ESRI_DARK, attr="Tiles © Esri — Esri, DeLorme, HERE", max_zoom=16)
    use_local_libraries(fmap)
    fmap.add_child(TileGuard())
    fmap.get_root().header.add_child(folium.Element(
        "<style>.leaflet-container{background:#071525}"
        ".leaflet-marker-icon.ka-lbl{pointer-events:none!important}"
        ".ka-lbl div{font:600 11px 'Segoe UI',system-ui,sans-serif;color:#E2E8F0;"
        "white-space:nowrap;text-shadow:0 0 3px #000,0 0 6px #000}</style>"))
    by_name = {r.name: r for r in rows.itertuples(index=False)}
    labelled = set(rows.sort_values("affected", ascending=False)
                   .head(MAX_LABELS if level != "Governorate" else len(rows))["name"])
    bounds: list = []        # where the data is
    outline: list = []       # every boundary, for a view with no data

    def colour_of(r) -> str:
        return SEV_COLOUR[int(r.worst)] if r.worst > 0 else PALETTE["excellent"]

    def label(where, text, dx: float = 0.0) -> None:
        folium.Marker(where, icon=folium.DivIcon(
            html=f"<div>{esc(text)}</div>", class_name="ka-lbl",
            icon_size=(220, 16), icon_anchor=(-dx, 8))).add_to(fmap)

    parts: dict = {}
    for a in shapes:
        parts.setdefault(key(a), []).append(a)
    drawn = {n for n in parts if n in by_name}

    # the circles first: a boundary drawn after them stays on top, so a circle
    # cannot swallow the clicks meant for the areas it overlaps
    top = max(int(rows["affected"].max()), 1) if len(rows) else 1
    for r in rows.itertuples(index=False):
        if r.name in drawn or pd.isna(r.latitude) or pd.isna(r.longitude):
            continue
        where = [float(r.latitude), float(r.longitude)]
        bounds.append(where)
        colour = colour_of(r)
        radius = 6 + 14 * math.sqrt(r.affected / top)
        if r.name in picked:
            folium.CircleMarker(where, radius=radius + 7, color="#F8FAFC", weight=2,
                                fill=False, opacity=0.9).add_to(fmap)
        folium.CircleMarker(where, radius=radius, color=colour, weight=1.5, fill=True,
                            fill_color=colour, fill_opacity=0.72,
                            tooltip=folium.Tooltip(esc(_tip(r)))).add_to(fmap)
        if (r.name in labelled and (r.affected > 0 or level == "Governorate")) or r.name in picked:
            label(where, r.name, radius + 3)

    for name, pieces in parts.items():
        r = by_name.get(name)
        for a in pieces:
            rings_ = [[[lat, lon] for lon, lat in ring] for ring in a.rings]
            outline.extend(rings_[0])
            if r is None:
                folium.Polygon(rings_, color="#64748B", weight=1, opacity=0.55, fill=True,
                               fill_color="#64748B", fill_opacity=0.05).add_to(fmap)
                continue
            bounds.extend(rings_[0])
            colour, chosen = colour_of(r), name in picked
            folium.Polygon(rings_, color="#F8FAFC" if chosen else colour,
                           weight=3 if chosen else 1.4, fill=True, fill_color=colour,
                           fill_opacity=0.45 if chosen else 0.28,
                           tooltip=folium.Tooltip(esc(_tip(r)))).add_to(fmap)
        if r is not None and ((name in labelled and (r.affected > 0 or level == "Governorate"))
                              or name in picked):
            outer = max((a.rings[0] for a in pieces), key=len)
            label([sum(p[1] for p in outer) / len(outer), sum(p[0] for p in outer) / len(outer)],
                  name)

    if sites is not None and len(sites):
        for s in sites.itertuples(index=False):
            where = [float(s.latitude), float(s.longitude)]
            bounds.append(where)
            colour = SEV_COLOUR[int(s.sev)]
            folium.CircleMarker(
                where, radius=4.2 if int(s.sev) == 2 else 3.4, color="#0B1F33", weight=1,
                fill=True, fill_color=colour, fill_opacity=0.95,
                tooltip=folium.Tooltip(esc(
                    f"{SITE_TIP}{s.site_id} · {s.label} · {PHASE[int(s.sev)]}"
                    f" · {int(s.issues)} check{'s' if int(s.issues) != 1 else ''} above "
                    "threshold"))).add_to(fmap)
    view = bounds or outline
    if view:
        lats = [b[0] for b in view]
        lons = [b[1] for b in view]
        fmap.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]], padding=(18, 18))
    return fmap
