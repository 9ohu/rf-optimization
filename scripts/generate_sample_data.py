"""Generate a realistic Huawei-style LTE KPI export + site database for demos.

Creates, under ``sample_data/``:
  * r5_lte_kpi_hourly.xlsx   - 14 days x hourly, ~30 cells, Huawei column names
  * r5_lte_kpi_daily.csv     - daily roll-up of the same
  * r5_site_database.csv     - lat/lon/height/azimuth/tilt per cell
  * r5_complaint_sample.csv  - a few worked complaint rows for the deep-dive

Several cells carry deliberately injected problems (overshooting, weak
coverage, UL interference, congestion, accessibility, availability, ping-pong
handover, a co-sited traffic imbalance, and a mid-window degradation) so every
part of the tool has something to find.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

RNG = np.random.default_rng(20260906)
OUT = Path(__file__).resolve().parents[1] / "sample_data"
OUT.mkdir(exist_ok=True)

DAYS = 14
START = pd.Timestamp("2026-08-15 00:00")
REGION = "R5"

# 10 sites on a rough grid around (24.71 N, 46.67 E), 3 cells each.
SITE_LL = {
    f"R5-{i:03d}": (24.71 + 0.010 * (i % 4) + RNG.normal(0, 0.002),
                    46.67 + 0.012 * (i // 4) + RNG.normal(0, 0.002))
    for i in range(1, 11)
}
AZ = [20, 140, 260]
# each site: 3 sectors, each sector carries L1800 always, L2100 widely, L800 on
# the coverage sites - so co-sited carriers share an azimuth / sector id.
SECTOR_CARRIERS = {
    1: ["L1800", "L2100", "L800"],
    2: ["L1800", "L2100"],
    3: ["L1800", "L2100", "L800"],
}
EARFCN = {"L1800": 1650, "L2100": 300, "L800": 6200}


def diurnal(hour: np.ndarray) -> np.ndarray:
    """0..1 load shape: morning shoulder, evening peak ~21:00."""
    return (0.28
            + 0.32 * np.exp(-((hour - 13) ** 2) / 26)
            + 0.55 * np.exp(-((hour - 21) ** 2) / 8)
            + 0.05 * RNG.normal(0, 1, size=hour.shape)).clip(0.05, 1.15)


def build_cell_catalogue() -> pd.DataFrame:
    rows = []
    for site, (lat, lon) in SITE_LL.items():
        mast = float(RNG.choice([24, 27, 30, 30, 33, 38.5]))
        for sec in (1, 2, 3):
            az = AZ[sec - 1]
            m_tilt = float(RNG.choice([0, 0, 1, 2]))
            for band in SECTOR_CARRIERS[sec]:
                cid = f"{site}_{band}_S{sec}"
                # low band a touch shallower, high band a touch steeper
                e_tilt = float(RNG.choice([2, 3, 3, 4])
                               + {"L800": -1, "L1800": 0, "L2100": 1}[band])
                rows.append(dict(
                    site_id=site, cell_id=cid, region=REGION, band=band,
                    sector=sec, latitude=round(lat, 6), longitude=round(lon, 6),
                    antenna_height_m=mast, azimuth_deg=az,
                    mech_tilt_deg=m_tilt, elec_tilt_deg=max(1.0, e_tilt),
                    vbw_deg=6.5, hbw_deg=65.0, earfcn=EARFCN[band],
                ))
    return pd.DataFrame(rows)


def assign_problems(cat: pd.DataFrame) -> dict[str, str]:
    """Pin problems to explicit cell ids so co-sited relationships are right."""
    return {
        "R5-001_L2100_S1": "overshoot",       # tall mast, shallow tilt (set below)
        "R5-003_L1800_S2": "weak_coverage",
        "R5-005_L1800_S1": "ul_interference",
        "R5-006_L1800_S3": "congestion",      # its L2100 sector-mate stays light
        "R5-007_L2100_S2": "accessibility",
        "R5-008_L1800_S1": "availability",
        "R5-009_L2100_S3": "pingpong",
        "R5-002_L1800_S1": "imbalance_hi",    # heavy; L2100/L800 S1 stay light
        "R5-004_L1800_S2": "degrading",
    }


def gen_cell_hourly(meta: dict, problem: str | None,
                    times: pd.DatetimeIndex) -> pd.DataFrame:
    n = len(times)
    hour = times.hour.to_numpy().astype(float)
    dayidx = np.asarray((times - times[0]).days, dtype=float)
    load = diurnal(hour)

    # --- baseline healthy cell -------------------------------------------
    base_users = RNG.uniform(20, 70)
    users = (base_users * load).clip(1, None)
    prb = (18 + 55 * load + RNG.normal(0, 4, n)).clip(1, 100)
    traffic = (users * (0.9 + 0.6 * load) * RNG.uniform(0.02, 0.05)).clip(0.001)
    ul_traffic = traffic * RNG.uniform(0.08, 0.16)

    rsrp = RNG.normal(-96, 3) - 3 * load + RNG.normal(0, 1.2, n)
    rsrq = RNG.normal(-9.5, 1.0) - 1.6 * load + RNG.normal(0, 0.5, n)
    sinr = RNG.normal(9.0, 1.5) - 3.2 * load + RNG.normal(0, 0.8, n)
    cqi = (8.6 + 0.10 * (sinr - 8)).clip(2, 15)
    pct_rsrp_poor = (4 + 2.5 * load + RNG.normal(0, 1.0, n)).clip(0, 100)
    dl_bler = (7 + 2.5 * load + RNG.normal(0, 1.2, n)).clip(0, 40)
    ul_rssi = RNG.normal(-116, 1.2, n) + 1.5 * load
    ta_p95 = np.full(n, RNG.uniform(700, 1400)) + RNG.normal(0, 60, n)
    ta_avg = ta_p95 * 0.45

    rrc_att = (users * RNG.uniform(3, 6) * (0.6 + load)).clip(1)
    rrc_sr = (99.6 - 0.7 * load + RNG.normal(0, 0.15, n)).clip(80, 100)
    erab_sr = (99.7 - 0.5 * load + RNG.normal(0, 0.12, n)).clip(80, 100)
    rach_sr = (98.5 - 1.0 * load + RNG.normal(0, 0.4, n)).clip(70, 100)
    drop = (0.35 + 0.30 * load + RNG.normal(0, 0.08, n)).clip(0, 20)
    ho_sr = (98.6 - 0.6 * load + RNG.normal(0, 0.3, n)).clip(70, 100)
    pingpong = (2.0 + RNG.normal(0, 0.4, n)).clip(0, 30)
    dl_thr = (RNG.uniform(18, 32) - 12 * load + 0.8 * (cqi - 8)
              + RNG.normal(0, 1.5, n)).clip(0.2, 90)
    ul_thr = (RNG.uniform(5, 9) - 3 * load + RNG.normal(0, 0.6, n)).clip(0.05, 40)
    pdcch = (20 + 45 * load + RNG.normal(0, 5, n)).clip(1, 100)
    rrc_rej = np.zeros(n)
    avail = np.full(n, 100.0)
    unavail = np.zeros(n)
    qpsk = (12 + 18 * load + RNG.normal(0, 3, n)).clip(0, 100)
    intf_prb = (5 + 4 * load + RNG.normal(0, 2, n)).clip(0, 100)

    # --- inject the problem --------------------------------------------
    if problem == "overshoot":
        ta_p95 = np.full(n, RNG.uniform(4200, 6500)) + RNG.normal(0, 250, n)
        ta_avg = ta_p95 * 0.5
        rsrp = RNG.normal(-88, 2, n) + RNG.normal(0, 1, n)          # strong
        rsrq = RNG.normal(-14.5, 0.8, n) - 1.2 * load               # bad quality
        sinr = RNG.normal(2.0, 1.2, n) - 2 * load
        cqi = (6.0 + 0.1 * sinr).clip(2, 15)
        drop = (0.9 + 0.5 * load + RNG.normal(0, 0.15, n)).clip(0, 20)
        intf_prb = (22 + 8 * load).clip(0, 100)
        qpsk = (30 + 15 * load).clip(0, 100)
    elif problem == "weak_coverage":
        rsrp = RNG.normal(-114, 2, n) - 2 * load
        pct_rsrp_poor = (30 + 12 * load + RNG.normal(0, 3, n)).clip(0, 100)
        sinr = RNG.normal(2.5, 1.4, n) - 2 * load
        cqi = (5.5 + 0.1 * sinr).clip(1, 15)
        dl_thr = (RNG.uniform(3, 7) - 2 * load + RNG.normal(0, 0.8, n)).clip(0.1)
        drop = (1.4 + 0.8 * load + RNG.normal(0, 0.2, n)).clip(0, 25)
        rrc_sr = (97.5 - 1.5 * load + RNG.normal(0, 0.4, n)).clip(70, 100)
        qpsk = (38 + 14 * load).clip(0, 100)
    elif problem == "ul_interference":
        ul_rssi = RNG.normal(-101, 1.5, n) + 2 * load
        ul_thr = (RNG.uniform(1.5, 3) - 1.2 * load).clip(0.05, 40)
        rach_sr = (90 - 3 * load + RNG.normal(0, 1.0, n)).clip(50, 100)
        drop = (1.2 + 0.6 * load + RNG.normal(0, 0.2, n)).clip(0, 25)
        rrc_sr = (98.3 - 1.0 * load + RNG.normal(0, 0.4, n)).clip(70, 100)
        intf_prb = (35 + 10 * load).clip(0, 100)
    elif problem == "congestion":
        prb = (55 + 42 * load + RNG.normal(0, 3, n)).clip(1, 100)
        pdcch = (55 + 40 * load).clip(1, 100)
        users = (base_users * 2.2 * load).clip(1)
        rrc_rej = np.where(load > 0.8, RNG.uniform(30, 120, n) * (load - 0.7), 0)
        dl_thr = (RNG.uniform(10, 18) - 12 * load + RNG.normal(0, 1.2, n)).clip(0.2)
        erab_sr = (99.2 - 3.0 * np.clip(load - 0.7, 0, 1) + RNG.normal(0, 0.2, n)).clip(70, 100)
        drop = (0.5 + 1.6 * np.clip(load - 0.6, 0, 1) + RNG.normal(0, 0.1, n)).clip(0, 20)
        traffic = traffic * 2.3
    elif problem == "accessibility":
        rrc_sr = (95.5 - 2.5 * load + RNG.normal(0, 0.6, n)).clip(60, 100)
        erab_sr = (96.5 - 2.0 * load + RNG.normal(0, 0.5, n)).clip(60, 100)
        rach_sr = (93 - 2 * load + RNG.normal(0, 1, n)).clip(50, 100)
        pdcch = (45 + 45 * load).clip(1, 100)
    elif problem == "availability":
        # cell down ~ days 6-8, plus nightly energy-saving lock 01:00-04:00
        down = ((dayidx >= 6) & (dayidx <= 8)) | np.isin(times.hour, [2, 3])
        avail = np.where(down, RNG.uniform(0, 30, n), 100.0)
        unavail = np.where(down, 60 * (1 - avail / 100), 0.0)
        for arr in (users, prb, traffic, ul_traffic, dl_thr, ul_thr, rrc_att):
            arr[down] = 0.0
        rrc_sr[down] = np.nan
        erab_sr[down] = np.nan
    elif problem == "pingpong":
        pingpong = (9 + 4 * load + RNG.normal(0, 1, n)).clip(0, 40)
        ho_sr = (94 - 1.5 * load + RNG.normal(0, 0.6, n)).clip(60, 100)
        drop = (0.9 + 0.5 * load).clip(0, 20)
    elif problem == "imbalance_hi":
        prb = (45 + 50 * load + RNG.normal(0, 3, n)).clip(1, 100)
        users = (base_users * 1.9 * load).clip(1)
        traffic = traffic * 2.0
        dl_thr = (RNG.uniform(12, 20) - 10 * load).clip(0.3)
    elif problem == "degrading":
        ramp = np.clip((dayidx - 3) / 9, 0, 1)          # worsens after day 3
        rsrp = rsrp - 9 * ramp
        sinr = sinr - 4 * ramp
        drop = drop + 1.6 * ramp
        dl_thr = dl_thr * (1 - 0.45 * ramp)
        pct_rsrp_poor = (pct_rsrp_poor + 22 * ramp).clip(0, 100)

    call_sr = (rrc_sr / 100) * (erab_sr / 100) * 100

    return pd.DataFrame({
        "Time": times,
        "eNodeB Name": meta["site_id"],
        "Cell Name": meta["cell_id"],
        "Cell ID": meta["local_id"],
        "Region": REGION,
        "Frequency Band": meta["band"],
        "DL EARFCN": meta["earfcn"],
        "RRC Setup Success Rate (%)": rrc_sr.round(3),
        "RRC Setup Attempts": rrc_att.round(0),
        "E-RAB Setup Success Rate (%)": erab_sr.round(3),
        "E-RAB Setup Attempts": (rrc_att * 0.85).round(0),
        "Call Setup Success Rate (%)": call_sr.round(3),
        "RACH Setup Success Rate (%)": rach_sr.round(2),
        "RRC Connection Reject": rrc_rej.round(0),
        "E-RAB Drop Rate (%)": drop.round(3),
        "UE Context Drop Rate (%)": (drop * 0.9).round(3),
        "Handover Success Rate (%)": ho_sr.round(3),
        "Intra-Frequency Handover Success Rate (%)": (ho_sr + RNG.normal(0.3, 0.2, n)).clip(0, 100).round(3),
        "Inter-Frequency Handover Success Rate (%)": (ho_sr - RNG.uniform(1, 3, n)).clip(0, 100).round(3),
        "Handover Ping-Pong Rate (%)": pingpong.round(2),
        "DL User Throughput (Mbps)": dl_thr.round(3),
        "UL User Throughput (Mbps)": ul_thr.round(3),
        "Cell Downlink Average Throughput (Mbps)": (dl_thr * RNG.uniform(1.5, 3)).round(2),
        "DL Latency (ms)": (14 + 20 * load + RNG.normal(0, 3, n)).clip(3).round(1),
        "Average RSRP (dBm)": rsrp.round(2),
        "Average RSRQ (dB)": rsrq.round(2),
        "Average SINR (dB)": sinr.round(2),
        "Average CQI": cqi.round(2),
        "DL Residual BLER (%)": dl_bler.round(2),
        "UL Residual BLER (%)": (dl_bler * RNG.uniform(0.8, 1.3)).clip(0, 60).round(2),
        "RSRP < -110 Ratio (%)": pct_rsrp_poor.round(2),
        "DL QPSK Ratio (%)": qpsk.round(1),
        "Average TA Distance (m)": ta_avg.round(0),
        "P95 TA Distance (m)": ta_p95.round(0),
        "DL PRB Utilization Rate (%)": prb.round(2),
        "UL PRB Utilization Rate (%)": (prb * RNG.uniform(0.55, 0.8)).clip(0, 100).round(2),
        "PDCCH CCE Utilization Rate (%)": pdcch.round(2),
        "Average RRC Connected Users": users.round(1),
        "Max RRC Connected Users": (users * RNG.uniform(1.3, 1.8)).round(0),
        "Average Active DL Users": (users * RNG.uniform(0.15, 0.3)).round(2),
        "UL RSSI (dBm)": ul_rssi.round(2),
        "High Interference PRB Ratio (%)": intf_prb.round(1),
        "DL Data Volume (GB)": traffic.round(4),
        "UL Data Volume (GB)": ul_traffic.round(4),
        "Total Traffic Volume (GB)": (traffic + ul_traffic).round(4),
        "Cell Availability (%)": np.round(avail, 3),
        "Cell Unavailable Time (min)": np.round(unavail, 1),
    })


def main() -> None:
    cat = build_cell_catalogue()
    cat["local_id"] = range(1, len(cat) + 1)
    problems = assign_problems(cat)
    print("Injected problems:")
    for cid, p in problems.items():
        print(f"  {cid:20s} -> {p}")

    times = pd.date_range(START, periods=DAYS * 24, freq="h")
    frames = []
    for _, m in cat.iterrows():
        meta = dict(site_id=m["site_id"], cell_id=m["cell_id"],
                    local_id=m["local_id"], band=m["band"], earfcn=m["earfcn"])
        frames.append(gen_cell_hourly(meta, problems.get(m["cell_id"]), times))
    hourly = pd.concat(frames, ignore_index=True)

    xlsx = OUT / "r5_lte_kpi_hourly.xlsx"
    hourly.to_excel(xlsx, index=False, sheet_name="LTE_Cell_HH")
    print(f"\nwrote {xlsx}  ({len(hourly):,} rows)")

    # daily roll-up (simple mean for rates, sum for volumes/counts)
    daily = hourly.copy()
    daily["Time"] = pd.to_datetime(daily["Time"]).dt.floor("D")
    sum_cols = [c for c in daily.columns if any(t in c for t in
               ("Volume", "Attempts", "Reject", "Unavailable"))]
    agg = {c: ("sum" if c in sum_cols else "mean")
           for c in daily.columns if c not in
           ("Time", "eNodeB Name", "Cell Name", "Region", "Frequency Band")}
    daily_g = (daily.groupby(["Time", "eNodeB Name", "Cell Name", "Region",
                              "Frequency Band"], as_index=False).agg(agg))
    csv = OUT / "r5_lte_kpi_daily.csv"
    daily_g.to_csv(csv, index=False)
    print(f"wrote {csv}  ({len(daily_g):,} rows)")

    site_db = cat[["cell_id", "site_id", "region", "band", "sector", "latitude",
                   "longitude", "antenna_height_m", "azimuth_deg",
                   "mech_tilt_deg", "elec_tilt_deg", "vbw_deg", "hbw_deg",
                   "earfcn"]].copy()
    over_id = next(c for c, p in problems.items() if p == "overshoot")
    weak_id = next(c for c, p in problems.items() if p == "weak_coverage")
    # give the overshooter (and its sector-mates) a shallow tilt on a tall mast
    over_site = over_id.split("_")[0]
    over_sec = over_id.split("_")[-1]
    m = ((site_db["cell_id"] == over_id) |
         ((site_db["site_id"] == over_site) &
          (site_db["cell_id"].str.endswith(over_sec))))
    site_db.loc[m, ["antenna_height_m", "mech_tilt_deg", "elec_tilt_deg"]] = \
        [42.0, 0.0, 1.0]
    sdb = OUT / "r5_site_database.csv"
    site_db.to_csv(sdb, index=False)
    print(f"wrote {sdb}  ({len(site_db):,} cells)")

    # a few complaint rows
    over = site_db[site_db["cell_id"] == over_id].iloc[0]
    weak = site_db[site_db["cell_id"] == weak_id].iloc[0]
    comp = pd.DataFrame([
        dict(complaint_id="CMP-1001", cell_id=over["cell_id"],
             site_id=over["site_id"],
             complaint_lat=round(over["latitude"] + 0.016, 6),
             complaint_lon=round(over["longitude"] + 0.003, 6),
             indoor=0, measured_rsrp_dbm=-114, measured_rsrq_db=-16,
             measured_sinr_db=-2,
             note="Customer: no signal at home, ~1.8 km north of site"),
        dict(complaint_id="CMP-1002",
             cell_id=weak["cell_id"], site_id=weak["site_id"],
             complaint_lat=round(weak["latitude"] + 0.008, 6),
             complaint_lon=round(weak["longitude"] - 0.006, 6),
             indoor=1, measured_rsrp_dbm=-118, measured_rsrq_db=-17,
             measured_sinr_db=-4, note="Poor indoor coverage, dropped calls"),
    ])
    ccsv = OUT / "r5_complaint_sample.csv"
    comp.to_csv(ccsv, index=False)
    print(f"wrote {ccsv}  ({len(comp)} complaints)")
    print("\nDone.")


if __name__ == "__main__":
    main()
