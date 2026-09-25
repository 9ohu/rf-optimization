"""Load a KPI export (Excel/CSV) and normalise it to the canonical schema."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from rfopt.ingest.mapping import MappingConfig, build_mapping
from rfopt.ingest.schema import DIM_COLUMNS, kpis_for

_NULL_TOKENS = {"", "-", "--", "n/a", "na", "nil", "null", "none", "/0", "#div/0!",
                "#value!", "#n/a", "invalid", "-/-", "..."}

# cell-name sector hints:  ..._L18_1 / ...-1 / ...A / ...(1)  -> sector 1
_SECTOR_TAIL_RE = re.compile(r"(?:[_\-\s]?)(?:S|SEC|CELL)?\s*([1-9])(?:\D*)$",
                             re.IGNORECASE)
_SECTOR_ALPHA_RE = re.compile(r"([A-F])\d?$", re.IGNORECASE)
_ALPHA_SECTOR = {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6}

_BAND_RE = re.compile(
    r"\b(L(?:08|8|18|19|21|23|26)00?|L\d{3,4}|U(?:09|21)00|G(?:09|18)00|"
    r"N(?:35|78|28)\d?|LTE\d{3,4}|B\d{1,2})\b", re.IGNORECASE)


@dataclass
class LoadResult:
    df: pd.DataFrame
    mapping: MappingConfig
    technology: str
    granularity: str                      # "hour" | "day" | "mixed"
    n_rows: int = 0
    n_sites: int = 0
    n_cells: int = 0
    date_range: tuple[pd.Timestamp, pd.Timestamp] | None = None
    warnings: list[str] = field(default_factory=list)
    source_columns: list[str] = field(default_factory=list)
    unmapped_columns: list[str] = field(default_factory=list)

    def describe(self) -> str:
        dr = ""
        if self.date_range:
            dr = f" | {self.date_range[0]:%Y-%m-%d %H:%M} -> {self.date_range[1]:%Y-%m-%d %H:%M}"
        return (f"{self.technology} {self.granularity} | {self.n_rows} rows | "
                f"{self.n_sites} sites | {self.n_cells} cells{dr}")


# --------------------------------------------------------------------------- #
def _read_any(path_or_buf, sheet: str | int | None) -> pd.DataFrame:
    name = getattr(path_or_buf, "name", str(path_or_buf))
    suffix = Path(name).suffix.lower()
    if suffix in {".csv", ".txt", ".tsv"}:
        sep = "\t" if suffix == ".tsv" else None
        # peek a few rows to find the header (Huawei sometimes adds a title row)
        peek = pd.read_csv(path_or_buf, sep=sep, engine="python", dtype=str,
                           keep_default_na=False, header=None, nrows=8)
        hr = _guess_header_row(peek)
        try:
            path_or_buf.seek(0)
        except Exception:
            pass
        return pd.read_csv(path_or_buf, sep=sep, engine="python", dtype=str,
                           keep_default_na=False, header=hr)

    # Excel: read the sheet once (headerless), then promote the guessed row.
    raw = pd.read_excel(path_or_buf,
                        sheet_name=(sheet if sheet is not None else 0),
                        header=None, dtype=str)
    hr = _guess_header_row(raw)
    df = raw.iloc[hr + 1:].reset_index(drop=True)
    df.columns = [str(c) for c in raw.iloc[hr].tolist()]
    return df


def _guess_header_row(raw: pd.DataFrame, scan: int = 8) -> int:
    best_row, best_score = 0, -1.0
    for i in range(min(scan, len(raw))):
        row = raw.iloc[i].tolist()
        non_null = [str(x) for x in row if str(x).strip() not in ("", "nan", "None")]
        if not non_null:
            continue
        # header rows are mostly text, mostly unique, few pure numbers
        uniq = len(set(non_null)) / len(non_null)
        numeric = sum(bool(re.fullmatch(r"-?\d[\d.,]*", s)) for s in non_null)
        score = uniq - (numeric / max(len(non_null), 1)) + 0.05 * len(non_null)
        if score > best_score:
            best_row, best_score = i, score
    return best_row


def _to_numeric(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip()
    low = s.str.lower()
    s = s.mask(low.isin(_NULL_TOKENS))
    s = (s.str.replace("%", "", regex=False)
           .str.replace(",", "", regex=False)
           .str.replace(" ", "", regex=False)
           .str.replace(" ", "", regex=False))
    return pd.to_numeric(s, errors="coerce")


def _infer_technology(columns: list[str], hint: str | None) -> str:
    if hint:
        return hint.upper()
    blob = " ".join(columns).lower()
    score = {
        "LTE": sum(t in blob for t in ("rsrp", "rsrq", "e-rab", "erab", "prb",
                                       "eutran", "enodeb", "sinr")),
        "UMTS": sum(t in blob for t in ("rscp", "ec/no", "ecno", "hsdpa", "rtwp",
                                        "nodeb", "cpich")),
        "GSM": sum(t in blob for t in ("rxqual", "rxlev", "sdcch", "erlang",
                                       "bcch", "tbf")),
    }
    best = max(score, key=score.get)
    return best if score[best] > 0 else "LTE"


def _derive_sector(cell_id: str, site_id: str) -> str:
    c = str(cell_id).strip()
    m = _SECTOR_TAIL_RE.search(c)
    if m:
        return f"{site_id}-S{int(m.group(1))}"
    m = _SECTOR_ALPHA_RE.search(c)
    if m and m.group(1).upper() in _ALPHA_SECTOR:
        return f"{site_id}-S{_ALPHA_SECTOR[m.group(1).upper()]}"
    return f"{site_id}-S0"


def _derive_band(cell_id: str, earfcn: str | float | None) -> str:
    m = _BAND_RE.search(str(cell_id))
    if m:
        return m.group(1).upper()
    try:
        e = int(float(earfcn))
    except (TypeError, ValueError):
        return "UNKNOWN"
    for lo, hi, name in [(0, 599, "L2100"), (1200, 1949, "L1800"),
                         (2750, 3449, "L2600"), (6150, 6449, "L800"),
                         (2400, 2649, "L2300"), (3450, 3799, "L2500")]:
        if lo <= e <= hi:
            return name
    return "UNKNOWN"


# --------------------------------------------------------------------------- #
def load_kpi_file(
    path_or_buf,
    *,
    technology: str | None = None,
    sheet: str | int | None = None,
    region_filter: str | None = None,
    extra_aliases: dict[str, list[str]] | None = None,
    manual_map: dict[str, str] | None = None,
) -> LoadResult:
    """Read a file and return a canonical :class:`LoadResult`.

    Parameters
    ----------
    technology      force "LTE"/"UMTS"/"GSM" instead of auto-detecting
    region_filter   keep only rows whose region matches (substring, case-insens.)
    extra_aliases   canonical -> [alias, ...] added on top of the YAML
    manual_map      source column -> canonical name, applied last (wins)
    """
    raw = _read_any(path_or_buf, sheet)
    raw.columns = [str(c).strip() for c in raw.columns]
    raw = raw.dropna(axis=1, how="all")
    src_cols = list(raw.columns)

    tech = _infer_technology(src_cols, technology)
    mapping = build_mapping(src_cols, tech, extra_aliases=extra_aliases)
    if manual_map:
        for s, c in manual_map.items():
            if s in raw.columns and c:
                mapping.resolved[s] = c
                mapping.method[s] = "manual"
                if s in mapping.unmapped:
                    mapping.unmapped.remove(s)

    df = raw.rename(columns=mapping.rename_dict())
    # collapse duplicate canonical columns (keep first non-null)
    df = df.loc[:, ~pd.Index(df.columns).duplicated(keep="first")]

    warnings: list[str] = []

    # ---- datetime + granularity ----------------------------------------
    if "datetime" in df.columns:
        import warnings as _w
        with _w.catch_warnings():
            _w.simplefilter("ignore")
            dt = pd.to_datetime(df["datetime"], errors="coerce")
            if dt.isna().mean() > 0.5:
                dt = pd.to_datetime(df["datetime"], errors="coerce",
                                    dayfirst=True)
        df["datetime"] = dt
    else:
        df["datetime"] = pd.NaT
        warnings.append("No time column found - trend/hourly analysis disabled.")

    gran = "day"
    if df["datetime"].notna().any():
        times = df["datetime"].dropna()
        has_time = (times.dt.hour.nunique() > 1) or (times.dt.minute.nunique() > 1)
        span_days = (times.max() - times.min()).total_seconds() / 86400
        per_day = len(times) / max(span_days, 1)
        gran = "hour" if (has_time and per_day > 1.5) else "day"

    # ---- identity columns --------------------------------------------
    for col in ("site_id", "cell_id", "region", "cell_local_id", "band", "earfcn"):
        if col not in df.columns:
            df[col] = np.nan
    df["site_id"] = df["site_id"].fillna("").astype(str).str.strip()
    df["cell_id"] = df["cell_id"].fillna("").astype(str).str.strip()
    if (df["site_id"] == "").all() and (df["cell_id"] != "").any():
        # derive a site id by trimming the sector suffix from the cell name
        df["site_id"] = df["cell_id"].str.replace(
            r"[_\-\s]?(?:S|SEC|CELL)?\s*[1-6][A-Za-z]?$", "", regex=True).str.strip()
        warnings.append("Site column missing - site id derived from cell name.")

    df["region"] = df["region"].fillna("").astype(str).str.strip()
    df["technology"] = tech
    df["granularity"] = gran
    df["sector_id"] = [
        _derive_sector(c, s) for c, s in zip(df["cell_id"], df["site_id"])
    ]
    df["band"] = [
        b if isinstance(b, str) and b.strip() else _derive_band(c, e)
        for b, c, e in zip(df["band"], df["cell_id"], df["earfcn"])
    ]

    # ---- numeric coercion of every mapped KPI ----------------------
    kpi_names = {d.name for d in kpis_for(tech)}
    for col in df.columns:
        if col in kpi_names:
            df[col] = _to_numeric(df[col])

    # ---- drop vendor "total/average" summary rows -----------------
    junk = df["cell_id"].str.lower().str.contains(
        r"average|total|sum|all cell|grand", regex=True, na=False)
    if junk.any():
        warnings.append(f"Dropped {int(junk.sum())} vendor summary rows.")
        df = df[~junk]

    # ---- region filter -------------------------------------------
    if region_filter:
        mask = df["region"].str.contains(region_filter, case=False, na=False)
        if not mask.any():
            mask = df["site_id"].str.contains(region_filter, case=False, na=False)
        warnings.append(
            f"Region filter '{region_filter}': kept {int(mask.sum())}/{len(df)} rows.")
        df = df[mask]

    df = df[df["cell_id"] != ""].reset_index(drop=True)

    ordered = [c for c in DIM_COLUMNS if c in df.columns]
    rest = [c for c in df.columns if c not in ordered]
    df = df[ordered + rest]

    dr = None
    if df["datetime"].notna().any():
        dr = (df["datetime"].min(), df["datetime"].max())

    return LoadResult(
        df=df,
        mapping=mapping,
        technology=tech,
        granularity=gran,
        n_rows=len(df),
        n_sites=df["site_id"].nunique(),
        n_cells=df["cell_id"].nunique(),
        date_range=dr,
        warnings=warnings,
        source_columns=src_cols,
        unmapped_columns=mapping.unmapped,
    )
