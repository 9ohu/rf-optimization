#!/usr/bin/env python3
r"""
build_customer_kmz.py  --  regenerate the "R5 Sites - Full Visualization" KMZ
that goes to the customer (Asiacell).

Two inputs, each supplying a different half of the picture:

  1. --arrows   the sector-arrows KMZ you maintain in Google Earth
                (e.g. Desktop\KMZ_20260909U_v2.kmz).
                -> site LOCATION, number of SECTORS + their azimuth,
                   and On Air (blue arrow) vs Planned (red arrow).

  2. --tracker  the weekly WeLink "Engineering Parameter Tracker" .xlsx
                (e.g. WK37 Engineering Parameter Tracker-06092026.xlsx).
                -> the CELL DETAILS in every sector balloon
                   (2G/3G/4G cells + RET), joined by Site-Sector key.

Status rules (as agreed):
  * "ARB" anywhere in the site name          -> Off Air
  * red arrows  (RSectors\ icon)             -> Planned
  * blue arrows (BSectors\ icon)             -> On Air
  * in the tracker but NOT in the arrows KMZ -> Off Air  (site retired;
        its cells still come from the tracker's *Deactive* sheets)

Output: a single .kmz (zipped doc.kml) with six folders -
  On Air / Planned / Off Air sectors  (3 dB main-lobe footprints)
  3D coverage beams                   (antenna -> ground, off by default)
  Telecom Towers (3D lattice)         (real-height lattice masts)
  Site Names
plus  <out>.review.csv  listing everything a human should eyeball.

Usage
-----
    .venv\Scripts\python.exe scripts\build_customer_kmz.py \
        --arrows  "C:\Users\swx1351646\Desktop\KMZ_20260909U_v2.kmz" \
        --tracker "D:\WeLink_data_files\swx1351646\ReceiveFiles\WK37 Engineering Parameter Tracker-06092026.xlsx" \
        --out     "C:\Users\swx1351646\Desktop\R5_Sites_Customer.kmz"

Only the standard library + pandas are required (pandas is already in the
project venv; it uses python-calamine to read the big workbook in ~15 s).
"""
from __future__ import annotations

import argparse
import csv
import math
import re
import sys
import zipfile
from collections import defaultdict
from datetime import date
from html import escape
from pathlib import Path

import pandas as pd

R5_PREFIXES = ("BAS", "NAS", "EMA", "SAM")

# Name shown in Google Earth's Places panel (strftime pattern; the date is
# the day the KMZ was generated).
KMZ_NAME_FORMAT = "R5-Sites-%d-%m-%Y"

R_EARTH = 6_371_000.0
M_PER_DEG = 111_320.0

# --- antenna / coverage model -------------------------------------------- #
HBW_DEG = 65.0            # horizontal half-power beamwidth of a panel antenna
VBW_DEG = 6.5             # vertical half-power beamwidth
FRONT_BACK_DB = 25.0      # horizontal pattern floor (3GPP Am)
LOBE_STEPS = 16           # points per half of the main-lobe outline
DEFAULT_TILT_DEG = 3.0    # used when the tracker has no tilt for the sector
LOBE_MIN_M, LOBE_MAX_M = 150.0, 800.0     # display clamp on the drawn reach

# --- 3D tower ------------------------------------------------------------- #
TOWER_SCALE = 1.0         # 1.0 = true antenna height (raise if you want them taller)
TOWER_PANELS = 6          # lattice bays between ground and the platform
AVIATION_BAND_MIN_M = 30  # towers this tall get the red/white banding

# KML colours are aabbggrr
C_STEEL = "ff8c8c8c"
C_AVIATION_RED = "ff2222dc"
C_AVIATION_WHITE = "fff2f2f2"
C_ANTENNA = "ffdcdcdc"
C_SHELTER_LINE = "ff555555"
C_SHELTER_FILL = "ffcfcfcf"
C_DISH = "ffe0e0e0"

STYLE_BLOCK = """  <Style id="onair">
    <IconStyle><scale>0</scale></IconStyle>
    <LabelStyle><scale>0</scale></LabelStyle>
    <LineStyle><color>c8d08a3c</color><width>1.4</width></LineStyle>
    <PolyStyle><color>7ae6791e</color><outline>1</outline></PolyStyle>
  </Style>
  <Style id="onair_core">
    <IconStyle><scale>0</scale></IconStyle>
    <LabelStyle><scale>0</scale></LabelStyle>
    <LineStyle><color>00000000</color><width>0</width></LineStyle>
    <PolyStyle><color>78f0a03c</color><outline>0</outline></PolyStyle>
  </Style>
  <Style id="planned">
    <IconStyle><scale>0</scale></IconStyle>
    <LabelStyle><scale>0</scale></LabelStyle>
    <LineStyle><color>c83232c8</color><width>1.4</width></LineStyle>
    <PolyStyle><color>7a3c3ce6</color><outline>1</outline></PolyStyle>
  </Style>
  <Style id="planned_core">
    <IconStyle><scale>0</scale></IconStyle>
    <LabelStyle><scale>0</scale></LabelStyle>
    <LineStyle><color>00000000</color><width>0</width></LineStyle>
    <PolyStyle><color>785a5af0</color><outline>0</outline></PolyStyle>
  </Style>
  <Style id="offair">
    <IconStyle><scale>0</scale></IconStyle>
    <LabelStyle><scale>0</scale></LabelStyle>
    <LineStyle><color>b4707070</color><width>1.2</width></LineStyle>
    <PolyStyle><color>6e9b9b9b</color><outline>1</outline></PolyStyle>
  </Style>
  <Style id="offair_core">
    <IconStyle><scale>0</scale></IconStyle>
    <LabelStyle><scale>0</scale></LabelStyle>
    <LineStyle><color>00000000</color><width>0</width></LineStyle>
    <PolyStyle><color>6eb4b4b4</color><outline>0</outline></PolyStyle>
  </Style>
  <Style id="boresight">
    <IconStyle><scale>0</scale></IconStyle>
    <LabelStyle><scale>0</scale></LabelStyle>
    <LineStyle><color>c8ffffff</color><width>1.6</width></LineStyle>
  </Style>
  <Style id="sitelabel">
    <IconStyle>
      <scale>0.5</scale>
      <Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href></Icon>
      <color>ff222222</color>
    </IconStyle>
    <LabelStyle><scale>1.0</scale><color>ffffffff</color></LabelStyle>
  </Style>
  <Style id="tower_steel">
    <LineStyle><color>%s</color><width>1.6</width></LineStyle>
    <PolyStyle><color>%s</color><outline>1</outline></PolyStyle>
  </Style>
  <Style id="tower_red">
    <LineStyle><color>%s</color><width>2.0</width></LineStyle>
    <PolyStyle><color>%s</color><outline>1</outline></PolyStyle>
  </Style>
  <Style id="tower_white">
    <LineStyle><color>%s</color><width>2.0</width></LineStyle>
    <PolyStyle><color>%s</color><outline>1</outline></PolyStyle>
  </Style>
  <Style id="tower_antenna">
    <LineStyle><color>ff707070</color><width>1.1</width></LineStyle>
    <PolyStyle><color>%s</color><outline>1</outline></PolyStyle>
  </Style>
  <Style id="tower_hut">
    <LineStyle><color>%s</color><width>1</width></LineStyle>
    <PolyStyle><color>%s</color><outline>1</outline></PolyStyle>
  </Style>
  <Style id="tower_dish">
    <LineStyle><color>ff606060</color><width>1.1</width></LineStyle>
    <PolyStyle><color>%s</color><outline>1</outline></PolyStyle>
  </Style>
  <Style id="tower_beacon">
    <LineStyle><color>ff1414ff</color><width>3.2</width></LineStyle>
  </Style>""" % (C_STEEL, C_STEEL, C_AVIATION_RED, C_AVIATION_RED,
                 C_AVIATION_WHITE, C_AVIATION_WHITE, C_ANTENNA,
                 C_SHELTER_LINE, C_SHELTER_FILL, C_DISH)


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def s(v) -> str:
    """tracker cell -> clean string ('' for NaN/None/'nan')."""
    if v is None:
        return ""
    t = str(v).strip()
    return "" if t.lower() in ("nan", "none", "nat", "null") else t


def num(v):
    t = s(v)
    if not t:
        return None
    m = re.search(r"-?\d+\.?\d*", t.replace(",", "."))
    return float(m.group()) if m else None


def fmt_h(h) -> str:
    if h is None:
        return ""
    return f"{h:g}"


def dest_point(lat, lon, bearing_deg, dist_m):
    br = math.radians(bearing_deg)
    dlat = dist_m * math.cos(br) / R_EARTH * (180 / math.pi)
    dlon = dist_m * math.sin(br) / (R_EARTH * math.cos(math.radians(lat))) * (180 / math.pi)
    return lon + dlon, lat + dlat


def _h_pattern(off_deg: float) -> float:
    """3GPP horizontal panel pattern as a power ratio (1.0 on boresight).

    A_h(x) = -min(12 (x/HBW)^2, Am) dB  ->  10 ** (A_h / 10)

    Power, not voltage: it gives the narrow teardrop a 65 deg sector actually
    paints on the ground - half the reach at the +-32.5 deg 3 dB points and
    pinched to nothing by +-90 - instead of the near-circular blob you get
    from the voltage ratio.
    """
    loss_db = min(12.0 * (off_deg / HBW_DEG) ** 2, FRONT_BACK_DB)
    return 10.0 ** (-loss_db / 10.0)


def coverage_reach_m(height_m, total_tilt_deg) -> float:
    """How far the sector is drawn.

    Anchored on the boresight ground distance ``h / tan(tilt)`` - where the
    centre of the main lobe lands - stretched a little toward the far 3 dB
    edge. Using the 3 dB edge directly is useless here: at the 2-4 deg tilts
    most of R5 runs, the far edge sits above the horizon and every lobe would
    hit the same cap.
    """
    h = max((height_m or 30.0) - 1.5, 1.0)
    bore = h / math.tan(math.radians(max(total_tilt_deg, 0.4)))
    return max(LOBE_MIN_M, min(bore * 1.35, LOBE_MAX_M))


def lobe_coords(lat, lon, az, reach_m, *, scale=1.0, alt=None) -> str:
    """Main-lobe footprint: a pattern-shaped petal, not a pie slice."""
    z = "0" if alt is None else f"{alt:.1f}"
    pts = [(lon, lat)]
    for i in range(-LOBE_STEPS, LOBE_STEPS + 1):
        off = 90.0 * i / LOBE_STEPS
        r = reach_m * scale * _h_pattern(off)
        if r < 1.0:
            continue
        pts.append(dest_point(lat, lon, az + off, r))
    pts.append((lon, lat))
    return " ".join(f"{x:.6f},{y:.6f},{z}" for x, y in pts)


def beam_coords(lat, lon, az, reach_m, ant_h) -> str:
    """The 3D beam itself: a fan from the antenna on the tower down to the
    outer edge of the footprint - reads as a coverage beam in Google Earth."""
    apex = f"{lon:.6f},{lat:.6f},{ant_h:.1f}"
    pts = [apex]
    for i in range(-LOBE_STEPS, LOBE_STEPS + 1, 2):
        off = 90.0 * i / LOBE_STEPS
        r = reach_m * _h_pattern(off)
        if r < 1.0:
            continue
        x, y = dest_point(lat, lon, az + off, r)
        pts.append(f"{x:.6f},{y:.6f},0")
    pts.append(apex)
    return " ".join(pts)


def boresight_coords(lat, lon, az, reach_m, ant_h) -> str:
    """A thin line down the boresight - shows exactly where the sector points."""
    x, y = dest_point(lat, lon, az, reach_m)
    return f"{lon:.6f},{lat:.6f},{ant_h:.1f} {x:.6f},{y:.6f},0"


def site_id_from_name(name: str) -> str:
    """Last '_'-token that looks like a site code wins (handles the mis-keyed
    'Rifia102_NAS0666' rows in the arrows KMZ)."""
    cands = re.findall(r"([A-Za-z]{2,4}\d{3,5})", name or "")
    return cands[-1].upper() if cands else ""


def clean_site_name(raw: str, site_id: str) -> str:
    """'38m_Alhuda_EMA3658' -> 'Alhuda_EMA3658'  (drop the leading height token,
    and the stray 'nannan' prefix that some tracker Deactive rows carry)."""
    n = (raw or "").strip().replace("\xa0", " ").strip()
    n = re.sub(r"^(?:nan)+", "", n, flags=re.I)
    n = re.sub(r"^\s*\d+(?:\.\d+)?\s*m\s*[_\-\s]+", "", n, flags=re.I)
    n = re.sub(r"^X_(?=ARB)", "", n)
    n = re.sub(r"\s*-+\s*Currently\s+off\s*air.*$", "", n, flags=re.I)
    n = n.strip(" _-")
    if site_id and site_id not in n.upper():
        n = f"{n}_{site_id}" if n else site_id
    return n or site_id


def height_from_name(raw: str):
    m = re.search(r"(\d+(?:\.\d+)?)\s*m(?![A-Za-z])", raw or "", re.I)
    return float(m.group(1)) if m else None


# --------------------------------------------------------------------------- #
# 1. the sector-arrows KMZ  ->  geometry
# --------------------------------------------------------------------------- #
def read_kml_text(path: Path) -> str:
    data = path.read_bytes()
    if data[:4] == b"PK\x03\x04":                       # real zipped KMZ
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".kml"))
            data = z.read(name)
    if data[:2] == b"\xff\xfe":
        return data.decode("utf-16-le")
    if data[:2] == b"\xfe\xff":
        return data.decode("utf-16-be")
    if data[:3] == b"\xef\xbb\xbf":
        return data.decode("utf-8-sig")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def parse_arrows(path: Path) -> dict:
    """site_id -> {name, lat, lon, height_m, family, azimuths[list], n_arrows}."""
    txt = read_kml_text(path)
    sites: dict[str, dict] = {}
    cur: dict | None = None
    for block in re.finditer(r"<Placemark>(.*?)</Placemark>", txt, re.S):
        body = block.group(1)
        nm = re.search(r"<name>(.*?)</name>", body, re.S)
        nm = (nm.group(1).strip() if nm else "").replace("\xa0", " ")
        style = re.search(r"<styleUrl>\s*#?([Yy]?\d+)\s*</styleUrl>", body)
        style = style.group(1) if style else ""
        cm = re.search(r"<coordinates>\s*([-\d.]+)\s*,\s*([-\d.]+)", body)
        lon = float(cm.group(1)) if cm else None
        lat = float(cm.group(2)) if cm else None

        sid = site_id_from_name(nm)
        if sid:
            cur = sites.setdefault(sid, dict(
                name=clean_site_name(nm, sid), raw_name=nm,
                lat=lat, lon=lon, height_m=height_from_name(nm),
                family=set(), azimuths=[], n_arrows=0))
            if cur["height_m"] is None:
                cur["height_m"] = height_from_name(nm)
        if cur is None:
            continue
        cur["n_arrows"] += 1
        fam = "R" if style[:1] in ("y", "Y") else "B"
        cur["family"].add(fam)
        az = num(style.lstrip("yY"))
        if az is not None:
            cur["azimuths"].append(int(round(az)) % 360)
        if cur["height_m"] is None:
            cur["height_m"] = height_from_name(nm)
        # keep the first real (non-placeholder) coordinate we see for the site
        if lat is not None and not _is_placeholder(lat, lon) and _is_placeholder(cur["lat"], cur["lon"]):
            cur["lat"], cur["lon"] = lat, lon

    for v in sites.values():
        v["azimuths"] = dedupe_azimuths(v["azimuths"])
    return sites


def _in_r5(lat, lon) -> bool:
    """southern Iraq bounding box - rejects gross coordinate typos."""
    return lat is not None and lon is not None and 29.0 <= lat <= 33.2 and 43.8 <= lon <= 49.2


def _is_placeholder(lat, lon) -> bool:
    if lat is None or lon is None:
        return True
    if abs(lat) < 1 and abs(lon) < 1:
        return True
    if (round(lat, 3), round(lon, 3)) in {(30.0, 47.0), (47.0, 30.0)}:
        return True
    return not _in_r5(lat, lon)


def dedupe_azimuths(azes: list[int], tol: int = 15) -> list[int]:
    """merge arrows that point within `tol` degrees (bands a few deg apart)."""
    if not azes:
        return []
    out: list[int] = []
    for a in sorted(set(azes)):
        if out and min((a - out[-1]) % 360, (out[-1] - a) % 360) <= tol:
            continue
        out.append(a)
    # wrap-around merge (e.g. 350 and 5)
    if len(out) > 1 and (out[0] - out[-1]) % 360 <= tol:
        out.pop()
    return out


# --------------------------------------------------------------------------- #
# 2. the Engineering Parameter Tracker  ->  cell details per Site-Sector
# --------------------------------------------------------------------------- #
def _sector_num(v) -> int | None:
    m = re.search(r"S?(\d+)\s*$", s(v))
    return int(m.group(1)) if m else None


def load_tracker(path: Path) -> dict:
    """site_id -> {sector_num -> dict(az, gsm[], umts[], lte[], ret[], active)}."""
    xl = pd.ExcelFile(path)                              # calamine engine

    def sheet(name):
        return (pd.read_excel(xl, sheet_name=name, dtype=str)
                if name in xl.sheet_names else pd.DataFrame())

    data: dict[str, dict] = defaultdict(lambda: defaultdict(
        lambda: dict(az=None, az_by={"lte": [], "gsm": [], "umts": []},
                     tilt=None, tilt_by={"lte": [], "gsm": [], "umts": []},
                     gsm=[], umts=[], lte=[], ret=[], active=False)))

    def sec(site_id, n):
        return data[site_id][n]

    def note_az(rec, tech, az):
        a = num(az)
        if a is not None:
            rec["az_by"][tech].append(int(round(a)) % 360)

    def note_tilt(rec, tech, mech, elec_tenths):
        """total downtilt = mechanical + electrical (the tracker stores the
        electrical part in 0.1 deg units)."""
        m = num(mech) or 0.0
        e = num(elec_tenths)
        if e is None:
            return
        e = e / 10.0 if e > 20 else e
        t = m + e
        if 0.0 <= t <= 20.0:
            rec["tilt_by"][tech].append(t)

    # ---- GSM (active + deactive) ----
    for sheet_name, live in (("GSM", True), ("GSM Deactive", False)):
        df = sheet(sheet_name)
        for d in df.to_dict("records"):
            sid = s(d.get("Site Code")).upper()
            if sid[:3] not in R5_PREFIXES:
                continue
            n = _sector_num(d.get("Sector"))
            if n is None:
                continue
            rec = sec(sid, n)
            note_az(rec, "gsm", d.get("Azimuth"))
            note_tilt(rec, "gsm", d.get("Mechanical Downtilt"),
                      d.get("Electrical Downtilt"))
            st = s(d.get("Status")) or ("ACTIVATED" if live else "DEACTIVATED")
            rec["active"] |= live and st.upper().startswith(("ACT", "ON"))
            rec["gsm"].append(dict(
                cell=s(d.get("Cell Name")), band=s(d.get("Frequency Band(*")),
                bcch=s(d.get("BCCH")), bsic=s(d.get("BSIC")), tch=s(d.get("TCH")),
                etilt=s(d.get("Electrical Downtilt")), ant=s(d.get("Antenna Model")),
                bsc=s(d.get("BSC Name")), lac=s(d.get("LAC")), status=st,
                az=num(d.get("Azimuth"))))

    # ---- UMTS (active + deactive) ----
    for sheet_name, live in (("UMTS", True), ("UMTS Deactive", False)):
        df = sheet(sheet_name)
        for d in df.to_dict("records"):
            sid = s(d.get("Site")).upper()
            if sid[:3] not in R5_PREFIXES:
                continue
            n = _sector_num(d.get("Site-Sector")) or _sector_num(d.get("Sector"))
            if n is None:
                continue
            rec = sec(sid, n)
            note_az(rec, "umts", d.get("Azimuth"))
            note_tilt(rec, "umts", d.get("Mechanical Downtilt"),
                      d.get("Actual Tilt(0.1degree)"))
            st = s(d.get("Status")) or ("ACTIVATED" if live else "DEACTIVATED")
            rec["active"] |= live and st.upper().startswith(("ACT", "ON"))
            rec["umts"].append(dict(
                cell=s(d.get("CellName")), tilt=s(d.get("Electrical Downtilt")),
                power=s(d.get("MaxPower")), sac=s(d.get("SAC")),
                psc=s(d.get("DL Primary Scrambling Code")), dlfreq=s(d.get("DlFreq")),
                status=st, az=num(d.get("Azimuth"))))

    # ---- LTE (active + deactive) ----
    for sheet_name, live in (("LTE", True), ("LTE Deactive", False)):
        df = sheet(sheet_name)
        for d in df.to_dict("records"):
            sid = s(d.get("SITE ID")).upper()
            if sid[:3] not in R5_PREFIXES:
                continue
            n = _sector_num(d.get("SECTOR NAME")) or _sector_num(d.get("SECTOR"))
            if n is None:
                continue
            rec = sec(sid, n)
            note_az(rec, "lte", d.get("AZIMUTH"))
            note_tilt(rec, "lte", d.get("M-DOWNTILT"), d.get("MAX RET"))
            st = s(d.get("ACTIVATION STATUS")) or ("Active" if live else "Inactive")
            rec["active"] |= live and st.upper().startswith(("ACT", "ON"))
            rec["lte"].append(dict(
                cell=s(d.get("CELL NAME")), pci=s(d.get("*PHYSICAL CELL ID")),
                earfcn=s(d.get("DOWNLINK EARFCN")), bw=s(d.get("BANDWIDTH")),
                tac=s(d.get("TAC")), rspower=s(d.get("RS POWER")),
                maxret=s(d.get("MAX RET")), status=st, az=num(d.get("AZIMUTH"))))

    # ---- RET ----
    ret_df = sheet("RET")
    for d in ret_df.to_dict("records"):
        code = re.search(r"([A-Za-z]{2,4}\d{3,5})", s(d.get("NAME")))
        if not code:
            continue
        sid = code.group(1).upper()
        if sid[:3] not in R5_PREFIXES:
            continue
        n = _sector_num(d.get("Sector"))
        if n is None:
            continue
        data[sid][n]["ret"].append((s(d.get("Band")), s(d.get("Actual Tilt(0.1degree)"))))

    # site coords / name / height from the tracker (fallback for retired sites).
    # GSM/UMTS names are the clean ones; the LTE sheets carry a stray 'nannan'
    # prefix on some rows, so they go last.  Coordinates are collected from every
    # sheet and reduced to a median, because individual rows carry gross typos
    # (e.g. one GSM row puts a Basra site near Baghdad).
    meta: dict[str, dict] = {}
    coord_cands: dict[str, list] = defaultdict(list)
    for sheet_name, sc, nc, la, lo, hc in (
        ("GSM", "Site Code", "Site Name", "Latitude", "Longitude", "Height"),
        ("UMTS", "Site", "NodeBName", "Latitude", "Longitude", "Height"),
        ("LTE", "SITE ID", "*ENODEB NAME", "LATITUDE", "LONGITUDE", "GROUDHEIGHT"),
        ("GSM Deactive", "Site Code", "Site Name", "Latitude", "Longitude", "Height"),
        ("UMTS Deactive", "Site", "NodeBName", "Latitude", "Longitude", "Height"),
        ("LTE Deactive", "SITE ID", "*ENODEB NAME", "LATITUDE", "LONGITUDE", "GROUDHEIGHT"),
    ):
        df = sheet(sheet_name)
        if df.empty:
            continue
        for d in df.to_dict("records"):
            sid = s(d.get(sc)).upper()
            if sid[:3] not in R5_PREFIXES:
                continue
            m = meta.setdefault(sid, dict(coord=None, name="", height=None))
            y, x = num(d.get(la)), num(d.get(lo))
            if y and x:
                if 43 < y < 50 and 28 < x < 34:          # lat/lon swapped
                    y, x = x, y
                if _in_r5(y, x):
                    coord_cands[sid].append((y, x))
            nm = re.sub(r"^(?:nan)+", "", s(d.get(nc)), flags=re.I).strip(" _-")
            if nm and not m["name"]:
                m["name"] = nm
            if m["height"] is None and num(d.get(hc)):
                m["height"] = num(d.get(hc))

    for sid, pts in coord_cands.items():
        pts.sort()
        mid = pts[len(pts) // 2]
        meta[sid]["coord"] = mid

    # resolve each sector's azimuth: LTE wins, then GSM, then UMTS; 0 = "not set"
    for secs in data.values():
        n_sec = len(secs)
        for idx, n in enumerate(sorted(secs)):
            rec = secs[n]
            chosen = None
            for tech in ("lte", "gsm", "umts"):
                nz = [a for a in rec["az_by"][tech] if a]
                if nz:
                    chosen = max(set(nz), key=nz.count)
                    break
            if chosen is None:                       # every tech said 0 / nothing
                allv = [a for lst in rec["az_by"].values() for a in lst]
                chosen = 0 if allv else round((idx * 360 / n_sec)) % 360
            rec["az"] = chosen

            tilt = None
            for tech in ("lte", "gsm", "umts"):
                vals = [t for t in rec["tilt_by"][tech] if t > 0]
                if vals:
                    tilt = sum(vals) / len(vals)
                    break
            rec["tilt"] = round(tilt, 1) if tilt else DEFAULT_TILT_DEG

    return {sid: dict(sectors=dict(secs),
                      coord=meta.get(sid, {}).get("coord"),
                      name=meta.get(sid, {}).get("name", ""),
                      height=meta.get(sid, {}).get("height"))
            for sid, secs in data.items()}


# --------------------------------------------------------------------------- #
# 3. balloons
# --------------------------------------------------------------------------- #
def balloon(site_name, site_id, sector_n, az, height_m, status, rec,
            *, tilt=None, reach_m=None) -> str:
    ht = f"{fmt_h(height_m)} m" if height_m else "n/a"
    p = [f"<b>{escape(site_name)}</b><br/>Site Code: {escape(site_id)} &nbsp; "
         f"Sector: {sector_n} &nbsp; Azimuth: {az:.0f}&deg;<br/>"
         f"Height: {ht} &nbsp; Status: {escape(status)}<br/>"]
    if tilt is not None:
        p.append(f"Total downtilt: {tilt:.1f}&deg; &nbsp; "
                 f"Modelled 3 dB reach: {reach_m:.0f} m<br/>")
    p.append("<br/>")

    p.append("<b>2G (GSM) Cells</b><br/>")
    gsm = _dedupe(rec.get("gsm", []) if rec else [])
    if gsm:
        for c in gsm:
            a = c["az"] if c["az"] is not None else az
            p.append(f"<u>{escape(c['cell'])}</u>: Azimuth={a:.0f}&deg;, Band={escape(c['band'])}, "
                     f"BCCH={escape(c['bcch'])}, BSIC={escape(c['bsic'])}, TCH={escape(c['tch'])}<br/>"
                     f"&nbsp;&nbsp;Electrical Downtilt={escape(c['etilt'])}, Power=n/a (not tracked), "
                     f"Antenna={escape(c['ant'])}<br/>"
                     f"&nbsp;&nbsp;BSC={escape(c['bsc'])}, LAC={escape(c['lac'])}, "
                     f"Status={escape(c['status'])}<br/>")
    else:
        p.append("No GSM record found.<br/>")

    p.append("<br/><b>3G (UMTS) Cells</b><br/>")
    umts = _dedupe(rec.get("umts", []) if rec else [])
    if umts:
        for c in umts:
            a = c["az"] if c["az"] is not None else az
            p.append(f"<u>{escape(c['cell'])}</u>: Azimuth={a:.0f}&deg;, Tilt={escape(c['tilt'])}, "
                     f"Power={escape(c['power'])}, SAC={escape(c['sac'])}, DL PSC={escape(c['psc'])}, "
                     f"DL Freq={escape(c['dlfreq'])}, Status={escape(c['status'])}<br/>")
    else:
        p.append("No UMTS cell found.<br/>")

    p.append("<br/><b>4G (LTE) Cells</b><br/>")
    lte = _dedupe(rec.get("lte", []) if rec else [])
    if lte:
        for c in lte:
            a = c["az"] if c["az"] is not None else az
            p.append(f"<u>{escape(c['cell'])}</u>: Azimuth={a:.0f}&deg;, PCI={escape(c['pci'])}, "
                     f"EARFCN={escape(c['earfcn'])}, BW={escape(c['bw'])}, TAC={escape(c['tac'])}, "
                     f"RS Power={escape(c['rspower'])}, MAX RET={escape(c['maxret'])}, "
                     f"Status={escape(c['status'])}<br/>")
    else:
        p.append("No LTE cell found.<br/>")

    p.append("<br/><b>RET Actual Tilt</b><br/>")
    ret = rec.get("ret", []) if rec else []
    seen = set()
    wrote = False
    for band, tilt in ret:
        key = (band, tilt)
        if key in seen:
            continue
        seen.add(key)
        wrote = True
        p.append(f"Band {escape(band)}: {escape(tilt)} (0.1&deg; units)<br/>")
    if not wrote:
        p.append("No RET record found.<br/>")
    return "".join(p)


def _dedupe(cells: list[dict]) -> list[dict]:
    out, seen = [], set()
    for c in cells:
        key = tuple(sorted(c.items(), key=lambda kv: kv[0]))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


# --------------------------------------------------------------------------- #
# 4. tower + label geometry
# --------------------------------------------------------------------------- #
def _m2deg(lat):
    return M_PER_DEG, M_PER_DEG * math.cos(math.radians(lat))


def tower_placemarks(lat, lon, height_m, azimuths=(), name="") -> list[str]:
    """A tapered 4-leg self-support lattice mast drawn at its real height.

    Legs taper from a wide footing to a narrow top, every bay is cross-braced,
    the platform carries one panel antenna per sector at its true azimuth, and
    masts at or above AVIATION_BAND_MIN_M get red/white aviation banding plus a
    beacon. An equipment shelter and a microwave dish complete the compound.
    """
    h = (height_m or 30.0) * TOWER_SCALE
    mlat, mlon = _m2deg(lat)
    w0 = min(max(h * 0.10, 1.8), 5.0)          # half-width at the footing
    w1 = min(max(h * 0.028, 0.6), 1.6)         # half-width at the platform
    n = TOWER_PANELS

    def P(dx, dy, zz):
        return f"{lon + dx / mlon:.6f},{lat + dy / mlat:.6f},{zz:.1f}"

    def line(pts):
        return ("<LineString><altitudeMode>relativeToGround</altitudeMode>"
                "<coordinates>" + " ".join(P(*p) for p in pts) +
                "</coordinates></LineString>")

    def poly(pts, extrude=0):
        return (f"<Polygon><extrude>{extrude}</extrude>"
                "<altitudeMode>relativeToGround</altitudeMode><outerBoundaryIs>"
                "<LinearRing><coordinates>" + " ".join(P(*p) for p in pts) +
                "</coordinates></LinearRing></outerBoundaryIs></Polygon>")

    def wid(k):                                  # half-width at bay boundary k
        return w0 + (w1 - w0) * (k / n)

    def zed(k):
        return h * k / n

    corners = ((1, 1), (1, -1), (-1, -1), (-1, 1))
    banded = h >= AVIATION_BAND_MIN_M
    steel, red, white = [], [], []

    # four legs, each one continuous tapering polyline over the whole height
    for sx, sy in corners:
        steel.append(line([(sx * wid(k), sy * wid(k), zed(k)) for k in range(n + 1)]))

    # zigzag (V) bracing: one polyline per face, bottom to top
    for i in range(4):
        ax, ay = corners[i]
        bx, by = corners[(i + 1) % 4]
        pts = []
        for k in range(n + 1):
            w, z = wid(k), zed(k)
            cx, cy = (ax, ay) if k % 2 == 0 else (bx, by)
            pts.append((cx * w, cy * w, z))
        steel.append(line(pts))

    # horizontal belts - these carry the aviation banding
    for k in range(n + 1):
        w, z = wid(k), zed(k)
        ring = line([(x * w, y * w, z) for x, y in corners]
                    + [(corners[0][0] * w, corners[0][1] * w, z)])
        (steel if not banded else (white if k % 2 else red)).append(ring)

    pw = w1 * 1.9                                 # antenna platform
    steel.append(line([(x * pw, y * pw, h) for x, y in corners]
                      + [(corners[0][0] * pw, corners[0][1] * pw, h)]))
    mast_top = h + max(2.0, h * 0.09)
    steel.append(line([(0, 0, h), (0, 0, mast_top)]))

    # panel antennas on the platform, one per sector, at the true azimuth
    ants = []
    for az in (azimuths or ()):
        br = math.radians(az)
        ux, uy = math.sin(br), math.cos(br)      # outward
        px, py = math.cos(br), -math.sin(br)     # across the face
        cx, cy = ux * pw * 1.15, uy * pw * 1.15
        hwp, ztop, zbot = 0.32, h + 1.5, h - 0.5
        ants.append(poly([(cx + px * hwp, cy + py * hwp, zbot),
                          (cx - px * hwp, cy - py * hwp, zbot),
                          (cx - px * hwp, cy - py * hwp, ztop),
                          (cx + px * hwp, cy + py * hwp, ztop),
                          (cx + px * hwp, cy + py * hwp, zbot)]))

    # microwave dish, hung off one face at ~62 % height
    dz = h * 0.62
    dw = wid(int(n * 0.62))
    dr, dbr = min(max(h * 0.035, 0.7), 1.6), math.radians(135)
    dcx, dcy = math.sin(dbr) * (dw + dr * 0.8), math.cos(dbr) * (dw + dr * 0.8)
    dpx, dpy = math.cos(dbr), -math.sin(dbr)
    dish = poly([(dcx + dpx * dr * math.cos(t), dcy + dpy * dr * math.cos(t),
                  dz + dr * math.sin(t))
                 for t in [math.radians(a) for a in range(0, 361, 40)]])

    # equipment shelter, offset from the footing like a real compound
    sx0, sy0, sw, sl = w0 + 3.2, -w0 - 1.0, 2.4, 3.2
    hut = poly([(sx0 - sw, sy0 - sl, 2.6), (sx0 + sw, sy0 - sl, 2.6),
                (sx0 + sw, sy0 + sl, 2.6), (sx0 - sw, sy0 + sl, 2.6),
                (sx0 - sw, sy0 - sl, 2.6)], extrude=1)

    def pm(style, geom, desc=""):
        d = (f"      <description><![CDATA[{desc}]]></description>\n" if desc else "")
        return (f"    <Placemark>\n      <name></name>\n{d}"
                f"      <styleUrl>#{style}</styleUrl>\n      {geom}\n    </Placemark>")

    info = (f"<b>{escape(name)}</b><br/>Antenna height: {h / TOWER_SCALE:.0f} m"
            f"<br/>Sectors: {len(azimuths or ())}") if name else ""
    out = []
    if steel:
        out.append(pm("tower_steel", "<MultiGeometry>" + "".join(steel) + "</MultiGeometry>", info))
    if red:
        out.append(pm("tower_red", "<MultiGeometry>" + "".join(red) + "</MultiGeometry>"))
    if white:
        out.append(pm("tower_white", "<MultiGeometry>" + "".join(white) + "</MultiGeometry>"))
    if ants:
        out.append(pm("tower_antenna", "<MultiGeometry>" + "".join(ants) + "</MultiGeometry>"))
    out.append(pm("tower_dish", dish))
    out.append(pm("tower_hut", hut))
    if banded:
        out.append(pm("tower_beacon",
                      line([(0, 0, mast_top - max(0.8, h * 0.02)), (0, 0, mast_top)])))
    return out


# --------------------------------------------------------------------------- #
# 5. assemble
# --------------------------------------------------------------------------- #
STATUS_STYLE = {"On Air": "onair", "Planned": "planned", "Off Air": "offair"}


def _is_arb(name: str) -> bool:
    """the operator's 'ARB...' border-road sites (ARBShebBRDR, X_ARBBtairaRD1);
    NOT place names that merely contain the letters (Marbad, Garbi, Arbatalaf)."""
    return bool(re.search(r"(?:^|_)ARB[A-Z]", name or ""))


def classify(sid: str, arrow: dict | None, trec: dict | None) -> tuple[str, str]:
    """-> (status, reason)."""
    name = (arrow["name"] if arrow else (trec or {}).get("name", "")) or sid
    tracker_active = bool(trec) and any(s["active"] for s in trec["sectors"].values())
    tracker_dead = bool(trec) and not tracker_active   # in tracker, every cell off

    if _is_arb(name):
        return "Off Air", "ARB border-road site"
    if tracker_dead:
        return "Off Air", "every cell is deactivated in the tracker"
    if arrow is not None:
        if arrow["family"] == {"R"}:
            return "Planned", "red arrows in KMZ"
        return "On Air", "blue arrows in KMZ"
    # not in the arrows KMZ -> the tracker decides
    if trec is None:
        return "Off Air", "not in the arrows KMZ or the tracker"
    return "On Air", "active in tracker but missing from the arrows KMZ"


def build(arrows_path: Path, tracker_path: Path, out_path: Path,
          prev_path: Path | None = None) -> None:
    print(f"reading arrows KMZ   {arrows_path.name}")
    arrows = parse_arrows(arrows_path)
    print(f"  {len(arrows)} sites, {sum(a['n_arrows'] for a in arrows.values())} arrows")

    print(f"reading tracker      {tracker_path.name}")
    tracker = load_tracker(tracker_path)
    print(f"  {len(tracker)} R5 sites with cell records")

    prev_sites = set()
    if prev_path and prev_path.exists():
        ptxt = read_kml_text(prev_path)
        prev_sites = {m.group(1).upper() for m in
                      re.finditer(r"Site Code:\s*([A-Za-z]{2,4}\d{3,5})", ptxt)}

    all_ids = sorted(set(arrows) | set(tracker) | prev_sites)

    folders = {"On Air": [], "Planned": [], "Off Air": []}
    towers, beams, labels, review = [], [], [], []
    counts = defaultdict(int)

    for sid in all_ids:
        a = arrows.get(sid)
        trec = tracker.get(sid)
        status, reason = classify(sid, a, trec)
        name = a["name"] if a else clean_site_name((trec or {}).get("name", ""), sid)

        def flag(cat, note):
            review.append((sid, name, status, cat, note))

        # ---- position: the arrows KMZ wins (user: "site location from this") ---
        lat = lon = None
        if a and not _is_placeholder(a["lat"], a["lon"]):
            lat, lon = a["lat"], a["lon"]
            if trec and trec["coord"]:
                d = _haversine(lat, lon, *trec["coord"])
                if d > 2000:
                    flag("moved", f"arrows KMZ and tracker disagree by {d/1000:.1f} km "
                                  f"(tracker: {trec['coord'][0]:.5f}, {trec['coord'][1]:.5f}) - "
                                  "one of them is wrong; used the arrows KMZ")
                elif d > 150:
                    flag("moved", f"arrows KMZ position is {d:.0f} m from the tracker's "
                                  f"({trec['coord'][0]:.5f}, {trec['coord'][1]:.5f}) - used arrows")
        elif trec and trec["coord"]:
            lat, lon = trec["coord"]
            if a:
                flag("no-coords", "placeholder position in arrows KMZ - used tracker position")
        if lat is None:
            flag("no-coords", "no position in arrows KMZ or tracker - SITE OMITTED, send coordinates")
            continue

        counts[status] += 1
        height_m = ((trec or {}).get("height")
                    or (a["height_m"] if a and a["height_m"] else None))
        if prev_sites and sid not in prev_sites:
            flag("new-site", f"not in last week's KMZ - added as {status}"
                             + ("" if trec else "; no tracker record yet, balloons are empty"))
        if a is None and status == "On Air":
            flag("missing-from-arrows",
                 "active in the tracker but not in your arrows KMZ - add it there")
        elif status == "Off Air":
            flag("off-air", reason)

        # ---- sectors ----
        secs = plan_sectors(sid, a, trec, review, status)
        if not secs:
            flag("no-sectors", "no sectors could be resolved - SITE OMITTED")
            continue

        ant_h = (height_m or 30.0) * TOWER_SCALE
        for n, az, rec in secs:
            tilt = (rec or {}).get("tilt") or DEFAULT_TILT_DEG
            reach = coverage_reach_m(height_m, tilt)
            desc = balloon(name, sid, n, az, height_m, status, rec,
                           tilt=tilt, reach_m=reach)
            style = STATUS_STYLE[status]

            # main 3 dB footprint - carries the balloon
            folders[status].append(
                "    <Placemark>\n      <name></name>\n"
                f"      <description><![CDATA[{desc}]]></description>\n"
                f"      <styleUrl>#{style}</styleUrl>\n"
                "      <Polygon>\n        <extrude>0</extrude>\n"
                "        <altitudeMode>clampToGround</altitudeMode>\n"
                "        <outerBoundaryIs><LinearRing><coordinates>"
                f"{lobe_coords(lat, lon, az, reach)}"
                "</coordinates></LinearRing></outerBoundaryIs>\n"
                "      </Polygon>\n    </Placemark>")
            # brighter inner core = the strong part of the lobe
            folders[status].append(
                "    <Placemark>\n      <name></name>\n"
                f"      <styleUrl>#{style}_core</styleUrl>\n"
                "      <Polygon>\n        <extrude>0</extrude>\n"
                "        <altitudeMode>clampToGround</altitudeMode>\n"
                "        <outerBoundaryIs><LinearRing><coordinates>"
                f"{lobe_coords(lat, lon, az, reach, scale=0.52)}"
                "</coordinates></LinearRing></outerBoundaryIs>\n"
                "      </Polygon>\n    </Placemark>")
            # the beam itself: antenna on the tower -> the footprint edge
            beams.append(
                "    <Placemark>\n      <name></name>\n"
                f"      <styleUrl>#{style}_core</styleUrl>\n"
                "      <Polygon>\n        <extrude>0</extrude>\n"
                "        <altitudeMode>relativeToGround</altitudeMode>\n"
                "        <outerBoundaryIs><LinearRing><coordinates>"
                f"{beam_coords(lat, lon, az, reach, ant_h)}"
                "</coordinates></LinearRing></outerBoundaryIs>\n"
                "      </Polygon>\n    </Placemark>")
            beams.append(
                "    <Placemark>\n      <name></name>\n"
                "      <styleUrl>#boresight</styleUrl>\n"
                "      <LineString><altitudeMode>relativeToGround</altitudeMode>"
                f"<coordinates>{boresight_coords(lat, lon, az, reach, ant_h)}"
                "</coordinates></LineString>\n    </Placemark>")

        towers.extend(tower_placemarks(lat, lon, height_m,
                                       [az for _, az, _ in secs], name))
        labels.append(
            f"    <Placemark>\n      <name>{escape(name)}</name>\n"
            "      <styleUrl>#sitelabel</styleUrl>\n"
            f"      <Point><coordinates>{lon:.7f},{lat:.7f},0</coordinates></Point>\n    </Placemark>")

    # ---- write ----
    n_sites = len(labels)
    doc = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2">\n<Document>\n'
        f'  <name>{date.today().strftime(KMZ_NAME_FORMAT)}</name>\n  <open>1</open>\n'
        + STYLE_BLOCK + "\n"
        + _folder("On Air sectors", folders["On Air"],
                  per_site=len(folders["On Air"]) // 2)
        + _folder("Planned sectors", folders["Planned"],
                  per_site=len(folders["Planned"]) // 2)
        + _folder("Off Air sectors", folders["Off Air"],
                  per_site=len(folders["Off Air"]) // 2)
        + _folder("3D coverage beams", beams, per_site=len(beams) // 2,
                  visible=False)     # heavy in 3D - tick it on when you want it
        + _folder("Telecom Towers (3D lattice)", towers, per_site=n_sites)
        + _folder("Site Names", labels)
        + "</Document>\n</kml>\n"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", doc)

    rv = out_path.with_suffix(".review.csv")
    order = {c: i for i, c in enumerate(
        ["no-coords", "no-sectors", "missing-from-arrows", "moved",
         "extra-arrow", "new-site", "off-air"])}
    with open(rv, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["category", "site_id", "site_name", "status", "note"])
        for sid, name, status, cat, note in sorted(
                review, key=lambda r: (order.get(r[3], 99), r[0])):
            w.writerow([cat, sid, name, status, note])

    by_cat = defaultdict(int)
    for r in review:
        by_cat[r[3]] += 1
    print(f"\nwrote {out_path}  ({out_path.stat().st_size/1e6:.1f} MB)")
    print(f"  On Air {counts['On Air']}   Planned {counts['Planned']}   "
          f"Off Air {counts['Off Air']}   sites {n_sites}")
    print(f"  review -> {rv.name}   " +
          "  ".join(f"{k}:{v}" for k, v in sorted(by_cat.items())))


def _folder(name, placemarks, per_site=None, visible=True):
    count = per_site if per_site is not None else len(placemarks)
    vis = "" if visible else "    <visibility>0</visibility>\n"
    return (f'  <Folder>\n    <name>{name} ({count})</name>\n    <open>0</open>\n{vis}'
            + "\n".join(placemarks) + "\n  </Folder>\n")


def plan_sectors(sid, arrow, trec, review, status):
    """-> list of (sector_num, azimuth_float, tracker_rec_or_None).

    The Engineering Parameter Tracker is the record of truth for a site that
    appears in it: its sector count / numbering / azimuths / cells are used
    verbatim. The arrows KMZ only supplies geometry for sites the tracker does
    not know (new / planned). Discrepancies are logged, never silently
    reconciled.
    """
    tsecs = trec["sectors"] if trec else {}
    arrow_az = arrow["azimuths"] if arrow else []

    if tsecs:
        out = [(n, float(tsecs[n]["az"] or 0), tsecs[n]) for n in sorted(tsecs)]
        if arrow_az and len(arrow_az) > len(out):
            review.append((sid, arrow["name"], status, "extra-arrow",
                           f"arrows KMZ draws {len(arrow_az)} sectors {arrow_az}, "
                           f"tracker has {len(out)} {[round(az) for _, az, _ in out]} - "
                           "possible new sector not yet in the tracker"))
        return out

    # no tracker data -> synthesise from the arrows
    if arrow_az:
        return [(i + 1, float(a), None) for i, a in enumerate(arrow_az)]
    if arrow and arrow["n_arrows"]:
        return [(1, 0.0, None)]
    return []


def _haversine(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R_EARTH * 2 * math.asin(math.sqrt(h))


# --------------------------------------------------------------------------- #
def _newest(folder: str, pattern: str) -> Path | None:
    p = Path(folder)
    if not p.is_dir():
        return None
    hits = sorted(p.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arrows", type=Path,
                    default=_newest(r"C:\Users\swx1351646\Desktop", "KMZ_*.kmz"),
                    help="sector-arrows KMZ you maintain in Google Earth")
    ap.add_argument("--tracker", type=Path,
                    default=_newest(r"D:\WeLink_data_files\swx1351646\ReceiveFiles",
                                    "WK* Engineering Parameter Tracker*.xlsx"),
                    help="WeLink Engineering Parameter Tracker .xlsx")
    ap.add_argument("--previous", type=Path, default=None,
                    help="optional: last week's Full Visualization KMZ (kept only "
                         "to carry over retired sites it still lists)")
    ap.add_argument("--out", type=Path,
                    default=Path(r"C:\Users\swx1351646\Desktop") /
                    f"R5_Sites_Customer_{date.today():%Y%m%d}.kmz")
    a = ap.parse_args(argv)

    for label, path in (("arrows KMZ", a.arrows), ("tracker", a.tracker)):
        if not path or not Path(path).exists():
            ap.error(f"{label} not found: {path}   (pass --{label.split()[0]})")

    build(a.arrows, a.tracker, a.out, a.previous)


if __name__ == "__main__":
    sys.exit(main())
