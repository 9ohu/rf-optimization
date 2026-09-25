"""Parse the operator's "R5 Sites" Google-Earth KMZ into a cell database.

The KMZ has one Placemark per **site-sector** (styleUrl ``#onair`` / ``#planned``
/ ``#offair``).  Its ``<description>`` is an HTML balloon:

    <b>SiteName</b>
    Site Code: BAS0011   Sector: 1   Azimuth: 350
    Height: 30 m   Status: On Air

    2G (GSM) Cells
    <u>Name-1</u>: Azimuth=350, Band=GSM900, BCCH=81, BSIC=61, TCH=85
       Electrical Downtilt=40, Power=n/a, Antenna=APE4517R2
       BSC=BBASH02, LAC=3031, Status=ACTIVATED
    3G (UMTS) Cells
    <u>U_Name-A2</u>: Azimuth=350, Tilt=50, Power=460, SAC=114, DL PSC=22, DL Freq=10737, Status=ACTIVATED
    4G (LTE) Cells
    <u>L_Name-1</u>: Azimuth=350, PCI=93, EARFCN=1750, BW=20MHz, TAC=13101, RS Power=182, MAX RET=50, Status=Active
    RET Actual Tilt
    Band U900: 50 (0.1 units)

and its ``<Polygon>`` first coordinate is the site apex (lat / lon).

``load_kmz_sites(path_or_buf)`` returns a :class:`KmzSites` with

  ``.cells``    one row per cell   (technology 2G/3G/4G + every field parsed)
  ``.sectors``  one row per site-sector (lat/lon, azimuth, height, tilt,
                status, per-tech cell counts)
  ``.sites``    one row per site
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path

import numpy as np
import pandas as pd

from rfopt.ingest._io import cached_parse
from rfopt.ingest.sitedb import R5_PREFIXES

# --------------------------------------------------------------------------- #
_SITE_RE = re.compile(r"([A-Za-z]{2,4}\d{3,6})")
_PM_RE = re.compile(r"<Placemark>(.*?)</Placemark>", re.S)
_CDATA_RE = re.compile(r"<description>\s*<!\[CDATA\[(.*?)\]\]>\s*</description>", re.S)
_DESC_RE = re.compile(r"<description>(.*?)</description>", re.S)
_STYLE_RE = re.compile(r"<styleUrl>\s*#?(\w+)\s*</styleUrl>")
_COORD_RE = re.compile(r"<coordinates>\s*([-\d.]+)\s*,\s*([-\d.]+)")
_TAG_RE = re.compile(r"<[^>]+>")
_KV_SPLIT = re.compile(r",(?![^()]*\))")

_STATUS_STYLE = {"onair": "On Air", "planned": "Planned", "offair": "Off Air"}

CELL_COLS = [
    "site_id", "site_name", "sector", "sector_id", "technology", "cell_name",
    "latitude", "longitude", "azimuth_deg", "status", "band", "band_label",
    "bcch", "bsic", "tch", "antenna", "bsc", "lac",
    "sac", "psc", "dl_freq", "pci", "earfcn", "bw_mhz", "tac",
    "elec_tilt_raw", "elec_tilt_deg", "cpich_power",
    "rs_power_raw", "rs_power_dbm", "max_ret_raw", "max_ret_deg", "ret_deg",
]
SECTOR_COLS = [
    "site_id", "site_name", "sector", "sector_id", "latitude", "longitude",
    "azimuth_deg", "antenna_height_m", "elec_tilt_deg", "ret_deg", "status",
    "air", "n_2g", "n_3g", "n_4g", "n_cells",
    "ret_u900_deg", "ret_u2100_deg", "ret_actual_deg",
]

# bump-free cache invalidation: any change to the frame columns changes this
_SCHEMA_SIG = hashlib.sha1(
    ("|".join(CELL_COLS) + "//" + "|".join(SECTOR_COLS)).encode()
).hexdigest()[:8]


@dataclass
class KmzSites:
    cells: pd.DataFrame
    sectors: pd.DataFrame
    sites: pd.DataFrame
    notes: list[str] = field(default_factory=list)

    # -- convenience ------------------------------------------------------- #
    def sector_cells(self, sector_id: str, tech: str | None = None) -> pd.DataFrame:
        d = self.cells[self.cells["sector_id"] == sector_id]
        if tech and tech.upper() != "ALL":
            d = d[d["technology"] == tech.upper()]
        return d.reset_index(drop=True)

    def summary(self) -> dict:
        c = self.cells
        return {
            "sites": int(self.sites["site_id"].nunique()) if len(self.sites) else 0,
            "sectors": int(len(self.sectors)),
            "cells": int(len(c)),
            "by_tech": c["technology"].value_counts().to_dict() if len(c) else {},
            "status": self.sectors["status"].value_counts().to_dict()
            if len(self.sectors) else {},
        }


# --------------------------------------------------------------------------- #
def _clean(html: str) -> str:
    t = html.replace("<br/>", "\n").replace("<br>", "\n").replace("</p>", "\n")
    t = t.replace("&nbsp;", " ").replace("&deg;", "").replace("°", "")
    t = _TAG_RE.sub("", t)
    return unescape(t)


def _kv(segment: str) -> dict:
    """``'Azimuth=350, Band=GSM900, ... Status=ACTIVATED'`` -> dict."""
    out: dict[str, str] = {}
    for part in _KV_SPLIT.split(segment):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip().lower()] = v.strip()
    return out


def _num(v):
    if v is None:
        return np.nan
    m = re.search(r"-?\d+\.?\d*", str(v))
    return float(m.group()) if m else np.nan


def _tilt_deg(raw) -> float:
    """Electrical tilt: source is 0.1-deg units when large (40 -> 4.0)."""
    v = _num(raw)
    if not np.isfinite(v):
        return np.nan
    return v / 10.0 if v > 20 else v


def _band_label(cell_name: str, tech: str, band_raw: str,
                earfcn, dl_freq) -> str:
    """A short band tag the way the operator names cells (L1800, U900, G900)."""
    name = str(cell_name or "")
    pre = re.match(r"^([A-Za-z]+\d*)", name)
    pre = pre.group(1).upper() if pre else ""
    if tech == "2G":
        b = str(band_raw or "").upper()
        if "900" in b:
            return "G900"
        if "1800" in b or "DCS" in b:
            return "G1800"
        return b or "GSM"
    if tech == "3G":
        f = _num(dl_freq)
        if pre.startswith(("U9", "U09")) or f == 3088:
            return "U900"
        return "U2100"
    # 4G
    ea = _num(earfcn)
    if pre.startswith("L21") or ea in (300.0, 325.0):
        return "L2100"
    if pre.startswith(("L26", "L40", "L41")) or (np.isfinite(ea) and ea >= 39000):
        return "L2600(TDD)"
    if pre.startswith(("L9", "L09", "L8")) or ea in (3689.0, 3750.0):
        return "L900"
    if pre.startswith("L23") or (np.isfinite(ea) and 38650 <= ea < 39000):
        return "L2300(TDD)"
    return "L1800"


def _parse_balloon(text: str) -> dict:
    rec: dict = {"site_name": "", "site_id": "", "sector": np.nan,
                 "azimuth_deg": np.nan, "height_m": np.nan, "status": "",
                 "cells": [], "ret_actual": {}}
    tech: str | None = None
    cur: dict | None = None
    for raw_ln in text.split("\n"):
        s = raw_ln.strip()
        if not s:
            continue
        low = s.lower()

        m = re.search(r"site code:\s*([a-z0-9]+).*?sector:\s*(\d+)"
                      r".*?azimuth:\s*(-?\d+)", low)
        if m:
            rec["site_id"] = m.group(1).upper()
            rec["sector"] = int(m.group(2))
            rec["azimuth_deg"] = float(m.group(3))
            continue
        m = re.search(r"height:\s*([\d.]+)\s*m.*?status:\s*(.+)$", low)
        if m:
            rec["height_m"] = float(m.group(1))
            rec["status"] = m.group(2).strip().title()
            continue
        if not rec["site_name"] and "cell" not in low and "site code" not in low:
            rec["site_name"] = s
            continue

        if re.match(r"2g\b.*cell", low):
            tech, cur = "2G", None
            continue
        if re.match(r"3g\b.*cell", low):
            tech, cur = "3G", None
            continue
        if re.match(r"4g\b.*cell", low):
            tech, cur = "4G", None
            continue
        if low.startswith("ret actual tilt"):
            tech, cur = "RET", None
            continue

        if tech == "RET":
            m = re.match(r"band\s+(\S+)\s*:\s*([\d.]+)", low)
            if m:
                rec["ret_actual"].setdefault(m.group(1).upper(), []).append(
                    float(m.group(2)))
            continue

        if tech in ("2G", "3G", "4G"):
            m = re.match(r"([A-Za-z0-9_.\-/]+)\s*:\s*(.+)$", s)
            if m and "=" in m.group(2):
                cur = {"technology": tech, "cell_name": m.group(1).strip(),
                       **_kv(m.group(2))}
                rec["cells"].append(cur)
            elif cur is not None and "=" in s:
                cur.update(_kv(s))
    return rec


def _read_kml(path_or_buf) -> str:
    if hasattr(path_or_buf, "read"):
        try:
            path_or_buf.seek(0)
        except Exception:
            pass
        data = path_or_buf.read()
    else:
        data = Path(path_or_buf).read_bytes()
    if not isinstance(data, (bytes, bytearray)):
        return str(data)
    if bytes(data[:4]) == b"PK\x03\x04":                # a zip -> KMZ
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            kn = next((n for n in z.namelist() if n.lower().endswith(".kml")), None)
            if not kn:
                raise ValueError("KMZ archive has no .kml member")
            return z.read(kn).decode("utf-8", "replace")
    return data.decode("utf-8", "replace")


def _cell_row(cell: dict, sec: dict) -> dict:
    tech = cell["technology"]
    az = _num(cell.get("azimuth"))
    row = {
        "site_id": sec["site_id"], "site_name": sec["site_name"],
        "sector": sec["sector"], "sector_id": sec["sector_id"],
        "technology": tech, "cell_name": cell.get("cell_name", ""),
        "latitude": sec["lat"], "longitude": sec["lon"],
        "azimuth_deg": az if np.isfinite(az) else sec["azimuth_deg"],
        "status": (cell.get("status") or sec["status"]).title(),
        "band": cell.get("band", ""),
    }
    if tech == "2G":
        row.update(bcch=cell.get("bcch", ""), bsic=cell.get("bsic", ""),
                   tch=cell.get("tch", ""), antenna=cell.get("antenna", ""),
                   bsc=cell.get("bsc", ""), lac=cell.get("lac", ""),
                   elec_tilt_raw=_num(cell.get("electrical downtilt")))
    elif tech == "3G":
        row.update(sac=cell.get("sac", ""), psc=cell.get("dl psc", ""),
                   dl_freq=_num(cell.get("dl freq")),
                   cpich_power=_num(cell.get("power")),
                   elec_tilt_raw=_num(cell.get("tilt")))
    else:  # 4G
        row.update(pci=_num(cell.get("pci")), earfcn=_num(cell.get("earfcn")),
                   bw_mhz=_num(cell.get("bw")), tac=cell.get("tac", ""),
                   rs_power_raw=_num(cell.get("rs power")),
                   max_ret_raw=_num(cell.get("max ret")))
    if tech == "4G":
        rp = row["rs_power_raw"]
        row["rs_power_dbm"] = (rp / 10.0 if np.isfinite(rp) and rp > 40
                               else rp if np.isfinite(rp) and rp > 0 else np.nan)
        mr = row["max_ret_raw"]
        row["max_ret_deg"] = mr / 10.0 if np.isfinite(mr) else np.nan
        row["elec_tilt_deg"] = row["max_ret_deg"]        # RET is the 4G tilt
    else:
        row["elec_tilt_deg"] = _tilt_deg(row.get("elec_tilt_raw"))
    row["band_label"] = _band_label(row["cell_name"], tech, row.get("band"),
                                    row.get("earfcn"), row.get("dl_freq"))
    return row


def _parse(path_or_buf, region, prefixes) -> pd.DataFrame:
    kml = _read_kml(path_or_buf)
    rows: list[dict] = []

    for block in _PM_RE.finditer(kml):
        body = block.group(1)
        stm = _STYLE_RE.search(body)
        style = stm.group(1).lower() if stm else ""
        if style not in _STATUS_STYLE:
            continue                                   # tower / label placemark

        dm = _CDATA_RE.search(body)
        balloon = dm.group(1) if dm else None
        if balloon is None:
            dm = _DESC_RE.search(body)
            balloon = dm.group(1) if dm else None
        if not balloon:
            continue

        cm = _COORD_RE.search(body)
        lon = float(cm.group(1)) if cm else np.nan
        lat = float(cm.group(2)) if cm else np.nan

        rec = _parse_balloon(_clean(balloon))
        sid = rec["site_id"]
        if not sid:
            m = _SITE_RE.search(rec["site_name"])
            sid = m.group(1).upper() if m else ""
        if not sid:
            continue
        secn = int(rec["sector"]) if np.isfinite(rec["sector"]) else 0
        sector_id = f"{sid}-S{secn}"
        status = rec["status"] or _STATUS_STYLE[style]
        sec_ctx = {"site_id": sid, "site_name": rec["site_name"], "sector": secn,
                   "sector_id": sector_id, "lat": lat, "lon": lon,
                   "azimuth_deg": _num(rec["azimuth_deg"]), "status": status}

        by_tech = {"2G": 0, "3G": 0, "4G": 0}
        tilts: list[float] = []
        cell_rows: list[dict] = []
        for cell in rec["cells"]:
            crow = _cell_row(cell, sec_ctx)
            crow["_rt"] = "c"
            cell_rows.append(crow)
            by_tech[cell["technology"]] += 1
            t = crow.get("elec_tilt_deg", np.nan)
            if np.isfinite(t):
                tilts.append(t)

        ret = {k: float(np.mean(v)) / 10.0 for k, v in rec["ret_actual"].items()}
        ret_mean = float(np.mean(list(ret.values()))) if ret else np.nan
        sector_tilt = (float(np.nanmedian(tilts)) if tilts else ret_mean)
        # the sector's RET: the antenna's actual tilt readout, else the cells
        sector_ret = ret_mean if np.isfinite(ret_mean) else sector_tilt
        # per-cell RET: the cell's own tilt, else the sector's RET
        for crow in cell_rows:
            t = crow.get("elec_tilt_deg", np.nan)
            crow["ret_deg"] = t if np.isfinite(t) else sector_ret
        rows.extend(cell_rows)
        rows.append({
            "_rt": "s", "site_id": sid, "site_name": rec["site_name"],
            "sector": secn, "sector_id": sector_id,
            "latitude": lat, "longitude": lon,
            "azimuth_deg": _num(rec["azimuth_deg"]),
            "antenna_height_m": _num(rec["height_m"]),
            "elec_tilt_deg": sector_tilt, "status": status, "air": style,
            "n_2g": by_tech["2G"], "n_3g": by_tech["3G"], "n_4g": by_tech["4G"],
            "n_cells": sum(by_tech.values()),
            "ret_u900_deg": ret.get("U900", np.nan),
            "ret_u2100_deg": ret.get("U2100", np.nan),
            "ret_actual_deg": ret_mean,
            "ret_deg": sector_ret,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["_prefix"] = df["site_id"].str[:3].str.upper()
    if region and region.upper() == "R5":
        df = df[df["_prefix"].isin([p.upper() for p in prefixes])]
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------- #
def load_kmz_sites(path_or_buf, *, region: str | None = "R5",
                   prefixes: tuple[str, ...] = R5_PREFIXES,
                   use_cache: bool = True) -> KmzSites:
    """Parse ``R5_Sites.kmz`` (path or uploaded buffer) into a cell database."""
    def parser(src):
        return _parse(src, region, prefixes)

    df = (cached_parse(path_or_buf, parser,
                       tag=f"kmzsites_{(region or 'all').lower()}",
                       extra=_SCHEMA_SIG)
          if use_cache else parser(path_or_buf))

    if df is None or df.empty:
        return KmzSites(
            pd.DataFrame(columns=CELL_COLS), pd.DataFrame(columns=SECTOR_COLS),
            pd.DataFrame(columns=["site_id", "site_name", "latitude", "longitude",
                                  "n_sectors", "n_cells", "status"]),
            notes=["KMZ parsed to 0 R5 sectors"])

    cells = df[df["_rt"] == "c"].reindex(columns=CELL_COLS).reset_index(drop=True)
    sectors = df[df["_rt"] == "s"].reindex(columns=SECTOR_COLS).reset_index(drop=True)
    for c in ("n_2g", "n_3g", "n_4g", "n_cells", "sector"):
        sectors[c] = pd.to_numeric(sectors[c], errors="coerce").fillna(0).astype(int)
    cells["sector"] = pd.to_numeric(cells["sector"],
                                    errors="coerce").fillna(0).astype(int)

    sites = (sectors.groupby("site_id", as_index=False)
             .agg(site_name=("site_name", "first"),
                  latitude=("latitude", "mean"),
                  longitude=("longitude", "mean"),
                  n_sectors=("sector_id", "nunique"),
                  n_cells=("n_cells", "sum"),
                  status=("status", lambda s: s.mode().iat[0]
                          if len(s.mode()) else "")))

    notes = [f"{len(sites)} sites, {len(sectors)} sectors, {len(cells)} cells "
             f"(2G {int((cells['technology'] == '2G').sum())} / "
             f"3G {int((cells['technology'] == '3G').sum())} / "
             f"4G {int((cells['technology'] == '4G').sum())})"]
    return KmzSites(cells=cells, sectors=sectors, sites=sites, notes=notes)
