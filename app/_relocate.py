"""The approved user locations, shared by the Sites map and Delay Tickets Analysis.

A location is stored only when "Approve & Re-analyze" is pressed on the Sites
map, keyed by the Ticket ID, and kept on disk beside the Data Resources
(`rfopt.resources.store.root`). Both pages re-run the same re-analysis
(`rfopt.complaints.relocate.reanalyse`) from it, on the same data and the same
correlation window, so a ticket never has two results.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

import pandas as pd
import streamlit as st

import _resources as R
from rfopt.resources.store import root


def _path():
    return root() / "relocations.json"


def load() -> dict:
    """Ticket ID -> {"lat", "lon", "at"} for every approved user location."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def approve(ticket_id: str, lat: float, lon: float) -> None:
    data = load()
    data[str(ticket_id)] = {"lat": round(float(lat), 6), "lon": round(float(lon), 6),
                            "at": datetime.now().strftime("%Y-%m-%d %H:%M")}
    _write(data)


def clear(ticket_id: str) -> None:
    data = load()
    if data.pop(str(ticket_id), None) is not None:
        _write(data)


def signature() -> tuple:
    """What a cache of re-analysed tickets is keyed on."""
    return tuple(sorted((k, v.get("lat"), v.get("lon")) for k, v in load().items()))


@st.cache_resource(show_spinner=False, max_entries=2)
def _sectors(kmz_path: str, kmz_sha: str, on_air: frozenset) -> pd.DataFrame:
    from rfopt.ingest.kmz_sites import load_kmz_sites
    from rfopt.ingest.site_status import apply_ep_status
    s = apply_ep_status(load_kmz_sites(kmz_path, region=None).sectors, on_air)
    s = s[s["air"].astype(str).eq("onair")].copy()
    for c in ("latitude", "longitude", "azimuth_deg"):
        s[c] = pd.to_numeric(s[c], errors="coerce")
    s["site_id"] = s["site_id"].astype(str).str.upper()
    s["sector_id"] = s["sector_id"].astype(str).str.upper()
    s = s.dropna(subset=["latitude", "longitude", "azimuth_deg"])
    return (s.drop_duplicates("sector_id")
            [["sector_id", "site_id", "latitude", "longitude", "azimuth_deg"]]
            .reset_index(drop=True))


def serving_sectors() -> pd.DataFrame:
    """Every on-air sector with its position and azimuth (KMZ, with the EP
    status rule) — what a user location can be served by."""
    f = R.kmz_file()
    if f is None:
        return pd.DataFrame(columns=["sector_id", "site_id", "latitude", "longitude",
                                     "azimuth_deg"])
    return _sectors(str(f.path), f.sha1, R.ep_on_air())
