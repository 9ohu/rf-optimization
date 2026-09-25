"""LTE coverage on the Sites map — the Streamlit side.

Reads the uploaded Coverage Insight exports into this session's memory (never
to disk), builds the grid once per set of files, hands its PNG blocks to
Streamlit's in-memory media store, and gives the map the layer that draws
them. The numbers on the cards and the legend come from the grid rows.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import streamlit as st
from branca.element import MacroElement
from jinja2 import Template

from rfopt.geo.coverage import (COUNT_EXACT, COUNT_RATIO, RSRP_OFFSET,
                                RSRP_SCALE, build_grid, grid_stats, load_bands)
from rfopt.ingest.coverage_grid import (CoverageFile, CoverageFormatError,
                                        read_coverage_export)

_ASSETS = Path(__file__).resolve().parent / "assets"

COVERAGE_CSS = """
.rf-cov { image-rendering: pixelated; image-rendering: crisp-edges; }
.rf-cov-legend { min-width: 262px; }
.rf-cov-legend .sm-lg-r { min-width: 104px; }
.rf-cov-legend .sm-lg-sub { color: #94A3B8; font-size: 11px; margin: -4px 0 6px; }
.rf-cov-legend .sm-lg-note { white-space: normal; line-height: 1.5; }
.rf-cov-pop .rf-cov-val { display: flex; align-items: center; gap: 8px; margin: 6px 0 4px;
    color: #94A3B8; }
.rf-cov-pop .rf-cov-val b { font-size: 22px; color: #F8FAFC; font-variant-numeric: tabular-nums; }
.rf-cov-badge { display: inline-flex; align-items: center; gap: 5px; margin-left: auto;
    padding: 1px 8px; border-radius: 999px; font-size: 11.5px; font-weight: 700; color: var(--c);
    background: color-mix(in srgb, var(--c) 16%, transparent);
    border: 1px solid color-mix(in srgb, var(--c) 45%, transparent); }
.rf-cov-badge i { width: 7px; height: 7px; border-radius: 50%; background: var(--c); }
.rf-cov-pop .rf-kvs { display: grid; grid-template-columns: auto 1fr; gap: 3px 12px;
    margin-top: 6px; padding-top: 6px; border-top: 1px solid #1E3A5F; font-size: 12px; }
.rf-cov-pop .rf-kvs span { color: #94A3B8; }
.rf-cov-pop .rf-kvs b { color: #F1F5F9; font-weight: 600; text-align: right;
    font-variant-numeric: tabular-nums; }
"""


class CoverageLayer(MacroElement):
    """The coverage grid, its legend and the click read-out."""
    _template = Template("""
        {% macro header(this, kwargs) %}
        <style>{{ this.css }}</style>
        {% endmacro %}
        {% macro script(this, kwargs) %}
        (function () {
          var MAP = {{ this._parent.get_name() }};
          var CFG = {{ this.cfg }};
          {{ this.js }}
        })();
        {% endmacro %}
    """)

    def __init__(self, cfg: dict):
        super().__init__()
        self._name = "CoverageLayer"
        self.css = COVERAGE_CSS
        self.cfg = json.dumps(cfg, separators=(",", ":")).replace("</", "<\\/")
        self.js = (_ASSETS / "coverage_layer.js").read_text(encoding="utf-8")


def read_upload(upload) -> CoverageFile:
    """One dropped export, parsed into compact arrays."""
    return read_coverage_export(io.BytesIO(upload.getvalue()), upload.name)


def region_of(f: CoverageFile) -> str:
    head = f.name[:3]
    return head.upper() if head.isalpha() else Path(f.name).stem[:12]


@st.cache_resource(show_spinner="Building the coverage grid…", max_entries=2)
def _grid(key: tuple, _files: tuple):
    lat = np.concatenate([f.lat for f in _files])
    lon = np.concatenate([f.lon for f in _files])
    rsrp = np.concatenate([f.rsrp for f in _files])
    mr = np.concatenate([f.mr for f in _files])
    return build_grid(lat, lon, rsrp, mr)


@st.cache_resource(show_spinner=False, max_entries=4)
def _stats(key: tuple, _files: tuple, bands: tuple, covered: float) -> dict:
    return grid_stats(np.concatenate([f.rsrp for f in _files]),
                      np.concatenate([f.mr for f in _files]), list(bands), covered)


class Coverage:
    """The grid built from the kept files, with its classes and statistics."""

    def __init__(self, kept: dict):
        ids = tuple(sorted(kept))
        self.files = tuple(kept[i] for i in ids)
        self.grid = _grid(ids, self.files)
        bands, covered = load_bands()
        self.bands, self.covered = bands, covered
        self.stats = _stats(ids, self.files, tuple(bands), covered)

    @property
    def time_note(self) -> str:
        cols = sorted({f.time_column for f in self.files if f.time_column})
        if cols:
            return f"column {', '.join(cols)} in the file (whole period drawn)"
        stamps = sorted({f.exported for f in self.files if f.exported})
        return ("none in the file" + (f" · exported {stamps[-1]}" if stamps else ""))

    @property
    def sources(self) -> str:
        return " · ".join(dict.fromkeys(region_of(f) for f in self.files))

    def weak_pct(self) -> float:
        """Share of MRs in Poor and Very poor grids."""
        return float(sum(b["mr_pct"] for b in self.stats["bands"]
                         if b["key"] in ("poor", "very_poor")))

    def layer_config(self) -> dict:
        """What the map needs; block URLs come from the in-memory media store."""
        from streamlit import runtime

        mgr = runtime.get_instance().media_file_mgr if runtime.exists() else None
        g = self.grid
        levels = []
        for lv in g.levels:
            blocks = []
            for (by, bx), png in sorted(lv.blocks.items()):
                url = (mgr.add(png, "image/png", f"rf-coverage.{lv.k}.{by}.{bx}")
                       if mgr is not None else "")
                blocks.append([by, bx, url])
            levels.append({"k": lv.k, "cell_lat": lv.cell_lat,
                           "cell_lon": lv.cell_lon, "cell_m": lv.cell_m,
                           "cells": lv.cells, "blocks": blocks})
        return {
            "lat0": g.lat0, "lon0": g.lon0, "block": g.block, "levels": levels,
            "min_px": 3, "opacity": 0.62,
            "rsrp_offset": RSRP_OFFSET, "rsrp_scale": RSRP_SCALE,
            "count_exact": COUNT_EXACT, "count_ratio": COUNT_RATIO,
            "bands": [{"key": b.key, "label": b.label, "lo": b.lo,
                       "colour": b.colour, "text": b.text} for b in self.bands],
            "stats": self.stats,
            "rsrp_label": f"{self.files[0].rsrp_column}" if self.files else "RSRP (dBm)",
            "time_note": self.time_note, "sources": self.sources,
        }


def compact(n: float) -> str:
    if n >= 1e9:
        return f"{n / 1e9:,.2f}B"
    if n >= 1e6:
        return f"{n / 1e6:,.1f}M"
    return f"{n:,.0f}"


__all__ = ["Coverage", "CoverageLayer", "CoverageFormatError", "compact",
           "read_upload", "region_of"]
