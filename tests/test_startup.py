"""The unified startup: one screen, over one background, that follows the real
preparation of every dataset and service the pages read — and no page loading
of its own afterwards."""

import base64
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import _startup as S  # noqa: E402


def test_the_background_is_the_high_resolution_original_as_provided():
    from PIL import Image
    name = S.BG_URL.rsplit("/", 1)[-1]
    path = APP / "static" / "startup" / name
    assert S.BG_URL == "/app/static/startup/rf_startup_bg.png" and path.exists()
    im = Image.open(path)
    assert im.format == "PNG" and im.size == (1672, 940)       # never resampled
    assert abs(S._ASPECT - 1672 / 940) < 1e-6
    # one background: no older or smaller copy left beside it
    assert [n for n in S.BG_NAMES if (APP / "static" / "startup" / n).exists()] == [name]


def test_the_startup_says_rf_analysis_only():
    text = S.markup() + S.CSS + S.JS
    assert "RF Optimization" not in text
    assert "Initialising RF Analysis" in text and "Launching RF Analysis Platform" in text


def test_the_layer_carries_every_element_over_one_background():
    html = S.markup()
    assert html.count('class="rfs-bg"') == 1 and html.count("<img") == 1
    assert html.count('class="stp"') == 4 and html.count('class="cn"') == len(S.CITIES)
    assert html.count('class="st"') == len(S.LINKS) and html.count('class="tw"') == 4
    for label, _ in S.STEPS:
        assert label in html
    assert "Ready" in html and "Launching RF Analysis Platform" in html
    # sharp: nothing blurred or rescaled
    assert "blur(" not in S.CSS and "feGaussianBlur" not in html
    assert "scale(" not in S.CSS.split("@keyframes rfsRing")[0]
    assert "translate(" not in S.CSS


def test_ready_comes_from_the_server_not_a_clock():
    js = S.JS
    assert "serverReady" in js and "m.type === 'ready'" in js
    # Ready only once the server said so and the bar reached the tasks done
    ready = js[js.index("if (serverReady && !readyAt"):]
    assert "shownPct >= 100" in ready.split("\n")[0]
    assert "100 * done / total" in js


def test_a_message_is_a_script_free_of_markup():
    body = S.message({"type": "task", "i": 0, "step": 1, "label": "EP <Tracker> — x"})
    inner = body[len("<script>"):-len("</script>")]
    assert "<" not in inner and ">" not in inner
    got = json.loads(re.search(r"msg\((.*)\)$", inner).group(1))
    assert got["label"] == "EP <Tracker> — x"


def test_the_preparation_runs_the_pages_own_loaders(tmp_path, monkeypatch, put_resource):
    monkeypatch.setenv("RFOPT_CACHE_DIR", str(tmp_path))
    import _warmup as WU
    # nothing applied: every data task is skipped, nothing fails
    out = WU.run()
    assert [o["state"] for o in out if o["state"] != "skipped"] == ["ok", "ok"]
    # a Daily Target, a KPI export and a KMZ: their tasks run
    from test_complaint_analysis import _target_bytes
    from test_site_map_ticket import _kmz, _kpi_csv
    from rfopt.complaints.target_store import save_target
    save_target(_target_bytes(), "Target 13-Sep.xlsx")
    put_resource("kpi", "R5 4G Monitoring Hourly KPI.csv", _kpi_csv().encode(), "4G KPI")
    put_resource("kmz", "R5_Sites.kmz", _kmz(), "Site KMZ")
    msgs = []
    out = WU.run(msgs.append)
    states = {o["label"]: o["state"] for o in out}
    for label in ("Loading Daily Target", "Loading Network Data — Site KMZ",
                  "Processing Network Data — KPI Exports",
                  "Preparing Analysis Engine — KPI Health",
                  "Preparing Analysis Engine — Complaints"):
        assert states[label] == "ok", (label, out)
    assert states["Loading Network Data — EP Tracker"] == "skipped"     # not applied
    assert not [o for o in out if o["state"] == "failed"], out
    assert msgs[0] == {"type": "begin", "total": len(out)} and msgs[-1] == {"type": "ready"}
    assert sum(m["type"] == "done" for m in msgs) == len(out)
    assert WU.is_warm()
    # the pages' caches are filled: the Sites map's KMZ and the Complaints analysis
    import _complaints as C
    import _site_data
    from rfopt.resources import store
    kmz = store.load().resources["kmz"].files[0]
    assert len(_site_data.load_kmz_path(str(kmz.path)).sectors) == 3
    assert C.load_workspace(*C.window_setting()).n == 2


def test_no_page_shows_a_loading_step_of_its_own():
    kept = {"_kpi_bulk.py": "Drawing the charts",          # user actions, not page loading
            "_kpi_report.py": "Building the report",
            "site_map.py": "Reading that KPI out of the export"}
    for p in list(APP.glob("*.py")) + list((APP / "views").glob("*.py")):
        for m in re.finditer(r'show_spinner="([^"]*)"', p.read_text(encoding="utf-8")):
            assert p.name in kept and m.group(1).startswith(kept[p.name]), (p.name, m.group(1))
    css = (APP / "_ui.py").read_text(encoding="utf-8")
    assert '[data-testid="stStatusWidget"] { display: none !important; }' in css


def _scripts(at) -> list[str]:
    return [e.proto.body for e in at.get("html") if e.proto.body.startswith("<script>")]


def test_the_first_run_shows_the_screen_and_prepares_then_never_again():
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    at = AppTest.from_file(str(APP / "Home.py"), default_timeout=180)
    at.run()
    assert not at.exception, at.exception
    bodies = _scripts(at)
    screen = [b for b in bodies if "atob(" in b]
    assert len(screen) == 1
    # st.html drops a script whose text holds markup: the loader holds none
    loader = screen[0][len("<script>"):-len("</script>")]
    assert "<" not in loader and ">" not in loader
    js = base64.b64decode(re.search(r"atob\('([A-Za-z0-9+/=]+)'\)", screen[0]).group(1)).decode()
    assert "rf_startup_done" in js and S.BG_URL in js
    msgs = [json.loads(re.search(r"msg\((.*)\)</script>$", b).group(1))
            for b in bodies if "rfStartup.msg(" in b]
    assert msgs[0]["type"] == "begin" and msgs[-1] == {"type": "ready"}
    assert {m["type"] for m in msgs} == {"begin", "task", "done", "ready"}
    at.run()                                        # a rerun: prepared already
    assert not at.exception, at.exception
    assert _scripts(at) == []
