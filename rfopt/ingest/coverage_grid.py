"""Read a DL Coverage Insight grid export (LTE): Latitude, Longitude, RSRP and
the MR count behind it, one row per grid.

The export arrives as a zip of one or more workbooks (Excel caps a sheet at a
million rows, so a region spills into `_1`, `_2` …), a single workbook, or a
CSV. Columns are found by their names, not their positions. Nothing is kept
on disk: the rows come back as compact arrays.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd


class CoverageFormatError(ValueError):
    """The file is not a coverage grid export."""


@dataclass
class CoverageFile:
    name: str
    size: int
    lat: np.ndarray                 # float32, one entry per grid row
    lon: np.ndarray
    rsrp: np.ndarray                # dBm
    mr: np.ndarray                  # MRs behind that grid's RSRP
    rsrp_column: str
    sheets: list[str] = field(default_factory=list)
    time_column: str | None = None
    exported: str | None = None     # the export stamp in the file name

    @property
    def rows(self) -> int:
        return int(len(self.lat))

    @property
    def mrs(self) -> float:
        return float(self.mr.sum(dtype=np.float64))


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def header_columns(header: list) -> dict[str, int] | None:
    """Column positions for lat / lon / rsrp (+ mr, time), or None."""
    names = [_norm(h) for h in header]
    found: dict[str, int] = {}
    for i, n in enumerate(names):
        if "lat" not in found and n in ("latitude", "lat"):
            found["lat"] = i
        elif "lon" not in found and n in ("longitude", "lon", "lng", "long"):
            found["lon"] = i
        elif "rsrp" not in found and "rsrp" in n.split() + [n]:
            found["rsrp"] = i
        elif "rsrp" not in found and "rsrp" in n:
            found["rsrp"] = i
        elif "mr" not in found and ("count" in n or "sample" in n
                                    or n in ("mr", "mrs", "mr num")):
            found["mr"] = i
        elif "time" not in found and ("time" in n or "date" in n):
            found["time"] = i
    return found if {"lat", "lon", "rsrp"} <= found.keys() else None


def _exported(name: str) -> str | None:
    m = re.search(r"(20\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", name)
    if not m:
        return None
    y, mo, d, h, mi, _ = m.groups()
    return f"{y}-{mo}-{d} {h}:{mi}"


def _columns(rows: list, cols: dict[str, int]) -> tuple[np.ndarray, ...]:
    """The needed columns as floats; rows that are not numbers are dropped."""
    idx = [cols["lat"], cols["lon"], cols["rsrp"]] + ([cols["mr"]] if "mr" in cols else [])
    try:
        # a plain numeric sheet converts in one go; anything else row by row
        a = np.asarray(rows, dtype=np.float64)[:, idx] if rows else np.empty((0, 4))
    except (ValueError, TypeError, IndexError):
        df = pd.DataFrame([[r[i] if i < len(r) else None for i in idx] for r in rows])
        a = df.apply(pd.to_numeric, errors="coerce").to_numpy(dtype=np.float64)
    if a.size == 0:
        a = np.empty((0, len(idx)))
    lat, lon, rsrp = a[:, 0], a[:, 1], a[:, 2]
    mr = a[:, 3] if a.shape[1] > 3 else np.ones(len(a))
    ok = (np.isfinite(lat) & np.isfinite(lon) & np.isfinite(rsrp)
          & (np.abs(lat) <= 90) & (np.abs(lon) <= 180))
    mr = np.where(np.isfinite(mr) & (mr > 0), mr, 1.0)
    return lat[ok], lon[ok], rsrp[ok], mr[ok]


def _table(rows: list, label: str):
    for h in range(min(10, len(rows))):
        cols = header_columns(list(rows[h]))
        if cols:
            header = list(rows[h])
            return (_columns(rows[h + 1:], cols), str(header[cols["rsrp"]]),
                    str(header[cols["time"]]) if "time" in cols else None)
    return None


def _read_workbook(data: bytes, label: str, parts: list, sheets: list, meta: dict):
    from python_calamine import CalamineWorkbook

    wb = CalamineWorkbook.from_filelike(io.BytesIO(data))
    for sh in wb.sheet_names:
        rows = wb.get_sheet_by_name(sh).to_python()
        got = _table(rows, f"{label}:{sh}")
        if got is None:
            continue
        cols, rsrp_col, time_col = got
        parts.append(cols)
        sheets.append(f"{label} · {sh}")
        meta.setdefault("rsrp", rsrp_col)
        if time_col:
            meta.setdefault("time", time_col)


def _read_csv(data: bytes, label: str, parts: list, sheets: list, meta: dict):
    df = pd.read_csv(io.BytesIO(data), header=None, dtype=str,
                     encoding_errors="replace", low_memory=False)
    got = _table(df.values.tolist(), label)
    if got is not None:
        cols, rsrp_col, time_col = got
        parts.append(cols)
        sheets.append(label)
        meta.setdefault("rsrp", rsrp_col)
        if time_col:
            meta.setdefault("time", time_col)


_BOOK = (".xlsx", ".xlsm", ".xls")


def read_coverage_export(src, name: str | None = None, *, progress=None) -> CoverageFile:
    """Every coverage table in a zip / workbook / CSV, as one CoverageFile.

    `progress(label)` is called before each workbook or CSV is read.
    """
    if isinstance(src, (str, Path)):
        name = name or Path(src).name
        data = Path(src).read_bytes()
    else:
        data = src.getvalue() if hasattr(src, "getvalue") else src.read()
    name = name or "coverage"
    parts: list = []
    sheets: list = []
    meta: dict = {}

    def one(blob: bytes, label: str):
        low = label.lower()
        if progress:
            progress(label)
        if low.endswith(_BOOK) or blob[:2] == b"PK" and not low.endswith(".csv"):
            _read_workbook(blob, label, parts, sheets, meta)
        elif low.endswith(".csv") or low.endswith(".txt"):
            _read_csv(blob, label, parts, sheets, meta)

    low = name.lower()
    if low.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            members = sorted(i.filename for i in z.infolist()
                             if not i.is_dir()
                             and i.filename.lower().endswith(_BOOK + (".csv", ".txt")))
            for m in members:
                one(z.read(m), Path(m).name)
    elif low.endswith(_BOOK):
        one(data, name)
    else:
        one(data, name)

    if not parts:
        raise CoverageFormatError(
            "no sheet with Latitude, Longitude and RSRP columns")
    lat = np.concatenate([p[0] for p in parts]).astype(np.float32)
    lon = np.concatenate([p[1] for p in parts]).astype(np.float32)
    rsrp = np.concatenate([p[2] for p in parts]).astype(np.float32)
    mr = np.concatenate([p[3] for p in parts]).astype(np.float32)
    if not len(lat):
        raise CoverageFormatError("the coverage sheets have no rows")
    return CoverageFile(name=name, size=len(data), lat=lat, lon=lon, rsrp=rsrp,
                        mr=mr, rsrp_column=meta.get("rsrp", "RSRP"),
                        sheets=sheets, time_column=meta.get("time"),
                        exported=_exported(name))
