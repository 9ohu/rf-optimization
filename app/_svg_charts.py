"""Small SVG charts for the report preview: bars, horizontal bars and lines.

The preview draws each slide as one 16:9 block of HTML, so its charts are
images (an SVG data URI: `st.html` does not keep inline <svg>). They show the
same numbers the PowerPoint's native charts are built from.
"""

from __future__ import annotations

import base64
import html
import math

import pandas as pd

FONT = "Segoe UI, system-ui, sans-serif"
AXIS, GRID, LABEL, VALUE = "#2A4A6F", "#16324F", "#CBD5E1", "#F1F5F9"


def _img(svg: str, w: int, h: int, cls: str = "rx-chart") -> str:
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<img class="{cls}" src="data:image/svg+xml;base64,{b64}" alt="">'


def _esc(x) -> str:
    return html.escape(str(x))


def _fmt(v: float, dec: int = 0) -> str:
    if v is None or not math.isfinite(v):
        return "—"
    return f"{v:,.{dec}f}"


def _ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if not math.isfinite(lo) or not math.isfinite(hi):
        return [0.0]
    if hi == lo:
        hi, lo = hi + 1, lo - 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step) * step
    out, v = [], start
    while v <= hi + step * 0.001:
        out.append(round(v, 10))
        v += step
    return out


def _svg(w: int, h: int, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" font-family="{FONT}">{body}</svg>')


def bars(labels, values, colours, *, w: int = 420, h: int = 250, dec: int = 0) -> str:
    """Vertical bars with their values on top and the labels below."""
    labels, values = list(labels), [float(v) for v in values]
    if not values:
        return ""
    top = max(max(values), 0.0) or 1.0
    ticks = _ticks(0.0, top)
    ymax = max(ticks[-1], top)
    left, right, tpad, bottom = 34, 8, 16, 58
    pw, ph = w - left - right, h - tpad - bottom
    body = []
    for t in ticks:
        y = tpad + ph - ph * t / ymax
        body.append(f'<line x1="{left}" x2="{w - right}" y1="{y:.1f}" y2="{y:.1f}" '
                    f'stroke="{GRID}" stroke-width="1"/>'
                    f'<text x="{left - 5}" y="{y + 3.5:.1f}" font-size="10" fill="{LABEL}" '
                    f'text-anchor="end">{_fmt(t)}</text>')
    n = len(values)
    slot = pw / n
    bw = min(46.0, slot * 0.62)
    for k, (lab, v) in enumerate(zip(labels, values)):
        x = left + slot * k + (slot - bw) / 2
        bh = ph * max(v, 0) / ymax
        y = tpad + ph - bh
        c = colours[k] if isinstance(colours, (list, tuple)) else colours
        body.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" '
                    f'rx="2" fill="{c}"/>'
                    f'<text x="{x + bw / 2:.1f}" y="{y - 4:.1f}" font-size="11" '
                    f'font-weight="700" fill="{VALUE}" text-anchor="middle">{_fmt(v, dec)}</text>')
        text = _esc(lab if len(str(lab)) <= 16 else str(lab)[:15] + "…")
        cx, cy = x + bw / 2, tpad + ph + 13
        if n > 5:
            body.append(f'<text x="{cx:.1f}" y="{cy:.1f}" font-size="10" fill="{LABEL}" '
                        f'text-anchor="end" transform="rotate(-32 {cx:.1f} {cy:.1f})">{text}</text>')
        else:
            body.append(f'<text x="{cx:.1f}" y="{cy:.1f}" font-size="10.5" fill="{LABEL}" '
                        f'text-anchor="middle">{text}</text>')
    body.append(f'<line x1="{left}" x2="{w - right}" y1="{tpad + ph}" y2="{tpad + ph}" '
                f'stroke="{AXIS}"/>')
    return _img(_svg(w, h, "".join(body)), w, h)


def hbars(labels, values, colours, *, w: int = 420, h: int = 250, dec: int = 0,
          signed: bool = False) -> str:
    """Horizontal bars, the first at the top. `signed` values (dBm) are drawn
    from the axis minimum so the bar length still reads as the value."""
    labels, values = list(labels), [float(v) for v in values]
    if not values:
        return ""
    lo, hi = min(values), max(values)
    if signed and hi < 0:
        base = min(_ticks(lo * 1.02, hi)[0], lo - abs(lo) * 0.02)
        span = (hi - base) or 1.0
    else:
        base, span = 0.0, (max(hi, 0.0) or 1.0)
    left, right, tpad, bottom = 78, 44, 6, 6
    pw, ph = w - left - right, h - tpad - bottom
    n = len(values)
    slot = ph / n
    bh = min(20.0, slot * 0.66)
    body = []
    for k, (lab, v) in enumerate(zip(labels, values)):
        y = tpad + slot * k + (slot - bh) / 2
        bw = max(2.0, pw * (v - base) / span)
        c = colours[k] if isinstance(colours, (list, tuple)) else colours
        body.append(f'<text x="{left - 6}" y="{y + bh / 2 + 3.5:.1f}" font-size="10.5" '
                    f'fill="{LABEL}" text-anchor="end">{_esc(str(lab)[:14])}</text>'
                    f'<rect x="{left}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="2" '
                    f'fill="{c}"/>'
                    f'<text x="{left + bw + 4:.1f}" y="{y + bh / 2 + 3.5:.1f}" font-size="10.5" '
                    f'font-weight="700" fill="{VALUE}">{_fmt(v, dec)}</text>')
    body.append(f'<line x1="{left}" x2="{left}" y1="{tpad}" y2="{tpad + ph}" stroke="{AXIS}"/>')
    return _img(_svg(w, h, "".join(body)), w, h)


def time_lines(lines, colours, *, w: int = 1100, h: int = 400, fill: bool = False,
               per_row: int = 5) -> str:
    """Draw Data's KPI chart as a picture: one line per object (`lines`:
    [(name, hourly Series)]) on the real time axis, in Draw Data's colours,
    filled under the curves when every value is positive, the names in a legend
    under the plot and the time ticks Draw Data uses (6-hourly up to two days,
    daily up to nine)."""
    lines = [(str(n), s.dropna()) for n, s in lines if len(s.dropna())]
    if not lines:
        return ""
    t0 = min(s.index.min() for _, s in lines)
    t1 = max(s.index.max() for _, s in lines)
    span = max((t1 - t0).total_seconds(), 3600.0)
    vals = [float(v) for _, s in lines for v in s.to_numpy()]
    lo, hi = min(vals), max(vals)
    if fill:
        lo = min(lo, 0.0)
    ticks = _ticks(lo, hi)
    ymin, ymax = min(ticks[0], lo), max(ticks[-1], hi)
    step = ticks[1] - ticks[0] if len(ticks) > 1 else 1.0
    dec = 0 if step >= 1 else 1 if step >= 0.1 else 2
    n_rows = math.ceil(len(lines) / per_row)
    left, right, tpad = 66, 16, 10
    bottom = 44 + 19 * n_rows
    pw, ph = w - left - right, h - tpad - bottom

    def X(t) -> float:
        return left + pw * (t - t0).total_seconds() / span

    def Y(v: float) -> float:
        return tpad + ph - ph * (v - ymin) / ((ymax - ymin) or 1.0)

    body = []
    for t in ticks:
        body.append(f'<line x1="{left}" x2="{w - right}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" '
                    f'stroke="{GRID}"/><text x="{left - 6}" y="{Y(t) + 3.5:.1f}" '
                    f'font-size="10.5" fill="{LABEL}" text-anchor="end">{_fmt(t, dec)}</text>')
    hours = span / 3600
    every = 6 if hours <= 50 else 24 if hours <= 24 * 9 else 24 * math.ceil(hours / 24 / 9)
    tick = pd.Timestamp(t0).floor("D")
    while tick <= t1:
        if tick >= t0:
            x = X(tick)
            body.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{tpad + ph}" y2="{tpad + ph + 4}" '
                        f'stroke="{AXIS}"/><text x="{x:.1f}" y="{tpad + ph + 16}" '
                        f'font-size="10" fill="{LABEL}" text-anchor="middle">'
                        f'{tick:%b %d}</text>')
            if every < 24:
                body.append(f'<text x="{x:.1f}" y="{tpad + ph + 28}" font-size="10" '
                            f'fill="{LABEL}" text-anchor="middle">{tick:%H:%M}</text>')
        tick += pd.Timedelta(hours=every)
    base = Y(0.0)
    for k, (name, s) in enumerate(lines):
        c = colours[k % len(colours)]
        pts = [(X(t), Y(float(v))) for t, v in s.items()]
        path = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        if fill:
            body.append(f'<polygon points="{path} {pts[-1][0]:.1f},{base:.1f} '
                        f'{pts[0][0]:.1f},{base:.1f}" fill="{c}" fill-opacity="0.18" '
                        f'stroke="none"/>')
        body.append(f'<polyline points="{path}" fill="none" stroke="{c}" stroke-width="1.6" '
                    f'stroke-linejoin="round"/>')
    body.append(f'<line x1="{left}" x2="{w - right}" y1="{tpad + ph}" y2="{tpad + ph}" '
                f'stroke="{AXIS}"/>')
    slot = pw / per_row
    for k, (name, _) in enumerate(lines):
        row, col = divmod(k, per_row)
        lx, ly = left + col * slot, h - 19 * n_rows + 19 * row + 6
        c = colours[k % len(colours)]
        body.append(f'<line x1="{lx:.1f}" x2="{lx + 20:.1f}" y1="{ly:.1f}" y2="{ly:.1f}" '
                    f'stroke="{c}" stroke-width="2.4"/><text x="{lx + 25:.1f}" '
                    f'y="{ly + 4:.1f}" font-size="11" fill="{LABEL}">{_esc(name)}</text>')
    return _img(_svg(w, h, "".join(body)), w, h)


def line(xlabels, series, *, w: int = 860, h: int = 300, dec: int = 1) -> str:
    """series: [(name, values, colour, dashed)] on one time axis, a legend on top."""
    xlabels = list(xlabels)
    vals = [v for _, vs, _, _ in series for v in vs if v is not None and math.isfinite(v)]
    if not xlabels or not vals:
        return ""
    ticks = _ticks(min(vals), max(vals))
    ymin, ymax = min(ticks[0], min(vals)), max(ticks[-1], max(vals))
    left, right, tpad, bottom = 52, 14, 30, 44
    pw, ph = w - left - right, h - tpad - bottom
    n = len(xlabels)

    def X(i: int) -> float:
        return left + (pw * i / (n - 1) if n > 1 else pw / 2)

    def Y(v: float) -> float:
        return tpad + ph - ph * (v - ymin) / ((ymax - ymin) or 1.0)

    body = []
    for t in ticks:
        body.append(f'<line x1="{left}" x2="{w - right}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" '
                    f'stroke="{GRID}"/><text x="{left - 6}" y="{Y(t) + 3.5:.1f}" '
                    f'font-size="10.5" fill="{LABEL}" text-anchor="end">{_fmt(t, 0)}</text>')
    step = max(1, math.ceil(n / 9))
    for i in range(0, n, step):
        body.append(f'<text x="{X(i):.1f}" y="{tpad + ph + 16}" font-size="10" fill="{LABEL}" '
                    f'text-anchor="middle">{_esc(xlabels[i])}</text>')
    lx = left
    for name, vs, colour, dashed in series:
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vs)
                       if v is not None and math.isfinite(v))
        dash = ' stroke-dasharray="7 5"' if dashed else ""
        body.append(f'<polyline points="{pts}" fill="none" stroke="{colour}" '
                    f'stroke-width="{1.8 if dashed else 2.4}"{dash}/>')
        if not dashed and n <= 60:
            body.append("".join(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="2.6" '
                                f'fill="{colour}"/>' for i, v in enumerate(vs)
                                if v is not None and math.isfinite(v)))
        body.append(f'<line x1="{lx}" x2="{lx + 22}" y1="12" y2="12" stroke="{colour}" '
                    f'stroke-width="2.4"{dash}/><text x="{lx + 27}" y="16" font-size="11" '
                    f'fill="{LABEL}">{_esc(name)}</text>')
        lx += 36 + 7 * len(name)
    body.append(f'<line x1="{left}" x2="{w - right}" y1="{tpad + ph}" y2="{tpad + ph}" '
                f'stroke="{AXIS}"/>')
    return _img(_svg(w, h, "".join(body)), w, h)
