"""Load the operator's "Engineering Parameter Tracker" workbook.

Sheets: GSM / UMTS / LTE (+ *Deactive*) / RET.  We read the technology sheets
into one canonical per-cell parameter frame:

    site_id, enodeb_name, cell_id, cell_name, sector, sector_num, technology,
    band, band_label, earfcn, bandwidth_mhz, latitude, longitude, azimuth_deg,
    antenna_height_m, mech_tilt_deg, elec_tilt_deg, elec_tilt_branches,
    max_ret_deg, total_tilt_deg, rs_power_dbm, pci, mod3, rsi, antenna_model,
    is_outdoor, city, district, sub_district, tac, cgi, status, region

R5 = site IDs beginning BAS / NAS / EMA / SAM (matches the file's "Region 5").
Electrical downtilt is stored per branch in 0.1 deg units, e.g. ``[40,40,40,42]``
-> mean 4.05 deg.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from rfopt.ingest._io import cached_parse, excel_file
from rfopt.ingest.sitedb import R5_PREFIXES

_PARSE_V = 2

_BAND_LABEL = {
    "1": "L2100", "3": "L1800", "7": "L2600", "8": "L900", "20": "L800",
    "28": "L700", "38": "L2600T", "40": "L2300T", "41": "N41",
    "band1": "U2100", "band8": "U900", "band3": "U1800",
    "gsm900": "G900", "gsm900_dcs1800": "G900/1800", "dcs1800": "G1800",
}

# per-tech header aliases -> canonical
_LTE_MAP = {
    "*enodeb name": "enodeb_name", "site id": "site_id",
    "activation status": "status", "cell name": "cell_name",
    "sector name": "sector", "*enodeb id": "enodeb_id", "sector": "sector_num",
    "*cell id": "cell_id", "*local cell id": "local_cell_id", "cgi": "cgi",
    "frequency band": "band", "downlink earfcn": "earfcn",
    "*physical cell id": "pci", "mod3": "mod3", "root sequence index": "rsi",
    "longitude": "longitude", "latitude": "latitude", "azimuth": "azimuth_deg",
    "isoutdoor": "is_outdoor", "city": "city", "district": "district",
    "sub_district": "sub_district", "groudheight": "antenna_height_m",
    "groundheight": "antenna_height_m", "m-downtilt": "mech_tilt_deg",
    "antenna": "antenna_model", "max ret": "max_ret_raw",
    "electrical downtilt": "elec_tilt_raw",
    "electrical downtilt/10": "elec_tilt_deg10", "tac": "tac",
    "bandwidth": "bandwidth", "rs power": "rs_power_raw", "region": "region",
    "*cell transmission and reception mode": "tx_rx_mode",
}
_GU_MAP = {
    "site name": "enodeb_name", "site code": "site_id", "site": "site_id",
    "nodebname": "enodeb_name", "nodebid": "enodeb_id", "cellname": "cell_name",
    "cell name": "cell_name", "sector": "sector_num", "site-sector": "sector",
    "ci": "cell_id", "cellid": "cell_id", "cgi": "cgi", "band": "band",
    "frequency band(*": "band_fam", "frequency band": "band_fam",
    "dlfreq": "earfcn", "bcch": "earfcn",
    "dl primary scrambling code": "psc", "bsic": "bsic",
    "longitude": "longitude", "latitude": "latitude", "azimuth": "azimuth_deg",
    "height": "antenna_height_m", "mechanical downtilt": "mech_tilt_deg",
    "electrical downtilt": "elec_tilt_deg_direct",
    "actual tilt(0.1degree)/10": "elec_tilt_deg10",
    "isoutdoor": "is_outdoor", "clutter": "clutter", "status": "status",
    "region": "region", "city": "city", "district": "district",
    "sub district": "sub_district", "lac": "lac", "maxpower": "max_power",
    "antenna model": "antenna_model",
}


@dataclass
class ParamLoad:
    df: pd.DataFrame
    technology: str
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        d = self.df
        return {
            "technology": self.technology,
            "cells": len(d), "sites": int(d["site_id"].nunique()),
            "with_elec_tilt": int(d["elec_tilt_deg"].notna().sum()),
            "with_rs_power": int(d["rs_power_dbm"].notna().sum()),
            "bands": d["band_label"].value_counts().to_dict(),
            "prefixes": d["site_id"].str[:3].value_counts().to_dict(),
        }


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _sector_no(s: pd.Series) -> pd.Series:
    """Sector number out of 'S3' / 'BAS0043-S3' / 'Name_BAS0043-7' / '3'.

    Never off the site code: the GSM sheet's Sector column reads 'BAS0043-S3',
    and a plain digit grab turns that into sector 43.
    """
    t = s.astype(str).str.strip().str.replace(r"\.0+$", "", regex=True)
    n = t.str.extract(r"[Ss](\d{1,2})\s*$")[0]
    n = n.fillna(t.str.extract(r"[-_ ](\d{1,2})\s*$")[0])
    n = n.fillna(t.str.extract(r"^(\d{1,2})$")[0])
    return pd.to_numeric(n, errors="coerce")


def _txt(df: pd.DataFrame, name: str) -> pd.Series:
    """A column as text, or an empty one when the sheet hasn't got it — the
    GSM / UMTS sheets carry far fewer columns than LTE."""
    col = df.get(name)
    if col is None:
        col = pd.Series("", index=df.index, dtype=object)
    return col.astype(str)


def _mean_array(s) -> float:
    """'[40, 40, 40, 42]' -> 40.5 ; '4.0' -> 4.0 ; junk -> NaN."""
    if s is None:
        return np.nan
    t = str(s).strip()
    if t in ("", "nan", "none", "null", "[]"):
        return np.nan
    if t.startswith("["):
        try:
            v = [float(x) for x in ast.literal_eval(t)]
            return float(np.mean(v)) if v else np.nan
        except (ValueError, SyntaxError):
            nums = re.findall(r"-?\d+\.?\d*", t)
            return float(np.mean([float(x) for x in nums])) if nums else np.nan
    try:
        return float(t)
    except ValueError:
        return np.nan


def _clean_rs_power(s) -> float:
    """RS EPRE in dBm. The file mixes dB (e.g. 18.2) and 0.1 dB (e.g. 182);
    0 / negatives are 'not provisioned'."""
    v = pd.to_numeric(s, errors="coerce")
    if v is None or v != v or v <= 0:
        return np.nan
    if v > 40:                       # stored in 0.1 dB
        v = v / 10.0
    return round(float(v), 2) if 3.0 <= v <= 40.0 else np.nan


def _read_sheet(xl: pd.ExcelFile, name: str) -> pd.DataFrame | None:
    if name not in xl.sheet_names:
        return None
    return pd.read_excel(xl, sheet_name=name, dtype=str)


def load_cell_params(
    path_or_buf,
    *,
    technology: str = "LTE",
    region: str | None = "R5",
    prefixes: tuple[str, ...] = R5_PREFIXES,
    include_deactive: bool = False,
    use_cache: bool = True,
) -> ParamLoad:
    tech = technology.upper()
    # _PARSE_V is part of the cache key: bump it whenever _parse_params changes
    # what it produces, or the parquet from the old parser keeps being served.
    tag = f"cellparams_v{_PARSE_V}_{tech}_{region or 'all'}_{int(include_deactive)}"
    parse = lambda src: _parse_params(src, tech, region, prefixes,
                                      include_deactive)          # noqa: E731
    out = cached_parse(path_or_buf, parse, tag=tag) if use_cache else parse(path_or_buf)
    return ParamLoad(df=out, technology=tech,
                     notes=list(out.attrs.get("_notes", ["loaded"])))


def _parse_params(path_or_buf, tech, region, prefixes, include_deactive):
    xl = excel_file(path_or_buf)
    frames = []
    for sn in ([tech] + ([f"{tech} Deactive"] if include_deactive else [])):
        raw = _read_sheet(xl, sn)
        if raw is None:
            continue
        raw.columns = [_norm(c) for c in raw.columns]
        amap = _LTE_MAP if tech == "LTE" else _GU_MAP
        ren = {c: amap[c] for c in raw.columns if c in amap}
        df = raw.rename(columns=ren)
        df = df.loc[:, ~pd.Index(df.columns).duplicated(keep="first")]
        df["_sheet"] = sn
        frames.append(df)
    if not frames:
        raise ValueError(f"No '{tech}' sheet in the parameter workbook.")
    df = pd.concat(frames, ignore_index=True)
    notes: list[str] = []

    # --- identity ---------------------------------------------------------
    df["site_id"] = _txt(df, "site_id").str.strip().str.upper()
    if "sector" not in df.columns or df["sector"].isna().all():
        df["sector"] = df.get("cell_name", df["site_id"])
    df["sector"] = df["sector"].astype(str).str.strip()
    sec_num = _sector_no(df["sector"])
    if "sector_num" in df.columns:
        df["sector_num"] = _sector_no(df["sector_num"]).fillna(sec_num)
    else:
        df["sector_num"] = sec_num
    df["cell_id"] = _txt(df, "cell_id").str.strip()
    df["cell_name"] = _txt(df, "cell_name").str.strip()
    df["technology"] = tech

    # --- band ------------------------------------------------------------
    bcol = df.get("band", df.get("band_fam", pd.Series("", index=df.index)))
    df["band"] = bcol.astype(str).str.strip()
    df["band_label"] = df["band"].str.lower().map(_BAND_LABEL).fillna(
        df.get("band_fam", pd.Series("", index=df.index)).astype(str).str.upper()
        .str.replace(" ", "")).replace("", np.nan)
    df["band_label"] = df["band_label"].fillna("B" + df["band"])
    df["bandwidth_mhz"] = pd.to_numeric(
        _txt(df, "bandwidth").str.extract(r"(\d+\.?\d*)")[0], errors="coerce")

    # --- geometry ------------------------------------------------------
    for c in ("latitude", "longitude", "azimuth_deg", "antenna_height_m",
              "earfcn", "pci", "mod3", "rsi", "psc"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c].astype(str).str.replace(
                r"[^\d.\-]", "", regex=True), errors="coerce")
        else:
            df[c] = np.nan
    la, lo = df["latitude"], df["longitude"]
    sw = la.between(43, 50) & lo.between(28, 34)
    df.loc[sw, ["latitude", "longitude"]] = df.loc[sw,
                                                   ["longitude", "latitude"]].values

    # --- tilt ---------------------------------------------------------
    mech = pd.to_numeric(df.get("mech_tilt_deg"), errors="coerce").fillna(0.0)
    df["mech_tilt_deg"] = mech.where(mech.between(-2, 15), 0.0)  # clamp errors
    if "elec_tilt_deg10" in df.columns:
        df["elec_tilt_deg"] = df["elec_tilt_deg10"].map(_mean_array)
    elif "elec_tilt_deg_direct" in df.columns:
        et = df["elec_tilt_deg_direct"].map(_mean_array)
        df["elec_tilt_deg"] = np.where(et > 20, et / 10.0, et)  # some in 0.1deg
    else:
        df["elec_tilt_deg"] = df.get("elec_tilt_raw",
                                     pd.Series(np.nan, index=df.index)).map(
            lambda s: (_mean_array(s) / 10.0
                       if _mean_array(s) == _mean_array(s) and _mean_array(s) > 20
                       else _mean_array(s)))
    df["elec_tilt_branches"] = _txt(df, "elec_tilt_raw")
    df["elec_tilt_deg"] = df["elec_tilt_deg"].where(
        df["elec_tilt_deg"].between(-3, 16))
    df["max_ret_deg"] = pd.to_numeric(df.get("max_ret_raw"),
                                      errors="coerce") / 10.0
    df["total_tilt_deg"] = (df["mech_tilt_deg"].fillna(0)
                            + df["elec_tilt_deg"]).round(2)

    # --- power / misc --------------------------------------------------
    df["rs_power_dbm"] = df.get("rs_power_raw",
                                pd.Series(np.nan, index=df.index)).map(_clean_rs_power)
    for c in ("city", "district", "sub_district", "antenna_model", "cgi",
              "region", "is_outdoor", "status", "tac"):
        df[c] = _txt(df, c).str.strip()
    df["status"] = df["status"].str.title()

    df["vbw_deg"] = 6.5      # TODO: map from antenna_model when a table is loaded
    df["hbw_deg"] = 65.0
    df["prefix"] = df["site_id"].str[:3]

    # --- filter --------------------------------------------------------
    n0 = len(df)
    if region and region.upper() == "R5":
        df = df[df["prefix"].isin(prefixes)]
        notes.append(f"R5 filter (BAS/NAS/EMA/SAM): {len(df)}/{n0} rows")
    elif region:
        df = df[df["region"].str.contains(region, case=False, na=False)]

    # --- dedupe: one row per (site, sector, band, cell_id); prefer complete --
    df["_score"] = (df["elec_tilt_deg"].notna().astype(int) * 2
                    + df["rs_power_dbm"].notna().astype(int)
                    + (df.get("pci", pd.Series(0, index=df.index)).fillna(0) > 0).astype(int))
    df = (df.sort_values("_score", ascending=False)
            .drop_duplicates(["site_id", "sector", "band", "cell_id"],
                             keep="first"))
    # also drop exact antenna-level dupes (same site/sector/band, no distinct cell)
    dup2 = df.duplicated(["site_id", "sector", "band", "pci"], keep="first")
    if dup2.any():
        notes.append(f"removed {int(dup2.sum())} duplicate cell rows")
        df = df[~dup2]

    df["sector_id"] = df["site_id"] + "-S" + \
        df["sector_num"].fillna(0).astype("Int64").astype(str)

    keep = ["site_id", "enodeb_name", "cell_id", "cell_name", "sector",
            "sector_num", "sector_id", "technology", "band", "band_label",
            "earfcn", "bandwidth_mhz", "latitude", "longitude", "azimuth_deg",
            "antenna_height_m", "mech_tilt_deg", "elec_tilt_deg",
            "elec_tilt_branches", "max_ret_deg", "total_tilt_deg",
            "rs_power_dbm", "pci", "mod3", "rsi", "antenna_model", "is_outdoor",
            "city", "district", "sub_district", "tac", "cgi", "status",
            "region", "prefix", "vbw_deg", "hbw_deg", "_sheet"]
    out = df[[c for c in keep if c in df.columns]].reset_index(drop=True)
    notes.append(f"{out['elec_tilt_deg'].notna().sum()}/{len(out)} cells have "
                 f"electrical tilt; {out['rs_power_dbm'].notna().sum()} have RS "
                 f"power")
    out.attrs["_notes"] = notes
    return out


def params_to_site_db(params: pd.DataFrame) -> pd.DataFrame:
    """Collapse the per-cell param frame to the per-sector site-DB shape
    (rfopt.ingest.sitedb) so the complaint engine can use real tilt."""
    g = (params.sort_values("bandwidth_mhz", ascending=False)
         .groupby(["site_id", "sector_num"], dropna=False))
    rows = []
    for (site, sec), grp in g:
        r0 = grp.iloc[0]
        rows.append({
            "site_id": site, "site_name": r0.get("enodeb_name", ""),
            "sector": int(sec) if pd.notna(sec) else 0,
            "sector_id": r0.get("sector_id", f"{site}-S{sec}"),
            "latitude": grp["latitude"].median(),
            "longitude": grp["longitude"].median(),
            "azimuth_deg": grp["azimuth_deg"].median(),
            "antenna_height_m": grp["antenna_height_m"].median(),
            "mech_tilt_deg": grp["mech_tilt_deg"].median(),
            "elec_tilt_deg": grp["elec_tilt_deg"].median(),
            "vbw_deg": 6.5, "hbw_deg": 65.0,
            "status": ("On Air" if r0.get("status", "").lower().startswith(("act", "on"))
                       else r0.get("status", "")),
            "region": r0.get("region", ""),
            "band": ",".join(sorted(grp["band_label"].dropna().unique())),
            "cell_id": r0.get("sector_id", ""),
        })
    df = pd.DataFrame(rows)
    df["prefix"] = df["site_id"].str[:3]
    return df
