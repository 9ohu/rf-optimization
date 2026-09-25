"""Command-line interface.

    python -m rfopt analyze KPI.xlsx --site-db sites.csv --region R5 \\
        --out-dir out --excel --markdown --kmz [--ai]

    python -m rfopt complaint --site-db sites.csv --cell CELL_A \\
        --distance 1800 --bearing 10 --height 38.5 --ret 7 [--ret-unit tenths]

    python -m rfopt sample            # (re)generate the demo data set
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _cmd_analyze(a: argparse.Namespace) -> int:
    from rfopt.pipeline import run_analysis
    from rfopt.reports import write_excel_report, write_markdown_report

    res = run_analysis(
        a.kpi_file, technology=a.tech, site_db=a.site_db,
        region_filter=a.region, env_kind=a.env, ret_unit=a.ret_unit,
        sheet=a.sheet,
    )
    print(f"\n{res.load.describe()}")
    print(f"mapping: {res.load.mapping.summary()}")
    if res.load.unmapped_columns:
        print(f"unmapped columns: {res.load.unmapped_columns}")
    s = res.summary()
    print(f"\n{s['diagnoses']} diagnoses ({s['critical_diagnoses']} critical), "
          f"{s['recommendations']} recommendations, "
          f"{s['kpi_breaches']} KPI breaches, "
          f"{s['degrading_trends']} degrading trends\n")

    for i, r in enumerate(res.recommendations[:a.top], 1):
        print("=" * 80)
        print(f"{i}. {r.as_text()}\n")

    narratives = None
    if a.ai or a.markdown or a.excel:
        from rfopt.ai import build_narrator
        narrator = build_narrator(use_ai=a.ai)
        print(f"[narrative engine: {narrator.source}]")
        by_cell: dict = {}
        for d in res.diagnoses:
            by_cell.setdefault(d.entity_id, d)
        narratives = []
        for r in res.recommendations:
            d = next((x for x in res.diagnoses
                      if x.entity_id == r.cell_id
                      and x.problem_class == r.category), by_cell.get(r.cell_id))
            if d is not None:
                narratives.append(narrator.narrate(d, r))

    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if a.excel:
        p = write_excel_report(res, out / "rf_optimization_report.xlsx", narratives)
        print(f"wrote {p}")
    if a.markdown:
        p = write_markdown_report(res, out / "rf_optimization_report.md", narratives)
        print(f"wrote {p}")
    if a.kmz:
        from rfopt.geo import build_site_kmz
        if res.site_db is None:
            print("--kmz needs --site-db")
        else:
            kpi_by_cell = {row["cell_id"]: row.dropna().to_dict()
                           for _, row in res.agg_cell.iterrows()}
            p = build_site_kmz(res.site_db, out / "r5_site_map.kmz",
                               region=a.region, region_prefix=a.region,
                               diagnoses=res.diagnoses,
                               recommendations=res.recommendations,
                               kpi_by_cell=kpi_by_cell)
            print(f"wrote {p}")
    return 0


def _cmd_complaint(a: argparse.Namespace) -> int:
    import pandas as pd

    from rfopt.actions.propagation import Environment
    from rfopt.actions.recommend import (CellContext, ComplaintContext,
                                         recommend_for_complaint)

    ctx = CellContext(
        cell_id=a.cell or "CELL", site_id=a.site or "",
        antenna_height_m=a.height, azimuth_deg=a.azimuth,
        mech_tilt_deg=a.mech_tilt, elec_tilt_deg=a.ret,
        vbw_deg=a.vbw, hbw_deg=a.hbw, ret_unit=a.ret_unit,
        env=Environment(kind=a.env, frequency_mhz=a.freq),
        inter_site_distance_m=a.isd,
        kpis={k: v for k, v in {
            "avg_rsrp_dbm": a.rsrp, "avg_rsrq_db": a.rsrq,
            "avg_sinr_db": a.sinr, "dl_user_thr_mbps": a.throughput,
            "dl_prb_util": a.prb, "total_traffic_gb": a.traffic,
            "ta_p95_m": a.ta_p95,
        }.items() if v is not None},
    )
    if a.ret_unit == "tenths":
        ctx.elec_tilt_deg = a.ret / 10.0
    cc = ComplaintContext(
        cell=ctx, complaint_lat=a.lat, complaint_lon=a.lon,
        distance_m=a.distance, bearing_deg_from_site=a.bearing,
        indoor=a.indoor, measured_rsrp_dbm=a.meas_rsrp,
        measured_rsrq_db=a.meas_rsrq, measured_sinr_db=a.meas_sinr,
    )
    if a.site_lat is not None and a.site_lon is not None:
        cc.cell.latitude = a.site_lat
        cc.cell.longitude = a.site_lon

    rec = recommend_for_complaint(cc, target_mode=a.mode)
    print(rec.as_text())
    if a.ai:
        from rfopt.ai import build_narrator
        from rfopt.diagnosis.models import Diagnosis
        d = Diagnosis(entity_level="cell", entity_id=ctx.cell_id,
                      site_id=ctx.site_id, technology="LTE",
                      problem_class="coverage", title=rec.problem,
                      severity="warning", confidence=rec.confidence,
                      evidence=rec.evidence)
        print("\n" + "=" * 80 + "\n")
        print(build_narrator(use_ai=True).narrate(d, rec).as_text())
    return 0


def _cmd_sample(_a: argparse.Namespace) -> int:
    import runpy
    script = Path(__file__).resolve().parents[1] / "scripts" / "generate_sample_data.py"
    runpy.run_path(str(script), run_name="__main__")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser("rfopt", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    an = sub.add_parser("analyze", help="run the full KPI -> action pipeline")
    an.add_argument("kpi_file")
    an.add_argument("--site-db", dest="site_db", default=None)
    an.add_argument("--tech", default=None, choices=["LTE", "UMTS", "GSM"])
    an.add_argument("--region", default=None, help="region / site-prefix filter")
    an.add_argument("--env", default="urban",
                    choices=["urban", "suburban", "rural"])
    an.add_argument("--ret-unit", default="deg", choices=["deg", "tenths"])
    an.add_argument("--sheet", default=None)
    an.add_argument("--out-dir", dest="out_dir", default="rfopt_out")
    an.add_argument("--top", type=int, default=10, help="print top-N actions")
    an.add_argument("--excel", action="store_true")
    an.add_argument("--markdown", action="store_true")
    an.add_argument("--kmz", action="store_true")
    an.add_argument("--ai", action="store_true", help="use Claude for narratives")
    an.set_defaults(func=_cmd_analyze)

    cp = sub.add_parser("complaint", help="worked RET/tilt recommendation")
    cp.add_argument("--cell", default=None)
    cp.add_argument("--site", default=None)
    cp.add_argument("--height", type=float, default=30.0)
    cp.add_argument("--azimuth", type=float, default=0.0)
    cp.add_argument("--mech-tilt", dest="mech_tilt", type=float, default=0.0)
    cp.add_argument("--ret", type=float, default=3.0,
                    help="current electrical tilt (deg, or RET units if "
                         "--ret-unit tenths)")
    cp.add_argument("--ret-unit", default="deg", choices=["deg", "tenths"])
    cp.add_argument("--vbw", type=float, default=6.5)
    cp.add_argument("--hbw", type=float, default=65.0)
    cp.add_argument("--freq", type=float, default=1800.0)
    cp.add_argument("--env", default="urban",
                    choices=["urban", "suburban", "rural"])
    cp.add_argument("--isd", type=float, default=None, help="inter-site dist (m)")
    cp.add_argument("--mode", default="boresight",
                    choices=["boresight", "edge"],
                    help="boresight = aim beam peak at the location (default); "
                         "edge = treat the location as the cell edge")
    cp.add_argument("--distance", type=float, default=None, help="site->UE (m)")
    cp.add_argument("--bearing", type=float, default=None,
                    help="bearing site->UE (deg from north)")
    cp.add_argument("--lat", type=float, default=None)
    cp.add_argument("--lon", type=float, default=None)
    cp.add_argument("--site-lat", dest="site_lat", type=float, default=None)
    cp.add_argument("--site-lon", dest="site_lon", type=float, default=None)
    cp.add_argument("--indoor", action="store_true")
    for name in ("rsrp", "rsrq", "sinr", "throughput", "prb", "traffic",
                 "ta-p95"):
        cp.add_argument(f"--{name}", dest=name.replace("-", "_"),
                        type=float, default=None)
    cp.add_argument("--meas-rsrp", dest="meas_rsrp", type=float, default=None)
    cp.add_argument("--meas-rsrq", dest="meas_rsrq", type=float, default=None)
    cp.add_argument("--meas-sinr", dest="meas_sinr", type=float, default=None)
    cp.add_argument("--ai", action="store_true")
    cp.set_defaults(func=_cmd_complaint)

    sp = sub.add_parser("sample", help="regenerate the demo data set")
    sp.set_defaults(func=_cmd_sample)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:                # keep CLI errors tidy
        print(f"error: {exc}", file=sys.stderr)
        if "--debug" in (argv or sys.argv):
            raise
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
