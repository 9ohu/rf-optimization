"""Colouring the map's sectors by a KPI, and saying what the colours mean.

Kept out of the page so the bands can be tested and the map rendered without
running Streamlit — a legend that lies is worse than no legend.
"""

from __future__ import annotations

import html
import json
import math
from collections import Counter
from typing import NamedTuple

import numpy as np
import pandas as pd
from branca.element import MacroElement
from jinja2 import Template

# Green / amber / red are the operator's own thresholds, so a colour means the
# same thing here as in the Dashboard and the report; grey is a sector the KPI
# file does not cover (a 2G-only sector, say).
KPI_BAND = {"ok": "#22C55E", "warning": "#FACC15", "critical": "#EF4444",
            "none": "#94A3B8"}
BAND_LABEL = {"ok": "OK", "warning": "Warning", "critical": "Critical",
              "none": "No data"}

LEGEND_CSS = """
.sm-legend {
    background: rgba(11, 31, 51, .96); border-radius: 10px; border: 1px solid #1E3A5F;
    box-shadow: 0 4px 16px rgba(0, 0, 0, .45); padding: 9px 11px 8px;
    font: 12px/1.5 'Segoe UI', system-ui, -apple-system, sans-serif;
    color: #E2E8F0; min-width: 230px;
}
.sm-legend .sm-lg-t {
    font-weight: 700; margin: 0 0 6px; padding-bottom: 5px; color: #F1F5F9;
    border-bottom: 1px solid #1E3A5F; white-space: nowrap;
}
.sm-legend .sm-lg-row {
    display: flex; align-items: center; gap: 8px; margin: 2px 0;
    white-space: nowrap;
}
.sm-legend .sm-lg-chip {
    border-radius: 3px; flex: 0 0 14px; border: 1px solid rgba(255, 255, 255, .18);
}
.sm-legend .sm-lg-r { font-variant-numeric: tabular-nums; min-width: 96px; }
.sm-legend .sm-lg-n {
    font-variant-numeric: tabular-nums; color: #94A3B8; flex: 1 1 auto;
}
.sm-legend .sm-lg-b { color: #94A3B8; font-size: 11px; font-weight: 600; }
.sm-legend .sm-lg-note {
    margin-top: 6px; padding-top: 5px; border-top: 1px solid #1E3A5F;
    color: #94A3B8; font-size: 11px; white-space: nowrap;
}
"""


def canonical_name(kpi: str) -> str | None:
    """The operator's own column name -> the schema's, so the YAML thresholds
    can be found for it. `LTE_Availability(%)@AB` is `cell_avail_pct`."""
    from rfopt.ingest.hourly_kpi import _KPI_MAP, _KPI_MAP_3G, _norm

    n = _norm(kpi)
    return _KPI_MAP.get(n) or _KPI_MAP_3G.get(n)


class KpiChoice(NamedTuple):
    file_id: str
    kind: str
    kpi: str
    label: str


_KIND_ORDER = {"4G": 0, "3G": 1, "2G": 2, "Other": 3}


def kpi_choices(headers, technology: str) -> dict[str, KpiChoice]:
    """Every KPI the uploaded exports offer for the map's technology.

    `headers` is (file_id, file_name, kind, kpis) per uploaded file. All takes
    every file; 4G / 3G / 2G take that technology's files only. Under All a
    KPI carries its technology in its label, because the exports share names
    that mean different things ("Integrity" is a 4G cell's and a 3G NodeB's);
    a second file of the same technology adds its file name.
    """
    files = sorted((h for h in headers
                    if technology == "All" or h[2] == technology),
                   key=lambda h: _KIND_ORDER.get(h[2], 9))
    many_kinds = technology == "All" and len({h[2] for h in files}) > 1
    per_kind = Counter(h[2] for h in files)
    out: dict[str, KpiChoice] = {}
    for fid, name, kind, kpis in files:
        for k in kpis:
            label = f"{k} · {kind}" if many_kinds else k
            if per_kind[kind] > 1:
                label += f" · {name}"
            out[f"{fid}::{k}"] = KpiChoice(fid, kind, k, label)
    return out


# magnitude ramp for a KPI nobody has set a threshold on: pale to deep, which
# reads as "more", not as "worse"
RAMP = ["#ffeda0", "#fed976", "#feb24c", "#fd8d3c", "#f03b20", "#bd0026"]


def _nice_breaks(v: pd.Series, want: int = 6) -> list[float]:
    """Round break points across the data, the way a map legend has them —
    0 / 10 / 50 / 100 / 500 / 1000, not 12.7 / 48.3 / 91.6."""
    lo, hi = float(v.min()), float(v.max())
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return []
    steps = [1, 2, 5]
    ticks: list[float] = []
    exp = math.floor(math.log10(max(abs(hi), 1e-9))) - 2
    while len(ticks) < 40 and exp < 12:
        for s in steps:
            t = s * 10 ** exp
            if lo < t < hi:
                ticks.append(t)
        exp += 1
    if not ticks:
        return []
    # thin to `want` cuts, keeping the spread
    step = max(1, round(len(ticks) / (want - 1)))
    cuts = sorted({round(t, 6) for t in ticks[::step]})[:want - 1]
    return cuts


def _fmt(x: float) -> str:
    if x is None or not np.isfinite(x):      # a KPI no drawn sector carries
        return "–"
    if x == int(x) and abs(x) < 1e6:
        return f"{int(x):,}"
    return f"{x:,.2f}".rstrip("0").rstrip(".")


def sector_values(sectors: pd.DataFrame, per_sector: pd.Series,
                  per_site: pd.Series) -> pd.Series:
    """The KPI value each drawn sector shows, indexed by `sector_id`.

    Its own value where the export names that sector; otherwise its site's,
    which is how a per-NodeB 3G export — no sector anywhere in it — still
    colours the beams instead of leaving the whole map grey. A sector with
    neither stays NaN and reads as no data.
    """
    own = sectors["sector_id"].map(per_sector)
    site = sectors["site_id"].map(per_site)
    return pd.Series(own.fillna(site).to_numpy(dtype=float),
                     index=sectors["sector_id"].to_numpy())


# the healthy side in shades, best first: darkest green is furthest from the
# warning line
OK_SHADES = ["#15803D", "#22C55E", "#86EFAC"]


def _graded_ok(v: pd.Series, ok_m: pd.Series, warn: float,
               up: bool) -> tuple[list[tuple], list[tuple]]:
    """Split the OK side into up to three shades of green.

    Most sectors are OK on a normal day, and one flat green hides the spread an
    engineer cares about: PRB at 20% and at 65% are both OK but not the same
    sector. Warning and critical keep their single colours.

    Returns the legend rows, and (key, a, b, last) per shade from low to high.
    """
    ok_v = v[ok_m].dropna()
    if ok_v.empty:
        return ([("ok", KPI_BAND["ok"],
                  f"[{_fmt(warn)}, +∞)" if up else f"(-∞, {_fmt(warn)}]")],
                [("ok", -math.inf, math.inf, True)])
    start, end = (warn, float(ok_v.max())) if up else (float(ok_v.min()), warn)
    # thirds of the healthy sectors actually on the map, rounded to a readable
    # step. Round-number ticks (the magnitude ramp's rule) left 87% of PRB in
    # one shade, and cannot cut a negative range like UL interference at all.
    span = end - start
    step = 10 ** math.floor(math.log10(span / 10)) if span > 0 else 1
    cuts = sorted({round(float(ok_v.quantile(q)) / step) * step
                   for q in (1 / 3, 2 / 3)})
    cuts = [c for c in cuts if start < c < end]
    bounds = [start] + cuts + [end]
    n = len(bounds) - 1
    parts = []
    for i in range(n):
        a, b = bounds[i], bounds[i + 1]
        last = i == n - 1
        parts.append((a, b, last, f"[{_fmt(a)}, {_fmt(b)}{']' if last else ')'}"))
    # the best end reads first: the top shade for an "up" KPI, the bottom one
    # for a "down" KPI
    order = list(reversed(range(n))) if up else list(range(n))
    spec, keyed = [], [None] * n
    for rank, i in enumerate(order):
        key = "ok" if n == 1 else f"ok{rank}"
        colour = (KPI_BAND["ok"] if n == 1 else
                  OK_SHADES[round(rank * (len(OK_SHADES) - 1) / (n - 1))])
        a, b, last, text = parts[i]
        spec.append((key, colour, text))
        keyed[i] = (key, a, b, last)
    return spec, keyed


def threshold_rule(kpi: str):
    """The operator's threshold for a KPI, or None when it is not judged.

    4G's thresholds first, then 3G's. Only a KPI averaged over the window is
    judged on them: a counter totalled across several days (a drop count,
    say) would otherwise be read against a line drawn for one day.
    """
    from rfopt.kpi.thresholds import load_thresholds
    from rfopt.kpi.trends import agg_how

    if agg_how(kpi) != "mean":
        return None
    canon = canonical_name(kpi) or kpi
    rule = (load_thresholds("LTE").rule(canon)
            or load_thresholds("UMTS").rule(canon))
    if rule is None or rule.warning is None or rule.critical is None:
        return None
    return rule


def band_sectors(values: pd.Series, kpi: str) -> tuple[pd.Series, list[tuple]]:
    """Put each sector in a band, and describe the bands for the legend.

    A KPI with thresholds is banded on them, so green / amber / red mean what
    they mean everywhere else in the tool. One without (traffic, users) is cut
    at round numbers across its real range and shaded by magnitude — no good
    or bad implied, because none is defined.

    Returns the band per sector and `(key, colour, interval)` per band.
    """
    scheme = band_scheme(values, kpi)
    return apply_scheme(values, scheme), scheme.spec


class BandScheme(NamedTuple):
    """How values become bands, decided once on the reference values (the
    window), so any other values — one hour of them — land in the same bands.
    A value past the reference range falls in the open outer band."""
    spec: list          # (key, colour, interval) per band: the legend
    rule: object        # the threshold rule, or None for a magnitude ramp
    ok_parts: list      # (key, a, b, last) per healthy shade, low to high
    edges: list         # the magnitude ramp's left edges


def apply_scheme(values: pd.Series, scheme: BandScheme) -> pd.Series:
    """The band key of every value under a scheme."""
    v = pd.to_numeric(values, errors="coerce")
    band = pd.Series("none", index=v.index, dtype=object)
    rule = scheme.rule
    if rule is not None:
        crit, warn = float(rule.critical), float(rule.warning)
        up = rule.direction == "up"
        if up:
            crit_m, warn_m, ok_m = v < crit, (v >= crit) & (v < warn), v >= warn
        else:
            crit_m, warn_m, ok_m = v > crit, (v > warn) & (v <= crit), v <= warn
        band[crit_m] = "critical"
        band[warn_m] = "warning"
        for j, (key, a, b, last) in enumerate(scheme.ok_parts):
            a = -math.inf if (j == 0 and not up) else a       # best end is open
            b = math.inf if (last and up) else b
            band[ok_m & (v >= a) & ((v <= b) if last else (v < b))] = key
        return band
    edges = scheme.edges
    for i, left in enumerate(edges):
        right = edges[i + 1] if i + 1 < len(edges) else None
        sel = (((v >= left) if i else v.notna())
               & ((v < right) if right is not None else v.notna()))
        band = band.mask(sel, f"b{i}")
    return band.where(v.notna(), "none")


def scheme_segments(scheme: BandScheme, lo: float, hi: float) -> list[tuple]:
    """(a, b, colour) across [lo, hi], low to high — the range bar under a value."""
    if not (np.isfinite(lo) and np.isfinite(hi)) or hi <= lo:
        return []
    colour = {k: c for k, c, _ in scheme.spec}
    rule = scheme.rule
    if rule is not None:
        crit, warn = float(rule.critical), float(rule.warning)
        up = rule.direction == "up"
        segs = ([(-math.inf, crit, colour["critical"]),
                 (crit, warn, colour["warning"])] if up else
                [(warn, crit, colour["warning"]),
                 (crit, math.inf, colour["critical"])])
        for j, (key, a, b, last) in enumerate(scheme.ok_parts):
            a = -math.inf if (j == 0 and not up) else a
            b = math.inf if (last and up) else b
            a, b = (max(a, warn), b) if up else (a, min(b, warn))
            segs.append((a, b, colour[key]))
    else:
        e = scheme.edges
        segs = [(-math.inf if i == 0 else left,
                 e[i + 1] if i + 1 < len(e) else math.inf, colour[f"b{i}"])
                for i, left in enumerate(e)]
    return sorted((max(a, lo), min(b, hi), c) for a, b, c in segs
                  if min(b, hi) > max(a, lo))


def band_scheme(values: pd.Series, kpi: str) -> BandScheme:
    """Decide the bands on these values: thresholds (with graded greens), or
    a round-number magnitude ramp when the KPI has none."""
    v = pd.to_numeric(values, errors="coerce")
    rule = threshold_rule(kpi)
    if rule is not None:
        crit, warn = float(rule.critical), float(rule.warning)
        up = rule.direction == "up"
        lo, hi = float(v.min()), float(v.max())
        spec, parts = _graded_ok(v, (v >= warn) if up else (v <= warn), warn, up)
        # with no sector past the critical line, the data's own extreme sits on
        # the wrong side of it and the interval ran backwards ("(85, 78.07]"),
        # so an empty critical band is shown open-ended instead
        if up:
            spec += [("warning", KPI_BAND["warning"],
                      f"[{_fmt(crit)}, {_fmt(warn)})"),
                     ("critical", KPI_BAND["critical"],
                      f"[{_fmt(lo)}, {_fmt(crit)})" if lo < crit
                      else f"(-∞, {_fmt(crit)})")]
        else:
            spec += [("warning", KPI_BAND["warning"],
                      f"({_fmt(warn)}, {_fmt(crit)}]"),
                     ("critical", KPI_BAND["critical"],
                      f"({_fmt(crit)}, {_fmt(hi)}]" if hi > crit
                      else f"({_fmt(crit)}, +∞)")]
        return BandScheme(spec, rule, parts, [])

    cuts = _nice_breaks(v)
    edges = [float(v.min())] + cuts
    spec = []
    for i, left in enumerate(edges):
        right = edges[i + 1] if i + 1 < len(edges) else None
        colour = RAMP[min(int(i * len(RAMP) / max(len(edges), 1)),
                          len(RAMP) - 1)]
        spec.append((f"b{i}", colour,
                     f"[{_fmt(left)}, {_fmt(right)})" if right is not None
                     else f"[{_fmt(left)}, +∞)"))
    return BandScheme(spec, None, [], edges)


def legend_rows(bands: pd.Series, spec: list[tuple]) -> list[tuple]:
    """(colour, interval, "(count, pct%)", label) per band, in reading order."""
    counts = bands.value_counts()
    total = max(int(len(bands)), 1)
    rows = [(colour, interval, f"({int(counts.get(k, 0)):,}, "
             f"{100 * int(counts.get(k, 0)) / total:.2f}%)", BAND_LABEL.get(k, "OK" if k.startswith("ok") else ""))
            for k, colour, interval in spec]
    n_none = int(counts.get("none", 0))
    if n_none:
        rows.append((KPI_BAND["none"], "no data",
                     f"({n_none:,}, {100 * n_none / total:.2f}%)",
                     BAND_LABEL["none"]))
    return rows


class KpiLegend(MacroElement):
    """What the colours mean: one boxed legend, bottom-left on the map.

    The map is drawn inside st_folium's iframe, so the Streamlit page's CSS
    never reaches it. The styles travel with the legend into the map
    document's head, and each colour square also carries its size and colour
    inline, so it can never render as loose text with invisible chips again.
    """
    _template = Template("""
        {% macro header(this, kwargs) %}
        <style>{{ this.css }}</style>
        {% endmacro %}
        {% macro script(this, kwargs) %}
        (function () {
          var ctl = L.control({position: 'bottomleft'});
          ctl.onAdd = function () {
            var d = L.DomUtil.create('div', 'sm-legend');
            d.innerHTML = {{ this.html }};
            L.DomEvent.disableClickPropagation(d);
            L.DomEvent.disableScrollPropagation(d);
            return d;
          };
          ctl.addTo({{ this._parent.get_name() }});
        })();
        {% endmacro %}
    """)

    def __init__(self, title: str, rows: list[tuple], note: str = ""):
        super().__init__()
        self._name = "KpiLegend"
        self.css = LEGEND_CSS
        items = "".join(
            "<div class='sm-lg-row'>"
            f"<span class='sm-lg-chip' style='display:inline-block;width:14px;"
            f"height:14px;background:{colour}'></span>"
            f"<span class='sm-lg-r'>{html.escape(interval)}</span>"
            f"<span class='sm-lg-n'>{tally}</span>"
            + (f"<span class='sm-lg-b'>{label}</span>" if label else "")
            + "</div>"
            for colour, interval, tally, label in rows)
        self.html = json.dumps(
            f"<div class='sm-lg-t'>{html.escape(title)}</div>{items}"
            + (f"<div class='sm-lg-note'>{html.escape(note)}</div>"
               if note else ""))
