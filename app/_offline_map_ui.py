"""The Sites map's "Offline map" box: what the local map packs hold, and the
one explicit action that fetches them (`rfopt.geo.offline_basemap.update`).

The update runs in the background — the map stays usable — and a pack is only
replaced once its new file is complete. Nothing else on the map uses the
Internet.

The building-scale satellite (Esri World Imagery, 0.5 m) is fetched with the
user's own ArcGIS API key for whole governorates — Basrah, Nasiriyah,
Samawah, Amarah — inside each one's boundary, sites or not. One pack per
region; each grows in place and carries on where an earlier update stopped.
"""

from __future__ import annotations

import os
import threading

import streamlit as st

from rfopt.geo import offline_basemap as OB

_LOCK = threading.Lock()
_JOB: dict = {"running": False, "stage": "", "done": 0, "total": 0, "log": [],
              "result": None}


def _progress(stage: str, done: int, total: int) -> None:
    _JOB.update(stage=stage, done=int(done), total=int(total))


def _log(msg: str) -> None:
    _JOB["log"] = (_JOB["log"] + [str(msg)])[-12:]


def start_update(keys, region, **detail) -> bool:
    """Start the update in the background; False when one is already running.
    `detail`: the building-scale packs' options (`OB.update`'s detail_max,
    arcgis_key, detail_fresh)."""
    with _LOCK:
        if _JOB["running"]:
            return False
        _JOB.update(running=True, stage="starting", done=0, total=0, log=[], result=None)

    def run():
        try:
            _JOB["result"] = OB.update(tuple(keys), region=region, progress=_progress,
                                       log=_log, **detail)
        except Exception as e:                     # never leave the job "running"
            _JOB["result"] = {"update": f"failed: {e}"}
        finally:
            _JOB["running"] = False

    threading.Thread(target=run, name="rf-offline-map-update", daemon=True).start()
    return True


def _status_html() -> str:
    rows = []
    for key, info in OB.status().items():
        pack = OB.PACKS[key]
        if info is None:
            state = '<span style="color:#FB923C">not installed</span>'
        else:
            state = (f'<span style="color:#22C55E">✓</span> {info["size_mb"]:,.1f} MB · '
                     f'z{info["min_zoom"]}–{info["max_zoom"]} · '
                     f'{info["updated"]:%d %b %Y}')
        rows.append(f"<div style='display:flex;justify-content:space-between;gap:8px;"
                    f"font-size:11.5px;padding:2px 0'><span style='color:#94A3B8'>"
                    f"{pack.title}</span><span style='text-align:right;white-space:nowrap'>{state}"
                    "</span></div>")
    return "".join(rows)


_ZOOMS = {"z16 · 2 m": 16, "z17 · 1 m": 17, "z18 · 0.5 m": 18, "z19 · 0.25 m": 19}


@st.cache_data(show_spinner=False, max_entries=16)
def _estimate(codes: tuple, max_zoom: int) -> dict:
    return OB.region_estimate(codes, max_zoom=max_zoom)


def _detail_options() -> tuple[list, dict] | None:
    """The building-scale imagery's packs and options, or None when it cannot
    be fetched (no key, no region)."""
    env = any(os.environ.get(v, "").strip() for v in ("RFOPT_ARCGIS_KEY", "ARCGIS_API_KEY"))
    saved = OB.arcgis_key()
    key = st.text_input(
        "ArcGIS API key", value=saved, type="password", key="sm_off_key",
        disabled=env,
        help="Esri World Imagery (Maxar, about 0.3–0.6 m in Iraq's cities) is "
             "licensed imagery: it is fetched with your own ArcGIS key (an ArcGIS "
             "Online organisational account, or an ArcGIS Location Platform "
             "developer account — free tier — with an API key that has the "
             "basemaps privilege) from Esri's World Imagery (for Export) service, "
             "the one Esri provides for offline use. The key is kept on this PC "
             "only, never in the map packs.").strip()
    if env:
        st.caption("Key from the RFOPT_ARCGIS_KEY / ARCGIS_API_KEY environment variable.")
    elif key != saved:
        OB.save_arcgis_key(key)
    names = OB.REGION_NAMES
    # a region already (partly) downloaded is picked: Update carries it on
    started = [c for c in names if OB.pack_path(OB.detail_key(c)).is_file()]
    pick = st.multiselect(
        "Governorate / Region boundary", list(names), default=started,
        key="sm_off_areas", format_func=lambda c: f"{c} · {names[c]}",
        help="The whole governorate is downloaded — every tile inside its boundary, "
             "with or without sites — so the map can be moved and zoomed anywhere "
             "in it offline. One pack per region.")
    max_zoom = _ZOOMS[st.selectbox("Sharpest zoom", list(_ZOOMS), index=2, key="sm_off_mz",
                                   help="Each zoom in takes about 4x the tiles of the one "
                                        "before. Zoom 18 shows buildings and rooftops; 19 "
                                        "suits the 30 cm imagery of the large cities.")]
    fresh = st.checkbox("Download the building-scale imagery again from scratch",
                        key="sm_off_fresh",
                        help="Otherwise each region's pack keeps every tile it has and "
                             "fetches only the missing ones — an update that was cut "
                             "short carries on where it stopped.")
    if not pick:
        st.warning("Pick at least one region.")
        return None
    est = _estimate(tuple(pick), max_zoom)
    tiles = sum(n for n, _ in est.values())
    gb = sum(mb for _, mb in est.values()) / 1000
    lines = " · ".join(f"{names[c]} ≈ {est[c][0]:,} tiles ({est[c][1] / 1000:,.1f} GB)"
                       for c in pick)
    st.caption(f"{lines}. Total ≈ {tiles:,} tiles · ≈ {gb:,.1f} GB (estimate; the "
               f"region boundaries, zooms {OB.DETAIL_MIN}–{max_zoom}). Each tile is one "
               "request on your ArcGIS account.")
    if not key:
        st.warning("Enter an ArcGIS API key to fetch the building-scale imagery.")
        return None
    return ([OB.detail_key(c) for c in pick],
            {"detail_max": max_zoom, "arcgis_key": key, "detail_fresh": fresh})


def offline_map_box(region) -> None:
    """The box's contents: pack status, the update action and its progress."""
    st.html(_status_html())
    st.caption(f"Map packs folder: `{OB.folder()}`. The map reads them offline; "
               "the Internet is used only by the update below.")
    imagery = st.checkbox("Regional satellite: Sentinel-2, 10 m (free; large download)",
                          key="sm_off_img")
    detail = st.checkbox("Building-scale satellite for whole regions: Esri World "
                         "Imagery, 0.5 m (ArcGIS key; very large download)",
                         key="sm_off_det")
    opts = _detail_options() if detail else None
    if st.button("Update offline map", icon=":material/download:", key="sm_off_go",
                 width="stretch", disabled=_JOB["running"] or (detail and opts is None),
                 help="Downloads OpenStreetMap streets and places, NASA night lights "
                      "and NASA day satellite for Iraq, at street level for the "
                      "sites' region, and the satellite packs ticked above. Uses the "
                      "Internet once; the map is offline afterwards."):
        packs, kw = opts or ([], {})
        keys = ["vector", "night", "earth"] + (["imagery"] if imagery else []) + packs
        start_update(keys, region, **kw)
    _job_view()


@st.fragment(run_every=2.0)
def _job_view() -> None:
    if _JOB["running"]:
        total = max(_JOB["total"], 1)
        st.progress(min(_JOB["done"] / total, 1.0),
                    text=f"{_JOB['stage']} · {_JOB['done']:,} / {_JOB['total']:,}")
        st.session_state["sm_off_seen"] = False
    elif _JOB["result"] is not None:
        for key, res in _JOB["result"].items():
            title = OB.PACKS[key].title if key in OB.PACKS else key
            (st.caption if res.startswith("ok") else st.warning)(f"{title}: {res}")
        if not st.session_state.get("sm_off_seen"):
            # the new packs are drawn at once
            st.session_state["sm_off_seen"] = True
            st.rerun(scope="app")


__all__ = ["offline_map_box", "start_update"]
