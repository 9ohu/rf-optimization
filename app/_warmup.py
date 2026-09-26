"""The unified startup: every dataset and service the pages read, prepared once.

The pages keep their data in Streamlit's process-wide caches. This module calls
the very same cached loaders, with the same arguments, before any page opens,
so every page then opens from memory, with no loading step of its own. It only
uses the existing Data Resources and loaders: a dataset that is not applied is
skipped (the page says so, as it always did), and a loader that fails is
reported and left to its page, which shows its own message as before.

Tasks run in order and report as they go: the startup screen's progress is
the share of tasks done, its status the task running. Nothing is timed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Callable

# the four steps the startup screen shows
DATA, NETWORK, MAP, ANALYSIS = range(4)

_WARMED: set = set()                    # data signatures prepared in this process


@dataclass
class Task:
    step: int
    label: str                          # the status line while it runs
    run: Callable[[], object]
    needs: Callable[[], bool] | None = None   # its data is applied; None = always


def signature() -> tuple:
    """What the prepared caches depend on: the applied files of every resource
    and the Correlation Window."""
    import _resources as R
    import _complaints as C
    keys = []
    for kind in ("ep", "kmz", "kpi", "coverage", "history", "complaints"):
        keys.append((kind, tuple(sorted(f.sha1 for f in R.files(kind)))))
    t = R.target()
    keys.append(("target", getattr(t, "sha1", "")))
    return tuple(keys) + (C.window_setting(),)


def is_warm() -> bool:
    try:
        return signature() in _WARMED
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# what each page reads when it opens
# --------------------------------------------------------------------------- #
def _history():
    import _resources as R
    return R.history()


def _target():
    import _dashboard as D
    return D.target()


def _ep():
    import _resources as R
    import _complaints as C
    import _kpi_workspace as W
    import _shared
    import _site_data
    path = R.ep_path()
    _shared.load_ep_all(path)
    _site_data.load_ep_path(path)
    W.site_table(path)
    W.cell_ids(path)
    C._site_table(path)
    return R.ep_on_air()


def _kmz():
    import _resources as R
    import _dashboard as D
    import _relocate as RL
    import _site_data
    import _sleep
    f = R.kmz_file()
    _site_data.load_kmz_path(str(f.path))
    _sleep._kmz_sites(str(f.path), f.sha1)
    D.sites()
    return RL.serving_sectors()


def _kpi_exports():
    """Every export's header and objects, and the judged KPIs of each
    technology as KPI Analysis opens it (its first technology, whole window)."""
    import _resources as R
    import _kpi_workspace as W
    from rfopt.ingest.hourly_kpi import KpiFileInfo
    for _, group in R.kpi_groups():
        for path, _, _ in group:
            R.kpi_info(path)
            R.kpi_index(path)
    files = W.combine(R.kpi_groups())
    loaded = [(b, i) for b, i in files if i.kind in KpiFileInfo.USABLE]
    for kind in {i.kind for _, i in loaded}:
        usable = [(b, i) for b, i in loaded if i.kind == kind]
        for b, _ in usable:
            W.index_of(b)
        W.site_health(SimpleNamespace(usable=usable))
    return loaded


def _kpi_health():
    """The KPI Analysis Overview's site health and trend, as it first opens."""
    import _resources as R
    import _kpi_workspace as W
    from rfopt.ingest.hourly_kpi import KpiFileInfo
    loaded = [(b, i) for b, i in W.combine(R.kpi_groups()) if i.kind in KpiFileInfo.USABLE]
    order = ["4G", "3G", "2G", "Other"]
    techs = [t for t in order if t in {i.kind for _, i in loaded}]
    if not techs:
        return None
    usable = [(b, i) for b, i in loaded if i.kind == techs[0]]
    hkey, frames, h = W.site_health(SimpleNamespace(usable=usable))
    W.health_halves(hkey, frames, h.start, h.end)
    W.ep_sites()
    W.areas()
    return h


def _coverage():
    import _resources as R
    from _coverage import Coverage
    kept = R.coverage()
    return Coverage(kept) if kept else None


def _map_services():
    import _resources as R
    import _site_data
    import _tile_proxy
    from _map_assets import served_locally
    served_locally()
    _tile_proxy._start()
    return _site_data.topology_index(R.ep_path()) if R.ep_path() else None


def _dashboard():
    import _dashboard as D
    return D.kpi()


def _complaints():
    import _complaints as C
    return C.load_workspace(*C.window_setting())


def _sleep():
    import _sleep
    return _sleep.page_data()


def _has(kind: str) -> Callable[[], bool]:
    def check() -> bool:
        import _resources as R
        return bool(R.files(kind))
    return check


def _has_target() -> bool:
    import _resources as R
    return R.target() is not None


def tasks() -> list[Task]:
    return [
        Task(DATA, "Loading Data Resources", lambda: None),
        Task(DATA, "Loading Ticket History", _history, _has("history")),
        Task(DATA, "Loading Daily Target", _target, _has_target),
        Task(NETWORK, "Loading Network Data — EP Tracker", _ep, _has("ep")),
        Task(NETWORK, "Loading Network Data — Site KMZ", _kmz, _has("kmz")),
        Task(NETWORK, "Processing Network Data — KPI Exports", _kpi_exports, _has("kpi")),
        Task(MAP, "Preparing Map Services", _map_services),
        Task(MAP, "Preparing Map Services — Coverage Grid", _coverage, _has("coverage")),
        Task(ANALYSIS, "Preparing Analysis Engine — KPI Health", _kpi_health, _has("kpi")),
        Task(ANALYSIS, "Preparing Analysis Engine — Dashboard", _dashboard, _has("kpi")),
        Task(ANALYSIS, "Preparing Analysis Engine — Complaints", _complaints, _has_target),
        Task(ANALYSIS, "Preparing Analysis Engine — Sleep Analysis", _sleep, _has("history")),
    ]


def run(report: Callable[[dict], None] | None = None) -> list[dict]:
    """Prepare everything, in order; `report` hears each step. Returns what
    each task did: ok, skipped (its data is not applied) or failed."""
    say = report or (lambda _m: None)
    todo = tasks()
    say({"type": "begin", "total": len(todo)})
    out = []
    for k, t in enumerate(todo):
        say({"type": "task", "i": k, "step": t.step, "label": t.label})
        t0 = time.perf_counter()
        state, note = "ok", ""
        try:
            present = t.needs is None or t.needs()
        except Exception:
            present = False
        if not present:
            state = "skipped"              # not applied: the page says so itself
        else:
            try:
                t.run()
            except Exception as exc:       # left to its page, which reports it
                state, note = "failed", f"{type(exc).__name__}: {exc}"[:200]
        out.append({"label": t.label, "state": state, "note": note,
                    "seconds": round(time.perf_counter() - t0, 3)})
        say({"type": "done", "i": k, "step": t.step, "state": state})
    try:
        _WARMED.add(signature())
    except Exception:
        pass
    say({"type": "ready"})
    return out


__all__ = ["ANALYSIS", "DATA", "MAP", "NETWORK", "Task", "is_warm", "run", "signature", "tasks"]
