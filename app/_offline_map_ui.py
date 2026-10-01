"""The Sites map's "Offline map" box: what the local map packs hold, and the
one explicit action that fetches them (`rfopt.geo.offline_basemap.update`).

The update runs in the background — the map stays usable — and a pack is only
replaced once its new file is complete. Nothing else on the map uses the
Internet.

The building-scale satellite (Esri World Imagery, 0.5 m) is fetched around
the sites with the user's own ArcGIS API key, for the areas picked here; the
pack keeps what it has and adds each area, so a large network comes down one
area at a time.
"""

from __future__ import annotations

import os
import re
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
    `detail`: the building-scale pack's options (`OB.update`'s sites, detail_max,
    detail_radius_km, arcgis_key, detail_fresh)."""
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


# Esri's offline export (World Imagery for Export) is meant for this many tiles
# at a time; more is fetched one area at a time
_ESRI_BATCH = 150_000
_RADII = {"0.5 km": 0.5, "1 km": 1.0, "2 km": 2.0}
_ZOOMS = {"z18 · 0.5 m": 18, "z19 · 0.25 m": 19}


def _area(site_id) -> str:
    """BAS3171 -> BAS: the area a site ID names."""
    m = re.match(r"\s*([A-Za-z]+)", str(site_id))
    return m.group(1).upper() if m else "Other"


@st.cache_data(show_spinner=False, max_entries=16)
def _estimate(points: tuple, max_zoom: int, radius_km: float) -> tuple[int, float]:
    return OB.detail_estimate(list(points), max_zoom=max_zoom, radius_km=radius_km)


def _detail_options(sites) -> dict | None:
    """The building-scale imagery's options, or None when it cannot be fetched
    (no key, no site)."""
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
    if sites is None or not len(sites):
        st.warning("No site positions: the building-scale imagery is fetched around "
                   "the sites of the KMZ.")
        return None
    by_area: dict[str, list] = {}
    for sid, la, lo in zip(sites["site_id"], sites["latitude"], sites["longitude"]):
        by_area.setdefault(_area(sid), []).append((la, lo))
    areas = sorted(by_area, key=lambda a: -len(by_area[a]))
    pick = st.multiselect(
        "Areas", areas, default=areas, key="sm_off_areas",
        format_func=lambda a: f"{a} · {len(by_area[a]):,} sites",
        help="The sites whose surroundings are fetched. The pack keeps what it "
             "already has and adds the areas picked now.")
    c1, c2 = st.columns(2)
    radius = _RADII[c1.selectbox("Sharp area around each site", list(_RADII),
                                 key="sm_off_rad",
                                 help="Radius at full sharpness (zoom 18). It doubles "
                                      "at each zoom out (2 km at zoom 16, 8 km at 14).")]
    max_zoom = _ZOOMS[c2.selectbox("Sharpest zoom", list(_ZOOMS), key="sm_off_mz",
                                   help="Zoom 19 suits the 30 cm imagery of the "
                                        "large cities; it takes about 4x the tiles "
                                        "of zoom 18 at the top level.")]
    fresh = st.checkbox("Download the building-scale imagery again from scratch",
                        key="sm_off_fresh",
                        help="Otherwise the pack keeps every tile it has and fetches "
                             "only the missing ones — an update that was cut short "
                             "carries on where it stopped.")
    points = tuple(p for a in pick for p in OB.site_points(*zip(*by_area[a])))
    if not points:
        st.warning("Pick at least one area.")
        return None
    tiles, mb = _estimate(points, max_zoom, radius)
    st.caption(f"Around {len(points):,} sites: ≈ {tiles:,} tiles · ≈ "
               f"{mb / 1000:,.1f} GB (estimate; zooms {OB.DETAIL_MIN}–{max_zoom}).")
    if tiles > _ESRI_BATCH:
        st.warning(f"Esri's offline export is meant for up to {_ESRI_BATCH:,} tiles at "
                   "a time: pick fewer areas and run the update once per area — the "
                   "pack keeps each area it has.")
    if not key:
        st.warning("Enter an ArcGIS API key to fetch the building-scale imagery.")
        return None
    return {"sites": list(points), "detail_max": max_zoom, "detail_radius_km": radius,
            "arcgis_key": key, "detail_fresh": fresh}


def offline_map_box(region, sites=None) -> None:
    """The box's contents: pack status, the update action and its progress.
    `sites`: the sites (site_id, latitude, longitude) the building-scale
    imagery is fetched around."""
    st.html(_status_html())
    st.caption(f"Map packs folder: `{OB.folder()}`. The map reads them offline; "
               "the Internet is used only by the update below.")
    imagery = st.checkbox("Regional satellite: Sentinel-2, 10 m (free; large download)",
                          key="sm_off_img")
    detail = st.checkbox("Building-scale satellite around the sites: Esri World "
                         "Imagery, 0.5 m (ArcGIS key; very large download)",
                         key="sm_off_det")
    opts = _detail_options(sites) if detail else None
    if st.button("Update offline map", icon=":material/download:", key="sm_off_go",
                 width="stretch", disabled=_JOB["running"] or (detail and opts is None),
                 help="Downloads OpenStreetMap streets and places, NASA night lights "
                      "and NASA day satellite for Iraq, at street level for the "
                      "sites' region, and the satellite packs ticked above. Uses the "
                      "Internet once; the map is offline afterwards."):
        keys = (["vector", "night", "earth"] + (["imagery"] if imagery else [])
                + (["detail"] if opts else []))
        start_update(keys, region, **(opts or {}))
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
