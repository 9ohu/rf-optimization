import zipfile

from rfopt.ai import TemplateNarrator
from rfopt.geo import build_site_kmz
from rfopt.reports import build_markdown_report
from rfopt.reports.excel_report import write_excel_report


def test_markdown_report_has_sections(analysis):
    md = build_markdown_report(analysis)
    assert "# RF Optimisation Report" in md
    assert "Priority action plan" in md
    assert "R5-" in md


def test_excel_report_multisheet(analysis, tmp_path):
    p = write_excel_report(analysis, tmp_path / "r.xlsx")
    import openpyxl
    wb = openpyxl.load_workbook(p)
    assert {"Overview", "Action Plan", "Diagnoses", "Cell KPIs"} <= set(wb.sheetnames)


def test_kmz_is_valid_zip_with_kml(analysis, tmp_path):
    p = build_site_kmz(analysis.site_db, tmp_path / "m.kmz",
                       region_prefix="R5", diagnoses=analysis.diagnoses,
                       recommendations=analysis.recommendations)
    with zipfile.ZipFile(p) as z:
        assert "doc.kml" in z.namelist()
        kml = z.read("doc.kml").decode("utf-8")
    assert "<kml" in kml and "Polygon" in kml
    assert "R5-001" in kml


def test_template_narrator_full_structure(analysis):
    n = TemplateNarrator()
    d = analysis.diagnoses[0]
    r = next(r for r in analysis.recommendations if r.cell_id == d.entity_id)
    nar = n.narrate(d, r)
    for key in ("summary", "why_it_happens", "confirming_kpis",
                "recommended_action", "parameter_change", "risks",
                "monitor_after", "rollback"):
        assert nar.sections.get(key), key
