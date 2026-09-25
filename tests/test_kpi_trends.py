"""Sniffing a dropped KPI file, reading only the picked columns, and trends."""

import io
import zipfile

import numpy as np
import pandas as pd
import pytest

from rfopt.ingest.hourly_kpi import load_hourly_raw, sniff_kpi_export
from rfopt.kpi.trends import (agg_how, object_options, series_for,
                              summary_table, trend_of, trends_for)

_BANNER = "\n\n\nSHAMS-3G\nSave Time :2026-09-11 10:51:53\nUser Name :x\n\n"
_3G = _BANNER + (
    "Time,RNC,NODEBNAME,NodeB ID,Integrity,VS.RscGroup.FlowCtrol.DL.DropNum,"
    "VS.IPPM.Rtt.Means(ms),3G_Availability@AB\n"
    "2026-09-08 00:00,RBASH01,Tannumah6_BAS0038,38,100%,0,1,100\n"
    "2026-09-08 01:00,RBASH01,Tannumah6_BAS0038,38,100%,900,45,92\n"
    "2026-09-08 00:00,REMAH02,Aager_NAS1580,1580,100%,10,4,100\n")

_4G = _BANNER.replace("SHAMS-3G", "4G Monitoring Hourly KPI's") + (
    "Time,eNodeB Name,Cell FDD TDD Indication,Cell Name,LocalCell Id,"
    "eNodeB Function Name,Integrity,4G Data Volume (GB),"
    "HW_DL PRB Avg Utilization(%),L.UL.Interference.Avg(dBm)\n"
    "2026-09-10 00:00,Sebeliyat_BAS3128,CELL_FDD,L_Sebeliyat_BAS3128-1,1,"
    "L_Sebeliyat_BAS3128,100%,11.6,62.8,-112\n"
    "2026-09-10 01:00,Sebeliyat_BAS3128,CELL_FDD,L_Sebeliyat_BAS3128-1,1,"
    "L_Sebeliyat_BAS3128,100%,9.4,55.1,-113\n"
    "2026-09-10 00:00,Najmi_SAM5849,CELL_FDD,L_Najmi_SAM5849-1,1,"
    "L_Najmi_SAM5849,100%,4.2,20.0,-118\n")


def _zip(text: str, name: str) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("export(Subreport 1).csv", text)
    buf.seek(0)
    buf.name = name
    return buf


def test_sniff_names_the_technology_and_lists_every_kpi():
    i4 = sniff_kpi_export(_zip(_4G, "4g.zip"))
    assert i4.kind == "4G"
    # every measured column, and not one of the object / hour columns
    assert "4G Data Volume (GB)" in i4.all_kpis
    assert "Integrity" in i4.all_kpis
    for ident in ("Time", "Cell Name", "eNodeB Name",
                  "Cell FDD TDD Indication"):
        assert ident not in i4.all_kpis

    i3 = sniff_kpi_export(_zip(_3G, "3g.zip"))
    assert i3.kind == "3G"
    assert "3G_Availability@AB" in i3.all_kpis
    assert "RNC" not in i3.all_kpis


_2G = _BANNER.replace("SHAMS-3G", "SHAMS-2G") + (
    "Time,BSC,Cell Name,Integrity,TCH Traffic (Erl),TCH Drop Rate(%),"
    "SDCCH Blocking Rate(%)\n"
    "2026-09-08 00:00,BSCBAS01,G_Tannumah_BAS0038A,100%,12.5,0.42,0.11\n"
    "2026-09-08 01:00,BSCBAS01,G_Tannumah_BAS0038A,100%,9.8,0.51,0.09\n"
    "2026-09-08 00:00,BSCBAG01,G_Elsewhere_BAG1234A,100%,4.0,0.20,0.00\n")


def test_sniff_recognises_a_2g_export():
    """No 2G file to hand yet, so the shape is inferred from how the operator
    names the others: a BSC column, or "2G" in the export's own banner."""
    info = sniff_kpi_export(_zip(_2G, "SHAMS-2G_Query_Result.zip"))
    assert info.kind == "2G"
    assert "TCH Drop Rate(%)" in info.all_kpis
    for ident in ("Time", "BSC", "Cell Name"):
        assert ident not in info.all_kpis

    df = load_hourly_raw(_zip(_2G, "SHAMS-2G_Query_Result.zip"),
                         ["TCH Traffic (Erl)"])
    # the charting path keeps every row — the export is already R5-scoped, and
    # the odd out-of-region row is the operator's, not ours to drop
    assert set(df["site_id"]) == {"BAS0038", "BAG1234"}
    assert df["object"].iloc[0] == "G_Tannumah_BAS0038A"
    assert df["parent"].iloc[0] == "BSCBAS01"         # the BSC, for the pivot
    assert df["TCH Traffic (Erl)"].max() == 12.5


def test_an_export_of_an_unnamed_technology_is_still_usable():
    """Better to chart a file whose technology we cannot name than to refuse
    it — the picker just calls it Other."""
    odd = _BANNER.replace("SHAMS-3G", "SOMETHING NEW") + (
        "Time,Cell Name,Integrity,Weird.Counter.Avg\n"
        "2026-09-08 00:00,X_Site_BAS0038-1,100%,7\n")
    info = sniff_kpi_export(_zip(odd, "odd.zip"))
    assert info.kind == "Other" and info.ok
    assert "Weird.Counter.Avg" in info.all_kpis


def test_sniff_shrugs_at_a_file_that_is_not_a_kpi_export():
    buf = io.BytesIO(b"just,some,csv\n1,2,3\n")
    buf.name = "random.csv"
    info = sniff_kpi_export(buf)
    assert info.kind == "unknown" and not info.ok and info.note


def test_raw_loader_reads_only_the_asked_for_columns():
    df = load_hourly_raw(_zip(_4G, "4g.zip"), ["4G Data Volume (GB)"])
    assert set(df.columns) == {"datetime", "object", "site_id", "sector_id",
                               "prefix", "duplex", "parent",
                               "4G Data Volume (GB)"}
    # the sector the cell sits on, so the map can colour its beam
    assert set(df["sector_id"]) == {"BAS3128-S1", "SAM5849-S1"}
    assert "HW_DL PRB Avg Utilization(%)" not in df.columns
    assert set(df["site_id"]) == {"BAS3128", "SAM5849"}
    assert df["4G Data Volume (GB)"].sum() == pytest.approx(25.2)


def test_agg_how_tells_a_level_from_a_counter():
    assert agg_how("HW_DL PRB Avg Utilization(%)") == "mean"
    assert agg_how("4G/LTE DROP CALL RATE (With MME) (%)") == "mean"
    assert agg_how("4G Data Volume (GB)") == "sum"
    assert agg_how("VS.RscGroup.FlowCtrol.DL.DropNum") == "sum"


def _series(values, start="2026-09-10"):
    idx = pd.date_range(start, periods=len(values), freq="h")
    return pd.Series(values, index=idx, dtype=float)


def test_trend_calls_rising_falling_and_flat():
    up = trend_of(_series(np.linspace(10, 20, 24)), "kpi")
    assert up.verdict == "Increasing" and up.pct_change > 50

    down = trend_of(_series(np.linspace(20, 10, 24)), "kpi")
    assert down.verdict == "Decreasing" and down.pct_change < -40

    flat = trend_of(_series([50, 51, 49, 50.5, 49.5] * 5), "kpi")
    assert flat.verdict == "Stable" and abs(flat.pct_change) < 3


def test_one_bad_hour_does_not_swing_the_verdict():
    """A single dead hour is an incident, not a trend — the robust fit has to
    ignore it or every outage would read as a collapse."""
    vals = [100.0] * 24
    vals[11] = 0.0
    assert trend_of(_series(vals), "LTE_Availability(%)@AB").verdict == "Stable"


def test_a_shorter_file_does_not_invent_a_rise(tmp_path):
    """Two exports covering different windows: the 4G counter must not read as
    a huge increase just because the 3G file reaches further back and its hours
    sum to zero."""
    d4 = load_hourly_raw(_zip(_4G, "4g.zip"), ["4G Data Volume (GB)"])
    d3 = load_hourly_raw(_zip(_3G, "3g.zip"), ["3G_Availability@AB"])
    both = pd.concat([d4, d3], ignore_index=True)

    s = series_for(both, "4G Data Volume (GB)")
    assert len(s) == 2                      # only the hours 4G actually covers
    assert s.iloc[0] > 0
    assert trend_of(s, "4G Data Volume (GB)").first > 0


_3G_NO_PROBE = _BANNER + (
    "Time,RNC,NODEBNAME,NodeB ID,Integrity,VS.RscGroup.FlowCtrol.DL.DropNum,"
    "VS.IPPM.Rtt.Means(ms),3G_Availability@AB\n"
    "2026-09-08 00:00,RBASH01,NoProbe_BAS0038,38,100%,0,0,100\n"
    "2026-09-08 01:00,RBASH01,NoProbe_BAS0038,38,100%,0,0,100\n"
    "2026-09-08 00:00,REMAH02,Probed_NAS1580,1580,100%,0,6,100\n"
    "2026-09-08 01:00,REMAH02,Probed_NAS1580,1580,100%,0,8,100\n")


def test_a_zero_rtt_is_no_measurement_not_a_flat_line():
    """VS.IPPM.Rtt.Means reads 0 where the IPPM probe is not running. A 0 ms
    round trip is impossible, and charting it drew a line along the axis that
    looked like data — and dragged the network average down with it."""
    df = load_hourly_raw(_zip(_3G_NO_PROBE, "3g.zip"),
                         ["VS.IPPM.Rtt.Means(ms)",
                          "VS.RscGroup.FlowCtrol.DL.DropNum"])
    rtt = "VS.IPPM.Rtt.Means(ms)"
    assert not (df[rtt] == 0).any()
    assert df[rtt].isna().sum() == 2                 # the unprobed NodeB

    assert len(series_for(df, rtt, level="Cell", obj="NoProbe_BAS0038")) == 0
    probed = series_for(df, rtt, level="Cell", obj="Probed_NAS1580")
    assert list(probed) == [6.0, 8.0]
    assert series_for(df, rtt).mean() == pytest.approx(7.0)   # not 3.5

    # a zero drop count is a real (good) reading and must survive
    drops = "VS.RscGroup.FlowCtrol.DL.DropNum"
    assert (df[drops] == 0).all()
    assert len(series_for(df, drops, level="Cell", obj="NoProbe_BAS0038")) == 2


def test_series_and_options_follow_the_chosen_object():
    df = load_hourly_raw(_zip(_4G, "4g.zip"), ["4G Data Volume (GB)"])
    assert object_options(df, "Site") == ["BAS3128", "SAM5849"]
    assert object_options(df, "Prefix") == ["BAS", "SAM"]

    whole = series_for(df, "4G Data Volume (GB)")
    one = series_for(df, "4G Data Volume (GB)", level="Site", obj="SAM5849")
    assert whole.iloc[0] == pytest.approx(15.8)     # 11.6 + 4.2 summed
    assert one.iloc[0] == pytest.approx(4.2)


def test_summary_table_puts_the_biggest_mover_first():
    df = load_hourly_raw(_zip(_4G, "4g.zip"),
                         ["4G Data Volume (GB)", "HW_DL PRB Avg Utilization(%)"])
    tbl = summary_table(trends_for(df, ["4G Data Volume (GB)",
                                        "HW_DL PRB Avg Utilization(%)"]))
    assert list(tbl.columns)[:3] == ["KPI", "Trend", "Change %"]
    assert tbl["Change %"].abs().is_monotonic_decreasing
