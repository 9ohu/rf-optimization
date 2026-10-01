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
    """A tile server on 127.0.0.1: /{z}/{y}/{x}.png, 404 outside `have`, 500 on
    `broken`, one same picture from zoom `blank_from` on."""
    have: set = set()
    hits: list = []
    broken: set = set()
    blank_from = 99

    def do_GET(self):  # noqa: N802
        parts = self.path.strip("/").split("/")
        try:
            z, y, x = int(parts[0]), int(parts[1]), int(parts[2].split(".")[0])
        except (ValueError, IndexError):
            z = y = x = -1
        type(self).hits.append((z, x, y))
        if (z, x, y) in self.broken:
            self.send_response(500)
            self.end_headers()
            return
        if (z, x, y) not in self.have and self.have:
            self.send_response(404)
            self.end_headers()
            return
        data = _png(7) if z >= self.blank_from else _png(z * 1000 + x + y)
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
    _Tiles.have, _Tiles.hits, _Tiles.broken, _Tiles.blank_from = set(), [], set(), 99
    yield f"http://127.0.0.1:{srv.server_address[1]}/{{z}}/{{y}}/{{x}}.png"
    srv.shutdown()


BOX = (47.70, 30.40, 47.90, 30.60)
SITE_A, SITE_B = (30.50, 47.80), (30.56, 47.86)        # (lat, lon), Basra


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(OB.time, "sleep", lambda s: None)


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


def test_the_building_scale_tiles_ring_each_site():
    # 0.5 km at zoom 18, doubled at each zoom out, capped
    assert OB.detail_rings() == [(14, 8.0), (15, 4.0), (16, 2.0), (17, 1.0), (18, 0.5)]
    assert OB.detail_rings(19, 0.5)[-1] == (19, 0.25)
    assert OB.detail_rings(18, 2.0)[0] == (14, OB.DETAIL_RADIUS_CAP_KM)
    rings = OB.detail_rings()
    one = OB.site_tiles([SITE_A], rings)
    assert one == sorted(set(one))
    for z, _ in rings:                                  # the site's own tile, every zoom
        assert (z, *OB.lonlat_to_tile(SITE_A[1], SITE_A[0], z)) in one
    assert {z for z, _, _ in one} == {14, 15, 16, 17, 18}
    # the z18 ring: tiles of ~132 m within 0.5 km — a disc, not its square
    z18 = [t for t in one if t[0] == 18]
    xs = [t[1] for t in z18]
    side = max(xs) - min(xs) + 1
    assert 7 <= side <= 10 and len(z18) < side * side
    # about the same count per zoom, and no tile far from the site
    per = [sum(1 for t in one if t[0] == z) for z, _ in rings]
    assert max(per) <= 2 * min(per)
    # a second site nearby shares tiles; the estimate is the plan
    two = OB.site_tiles([SITE_A, SITE_B], rings)
    assert len(one) < len(two) < 2 * len(one)
    n, mb = OB.detail_estimate([SITE_A, SITE_B])
    assert n == len(two) and mb == pytest.approx(n * OB.DETAIL_TILE_KB / 1000)
    assert OB.detail_estimate([]) == (0, 0.0)
    w, s_, e, n_ = OB.rings_bbox([SITE_A], rings)
    assert w < SITE_A[1] < e and s_ < SITE_A[0] < n_
    assert OB.site_points([30.5, None, "x", 10.0], [47.8, 47.8, 47.8, 47.8]) == [(30.5, 47.8)]


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
# the building-scale pack (Esri World Imagery with the user's ArcGIS key)
# --------------------------------------------------------------------------- #
def test_the_esri_imagery_needs_the_users_key(packs_dir, tmp_path, monkeypatch):
    for v in ("RFOPT_ARCGIS_KEY", "ARCGIS_API_KEY", "RFOPT_DETAIL_URL"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("RFOPT_CACHE_DIR", str(tmp_path / "cache"))
    assert OB.arcgis_key() == ""
    with pytest.raises(OB.UpdateError, match="ArcGIS API key"):
        OB.detail_sources()
    res = OB.update(("detail",), sites=[SITE_A], log=lambda m: None)
    assert res["detail"].startswith("failed") and "ArcGIS API key" in res["detail"]
    # Esri's offline service first, a missing tile answers 404 (blankTile=false)
    urls, sid = OB.detail_sources("ab c/d")
    assert sid == OB.ESRI_DETAIL_ID and len(urls) == 3
    assert urls[0].startswith("https://tiledbasemaps.arcgis.com/arcgis/rest/services/"
                              "World_Imagery/MapServer/tile/{z}/{y}/{x}?blankTile=false")
    assert all(u.endswith("token=ab%20c%2Fd") for u in urls)
    # the key is saved beside the packs (never in them); the environment wins
    OB.save_arcgis_key("  saved-key ")
    assert OB.arcgis_key() == "saved-key"
    assert not (packs_dir / "arcgis_api_key.txt").exists()
    assert OB.detail_sources()[0][0].endswith("token=saved-key")
    monkeypatch.setenv("RFOPT_ARCGIS_KEY", "env-key")
    assert OB.arcgis_key() == "env-key"
    OB.save_arcgis_key("")
    monkeypatch.delenv("RFOPT_ARCGIS_KEY")
    assert OB.arcgis_key() == ""
    # another licensed service instead of Esri
    monkeypatch.setenv("RFOPT_DETAIL_URL", "https://img.example/{z}/{x}/{y}.jpg?key=S")
    assert OB.detail_sources() == (["https://img.example/{z}/{x}/{y}.jpg?key=S"],
                                   "https://img.example/{z}/{x}/{y}.jpg?key=***")


def test_a_key_never_reaches_a_message_or_the_pack(packs_dir, tile_server, no_wait):
    secret = tile_server + "?blankTile=false&token=S3CRET"
    assert "S3CRET" not in OB.redact(secret) and OB.redact(secret).endswith("token=***")
    with pytest.raises(ConnectionError) as e:
        OB.http_get("http://127.0.0.1:9/0/0/0.png?token=S3CRET", tries=1)
    assert "S3CRET" not in str(e.value)
    res = OB.update(("detail",), sites=[SITE_A], detail_max=15, detail_url=secret,
                    log=lambda m: None)
    assert res["detail"].startswith("ok"), res
    from pmtiles.reader import MmapSource, Reader
    with open(packs_dir / "detail.pmtiles", "rb") as f:
        meta = Reader(MmapSource(f)).metadata()
    assert "S3CRET" not in str(meta) and meta["source"].endswith("token=***")
    assert "Esri" in meta["attribution"]
    assert b"S3CRET" not in (packs_dir / "detail.pmtiles").read_bytes()


def test_the_building_scale_pack_grows_and_resumes(packs_dir, tile_server, no_wait):
    rings = OB.detail_rings()
    want_a = OB.site_tiles([SITE_A], rings)
    res = OB.update(("detail",), sites=[SITE_A], detail_url=tile_server,
                    log=lambda m: None)
    assert res["detail"].startswith("ok") and f"{len(want_a):,} new" in res["detail"]
    head = OB.read_header(packs_dir / "detail.pmtiles")
    assert (head["min_zoom"], head["max_zoom"]) == (14, 18) and head["tiles"] == len(want_a)
    assert head["tile_type"] == "png"
    # the same sites again: nothing is fetched but the probe
    _Tiles.hits = []
    res = OB.update(("detail",), sites=[SITE_A], detail_url=tile_server, log=lambda m: None)
    assert res["detail"].startswith("ok") and "0 new" in res["detail"]
    assert len(_Tiles.hits) == 1
    # another area adds only its own tiles; the first area stays
    want_b = set(OB.site_tiles([SITE_B], rings)) - set(want_a)
    _Tiles.hits = []
    res = OB.update(("detail",), sites=[SITE_B], detail_url=tile_server, log=lambda m: None)
    assert res["detail"].startswith("ok")
    assert set(_Tiles.hits[1:]) == want_b
    from pmtiles.reader import MmapSource, all_tiles
    with open(packs_dir / "detail.pmtiles", "rb") as f:
        got = dict(all_tiles(MmapSource(f)))
    assert set(got) == set(want_a) | want_b
    z, x, y = want_a[-1]
    assert got[(z, x, y)] == _png(z * 1000 + x + y)
    # a tile that keeps failing is left out, the rest kept, and the next
    # update fetches only it
    fresh_site = (30.70, 47.60)
    want_c = set(OB.site_tiles([fresh_site], rings)) - set(got)
    bad = sorted(want_c)[len(want_c) // 2]
    _Tiles.broken = {bad}
    res = OB.update(("detail",), sites=[fresh_site], detail_url=tile_server,
                    log=lambda m: None)
    assert res["detail"].startswith("incomplete") and "1 tiles could not" in res["detail"]
    assert OB.read_header(packs_dir / "detail.pmtiles")["tiles"] == len(got) + len(want_c) - 1
    _Tiles.broken, _Tiles.hits = set(), []
    res = OB.update(("detail",), sites=[fresh_site], detail_url=tile_server,
                    log=lambda m: None)
    assert res["detail"].startswith("ok") and set(_Tiles.hits[1:]) == {bad}
    # start over: only the sites asked for now
    res = OB.update(("detail",), sites=[SITE_A], detail_url=tile_server,
                    detail_fresh=True, log=lambda m: None)
    assert OB.read_header(packs_dir / "detail.pmtiles")["tiles"] == len(want_a)
    assert not list(packs_dir.glob("*.part")) and not list(packs_dir.glob("rf_pack_*"))


def test_a_download_cut_short_carries_on_where_it_stopped(packs_dir, tile_server, no_wait):
    from pmtiles.tile import zxy_to_tileid
    want = OB.site_tiles([SITE_A], OB.detail_rings(16))
    # the app was closed mid-download: a third of the tiles wait in the stash
    stash = packs_dir / "detail.download"
    stash.mkdir()
    (stash / "source.txt").write_text(OB.redact(tile_server))
    early = want[: len(want) // 3]
    for z, x, y in early:
        (stash / str(zxy_to_tileid(z, x, y))).write_bytes(_png(z * 1000 + x + y))
    (stash / "123.part").write_bytes(b"half a til")            # never taken
    res = OB.update(("detail",), sites=[SITE_A], detail_max=16, detail_url=tile_server,
                    log=lambda m: None)
    assert res["detail"].startswith("ok")
    assert not set(_Tiles.hits[1:]) & set(early)                # not fetched again
    assert set(_Tiles.hits[1:]) == set(want) - set(early)
    assert OB.read_header(packs_dir / "detail.pmtiles")["tiles"] == len(want)
    assert not stash.exists()                                   # done: the stash goes
    # a stash from another source is not mixed in
    stash.mkdir()
    (stash / "source.txt").write_text("another source")
    (stash / str(zxy_to_tileid(*want[0]))).write_bytes(_png(1))
    OB.update(("detail",), sites=[SITE_A], detail_max=16, detail_url=tile_server,
              detail_fresh=True, log=lambda m: None)
    from pmtiles.reader import MmapSource, Reader
    with open(packs_dir / "detail.pmtiles", "rb") as f:
        z, x, y = want[0]
        assert Reader(MmapSource(f)).get(z, x, y) == _png(z * 1000 + x + y)


def test_a_source_that_dies_stops_the_fetch_and_keeps_what_came(packs_dir, tile_server,
                                                                no_wait):
    want = OB.site_tiles([SITE_A], OB.detail_rings())
    z0 = [t for t in want if t[0] == 14]
    _Tiles.broken = set(want) - set(z0)          # every tile past the probe zoom fails
    res = OB.update(("detail",), sites=[SITE_A], detail_url=tile_server, log=lambda m: None)
    assert res["detail"].startswith("incomplete") and "press Update again" in res["detail"]
    tries = [h for h in _Tiles.hits[1:] if h in _Tiles.broken]
    assert len(set(tries)) < len(_Tiles.broken) / 2            # it stopped, not ground on
    assert OB.read_header(packs_dir / "detail.pmtiles")["tiles"] == len(z0)


def test_the_new_pack_waits_for_the_map_to_let_go_of_the_old(tmp_path, monkeypatch):
    part, out = tmp_path / "x.part", tmp_path / "x.pmtiles"
    part.write_bytes(b"new")
    out.write_bytes(b"old")
    calls = []
    real = Path.replace

    def busy(self, target):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("in use")          # Windows: the relay has it open
        return real(self, target)

    monkeypatch.setattr(Path, "replace", busy)
    monkeypatch.setattr(OB.time, "sleep", lambda s: None)
    OB._swap(part, out)
    assert out.read_bytes() == b"new" and len(calls) == 3


def test_a_no_imagery_placeholder_is_left_out(packs_dir, tile_server):
    _Tiles.blank_from = 17          # one grey picture for every street tile past z16
    res = OB.update(("detail",), sites=[SITE_A], detail_url=tile_server, log=lambda m: None)
    assert res["detail"].startswith("ok")
    head = OB.read_header(packs_dir / "detail.pmtiles")
    assert head["max_zoom"] == 16           # the map draws zoom 16 larger there instead


def test_the_map_is_told_the_zooms_each_pack_holds(packs_dir, tile_server):
    import _map_assets as MA
    OB.build_raster_pack(tile_server, [(14, 17, BOX)], packs_dir / "detail.pmtiles",
                         attribution="t", name="d",
                         todo=OB.site_tiles([SITE_A], OB.detail_rings(17)))
    cfg = MA.offline_basemap_config("Satellite")
    assert cfg["packs"]["detail"]["installed"]
    assert (cfg["packs"]["detail"]["min_zoom"], cfg["packs"]["detail"]["native_max"]) == (14, 17)
    assert not cfg["packs"]["imagery"]["installed"]
    assert cfg["packs"]["imagery"]["native_max"] == OB.PACKS["imagery"].native_max == 13


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
    assert set(cfg["packs"]) == {"vector", "night", "earth", "imagery", "detail"}
    assert not any(p["installed"] for p in cfg["packs"].values())
    for host in ("arcgisonline", "arcgis.com", "openstreetmap.org/", "tile.",
                 "gibs.earthdata", "tiles.maps.eox", "token="):
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


def test_satellite_and_night_satellite_draw_the_building_scale_imagery():
    js = (APP / "static" / "vendor" / "protomaps" / "rf-basemap.js").read_text(encoding="utf-8")
    night = js[js.index('mode === "Night Satellite"'):js.index("// Satellite, Coverage")]
    day = js[js.index("// Satellite, Coverage"):js.index("if (missing.length)")]
    # Satellite: the building-scale pack over the regional one; Blue Marble
    # only at country scale. Coverage keeps its imagery as it was.
    assert 'sat && has("detail")' in day and 'raster("detail"' in day
    assert "maxZoom: 12" in day and 'var sat = mode === "Satellite"' in day
    # Night: the same imagery graded to night (a colour grade, no sharpening),
    # the city lights screened over it, the glowing streets on top
    assert 'raster("detail"' in night and 'raster("imagery"' in night
    assert "maxZoom: packs.imagery.native_max" in night     # never stretched
    assert night.count('className: "rf-night-sat"') == 2
    assert "rf-night-glow" in night and "rf-night-roads" in night
    assert ".rf-night-glow{mix-blend-mode:screen}" in js
    grade = js[js.index(".rf-night-sat{"):js.index("}", js.index(".rf-night-sat{"))]
    assert "brightness(" in grade and "blur" not in grade and "url(" not in grade


def test_the_offline_map_box_asks_for_the_key_and_estimates_the_download(
        tmp_path, monkeypatch, put_resource):
    for v in ("RFOPT_ARCGIS_KEY", "ARCGIS_API_KEY", "RFOPT_DETAIL_URL", "RFOPT_BASEMAP_DIR"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("RFOPT_CACHE_DIR", str(tmp_path))
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    from test_site_map_ticket import _kmz
    put_resource("kmz", "R5_Sites.kmz",
                 _kmz([("NAS0002", (31.05, 46.26), [(1, 0), (2, 120)])]), "Site KMZ")
    at = AppTest.from_file(str(APP / "views/site_map.py"), default_timeout=300)
    at.run()
    assert not at.exception, at.exception
    assert not [w for w in at.text_input if w.key == "sm_off_key"]     # only once ticked
    at.checkbox(key="sm_off_det").check().run()
    assert not at.exception, at.exception
    key = at.text_input(key="sm_off_key")
    assert key.value == "" and key.proto.type == key.proto.PASSWORD
    areas = at.multiselect(key="sm_off_areas")
    assert sorted(areas.value) == ["BAS", "NAS"]
    captions = " ".join(c.value for c in at.caption)
    assert "Around 2 sites" in captions and "tiles" in captions and "GB" in captions
    assert at.button(key="sm_off_go").disabled                        # no key yet
    assert any("ArcGIS API key" in w.value for w in at.warning)
    # a key: kept on this PC (beside the packs), the update can start
    key.input("my-key").run()
    assert not at.button(key="sm_off_go").disabled
    assert (tmp_path / "resources" / "arcgis_api_key.txt").read_text() == "my-key"
    at.multiselect(key="sm_off_areas").unselect("NAS").run()
    assert "Around 1 sites" in " ".join(c.value for c in at.caption)
