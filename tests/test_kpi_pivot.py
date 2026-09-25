"""The emailed pivot workbook: layout, sheet naming and the colour rules."""

import re
import zipfile

import pandas as pd
import pytest

from rfopt.reports.kpi_pivot import (SPECS, pivot_one, spec_for,
                                     write_pivot_workbook)

PRB = "HW_DL PRB Avg Utilization(%)"
INTF = "L.UL.Interference.Avg(dBm)"


@pytest.fixture
def hourly():
    """Two cells over six hours, one of them breaching the PRB threshold."""
    idx = pd.date_range("2026-09-10", periods=6, freq="h")
    rows = []
    for cell, prb in (("L_A_BAS0001-1", 30.0), ("L_A_BAS0001-2", 88.0)):
        for i, t in enumerate(idx):
            rows.append({"datetime": t, "object": cell, "duplex": "CELL_FDD",
                         "site_id": "BAS0001", "prefix": "BAS",
                         PRB: prb + i, INTF: -112.0 + i})
    return pd.DataFrame(rows)


def _sheet_xml(path, sheet_index: int = 1) -> str:
    with zipfile.ZipFile(path) as z:
        return z.read(f"xl/worksheets/sheet{sheet_index}.xml").decode()


def test_pivot_is_a_row_per_cell_and_a_column_per_hour(hourly):
    piv = pivot_one(hourly, PRB)
    assert list(piv.columns[:2]) == ["object", "Cell FDD TDD Indication"]
    assert len(piv) == 2
    assert len(piv.columns) == 2 + 6


def test_the_five_known_kpis_keep_their_sheet_names():
    assert spec_for(INTF).sheet == "UL Inter"
    assert spec_for("LTE_Availability(%)@AB").sheet == "LTE_Availability"
    assert spec_for(PRB).sheet == "DL_PRB_Utilization"
    assert set(SPECS) >= {INTF, PRB, "4G Data Volume (GB)",
                          "LTE_Availability(%)@AB",
                          "4G DL User Throughput mbps_Asiacell"}


def test_an_unlisted_kpi_gets_a_legal_unique_sheet_name():
    s = spec_for("L.E-RAB.AbnormRel.Radio/DRBReset[x]")
    assert not set(s.sheet) & set(":\\/?*[]")
    assert len(s.sheet) <= 31
    assert spec_for("Same Name", {"Same Name"}).sheet != "Same Name"


def test_workbook_layout_matches_the_emailed_format(tmp_path, hourly):
    out, sheets, _ = write_pivot_workbook(hourly, [PRB], tmp_path / "book.xlsx")
    assert sheets == ["DL_PRB_Utilization"]

    head = pd.read_excel(out, sheet_name="DL_PRB_Utilization", header=None,
                         nrows=3)
    banner = str(head.iloc[0, 0])
    assert banner.startswith(f"Average of {PRB} - 2026-09-10 - Hourly")
    assert "Issue threshold: > 80" in banner
    assert list(head.iloc[1])[:2] == ["Row Labels", "Cell FDD TDD Indication"]
    assert str(head.iloc[1, 2]) == "09/10 12:00 AM"
    assert str(head.iloc[2, 1]) == "CELL_FDD"

    xml = _sheet_xml(out)
    assert 'topLeftCell="C3"' in xml           # frozen under the header
    assert "<autoFilter" in xml


def test_the_breach_flag_outranks_the_gradient(tmp_path, hourly):
    """The rule order is the whole point: xlsxwriter gives the rule added
    first the higher precedence, and without stopIfTrue the gradient repaints
    a real breach."""
    out, _, _ = write_pivot_workbook(hourly, [PRB], tmp_path / "book.xlsx")
    xml = _sheet_xml(out)
    flag = re.search(r'<cfRule type="cellIs"[^>]*priority="(\d+)"[^>]*'
                     r'stopIfTrue="1"', xml)
    scale = re.search(r'<cfRule type="colorScale" priority="(\d+)"', xml)
    assert flag and scale
    assert int(flag.group(1)) < int(scale.group(1))
    assert "<formula>80</formula>" in xml


def test_fixed_anchors_are_used_not_percentiles(tmp_path, hourly):
    """A quiet cell must not be painted red for being the sheet's minimum —
    the anchors are tied to the KPI's real range."""
    out, _, _ = write_pivot_workbook(hourly, [INTF], tmp_path / "book.xlsx")
    xml = _sheet_xml(out)
    assert '<cfvo type="num" val="-140"/>' in xml
    assert '<cfvo type="num" val="-100"/>' in xml
    assert '<cfvo type="num" val="-60"/>' in xml
    assert "percentile" not in xml


def test_an_unlisted_kpi_is_coloured_by_which_way_is_better(tmp_path):
    idx = pd.date_range("2026-09-10", periods=4, freq="h")
    df = pd.DataFrame([{"datetime": t, "object": "c1", "duplex": "CELL_FDD",
                        "site_id": "BAS0001", "prefix": "BAS",
                        "4G CSSR %_Asiacell": 99.0,
                        "4G/LTE DROP CALL RATE (With MME) (%)_Asiacell": 0.5}
                       for t in idx])
    out, sheets, _ = write_pivot_workbook(
        df, ["4G CSSR %_Asiacell",
             "4G/LTE DROP CALL RATE (With MME) (%)_Asiacell"],
        tmp_path / "book.xlsx")
    assert len(sheets) == 2

    def scale(i: int) -> list[str]:
        block = re.search(r"<colorScale>.*?</colorScale>", _sheet_xml(out, i),
                          re.S).group(0)
        return re.findall(r'<color rgb="FF(\w{6})"/>', block)

    red, yellow, green = "F8696B", "FFEB84", "63BE7B"
    assert scale(1) == [red, yellow, green]      # CSSR: low is the bad end
    assert scale(2) == [green, yellow, red]      # drop rate: high is the bad end


def test_a_mixed_export_writes_every_technologys_sheet(tmp_path, hourly):
    """Picking a 4G and a 3G KPI together used to produce the 4G sheets only.

    The 4G rows carry `duplex` and the 3G rows do not, and the second column
    was chosen from the whole frame — so the 3G sheet was indexed on an empty
    duplex, pivot_table dropped every row as NaN-indexed, and the sheet was
    quietly skipped.
    """
    rtt = "VS.IPPM.Rtt.Means(ms)"
    idx = pd.date_range("2026-09-10", periods=6, freq="h")
    three_g = pd.DataFrame([{"datetime": t, "object": "NodeB_BAS0001",
                             "parent": "RBASH01", "site_id": "BAS0001",
                             "prefix": "BAS", "tech": "3G", rtt: 4.0 + i}
                            for i, t in enumerate(idx)])
    mixed = pd.concat([hourly.assign(tech="4G"), three_g], ignore_index=True)

    out, sheets, left = write_pivot_workbook(mixed, [PRB, rtt],
                                             tmp_path / "mixed.xlsx")
    assert sheets == ["DL_PRB_Utilization", rtt]
    assert not left

    four = pd.read_excel(out, sheet_name="DL_PRB_Utilization", header=None,
                         nrows=3)
    assert list(four.iloc[1])[:2] == ["Row Labels", "Cell FDD TDD Indication"]
    assert len(pd.read_excel(out, sheet_name="DL_PRB_Utilization",
                             header=1)) == 2            # the two 4G cells

    three = pd.read_excel(out, sheet_name=rtt, header=None, nrows=3)
    assert list(three.iloc[1])[:2] == ["Row Labels", "RNC"]
    assert str(three.iloc[2, 0]) == "NodeB_BAS0001"
    assert str(three.iloc[2, 1]) == "RBASH01"


def test_a_2g_sheet_names_its_group_column_bsc(tmp_path):
    idx = pd.date_range("2026-09-10", periods=3, freq="h")
    df = pd.DataFrame([{"datetime": t, "object": "G_A_BAS0001A",
                        "parent": "BSCBAS01", "site_id": "BAS0001",
                        "prefix": "BAS", "tech": "2G",
                        "TCH Drop Rate(%)": 0.4} for t in idx])
    out, sheets, _ = write_pivot_workbook(df, ["TCH Drop Rate(%)"],
                                          tmp_path / "2g.xlsx")
    head = pd.read_excel(out, sheet_name=sheets[0], header=None, nrows=2)
    assert list(head.iloc[1])[:2] == ["Row Labels", "BSC"]


def test_sheets_come_out_in_the_teams_own_order(hourly):
    from rfopt.reports.kpi_pivot import order_kpis

    picked = ["LTE_Availability(%)@AB", "Integrity", PRB, INTF]
    assert order_kpis(picked) == [INTF, "LTE_Availability(%)@AB", PRB,
                                  "Integrity"]


def test_a_sheet_per_selected_kpi_up_to_the_cap(tmp_path, hourly):
    out, sheets, _ = write_pivot_workbook(hourly, [PRB, INTF],
                                          tmp_path / "two.xlsx")
    assert sheets == ["UL Inter", "DL_PRB_Utilization"]
    assert pd.ExcelFile(out).sheet_names == sheets

    out2, capped, over = write_pivot_workbook(hourly, [PRB, INTF],
                                              tmp_path / "one.xlsx", max_sheets=1)
    assert capped == ["UL Inter"]
    assert over == [PRB]        # named, not silently dropped
    assert out2.exists()
