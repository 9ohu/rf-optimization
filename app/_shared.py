"""Shared helpers for the RF Optimizer views."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import streamlit as st

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

SAMPLE_DIR = _ROOT / "sample_data"
# where the KPI Analysis Excel export is written (a user's own click); the app
# never reads from, or searches, any folder of the computer
DL = Path.home() / "Downloads"

PRIO_EMOJI = {"P1": "\U0001F534", "P2": "\U0001F7E0", "P3": "\U0001F7E1",
              "P4": "\U0001F7E2"}
SEV_EMOJI = {"critical": "\U0001F534", "warning": "\U0001F7E0", "ok": "\U0001F7E2",
             "info": "\u26AA"}


class NamedBytes(io.BytesIO):
    def __init__(self, data, name):
        super().__init__(data)
        self.name = name


def src_of(upload, path):
    """A real path (so the on-disk parquet cache applies) or a named buffer."""
    if upload is not None:
        return NamedBytes(upload.getvalue(), upload.name)
    return str(path) if path is not None else None


# Streamlit's cache hasher treats *any* io.IOBase carrying a `.name` as a file
# on disk and stats that name (hashing.py: `os.path.getmtime(obj_name)`), so a
# NamedBytes built from an upload blew up with FileNotFoundError on the bare
# file name before the loader ever ran. An upload is identified by its name and
# size instead - it is already in memory, there is nothing to stat.
SRC_HASH = {NamedBytes: lambda b: (b.name, b.getbuffer().nbytes)}


@st.cache_resource(show_spinner=False, hash_funcs=SRC_HASH)
def load_kpi(src):
    from rfopt.ingest.hourly_kpi import load_hourly_kpi
    return load_hourly_kpi(src)


_KPI_IDS = {"datetime", "granularity", "technology", "site_id", "sector", "sector_id",
            "cell_id", "duplex", "prefix"}


def load_kpi_files(paths: tuple):
    """Several 4G hourly exports as one: one row per cell and hour however many
    files there are (the most recent file last: its value is the one used). A
    file with none of the KPIs adds nothing but its notes."""
    from rfopt.ingest.hourly_kpi import HourlyKpiLoad, merge_hourly
    loads = [load_kpi(p) for p in paths]
    if len(loads) == 1:
        return loads[0]
    frames = [ld.df for ld in loads if set(ld.df.columns) - _KPI_IDS]
    return HourlyKpiLoad(df=merge_hourly(frames or [loads[0].df], key=("datetime", "cell_id")),
                         notes=[f"{Path(p).name}: {n}" for p, ld in zip(paths, loads)
                                for n in ld.notes])


@st.cache_resource(show_spinner=False,
                   hash_funcs=SRC_HASH)
def load_kpi_3g(src):
    from rfopt.ingest.hourly_kpi import load_hourly_kpi_3g
    return load_hourly_kpi_3g(src)


# --------------------------------------------------------------------------- #
# Back-compat helpers for the dormant pages in app/_advanced/
# --------------------------------------------------------------------------- #
def page_setup(title: str, icon: str = "\U0001F4E1") -> None:
    try:
        st.set_page_config(page_title=f"{title} - RF Optimizer", page_icon=icon,
                           layout="wide", initial_sidebar_state="expanded")
    except Exception:
        pass


def settings() -> dict:
    return st.session_state.setdefault("settings", {
        "technology": None, "region": "R5", "env_kind": "urban",
        "ret_unit": "tenths", "use_ai": False, "api_key": "",
        "threshold_overrides": {}})


def have_result() -> bool:
    return st.session_state.get("result") is not None


def get_result():
    return st.session_state.get("result")


def require_result() -> None:
    if not have_result():
        st.warning("This page needs the KPI engine. Run it from the "
                   "`app/_advanced/` pages (not registered by default).")
        st.stop()


def download_bytes(label: str, data: bytes, file_name: str, mime: str) -> None:
    st.download_button(label, data=data, file_name=file_name, mime=mime,
                       use_container_width=True)


def kpi_badge(value, rule, unit: str = "") -> str:
    from rfopt.kpi.thresholds import Severity
    if value is None or (isinstance(value, float) and value != value):
        return "—"
    sev = rule.evaluate(float(value)) if rule else Severity.OK
    dot = {Severity.OK: "\U0001F7E2", Severity.WARNING: "\U0001F7E0",
           Severity.CRITICAL: "\U0001F534"}[sev]
    return f"{dot} {value:.2f}{unit}"


def style_severity(df, col: str = "severity"):
    def _row(r):
        c = {"critical": "background-color:#fde2e1",
             "warning": "background-color:#fff4dd",
             "Critical": "background-color:#fde2e1",
             "Warning": "background-color:#fff4dd"}.get(str(r.get(col, "")), "")
        return [c] * len(r)
    try:
        return df.style.apply(_row, axis=1)
    except Exception:
        return df


@st.cache_resource(show_spinner=False)
def load_ep_all(path: str):
    """Every R5 cell in the EP tracker, all technologies, deactive sheets too."""
    import pandas as pd
    from rfopt.ingest.cellparams import load_cell_params
    frames = []
    for tech in ("LTE", "UMTS", "GSM"):
        try:
            frames.append(load_cell_params(path, technology=tech, region="R5",
                                           include_deactive=True).df)
        except Exception:
            continue                  # that technology's sheet isn't in this book
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
