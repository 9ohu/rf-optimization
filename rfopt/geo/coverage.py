"""LTE coverage as a geographic grid, built from measured RSRP.

The DL Coverage Insight export is already a grid: one RSRP per ~50 m cell, with
the MR count behind it. This module keeps that native grid for close zooms and
builds coarser levels (100 m, 200 m …) for wider ones, each cell holding the
MR-weighted median RSRP of the native grids inside it — a median, so a handful
of extreme grids cannot swing a whole block, and weighted by MRs, so a grid
measured once does not count as much as one measured a thousand times.

Each level is cut into 512 × 512-cell blocks and packed into PNGs that carry
the values, not colours: the browser colours them with the thresholds, and a
click reads the exact cell back. Nothing here invents a sample: an empty cell
stays transparent.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

_CONFIG = Path(__file__).resolve().parents[2] / "config" / "thresholds_lte.yaml"

M_PER_DEG = 111_320.0
BLOCK = 512
MAX_LEVELS = 8
DEFAULT_STEP = 0.00045              # ~50 m, when the points are not a lattice

# pixel packing: 12 bits of RSRP (0.05 dB steps) + 12 bits of MR count
RSRP_OFFSET = 160.0
RSRP_SCALE = 20.0
COUNT_EXACT = 2048                  # counts below this are stored exactly
COUNT_RATIO = 1.00676               # above it, ~0.7 % steps up to ~2e9


# --------------------------------------------------------------------------- #
# RSRP classes
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Band:
    key: str
    label: str
    lo: float | None                # value >= lo belongs here; None = open floor
    colour: str
    text: str = ""


def load_bands(path: str | Path | None = None) -> tuple[list[Band], float]:
    """The RSRP classes and the "covered" line, from thresholds_lte.yaml.

    Fair ends and Poor begins at the avg_rsrp_dbm warning line, Very poor at
    its critical line — the same RSRP thresholds the KPI engine judges cells
    by. Excellent / Good and the covered line live in the `coverage:` block.
    """
    data = yaml.safe_load(Path(path or _CONFIG).read_text(encoding="utf-8")) or {}
    cov = data.get("coverage") or {}
    lines = cov.get("rsrp_dbm") or {}
    rule = (data.get("kpis") or {}).get("avg_rsrp_dbm") or {}
    excellent = float(lines["excellent"])
    good = float(lines["good"])
    fair = float(lines.get("fair", rule.get("warning")))
    poor = float(lines.get("poor", rule.get("critical")))
    covered = float(cov["covered_dbm"])
    if not excellent > good > fair > poor:
        raise ValueError("coverage RSRP lines must fall: excellent > good > "
                         f"fair > poor, got {excellent}, {good}, {fair}, {poor}")

    def f(x):
        return f"{x:g}"

    bands = [
        Band("excellent", "Excellent", excellent, "#22C55E", f"≥ {f(excellent)}"),
        Band("good", "Good", good, "#4ADE80", f"{f(good)} to {f(excellent)}"),
        Band("fair", "Fair", fair, "#FACC15", f"{f(fair)} to {f(good)}"),
        Band("poor", "Poor", poor, "#FB923C", f"{f(poor)} to {f(fair)}"),
        Band("very_poor", "Very poor", None, "#EF4444", f"< {f(poor)}"),
    ]
    return bands, covered


def band_index(values: np.ndarray, bands: list[Band]) -> np.ndarray:
    """0 for the best class … len(bands) - 1 for the worst."""
    v = np.asarray(values, dtype=np.float64)
    idx = np.zeros(v.shape, dtype=np.int64)
    for b in bands:
        if b.lo is not None:
            idx += v < b.lo
    return idx


def grid_stats(rsrp: np.ndarray, mr: np.ndarray, bands: list[Band],
               covered: float) -> dict:
    """Per class: grids and share of MRs; overall: MRs, grids, covered %."""
    w = np.asarray(mr, dtype=np.float64)
    total = float(w.sum()) or 1.0
    idx = band_index(rsrp, bands)
    per = []
    for j, b in enumerate(bands):
        m = idx == j
        per.append({"key": b.key, "grids": int(m.sum()),
                    "mr_pct": 100.0 * float(w[m].sum()) / total})
    cov_m = np.asarray(rsrp, dtype=np.float64) >= covered
    return {"grids": int(len(w)), "mrs": float(w.sum()), "bands": per,
            "covered_dbm": covered,
            "covered_pct": 100.0 * float(w[cov_m].sum()) / total}


# --------------------------------------------------------------------------- #
# the grid
# --------------------------------------------------------------------------- #
def detect_step(v: np.ndarray) -> float:
    """The export's grid step in degrees (0.00045 for the 50 m grid)."""
    u = np.unique(np.round(np.asarray(v, dtype=np.float64), 6))
    d = np.diff(u)
    d = d[(d > 1e-5) & (d < 0.01)]
    step = float(np.median(d)) if len(d) else DEFAULT_STEP
    return step if 0.0001 <= step <= 0.01 else DEFAULT_STEP


def lattice_origin(v: np.ndarray, step: float) -> float:
    """An origin that puts the export's grid centres in the middle of cells,
    so rounding in the coordinates never tips a grid into its neighbour."""
    v = np.asarray(v, dtype=np.float64)
    frac = (v / step) % 1.0
    ang = 2 * math.pi * frac
    off = (math.atan2(float(np.sin(ang).mean()), float(np.cos(ang).mean()))
           / (2 * math.pi)) % 1.0
    m = float(v.min()) / step
    return (round(m - off) + off - 0.5) * step


def weighted_median(keys: np.ndarray, values: np.ndarray, weights: np.ndarray):
    """Per key: the weighted median value and the total weight."""
    order = np.lexsort((values, keys))
    ks, vs = keys[order], values[order]
    ws = np.asarray(weights, dtype=np.float64)[order]
    starts = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])
    cum = np.cumsum(ws)
    before = np.r_[0.0, cum[starts[1:] - 1]]
    tot = np.add.reduceat(ws, starts)
    pos = np.searchsorted(cum, before + tot / 2.0, side="left")
    return ks[starts], vs[pos], tot


def rsrp_code(v) -> np.ndarray:
    return np.clip(np.round((np.asarray(v, dtype=np.float64) + RSRP_OFFSET)
                            * RSRP_SCALE), 0, 4095).astype(np.int64)


def count_code(c) -> np.ndarray:
    c = np.asarray(c, dtype=np.float64)
    big = COUNT_EXACT + np.round(np.log(np.maximum(c, COUNT_EXACT) / COUNT_EXACT)
                                 / math.log(COUNT_RATIO))
    return np.where(c < COUNT_EXACT, np.round(c), np.minimum(big, 4095)).astype(np.int64)


def decode_pixel(px) -> tuple[float, float] | None:
    """(rsrp dBm, MR count) out of one RGBA pixel, or None for an empty cell."""
    r, g, b, a = (int(x) for x in px[:4])
    if a < 128:
        return None
    q = (r << 4) | (g >> 4)
    cc = ((g & 15) << 8) | b
    mr = cc if cc < COUNT_EXACT else COUNT_EXACT * COUNT_RATIO ** (cc - COUNT_EXACT)
    return q / RSRP_SCALE - RSRP_OFFSET, float(mr)


@dataclass
class CoverageLevel:
    k: int
    cell_lat: float
    cell_lon: float
    cells: int
    blocks: dict = field(default_factory=dict)   # (by, bx) -> PNG bytes

    @property
    def cell_m(self) -> float:
        return self.cell_lat * M_PER_DEG


@dataclass
class CoverageGrid:
    lat0: float
    lon0: float
    step_lat: float
    step_lon: float
    block: int
    levels: list[CoverageLevel]
    extent: tuple[float, float, float, float]    # south, west, north, east

    @property
    def png_bytes(self) -> int:
        return sum(len(p) for lv in self.levels for p in lv.blocks.values())


def _encode_blocks(cy, cx, med, tot, block: int) -> dict:
    from PIL import Image

    by, bx = cy // block, cx // block
    bkey = by * 1_000_003 + bx
    order = np.argsort(bkey, kind="stable")
    bkey, cy, cx = bkey[order], cy[order], cx[order]
    q, cc = rsrp_code(med[order]), count_code(tot[order])
    starts = np.flatnonzero(np.r_[True, bkey[1:] != bkey[:-1]])
    ends = np.r_[starts[1:], len(bkey)]
    out = {}
    for s, e in zip(starts, ends):
        img = np.zeros((block, block, 4), dtype=np.uint8)
        yy = block - 1 - (cy[s:e] % block)          # row 0 is the north edge
        xx = cx[s:e] % block
        img[yy, xx, 0] = q[s:e] >> 4
        img[yy, xx, 1] = ((q[s:e] & 15) << 4) | (cc[s:e] >> 8)
        img[yy, xx, 2] = cc[s:e] & 255
        img[yy, xx, 3] = 255
        buf = io.BytesIO()
        Image.fromarray(img, "RGBA").save(buf, "PNG", compress_level=6)
        out[(int(by[order][s]), int(bx[order][s]))] = buf.getvalue()
    return out


def build_grid(lat, lon, rsrp, mr, *, block: int = BLOCK,
               max_levels: int = MAX_LEVELS, progress=None) -> CoverageGrid:
    """The native grid and its coarser levels, packed as PNG blocks."""
    lat = np.asarray(lat, dtype=np.float64)
    lon = np.asarray(lon, dtype=np.float64)
    rsrp = np.asarray(rsrp, dtype=np.float64)
    mr = np.asarray(mr, dtype=np.float64)
    step_lat, step_lon = detect_step(lat), detect_step(lon)
    lat0, lon0 = lattice_origin(lat, step_lat), lattice_origin(lon, step_lon)
    iy = np.floor((lat - lat0) / step_lat).astype(np.int64)
    ix = np.floor((lon - lon0) / step_lon).astype(np.int64)
    ny, nx = int(iy.max()) + 1, int(ix.max()) + 1
    top = int(np.clip(math.ceil(math.log2(max(ny, nx, 1) / block)), 0,
                      max_levels - 1)) if max(ny, nx) > block else 0
    levels = []
    for k in range(top + 1):
        if progress:
            progress(k, top)
        nxk = (nx >> k) + 1
        keys = (iy >> k) * nxk + (ix >> k)
        ukey, med, tot = weighted_median(keys, rsrp, mr)
        cy, cx = ukey // nxk, ukey % nxk
        levels.append(CoverageLevel(k, step_lat * 2 ** k, step_lon * 2 ** k,
                                    int(len(ukey)),
                                    _encode_blocks(cy, cx, med, tot, block)))
    return CoverageGrid(lat0, lon0, step_lat, step_lon, block, levels,
                        (float(lat.min()), float(lon.min()),
                         float(lat.max()), float(lon.max())))
