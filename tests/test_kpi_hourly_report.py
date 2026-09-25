"""The 4G + 3G hourly KPI report: loader, analysis and the exported workbook."""

import io
import zipfile

import pandas as pd
import pytest

from rfopt.ingest.hourly_kpi import load_hourly_kpi_3g
from rfopt.kpi.hourly_report import (analyse_3g, analyse_4g,
                                     build_kpi_workbook, hourly_pivot)

# the operator's export carries five banner lines before the real header
_3G_CSV = """

SHAMS-3G
Save Time :2026-09-11 10:51:53
User Name :HWMS.S.Ali

Time,RNC,NODEBNAME,NodeB ID,Integrity,VS.RscGroup.FlowCtrol.DL.DropNum,VS.IPPM.Rtt.Means(ms),3G_Availability@AB
2026-09-08 00:00,RBASH01,Tannumah6_BAS0038,38,100%,0,0,100
2026-09-08 01:00,RBASH01,Tannumah6_BAS0038,38,100%,900000,45,92
2026-09-08 00:00,REMAH02,Aager_NAS1580,1580,100%,0,4,100
2026-09-08 01:00,REMAH02,Aager_NAS1580,1580,100%,10,5,100
2026-09-08 00:00,RBAGH01,Somewhere_BAG1234,1234,100%,0,1,100
"""


def _zip_of(text: str, name: str = "export(Subreport 1).csv") -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text)
    buf.seek(0)
    buf.name = "SHAMS-3G_Query_Result.zip"
    return buf


def test_3g_loader_reads_the_raw_export():
    load = load_hourly_kpi_3g(_zip_of(_3G_CSV), use_cache=False)
    df = load.df
    assert set(df["site_id"]) == {"BAS0038", "NAS1580"}     # BAG is not R5
    assert df["technology"].eq("UMTS").all()
    assert {"cell_avail_pct", "ipmm_rtt_ms", "dl_flowctrl_drops"} <= set(df.columns)
    bad = df[(df["site_id"] == "BAS0038") & (df["ipmm_rtt_ms"] == 45)]
    assert len(bad) == 1 and bad.iloc[0]["cell_avail_pct"] == 92


def test_3g_loader_rejects_a_pivot_workbook():
    buf = io.BytesIO(b"not really a workbook")
    buf.name = "3G KPIs Hourly.xlsx"
    with pytest.raises(ValueError, match="raw NPM export"):
        load_hourly_kpi_3g(buf, use_cache=False)


def test_3g_analysis_flags_the_bad_nodeb():
    res = analyse_3g(load_hourly_kpi_3g(_zip_of(_3G_CSV), use_cache=False).df)
    assert not res.findings.empty
    f = res.findings
    assert set(f["Site"]) == {"BAS0038"}                # the clean NodeB is quiet
    assert {"Iub transport congestion", "NodeB availability"} <= set(f["Problem"])
    # the action must name the transport, not an RF change
    assert "Iub" in " ".join(f["Recommended action"])
    assert "01:00" in " ".join(f["Worst hours"])


@pytest.fixture
def synth_4g():
    """Three cells over 24 h: one clean, one congested, one interfered."""
    rows = []
    for h in range(24):
        t = pd.Timestamp("2026-09-10") + pd.Timedelta(hours=h)
        busy = 1.0 if 18 <= h <= 22 else 0.35
        rows += [
            dict(datetime=t, cell_id="L_Clean_BAS0001-1", site_id="BAS0001",
                 sector_id="BAS0001-S1", dl_prb_util=20 * busy, ul_prb_util=10,
                 cell_avail_pct=100.0, call_setup_sr=99.9, erab_drop_rate=0.1,
                 dl_user_thr_mbps=25.0, ul_user_thr_mbps=4.0, ho_sr=99.5,
                 ul_rssi_dbm=-118.0, total_traffic_gb=4.0 * busy,
                 rrc_conn_users_avg=12.0),
            dict(datetime=t, cell_id="L_Busy_BAS0002-1", site_id="BAS0002",
                 sector_id="BAS0002-S1", dl_prb_util=95 * busy, ul_prb_util=55,
                 cell_avail_pct=100.0, call_setup_sr=99.0, erab_drop_rate=0.3,
                 dl_user_thr_mbps=2.5, ul_user_thr_mbps=1.2, ho_sr=99.0,
                 ul_rssi_dbm=-115.0, total_traffic_gb=9.0 * busy,
                 rrc_conn_users_avg=80.0),
            dict(datetime=t, cell_id="L_Noisy_BAS0003-1", site_id="BAS0003",
                 sector_id="BAS0003-S1", dl_prb_util=30.0, ul_prb_util=20,
                 cell_avail_pct=100.0, call_setup_sr=99.5, erab_drop_rate=0.4,
                 dl_user_thr_mbps=18.0, ul_user_thr_mbps=1.0, ho_sr=99.2,
                 ul_rssi_dbm=-99.0, total_traffic_gb=5.0 * busy,
                 rrc_conn_users_avg=20.0),
        ]
    df = pd.DataFrame(rows)
    df["technology"] = "LTE"
    df["duplex"] = "CELL_FDD"
    df["granularity"] = "hour"
    return df


def test_4g_analysis_separates_congestion_from_interference(synth_4g):
    res = analyse_4g(synth_4g)
    by_cell = res.findings.groupby("Cell")["Class"].apply(set)
    assert "L_Clean_BAS0001-1" not in by_cell.index
    assert "high_utilization" in by_cell["L_Busy_BAS0002-1"]
    assert "interference" in by_cell["L_Noisy_BAS0003-1"]
    # the busy-hour view has to survive the daily mean
    row = res.per_entity.set_index("cell_id").loc["L_Busy_BAS0002-1"]
    assert row["bh_dl_prb_util"] > row["dl_prb_util"]
    assert res.findings["Recommended action"].str.len().min() > 20


def test_hourly_pivot_is_one_row_per_cell_one_column_per_hour(synth_4g):
    piv = hourly_pivot(synth_4g, "dl_prb_util")
    assert list(piv.columns[:2]) == ["Cell", "duplex"]
    assert len(piv) == 3
    assert len(piv.columns) == 2 + 24
    assert "09/10 18:00" in piv.columns


def test_workbook_has_the_report_and_the_pivots(tmp_path, synth_4g):
    res4 = analyse_4g(synth_4g)
    res3 = analyse_3g(load_hourly_kpi_3g(_zip_of(_3G_CSV), use_cache=False).df)
    out = build_kpi_workbook(tmp_path / "kpi.xlsx", res4=res4, res3=res3,
                             meta={"file_4g": "x.zip", "file_3g": "y.zip"},
                             pivots_4g=["DL_PRB_Utilization"],
                             pivots_3g=["3G_Availability"])
    names = pd.ExcelFile(out).sheet_names
    for want in ("Summary", "4G Network KPIs", "4G Action List",
                 "4G Cell Summary", "3G Action List", "DL_PRB_Utilization",
                 "3G_Availability"):
        assert want in names, f"{want} missing from {names}"
    assert "DL_Throughput" not in names            # not asked for
    summary = pd.read_excel(out, sheet_name="Summary")
    assert summary.set_index("Item").loc["4G cells", "Value"] == 3
