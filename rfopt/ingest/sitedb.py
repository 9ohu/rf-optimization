"""Site-database loader + the R5 region filter.

Accepts several layouts and normalises to one canonical per-**sector** frame:

    site_id, site_name, sector, latitude, longitude, azimuth_deg,
    antenna_height_m, mech_tilt_deg, elec_tilt_deg, vbw_deg, hbw_deg,
    status, region, band, cell_id

Handles the operator's "DB" export
(``Foldr Name | Name | Longitude | latitude | Direction | Sector | Function |
Status | Height``) as well as a generic per-cell parameter dump.

R5 = the four southern-Iraq site-ID prefixes the user owns: BAS, NAS, EMA, SAM.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from rfopt.ingest._io import excel_file, read_excel

R5_PREFIXES: tuple[str, ...] = ("BAS", "NAS", "EMA", "SAM")

_SITE_ID_RE = re.compile(r"([A-Za-z]{2,4}\d{3,6})")

_ALIASES: dict[str, list[str]] = {
    "site_id": ["site id", "site_id", "siteid", "name", "enodeb name", "enodeb",
                "nodeb name", "bts name", "site", "site id(sd check_site_id)",
                "site id(sd check)", "ne name"],
    "site_name": ["foldr name", "folder name", "site name", "sitename"],
    "sector": ["sector", "sector id", "sector no", "sec", "sectorid"],
    "latitude": ["latitude", "lat", "site lat", "y"],
    "longitude": ["longitude", "long", "lon", "lng", "site lon", "x"],
    "azimuth_deg": ["direction", "azimuth", "azimuth deg", "az", "antenna azimuth",
                    "bearing", "orientation"],
    "antenna_height_m": ["height", "antenna height", "antenna height m",
                         "ant height", "hba", "agl", "tower height"],
    "mech_tilt_deg": ["mechanical tilt", "mech tilt", "m tilt", "mtilt",
                      "mechanical downtilt", "mech_tilt_deg"],
    "elec_tilt_deg": ["electrical tilt", "elec tilt", "e tilt", "etilt", "ret",
                      "electrical downtilt", "digital tilt", "elec_tilt_deg",
                      "electrical antenna tilt", "etilt deg"],
    "vbw_deg": ["vbeamwidth", "v beamwidth", "vertical beamwidth", "vbw",
                "vert bw"],
    "hbw_deg": ["hbeamwidth", "h beamwidth", "horizontal beamwidth", "hbw",
                "horiz bw", "beamwidth"],
    "status": ["status", "cell status", "on air", "state"],
    "band": ["band", "frequency band", "carrier", "tech", "technology"],
    "cell_id": ["cell name", "cell id", "cell", "cellname", "eutrancell",
                "cgi", "lcr id"],
    "region": ["region", "cluster", "area", "zone"],
}
_REV = {a: canon for canon, al in _ALIASES.items() for a in al}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", str(s).strip().lower()).strip()


def _read(path_or_buf, sheet=None) -> pd.DataFrame:
    name = getattr(path_or_buf, "name", str(path_or_buf))
    if Path(name).suffix.lower() in (".csv", ".txt", ".tsv"):
        return pd.read_csv(path_or_buf, dtype=str, keep_default_na=False)
    xl = excel_file(path_or_buf)
    if sheet is not None:
        return read_excel(xl, sheet_name=sheet, dtype=str)
    # pick the sheet that looks most like a site table
    best, best_score = xl.sheet_names[0], -1.0
    for sn in xl.sheet_names:
        head = read_excel(xl, sheet_name=sn, nrows=1, dtype=str)
        cols = {_norm(c) for c in head.columns}
        score = sum(any(a in c for c in cols) for a in
                    ("lat", "lon", "azimuth", "direction", "site", "name",
                     "height", "sector"))
        if score > best_score:
            best, best_score = sn, score
    return read_excel(xl, sheet_name=best, dtype=str)


def _guess_prefixes(kind: str) -> tuple[str, ...] | None:
    return R5_PREFIXES if kind and kind.upper() in ("R5", "R-5", "SOUTH") else None


def load_site_db(
    path_or_buf,
    *,
    sheet=None,
    region: str | None = "R5",
    prefixes: tuple[str, ...] | None = None,
    default_vbw: float = 6.5,
    default_hbw: float = 65.0,
) -> pd.DataFrame:
    """Return the canonical per-sector site frame, filtered to the region.

    ``region="R5"`` (default) keeps only site ids beginning BAS/NAS/EMA/SAM.
    Pass ``region=None`` to keep everything, or ``prefixes=(...)`` to override.
    """
    raw = _read(path_or_buf, sheet)
    raw.columns = [str(c).strip() for c in raw.columns]
    ren: dict[str, str] = {}
    for c in raw.columns:
        canon = _REV.get(_norm(c))
        if canon and canon not in ren.values():
            ren[c] = canon
    df = raw.rename(columns=ren)

    # site id: use the mapped column, else extract from site_name / any id-ish col
    if "site_id" not in df.columns or df["site_id"].astype(str).str.strip().eq("").all():
        src = df.get("site_name", df.iloc[:, 0])
        df["site_id"] = src.astype(str).str.extract(_SITE_ID_RE.pattern)[0]
    df["site_id"] = df["site_id"].astype(str).str.strip().str.upper()
    df = df[df["site_id"].str.match(r"^[A-Z]{2,4}\d{3,6}$", na=False)]

    for c in ("latitude", "longitude", "azimuth_deg", "antenna_height_m",
              "mech_tilt_deg", "elec_tilt_deg", "vbw_deg", "hbw_deg", "sector"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c].astype(str).str.replace(r"[^\d.\-]", "",
                                  regex=True), errors="coerce")
        else:
            df[c] = np.nan

    # lat/lon sometimes swapped: for southern Iraq lat 28-34, lon 43-50
    la, lo = df["latitude"], df["longitude"]
    swap = (la.between(43, 50) & lo.between(28, 34))
    df.loc[swap, ["latitude", "longitude"]] = df.loc[swap, ["longitude",
                                                            "latitude"]].values

    if "sector" not in df or df["sector"].isna().all():
        df["sector"] = (df.groupby("site_id").cumcount() + 1)
    df["sector"] = df["sector"].fillna(0).astype(int).clip(lower=0)

    df["vbw_deg"] = df["vbw_deg"].fillna(default_vbw)
    df["hbw_deg"] = df["hbw_deg"].fillna(default_hbw)
    df["mech_tilt_deg"] = df["mech_tilt_deg"].fillna(0.0)
    for c in ("status", "site_name", "band", "region", "cell_id"):
        if c not in df.columns:
            df[c] = ""
        df[c] = df[c].astype(str).str.strip()
    df["status"] = df["status"].str.title().replace({"On Air ": "On Air"})
    df["prefix"] = df["site_id"].str[:3]

    pref = prefixes or _guess_prefixes(region)
    if pref:
        df = df[df["prefix"].isin(pref)]
    elif region:
        df = df[df["region"].str.contains(region, case=False, na=False) |
                df["site_id"].str.contains(region, case=False, na=False)]

    df["sector_id"] = df["site_id"] + "-S" + df["sector"].astype(str)
    keep = ["site_id", "site_name", "sector", "sector_id", "latitude",
            "longitude", "azimuth_deg", "antenna_height_m", "mech_tilt_deg",
            "elec_tilt_deg", "vbw_deg", "hbw_deg", "status", "region", "band",
            "cell_id", "prefix"]
    out = df[[c for c in keep if c in df.columns]].reset_index(drop=True)
    # for cell-level joins downstream, also expose 'cell_id' fallback = sector_id
    out.loc[out["cell_id"].eq("") | out["cell_id"].isna(), "cell_id"] = \
        out["sector_id"]
    return out


def region_summary(sdb: pd.DataFrame) -> dict:
    on = sdb["status"].str.lower().str.startswith("on").sum()
    return {
        "sites": int(sdb["site_id"].nunique()),
        "sectors": int(len(sdb)),
        "on_air_sectors": int(on),
        "planned_sectors": int(sdb["status"].str.lower().str.startswith("plan").sum()),
        "prefixes": sdb["prefix"].value_counts().to_dict(),
        "has_tilt": bool(sdb["elec_tilt_deg"].notna().any()),
        "lat_range": [round(float(sdb["latitude"].min()), 3),
                      round(float(sdb["latitude"].max()), 3)],
        "lon_range": [round(float(sdb["longitude"].min()), 3),
                      round(float(sdb["longitude"].max()), 3)],
    }
