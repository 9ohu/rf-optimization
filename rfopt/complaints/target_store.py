"""The active Daily Target, as the Complaints pages read it.

The file itself lives in the Data Resources store (Complaint Data): an upload
is parsed and validated first, and only a valid file is staged and applied, so
a broken upload never costs the last good one. `load_active` reads the active
Complaint Data's Daily Target. The old single-file store
(``complaints/daily_target.json``) is only read once, to move it into Data
Resources.
"""

from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from rfopt.complaints.worklist import load_worklist, read_source

REQUIRED = ("ticket_id", "site_id", "problem_time")
_META = "daily_target.json"


class TargetFormatError(ValueError):
    """The upload is not a usable Daily Target file."""


def store_dir() -> Path:
    base = Path(os.environ.get("RFOPT_CACHE_DIR", Path.home() / ".rfopt_cache"))
    return base / "complaints"


class _Named(io.BytesIO):
    def __init__(self, data: bytes, name: str):
        super().__init__(data)
        self.name = name


@dataclass
class TargetDataset:
    name: str
    uploaded_at: str
    records: int
    size: int
    sha1: str
    path: Path
    columns: list = field(default_factory=list)

    def read_bytes(self) -> bytes:
        return self.path.read_bytes()


def source_columns(data: bytes, name: str, rows) -> pd.DataFrame:
    """Every column of the original file for the given tickets, in their order
    (`rows`: the tickets' `_row`, from `parse_target`)."""
    raw = read_source(_Named(data, name))
    return raw.iloc[list(rows)].reset_index(drop=True)


def parse_target(data: bytes, name: str) -> pd.DataFrame:
    """The tickets in a Daily Target export, or TargetFormatError."""
    try:
        wl = load_worklist(_Named(data, name))
    except Exception as exc:                      # unreadable workbook / CSV
        raise TargetFormatError(f"could not read the file ({exc})") from exc
    missing = [c for c in REQUIRED if c not in wl.columns]
    if missing:
        found = ", ".join(map(str, wl.attrs.get("_src_cols", [])[:24])) or "none"
        raise TargetFormatError(
            "no column for " + ", ".join(m.replace("_", " ") for m in missing)
            + f" (columns found: {found})")
    tid = wl["ticket_id"].fillna("").astype(str).str.strip()
    wl = wl[tid.ne("") & ~tid.str.lower().isin(["nan", "none"])]
    if wl.empty:
        raise TargetFormatError("the sheet has no tickets")
    if wl["problem_time"].notna().mean() < 0.5:
        raise TargetFormatError("most Problem Time values are not dates")
    return wl.reset_index(drop=True)


DAILY_TARGET, HISTORY = "Daily Target", "Complaint history"


def legacy_active() -> TargetDataset | None:
    """The Daily Target of the old single-file store, if one was uploaded there."""
    folder = store_dir()
    mp = folder / _META
    if not mp.is_file():
        return None
    try:
        meta = json.loads(mp.read_text(encoding="utf-8"))
        ds = TargetDataset(name=meta["name"], uploaded_at=meta["uploaded_at"],
                           records=int(meta["records"]), size=int(meta["size"]),
                           sha1=meta["sha1"], path=folder / meta["file"],
                           columns=list(meta.get("columns", [])))
    except (OSError, ValueError, KeyError):
        return None
    return ds if ds.path.is_file() else None


def target_summary(wl: pd.DataFrame) -> dict:
    return {"tickets": int(len(wl)),
            "columns": [str(c) for c in wl.attrs.get("_src_cols", [])]}


def save_target(data: bytes, name: str, *, now: datetime | None = None) -> TargetDataset:
    """Validate a Daily Target upload, then stage and apply it as Complaint Data."""
    from rfopt.resources import store
    wl = parse_target(data, name)                 # invalid -> raises, nothing touched
    f = store.put_file(name, data)
    f.role, f.summary = DAILY_TARGET, target_summary(wl)
    store.stage("complaints", [f])
    store.apply("complaints")
    return load_active()


def load_active() -> TargetDataset | None:
    """The active Complaint Data's Daily Target, or None."""
    from rfopt.resources import store
    try:
        res = store.resource("complaints")
    except store.ResourceError:
        return None
    f = next((f for f in res.files if f.role == DAILY_TARGET), None)
    if f is None or not f.path.is_file():
        return None
    return TargetDataset(name=f.name, uploaded_at=(f.uploaded_at or res.applied_at or "")[:16],
                         records=int(f.summary.get("tickets", 0)), size=f.size,
                         sha1=f.sha1, path=f.path, columns=list(f.summary.get("columns", [])))
