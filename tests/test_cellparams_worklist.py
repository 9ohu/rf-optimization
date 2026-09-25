"""Engineering-parameter loader + site audit + daily worklist processor."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from rfopt.complaints.site_audit import audit_site, audit_sites
from rfopt.complaints.worklist import load_worklist, process_worklist
from rfopt.ingest.cellparams import _clean_rs_power, _mean_array, _sector_no

WK37 = Path(r"D:\WeLink_data_files\swx1351646\ReceiveFiles"
            r"\WK37 Engineering Parameter Tracker-06092026.xlsx")


def test_mean_array_parsing():
    assert _mean_array("[40, 40, 40, 42]") == pytest.approx(40.5)
    assert _mean_array("4.0") == 4.0
    assert np.isnan(_mean_array("None"))
    assert np.isnan(_mean_array(""))


def test_clean_rs_power():
    assert _clean_rs_power("182") == 18.2       # tenths
    assert _clean_rs_power("18.2") == 18.2      # already dB
    assert np.isnan(_clean_rs_power("0"))       # missing
    assert np.isnan(_clean_rs_power("-78"))     # error


def test_sector_number_never_comes_from_the_site_code():
    """The GSM sheet's Sector column reads 'BAS0043-S3' — not sector 43."""
    s = _sector_no(pd.Series(["BAS0043-S3", "S2", "Jumhoriya6_BAS0043-7", "3",
                              "3.0", "BAS0043", ""]))
    assert list(s[:5]) == [3, 2, 7, 3, 3]
    assert s[5:].isna().all()


@pytest.fixture
def synth_params():
    """3 sites: clean, tilt-imbalance + azimuth gap, missing-RET."""
    rows = []
    def cell(site, sec, band, az, tilt, h=25, pci=1, rsp=18.2):
        return dict(site_id=site, enodeb_name=f"n_{site}", cell_id=f"{site}{sec}{band}",
                    cell_name=f"L_{site}", sector=f"{site}-S{sec}", sector_num=sec,
                    sector_id=f"{site}-S{sec}", technology="LTE", band=str(band),
                    band_label={3: "L1800", 1: "L2100", 41: "N41"}[band],
                    earfcn=1750 if band == 3 else 300, bandwidth_mhz=20 if band != 1 else 10,
                    latitude=30.5 + hash(site) % 5 * 0.01, longitude=47.8 + sec * 0.001,
                    azimuth_deg=az, antenna_height_m=h, mech_tilt_deg=0.0,
                    elec_tilt_deg=tilt, elec_tilt_branches=str([tilt]),
                    max_ret_deg=10.0, total_tilt_deg=tilt, rs_power_dbm=rsp,
                    pci=pci, mod3=pci % 3, rsi=0, antenna_model="X", is_outdoor="Macro",
                    city="Basrah", district="d", sub_district="s", tac="1", cgi="c",
                    status="Active", region="Region 5", prefix=site[:3],
                    vbw_deg=6.5, hbw_deg=65.0)
    for s, az in [(1, 0), (2, 120), (3, 240)]:
        rows.append(cell("BAS0001", s, 3, az, 4.0, pci=100 + s))
        rows.append(cell("BAS0001", s, 1, az, 4.0, pci=200 + s))
    for s, az, t in [(1, 0, 4.0), (2, 30, 7.5), (3, 200, 4.0)]:
        rows.append(cell("BAS0002", s, 3, az, t, pci=10 + s))
    for s, az in [(1, 0), (2, 120), (3, 240)]:
        rows.append(cell("BAS0003", s, 3, az, np.nan, pci=50 + s))
    df = pd.DataFrame(rows)
    return df


def test_audit_clean_site(synth_params):
    a = audit_site("BAS0001", synth_params)
    assert a.n_sectors == 3
    assert set(a.bands) == {"L1800", "L2100"}
    assert not [f for f in a.flags if f.severity == "critical"]


def test_audit_finds_tilt_imbalance_and_gap(synth_params):
    a = audit_site("BAS0002", synth_params)
    codes = {f.code for f in a.flags}
    assert "tilt_imbalance" in codes
    assert "azimuth_gap" in codes          # 200->0 is a 160 gap; sorted 0/30/200
    assert a.severity in ("warning", "info")


def test_audit_missing_ret_is_critical(synth_params):
    a = audit_site("BAS0003", synth_params)
    mr = [f for f in a.flags if f.code == "missing_ret"]
    assert mr and mr[0].severity == "critical"
    assert "far_coverage" in a.likely_causes()


def test_audit_unknown_site(synth_params):
    a = audit_site("BAS9999", synth_params)
    assert not a.site_found_ok()


def test_worklist_end_to_end(synth_params, tmp_path):
    wl_csv = tmp_path / "target.csv"
    pd.DataFrame([
        {"Ticket ID": "T1", "Site ID(SD Check_site_id)": "BAS0003",
         "City": "Basrah", "Affected Services": "Data Service",
         "Problem Time": "2026-09-07T19:00:00.000Z",
         "Diagnostic Comment(Incident Diagnostic)": ""},
        {"Ticket ID": "T2", "Site ID(SD Check_site_id)": "BAS0001",
         "City": "Basrah", "Affected Services": "Coverage",
         "Problem Time": "2026-09-07T20:00:00.000Z",
         "Diagnostic Comment(Incident Diagnostic)":
             "user not connected, no issue from PS side"},
        {"Ticket ID": "T3", "Site ID(SD Check_site_id)": "0",
         "City": "Basrah", "Affected Services": "Data Service",
         "Problem Time": "2026-09-07T21:00:00.000Z",
         "Diagnostic Comment(Incident Diagnostic)": ""},
    ]).to_csv(wl_csv, index=False)

    wl = load_worklist(wl_csv)
    assert list(wl["site_id"].fillna("")) == ["BAS0003", "BAS0001", ""]
    df = process_worklist(wl, synth_params, history_counts={"BAS0003": 20})

    r1 = df[df.ticket_id == "T1"].iloc[0]
    assert r1["likely_cause"] == "far_coverage"       # missing RET
    assert r1["param_severity"] == "critical"
    assert r1["priority"] == "P1"                       # crit + 20 history

    r2 = df[df.ticket_id == "T2"].iloc[0]
    assert r2["likely_cause"] == "not_rf"               # comment
    assert r2["priority"] == "P4"

    r3 = df[df.ticket_id == "T3"].iloc[0]
    assert r3["likely_cause"] == "insufficient_data"
    assert "no site id" in r3["param_severity"]


def test_worklist_kpi_verdict_overrides(synth_params, tmp_path):
    """A measured KPI issue beats the parameter guess."""
    ts = pd.Timestamp("2026-09-07", tz="UTC")
    kpi = pd.DataFrame([
        {"datetime": ts + pd.Timedelta(hours=h), "granularity": "hour",
         "technology": "LTE", "site_id": "BAS0001", "sector": s,
         "sector_id": f"BAS0001-S{s}", "cell_id": f"L_x_BAS0001-{s}",
         "dl_prb_util": 95 if s == 2 else 40, "total_traffic_gb": 3.0,
         "erab_drop_rate": 0.1, "call_setup_sr": 99.9, "ho_sr": 99.5,
         "ul_rssi_dbm": -115, "cell_avail_pct": 100.0,
         "dl_user_thr_mbps": 20.0}
        for h in range(6) for s in (1, 2, 3)
    ])
    wl_csv = tmp_path / "t.csv"
    pd.DataFrame([{"Ticket ID": "K1", "Site ID(SD Check_site_id)": "BAS0001",
                   "City": "Basrah", "Affected Services": "Data Service",
                   "Problem Time": "2026-09-07T02:00:00.000Z",
                   "Diagnostic Comment(Incident Diagnostic)": ""}]
                 ).to_csv(wl_csv, index=False)
    wl = load_worklist(wl_csv)
    df = process_worklist(wl, synth_params, kpi=kpi)
    r = df.iloc[0]
    assert r["kpi_status"] == "kpi issue"
    assert r["likely_cause"] == "congestion"
    assert "S2 DL PRB 95%" in r["kpi_evidence"]
    assert r["confidence"] >= 0.6
    assert r["priority"] == "P1"          # severe (>=92) PRB


def test_hourly_kpi_loader_column_map():
    from rfopt.ingest.hourly_kpi import _norm, _KPI_MAP
    assert _norm("HW_DL PRB Avg Utilization(%)") in _KPI_MAP
    assert _KPI_MAP[_norm("HW_DL PRB Avg Utilization(%)")] == "dl_prb_util"
    assert _KPI_MAP[_norm("4G/LTE DROP CALL RATE (With MME) (%)_Asiacell")] \
        == "erab_drop_rate"


@pytest.mark.skipif(not (Path.home() / "Downloads" /
                         "Original_Data_R5_Hourly.zip").exists(),
                    reason="hourly KPI zip not present")
def test_real_hourly_kpi_load():
    from rfopt.ingest.hourly_kpi import load_hourly_kpi
    hk = load_hourly_kpi(str(Path.home() / "Downloads" /
                             "Original_Data_R5_Hourly.zip"))
    s = hk.summary()
    assert s["sites"] > 1000
    assert "dl_prb_util" in s["kpis"] and "ul_rssi_dbm" in s["kpis"]
    assert set(hk.df["prefix"].unique()) <= {"BAS", "NAS", "EMA", "SAM"}


# ---- real file -----------------------------------------------------------
@pytest.mark.skipif(not WK37.exists(), reason="WK37 parameter file not present")
def test_real_param_load_and_worklist():
    from rfopt.ingest.cellparams import load_cell_params, params_to_site_db
    pl = load_cell_params(str(WK37), technology="LTE", region="R5")
    assert pl.df["site_id"].nunique() > 1000
    assert set(pl.df["prefix"].unique()) <= {"BAS", "NAS", "EMA", "SAM"}
    assert pl.df["elec_tilt_deg"].notna().mean() > 0.7
    sdb = params_to_site_db(pl.df)
    assert (sdb["elec_tilt_deg"].notna()).mean() > 0.7

    tgt = Path.home() / "Desktop" / "Target 7-Sep.xlsx"
    if tgt.exists():
        wl = load_worklist(str(tgt))
        df = process_worklist(wl, pl.df)
        assert len(df) == len(wl)
        assert df["priority"].isin(["P1", "P2", "P3", "P4"]).all()
