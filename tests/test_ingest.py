from rfopt.ingest import build_mapping, load_kpi_file, normalise_name
from rfopt.ingest.schema import kpi_def, kpis_for


def test_normalise_name_unifies_synonyms():
    assert normalise_name("RRC Setup Success Rate (%)") == \
        normalise_name("rrc_setup_success_ratio")
    assert normalise_name("DL User Throughput (Mbps)") == \
        normalise_name("Downlink User Throughput")


def test_mapping_resolves_huawei_display_names():
    cols = ["Start Time", "eNodeB Name", "Cell Name",
            "E-RAB Setup Success Rate (%)", "Average RSRP (dBm)",
            "DL PRB Utilization Rate (%)", "Total Traffic Volume (GB)"]
    m = build_mapping(cols, "LTE")
    assert m.resolved["E-RAB Setup Success Rate (%)"] == "erab_setup_sr"
    assert m.resolved["Average RSRP (dBm)"] == "avg_rsrp_dbm"
    assert m.resolved["DL PRB Utilization Rate (%)"] == "dl_prb_util"
    assert m.resolved["eNodeB Name"] == "site_id"
    assert not m.unmapped


def test_schema_directions_sane():
    assert kpi_def("erab_drop_rate", "LTE").direction == "down"
    assert kpi_def("avg_rsrp_dbm", "LTE").direction == "up"
    assert kpi_def("dl_prb_util", "LTE").direction == "down"
    assert len(kpis_for("LTE")) > 40


def test_loader_reads_sample_and_derives_dims(sample_data):
    res = load_kpi_file(str(sample_data / "r5_lte_kpi_hourly.xlsx"),
                        region_filter="R5")
    assert res.technology == "LTE"
    assert res.granularity == "hour"
    assert res.n_cells == 80 and res.n_sites == 10
    df = res.df
    assert {"site_id", "cell_id", "sector_id", "band", "datetime"} <= set(df.columns)
    # sector id groups co-sited carriers
    row = df[df["cell_id"] == "R5-002_L1800_S1"].iloc[0]
    assert row["sector_id"] == "R5-002-S1"
    assert df["avg_rsrp_dbm"].notna().mean() > 0.9
