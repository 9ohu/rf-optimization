"""History of Tickets — what the page draws, kept out of the page.

The filters, the counting and the pictures of the dashboard: the ticket history
(`rfopt.complaints.history`, the History ticket resource) filtered by City, Sup
District, User, Status and Group (any number of values each) and by the days
the tickets were created in; every count is of HPSM Incident IDs; the bars and
donuts are SVG pictures (an SVG data URI: `st.html` does not keep inline <svg>)
in the app's dark style.
"""

from __future__ import annotations

import base64
import html
import math

import pandas as pd

from rfopt.complaints.history import CLOSED, IN_PROGRESS, PENDING, SLEEP

ALL = "All"
FILTERS = (("city", "City"), ("sup_district", "Sup District"), ("user", "User"),
           ("status", "Status"), ("group", "Group"))
FONT = "Segoe UI, system-ui, sans-serif"
GRID, AXIS, LABEL, VALUE = "#16324F", "#2A4A6F", "#CBD5E1", "#F1F5F9"
STATE_COLOUR = {CLOSED: "#22C55E", PENDING: "#EF4444", IN_PROGRESS: "#1597FF",
                SLEEP: "#A78BFA"}
STATUS_COLOUR = {"close": "#22C55E", "closed": "#22C55E", "sleep": "#A78BFA",
                 "resolve": "#1597FF", "pending": "#EF4444", "reopen": "#F59E0B",
                 "reject": "#F472B6"}
SERIES = ["#1597FF", "#22C55E", "#F59E0B", "#A78BFA", "#2DD4BF", "#F472B6", "#FB923C",
          "#FACC15"]
NONE_COLOUR, OTHERS_COLOUR = "#64748B", "#94A3B8"
EMPTY = {"user": "No user", "group": "No group", "sup_district": "No Sup District",
         "city": "No city", "status": "No status", "site_id": "No site",
         "rf_analysis": "No RF Analysis"}

CSS = """
<style>
.st-key-rf_card_th_filters { border: 1px solid #1597FF !important;
    box-shadow: 0 0 0 1px rgba(21, 151, 255, .25), 0 0 18px rgba(21, 151, 255, .16); }
.st-key-rf_card_th_filters [data-testid="stFormSubmitButton"] button { min-height: 40px; }
.th-lbl { font-size: 12.25px; line-height: 19.6px; min-height: 21px; margin-bottom: 3.5px;
    color: #E2E8F0; }
.st-key-th_period_box button { min-height: 35px; height: 35px; justify-content: space-between;
    gap: 6px; padding: 0 10px; background: #0D2945; border-color: #1E3A5F;
    border-radius: 10px; font-weight: 400; }
.st-key-th_period_box button p { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.th-kpis { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; }
.th-kpi { display: flex; align-items: center; gap: 12px; background: #0B1F33;
    border: 1px solid #1E3A5F; border-radius: 12px; padding: 14px 12px; min-width: 0; }
.th-kpi > div:last-child { min-width: 0; }
.th-kpi-ico { flex: 0 0 54px; height: 54px; border-radius: 50%; display: flex; align-items: center;
    justify-content: center; border: 4px solid var(--c);
    background: color-mix(in srgb, var(--c) 16%, #071525);
    box-shadow: 0 0 18px color-mix(in srgb, var(--c) 45%, transparent),
                inset 0 0 12px color-mix(in srgb, var(--c) 25%, transparent); }
.th-kpi-t { font-size: 14px; color: #E2E8F0; white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }
.th-kpi-v { font-size: 26px; font-weight: 800; color: #F8FAFC; line-height: 1.2;
    font-variant-numeric: tabular-nums; }
.th-kpi-p { font-size: 14px; color: #CBD5E1; font-variant-numeric: tabular-nums; }
@media (max-width: 1250px) { .th-kpis { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 760px) { .th-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.th-h { font-size: 15.5px; font-weight: 700; color: #F1F5F9; line-height: 1.25; }
.st-key-rf_card_th_users, .st-key-rf_card_th_status, .st-key-rf_card_th_group {
    min-height: 318px; }
.st-key-rf_card_th_city, .st-key-rf_card_th_sd { min-height: 292px; }
.th-chart { width: 100%; height: auto; display: block; }
.th-none { color: #94A3B8; font-size: 13px; padding: 40px 0; text-align: center; }
.th-note { color: #64748B; font-size: 11px; margin-top: 4px; }
.th-dn { display: flex; align-items: center; gap: 12px; margin-top: 6px; }
.th-dn img { flex: 0 0 43%; max-width: 200px; min-width: 0; }
.th-leg { flex: 1 1 auto; display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.th-leg div { display: grid; grid-template-columns: 11px minmax(0, 1fr) auto 44px; gap: 7px;
    align-items: center; font-size: 12.5px; color: #E2E8F0; }
.th-leg i { width: 11px; height: 11px; border-radius: 50%; background: var(--c);
    box-shadow: 0 0 6px var(--c); }
.th-leg span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.th-leg b { font-weight: 500; text-align: right; font-variant-numeric: tabular-nums; }
.th-leg small { color: #E2E8F0; text-align: right; font-size: 12.5px;
    font-variant-numeric: tabular-nums; }
.th-sub { color: #94A3B8; font-size: 11.5px; margin-top: 10px; }
[class*="st-key-th_va_"] button { min-height: 30px; padding: 2px 12px; font-size: 13px;
    background: transparent; border: 1px solid #2A4A6F; }
[class*="st-key-th_mode_"] [data-testid="stButtonGroup"] { justify-content: flex-end; }
[class*="st-key-th_mode_"] button { min-height: 28px; padding: 0 9px; font-size: 12px; }
[class*="st-key-th_mode_"] button p { font-size: 12px; }
.th-foot { display: flex; justify-content: space-between; align-items: center; gap: 10px;
    color: #94A3B8; font-size: 12.5px; padding: 6px 4px 2px; }
.th-foot b { color: #CBD5E1; font-weight: 500; }
.th-foot img { height: 20px; vertical-align: middle; margin-left: 8px; }
</style>
"""


# --------------------------------------------------------------------------- #
# the data
# --------------------------------------------------------------------------- #
def options(df: pd.DataFrame, field: str) -> list:
    """The values a filter offers: the file's own, busiest first; an empty one
    under its "No …" name. None picked is all of them."""
    s = df[field].replace("", EMPTY[field])
    return list(s.value_counts().index)


def apply_filters(df: pd.DataFrame, chosen: dict) -> pd.DataFrame:
    """The tickets with one of the values picked in every filter (none picked:
    any value), created in the period's days (none: any day)."""
    out = df
    for field, _ in FILTERS:
        v = chosen.get(field) or []
        if isinstance(v, str):
            v = [] if v == ALL else [v]
        if v:
            out = out[out[field].replace("", EMPTY[field]).isin(list(v))]
    period = chosen.get("period")
    if period:
        lo, hi = pd.Timestamp(period[0]), pd.Timestamp(period[-1]) + pd.Timedelta(days=1)
        out = out[out["create_time"].ge(lo) & out["create_time"].lt(hi)]
    return out


def group_title(groups) -> str:
    """The groups the Status card is of: all, or the ones picked."""
    groups = list(groups or [])
    if not groups:
        return "All Groups"
    return ", ".join(groups) + (" Group" if len(groups) == 1 else " Groups")


def count(df: pd.DataFrame) -> int:
    """Tickets are counted by their HPSM Incident ID."""
    return int(df["hpsm_id"].nunique())


def by(df: pd.DataFrame, field: str, *, keep_empty: bool = False,
       drop: tuple = ()) -> pd.Series:
    """Tickets (HPSM Incident IDs) per value of `field`, most first."""
    d = df[~df[field].isin(drop)]
    if not keep_empty:
        d = d[d[field].ne("")]
    s = (d.assign(_k=d[field].replace("", EMPTY[field])).groupby("_k")["hpsm_id"].nunique())
    return s.sort_values(ascending=False, kind="stable")


def top_with_others(s: pd.Series, n: int) -> pd.Series:
    """The first `n`, and the rest added up as Others."""
    if len(s) <= n + 1:
        return s
    out = s.head(n).copy()
    out.loc["Others"] = int(s.iloc[n:].sum())
    return out


def states(df: pd.DataFrame) -> dict:
    return {st: int(df.loc[df["state"].eq(st), "hpsm_id"].nunique())
            for st in (CLOSED, PENDING, IN_PROGRESS, SLEEP)}


def pct(k: float, of: float) -> float:
    return 100.0 * k / of if of else 0.0


# --------------------------------------------------------------------------- #
# the pictures
# --------------------------------------------------------------------------- #
def _esc(x) -> str:
    return html.escape(str(x))


def _img(svg: str, w: int, h: int) -> str:
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return (f'<img class="th-chart" src="data:image/svg+xml;base64,{b64}" '
            f'style="aspect-ratio:{w}/{h}" alt="">')


def _svg(w: int, h: int, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" font-family="{FONT}"><defs>'
            '<linearGradient id="hb" x1="0" x2="1" y1="0" y2="0">'
            '<stop offset="0" stop-color="#0F5FD0"/><stop offset="1" stop-color="#1B9BFF"/>'
            '</linearGradient><linearGradient id="vb" x1="0" x2="0" y1="0" y2="1">'
            '<stop offset="0" stop-color="#1B9BFF"/><stop offset="1" stop-color="#0F5FD0"/>'
            f'</linearGradient></defs>{body}</svg>')


def nice_ticks(top: float, n: int = 4) -> list[float]:
    """0 … a round number above `top`, in n or so steps."""
    if not top or not math.isfinite(top) or top <= 0:
        return [0.0, 1.0]
    raw = top / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    out, v = [0.0], 0.0
    while v < top - 1e-9:
        v += step
        out.append(round(v, 10))
    return out


def _num(v: float, as_pct: bool) -> str:
    return f"{v:.1f}%" if as_pct else f"{v:,.0f}"


def _tick(v: float, as_pct: bool) -> str:
    return f"{v:g}%" if as_pct else f"{v:,.0f}"


def hbars(labels, values, *, w: int = 330, row: int = 27, as_pct: bool = False,
          label_w: int | None = None) -> str:
    """Horizontal bars: the name on the left, the value at the end of its bar,
    the scale under them."""
    labels, values = [str(x) for x in labels], [float(v) for v in values]
    if not values:
        return '<div class="th-none">No ticket for these filters.</div>'
    ticks = nice_ticks(max(values))
    top = ticks[-1] or 1.0
    shown = [x if len(x) <= 20 else x[:19] + "…" for x in labels]
    lw = label_w or int(min(150, max(58, 14 + 6.8 * max(len(x) for x in shown))))
    right, pad, axis = 50, 6, 24
    h = pad + row * len(values) + axis
    pw = w - lw - right
    body = []
    for t in ticks:
        x = lw + pw * t / top
        body.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{pad}" y2="{h - axis}" '
                    f'stroke="{GRID}"/><text x="{x:.1f}" y="{h - 7}" font-size="11" '
                    f'fill="{LABEL}" text-anchor="middle">{_esc(_tick(t, as_pct))}</text>')
    bh = row * 0.64
    for k, (lab, v, text) in enumerate(zip(labels, values, shown)):
        y = pad + row * k + (row - bh) / 2
        bw = max(1.5, pw * v / top) if v > 0 else 0
        body.append(f'<text x="{lw - 7}" y="{y + bh / 2 + 4.3:.1f}" font-size="12" '
                    f'fill="#E2E8F0" text-anchor="end"><title>{_esc(lab)}</title>'
                    f'{_esc(text)}</text>'
                    f'<rect x="{lw}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="2" '
                    f'fill="url(#hb)"/>'
                    f'<text x="{lw + bw + 5:.1f}" y="{y + bh / 2 + 4.3:.1f}" font-size="12" '
                    f'fill="{VALUE}">{_esc(_num(v, as_pct))}</text>')
    body.append(f'<line x1="{lw}" x2="{lw}" y1="{pad}" y2="{h - axis}" stroke="{AXIS}"/>')
    return _img(_svg(w, h, "".join(body)), w, h)


def vbars(labels, values, *, w: int = 330, h: int = 224, as_pct: bool = False,
          tilt: int = 0) -> str:
    """Vertical bars: the value on top of each, the name under it, the scale left.
    `tilt`: the names slanted by that many degrees, for long ones."""
    labels, values = [str(x) for x in labels], [float(v) for v in values]
    if not values:
        return '<div class="th-none">No ticket for these filters.</div>'
    ticks = nice_ticks(max(values))
    top = ticks[-1] or 1.0
    left, right, tpad, bottom = 46, 8, 22, 28
    names = [x if len(x) <= 12 else x[:11] + "…" for x in labels]
    if tilt:
        a = math.radians(tilt)
        names = [x if len(x) <= 26 else x[:25] + "…" for x in labels]
        run = [6.3 * len(x) for x in names]              # a name's length at 12px, roughly
        bottom = int(max(run) * math.sin(a)) + 24
        # the first name hangs left of its bar: room for it
        left = max(left, int(run[0] * math.cos(a) - (w - left - right) / len(values) / 2) + 12)
    pw, ph = w - left - right, h - tpad - bottom
    body = []
    for t in ticks:
        y = tpad + ph - ph * t / top
        body.append(f'<line x1="{left}" x2="{w - right}" y1="{y:.1f}" y2="{y:.1f}" '
                    f'stroke="{GRID}"/><text x="{left - 7}" y="{y + 4:.1f}" font-size="11" '
                    f'fill="{LABEL}" text-anchor="end">{_esc(_tick(t, as_pct))}</text>')
    slot = pw / len(values)
    bw = min(66.0, slot * 0.74)
    for k, (lab, v, name) in enumerate(zip(labels, values, names)):
        x = left + slot * k + (slot - bw) / 2
        bh = ph * v / top
        y = tpad + ph - bh
        cx = x + bw / 2
        body.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="2" '
                    f'fill="url(#vb)"/><text x="{cx:.1f}" y="{y - 6:.1f}" '
                    f'font-size="12" fill="{VALUE}" text-anchor="middle">'
                    f'{_esc(_num(v, as_pct))}</text>')
        if tilt:
            ly = tpad + ph + 14
            body.append(f'<text x="{cx:.1f}" y="{ly:.1f}" font-size="12" fill="#E2E8F0" '
                        f'text-anchor="end" transform="rotate(-{tilt} {cx:.1f} {ly:.1f})">'
                        f'<title>{_esc(lab)}</title>{_esc(name)}</text>')
        else:
            body.append(f'<text x="{cx:.1f}" y="{h - 8}" font-size="12" fill="#E2E8F0" '
                        f'text-anchor="middle">{_esc(name)}</text>')
    body.append(f'<line x1="{left}" x2="{w - right}" y1="{tpad + ph}" y2="{tpad + ph}" '
                f'stroke="{AXIS}"/>')
    return _img(_svg(w, h, "".join(body)), w, h)


def _arc(cx, cy, r0, r1, a0, a1) -> str:
    """A ring slice from angle a0 to a1 (radians, clockwise from 12 o'clock)."""
    large = 1 if a1 - a0 > math.pi else 0

    def p(r, a):
        return cx + r * math.sin(a), cy - r * math.cos(a)

    (x0, y0), (x1, y1) = p(r1, a0), p(r1, a1)
    (x2, y2), (x3, y3) = p(r0, a1), p(r0, a0)
    return (f"M{x0:.2f},{y0:.2f} A{r1},{r1} 0 {large} 1 {x1:.2f},{y1:.2f} "
            f"L{x2:.2f},{y2:.2f} A{r0},{r0} 0 {large} 0 {x3:.2f},{y3:.2f} Z")


def donut(parts, total: int, *, size: int = 150, thick: int = 30,
          centre: str = "Tickets") -> str:
    """parts: (label, count, colour). The ring with each share written on it,
    the total in the middle."""
    cx = cy = size / 2
    r1 = size / 2 - 3
    r0 = r1 - thick
    body = []
    if not total:
        body.append(f'<circle cx="{cx}" cy="{cy}" r="{(r0 + r1) / 2}" fill="none" '
                    f'stroke="{NONE_COLOUR}" stroke-width="{thick}"/>')
    a = 0.0
    live = [(lab, k, c) for lab, k, c in parts if k > 0]
    for lab, k, c in live:
        share = k / total
        a1 = a + share * 2 * math.pi
        if len(live) == 1:
            body.append(f'<circle cx="{cx}" cy="{cy}" r="{(r0 + r1) / 2}" fill="none" '
                        f'stroke="{c}" stroke-width="{thick}"/>')
        else:
            body.append(f'<path d="{_arc(cx, cy, r0, r1, a, a1)}" fill="{c}" '
                        f'stroke="#0B1F33" stroke-width="1.5"/>')
        if share >= 0.04:
            mid, rm = (a + a1) / 2, (r0 + r1) / 2
            body.append(f'<text x="{cx + rm * math.sin(mid):.1f}" '
                        f'y="{cy - rm * math.cos(mid) + 3.5:.1f}" font-size="9.5" '
                        f'font-weight="600" fill="#FFFFFF" text-anchor="middle">'
                        f'{share * 100:.1f}%</text>')
        a = a1
    body.append(f'<circle cx="{cx}" cy="{cy}" r="{r0 - 3}" fill="#071525"/>'
                f'<text x="{cx}" y="{cy + 2}" font-size="19" font-weight="700" fill="#F8FAFC" '
                f'text-anchor="middle">{total:,}</text>'
                f'<text x="{cx}" y="{cy + 19}" font-size="11.5" fill="#E2E8F0" '
                f'text-anchor="middle">{_esc(centre)}</text>')
    return _img(_svg(size, size, "".join(body)), size, size)


def legend(parts, total: int) -> str:
    return '<div class="th-leg">' + "".join(
        f'<div style="--c:{c}" title="{_esc(lab)}: {k:,} tickets"><i></i><span>{_esc(lab)}</span>'
        f'<b>{k:,}</b><small>{pct(k, total):.1f}%</small></div>' for lab, k, c in parts) + "</div>"


def kpi_cards(cards) -> str:
    """cards: (title, icon svg, colour, count, share)."""
    from _ui import icon_img
    return '<div class="th-kpis">' + "".join(
        f'<div class="th-kpi" style="--c:{c}"><div class="th-kpi-ico">{icon_img(ic, c, 30)}</div>'
        f'<div><div class="th-kpi-t">{_esc(t)}</div><div class="th-kpi-v">{k:,}</div>'
        f'<div class="th-kpi-p">{p:.1f}%</div></div></div>'
        for t, ic, c, k, p in cards) + "</div>"


def status_colour(status: str, k: int) -> str:
    return STATUS_COLOUR.get(str(status).strip().lower(), SERIES[k % len(SERIES)])


def group_colours(labels) -> list:
    out, k = [], 0
    for lab in labels:
        if lab == EMPTY["group"]:
            out.append(NONE_COLOUR)
        elif lab == "Others":
            out.append(OTHERS_COLOUR)
        else:
            out.append(SERIES[k % len(SERIES)])
            k += 1
    return out
