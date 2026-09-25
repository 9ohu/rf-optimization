"""KPI over time for the Sites map.

The export's own timestamps, one row per drawn sector, packed small enough to
ride inside the map — so the time slider, Play and the recolouring run in the
browser instead of rebuilding the map on every step. Nothing here invents a
timestamp or a value: every frame is a column of the uploaded file.
"""

from __future__ import annotations

import json
import math
import re

import numpy as np
import pandas as pd

_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_NAN_Q = 4095                  # the quantised "no value"
VALUE_BUDGET = 300_000         # rows × frames up to which hover values ship


def series_pivots(df: pd.DataFrame, kpi: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The KPI per sector and per site (the per-NodeB rows), a column per
    timestamp, aggregated across a sector's cells the same way the window is."""
    from rfopt.kpi.trends import agg_how

    d = df[df[kpi].notna()]
    how = agg_how(kpi)
    no_sector = d["sector_id"].astype(str).str.endswith("-S0")

    def pivot(rows: pd.DataFrame, by: str) -> pd.DataFrame:
        if rows.empty:
            return pd.DataFrame()
        p = rows.pivot_table(index=by, columns="datetime", values=kpi,
                             aggfunc=how, observed=True)
        p.index = p.index.astype(str)
        return p

    return pivot(d[~no_sector], "sector_id"), pivot(d[no_sector], "site_id")


def time_matrix(sectors: pd.DataFrame, sec: pd.DataFrame,
                site: pd.DataFrame) -> tuple[list, np.ndarray]:
    """Timestamps, and the value of every sector at each of them: its own,
    else its site's at that hour (a 3G NodeB names no sector)."""
    times = sorted(set(sec.columns) | set(site.columns))
    n = len(sectors)
    if not times:
        return [], np.empty((n, 0))
    own = (sec.reindex(index=sectors["sector_id"].astype(str), columns=times)
           .to_numpy(dtype=float) if len(sec) else np.full((n, len(times)), np.nan))
    fall = (site.reindex(index=sectors["site_id"].astype(str), columns=times)
            .to_numpy(dtype=float) if len(site) else np.full((n, len(times)), np.nan))
    return times, np.where(np.isnan(own), fall, own)


def time_labels(times: list) -> list[str]:
    return [pd.Timestamp(t).strftime("%Y-%m-%d %H:%M") for t in times]


def pack_frames(window: np.ndarray, matrix: np.ndarray, scheme,
                band_order: list[str]) -> dict:
    """Frame 0 is the whole window, then one frame per timestamp.

    Each frame is a string with one band code per row; sectors with identical
    series share a row (every sector of a 3G NodeB does). Values ride along,
    quantised to two characters, when they fit the budget — they are only for
    the hover text; the bands are exact.
    """
    from _kpi_map import apply_scheme

    full = np.column_stack([np.asarray(window, dtype=float).reshape(-1, 1),
                            np.asarray(matrix, dtype=float)])
    full = np.where(np.isfinite(full), full, np.nan)
    rows: dict[bytes, int] = {}
    first: list[int] = []
    src = np.zeros(len(full), dtype=np.int64)
    for i, r in enumerate(full):
        k = r.tobytes()
        if k not in rows:
            rows[k] = len(first)
            first.append(i)
        src[i] = rows[k]
    uniq = full[first] if first else np.empty((0, full.shape[1]))

    index = {k: j for j, k in enumerate(band_order)}
    keys = apply_scheme(pd.Series(uniq.ravel()), scheme)
    codes = (keys.map(index).fillna(index["none"]).to_numpy(dtype=np.uint8)
             .reshape(uniq.shape) + 48)
    frames = [codes[:, f].tobytes().decode("ascii") for f in range(uniq.shape[1])]

    vals, vmin, step = None, None, None
    finite = uniq[np.isfinite(uniq)]
    if finite.size and uniq.size <= VALUE_BUDGET:
        vmin, vmax = float(finite.min()), float(finite.max())
        step = (vmax - vmin) / (_NAN_Q - 1) if vmax > vmin else 1.0
        q = np.where(np.isfinite(uniq),
                     np.rint((np.nan_to_num(uniq, nan=vmin) - vmin) / step),
                     _NAN_Q).astype(np.int64)
        alpha = np.frombuffer(_B64.encode("ascii"), dtype=np.uint8)
        pair = np.stack([alpha[q // 64], alpha[q % 64]], axis=-1)
        vals = [np.ascontiguousarray(pair[:, f, :]).tobytes().decode("ascii")
                for f in range(uniq.shape[1])]
    return {"src": src.tolist(), "codes": frames, "vals": vals,
            "vmin": vmin, "step": step}


def unpack_value(packed: dict, frame: int, sector: int) -> float | None:
    """The inverse of the value packing, as the browser does it (for tests)."""
    if not packed["vals"]:
        return None
    s, row = packed["vals"][frame], packed["src"][sector]
    q = _B64.index(s[2 * row]) * 64 + _B64.index(s[2 * row + 1])
    return None if q == _NAN_Q else packed["vmin"] + q * packed["step"]


_UNIT_PAREN = re.compile(r"\((%|ms|dBm|dB|GB|MB|Mbps|kbps|Erl)\)", re.I)
_UNIT_WORD = re.compile(r"(?<![a-z])(mbps|kbps|dbm)(?![a-z])", re.I)
_UNIT_CASE = {"mbps": "Mbps", "kbps": "kbps", "dbm": "dBm", "db": "dB",
              "gb": "GB", "mb": "MB", "ms": "ms", "erl": "Erl", "%": "%"}


def kpi_unit(kpi: str) -> str:
    """The unit the schema gives the KPI, else the one its column names."""
    from _kpi_map import canonical_name
    from rfopt.ingest.schema import kpi_def

    canon = canonical_name(kpi)
    if canon:
        for tech in ("LTE", "UMTS", "GSM"):
            d = kpi_def(canon, tech)
            if d is not None and d.unit and d.unit not in ("index", "#"):
                return d.unit
    m = _UNIT_PAREN.search(kpi) or _UNIT_WORD.search(kpi)
    return _UNIT_CASE.get(m.group(1).lower(), m.group(1)) if m else ""


def series_stats(values) -> dict:
    v = np.array([x for x in values if x is not None and np.isfinite(x)],
                 dtype=float)
    if not v.size:
        return {"n": 0}
    return {"n": int(v.size), "min": float(v.min()), "max": float(v.max()),
            "avg": float(v.mean()), "total": float(v.sum())}


def parse_tip(tip) -> tuple[str, str | None]:
    """What a map click asked for: ("select", sector id), ("close", None), or
    ("none", None). Clicks carry a " · #<n>" nonce so a repeat still counts."""
    if not tip:
        return "none", None
    head = str(tip).split(" · ")[0].strip()
    if head.startswith("__close__"):
        return "close", None
    return ("select", head) if head else ("none", None)


def js_json(obj) -> str:
    """JSON for a <script>: no NaN, and no "</" that could end the script."""
    def clean(x):
        if isinstance(x, (float, np.floating)):
            return float(x) if math.isfinite(x) else None
        if isinstance(x, np.integer):
            return int(x)
        if isinstance(x, dict):
            return {str(k): clean(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [clean(v) for v in x]
        return x
    return (json.dumps(clean(obj), ensure_ascii=False, separators=(",", ":"))
            .replace("</", "<\\/"))
