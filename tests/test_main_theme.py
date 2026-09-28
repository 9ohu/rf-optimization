"""The main system's look: the Iraq network map behind every page, the glass
sidebar with HUAWEI and its tower, and no Deploy button or ⋮ menu."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import _ui as U  # noqa: E402


def test_the_assets_are_the_originals_never_resampled():
    from PIL import Image
    bg = Image.open(APP / "static" / "main" / "rf_main_bg.png")
    tower = Image.open(APP / "static" / "main" / "rf_sidebar_tower.png")
    assert bg.format == tower.format == "PNG"
    assert bg.size == (1672, 941)                  # the map at its own resolution
    assert tower.size == (860, 1225)               # a crop of the tower, not rescaled
    assert "aspect-ratio: 860 / 1225" in U._CSS


def test_the_map_is_behind_the_main_area_and_the_panels_are_glass():
    css = U._CSS
    main = css[css.index('[data-testid="stMain"] {'):]
    assert 'url("/app/static/main/rf_main_bg.png") center 42% / cover no-repeat' in main
    assert "backdrop-filter" in css and "rgba(7, 19, 36, .62)" in css


def test_the_tower_belongs_to_the_sidebar_and_collapses_with_it():
    css = U._CSS
    assert '[data-testid="stSidebar"]::before' in css
    assert "rf_sidebar_tower.png" in css.split('[data-testid="stSidebar"]::before')[1][:600]
    assert '[data-testid="stSidebar"][aria-expanded="false"]::before { display: none; }' in css
    # nothing fixed to the screen: it moves with the sidebar
    before = css.split('[data-testid="stSidebar"]::before {')[1].split("}")[0]
    assert "position: absolute" in before and "fixed" not in before


def test_the_sidebar_brand_is_huawei_and_its_author_only():
    assert "HUAWEI" in U.LOGO_SVG and "&#169; Shamsaldin Ali" in U.LOGO_SVG
    assert U.LOGO_SVG.count("<path") == 8          # the eight petals
    assert "RF Optimization" not in U.LOGO_SVG
    for text in (U.LOGO_SVG, U._CSS, (APP / "Home.py").read_text(encoding="utf-8")):
        assert "Intelligent Iraq" not in text


def test_no_deploy_button_and_no_menu():
    css = U._CSS
    assert ('[data-testid="stAppDeployButton"], [data-testid="stMainMenu"] '
            '{ display: none !important; }') in css
    cfg = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert 'toolbarMode = "minimal"' in cfg


def test_the_startup_screen_is_left_as_it_was():
    import _startup as S
    assert S.BG_URL == "/app/static/startup/rf_startup_bg.png"
    assert "rf_main_bg" not in S.CSS + S.JS + S.markup()


def test_the_project_chip_reads_ms_asiacell_project():
    import inspect
    src = inspect.getsource(U.header)
    assert 'project: str = "MS Asiacell Project"' in src
    assert "Project: {" not in src and "R5 · Asiacell" not in src


def test_sleep_analysis_panels_are_denser_glass_on_that_page_only():
    import _sleep
    assert '[class*="st-key-sl_card"]' in _sleep.CSS
    assert "rgba(6, 16, 31, .90) !important" in _sleep.CSS
    assert "rgba(6, 16, 31, .90)" not in U._CSS          # the other pages keep theirs
