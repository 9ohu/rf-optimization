"""The Sites map's KPI, judged cell by cell (Site Map only).

The map used to show a sector by one value: the mean of all its cells over the
whole export (the "Whole window"), or the mean of its cells at a timestamp —
read against one line, the FDD one even for a TDD cell's UL interference. A
cell far over its line was hidden twice: by the sector's other cells, and by
its own quiet hours. For a KPI with a judged threshold it is now:

* every cell judged on its own — every hour (Per Hour), or on its daily value
  (Per Day: the cell's own daily mean, never mixed with other cells) — against
  its own line: a TDD cell's UL interference against the TDD line
  (`rfopt.kpi.thresholds.hourly_severity`, the existing rules, unchanged);
* a sector (or a per-NodeB site) at an hour / a day takes its worst cell: the
  most severe, then the worst value; over the whole window, its worst
  cell-hour (cell-day). One critical cell is a critical sector;
* the cells that were critical, with their own series, for the drawer's
  Critical Cells and History.

A KPI without a judged threshold keeps the aggregate of the sector's cells
(mean / sum) — there is no Critical to hide. The 4G layer filter keeps only the
TDD or FDD cells (the export's Cell FDD TDD Indication; for an export without
it, the KMZ's band of the cell).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

PER_HOUR, PER_DAY = "Per Hour", "Per Day"
PERIODS = (PER_HOUR, PER_DAY)
TDD, FDD, ALL = "TDD", "FDD", "All"
LAYERS = (TDD, FDD, ALL)
_DUPLEX = {TDD: "CELL_TDD", FDD: "CELL_FDD"}


@dataclass
class MapKpi:
    """One KPI on the map: the value (and, judged, the severity) of every
    sector / per-NodeB site over the whole window and per frame."""
    per_sector: pd.Series                   # sector -> whole-window value
    per_site: pd.Series                     # site (per-NodeB rows) -> value
    window: tuple                           # first / last timestamp of the export
    sec_t: pd.DataFrame                     # sector x frame
    site_t: pd.DataFrame                    # site x frame
    period: str = PER_HOUR
    layer: str = ALL
    judged: bool = False                    # judged cell by cell on a threshold
    sev_sector: pd.Series | None = None     # 0 / 1 / 2, whole window
    sev_site: pd.Series | None = None
    sev_sec_t: pd.DataFrame | None = None
    sev_site_t: pd.DataFrame | None = None
    cells: pd.DataFrame | None = None       # lvl, key, object, duplex, frame, value, sev
    has_duplex: bool = False                # the cells' FDD / TDD is known


def tdd_band(band_label) -> pd.Series:
    """A KMZ cell's band is TDD (L2300(TDD), L2600(TDD))."""
    return pd.Series(band_label).astype(str).str.upper().str.contains("TDD", regex=False)


def duplex_of_cells(cells: pd.DataFrame) -> dict:
    """KMZ 4G cell name (upper) -> CELL_TDD / CELL_FDD, from its band."""
    if cells is None or cells.empty or "cell_name" not in cells.columns:
        return {}
    c = cells[cells["technology"].astype(str) == "4G"]
    tdd = tdd_band(c["band_label"]).to_numpy()
    return dict(zip(c["cell_name"].astype(str).str.strip().str.upper(),
                    np.where(tdd, _DUPLEX[TDD], _DUPLEX[FDD])))


def with_layer(df: pd.DataFrame, layer: str = ALL, duplex_of: dict | None = None):
    """The rows, with a clean `duplex` (CELL_TDD / CELL_FDD / NaN), and only
    the TDD or FDD cells when a layer is picked. Returns (rows, known)."""
    if "duplex" in df.columns:
        dup = df["duplex"].astype(str).str.strip().str.upper()
        dup = dup.where(dup.isin(list(_DUPLEX.values())))
    else:
        dup = pd.Series(np.nan, index=df.index, dtype=object)
    if duplex_of and dup.isna().any():
        dup = dup.fillna(df["object"].astype(str).str.strip().str.upper().map(duplex_of))
    known = bool(dup.notna().any())
    out = df.assign(duplex=dup)
    if layer in _DUPLEX:
        out = out[out["duplex"] == _DUPLEX[layer]]
    return out, known


def _frames(t: pd.Series, period: str) -> pd.Series:
    return t.dt.floor("D") if period == PER_DAY else t


def _pivot(rows: pd.DataFrame, by: str, col: str, how) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame()
    p = rows.pivot_table(index=by, columns="frame", values=col, aggfunc=how, observed=True)
    p.index = p.index.astype(str)
    return p


def evaluate(df: pd.DataFrame, kpi: str, *, rule=None, period: str = PER_HOUR,
             layer: str = ALL, duplex_of: dict | None = None) -> MapKpi:
    """The KPI of every sector / site: judged cell by cell when `rule` is the
    KPI's judged threshold, else the aggregate of its cells (as before)."""
    from rfopt.kpi.thresholds import hourly_severity
    from rfopt.kpi.trends import agg_how

    window = (str(df["datetime"].min()), str(df["datetime"].max()))
    d, known = with_layer(df[df[kpi].notna()], layer, duplex_of)
    no_sector = d["sector_id"].astype(str).str.endswith("-S0").to_numpy()
    d = d.assign(frame=_frames(d["datetime"], period),
                 lvl=np.where(no_sector, "site", "sector"),
                 key=np.where(no_sector, d["site_id"].astype(str), d["sector_id"].astype(str)))
    how = agg_how(kpi)
    if rule is None:
        sec, site = d[d["lvl"] == "sector"], d[d["lvl"] == "site"]
        return MapKpi(sec.groupby("key", observed=True)[kpi].agg(how),
                      site.groupby("key", observed=True)[kpi].agg(how), window,
                      _pivot(sec, "key", kpi, how), _pivot(site, "key", kpi, how),
                      period=period, layer=layer, has_duplex=known)

    # every cell on its own: each hour, or its own daily mean
    cols = ["lvl", "key", "object", "frame"]
    if period == PER_DAY:
        cell = (d.groupby(cols, observed=True, sort=False)
                .agg(value=(kpi, "mean"), duplex=("duplex", "first")).reset_index())
    else:
        cell = d[cols + ["duplex", kpi]].rename(columns={kpi: "value"}).reset_index(drop=True)
    cell["sev"] = hourly_severity(rule, cell["value"].to_numpy(dtype=float),
                                  cell["duplex"].to_numpy() if known else None)
    # a sector's worst cell: the most severe, then the worst value
    worse = cell["value"].to_numpy(dtype=float) * (1.0 if rule.direction != "up" else -1.0)
    top = cell.iloc[np.lexsort((-worse, -cell["sev"].to_numpy()))]
    per_t = top.drop_duplicates(["lvl", "key", "frame"])
    per_w = top.drop_duplicates(["lvl", "key"])

    def series(rows, lvl, col):
        r = rows[rows["lvl"] == lvl]
        return pd.Series(r[col].to_numpy(), index=r["key"].astype(str).to_numpy())

    def table(lvl, col):
        r = per_t[per_t["lvl"] == lvl]
        if r.empty:
            return pd.DataFrame()
        p = r.pivot(index="key", columns="frame", values=col)
        p.index = p.index.astype(str)
        return p.astype(float)

    return MapKpi(series(per_w, "sector", "value"), series(per_w, "site", "value"), window,
                  table("sector", "value"), table("site", "value"), period=period,
                  layer=layer, judged=True,
                  sev_sector=series(per_w, "sector", "sev").astype(float),
                  sev_site=series(per_w, "site", "sev").astype(float),
                  sev_sec_t=table("sector", "sev"), sev_site_t=table("site", "sev"),
                  cells=cell, has_duplex=known)


def critical_cells(m: MapKpi, lvl: str, key: str, times: list, rule) -> list[dict]:
    """The cells of one sector (lvl "sector") or per-NodeB site that were
    critical in an evaluated hour / day: their full names, how many hours /
    days, their own series over `times` and the line each was judged on."""
    from rfopt.kpi.thresholds import rule_for_duplex

    if not m.judged or m.cells is None or rule is None:
        return []
    c = m.cells[(m.cells["lvl"] == lvl) & (m.cells["key"] == str(key))]
    if c.empty:
        return []
    out = []
    for name, rows in c.groupby("object", sort=True):
        n = int((rows["sev"] >= 2).sum())
        if not n:
            continue
        dup = rows["duplex"].dropna()
        dup = str(dup.iloc[0]) if len(dup) else ""
        line = rule_for_duplex(rule, dup) or rule
        s = rows.set_index("frame")["value"]
        s = s[~s.index.duplicated(keep="last")].reindex(times)
        out.append({"name": str(name),
                    "layer": TDD if dup == _DUPLEX[TDD] else FDD if dup == _DUPLEX[FDD] else "",
                    "n": n, "line": float(line.critical),
                    "series": [float(v) if pd.notna(v) else None for v in s.to_numpy()]})
    return out


def band_keys(values, sev, scheme) -> pd.Series:
    """The band of every value under a scheme, its colour decided by the
    severity of the cell behind it: critical / warning where a cell was over
    its own line, a shade of OK where none was — even when the value sits past
    the scheme's line (a TDD cell under its looser TDD line)."""
    from _kpi_map import apply_scheme

    v = pd.to_numeric(pd.Series(values), errors="coerce").reset_index(drop=True)
    keys = apply_scheme(v, scheme).reset_index(drop=True)
    if sev is None or scheme.rule is None:
        return keys
    s = pd.to_numeric(pd.Series(sev), errors="coerce").reset_index(drop=True)
    has = v.notna() & s.notna()
    keys = keys.where(~(has & (s >= 2)), "critical")
    keys = keys.where(~(has & (s == 1)), "warning")
    ok = has & (s == 0) & keys.isin(["critical", "warning"])
    if ok.any() and scheme.ok_parts:
        near = scheme.ok_parts[0 if scheme.rule.direction == "up" else -1][0]
        keys = keys.where(~ok, near)
    return keys


__all__ = ["ALL", "FDD", "LAYERS", "MapKpi", "PERIODS", "PER_DAY", "PER_HOUR", "TDD",
           "band_keys", "critical_cells", "duplex_of_cells", "evaluate", "tdd_band",
           "with_layer"]
