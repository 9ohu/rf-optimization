"""The Sites map's offline basemap: local map packs, served by the app's relay,
and the one explicit update that fetches them."""

import gzip
import http.server
import io
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from rfopt.geo import offline_basemap as OB

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

pmtiles = pytest.importorskip("pmtiles")


@pytest.fixture
def packs_dir(tmp_path, monkeypatch):
    d = tmp_path / "basemap"
    d.mkdir()
    monkeypatch.setenv("RFOPT_BASEMAP_DIR", str(d))
    return d


def _png(seed: int) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (seed % 256, 10, 20)).save(buf, "PNG")
    return buf.getvalue()


class _Tiles(http.server.BaseHTTPRequestHandler):
    """A tile server on 127.0.0.1: /{z}/{y}/{x}.png, 404 outside `have`."""
    have: set = set()
    hits: list = []

    def do_GET(self):  # noqa: N802
        parts = self.path.strip("/").split("/")
        try:
            z, y, x = int(parts[0]), int(parts[1]), int(parts[2].split(".")[0])
        except (ValueError, IndexError):
            z = y = x = -1
        type(self).hits.append((z, x, y))
        if (z, x, y) not in self.have and self.have:
            self.send_response(404)
            self.end_headers()
            return
        data = _png(z * 1000 + x + y)
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def tile_server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Tiles)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Tiles.have, _Tiles.hits = set(), []
    yield f"http://127.0.0.1:{srv.server_address[1]}/{{z}}/{{y}}/{{x}}.png"
    srv.shutdown()


BOX = (47.70, 30.40, 47.90, 30.60)


# --------------------------------------------------------------------------- #
# tile arithmetic
# --------------------------------------------------------------------------- #
def test_the_tiles_of_a_box_and_the_sites_region():
    assert OB.lonlat_to_tile(0, 0, 1) == (1, 1)
    assert OB.lonlat_to_tile(47.8, 30.5, 8) == (161, 105)
    assert set(OB.tiles_in(BOX, 8)) == {(161, 105), (162, 105)}      # it straddles x
    todo = OB.plan([(0, 3, OB.IRAQ), (4, 6, BOX)])
    assert todo == sorted(set(todo)) and (0, 0, 0) in todo
    assert all(z <= 6 for z, _, _ in todo)
    # the sites' extent with its margin; no position -> R5
    w, s, e, n = OB.region_bbox([30.5, 30.6], [47.8, 47.9], margin_km=10)
    assert w < 47.8 and e > 47.9 and s < 30.5 and n > 30.6 and n - 30.6 == pytest.approx(0.0898, abs=1e-3)
    assert OB.region_bbox([], []) == OB.R5
    assert OB.region_bbox([float("nan")], [47.8]) == OB.R5


# --------------------------------------------------------------------------- #
# packs
# --------------------------------------------------------------------------- #
def test_a_raster_pack_is_built_from_a_tile_server_and_read_back(packs_dir, tile_server):
    bands = [(0, 2, OB.IRAQ), (3, 6, BOX)]
    out = packs_dir / "night.pmtiles"
    n = OB.build_raster_pack(tile_server, bands, out, attribution="test", name="night")
    assert n == len(OB.plan(bands)) and out.is_file()
    assert not list(packs_dir.glob("*.part")) and not list(packs_dir.glob("rf_pack_*"))
    head = OB.read_header(out)
    assert head["tile_type"] == "png" and (head["min_zoom"], head["max_zoom"]) == (0, 6)
    info = OB.status()["night"]
    assert info is not None and info["tiles"] == n
    assert OB.status()["vector"] is None and not OB.installed("vector")
    # every tile as served
    from pmtiles.reader import MmapSource, Reader
    with open(out, "rb") as f:
        r = Reader(MmapSource(f))
        assert r.get(3, 5, 3) == _png(3 * 1000 + 5 + 3)


def test_a_region_is_cut_out_of_a_larger_archive(packs_dir):
    from pmtiles.tile import Compression, TileType, zxy_to_tileid
    from pmtiles.writer import Writer

    src = packs_dir / "planet.pmtiles"
    big = OB.plan([(0, 9, OB.IRAQ)])
    with open(src, "wb") as f:
        w = Writer(f)
        for z, x, y in sorted(big, key=lambda t: zxy_to_tileid(*t)):
            w.write_tile(zxy_to_tileid(z, x, y), gzip.compress(f"{z}/{x}/{y}".encode(), mtime=0))
        w.finalize({"tile_type": TileType.MVT, "tile_compression": Compression.GZIP},
                   {"vector_layers": [{"id": "roads"}]})
    bands = [(0, 4, OB.IRAQ), (5, 9, BOX)]
    out = packs_dir / "region.pmtiles"
    n = OB.extract_region(OB.file_reader(src), bands, out, gap=0, batch=1 << 12)
    want = OB.plan(bands)
    assert n == len(want)
    from pmtiles.reader import MmapSource, Reader, all_tiles
    with open(out, "rb") as f:
        got = {zxy: gzip.decompress(d).decode() for zxy, d in all_tiles(MmapSource(f))}
        meta = Reader(MmapSource(f)).metadata()
    assert set(got) == set(want)
    assert all(v == f"{z}/{x}/{y}" for (z, x, y), v in got.items())
    assert OB.read_header(out)["tile_type"] == "mvt"
    assert meta["vector_layers"] == [{"id": "roads"}] and "OpenStreetMap" in meta["attribution"]


def test_the_update_keeps_the_old_pack_when_a_source_fails(packs_dir, tile_server,
                                                           monkeypatch):
    monkeypatch.setitem(OB.RASTER_SOURCES, "night", [tile_server])
    monkeypatch.setitem(OB.RASTER_SOURCES, "earth", ["http://127.0.0.1:9/{z}/{y}/{x}.jpg"])
    monkeypatch.setattr(OB, "bands_for", lambda key, region, **k: [(0, 3, OB.IRAQ)])
    old = packs_dir / "earth.pmtiles"
    old.write_bytes(b"keep me")
    res = OB.update(("night", "earth"), log=lambda m: None)
    assert res["night"].startswith("ok") and OB.installed("night")
    assert res["earth"].startswith("failed") and old.read_bytes() == b"keep me"
    assert not list(packs_dir.glob("*.part"))


# --------------------------------------------------------------------------- #
# the relay serves the packs, with byte ranges, and nothing else
# --------------------------------------------------------------------------- #
def test_the_relay_serves_a_pack_by_byte_range(packs_dir):
    import _tile_proxy as TP
    data = bytes(range(256)) * 40
    (packs_dir / "region.pmtiles").write_bytes(data)
    base = TP.packs_url()
    assert base.startswith("http://127.0.0.1:") and base.endswith("/pm/")

    def get(name, rng=None):
        req = urllib.request.Request(base + name, headers={"Range": rng} if rng else {})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.headers, r.read()

    status, headers, body = get("region.pmtiles", "bytes=100-227")
    assert status == 206 and body == data[100:228]
    assert headers["Content-Range"] == f"bytes 100-227/{len(data)}"
    assert headers["Access-Control-Allow-Origin"] == "*"
    status, _, body = get("region.pmtiles", "bytes=-10")
    assert status == 206 and body == data[-10:]
    status, _, body = get("region.pmtiles")
    assert status == 200 and body == data
    for bad in ("other.pmtiles", "night.pmtiles", "..%2Fsecret.pmtiles", "region.pmtiles.bak"):
        with pytest.raises(urllib.error.HTTPError) as e:
            get(bad)
        assert e.value.code == 404


# --------------------------------------------------------------------------- #
# the Sites map asks no tile server
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", ["Dark", "Streets", "Satellite", "Coverage",
                                  "Night Satellite"])
def test_every_basemap_mode_is_drawn_from_the_local_packs(packs_dir, mode):
    import folium

    import _map_assets as MA
    (packs_dir / "region.pmtiles").write_bytes(b"x")          # not a pack: not installed
    fmap = folium.Map(tiles=None)
    MA.use_local_libraries(fmap)
    cfg = MA.add_offline_basemap(fmap, mode)
    html = fmap.get_root().render()
    assert f'"mode": "{mode}"' in html and "rfBasemap(" in html
    assert cfg["base"].startswith("http://127.0.0.1:")
    assert set(cfg["packs"]) == {"vector", "night", "earth", "imagery"}
    assert not any(p["installed"] for p in cfg["packs"].values())
    for host in ("arcgisonline", "openstreetmap.org/", "tile.", "gibs.earthdata",
                 "tiles.maps.eox"):
        assert host not in html, host


def test_the_sites_map_has_five_modes_and_no_online_tiles():
    src = (APP / "views" / "site_map.py").read_text(encoding="utf-8")
    for host in ("arcgisonline", "openstreetmap.org", "_add_basemap", "_TileGuard"):
        assert host not in src, host
    for mode in ('"Dark"', '"Streets"', '"Satellite"', '"Coverage"', '"Night Satellite"'):
        assert mode + ": {" in src, mode
    js = (APP / "static" / "vendor" / "protomaps" / "rf-basemap.js").read_text(encoding="utf-8")
    assert "https://" not in js and "http://" not in js       # no URL of its own
    assert "fetch(" not in js and "XMLHttpRequest" not in js
    for f in ("pmtiles.js", "protomaps-leaflet.js", "rf-basemap.js"):
        assert (APP / "static" / "vendor" / "protomaps" / f).is_file()
