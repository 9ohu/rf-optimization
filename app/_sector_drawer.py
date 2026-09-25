"""The Sites map's Sector Details drawer and KPI time slider — the Python side.

Builds the payloads from the real data (the KMZ, the EP tracker, the uploaded
KPI export and its own timestamps) and hands them, with the scripts and styles
in `app/assets`, to the map. Nothing here makes up a value: an empty tracker,
a sector the file does not cover, a KPI without a threshold all say so.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
from branca.element import MacroElement
from jinja2 import Template

from _kpi_map import (BAND_LABEL, KPI_BAND, LEGEND_CSS, apply_scheme,
                      scheme_segments)
from _kpi_time import js_json, pack_frames, series_stats, time_labels, time_matrix
from _map_ui import band_rank
from _ui import ICONS

_ASSETS = Path(__file__).resolve().parent / "assets"


def _asset(name: str) -> str:
    return (_ASSETS / name).read_text(encoding="utf-8")


class MapAssets(MacroElement):
    """The time bar, drawer and legend styles, in the map document's head."""
    _template = Template("""
        {% macro header(this, kwargs) %}
        <style>{{ this.css }}</style>
        {% endmacro %}
    """)

    def __init__(self):
        super().__init__()
        self._name = "RfMapAssets"
        self.css = _asset("rf_map.css") + LEGEND_CSS


class KpiTimeline(MacroElement):
    """The KPI over the export's timestamps: recolours beams, towers, legend
    and the cards above the map as the slider moves."""
    _template = Template("""
        {% macro script(this, kwargs) %}
        (function () {
          var MAP = {{ this._parent.get_name() }};
          var BEAMS = {{ this.beams }};
          var CFG = {{ this.cfg }};
          {{ this.js }}
        })();
        {% endmacro %}
    """)

    def __init__(self, cfg: dict, beams_name: str | None):
        super().__init__()
        self._name = "KpiTimeline"
        self.cfg = js_json(cfg)
        self.beams = beams_name or "null"
        self.js = _asset("kpi_timeline.js")


class SectorDrawer(MacroElement):
    """The right-side Sector Details drawer (also carries the click plumbing,
    so it is on the map even when nothing is selected). The Sites page builds it
    without data and publishes the selected site's payload beside the map, so
    the map's script — and the map — stay the same when a sector is opened."""
    _template = Template("""
        {% macro script(this, kwargs) %}
        (function () {
          var MAP = {{ this._parent.get_name() }};
          var CFG = {{ this.cfg }};
          var ICON_TOWER = {{ this.tower }};
          var ICON_CHART = {{ this.chart }};
          {{ this.js }}
        })();
        {% endmacro %}
    """)

    def __init__(self, cfg: dict | None):
        super().__init__()
        self._name = "SectorDrawer"
        self.cfg = js_json(cfg) if cfg else "null"
        self.tower = js_json(ICONS["tower"])
        self.chart = js_json(ICONS["chart"])
        self.js = _asset("sector_drawer.js")


def store_key(*parts) -> str:
    """Where the browser keeps the slider position for one file + KPI."""
    return "rf_t:" + hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:12]


def band_order(scheme) -> list[str]:
    return [k for k, _, _ in scheme.spec] + ["none"]


def kpi_config(*, label: str, kpi: str, unit: str, how: str, file_name: str,
               scheme, draw: pd.DataFrame, window_vals, sec: pd.DataFrame,
               site: pd.DataFrame, store: str) -> dict:
    """Everything the time slider needs, from the export's own timestamps."""
    window_vals = np.asarray(window_vals, dtype=float)
    times, matrix = time_matrix(draw, sec, site)
    order = band_order(scheme)
    packed = pack_frames(window_vals, matrix, scheme, order)
    colour = {k: c for k, c, _ in scheme.spec}
    colour["none"] = KPI_BAND["none"]
    text = {k: t for k, _, t in scheme.spec}
    bands = [{"key": k, "colour": colour[k], "interval": text.get(k, "no data"),
              "label": BAND_LABEL.get(k) or ("OK" if k.startswith("ok") else ""),
              "rank": band_rank(k)} for k in order]
    every = np.concatenate([window_vals.ravel(), matrix.ravel()])
    finite = every[np.isfinite(every)]
    lo, hi = (float(finite.min()), float(finite.max())) if finite.size else (None, None)
    labels = time_labels(times)
    rule = scheme.rule
    return {
        "label": label, "column": kpi, "unit": unit, "how": how, "file": file_name,
        "bands": bands, "times": labels,
        "window": f"{labels[0]} → {labels[-1]}" if labels else "",
        "codes": packed["codes"], "vals": packed["vals"], "vmin": packed["vmin"],
        "step": packed["step"], "src": packed["src"], "range": [lo, hi],
        "segs": ([list(s) for s in scheme_segments(scheme, lo, hi)]
                 if lo is not None else []),
        "rule": ({"warning": float(rule.warning), "critical": float(rule.critical),
                  "direction": rule.direction} if rule is not None else None),
        "store": store,
    }


def sector_kpis(sectors: pd.DataFrame, sec: pd.DataFrame, site: pd.DataFrame,
                window_vals, scheme) -> list[dict]:
    """One site's sectors over the export: the exact series, the band code per
    frame (window first), min / avg / max, and where the value comes from."""
    window_vals = np.asarray(window_vals, dtype=float)
    _, matrix = time_matrix(sectors, sec, site)
    order = band_order(scheme)
    index = {k: j for j, k in enumerate(order)}
    out = []
    for j in range(len(sectors)):
        w = window_vals[j]
        row = matrix[j] if matrix.shape[1] else np.array([])
        keys = apply_scheme(pd.Series(np.concatenate([[w], row])), scheme)
        sid = str(sectors["sector_id"].iloc[j])
        site_id = str(sectors["site_id"].iloc[j])
        src = ("sector" if len(sec) and sid in sec.index else
               "site" if len(site) and site_id in site.index else "none")
        series = [float(x) if np.isfinite(x) else None for x in row]
        out.append({"series": series,
                    "window": float(w) if np.isfinite(w) else None,
                    "codes": "".join(chr(48 + index.get(k, index["none"]))
                                     for k in keys),
                    "stats": series_stats(series), "src": src})
    return out


def table_json(df: pd.DataFrame | None) -> dict | None:
    """A table for the drawer, every value already as the text to show."""
    if df is None:
        return None

    def cell(v):
        if v is None or (not isinstance(v, str) and pd.isna(v)):
            return "–"
        if isinstance(v, (float, np.floating)):
            return f"{float(v):g}"
        return str(v)

    return {"cols": [str(c) for c in df.columns],
            "rows": [[cell(v) for v in r] for r in df.itertuples(index=False)]}
