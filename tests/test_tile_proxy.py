"""The local tile relay the maps fall back to when the VPN blocks the tile hosts."""

import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import _tile_proxy as TP  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"tile"


def test_only_the_maps_own_tile_services_are_relayed():
    esri = ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/"
            "tile/{z}/{y}/{x}")
    assert TP.upstreams(esri) == [
        esri, esri.replace("server.arcgisonline.com", "services.arcgisonline.com")]
    assert TP.upstreams("OpenStreetMap") == ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"]
    assert TP.upstreams("Esri.WorldImagery")[0] == esri
    assert TP.upstreams("https://evil.example.com/{z}/{x}/{y}.png") == []
    assert TP.relay_url("https://evil.example.com/{z}/{x}/{y}.png") is None


@pytest.fixture
def upstream():
    hits = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            if self.path == "/7/40/80.png":
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.end_headers()
                self.wfile.write(PNG)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", hits
    srv.shutdown()


def test_a_tile_is_relayed_cached_and_a_missing_one_is_404(tmp_path, monkeypatch, upstream):
    monkeypatch.setenv("RFOPT_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    base, hits = upstream
    # a relay route to the fake upstream; the first upstream is down
    monkeypatch.setitem(TP._routes, "0123456789ab",
                        ["http://127.0.0.1:9/{z}/{x}/{y}.png", base + "/{z}/{x}/{y}.png"])
    srv = TP._start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/t/0123456789ab"
    no_proxy = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with no_proxy.open(f"{url}/7/80/40", timeout=10) as r:
        assert r.status == 200 and r.read() == PNG
        assert r.headers["Content-Type"] == "image/png"
        assert r.headers["Access-Control-Allow-Origin"] == "*"
    assert hits == ["/7/40/80.png"]
    assert (tmp_path / "resources" / "tiles" / "0123456789ab" / "7" / "80" / "40.tile").read_bytes() == PNG
    with no_proxy.open(f"{url}/7/80/40", timeout=10) as r:      # from the disk cache
        assert r.read() == PNG
    assert hits == ["/7/40/80.png"]
    for bad in (f"{url}/7/81/40", f"http://127.0.0.1:{srv.server_address[1]}/t/ffffffffffff/1/1/1",
                f"http://127.0.0.1:{srv.server_address[1]}/etc/passwd"):
        with pytest.raises(urllib.error.HTTPError) as e:
            no_proxy.open(bad, timeout=10)
        assert e.value.code == 404


def test_the_maps_carry_the_relay_and_move_a_failing_layer_to_it():
    import folium
    from _map_assets import TileGuard, add_basemap, add_overlay

    m = folium.Map(tiles=None)
    add_basemap(m, "OpenStreetMap")
    add_overlay(m, "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/"
                   "World_Transportation/MapServer/tile/{z}/{y}/{x}", name="roads")
    m.add_child(TileGuard())
    html = m.get_root().render()
    assert html.count('"fallback": "http://127.0.0.1:') == 3
    assert "layer.setUrl(fb)" in html and "FAILS = 4" in html
