"""The Sites map's own data loaders — in a module of their own, so the unified
startup (`_warmup`) prepares the very caches the page reads.

Moved out of `views/site_map.py` unchanged; the page imports them under their
old names. No spinner: they are prepared at startup, and a page never shows a
loading step of its own.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st


@st.cache_resource(show_spinner=False)
def load_kmz_path(path: str):
    """Every site of the KMZ. The file is the R5 site list itself, so nothing is
    filtered out of it: the region filter (BAS / NAS / EMA / SAM site-ID
    prefixes) dropped 12 of its 1,710 sites — USM0728-USM0733, UNS0140-UNS0143,
    UEA0727 and IFIA102 — although the KMZ places each of them with valid
    coordinates."""
    from rfopt.ingest.kmz_sites import load_kmz_sites
    return load_kmz_sites(path, region=None)


@st.cache_resource(show_spinner=False)
def load_ep_path(path: str) -> pd.DataFrame:
    """Every R5 cell in the Engineering Parameter tracker, all technologies.

    The Deactive sheets come too: a sector the KMZ still shows On Air but the
    tracker has deactivated is exactly the kind of divergence worth seeing.
    """
    from rfopt.ingest.cellparams import load_cell_params
    frames = []
    for tech in ("LTE", "UMTS", "GSM"):
        try:
            frames.append(load_cell_params(path, technology=tech, region="R5",
                                           include_deactive=True).df)
        except Exception:
            continue              # that technology's sheet isn't in this book
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


@st.cache_resource(show_spinner=False, max_entries=2)
def topology_index(path: str) -> tuple[dict, dict]:
    """Sector id → the tracker's topology values (Macro, Micro, Indoor …),
    and the same per site for the sectors the tracker numbers differently."""
    ep = load_ep_path(path)
    if ep.empty or not {"is_outdoor", "site_id", "sector_id"} <= set(ep.columns):
        return {}, {}
    d = pd.DataFrame({"site": ep["site_id"].astype(str).str.upper(),
                      "sector": ep["sector_id"].astype(str).str.upper(),
                      "t": ep["is_outdoor"].astype(str).str.strip()})
    d = d[~d["t"].str.lower().isin(["", "nan", "none"])]
    return (d.groupby("sector")["t"].agg(frozenset).to_dict(),
            d.groupby("site")["t"].agg(frozenset).to_dict())
