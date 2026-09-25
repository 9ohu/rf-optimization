"""KPI Analysis · Report Export — one KPI, one area, one period, as a report.

Top: the filters (Technology, KPI, Governorate, Sup Districts, Site, Time
Period and the Threshold, set by the user). Left: the report as its PowerPoint
slides, one at a time, redrawn whenever a filter or the threshold changes.
Right: the Excel workbook (the Draw Data export's own layout), then the two
exports, saved to the Desktop (with a download as the fallback).

A cell is affected when the KPI is beyond the threshold — judged by
`_kpi_health.object_values`, the KPI Analysis engine, with the user's threshold
in place of the configured one and the KPI's own direction (a low availability
is bad, a high interference is bad) — or when its hours show a sudden spike
against its own normal behaviour (`rfopt.kpi.anomaly.sudden_spikes`). Sites are
placed by `_kpi_region.site_regions`, the tickets are the Daily Target of
Complaint Data, and everything the preview and the two files show comes from
one `rfopt.reports.kpi_report.KpiReport`.
"""

from __future__ import annotations

import base64
import dataclasses
import io
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

import _kpi_workspace as W
import _resources as R
import _svg_charts as C
from _charts import LINE_COLOURS
from _kpi_health import Judged, judged_columns, object_values
from _kpi_region import GOVERNORATES, UNKNOWN
from _ui import PALETTE, fmt_bytes, icon_img, title_html
from rfopt.kpi.anomaly import SPIKE_COLS, sudden_spikes
from rfopt.kpi.trends import agg_how
from rfopt.reports.kpi_report import (SLIDES, TICKET_COLS, Assets, KpiReport, build_pptx,
                                      build_xlsx, pptx_name, ticket_header, ticket_text,
                                      top_sites_note, trend_note, xlsx_name)

ASSETS = Path(__file__).resolve().parent / "assets" / "report"
ALL_GOV, ALL_SITES = "All Governorates", "All Sites"
TDD_LABEL = "4G TDD Interference"
PAGE_ROWS = 50
BAD, OKC, BLUE = PALETTE["critical"], PALETTE["excellent"], "#1597FF"
ss = st.session_state

CSS = """
<style>
.rx-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.rx-sub { font-size: 12px; color: #94A3B8; margin-top: -6px; }
.rx-slide { position: relative; width: 100%; aspect-ratio: 16 / 9; container-type: inline-size;
    background: #071525; border: 1px solid #1E3A5F; border-radius: 10px; overflow: hidden;
    color: #E2E8F0; font-family: 'Segoe UI', system-ui, sans-serif; }
.rx-cover { background-size: cover; background-position: center; }
.rx-logo { position: absolute; height: 5.4cqw; }
.rx-cov { position: absolute; left: 4.1cqw; top: 10.5cqw; width: 50cqw; display: flex;
    flex-direction: column; gap: 1.6cqw; }
.rx-title { font-size: 3.5cqw; font-weight: 800; line-height: 1.15; color: #FFFFFF;
    text-shadow: 0 0 1cqw rgba(0,0,0,.6); }
.rx-sum { display: grid; grid-template-columns: 14cqw 1.4cqw auto; row-gap: .25cqw;
    font-size: 1.28cqw; line-height: 1.35; }
.rx-sum span { color: #CBD5E1; } .rx-sum b { color: #FFFFFF; font-weight: 700; }
.rx-desc { font-size: 1.22cqw; line-height: 1.4; color: #E2E8F0; max-width: 44cqw; }
.rx-h { position: absolute; left: 3.4cqw; top: 2.3cqw; right: 20cqw; }
.rx-h b { display: block; font-size: 2.4cqw; font-weight: 800; color: #F8FAFC; }
.rx-h span { display: block; font-size: 1.1cqw; color: #94A3B8; margin-top: .3cqw;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rx-rule { position: absolute; left: 3.4cqw; right: 3.4cqw; top: 9cqw; height: .14cqw;
    background: #20BFFF; }
.rx-page { position: absolute; right: 3.4cqw; bottom: 1.6cqw; font-size: 1cqw; color: #94A3B8; }
.rx-body { position: absolute; left: 3.4cqw; right: 3.4cqw; top: 10.6cqw; bottom: 4.2cqw;
    display: grid; gap: 1.2cqw; }
.rx-p { background: #0B1F33; border: 1px solid #1E3A5F; border-radius: .8cqw; padding: 1cqw 1.2cqw;
    display: flex; flex-direction: column; min-height: 0; min-width: 0; }
.rx-p b { font-size: 1.35cqw; color: #F1F5F9; }
.rx-p small { font-size: .95cqw; color: #94A3B8; margin: .2cqw 0 .6cqw; }
.rx-p img.rx-chart { flex: 1 1 auto; width: 100%; min-height: 0; object-fit: contain; }
.rx-p .rx-none { flex: 1 1 auto; display: flex; align-items: center; justify-content: center;
    font-size: 1.2cqw; color: #94A3B8; }
.rx-big { font-size: 6.5cqw; font-weight: 800; color: #20BFFF; line-height: 1.1; margin: .8cqw 0; }
.rx-p ul { margin: .4cqw 0 0; padding-left: 1.4cqw; font-size: 1.05cqw; color: #94A3B8; line-height: 1.6; }
.rx-tbl { width: 100%; border-collapse: collapse; font-size: .95cqw; }
.rx-tbl th { text-align: left; color: #20BFFF; background: #0B1F33; padding: .55cqw .7cqw;
    border-bottom: 1px solid #1E3A5F; }
.rx-tbl td { padding: .5cqw .7cqw; border-bottom: 1px solid #16324F; color: #E2E8F0; white-space: nowrap; }
.rx-tbl tr:nth-child(even) td { background: #0D2945; }
.rx-more { font-size: 1cqw; color: #94A3B8; margin-top: .6cqw; }
.rx-x { display: flex; gap: 12px; align-items: center; }
.rx-x-ico { flex: 0 0 46px; height: 46px; border-radius: 10px; display: flex; align-items: center;
    justify-content: center; font: 800 20px 'Segoe UI', sans-serif; color: #fff; }
.rx-x-t { font-size: 14.5px; font-weight: 700; color: #F1F5F9; }
.rx-x-s { font-size: 12px; color: #94A3B8; }
.rx-info { display: flex; gap: 12px; background: #071525; border: 1px solid #1E3A5F;
    border-left: 3px solid #20BFFF; border-radius: 10px; padding: 10px 12px; font-size: 12.5px;
    color: #CBD5E1; line-height: 1.55; }
.rx-info b { color: #20BFFF; }
.st-key-rx_nav { justify-content: flex-end; }
</style>
"""


# --------------------------------------------------------------------------- #
# the KPIs a report can be about: every KPI with a threshold, and TDD interference
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Choice:
    label: str           # "4G TDD Interference", "4G DL PRB", "3G RTWP"
    column: str
    kind: str            # 4G / 3G
    tdd: bool            # UL interference over the TDD cells only
    judged: Judged       # the KPI Analysis judgement of the column


def kpi_choices(ws) -> list[Choice]:
    out, seen = [], set()
    for _, info in ws.files:
        if info.kind not in ("4G", "3G", "2G", "Other"):
            continue
        for j in judged_columns(info.all_kpis, info.kind):
            if j.rule is None or j.label in seen:
                continue
            seen.add(j.label)
            out.append(Choice(j.label, j.column, j.kind, False, j))
            if j.key == "INTER" and j.kind == "4G" and TDD_LABEL not in seen:
                seen.add(TDD_LABEL)
                out.append(Choice(TDD_LABEL, j.column, j.kind, True, j))
    return out


def _user_rule(rule, threshold: float):
    """The KPI's rule with the user's threshold: its direction stays its own."""
    try:
        return dataclasses.replace(rule, warning=float(threshold), critical=float(threshold))
    except TypeError:
        import copy
        r = copy.copy(rule)
        r.warning = r.critical = float(threshold)
        return r


# --------------------------------------------------------------------------- #
# the report data, built once per set of filters
# --------------------------------------------------------------------------- #
def _frame(ws, ch: Choice, period) -> pd.DataFrame:
    parts = [W.raw(b, (ch.column,)) for b, i in ws.files
             if i.kind == ch.kind and ch.column in set(i.all_kpis)]
    if not parts:
        return pd.DataFrame()
    df = W.in_period(pd.concat(parts, ignore_index=True), period)
    if ch.tdd:
        df = df[df.get("duplex", pd.Series("", index=df.index)).astype(str).str.upper()
                == "CELL_TDD"]
    return df[df[ch.column].notna()]


def _tickets(regions: pd.DataFrame, keep_sites: set | None, period) -> pd.DataFrame:
    """The Daily Target's tickets in the area (by their site's place) and period."""
    ds = R.target()
    if ds is None:
        return pd.DataFrame(columns=TICKET_COLS)
    from rfopt.complaints.correlate import local_time
    t = W.tickets(ds.sha1, ds)

    def col(name: str) -> pd.Series:
        return t[name] if name in t.columns else pd.Series([""] * len(t), index=t.index)

    def clean(s: pd.Series) -> pd.Series:
        return s.map(lambda v: "" if v is None or str(v).strip().lower() in ("", "nan", "none",
                                                                           "nat") else str(v).strip())

    out = pd.DataFrame({
        "Ticket ID": clean(col("ticket_id")),
        "Site ID": clean(col("site_id")).str.upper(),
        "City": clean(col("city")),
        "SLA Target Time": (local_time(t["sla_target"]) if "sla_target" in t.columns
                            else pd.Series(pd.NaT, index=t.index)),
        "Create Time": (local_time(t["create_time"]) if "create_time" in t.columns
                        else pd.Series(pd.NaT, index=t.index)),
        "Problem Time": t["problem_local"],
        "Is CMC": clean(col("is_cmc")),
        "KPI Value": np.nan,                  # the site's KPI at the problem hour (_build)
    })
    if period:
        day = out["Problem Time"].dt.date
        out = out[day.between(period[0], period[1])]
    if keep_sites is not None:
        out = out[out["Site ID"].isin(keep_sites)]
    return out.reset_index(drop=True)


@st.cache_resource(show_spinner="Building the report…", max_entries=6)
def _build(key: tuple, _ws, _ch: Choice, gov: str, sds: tuple, site: str, period: tuple,
           threshold: float) -> KpiReport:
    ch = _ch
    df = _frame(_ws, ch, period)
    j = ch.judged
    rule = _user_rule(j.rule, threshold)
    judged = dataclasses.replace(j, rule=rule, label=ch.label)
    low = rule.direction == "up"
    # one row per cell (the 4G export's own cell, the 3G export's NodeB)
    cell_df = df.assign(sector_id=df["object"].astype(str)) if len(df) else df
    objs = object_values(cell_df, judged) if len(df) else pd.DataFrame(
        columns=["site_id", "object_id", "value", "sev", "peak", "peak_time"])
    # a sudden spike against the cell's own normal hours, from the KPI's warning
    # level on (or from the user's threshold, where that is the milder one)
    warn = float(j.rule.warning)
    level = max(warn, float(threshold)) if low else min(warn, float(threshold))
    spikes = (sudden_spikes(df, ch.column, low_is_bad=low, level=level) if len(df)
              else pd.DataFrame(columns=SPIKE_COLS)).set_index("object")
    by_cell = df.groupby(df["object"].astype(str))[ch.column] if len(df) else None
    worst = ((by_cell.min() if low else by_cell.max()) if by_cell is not None
             else pd.Series(dtype=float))

    ticket_all = _tickets(pd.DataFrame(), None, period)
    sites = set(objs["site_id"].astype(str)) | set(ticket_all["Site ID"]) - {""}
    regions = W.placed(sites)

    def place(ids, col):
        return regions[col].reindex(pd.Index(ids)).fillna(UNKNOWN).to_numpy()

    oid = objs["object_id"].astype(str)
    sid = objs["site_id"].astype(str).to_numpy()
    cells = pd.DataFrame({
        "object_id": oid.to_numpy(), "site_id": sid, "cell_name": oid.to_numpy(),
        "governorate": place(sid, "governorate"), "sup_district": place(sid, "sup_district"),
        "value": objs["value"].to_numpy(dtype=float),
        "peak": objs["peak"].to_numpy(dtype=float), "peak_time": objs["peak_time"].to_numpy(),
        "worst": worst.reindex(oid).to_numpy(dtype=float),
        "beyond": (objs["sev"].to_numpy() > 0) if len(objs) else np.array([], dtype=bool),
        "spike": oid.isin(spikes.index).to_numpy(),
        "spike_value": spikes["spike_value"].reindex(oid).to_numpy(dtype=float),
        "spike_time": pd.to_datetime(spikes["spike_time"].reindex(oid).to_numpy()),
    })
    cells["affected"] = cells["beyond"] | cells["spike"]

    def in_scope(frame: pd.DataFrame, site_col: str, gov_col=None, sd_col=None) -> pd.Series:
        m = pd.Series(True, index=frame.index)
        if gov:
            m &= frame[gov_col] == gov
        if sds:
            m &= frame[sd_col].isin(list(sds))
        if site:
            m &= frame[site_col] == site
        return m

    cells = cells[in_scope(cells, "site_id", "governorate", "sup_district")].reset_index(drop=True)

    # the cells' hours as the KPI export has them: the Excel workbook, the site
    # lines and the trend are all drawn from these
    cols = [c for c in ("datetime", "object", "site_id", "duplex", "parent", ch.column)
            if c in df.columns]
    rows = (df.loc[df["object"].astype(str).isin(set(cells["object_id"])), cols]
            .assign(tech=ch.kind).reset_index(drop=True) if len(df)
            else pd.DataFrame(columns=cols + ["tech"]))
    # the network average, hour by hour (never folded into days)
    trend = (rows.groupby("datetime")[ch.column].mean().sort_index() if len(rows)
             else pd.Series(dtype=float))

    keep_sites = set(regions.index[in_scope(regions.assign(_s=regions.index), "_s",
                                            "governorate", "sup_district").to_numpy()])
    tickets = (ticket_all if not (gov or sds or site)
               else ticket_all[ticket_all["Site ID"].isin(keep_sites)].reset_index(drop=True))
    if len(tickets):
        # each ticket's KPI: its site at the problem hour, drawn as Draw Data
        # draws a site (its cells averaged, or added up for a count)
        site_hour = (rows.groupby(["site_id", "datetime"])[ch.column].agg(agg_how(ch.column))
                     if len(rows) else pd.Series(dtype=float))
        hour = tickets["Problem Time"].dt.floor("h")
        tickets = tickets.assign(**{"KPI Value": [
            float(site_hour.get((s, h), np.nan)) if pd.notna(h) else np.nan
            for s, h in zip(tickets["Site ID"], hour)]})

    return KpiReport(
        tech=ch.kind, kpi=ch.label, column=ch.column, unit=j.unit, low_is_bad=low,
        per_day=j.how == "day_sum", threshold=float(threshold), governorate=gov,
        sup_districts=list(sds), site=site, start=period[0], end=period[1], cells=cells,
        rows=rows, trend=trend, tickets=tickets)


# --------------------------------------------------------------------------- #
# the slides, as the PowerPoint will have them
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=4)
def _data_uri(name: str, width: int = 0) -> str:
    from PIL import Image
    path = ASSETS / name
    if width and path.suffix.lower() == ".jpg":
        im = Image.open(path).convert("RGB")
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _esc(x) -> str:
    import html
    return html.escape(str(x))


def _panel(title: str, note: str, body: str) -> str:
    return f'<div class="rx-p"><b>{_esc(title)}</b><small>{_esc(note)}</small>{body}</div>'


def _none(text: str) -> str:
    return f'<div class="rx-none">{_esc(text)}</div>'


def _frame_html(r: KpiReport, k: int, title: str, body: str, cols: str) -> str:
    scope = r.sd_text if r.sup_districts else r.site_text
    return (f'<div class="rx-slide"><div class="rx-h"><b>{_esc(title)}</b>'
            f'<span>{_esc(r.kpi)} · {_esc(r.gov_text)} · {_esc(scope)} · '
            f'{_esc(r.period_text)}</span></div>'
            f'<img class="rx-logo" style="right:3.4cqw;top:2.6cqw;height:3.6cqw" '
            f'src="{_data_uri("huawei_logo_white.png")}" alt="HUAWEI">'
            f'<div class="rx-rule"></div>'
            f'<div class="rx-body" style="grid-template-columns:{cols}">{body}</div>'
            f'<div class="rx-page">{k + 1} / {len(SLIDES)}</div></div>')


def slide_html(r: KpiReport, k: int) -> str:
    what = "NodeBs" if r.tech == "3G" else "cells"
    short = r.kpi[len(r.tech):].strip() if r.kpi.startswith(r.tech) else r.kpi
    if k == 0:
        rows = "".join(f"<span>{_esc(a)}</span><span>:</span><b>{_esc(b)}</b>"
                       for a, b in r.summary_rows())
        return (f'<div class="rx-slide rx-cover" style="background-image:url('
                f'{_data_uri("report_cover.jpg", 1280)})">'
                f'<img class="rx-logo" style="left:4.1cqw;top:3.4cqw" '
                f'src="{_data_uri("huawei_logo_white.png")}" alt="HUAWEI">'
                f'<div class="rx-cov"><div class="rx-title">{_esc(r.tech)} {_esc(short)}<br>'
                f'Analysis Report</div><div class="rx-sum">{rows}</div>'
                f'<div class="rx-desc">{_esc(r.description)}</div></div></div>')
    if k == 1:
        t = r.trend.dropna()
        series = [("Network Average", list(t.to_numpy(dtype=float)), "#20BFFF", False)]
        if not r.per_day:
            series.append((f"Threshold ({r.threshold:g}{r.unit})", [r.threshold] * len(t),
                           BAD, True))
        chart = (C.line([f"{x:%d %b %H:%M}" for x in t.index], series, w=1100, h=390)
                 if len(t) else _none("No KPI data in the area and period."))
        return _frame_html(r, k, "R5 Network Trend", _panel(
            f"{r.kpi} — network average (hourly)", trend_note(r), chart), "1fr")
    if k == 2:
        parts = []
        for title, col, keep in (("Governorate", "governorate", ()),
                                 ("Sup District", "sup_district", tuple(r.sup_districts))):
            s = r.affected_by(col, keep).head(8)
            body = (C.bars(list(s.index), list(s.to_numpy()),
                           [BAD] + [BLUE] * (len(s) - 1), w=540, h=300)
                    if len(s) and s.sum() else _none(f"No affected {what}."))
            parts.append(_panel(f"Affected {what} by {title}",
                                f"beyond the threshold {r.threshold_text} or with a sudden "
                                "spike", body))
        return _frame_html(r, k, "Affected Cells by Area", "".join(parts), "1fr 1fr")
    if k == 3:
        lines = r.site_lines(10)
        chart = (C.time_lines(list(lines.items()), LINE_COLOURS, w=1100, h=400,
                              fill=all(float(v.min()) >= 0 for v in lines.values()))
                 if lines else _none("No affected site in the area and period."))
        return _frame_html(r, k, "Top Sites", _panel(
            f"Top 10 Sites by {short}", top_sites_note(r), chart), "1fr")
    if k == 4:
        s = r.top_ticket_sites(10)
        chart = (C.hbars(list(s.index), list(s.to_numpy()), BLUE, w=720, h=330)
                 if len(s) else _none("No ticket in the area and period."))
        big = (f'<div class="rx-p"><b>Total Tickets</b><div class="rx-big">'
               f"{r.total_tickets:,}</div><ul><li>Area: {_esc(r.gov_text)}</li>"
               f"<li>Sup District(s): {_esc(r.sd_text)}</li><li>Site: {_esc(r.site_text)}</li>"
               f"<li>Problem time within {_esc(r.period_text)}</li>"
               "<li>Source: the Daily Target (Complaint Data)</li></ul></div>")
        return _frame_html(r, k, "Tickets", big + _panel(
            "Top 10 Sites by Number of Tickets", "tickets per site in the area and period",
            chart), "1fr 2.6fr")
    t = r.tickets.sort_values("Problem Time", ascending=False, kind="stable").head(14)
    if t.empty:
        body = _panel("Tickets", "", _none("No ticket in the area and period."))
    else:
        head = "".join(f"<th>{_esc(ticket_header(r, c))}</th>" for c in TICKET_COLS)
        rows = "".join("<tr>" + "".join(f"<td>{_esc(ticket_text(v))}</td>" for v in row)
                       + "</tr>" for row in t[TICKET_COLS].itertuples(index=False))
        more = r.total_tickets - len(t)
        body = (f'<div><table class="rx-tbl"><tr>{head}</tr>{rows}</table>'
                + (f'<div class="rx-more">… and {more:,} more ticket{"s" if more != 1 else ""} '
                   "in the selected area and period</div>" if more > 0 else "") + "</div>")
    return _frame_html(r, k, "Received Tickets", body, "1fr")


# --------------------------------------------------------------------------- #
# saving to the Desktop
# --------------------------------------------------------------------------- #
def desktop_dir() -> Path | None:
    """The user's Desktop folder (its real place when Windows moved it), if any.
    `RFOPT_DESKTOP` points it elsewhere (the tests use their own folder)."""
    override = os.environ.get("RFOPT_DESKTOP")
    if override:
        p = Path(override)
        return p if p.is_dir() else None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer"
                            r"\User Shell Folders") as k:
            p = Path(os.path.expandvars(winreg.QueryValueEx(k, "Desktop")[0]))
            if p.is_dir():
                return p
    except (ImportError, OSError):
        pass
    p = Path.home() / "Desktop"
    return p if p.is_dir() else None


def save_to_desktop(name: str, data: bytes) -> tuple[Path | None, str]:
    """(where it was saved, or None, and why not). Saved only if it reads back."""
    folder = desktop_dir()
    if folder is None:
        return None, "no Desktop folder is reachable from the app"
    target = folder / name
    try:
        target.write_bytes(data)
    except PermissionError:
        return None, f"{name} is open in another program — close it and export again"
    except OSError as exc:
        return None, str(exc)
    if not target.is_file() or target.stat().st_size != len(data):
        return None, "the file did not read back from the Desktop"
    return target, ""


# --------------------------------------------------------------------------- #
# the tab
# --------------------------------------------------------------------------- #
def _fit(key: str, options, fallback=None, multi: bool = False) -> None:
    W.restore(key)
    if key not in ss:
        if fallback is not None:
            ss[key] = fallback
        return
    v = ss[key]
    if multi:
        ss[key] = [x for x in (v or []) if x in options]
    elif v not in options:
        ss[key] = fallback


def _export(kind: str, r: KpiReport) -> None:
    with st.spinner("Writing the PowerPoint…" if kind == "pptx" else "Writing the workbook…"):
        if kind == "pptx":
            data = build_pptx(r, Assets(ASSETS / "report_cover.jpg",
                                        ASSETS / "huawei_logo_white.png"))
            name = pptx_name(r)
        else:
            data, name = build_xlsx(r), xlsx_name(r)
    where, why = save_to_desktop(name, data)
    ss[f"rx_out_{kind}"] = (name, data, str(where) if where else "", why)


def _export_card(kind: str, r: KpiReport, title: str, sub: str, colour: str, letter: str,
                 disabled: bool = False) -> None:
    with st.container(key=f"rf_card_rx_exp_{kind}", border=True):
        c1, c2 = st.columns([2.3, 1.2], gap="small", vertical_alignment="center")
        c1.html(f'<div class="rx-x"><span class="rx-x-ico" style="background:{colour}">'
                f'{letter}</span><div><div class="rx-x-t">{_esc(title)}</div>'
                f'<div class="rx-x-s">{_esc(sub)}</div></div></div>')
        c2.button("Export", icon=":material/download:", key=f"rx_exp_{kind}", type="primary",
                  width="stretch", on_click=_export, args=(kind, r),
                  disabled=disabled or not r.total_cells)
        out = ss.get(f"rx_out_{kind}")
        if out:
            name, data, where, why = out
            if where:
                st.success(f"Saved to the Desktop: {where} ({fmt_bytes(len(data))})",
                           icon=":material/check_circle:")
            else:
                st.warning(f"Not saved to the Desktop: {why}. Download it instead.",
                           icon=":material/warning:")
            st.download_button(f"Download {name}", data=data, file_name=name,
                               key=f"rx_dl_{kind}", icon=":material/save:", width="stretch",
                               on_click="ignore",
                               mime=("application/vnd.openxmlformats-officedocument."
                                     + ("presentationml.presentation" if kind == "pptx"
                                        else "spreadsheetml.sheet")))


def render(ws) -> None:
    st.html(CSS)
    choices = kpi_choices(ws)
    if not choices:
        st.html('<div class="rf-card">No KPI with a threshold in the loaded KPI Data: the '
                "report needs a judged KPI (PRB, interference, availability, RTWP …).</div>")
        return
    order = {"4G": 0, "3G": 1, "2G": 2}
    techs = sorted({c.kind for c in choices}, key=lambda t: order.get(t, 9))
    _fit("rx_tech", techs, techs[0])
    by_label = {c.label: c for c in choices}

    # every loaded site of the technology, placed like everywhere else
    with st.container(key="rf_card_rx_filters", border=True):
        cols = st.columns([0.75, 1.5, 1.05, 1.85, 1.0, 1.35, 0.9], gap="small",
                          vertical_alignment="bottom")
        with cols[0]:
            tech = st.selectbox("Technology", techs, key="rx_tech")
        W.remember("rx_tech", tech)
        labels = [c.label for c in choices if c.kind == tech]
        default = TDD_LABEL if TDD_LABEL in labels else labels[0]
        _fit("rx_kpi", labels, default)
        with cols[1]:
            kpi = st.selectbox("KPI", labels, key="rx_kpi")
        W.remember("rx_kpi", kpi)
        ch = by_label[kpi]
        index = pd.concat([W.index_of(b) for b, i in ws.files if i.kind == tech],
                          ignore_index=True)
        places = W.placed(index["site_id"].dropna().astype(str).unique())
        gov_opts = [ALL_GOV] + [g for g in GOVERNORATES if (places["governorate"] == g).any()]
        _fit("rx_gov", gov_opts, ALL_GOV)
        with cols[2]:
            gov = st.selectbox("Governorate", gov_opts, key="rx_gov")
        W.remember("rx_gov", gov)
        gp = places if gov == ALL_GOV else places[places["governorate"] == gov]
        sd_opts = sorted(set(gp["sup_district"]) - {UNKNOWN})
        _fit("rx_sd", sd_opts, [], multi=True)
        with cols[3]:
            sds = st.multiselect("Sup District", sd_opts, key="rx_sd",
                                 placeholder="All Sup Districts")
        W.remember("rx_sd", sds)
        sp = gp if not sds else gp[gp["sup_district"].isin(sds)]
        site_opts = [ALL_SITES] + sorted(sp.index)
        _fit("rx_site", site_opts, ALL_SITES)
        with cols[4]:
            site = st.selectbox("Site", site_opts, key="rx_site")
        W.remember("rx_site", site)
        lo, hi = W.window_of(ws) if ws.tech == tech else (
            index["datetime"].min().date(), index["datetime"].max().date())

        def ok(v) -> bool:
            return isinstance(v, (tuple, list)) and len(v) == 2 and lo <= v[0] <= v[1] <= hi

        W.restore("rx_period", ok)
        if not isinstance(ss.get("rx_period"), (tuple, list)) or not ss["rx_period"] or (
                len(ss["rx_period"]) == 2 and not ok(ss["rx_period"])):
            ss["rx_period"] = (lo, hi)
        with cols[5]:
            st.date_input("Time Period", min_value=lo, max_value=hi, key="rx_period",
                          format="DD/MM/YYYY")
        period = tuple(ss["rx_period"]) if ok(ss["rx_period"]) else ss.get("rx_period_last",
                                                                            (lo, hi))
        ss["rx_period_last"] = period
        W.remember("rx_period", period)
        rule = ch.judged.rule
        thr_key = f"rx_thr_{kpi}"
        W.restore(thr_key, lambda v: isinstance(v, (int, float)))
        if thr_key not in ss:
            ss[thr_key] = float(rule.critical)
        unit = ch.judged.unit.strip()
        with cols[6]:
            thr = st.number_input(f"Threshold{f' ({unit})' if unit else ''}", key=thr_key,
                                  step=1.0 if abs(float(rule.critical)) >= 10 else 0.1,
                                  format="%g",
                                  help=f"Beyond it a cell counts as affected. The KPI's own "
                                       f"direction applies: "
                                       f"{'below' if rule.direction == 'up' else 'above'} "
                                       f"the threshold is bad. Configured critical: "
                                       f"{rule.critical:g}{ch.judged.unit}.")
        W.remember(thr_key, float(thr))

    tgt = R.target()
    key = (tuple((W.src_key(b), i.kind) for b, i in ws.files), W.ep_key(), bool(W.areas()),
           tgt.sha1 if tgt else "")
    r = _build(key, ws, ch, "" if gov == ALL_GOV else gov, tuple(sds),
               "" if site == ALL_SITES else site, period, float(thr))

    left, right = st.columns([1.62, 1], gap="medium")
    with left, st.container(key="rf_card_rx_preview", border=True):
        h1, h2 = st.columns([2.2, 1], gap="small", vertical_alignment="center")
        h1.html(title_html("Report Preview (PowerPoint Style)", "file",
                           subtitle="preview your report before exporting"))
        k = int(ss.get("rx_slide", 0)) % len(SLIDES)
        with h2, st.container(key="rx_nav", horizontal=True, gap="small",
                              vertical_alignment="center"):
            st.button(":material/chevron_left:", key="rx_prev", disabled=k == 0,
                      on_click=lambda: ss.update(rx_slide=max(0, k - 1)))
            st.html(f'<div style="font-size:13px;color:#E2E8F0;padding:0 6px">'
                    f"{k + 1} / {len(SLIDES)}</div>")
            st.button(":material/chevron_right:", key="rx_next",
                      disabled=k == len(SLIDES) - 1,
                      on_click=lambda: ss.update(rx_slide=min(len(SLIDES) - 1, k + 1)))
        st.html(slide_html(r, k))
        st.caption(f"Slide {k + 1}: {SLIDES[k]} · {r.total_cells:,} "
                   f"{'NodeBs' if r.tech == '3G' else 'cells'} judged on "
                   f"{r.threshold_text} and sudden spikes · the preview follows every filter")

    with right:
        what = "NodeBs" if r.tech == "3G" else "cells"
        with st.container(key="rf_card_rx_excel", border=True):
            st.html('<div class="rx-head">' + title_html(
                "Excel Preview", "file",
                subtitle="the Draw Data workbook: a row per cell, a column per hour")
                + f'<span class="rx-x-ico" style="background:#107C41;flex-basis:38px;'
                  f'height:38px;font-size:17px">X</span></div>')
            full = r.excel_preview()
            if full.empty:
                st.html(f'<div class="rx-info"><div>No affected {what} in the area and period: '
                        "the workbook would have no row.</div></div>")
            else:
                pages = max(1, -(-len(full) // PAGE_ROWS))
                p = min(max(int(ss.get("rx_page", 1)), 1), pages)
                ss["rx_page"] = p
                part = full.iloc[(p - 1) * PAGE_ROWS:p * PAGE_ROWS].reset_index(drop=True)
                part.index = part.index + 1 + (p - 1) * PAGE_ROWS
                st.dataframe(part, width="stretch", height=min(420, 38 + 35 * len(part)))
                n1, n2 = st.columns([1.3, 1], gap="small", vertical_alignment="center")
                with n1:
                    st.number_input(f"Page (of {pages})", 1, pages, key="rx_page", step=1)
                n2.html(f'<div style="text-align:right;font-size:13px;color:#E2E8F0">'
                        f"Sheet <b>{_esc(r.sheet)}</b> · Affected {what}: "
                        f"<b style='color:{BAD}'>{len(full):,}</b> · "
                        f"{sum(str(c)[:1].isdigit() for c in full.columns):,} hours</div>")
        _export_card("pptx", r, "Export PowerPoint", "Save the report to the Desktop",
                     "#D24726", "P")
        _export_card("xlsx", r, "Export Excel",
                     "Save the Excel file to the Desktop — the Draw Data workbook of the "
                     f"affected {what}", "#107C41", "X", disabled=not r.affected_cells)
