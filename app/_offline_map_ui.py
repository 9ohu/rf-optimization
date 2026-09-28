"""The Sites map's "Offline map" box: what the local map packs hold, and the
one explicit action that fetches them (`rfopt.geo.offline_basemap.update`).

The update runs in the background — the map stays usable — and a pack is only
replaced once its new file is complete. Nothing else on the map uses the
Internet.
"""

from __future__ import annotations

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


def start_update(keys, region) -> bool:
    """Start the update in the background; False when one is already running."""
    with _LOCK:
        if _JOB["running"]:
            return False
        _JOB.update(running=True, stage="starting", done=0, total=0, log=[], result=None)

    def run():
        try:
            _JOB["result"] = OB.update(tuple(keys), region=region, progress=_progress,
                                       log=_log)
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


def offline_map_box(region) -> None:
    """The box's contents: pack status, the update action and its progress."""
    st.html(_status_html())
    st.caption(f"Map packs folder: `{OB.folder()}`. The map reads them offline; "
               "the Internet is used only by the update below.")
    imagery = st.checkbox("Include street-scale satellite imagery (large download)",
                          key="sm_off_img")
    if st.button("Update offline map", icon=":material/download:", key="sm_off_go",
                 width="stretch", disabled=_JOB["running"],
                 help="Downloads OpenStreetMap streets and places, NASA night lights "
                      "and NASA day satellite for Iraq, at street level for the "
                      "sites' region. Uses the Internet once; the map is offline "
                      "afterwards."):
        keys = ["vector", "night", "earth"] + (["imagery"] if imagery else [])
        start_update(keys, region)
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
