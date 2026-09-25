"""Fast I/O helpers: calamine Excel reads + a parquet cache for big parses."""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path

import pandas as pd

try:
    import python_calamine  # noqa: F401
    _EXCEL_ENGINE = "calamine"
except Exception:                       # pragma: no cover
    _EXCEL_ENGINE = None

_CACHE_DIR = Path(os.environ.get("RFOPT_CACHE_DIR",
                                 Path.home() / ".rfopt_cache"))


def read_excel(path_or_buf, **kw) -> pd.DataFrame:
    """pandas.read_excel with the fast engine when available."""
    if _EXCEL_ENGINE and "engine" not in kw:
        try:
            return pd.read_excel(path_or_buf, engine=_EXCEL_ENGINE, **kw)
        except Exception:
            pass
    return pd.read_excel(path_or_buf, **kw)


def excel_file(path_or_buf):
    if _EXCEL_ENGINE:
        try:
            return pd.ExcelFile(path_or_buf, engine=_EXCEL_ENGINE)
        except Exception:
            pass
    return pd.ExcelFile(path_or_buf)


def _fingerprint(path_or_buf, extra: str = "") -> str | None:
    """Stable id for a source file (path + size + mtime), or None for buffers."""
    p = getattr(path_or_buf, "name", path_or_buf)
    try:
        st = os.stat(p)
    except (OSError, TypeError):
        return None
    raw = f"{p}|{st.st_size}|{int(st.st_mtime)}|{extra}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def cached_parse(path_or_buf, parser, *, tag: str, extra: str = ""):
    """Return ``parser(path_or_buf)`` as a DataFrame, via a parquet cache.

    The cache key is the source file's path+size+mtime (+``extra``). Buffers
    (uploads) are never cached. Set ``RFOPT_CACHE_DIR`` to relocate the cache;
    it is safe to delete at any time.
    """
    fp = _fingerprint(path_or_buf, extra)
    if fp is None:
        return parser(path_or_buf)
    cache = _CACHE_DIR / f"{tag}_{fp}.parquet"
    if cache.exists():
        try:
            return pd.read_parquet(cache)
        except Exception:
            pass
    df = parser(path_or_buf)
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache, index=False)
        # keep the cache small: drop all but the 6 newest files per tag
        files = sorted(_CACHE_DIR.glob(f"{tag}_*.parquet"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[6:]:
            old.unlink(missing_ok=True)
    except Exception:
        pass
    return df
