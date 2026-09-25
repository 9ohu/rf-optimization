"""Load the Asiacell / R5 hourly 4G KPI export into the canonical schema.

Two shapes are supported:

* **long CSV** (``Original_Data_R5_Hourly.csv``, optionally inside a .zip) -
  one row per cell-sector per hour, ~72 operator KPI columns.
* **pivot workbook** (``4G KPIs Hourly.xlsx``) - one sheet per KPI, a title
  row, then ``Row Labels | Cell FDD TDD Indication | <hour> | <hour> ...``.

Output columns match ``rfopt.ingest.schema`` so the existing KPI / diagnosis /
anomaly engines run on it unchanged. Granularity is hourly; the export is
per eNodeB-sector (aggregated over carriers), so there is no ``band``.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from rfopt.ingest._io import cached_parse, excel_file
from rfopt.ingest.sitedb import R5_PREFIXES

_SITE_RE = re.compile(r"([A-Za-z]{2,4}\d{3,6})")

# operator KPI header (normalised)  ->  canonical schema name
_KPI_MAP: dict[str, str] = {
    "4g lte drop call rate with mme percent asiacell": "erab_drop_rate",
    "4g lte drop call rate with mme percent": "erab_drop_rate",
    "4g cssr percent asiacell": "call_setup_sr",
    "4g cssr percent": "call_setup_sr",
    "e rab setup success rate voip percent": "volte_erab_setup_sr",
    "volte completion rate percent npm": "volte_completion_pct",
    "4g data volume gb": "total_traffic_gb",
    "lte availability percent ab": "cell_avail_pct",
    "lte availability percent": "cell_avail_pct",
    "4g cell level availability chen": "cell_avail_pct",
    "hw dl prb avg utilization percent": "dl_prb_util",
    "hw ul prb avg utilization percent": "ul_prb_util",
    # the same counters under the NPM query's other naming
    "downlink prb utilization rate percent": "dl_prb_util",
    "uplink prb utilization rate percent": "ul_prb_util",
    "4g dl user throughput mbps asiacell": "dl_user_thr_mbps",
    "4g dl user throughput mbps": "dl_user_thr_mbps",
    "4g ul user throughput mbps asiacell": "ul_user_thr_mbps",
    "4g ul user throughput mbps": "ul_user_thr_mbps",
    "outgoing ho exec success rate percent percent asiacell": "ho_sr",
    "outgoing ho exec success rate percent asiacell": "ho_sr",
    "outgoing ho exec success rate percent": "ho_sr",
    "inter frequency handover out success rate percent": "inter_freq_ho_sr",
    "intra frequency handover out success rate percent": "intra_freq_ho_sr",
    "srvcc ho success rate huawei npm": "srvcc_ho_sr",
    "l traffic user avg": "rrc_conn_users_avg",
    "l ul interference avg dbm": "ul_rssi_dbm",
    "cs fallback success rate 4g side only asiacell": "csfb_sr",
    "l rrc setupfail noreply": "rrc_setupfail_noreply",
    "l rrc setupfail rej": "rrc_setupfail_rej",
    "l rrc setupfail resfail": "rrc_setupfail_resfail",
    "l e rab abnormrel radio": "erab_abnorm_rel_radio",
    "l e rab abnormrel mme": "erab_abnorm_rel_mme",
    "l e rab abnormrel hofailure": "erab_abnorm_rel_ho",
}
# pivot-sheet title  ->  canonical
_PIVOT_TITLE_MAP = {
    "l.ul.interference.avg": "ul_rssi_dbm",
    "4g data volume": "total_traffic_gb",
    "lte_availability": "cell_avail_pct",
    "dl prb avg utilization": "dl_prb_util",
    "ul prb avg utilization": "ul_prb_util",
    "dl user throughput": "dl_user_thr_mbps",
    "ul user throughput": "ul_user_thr_mbps",
    "drop call rate": "erab_drop_rate",
    "cssr": "call_setup_sr",
}

_ID_COLS = {"date": "date", "time": "time", "datetime": "datetime",
            "start time": "datetime", "period start time": "datetime",
            "enodeb name": "enodeb_name", "cell name": "cell_id",
            "localcell id": "local_cell_id", "cell fdd tdd indication": "duplex",
            "enodeb function name": "enodeb_fn"}

# ---- 3G / UMTS NodeB-level export (Time, RNC, NODEBNAME, NodeB ID, ...) ---- #
_KPI_MAP_3G: dict[str, str] = {
    "3g availability ab": "cell_avail_pct",
    "3g availability": "cell_avail_pct",
    "vs ippm rtt means ms": "ipmm_rtt_ms",
    "vs rscgroup flowctrol dl dropnum": "dl_flowctrl_drops",
}
_ID_COLS_3G = {"time": "time", "date": "date", "datetime": "datetime",
               "rnc": "rnc", "nodebname": "nodeb_name", "nodeb name": "nodeb_name",
               "nodeb id": "nodeb_id"}

# Counters where a zero is not a reading. A 0 ms round trip is physically
# impossible: it means the IPPM probe is not running on that NodeB, and
# charting it as zero draws a flat line along the axis that looks like a
# measurement. Availability and the drop counters are left alone — zero is a
# real (and good) value for those.
_ZERO_IS_MISSING = ("vs ippm rtt means ms",)
_ZERO_IS_MISSING_CANON = ("ipmm_rtt_ms",)

# ---- how each export names its objects, which is how we tell them apart --- #
_MARK_4G = ("enodeb name", "enodeb function name", "cell fdd tdd indication")
_MARK_3G = ("rnc", "nodebname", "nodeb name")
_MARK_2G = ("bsc", "bsc name", "bts name", "btsname", "bts")


@dataclass
class KpiFileInfo:
    """What a dropped file turns out to be — read from its header alone."""
    name: str
    kind: str                     # "4G" | "3G" | "4G pivot" | "unknown"
    columns: list[str] = field(default_factory=list)
    kpis: list[tuple[str, str]] = field(default_factory=list)   # raw, label
    all_kpis: list[str] = field(default_factory=list)   # every KPI in the sheet
    first_time: str = ""
    note: str = ""

    USABLE = ("4G", "3G", "2G", "Other")

    @property
    def ok(self) -> bool:
        return self.kind in self.USABLE or self.kind == "4G pivot"


# columns that name the object or the hour rather than measuring anything
_NOT_A_KPI = {"time", "date", "datetime", "start time", "period start time",
              "enodeb name", "enodeb function name", "cell name",
              "cell fdd tdd indication", "localcell id", "local cell id",
              "rnc", "nodebname", "nodeb name", "nodeb id", "site name",
              "site id", "cellid", "cell id",
              "bsc", "bsc name", "bts", "bts name", "btsname", "cellname"}


def _head_bytes(path_or_buf, n: int = 262_144) -> tuple[bytes, str]:
    """The first n bytes and the file name — never the whole 140 MB CSV."""
    if hasattr(path_or_buf, "read"):
        name = getattr(path_or_buf, "name", "upload")
        try:
            data = path_or_buf.getvalue()
        except AttributeError:
            pos = path_or_buf.tell()
            path_or_buf.seek(0)
            data = path_or_buf.read()
            path_or_buf.seek(pos)
        return data, name
    p = Path(path_or_buf)
    if p.suffix.lower() in (".zip", ".xlsx", ".xls"):
        return p.read_bytes(), p.name          # both need the whole container
    with open(p, "rb") as fh:
        head = fh.read(n)
    # a container renamed to anything else still has to be read whole — the
    # user drops files "regardless of type", so trust the bytes, not the suffix
    return (p.read_bytes() if head[:2] == b"PK" else head), p.name


def sniff_kpi_export(path_or_buf) -> KpiFileInfo:
    """Identify a dropped file and list the KPIs inside it, cheaply.

    Reads the banner and the header row only — a 140 MB export is identified in
    milliseconds, so the page can show what it got before anything is parsed.
    """
    data, name = _head_bytes(path_or_buf)
    suffix = Path(name).suffix.lower()

    if suffix in (".xlsx", ".xls"):
        try:
            xl = excel_file(io.BytesIO(data))
            return KpiFileInfo(name=name, kind="4G pivot",
                               columns=list(xl.sheet_names),
                               kpis=[(s, s) for s in xl.sheet_names],
                               note="pivot workbook — one sheet per KPI")
        except Exception as exc:
            return KpiFileInfo(name=name, kind="unknown",
                               note=f"could not read the workbook ({exc})")

    if suffix == ".zip" or data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                inner = next((n for n in z.namelist()
                              if n.lower().endswith((".csv", ".txt"))), None)
                if inner is None:
                    return KpiFileInfo(name=name, kind="unknown",
                                       note="the zip holds no CSV")
                with z.open(inner) as fh:      # streamed: only the head is read
                    data = fh.read(262_144)
        except zipfile.BadZipFile:
            return KpiFileInfo(name=name, kind="unknown",
                               note="not a readable zip")

    hdr = _guess_header_row(data)
    txt = io.TextIOWrapper(io.BytesIO(data), encoding="utf-8-sig",
                           errors="replace")
    lines = []
    for i, line in enumerate(txt):
        if i > hdr + 1:
            break
        lines.append(line.rstrip("\r\n"))
    if len(lines) <= hdr:
        return KpiFileInfo(name=name, kind="unknown",
                           note="no header row found in the first lines")

    cols = [c.strip().strip('"') for c in lines[hdr].split(",")]
    row1 = lines[hdr + 1].split(",") if len(lines) > hdr + 1 else []
    norm = {c: _norm(c) for c in cols}

    # an hourly export names an hour and an object. Without both this is some
    # other file that happens to be a CSV, and guessing at it helps nobody.
    has_time = any(n in ("time", "date", "datetime", "start time")
                   for n in norm.values())
    has_object = any(n in _OBJ_COLS or n in _ID_COLS or n in _ID_COLS_3G
                     for n in norm.values())
    if not (has_time and has_object):
        return KpiFileInfo(name=name, kind="unknown", columns=cols,
                           note="no hourly KPI export: needs a time column and "
                                "a cell / NodeB column")

    marks = set(norm.values())
    banner = " ".join(lines[:hdr]).lower() + " " + name.lower()
    if marks & set(_MARK_3G):
        kind = "3G"
    elif marks & set(_MARK_4G):
        kind = "4G"
    elif marks & set(_MARK_2G) or re.search(r"\b2g\b", banner):
        kind = "2G"
    else:
        # a valid hourly export whose technology we cannot name — still usable,
        # the tool charts whatever columns it has
        kind = "Other"

    table = _KPI_MAP_3G if kind == "3G" else _KPI_MAP
    tech = "UMTS" if kind == "3G" else "LTE"
    kpis = [(c, _kpi_label(table[n], tech)) for c, n in norm.items()
            if n in table]
    # everything measurable in the sheet, named exactly as the operator named
    # it — the tool charts whatever is in the file, not only what it can map
    all_kpis = [c for c, n in norm.items() if n not in _NOT_A_KPI and c]
    if not all_kpis:
        return KpiFileInfo(name=name, kind="unknown", columns=cols,
                           note="no KPI column found in this file")
    return KpiFileInfo(
        name=name, kind=kind, columns=cols, kpis=kpis,
        all_kpis=all_kpis, first_time=(row1[0].strip() if row1 else ""),
        note=f"{len(cols)} columns · {len(all_kpis)} KPIs in the sheet")


_OBJ_COLS = {"cell name": "object", "nodebname": "object",
             "enodeb name": "parent", "nodeb name": "object",
             "enodeb function name": "parent",
             "cell fdd tdd indication": "duplex", "rnc": "parent",
             "localcell id": "local_cell_id", "local cell id": "local_cell_id",
             # 2G: the cell is the object, the BSC / BTS is its parent
             "cellname": "object", "bts name": "parent", "btsname": "parent",
             "bsc": "parent", "bsc name": "parent", "site name": "parent"}


def load_hourly_raw(path_or_buf, kpi_columns: list[str]) -> pd.DataFrame:
    """Load just the picked KPI columns, keeping the operator's own names.

    The charting side of the tool works on whatever is in the sheet, so nothing
    is renamed or dropped here. Only the requested columns are read, which is
    what keeps a 140 MB export usable: ~3 s for a handful of KPIs instead of
    ~40 s for all 71.
    """
    data, name = _read_bytes(path_or_buf)
    if Path(name).suffix.lower() == ".zip" or data[:2] == b"PK":
        data = _extract_csv_from_zip(data)
    hdr = _guess_header_row(data)
    head = pd.read_csv(io.BytesIO(data), dtype=str, skiprows=hdr, nrows=0,
                       encoding="utf-8-sig")
    cols = [str(c).strip() for c in head.columns]
    ren = {c: _OBJ_COLS[_norm(c)] for c in cols if _norm(c) in _OBJ_COLS}
    time_col = next((c for c in cols if _norm(c) in ("time", "datetime",
                                                     "start time")), None)
    date_col = next((c for c in cols if _norm(c) == "date"), None)
    want = [c for c in cols
            if c in set(kpi_columns) or c in ren or c in (time_col, date_col)]
    df = pd.read_csv(io.BytesIO(data), dtype=str, low_memory=False,
                     skiprows=hdr, encoding="utf-8-sig",
                     usecols=[c for c in head.columns if str(c).strip() in want])
    df.columns = [str(c).strip() for c in df.columns]

    stamp = df[time_col] if time_col else pd.Series("", index=df.index)
    if date_col:                       # format A: separate Date + HH:MM columns
        stamp = df[date_col].astype(str) + " " + stamp.astype(str)
    df["datetime"] = pd.to_datetime(stamp, errors="coerce")
    # "eNodeB Name" and "eNodeB Function Name" both mean the parent, so the
    # rename can collide; keep the first and move on
    df = df.rename(columns=ren)
    df = df.loc[:, ~pd.Index(df.columns).duplicated(keep="first")]
    if "object" not in df.columns:
        df["object"] = df.get("parent", "").astype(str)
    df["object"] = df["object"].astype(str).str.strip()
    df["site_id"] = df["object"].str.extract(_SITE_RE.pattern)[0].str.upper()
    df["prefix"] = df["site_id"].str[:3]
    # the sector the cell sits on, so a per-cell KPI can be put on the map's
    # per-sector beams. Same rule as the canonical loader, so both agree.
    sec = pd.to_numeric(df["object"].str.extract(r"[-_ ]([1-9])\s*$")[0],
                        errors="coerce")
    if "local_cell_id" in df.columns:       # 3G / 2G exports carry no such id
        lcid = pd.to_numeric(df["local_cell_id"], errors="coerce")
        sec = sec.fillna(lcid.where(lcid.between(1, 12)))
    df["sector_id"] = (df["site_id"] + "-S"
                       + sec.where(sec.between(1, 12)).fillna(0)
                       .astype("Int64").astype(str))

    for c in kpi_columns:
        if c in df.columns:
            df[c] = pd.to_numeric(
                df[c].astype(str).str.replace(r"[%,\s]", "", regex=True),
                errors="coerce")
            if _norm(c) in _ZERO_IS_MISSING:
                df.loc[df[c] == 0, c] = np.nan
    # `duplex` / `parent` are what the emailed pivot puts next to the cell
    # name (Cell FDD TDD Indication for 4G, RNC for 3G)
    keep = ["datetime", "object", "site_id", "sector_id", "prefix"] + \
           [c for c in ("duplex", "parent") if c in df.columns] + \
           [c for c in kpi_columns if c in df.columns]
    out = df[df["datetime"].notna() & df["object"].ne("")][keep]
    return out.reset_index(drop=True)


def merge_hourly(frames: list, key: tuple = ("datetime", "object")) -> pd.DataFrame:
    """Several exports of one technology read as one: one row per object and
    hour. Where two frames hold the same hour of an object, each KPI takes the
    value of the later frame that has one (callers put the most recent export
    last); a KPI only one file carries comes from that file. So an hour that
    two files share is counted once, and files with different KPIs, cells or
    periods add up. A single frame is returned as it is."""
    frames = [f for f in frames if f is not None]
    full = [f for f in frames if len(f)]
    if len(full) <= 1:
        return full[0] if full else (frames[0] if frames else pd.DataFrame())
    key = list(key)
    df = pd.concat(full, ignore_index=True, sort=False)
    dup = df.duplicated(key, keep=False)
    if dup.any():
        one = df[dup].groupby(key, sort=False, as_index=False).last()
        df = pd.concat([df[~dup], one], ignore_index=True, sort=False)
    return df.sort_values(key, kind="stable").reset_index(drop=True)


def _kpi_label(canonical: str, tech: str) -> str:
    from rfopt.ingest.schema import kpi_def
    d = kpi_def(canonical, tech)
    return d.label if d else canonical


@dataclass
class HourlyKpiLoad:
    df: pd.DataFrame
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        d = self.df
        dr = (d["datetime"].min(), d["datetime"].max()) if len(d) else (None, None)
        return {
            "rows": len(d), "sites": int(d["site_id"].nunique()),
            "sectors": int(d["sector_id"].nunique()),
            "dates": sorted(d["datetime"].dt.date.astype(str).unique().tolist())
            if len(d) else [],
            "hours": int(d["datetime"].dt.hour.nunique()) if len(d) else 0,
            "kpis": [c for c in d.columns if c in _KPI_MAP.values()
                     or c in ("volte_completion_pct", "srvcc_ho_sr")],
            "date_range": [str(dr[0]), str(dr[1])],
        }


def _norm(s: str) -> str:
    s = str(s).lower().replace("%", " percent ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


# --------------------------------------------------------------------------- #
def _read_bytes(path_or_buf) -> tuple[bytes, str]:
    if hasattr(path_or_buf, "read"):
        return path_or_buf.read(), getattr(path_or_buf, "name", "upload")
    p = Path(path_or_buf)
    return p.read_bytes(), p.name


def _extract_csv_from_zip(data: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        name = next((n for n in z.namelist() if n.lower().endswith(".csv")), None)
        if not name:
            raise ValueError("zip has no .csv")
        return z.read(name)


def _guess_header_row(data: bytes, scan: int = 15) -> int:
    """Skip the operator's title / 'Save Time' / 'User Name' banner rows."""
    txt = io.TextIOWrapper(io.BytesIO(data), encoding="utf-8-sig",
                           errors="replace")
    for i, line in enumerate(txt):
        if i >= scan:
            break
        cells = [c.strip().strip('"').lower() for c in line.split(",")]
        # the banner lines carry a single cell, so three is enough to tell a
        # header from them — and a thin export (the 3G one has eight columns)
        # still has to be found
        if sum(bool(c) for c in cells) >= 3 and any(
                c in ("time", "date", "cell name", "enodeb name", "nodebname",
                      "rnc", "bsc") for c in cells):
            return i
    return 0


def _load_long_csv(data: bytes, notes: list[str]) -> pd.DataFrame:
    hdr = _guess_header_row(data)
    # read only the header, decide which columns we actually need, then load
    head = pd.read_csv(io.BytesIO(data), dtype=str, skiprows=hdr, nrows=0,
                       encoding="utf-8-sig")
    cols = [str(c).strip() for c in head.columns]
    want = [c for c in cols if _norm(c) in _ID_COLS or _norm(c) in _KPI_MAP]
    raw = pd.read_csv(io.BytesIO(data), dtype=str, low_memory=False,
                      skiprows=hdr, encoding="utf-8-sig",
                      usecols=[c for c in head.columns
                               if str(c).strip() in want] or None)
    if hdr:
        notes.append(f"skipped {hdr} banner row(s)")
    raw.columns = [str(c).strip() for c in raw.columns]
    nmap = {c: _norm(c) for c in raw.columns}
    ren = {c: _ID_COLS[n] for c, n in nmap.items() if n in _ID_COLS}
    ren.update({c: _KPI_MAP[n] for c, n in nmap.items() if n in _KPI_MAP})
    df = raw.rename(columns=ren)
    df = df.loc[:, ~pd.Index(df.columns).duplicated(keep="first")]

    # a lone "Time" column often carries the full datetime ("2026-09-07 00:00")
    if "datetime" not in df.columns and "time" in df.columns \
            and "date" not in df.columns:
        sample = df["time"].dropna().astype(str).head(50)
        if sample.str.contains(r"\d{4}-\d\d-\d\d|\d\d/\d\d/\d{2,4}").mean() > 0.5:
            df = df.rename(columns={"time": "datetime"})

    if "datetime" in df.columns:
        dt = pd.to_datetime(df["datetime"], errors="coerce")
    else:
        dt = pd.to_datetime(df.get("date", "").astype(str) + " "
                            + df.get("time", "00:00").astype(str),
                            errors="coerce")
    df["datetime"] = dt
    return df


def _load_pivot_xlsx(data: bytes, notes: list[str]) -> pd.DataFrame:
    xl = excel_file(io.BytesIO(data))
    frames = []
    for sn in xl.sheet_names:
        head = pd.read_excel(xl, sheet_name=sn, header=None, nrows=1, dtype=str)
        title = _norm(str(head.iloc[0, 0]) if head.size else sn)
        canon = next((v for k, v in _PIVOT_TITLE_MAP.items() if k in title), None)
        if canon is None:
            canon = next((v for k, v in _PIVOT_TITLE_MAP.items()
                          if k in _norm(sn)), None)
        if canon is None:
            continue
        # date from the title  e.g. "... - 2026-09-07 - Hourly ..."
        m = re.search(r"(20\d\d-\d\d-\d\d)", str(head.iloc[0, 0]))
        day = m.group(1) if m else "2026-01-01"
        tbl = pd.read_excel(xl, sheet_name=sn, header=1, dtype=str)
        tbl = tbl.rename(columns={tbl.columns[0]: "cell_id",
                                  tbl.columns[1]: "duplex"})
        hour_cols = [c for c in tbl.columns if re.search(r"\d{1,2}:\d{2}", str(c))]
        long = tbl.melt(id_vars=["cell_id", "duplex"], value_vars=hour_cols,
                        var_name="_h", value_name=canon)
        long["datetime"] = pd.to_datetime(
            day + " " + long["_h"].astype(str), errors="coerce")
        frames.append(long.drop(columns="_h"))
    if not frames:
        raise ValueError("no recognisable KPI sheets in the workbook")
    out = frames[0]
    for f in frames[1:]:
        out = out.merge(f, on=["cell_id", "duplex", "datetime"], how="outer")
    return out


# --------------------------------------------------------------------------- #
def load_hourly_kpi(
    path_or_buf,
    *,
    region: str | None = "R5",
    prefixes: tuple[str, ...] = R5_PREFIXES,
    use_cache: bool = True,
) -> HourlyKpiLoad:
    parse = lambda src: _parse_hourly(src, region, prefixes)      # noqa: E731
    out = cached_parse(path_or_buf, parse, tag=f"hourlykpi_{region or 'all'}") \
        if use_cache else parse(path_or_buf)
    return HourlyKpiLoad(df=out, notes=list(out.attrs.get("_notes", ["loaded"])))


def load_hourly_kpi_3g(
    path_or_buf,
    *,
    region: str | None = "R5",
    prefixes: tuple[str, ...] = R5_PREFIXES,
    use_cache: bool = True,
) -> HourlyKpiLoad:
    """The hourly 3G export — one row per **NodeB** per hour.

    Far thinner than the 4G file: availability, IP-path RTT and downlink
    flow-control drops. Same canonical column names, so the same aggregation
    and threshold code runs over it.
    """
    parse = lambda src: _parse_hourly_3g(src, region, prefixes)    # noqa: E731
    out = cached_parse(path_or_buf, parse, tag=f"hourlykpi3g_{region or 'all'}") \
        if use_cache else parse(path_or_buf)
    return HourlyKpiLoad(df=out, notes=list(out.attrs.get("_notes", ["loaded"])))


def _parse_hourly_3g(path_or_buf, region, prefixes):
    data, name = _read_bytes(path_or_buf)
    notes: list[str] = []
    suffix = Path(name).suffix.lower()
    if suffix in (".xlsx", ".xls"):
        raise ValueError("the 3G reader needs the raw NPM export (.csv or the "
                         ".zip around it), not a pivot workbook")
    if suffix == ".zip":
        data = _extract_csv_from_zip(data)
    hdr = _guess_header_row(data)
    raw = pd.read_csv(io.BytesIO(data), dtype=str, low_memory=False,
                      skiprows=hdr, encoding="utf-8-sig")
    if hdr:
        notes.append(f"skipped {hdr} banner row(s)")
    raw.columns = [str(c).strip() for c in raw.columns]
    nmap = {c: _norm(c) for c in raw.columns}
    ren = {c: _ID_COLS_3G[n] for c, n in nmap.items() if n in _ID_COLS_3G}
    ren.update({c: _KPI_MAP_3G[n] for c, n in nmap.items() if n in _KPI_MAP_3G})
    df = raw.rename(columns=ren)
    df = df.loc[:, ~pd.Index(df.columns).duplicated(keep="first")]

    df["datetime"] = pd.to_datetime(df.get("datetime", df.get("time")),
                                    errors="coerce")
    df["nodeb_name"] = df.get("nodeb_name", "").astype(str).str.strip()
    df["site_id"] = df["nodeb_name"].str.extract(_SITE_RE.pattern)[0].str.upper()
    df["rnc"] = df.get("rnc", "").astype(str).str.strip()
    df["technology"] = "UMTS"
    df["granularity"] = "hour"
    df["prefix"] = df["site_id"].str[:3]
    # the export is per NodeB, so site == sector == "cell" for the engines
    df["sector_id"] = df["site_id"]
    df["cell_id"] = df["nodeb_name"]

    kpi_cols = [c for c in df.columns if c in set(_KPI_MAP_3G.values())]
    for c in kpi_cols:
        df[c] = pd.to_numeric(
            df[c].astype(str).str.replace(r"[%,\s]", "", regex=True),
            errors="coerce")
        if c in _ZERO_IS_MISSING_CANON:
            df.loc[df[c] == 0, c] = np.nan

    n0 = len(df)
    df = df[df["site_id"].notna() & df["datetime"].notna()]
    if region and region.upper() == "R5":
        df = df[df["prefix"].isin(prefixes)]
    notes.append(f"kept {len(df)}/{n0} rows after id/date/R5 filter")

    keep = ["datetime", "granularity", "technology", "site_id", "sector_id",
            "cell_id", "nodeb_name", "nodeb_id", "rnc", "prefix"] + kpi_cols
    out = df[[c for c in keep if c in df.columns]].reset_index(drop=True)
    if len(out):
        notes.append(f"{out['datetime'].dt.date.nunique()} day(s), "
                     f"{out['datetime'].dt.hour.nunique()} distinct hours, "
                     f"{out['site_id'].nunique()} NodeBs, {len(kpi_cols)} KPIs")
    out.attrs["_notes"] = notes
    return out


def _parse_hourly(path_or_buf, region, prefixes):
    data, name = _read_bytes(path_or_buf)
    notes: list[str] = []
    suffix = Path(name).suffix.lower()

    if suffix == ".zip":
        data = _extract_csv_from_zip(data)
        suffix = ".csv"
    if suffix in (".csv", ".txt"):
        df = _load_long_csv(data, notes)
    elif suffix in (".xlsx", ".xls"):
        df = _load_pivot_xlsx(data, notes)
    else:
        raise ValueError(f"unsupported KPI file type: {suffix}")

    # --- identity ------------------------------------------------------
    src = df.get("enodeb_name", df.get("enodeb_fn", df.get("cell_id", "")))
    df["site_id"] = src.astype(str).str.extract(_SITE_RE.pattern)[0].str.upper()
    if "site_id" not in df or df["site_id"].isna().all():
        df["site_id"] = df["cell_id"].astype(str).str.extract(
            _SITE_RE.pattern)[0].str.upper()
    sec = df.get("cell_id", "").astype(str).str.extract(r"[-_ ]([1-9])\s*$")[0]
    lcid = pd.to_numeric(df.get("local_cell_id", np.nan), errors="coerce")
    sec = pd.to_numeric(sec, errors="coerce")
    sec = sec.fillna(lcid.where(lcid.between(1, 12)))
    df["sector"] = sec.where(sec.between(1, 12))
    df["sector_id"] = (df["site_id"] + "-S"
                       + df["sector"].fillna(0).astype("Int64").astype(str))
    df["cell_id"] = df.get("cell_id", df["sector_id"]).astype(str)
    df["technology"] = "LTE"
    df["granularity"] = "hour"
    df["prefix"] = df["site_id"].str[:3]

    # --- numeric KPIs ------------------------------------------------
    kpi_cols = [c for c in df.columns if c in set(_KPI_MAP.values())]
    for c in kpi_cols:
        s = pd.to_numeric(df[c], errors="coerce")
        if s.isna().mean() > 0.2:           # dirty column -> strip %/commas
            s = pd.to_numeric(df[c].astype(str).str.replace(
                r"[%,\s]", "", regex=True), errors="coerce")
        df[c] = s
    # UL interference sanity: keep only plausible dBm
    if "ul_rssi_dbm" in df.columns:
        df.loc[~df["ul_rssi_dbm"].between(-150, -60), "ul_rssi_dbm"] = np.nan

    # --- filter ----------------------------------------------------
    n0 = len(df)
    df = df[df["site_id"].notna() & df["datetime"].notna()]
    if region and region.upper() == "R5":
        df = df[df["prefix"].isin(prefixes)]
    notes.append(f"kept {len(df)}/{n0} rows after id/date/R5 filter")

    keep = ["datetime", "granularity", "technology", "site_id", "sector",
            "sector_id", "cell_id", "duplex", "prefix"] + kpi_cols
    out = df[[c for c in keep if c in df.columns]].reset_index(drop=True)
    if len(out):
        notes.append(f"{out['datetime'].dt.date.nunique()} day(s), "
                     f"{out['datetime'].dt.hour.nunique()} distinct hours, "
                     f"{out['site_id'].nunique()} sites, {len(kpi_cols)} KPIs")
    out.attrs["_notes"] = notes
    return out
