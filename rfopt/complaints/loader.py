"""Load a complaint-ticket export and normalise the columns we need.

Tolerant of the operator's wide "CC Process" dumps and of a slim sample with
just ``ticket id / msisdn / problem time / create time / site id / location``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from rfopt.ingest._io import excel_file, read_excel

_SITE_RE = re.compile(r"([A-Za-z]{2,4}\d{3,6})")
_SECTOR_RE = re.compile(r"[-_ ]([1-9])\b")
_GEOCODE_CSV = Path(__file__).resolve().parents[2] / "config" / "r5_area_geocode.csv"


def _norm_area(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", str(s).strip().lower())
    s = re.sub(r"\b(centre|center|cntr|ctr)\b", "center", s)
    return re.sub(r"\s+", " ", s).strip()


@lru_cache(maxsize=1)
def _load_geocode() -> dict[str, tuple[float, float, float]]:
    """area name (normalised) -> (lat, lon, spread_km)."""
    if not _GEOCODE_CSV.exists():
        return {}
    g = pd.read_csv(_GEOCODE_CSV)
    out: dict[str, tuple[float, float, float]] = {}
    for _, r in g.iterrows():
        try:
            out[_norm_area(r["subdistrict"])] = (
                float(r["lat"]), float(r["lon"]),
                float(r.get("spread_km", 10.0) or 10.0))
        except (TypeError, ValueError):
            continue
    return out

# canonical -> candidate source headers (normalised, substring match)
_MAP: dict[str, list[str]] = {
    "ticket_id": ["ticket id", "incident id", "hpsm incident id", "ticket",
                  "complaint id", "case id", "id"],
    "msisdn": ["msisdn", "number of user", "user number", "phone", "mobile",
               "subscriber", "b number", "calling number"],
    "problem_time": ["problem time", "call time", "complaint time",
                     "occurrence time", "fault time", "event time"],
    "create_time": ["create time", "created at", "createtime", "creation time",
                    "submittime", "open time", "logged"],
    "close_time": ["close time", "closure time", "resolved time", "end time"],
    "serving_raw": ["sector serving", "serving sector", "serving cell",
                    "serving site sector", "sector"],
    "site_id": ["site id", "site_id", "siteid", "serving site", "site id(sd check_site_id)",
                "site id(sd check)", "enodeb"],
    "site_name": ["site name", "sitename"],
    "cell_id": ["cell id", "cell name", "cell", "cgi", "lcr"],
    "latitude": ["latitude", "lat", "complaint lat", "y lat", "user lat"],
    "longitude": ["longitude", "long", "lon", "lng", "complaint lon", "user lon"],
    "city": ["city", "governorate", "gouvernorate", "province"],
    "sub_district": ["sub district", "subdistrict", "district", "neighborhood",
                     "neighbourhood", "area name", "land mark", "incident location"],
    "region": ["region", "cluster"],
    "status": ["status", "ticket status", "current status"],
    "group": ["group", "team", "queue"],
    "closure_code": ["closure code", "closure code(incident diagnostic)"],
    "rf_analysis": ["rf analysis", "rf classification"],
    "root_cause": ["root cause"],
    "diag_comment": ["diagnostic comment", "diagnostic comment(incident diagnostic)",
                     "field comment", "brief description", "remarks",
                     "updated breakdown remarks"],
    "diag_action": ["diagnostic action", "diagnostic action(incident diagnostic)",
                    "action"],
}


@dataclass
class ComplaintColumns:
    resolved: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"{len(self.resolved)} fields mapped"
                + (f"; missing {', '.join(self.missing)}" if self.missing else ""))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", str(s).strip().lower()).strip()


def _read(path_or_buf, sheet=None) -> pd.DataFrame:
    name = getattr(path_or_buf, "name", str(path_or_buf))
    if Path(name).suffix.lower() in (".csv", ".txt", ".tsv"):
        return pd.read_csv(path_or_buf, dtype=str, keep_default_na=False)
    xl = excel_file(path_or_buf)
    if sheet is None:
        # the biggest sheet is the ticket list
        sizes = {sn: read_excel(xl, sheet_name=sn, nrows=0).shape[1]
                 for sn in xl.sheet_names}
        counts = {}
        for sn in xl.sheet_names:
            try:
                counts[sn] = len(read_excel(xl, sheet_name=sn, usecols=[0],
                                               dtype=str))
            except Exception:
                counts[sn] = 0
        sheet = max(counts, key=counts.get)
    return read_excel(xl, sheet_name=sheet, dtype=str)


def load_complaints(
    path_or_buf,
    *,
    sheet=None,
    region: str | None = "R5",
    prefixes: tuple[str, ...] = ("BAS", "NAS", "EMA", "SAM"),
    rf_only: bool = False,
    open_only: bool = False,
    dedupe: bool = True,
) -> pd.DataFrame:
    """Return a normalised complaint frame.

    Adds ``._cols`` (a :class:`ComplaintColumns`) and ``._notes`` as attrs.
    """
    raw = _read(path_or_buf, sheet)
    raw.columns = [str(c).strip() for c in raw.columns]
    norm_cols = {c: _norm(c) for c in raw.columns}

    cols = ComplaintColumns()
    out = pd.DataFrame(index=raw.index)
    for canon, cands in _MAP.items():
        hit = None
        # exact-ish first, then substring
        for c, n in norm_cols.items():
            if n in cands:
                hit = c
                break
        if hit is None:
            for c, n in norm_cols.items():
                if any(cand in n for cand in cands) and c not in cols.resolved.values():
                    hit = c
                    break
        if hit is not None:
            out[canon] = raw[hit]
            cols.resolved[canon] = hit
        else:
            cols.missing.append(canon)

    notes: list[str] = []

    # --- site / sector ------------------------------------------------
    serving = out.get("serving_raw", pd.Series("", index=out.index)).astype(str)
    site_from_serv = serving.str.extract(_SITE_RE.pattern)[0]
    sec_from_serv = serving.str.extract(r"[-_ ]([1-9])\s*$")[0]
    if "site_id" in out:
        sid = out["site_id"].astype(str).str.extract(_SITE_RE.pattern)[0]
    else:
        sid = pd.Series(np.nan, index=out.index)
    out["site_id"] = sid.fillna(site_from_serv).astype(str).str.upper().str.strip()
    out["sector"] = pd.to_numeric(sec_from_serv, errors="coerce")
    if "cell_id" in out:
        cell_sec = out["cell_id"].astype(str).str.extract(r"[-_ ]([1-9])\s*$")[0]
        out["sector"] = out["sector"].fillna(pd.to_numeric(cell_sec, errors="coerce"))
    out["prefix"] = out["site_id"].str[:3]

    # --- coordinates ------------------------------------------------
    def _numcol(name: str) -> pd.Series:
        if name in out.columns:
            return pd.to_numeric(out[name], errors="coerce")
        return pd.Series(np.nan, index=out.index)

    la = _numcol("latitude")
    lo = _numcol("longitude")
    # swap if clearly reversed for southern Iraq
    sw = la.between(43, 50) & lo.between(28, 34)
    la2, lo2 = la.copy(), lo.copy()
    la2[sw], lo2[sw] = lo[sw], la[sw]
    in_range = la2.between(28.5, 34) & lo2.between(43, 50)
    out["complaint_lat"] = la2.where(in_range)
    out["complaint_lon"] = lo2.where(in_range)
    out["coord_source"] = np.where(in_range, "ticket", "")
    out["geo_spread_km"] = np.where(in_range, 0.0, np.nan)
    n_coord = int(in_range.sum())

    # --- fallback: geocode the sub-district / area name -----------
    geo = _load_geocode()
    if geo and "sub_district" in out.columns:
        need = out["coord_source"].eq("") & out["sub_district"].notna()
        keys = out.loc[need, "sub_district"].map(_norm_area)
        matched = keys.map(lambda k: geo.get(k))
        # token-overlap fallback for near-misses
        miss = matched.isna() & keys.ne("")
        if miss.any():
            gk = list(geo)
            gtok = {g: set(g.split()) for g in gk}
            def _fuzzy(k):
                kt = set(k.split())
                if not kt:
                    return None
                best, bs = None, 0.0
                for g, t in gtok.items():
                    j = len(kt & t) / len(kt | t)
                    if j > bs:
                        best, bs = g, j
                return geo[best] if bs >= 0.6 else None
            matched.loc[miss] = keys.loc[miss].map(_fuzzy)
        got = matched.dropna()
        if len(got):
            out.loc[got.index, "complaint_lat"] = got.map(lambda t: t[0])
            out.loc[got.index, "complaint_lon"] = got.map(lambda t: t[1])
            out.loc[got.index, "geo_spread_km"] = got.map(lambda t: t[2])
            out.loc[got.index, "coord_source"] = "subdistrict"
        notes.append(f"sub-district geocode: matched {len(got)} of "
                     f"{int(need.sum())} coord-less tickets (approximate, "
                     f"site-level only)")
    notes.append(f"location: {n_coord} exact / "
                 f"{int((out['coord_source'] == 'subdistrict').sum())} area-centroid "
                 f"/ {int((out['coord_source'] == '').sum())} serving-site-only")

    # --- times ---------------------------------------------------
    for tc in ("problem_time", "create_time", "close_time"):
        if tc in out:
            out[tc] = pd.to_datetime(out[tc], errors="coerce", utc=True)
    if {"problem_time", "create_time"} <= set(out.columns):
        out["report_lag_h"] = (out["create_time"] - out["problem_time"]
                               ).dt.total_seconds() / 3600

    # --- text fields --------------------------------------------
    for tc in ("status", "group", "closure_code", "rf_analysis", "root_cause",
               "diag_comment", "diag_action", "city", "sub_district", "region",
               "ticket_id", "msisdn"):
        if tc in out:
            out[tc] = out[tc].astype(str).str.strip()

    # --- filters ----------------------------------------------
    before = len(out)
    if prefixes and region and region.upper() == "R5":
        out = out[out["prefix"].isin(prefixes)]
        notes.append(f"R5 filter (BAS/NAS/EMA/SAM): kept {len(out)}/{before}")
    elif region:
        m = pd.Series(False, index=out.index)
        for c in ("region", "city"):
            if c in out:
                m |= out[c].str.contains(region, case=False, na=False)
        if m.any():
            out = out[m]
            notes.append(f"region '{region}': kept {len(out)}/{before}")

    if rf_only and "group" in out:
        out = out[out["group"].str.strip().str.upper().eq("RF")]
        notes.append(f"RF group only: {len(out)}")
    if open_only and "status" in out:
        openish = ~out["status"].str.strip().str.lower().isin(
            ["close", "closed", "completed", "resolved", "reject", "rejected"])
        out = out[openish]
        notes.append(f"open tickets only: {len(out)}")
    if dedupe and "ticket_id" in out:
        out = out.sort_values("create_time").drop_duplicates("ticket_id",
                                                             keep="last")
        notes.append(f"deduped by ticket id: {len(out)}")

    out = out.reset_index(drop=True)
    out.attrs["_cols"] = cols
    out.attrs["_notes"] = notes
    return out
