"""The maps load Leaflet from the app itself and keep their basemap drawn
while tiles arrive (slow VPN, zooming)."""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


@pytest.fixture(autouse=True)
def _app_on_path():
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))


def test_the_map_loads_only_leaflet_and_from_the_app(monkeypatch):
    import folium
    from folium.plugins import Draw

    import _map_assets as A

    for f in A._FILES.values():
        assert (A.VENDOR / f).stat().st_size > 1000
    monkeypatch.setattr(A, "served_locally", lambda: True)
    m = folium.Map(location=[30.5, 47.8], zoom_start=12, tiles=None)
    draw = Draw().add_to(m)
    A.use_local_libraries(m, draw)
    A.add_basemap(m, "https://server.arcgisonline.com/x/{z}/{y}/{x}", attr="Esri", max_zoom=16)
    m.add_child(A.TileGuard())
    m.add_child(A.MousePositionControl())
    doc = m.get_root().render()
    links = re.findall(r'(?:src|href)="([^"]+\.(?:js|css))"', doc)
    assert sorted(links) == sorted(["/app/static/vendor/leaflet/leaflet.js",
                                    "/app/static/vendor/leaflet/leaflet.css",
                                    "/app/static/vendor/leaflet.draw/leaflet.draw.js",
                                    "/app/static/vendor/leaflet.draw/leaflet.draw.css"])
    for cdn in ("jquery", "bootstrap", "fontawesome", "awesome-markers", "MousePosition.min"):
        assert cdn not in doc
    # a soft copy underneath, tiles once a zoom ends, retries on a failed tile
    assert doc.count('"updateWhenZooming": false') == 2
    assert '"maxNativeZoom": 10' in doc and '"maxNativeZoom": 16' in doc
    assert "tileerror" in doc and "Loading basemap" in doc

    # without static serving the same two libraries come from their CDN
    monkeypatch.setattr(A, "served_locally", lambda: False)
    assert A.library_urls()["leaflet_js"].startswith("https://cdn.jsdelivr.net/")


def test_the_app_serves_its_static_folder_and_the_sites_map_uses_it():
    config = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert re.search(r"^enableStaticServing\s*=\s*true", config, re.M)
    page = (APP / "views" / "site_map.py").read_text(encoding="utf-8")
    assert "_use_local_libraries(fmap, _draw)" in page and "_TileGuard()" in page
    assert "folium.Icon(" not in page and not re.search(r"(?<![\w])MousePosition\(", page)
    # opening a sector does not rebuild the map: the drawer rides in the map
    # without data, and the selected site's data is published beside it
    assert "_SectorDrawer(None)" in page and 'id="rf-drawer-payload"' in page
    assert ".st-key-sm_drawer_payload { display: none !important; }" in page
    drawer = (APP / "assets" / "sector_drawer.js").read_text(encoding="utf-8")
    assert "getElementById('rf-drawer-payload')" in drawer and "setInterval(poll" in drawer
    # today's worklist is gone from the Sites page and its drawer
    assert "worklist" not in page.lower()
    assert "worklist" not in (APP / "assets" / "sector_drawer.js").read_text(
        encoding="utf-8").lower()
