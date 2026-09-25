"""End-to-end orchestration: file -> canonical -> KPI -> diagnosis -> actions.

    result = run_analysis("kpi.xlsx", site_db="sites.csv")
    result.diagnoses        # list[Diagnosis]
    result.recommendations  # list[Recommendation]
    result.breaches         # list[KpiBreach]
    result.trends           # list[TrendResult]
    result.agg_cell / result.agg_site / result.agg_hourly   # DataFrames
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from rfopt.actions.geometry import haversine_m
from rfopt.actions.propagation import Environment
from rfopt.actions.recommend import CellContext, Recommendation, recommend_for_cell
from rfopt.diagnosis import Diagnosis, diagnose_frame
from rfopt.diagnosis.engine import diagnose_entity
from rfopt.ingest import load_kpi_file
from rfopt.ingest.loader import LoadResult
from rfopt.kpi import (KpiBreach, TrendResult, aggregate, busy_hour_table,
                       detect_anomalies, evaluate_thresholds, load_thresholds)


@dataclass
class AnalysisResult:
    load: LoadResult
    technology: str
    thresholds: object
    agg_cell: pd.DataFrame
    agg_sector: pd.DataFrame
    agg_site: pd.DataFrame
    agg_hourly: pd.DataFrame
    busy_hour: pd.DataFrame
    breaches: list[KpiBreach]
    trends: list[TrendResult]
    diagnoses: list[Diagnosis]
    recommendations: list[Recommendation]
    site_db: pd.DataFrame | None = None
    isd_by_site: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    # -- convenience views ------------------------------------------------
    def summary(self) -> dict:
        crit = sum(1 for d in self.diagnoses if d.severity == "critical")
        return {
            "technology": self.technology,
            "rows": self.load.n_rows,
            "sites": self.load.n_sites,
            "cells": self.load.n_cells,
            "granularity": self.load.granularity,
            "kpi_breaches": len(self.breaches),
            "degrading_trends": sum(1 for t in self.trends
                                    if t.verdict in ("degrading", "step-down")),
            "diagnoses": len(self.diagnoses),
            "critical_diagnoses": crit,
            "recommendations": len(self.recommendations),
        }

    def diagnoses_df(self) -> pd.DataFrame:
        return pd.DataFrame([d.as_dict() for d in self.diagnoses])

    def recommendations_df(self) -> pd.DataFrame:
        return pd.DataFrame([r.as_dict() for r in self.recommendations])

    def breaches_df(self) -> pd.DataFrame:
        return pd.DataFrame([b.as_dict() for b in self.breaches])

    def trends_df(self) -> pd.DataFrame:
        return pd.DataFrame([t.as_dict() for t in self.trends])


# --------------------------------------------------------------------------- #
def _load_site_db(site_db) -> pd.DataFrame | None:
    if site_db is None:
        return None
    if isinstance(site_db, pd.DataFrame):
        df = site_db.copy()
    else:
        p = Path(getattr(site_db, "name", str(site_db)))
        df = (pd.read_csv(site_db) if p.suffix.lower() in (".csv", ".txt")
              else pd.read_excel(site_db))
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    ren = {
        "cell": "cell_id", "cellname": "cell_id", "cell_name": "cell_id",
        "enodeb": "site_id", "enodeb_name": "site_id", "site": "site_id",
        "lat": "latitude", "lon": "longitude", "long": "longitude",
        "height": "antenna_height_m", "antenna_height": "antenna_height_m",
        "azimuth": "azimuth_deg", "mechanical_tilt": "mech_tilt_deg",
        "electrical_tilt": "elec_tilt_deg", "ret": "elec_tilt_deg",
        "m_tilt": "mech_tilt_deg", "e_tilt": "elec_tilt_deg",
        "vbeamwidth": "vbw_deg", "hbeamwidth": "hbw_deg",
    }
    df = df.rename(columns={k: v for k, v in ren.items() if k in df.columns})
    for c in ("latitude", "longitude", "antenna_height_m", "azimuth_deg",
              "mech_tilt_deg", "elec_tilt_deg", "vbw_deg", "hbw_deg"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _isd_by_site(site_db: pd.DataFrame | None) -> dict[str, float]:
    if site_db is None or not {"site_id", "latitude", "longitude"} <= set(site_db.columns):
        return {}
    sites = (site_db.dropna(subset=["latitude", "longitude"])
             .groupby("site_id")[["latitude", "longitude"]].mean())
    if len(sites) < 2:
        return {}
    coords = sites.to_numpy()
    ids = sites.index.tolist()
    out: dict[str, float] = {}
    for i, sid in enumerate(ids):
        ds = []
        for j in range(len(ids)):
            if i == j:
                continue
            ds.append(haversine_m(coords[i][0], coords[i][1],
                                  coords[j][0], coords[j][1]))
        ds.sort()
        near = ds[:3] if len(ds) >= 3 else ds
        out[sid] = float(np.mean(near)) if near else 1000.0
    return out


def _cell_context(cell_row: pd.Series, sdb_row: pd.Series | None,
                  env_kind: str, isd: float | None,
                  sibling_load: dict[str, float] | None,
                  ret_unit: str) -> CellContext:
    k = {c: (float(cell_row[c]) if pd.notna(cell_row[c]) else None)
         for c in cell_row.index
         if isinstance(cell_row[c], (int, float, np.floating, np.integer))}
    ctx = CellContext(
        cell_id=str(cell_row.get("cell_id", "")),
        site_id=str(cell_row.get("site_id", "")),
        technology=str(cell_row.get("technology", "LTE")),
        band=str(cell_row.get("band", "")),
        env=Environment(kind=env_kind),
        inter_site_distance_m=isd,
        kpis=k,
        sibling_load=sibling_load or {},
        ret_unit=ret_unit,
    )
    if sdb_row is not None:
        for attr, col in (("latitude", "latitude"), ("longitude", "longitude"),
                          ("antenna_height_m", "antenna_height_m"),
                          ("azimuth_deg", "azimuth_deg"),
                          ("mech_tilt_deg", "mech_tilt_deg"),
                          ("elec_tilt_deg", "elec_tilt_deg"),
                          ("vbw_deg", "vbw_deg"), ("hbw_deg", "hbw_deg")):
            v = sdb_row.get(col)
            if v is not None and pd.notna(v):
                setattr(ctx, attr, float(v))
    return ctx


def run_analysis(
    kpi_file,
    *,
    technology: str | None = None,
    site_db=None,
    region_filter: str | None = None,
    env_kind: str = "urban",
    threshold_overrides: dict | None = None,
    threshold_path: str | None = None,
    ret_unit: str = "deg",
    manual_map: dict[str, str] | None = None,
    sheet=None,
) -> AnalysisResult:
    load = load_kpi_file(kpi_file, technology=technology, sheet=sheet,
                         region_filter=region_filter, manual_map=manual_map)
    tech = load.technology
    df = load.df
    ts = load_thresholds(tech, path=threshold_path, overrides=threshold_overrides)

    sdb = _load_site_db(site_db)
    isd_map = _isd_by_site(sdb)

    agg_cell = aggregate(df, "cell", "all", tech)
    agg_sector = aggregate(df, "sector", "all", tech)
    agg_site = aggregate(df, "site", "all", tech)
    agg_hourly = (aggregate(df, "cell", "hour", tech)
                  if load.granularity == "hour" else pd.DataFrame())
    bh = busy_hour_table(df, "site") if load.granularity == "hour" else pd.DataFrame()

    # breaches on the daily/all cell aggregate + hourly for capacity KPIs
    breaches = evaluate_thresholds(agg_cell, ts, level="cell", period="all")
    cap = ["dl_prb_util", "ul_prb_util", "pdcch_util", "ue_rejected_rrc",
           "rrc_conn_users_avg"]
    if not agg_hourly.empty:
        hb = evaluate_thresholds(agg_hourly, ts, level="cell", period="hour",
                                 only_kpis=cap)
        seen_bh: set[tuple] = set()
        for b in hb:
            if b.severity.name != "CRITICAL":
                continue
            key = (b.entity_id, b.kpi)
            if key in seen_bh:
                continue
            seen_bh.add(key)
            b.when = f"busy-hour ({b.when[-8:-3]})" if len(b.when) >= 8 else "busy-hour"
            breaches.append(b)

    # trends per cell
    trends = detect_anomalies(df, level="cell", technology=tech) \
        if load.granularity == "hour" or df["datetime"].nunique() >= 5 else []
    trends_by_entity: dict[str, list] = {}
    for t in trends:
        trends_by_entity.setdefault(t.entity_id, []).append(t)

    # diagnoses
    diagnoses = diagnose_frame(
        agg_cell, ts, level="cell", site_db=sdb,
        trends_by_entity=trends_by_entity, env_kind=env_kind,
        isd_by_site=isd_map,
    )

    # recommendations - group diagnoses by cell, build context, run engine
    sdb_by_cell = ({str(r["cell_id"]): r for _, r in sdb.iterrows()}
                   if sdb is not None and "cell_id" in sdb.columns else {})
    cell_rows = {str(r["cell_id"]): r for _, r in agg_cell.iterrows()}
    sibling = {}
    if "sector_id" in agg_cell.columns and "dl_prb_util" in agg_cell.columns:
        for sec, g in agg_cell.groupby("sector_id"):
            for _, r in g.iterrows():
                sibling[str(r["cell_id"])] = {
                    str(rr["cell_id"]): float(rr["dl_prb_util"])
                    for _, rr in g.iterrows()
                    if pd.notna(rr["dl_prb_util"])
                }

    by_cell: dict[str, list[Diagnosis]] = {}
    for d in diagnoses:
        by_cell.setdefault(d.entity_id, []).append(d)

    recommendations: list[Recommendation] = []
    for cid, digs in by_cell.items():
        crow = cell_rows.get(cid)
        if crow is None:
            continue
        # merge sibling load discovered by the imbalance rule
        sib = dict(sibling.get(cid, {}))
        for d in digs:
            sib.update(d.kpis.get("_sibling_load", {}) or {})
        ctx = _cell_context(crow, sdb_by_cell.get(cid), env_kind,
                            isd_map.get(str(crow.get("site_id", ""))),
                            sib, ret_unit)
        ctx.has_cosite_capacity_layer = len(sib) > 1
        if d := next((x for x in digs if x.problem_class == "traffic_imbalance"),
                     None):
            pass
        recommendations.extend(recommend_for_cell(ctx, digs))

    recommendations.sort(key=lambda r: ({"P1": 0, "P2": 1, "P3": 2, "P4": 3}
                                        .get(r.priority, 4), -r.confidence))

    return AnalysisResult(
        load=load, technology=tech, thresholds=ts,
        agg_cell=agg_cell, agg_sector=agg_sector, agg_site=agg_site,
        agg_hourly=agg_hourly, busy_hour=bh,
        breaches=breaches, trends=trends,
        diagnoses=diagnoses, recommendations=recommendations,
        site_db=sdb, isd_by_site=isd_map, warnings=list(load.warnings),
    )
