"""Data Resources as the pages read them — the one way in to the app's data.

Every page asks here for what it needs (the EP tracker, the site KMZ, the KPI
exports, the Daily Target, the coverage grids) and gets the **active** dataset
of that resource from the store (`rfopt.resources.store`). Nothing here
uploads, and nothing here looks at the computer's folders: a dataset exists only
after the user uploaded and applied it on the Data Resources page.

Reads are cached per stored file (its content hash is in its path), so a file
is parsed once per app run however many pages use it, and an applied
replacement is a new path — no stale cache to clear. The loaders' own on-disk
caches (EP, KMZ) and the coverage sidecar (``.coverage.npz``) make a restart
fast too.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

import _shared
from rfopt.complaints.target_store import (DAILY_TARGET, HISTORY, TargetFormatError,
                                           legacy_active, load_active, parse_target,
                                           target_summary)
from rfopt.resources import store as S

PAGE = "views/data_resources.py"
EP_ROLE, KMZ_ROLE = "EP tracker", "Site KMZ"
GRID = "Coverage grid"
TICKET_HISTORY = "Ticket history"


class FileRejected(ValueError):
    """The file is not what this resource holds (the message says why)."""


# --------------------------------------------------------------------------- #
# what each page reads
# --------------------------------------------------------------------------- #
def active(kind: str) -> S.Resource | None:
    """The resource when it has an active dataset, else None."""
    try:
        res = S.resource(kind)
    except S.ResourceError:
        return None
    return res if res.files else None


def files(kind: str) -> list:
    """The active dataset's files (never a pending upload)."""
    res = active(kind)
    return [f for f in (res.files if res else []) if f.path.is_file()]


def when(stamp: str | None) -> str:
    """"2026-09-18 12:40:05" as "18 Sep 2026, 12:40"."""
    from datetime import datetime
    if not stamp:
        return "—"
    try:
        return datetime.strptime(stamp[:16], "%Y-%m-%d %H:%M").strftime("%d %b %Y, %H:%M")
    except ValueError:
        return stamp


def _one(kind: str, role: str | None = None) -> S.StoredFile | None:
    return next((f for f in files(kind) if role is None or f.role == role), None)


def ep_path() -> str | None:
    f = _one("ep")
    return str(f.path) if f else None


def kmz_path() -> str | None:
    f = _one("kmz")
    return str(f.path) if f else None


def ep_file() -> S.StoredFile | None:
    return _one("ep")


def kmz_file() -> S.StoredFile | None:
    return _one("kmz")


def target():
    """The active Daily Target (a TargetDataset), or None."""
    return load_active()


def history_path() -> str | None:
    f = _one("complaints", HISTORY)
    return str(f.path) if f else None


@st.cache_resource(show_spinner=False, max_entries=32)
def kpi_info(path: str):
    """The export's technology and KPI list, from its header alone."""
    from rfopt.ingest.hourly_kpi import sniff_kpi_export
    return sniff_kpi_export(path)


@st.cache_resource(show_spinner="Reading the objects in the KPI file…", max_entries=16)
def kpi_index(path: str) -> pd.DataFrame:
    """Objects and hours only — no KPI column, so it is quick even on 140 MB."""
    from rfopt.ingest.hourly_kpi import load_hourly_raw
    return load_hourly_raw(path, [])


def kpi_sources() -> list:
    """[(path, KpiFileInfo, StoredFile)] for every file of the active KPI Data."""
    return [(str(f.path), kpi_info(str(f.path)), f) for f in files("kpi")]


def kpi_groups() -> list:
    """The active KPI Data by technology: [(technology, [(path, KpiFileInfo,
    StoredFile)])], the technologies in the order their first file comes. The
    pages read a technology's files as one export (`merge_hourly`); within
    one, the file whose hours end latest comes last, so where two files hold
    the same hour of a cell, the most recent export's value is the one used."""
    order: dict = {}
    for src in kpi_sources():
        order.setdefault(src[1].kind, []).append(src)

    def recency(src) -> tuple:
        s = src[2].summary or {}
        return str(s.get("end", "")), src[2].uploaded_at or ""

    return [(kind, sorted(group, key=recency)) for kind, group in order.items()]


@st.cache_resource(show_spinner="Reading the coverage grid…", max_entries=16)
def _coverage_file(sha1: str, path: str, name: str):
    from rfopt.ingest.coverage_grid import CoverageFile, read_coverage_export
    side = Path(path).parent / ".coverage.npz"
    if side.is_file():
        try:
            z = np.load(side, allow_pickle=False)
            meta = json.loads(str(z["meta"]))
            return CoverageFile(name=name, size=int(meta["size"]), lat=z["lat"], lon=z["lon"],
                                rsrp=z["rsrp"], mr=z["mr"], rsrp_column=meta["rsrp_column"],
                                sheets=list(meta["sheets"]), time_column=meta["time_column"],
                                exported=meta["exported"])
        except Exception:
            pass
    cf = read_coverage_export(path, name)
    try:
        meta = json.dumps({"size": cf.size, "rsrp_column": cf.rsrp_column, "sheets": cf.sheets,
                           "time_column": cf.time_column, "exported": cf.exported})
        tmp = side.parent / ".coverage.part.npz"
        np.savez(tmp, lat=cf.lat, lon=cf.lon, rsrp=cf.rsrp, mr=cf.mr, meta=np.array(meta))
        os.replace(tmp, side)
    except OSError:
        pass
    return cf


@st.cache_resource(show_spinner="Reading the ticket history…", max_entries=2)
def _history(path: str):
    from rfopt.complaints.history import read_history
    return read_history(path)


def history_file() -> S.StoredFile | None:
    """The active History ticket file, or None."""
    return _one("history")


def history():
    """The active ticket history as History of Tickets reads it, or None."""
    f = history_file()
    return _history(str(f.path)) if f else None


def coverage() -> dict:
    """sha1 → CoverageFile for every coverage grid of the active Coverage Data."""
    return {f.sha1: _coverage_file(f.sha1, str(f.path), f.name)
            for f in files("coverage") if f.role.startswith(GRID)}


def coverage_extras() -> list:
    """The LAT and log files of the active Coverage Data (kept, not read)."""
    return [f for f in files("coverage") if not f.role.startswith(GRID)]


# --------------------------------------------------------------------------- #
# what a file is — checked before it can be applied
# --------------------------------------------------------------------------- #
def _tokens(name: str) -> list:
    return re.split(r"[^a-z0-9]+", name.lower())


def inspect(kind: str, f: S.StoredFile) -> tuple[str, dict]:
    """(role, summary) of one stored file, or FileRejected."""
    path = str(f.path)
    if kind == "ep":
        ep = _shared.load_ep_all(path)
        if ep is None or ep.empty or "site_id" not in ep.columns:
            raise FileRejected("no LTE, UMTS or GSM sheet with R5 sites — is this the "
                               "Engineering Parameter tracker?")
        tech = (ep["technology"].astype(str).value_counts().to_dict()
                if "technology" in ep.columns else {})
        placed = 0
        if {"latitude", "longitude"} <= set(ep.columns):
            ll = ep.assign(lat=pd.to_numeric(ep["latitude"], errors="coerce"),
                           lon=pd.to_numeric(ep["longitude"], errors="coerce"))
            placed = int(ll.dropna(subset=["lat", "lon"])["site_id"].nunique())
        return EP_ROLE, {"sites": int(ep["site_id"].nunique()), "cells": int(len(ep)),
                         "by_tech": {str(k): int(v) for k, v in tech.items()},
                         "placed_sites": placed}
    if kind == "kpi":
        from rfopt.ingest.hourly_kpi import KpiFileInfo
        info = kpi_info(path)
        if info.kind not in KpiFileInfo.USABLE:
            raise FileRejected("not a raw hourly KPI export"
                               + (f" ({info.note})" if info.note else f" ({info.kind})"))
        idx = kpi_index(path)
        if idx.empty:
            raise FileRejected("the export has no hourly rows")
        return f"{info.kind} KPI", {
            "tech": info.kind, "kpis": len(info.all_kpis), "kpi_set": list(info.all_kpis),
            "object_sketch": S.object_sketch(idx["object"].unique()),
            "objects": int(idx["object"].nunique()),
            "sites": int(idx["site_id"].nunique()), "rows": int(len(idx)),
            "start": f"{idx['datetime'].min():%Y-%m-%d %H:%M}",
            "end": f"{idx['datetime'].max():%Y-%m-%d %H:%M}"}
    if kind == "kmz":
        from rfopt.ingest.kmz_sites import load_kmz_sites
        try:
            ks = load_kmz_sites(path, region=None)
        except Exception as exc:
            raise FileRejected(f"the KMZ could not be read ({exc})") from exc
        if ks is None or not len(ks.sectors):
            raise FileRejected("no site or sector placemarks in the file")
        return KMZ_ROLE, {"sites": int(ks.sectors["site_id"].nunique()),
                          "sectors": int(len(ks.sectors)), "cells": int(len(ks.cells))}
    if kind == "complaints":
        history_first = "cc process" in f.name.lower() or "history" in f.name.lower()
        if not history_first:
            try:
                return DAILY_TARGET, target_summary(parse_target(f.path.read_bytes(), f.name))
            except TargetFormatError as exc:
                first = exc
        else:
            first = None
        try:
            from rfopt.complaints import load_complaints
            cc = load_complaints(path, region="R5")
        except Exception as exc:
            raise FileRejected(f"not a Daily Target ({first or exc}) and not a complaint "
                               "history") from exc
        if cc is None or cc.empty:
            raise FileRejected(f"not a Daily Target ({first}) and no R5 complaint in it"
                               if first else "no R5 complaint in the file")
        return HISTORY, {"complaints": int(len(cc)),
                         "sites": int(cc["site_id"].nunique()) if "site_id" in cc else 0}
    if kind == "history":
        from rfopt.complaints.history import HistoryFormatError, summary
        try:
            df = _history(path)
        except HistoryFormatError as exc:
            raise FileRejected(f"not the R5 ticket history: {exc}") from exc
        return TICKET_HISTORY, summary(df)
    if kind == "coverage":
        from rfopt.ingest.coverage_grid import CoverageFormatError
        try:
            cf = _coverage_file(f.sha1, path, f.name)
        except (CoverageFormatError, ValueError, OSError) as exc:
            words = _tokens(f.name)
            if "lat" in words:
                return f"LAT · {f.name}", {"note": "kept with Coverage Data"}
            if "log" in words or "logs" in words or Path(f.name).suffix.lower() in (".log",
                                                                                   ".txt"):
                return f"Log · {f.name}", {"note": "kept with Coverage Data"}
            raise FileRejected(f"no coverage grid in it ({exc}); a LAT or log file is kept "
                               "when its name says LAT or log") from exc
        from _coverage import region_of
        return f"{GRID} · {region_of(cf)}", {
            "grids": cf.rows, "mrs": round(cf.mrs), "rsrp_column": cf.rsrp_column,
            "time_column": cf.time_column or "", "exported": cf.exported or "",
            "sheets": len(cf.sheets)}
    raise FileRejected(f"unknown resource {kind}")


def stage_files(kind: str, named: list, *, source: str = "upload") -> tuple:
    """Store and check the files the user uploaded, as the pending replacement:
    [(name, bytes)] → (the resource with its pending upload, or None when no
    file was taken; [(name, reason)] of the files that were not). The active
    dataset is not touched."""
    if kind == "kpi":
        _complete_kpi_summaries()
    good, bad, dropped = [], [], []
    for name, data in named:
        f = S.put_file(name, data)
        try:
            f.role, f.summary = inspect(kind, f)
        except FileRejected as exc:
            bad.append((name, str(exc)))
            dropped.append(f)
            continue
        except Exception as exc:                   # a reader crashed on it
            bad.append((name, f"could not be read ({type(exc).__name__}: {exc})"))
            dropped.append(f)
            continue
        if kind in S.MANY and any(a.sha1 == f.sha1 for a in S.resource(kind).files):
            bad.append((name, f"it is already active in {S.SPECS[kind].title} (the same "
                              "file)"))
            continue
        good.append(f)
    res = S.stage(kind, good, source=source) if good else None
    # a rejected file's stored copy goes, unless a taken file has the same content
    S.forget_unused([f for f in dropped if f.sha1 not in {g.sha1 for g in good}])
    return res, bad


def _complete_kpi_summaries() -> None:
    """KPI files stored before their summary listed their KPIs and cells get
    that now: whether a new export is a newer copy of one depends on it."""
    res = S.resource("kpi")
    for f in list(res.files) + list(res.pending):
        s = f.summary or {}
        if s.get("kpi_set") and s.get("object_sketch") and s.get("end"):
            continue
        try:
            _, summary = inspect("kpi", f)
        except Exception:                          # unreadable: it is simply not a copy
            continue
        S.set_summary("kpi", f.sha1, summary)


def summary_of(kind: str, f: S.StoredFile) -> dict:
    """The file's summary, worked out (once) when a moved-in file has none."""
    if f.summary:
        return f.summary
    try:
        _, summary = inspect(kind, f)
    except Exception:
        return {}
    S.set_summary(kind, f.sha1, summary)
    return summary


# --------------------------------------------------------------------------- #
# the app's own earlier storage, moved in once — never the computer's folders
# --------------------------------------------------------------------------- #
def _move_in(kind: str, named: list) -> None:
    good = []
    for name, path, role, summary in named:
        f = S.put_file(name, path)
        f.role, f.summary = role, summary
        good.append(f)
    if good:
        S.stage(kind, good, source="moved from the app's earlier storage")
        S.apply(kind)


def _move_earlier_uploads() -> list:
    """What the user once uploaded on the Sites and Complaints pages, kept by the
    app in its own folder (``RFOPT_CACHE_DIR``), becomes their active data. The
    app's own folder only: no Desktop, Downloads or any other place is looked
    at. Returns what could not be moved."""
    base = S.root().parent
    problems = []

    def ep():
        up, nm = base / "sm_uploaded_EP_tracker.xlsx", base / "sm_uploaded_EP_tracker.name.txt"
        if up.is_file() and not S.resource("ep").files:
            name = nm.read_text(encoding="utf-8").strip() if nm.is_file() else up.name
            _move_in("ep", [(name, up, EP_ROLE, {})])

    def kmz():
        up, nm = base / "sm_uploaded_R5_Sites.kmz", base / "sm_uploaded_R5_Sites.name.txt"
        if up.is_file() and not S.resource("kmz").files:
            name = nm.read_text(encoding="utf-8").strip() if nm.is_file() else up.name
            _move_in("kmz", [(name, up, KMZ_ROLE, {})])

    def complaints():
        old = legacy_active()
        if old is not None and not S.resource("complaints").files:
            _move_in("complaints", [(old.name, old.path, DAILY_TARGET,
                                     {"tickets": old.records, "columns": old.columns})])

    for step in (ep, kmz, complaints):
        try:
            step()
        except Exception as exc:                  # one bad file must not stop the rest
            problems.append(f"{step.__name__}: {exc}")
    return problems


@st.cache_resource(show_spinner=False)
def _ready(folder: str) -> bool:
    try:
        reg = S.load()           # an earlier, versioned registry is converted here
    except S.ResourceError:
        return False
    if not reg.migrated:
        _move_earlier_uploads()
        S.mark_migrated()
    return True


def ready() -> None:
    """Once per app run: the store is read (and, the very first time, the
    uploads the app kept in its own folder are moved in). Nothing on the
    computer is searched."""
    _ready(str(S.root()))


# --------------------------------------------------------------------------- #
# the sidebar: which data a page is using, and where to change it
# --------------------------------------------------------------------------- #
def link(label: str = "Manage in Data Resources") -> None:
    try:
        st.page_link(PAGE, label=label, icon=":material/database:", width="stretch")
    except Exception:                  # a page run on its own (tests) has no navigation
        st.caption(f"{label} (sidebar → Data → Data Resources)")


