"""KPI Analysis · Draw Data — Bulk Draw: a Site ID + Sector list, a chart each.

The uploaded list carries no cell: the EP tracker says which cells a site and a
sector hold (Site → Sector → Cells), and the charts, the workbook and the
report are Draw Data's own. Single Draw is untouched. Every test runs on its
own empty store (conftest).
"""

import io
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

# the export: two sites of four cells each (BAS0001 sectors 1-2, BAS0002 sector 1),
# and a 3G NodeB that no sector owns
INDEX = pd.DataFrame([
    ("L_A_BAS0001-1", "BAS0001", "BAS0001-S1"),
    ("L21_A_BAS0001-1", "BAS0001", "BAS0001-S1"),
    ("L_A_BAS0001-6", "BAS0001", "BAS0001-S6"),      # the EP tracker calls it sector 2
    ("L_A_BAS0001-2", "BAS0001", "BAS0001-S2"),
    ("L26_A_BAS0001-9", "BAS0001", "BAS0001-S0"),    # not in the tracker: sector 9 by name
    ("L_B_BAS0002-1", "BAS0002", "BAS0002-S1"),
    ("U_A_BAS0001", "BAS0001", "BAS0001-S0"),        # a NodeB beside 4G cells
    ("U_C_BAS0003", "BAS0003", "BAS0003-S0"),        # a site the export measures whole
], columns=["object", "site_id", "sector_id"])

EP = pd.DataFrame({
    "cell": ["L_A_BAS0001-1", "L21_A_BAS0001-1", "L_A_BAS0001-6", "L_A_BAS0001-2",
             "L_B_BAS0002-1"],
    "ep_site": ["BAS0001"] * 4 + ["BAS0002"],
    "ep_sector": [1.0, 1.0, 2.0, 2.0, 1.0],
    "band": ["L1800", "L2100", "L1800", "L1800", "L1800"],
    "tech": ["LTE"] * 5,
}).set_index("cell")
KPI = "HW_DL PRB Avg Utilization(%)"


def _list_xlsx(rows) -> bytes:
    buf = io.BytesIO()
    pd.DataFrame(rows, columns=["Site ID ", "sector  "]).to_excel(buf, index=False)
    return buf.getvalue()


def _hourly() -> pd.DataFrame:
    """A little hourly export: every object, 6 hours, one KPI."""
    hours = pd.date_range("2026-09-17", periods=6, freq="h")
    rows = []
    for i, r in enumerate(INDEX.itertuples(index=False)):
        for h, t in enumerate(hours):
            rows.append({"datetime": t, "object": r.object, "site_id": r.site_id,
                         "sector_id": r.sector_id, "prefix": r.site_id[:3],
                         KPI: 10.0 + i + h})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# the list, and the cells behind it
# --------------------------------------------------------------------------- #
def test_the_list_is_read_as_site_id_and_sector():
    import _kpi_bulk as B
    rows = B.read_list("Book4.xlsx", _list_xlsx([("bas0001 ", 2.0), ("BAS0002", 1),
                                                 ("BAS0001", 2), ("BAS9999", None)]))
    assert list(rows["site_id"]) == ["BAS0001", "BAS0002", "BAS9999"]   # upper, once each
    assert list(rows["sector"])[:2] == [2.0, 1.0] and pd.isna(rows["sector"].iloc[2])
    with pytest.raises(B.ListError):
        B.read_list("notes.xlsx", _list_xlsx([])[:200])
    other = io.BytesIO()
    pd.DataFrame({"Cell": ["x"], "Value": [1]}).to_excel(other, index=False)
    with pytest.raises(B.ListError, match="Site ID"):
        B.read_list("other.xlsx", other.getvalue())


def test_the_ep_tracker_says_which_sector_a_cell_is_in():
    import _kpi_bulk as B
    o = B.objects_of(INDEX, EP).set_index("object")
    # the tracker wins over the name the export gives the cell
    assert o.loc["L_A_BAS0001-6", "sector"] == 2.0 and o.loc["L_A_BAS0001-6", "from_ep"]
    assert o.loc["L_A_BAS0001-1", "sector"] == 1.0
    # a cell the tracker does not list keeps the sector the export names
    assert o.loc["L26_A_BAS0001-9", "sector"] == 0.0
    assert not o.loc["L26_A_BAS0001-9", "from_ep"]
    # only an object nobody places answers for the whole site (a 3G NodeB)
    assert o.loc["U_A_BAS0001", "site_level"] and not o.loc["L_A_BAS0001-6", "site_level"]
    assert o.loc["L_A_BAS0001-1", "band"] == "L1800"


def test_per_site_takes_every_cell_and_ignores_the_sector_column():
    import _kpi_bulk as B
    rows = pd.DataFrame({"site_id": ["BAS0001", "BAS0002"], "sector": [2.0, 1.0]})
    groups, missing = B.groups_for(B.objects_of(INDEX, EP), rows, per_sector=False)
    assert [g.title for g in groups] == ["BAS0001", "BAS0002"] and not missing
    assert sorted(groups[0].cells) == sorted(INDEX[INDEX["site_id"].eq("BAS0001")]["object"])
    assert len(groups[0].cells) == 6          # every object of the site, the NodeB too
    assert groups[0].bands == ["L1800", "L2100"]


def test_per_sector_takes_the_sector_the_tracker_gives():
    import _kpi_bulk as B
    rows = pd.DataFrame({"site_id": ["BAS0001", "BAS0001", "BAS0002", "BAS0003", "BAS9999",
                                     "BAS0002", "BAS0001"],
                         "sector": [1.0, 2.0, 1.0, 2.0, 1.0, None, 5.0]})
    groups, missing = B.groups_for(B.objects_of(INDEX, EP), rows, per_sector=True)
    by = {g.title: sorted(g.cells) for g in groups}
    # only the sector's own cells — the cell nobody places is not in either
    assert by["BAS0001 - Sector 1"] == ["L21_A_BAS0001-1", "L_A_BAS0001-1"]
    assert by["BAS0001 - Sector 2"] == ["L_A_BAS0001-2", "L_A_BAS0001-6"]
    assert by["BAS0002 - Sector 1"] == ["L_B_BAS0002-1"]
    # a site the export measures whole (a 3G NodeB) answers for its sectors
    assert by["BAS0003 - Sector 2"] == ["U_C_BAS0003"]
    # nothing is dropped in silence
    assert ("BAS9999 · Sector 1", "no cell of this site in the loaded export") in missing
    assert ("BAS0002", "no Sector in the list for this row") in missing
    assert ("BAS0001 · Sector 5", "no cell in this sector (EP tracker)") in missing


def test_a_site_level_kpi_is_drawn_for_the_site_not_for_cells():
    """3G flow control and its kind: the export counts it for the site."""
    import _kpi_bulk as B
    data = _hourly()
    data.loc[~data["object"].eq("U_C_BAS0003"), KPI] = None      # only the site holds it
    objects = B.objects_of(INDEX, EP)
    rows = pd.DataFrame({"site_id": ["BAS0003"], "sector": [2.0]})

    # Per Site: one series, the site's own
    groups, _ = B.groups_for(objects, rows, per_sector=False)
    plan, no_data = B.plan_for(groups, objects, data, KPI, per_sector=False)
    assert len(plan) == 1 and plan[0].site_only and plan[0].live == ["U_C_BAS0003"]
    assert not no_data
    panels = B._panels(("site",), data, KPI, plan)
    assert list(panels["BAS0003"].lines) == ["BAS0003"]           # the site, not the object
    assert len(next(iter(panels["BAS0003"].lines.values()))) == 6
    assert "site-level KPI" in B._cells_note(plan[0], True)

    # Per Sector: it cannot be split, and nothing is invented
    groups, _ = B.groups_for(objects, rows, per_sector=True)
    plan, no_data = B.plan_for(groups, objects, data, KPI, per_sector=True)
    assert plan == [] and len(no_data) == 1
    assert "site-level KPI" in no_data[0][1] and "cannot be split by sector" in no_data[0][1]


def test_a_cell_level_kpi_still_draws_its_cells():
    import _kpi_bulk as B
    data = _hourly()
    objects = B.objects_of(INDEX, EP)
    rows = pd.DataFrame({"site_id": ["BAS0001"], "sector": [2.0]})
    groups, _ = B.groups_for(objects, rows, per_sector=True)
    plan, no_data = B.plan_for(groups, objects, data, KPI, per_sector=True)
    assert len(plan) == 1 and not plan[0].site_only and not no_data
    assert sorted(plan[0].live) == ["L_A_BAS0001-2", "L_A_BAS0001-6"]
    # a cell with no value for this KPI drops out of the chart, the rest stay
    data.loc[data["object"].eq("L_A_BAS0001-6"), KPI] = None
    plan, _ = B.plan_for(groups, objects, data, KPI, per_sector=True)
    assert plan[0].live == ["L_A_BAS0001-2"] and not plan[0].site_only


def test_nothing_is_concatenated_when_the_kpi_is_not_in_the_export():
    """The reported crash: the technology changed under a drawn run."""
    import _kpi_bulk as B
    data = _hourly().drop(columns=[KPI]).assign(**{"Other KPI": 1.0})
    objects = B.objects_of(INDEX, EP)
    rows = pd.DataFrame({"site_id": ["BAS0001"], "sector": [2.0]})
    groups, _ = B.groups_for(objects, rows, per_sector=False)
    plan, no_data = B.plan_for(groups, objects, data, KPI, per_sector=False)
    assert plan == [] and no_data and "no KPI data" in no_data[0][1]
    page = (APP / "_kpi_bulk.py").read_text(encoding="utf-8")
    assert "if not frames:" in page                 # never pd.concat an empty list


# --------------------------------------------------------------------------- #
# the charts, the workbook, the report
# --------------------------------------------------------------------------- #
def test_a_chart_per_group_and_one_for_all_of_them():
    import _kpi_bulk as B
    rows = pd.DataFrame({"site_id": ["BAS0001", "BAS0002"], "sector": [2.0, 1.0]})
    groups, _ = B.groups_for(B.objects_of(INDEX, EP), rows, per_sector=True)
    data = _hourly()
    panels = B._panels(("t",), data, KPI, groups)
    one = panels["BAS0001|2"]
    assert sorted(one.lines) == ["L_A_BAS0001-2", "L_A_BAS0001-6"]
    assert one.kpi == KPI and len(next(iter(one.lines.values()))) == 6
    # the combined chart holds the cells of the drawn charts, each once
    assert sorted(panels["__cells__"]) == ["L_A_BAS0001-2", "L_A_BAS0001-6", "L_B_BAS0002-1"]
    assert sorted(panels["__all__"].lines) == sorted(panels["__cells__"])
    fig = B.figure(one)
    assert len(fig.data) == 2 and fig.layout.legend.title.text == "Cell Name:"


def test_the_workbook_is_the_draw_data_one_over_the_drawn_cells(tmp_path, monkeypatch):
    import _kpi_bulk as B
    from rfopt.reports.kpi_pivot import write_pivot_workbook
    monkeypatch.setattr(B._shared, "DL", tmp_path)
    data = _hourly()
    cells = ["L_A_BAS0001-2", "L_A_BAS0001-6"]
    note = B.excel(data, KPI, cells, "BulkDraw_Sector")
    assert "Saved to Downloads" in note
    mine = next(tmp_path.glob("BulkDraw_Sector*.xlsx"))
    # the same writer over the same rows: byte for byte the same layout
    want = tmp_path / "want.xlsx"
    write_pivot_workbook(data[data["object"].isin(cells)], [KPI], want)
    import openpyxl
    a = openpyxl.load_workbook(mine).active
    b = openpyxl.load_workbook(want).active
    assert a.title == b.title and a.dimensions == b.dimensions
    assert [c.value for c in a[2]] == [c.value for c in b[2]]        # the header row
    assert [c.value for c in a[3]] == [c.value for c in b[3]]        # the first cell row
    assert {r[0].value for r in a.iter_rows(min_row=3)} == set(cells)


def _deck(charts: int = 1):
    from rfopt.reports.bulk_deck import Deck
    return Deck(kpi=KPI, tech="4G", by="Per Sector", window="17 Sep 2026 00:00 – 06:00",
                charts=charts)


def test_the_report_is_a_powerpoint_of_six_charts_to_a_slide(tmp_path, monkeypatch):
    import _kpi_bulk as B
    Presentation = pytest.importorskip("pptx").Presentation
    monkeypatch.setattr(B._shared, "DL", tmp_path)
    rows = pd.DataFrame({"site_id": ["BAS0001"], "sector": [2.0]})
    groups, _ = B.groups_for(B.objects_of(INDEX, EP), rows, per_sector=True)
    panels = B._panels(("t2",), _hourly(), KPI, groups)
    one = panels[groups[0].key]
    # eight charts: two slides of the grid, the combined chart a slide of its own
    charts = [(f"{groups[0].title}", one, None) for _ in range(8)]
    note = B.save_report(charts, ("Combined Chart (All in One)", panels["__all__"], None),
                         _deck(9), "BulkDraw_Sector_PRB")
    assert "Saved to Downloads" in note and "4 slide(s)" in note

    deck = next(tmp_path.glob("*.pptx"))
    assert not list(tmp_path.glob("*.html")) and not list(tmp_path.glob("*.pdf"))
    prs = Presentation(str(deck))
    slides = list(prs.slides)
    assert len(slides) == 4                                  # cover, six, two, combined
    drawn = [sum(1 for sh in sl.shapes if getattr(sh, "has_chart", False)) for sl in slides]
    assert drawn == [0, 6, 2, 1]
    # the title above each chart, and no line of description under it
    titles = [sh.text_frame.text for sh in slides[1].shapes
              if sh.has_text_frame and sh.text_frame.text == "BAS0001 - Sector 2"]
    assert len(titles) == 6
    assert not any("cell(s)" in sh.text_frame.text for sh in slides[1].shapes
                   if sh.has_text_frame)
    last = " ".join(sh.text_frame.text for sh in slides[-1].shapes if sh.has_text_frame)
    assert "Combined Chart" in last                          # after the individual charts

    # the Report Export's own dress: the navy card, the cyan rule, Draw Data's lines
    card = next(sh for sh in slides[1].shapes if sh.shape_type == 1 and not sh.has_chart)
    assert str(card.fill.fore_color.rgb) == "0B1F33"
    chart = next(sh for sh in slides[1].shapes if getattr(sh, "has_chart", False)).chart
    assert not chart.has_legend
    assert str(list(chart.plots[0].series)[0].format.line.color.rgb) == "20BFFF"
    assert len(chart.plots[0].categories) == 6

    low = deck.read_bytes().lower()
    assert not any(w in low for w in (b"generated by ai", b"ai-generated", b"ai generated",
                                      b"powered by ai", b"artificial intelligence",
                                      b"claude", b"anthropic"))


def test_a_site_level_kpi_and_a_single_chart_still_make_a_deck(tmp_path, monkeypatch):
    import _kpi_bulk as B
    Presentation = pytest.importorskip("pptx").Presentation
    monkeypatch.setattr(B._shared, "DL", tmp_path)
    rows = pd.DataFrame({"site_id": ["BAS0003"], "sector": [float("nan")]})
    objects = B.objects_of(INDEX, EP)
    data = _hourly()
    groups, _ = B.groups_for(objects, rows, per_sector=False)
    groups, _ = B.plan_for(groups, objects, data, KPI, per_sector=False)
    panels = B._panels(("t3",), data, KPI, groups)
    assert groups[0].site_only and list(panels[groups[0].key].lines) == ["BAS0003"]
    note = B.save_report([(groups[0].title, panels[groups[0].key], None)], None,
                         _deck(1), "BulkDraw_Site_PRB")
    assert "1 chart(s) on 2 slide(s)" in note
    prs = Presentation(str(next(tmp_path.glob("*.pptx"))))
    assert len(list(prs.slides)) == 2
    head = [sh.text_frame.text for sh in list(prs.slides)[1].shapes if sh.has_text_frame]
    assert "Chart 1" in head and "BAS0003" in head           # the site is its own title


def test_the_deck_places_the_drawn_chart_itself_when_there_is_a_picture(tmp_path,
                                                                        monkeypatch):
    """The charts of the report are Draw Data's own, not drawn again."""
    import _kpi_bulk as B
    Presentation = pytest.importorskip("pptx").Presentation
    pio = pytest.importorskip("plotly.io")
    Image = pytest.importorskip("PIL.Image")
    monkeypatch.setattr(B._shared, "DL", tmp_path)
    rows = pd.DataFrame({"site_id": ["BAS0001"], "sector": [2.0]})
    groups, _ = B.groups_for(B.objects_of(INDEX, EP), rows, per_sector=True)
    panels = B._panels(("t4",), _hourly(), KPI, groups)

    shot = tmp_path / "one.png"
    try:                                        # no image engine here: nothing to test
        pio.write_images([B.figure(panels[groups[0].key], height=624)], [shot],
                         format="png", width=1080, height=624, scale=1)
    except Exception as exc:                    # pragma: no cover - depends on the machine
        pytest.skip(f"no image engine for plotly: {exc}")
    png = B._lighter(shot.read_bytes())
    assert len(png) <= shot.stat().st_size      # the card behind it, and a palette
    assert Image.open(io.BytesIO(png)).size == (1080, 624)

    B.save_report([("BAS0001 - Sector 2", panels[groups[0].key], png)], None, _deck(1),
                  "BulkDraw_Sector_PNG")
    slide = list(Presentation(str(next(tmp_path.glob("*.pptx")))).slides)[1]
    pics = [sh for sh in slide.shapes if sh.shape_type == 13 and sh.image.size == (1080, 624)]
    assert len(pics) == 1                       # the picture, not a chart drawn again
    assert not any(getattr(sh, "has_chart", False) for sh in slide.shapes)


def test_the_report_is_made_without_an_image_engine_too():
    """A machine with no kaleido still gets the deck, its lines drawn natively."""
    import _kpi_bulk as B
    panels = B._panels(("t5",), _hourly(), KPI,
                       B.groups_for(B.objects_of(INDEX, EP),
                                    pd.DataFrame({"site_id": ["BAS0001"], "sector": [2.0]}),
                                    per_sector=True)[0])
    jobs = [(panels["BAS0001|2"], 1080, 624), (None, 1080, 624)]

    def no_engine(*a, **k):
        raise RuntimeError("kaleido is not installed")

    import plotly.io as pio
    real, pio.write_images = pio.write_images, no_engine
    try:
        assert B.chart_pngs(jobs) == [None, None]
    finally:
        pio.write_images = real
    assert B.chart_pngs([]) == []


def test_every_chart_card_carries_a_copy_chart_button():
    import _kpi_bulk as B
    from _charts import COPY_JS
    assert "rfChartPng" in COPY_JS and "button.rf-copy" in COPY_JS
    head = B._head("rf_card_kb_BAS0001_2", "BAS0001 - Sector 2", "4G · " + KPI, "BAS0001_PRB")
    assert "sm-chart-head" in head and "<span class='t'>BAS0001 - Sector 2</span>" in head
    assert "data-card='rf_card_kb_BAS0001_2'" in head and ">Copy Chart<" in head
    page = (APP / "_kpi_bulk.py").read_text(encoding="utf-8")
    assert "st.html(COPY_JS, unsafe_allow_javascript=True)" in page
    # the card's key is what CSS sees: a sector key must hold word characters only
    assert r'card = "rf_card_kb_" + re.sub(r"\W", "_", g.key)' in page


# --------------------------------------------------------------------------- #
# the page
# --------------------------------------------------------------------------- #
def test_draw_data_has_both_modes_and_single_draw_is_untouched():
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    at = AppTest.from_file(str(APP / "views/kpi_draw.py"), default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    mode = at.segmented_control(key="kd_mode")
    assert list(mode.options) == ["Single Draw", "Bulk Draw"] and mode.value == "Single Draw"
    html = " ".join(e.proto.body for e in at.get("html"))
    assert "kb-step" not in html                       # Single Draw draws none of Bulk's steps

    at.segmented_control(key="kd_mode").set_value("Bulk Draw").run()
    assert not at.exception, at.exception
    html = " ".join(e.proto.body for e in at.get("html"))
    assert "No KPI Data yet" in html                   # nothing uploaded in this test's store
    page = (APP / "views/kpi_draw.py").read_text(encoding="utf-8")
    assert "W.open_workspace(" in page and "trend_state" in page       # Single Draw as it was
