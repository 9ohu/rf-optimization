"""The startup screen: one animated layer over the RF Optimization background,
added once when the app opens and gone before the pages are used."""

import base64
import re
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import _startup as S  # noqa: E402


def test_the_background_is_the_apps_own_asset():
    from PIL import Image
    path = APP / "static" / "startup" / "rf_startup_bg.jpg"
    assert S.BG_URL == "/app/static/startup/rf_startup_bg.jpg" and path.exists()
    w, h = Image.open(path).size
    assert w >= 2000 and abs(w / h - 2392 / 898) < 0.01
    # the storyboard's own labels were painted out of the top corners
    im = Image.open(path).convert("L")
    for box in ((0, 0, 430, 115), (2090, 0, 2345, 110)):
        assert max(im.crop(box).getdata()) < 120


def test_one_timeline_from_initialising_to_ready():
    secs = [t for t, *_ in S.TIMELINE]
    pcts = [p for _, p, *_ in S.TIMELINE]
    assert secs == sorted(secs) and pcts == sorted(pcts) and pcts[-1] == 100
    statuses = [s for _, _, s, _ in S.TIMELINE]
    for words in ("Initialising RF Optimization", "Loading Network Data",
                  "Processing Network Data", "Preparing Map Services",
                  "Preparing Analysis Engine", "Almost Ready"):
        assert words in statuses, words
    assert [k for *_, k in S.TIMELINE] == sorted(k for *_, k in S.TIMELINE)
    assert S.TIMELINE[-1][0] < S.READY_AT < S.LEAVE_AT <= 12


def test_the_layer_carries_every_element_over_one_background():
    html = S.markup()
    assert html.count('class="rfs-bg"') == 1 and html.count("<img") == 1
    assert html.count('class="stp"') == 4 and html.count('class="cn"') == len(S.CITIES)
    assert html.count('class="st"') == len(S.LINKS) and html.count('class="tw"') == 4
    for label, _ in S.STEPS:
        assert label in html
    assert "Ready" in html and "Launching RF Optimization Platform" in html


def _script(at) -> str:
    bodies = [e.proto.body for e in at.get("html") if "atob(" in e.proto.body]
    assert len(bodies) <= 1
    return bodies[0] if bodies else ""


def test_the_app_adds_the_startup_screen_once_per_session():
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    at = AppTest.from_file(str(APP / "Home.py"), default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    body = _script(at)
    assert body
    # st.html drops a script whose text holds markup: the loader holds none
    loader = body[len("<script>"):-len("</script>")]
    assert "<" not in loader and ">" not in loader.replace("=>", "")
    js = base64.b64decode(re.search(r"atob\('([A-Za-z0-9+/=]+)'\)", body).group(1)).decode()
    assert "rf_startup_done" in js and "sessionStorage" in js
    assert S.BG_URL in js and "Preparing Analysis Engine" in js
    at.run()                                        # a rerun: not added again
    assert not at.exception, at.exception
    assert _script(at) == ""
