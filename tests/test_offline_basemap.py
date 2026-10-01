"""The Sites map's offline basemap: local map packs, served by the app's relay,
and the one explicit update that fetches them."""

import gzip
import http.server
import io
import math
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
CITIES = {"BAS": (30.508, 47.783), "NAS": (31.044, 46.257),        # (lat, lon)
          "SAM": (31.318, 45.281), "EMA": (31.836, 47.144)}


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(OB.time, "sleep", lambda s: None)


def _square(lat, lon, km):
    d, e = km / 111.32 / 2, km / (111.32 * 0.8616) / 2
    return ((lon - e, lat - d), (lon + e, lat - d), (lon + e, lat + d), (lon - e, lat + d),
            (lon - e, lat - d))


@pytest.fixture
def small_region(monkeypatch):
    """"BAS" is a 1.5 km square in Basra city: a region of a few hundred tiles."""
    reg = OB.Region("BAS", "Basrah", "Al-Basrah", (_square(30.508, 47.783, 1.5),))
    OB.region_rows.cache_clear()
    monkeypatch.setattr(OB, "regions", lambda: {"BAS": reg})
    yield reg
    OB.region_rows.cache_clear()


def _inside(lat, lon, rings):
    """Even-odd point in polygon (lon / lat)."""
    hit = False
    for ring in rings:
        for (x0, y0), (x1, y1) in zip(ring, ring[1:]):
            if (y0 > lat) != (y1 > lat) and lon < x0 + (lat - y0) * (x1 - x0) / (y1 - y0):
                hit = not hit
    return hit


def _tiles_of(code, zmax=None):
    zmax = zmax or OB.DETAIL_MAX
    return list(OB.region_tiles(code, OB.DETAIL_MIN, zmax))


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


def test_the_four_regions_are_the_governorates():
    regs = OB.regions()
    assert list(regs) == ["BAS", "NAS", "SAM", "EMA"]
    assert {c: (r.name, r.governorate) for c, r in regs.items()} == {
        "BAS": ("Basrah", "Al-Basrah"), "NAS": ("Nasiriyah", "Dhi Qar"),
        "SAM": ("Samawah", "Al-Muthanna"), "EMA": ("Amarah", "Maysan")}
    official_km2 = {"BAS": 19070, "NAS": 12900, "SAM": 51740, "EMA": 16072}
    for c, r in regs.items():
        ring = r.rings[0]
        a = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:])) / 2
        km2 = abs(a) * 111.32 ** 2 * math.cos(math.radians((r.bbox[1] + r.bbox[3]) / 2))
        assert km2 == pytest.approx(official_km2[c], rel=0.15), c
        # each governorate's city is inside its own boundary, no other
        for other, (lat, lon) in CITIES.items():
            assert _inside(lat, lon, r.rings) == (other == c), (c, other)
        assert not _inside(29.376, 47.977, r.rings)              # Kuwait City
        assert not _inside(33.315, 44.366, r.rings)              # Baghdad
    # neighbours share their border: no gap between two picked regions
    ba, dq = set(regs["BAS"].rings[0]), set(regs["NAS"].rings[0])
    assert len(ba & dq) >= 10


def _brute(rings, z):
    """Every zoom-z tile the polygon touches, tested one by one."""
    n = 2 ** z
    pts = [[OB._frac_tile(lon, lat, n) for lon, lat in ring] for ring in rings]
    xs = [p[0] for r in pts for p in r]
    ys = [p[1] for r in pts for p in r]

    def crosses(a, b, x, y):          # the segment a-b meets the square [x, x+1] x [y, y+1]
        (ax, ay), (bx, by) = a, b
        t0, t1 = 0.0, 1.0
        for p, q in ((-(bx - ax), ax - x), (bx - ax, x + 1 - ax),
                     (-(by - ay), ay - y), (by - ay, y + 1 - ay)):
            if p == 0:
                if q < 0:
                    return False
            else:
                r = q / p
                if p < 0:
                    t0 = max(t0, r)
                else:
                    t1 = min(t1, r)
        return t0 <= t1

    out = set()
    for x in range(int(min(xs)), int(max(xs)) + 1):
        for y in range(int(min(ys)), int(max(ys)) + 1):
            cx, cy = x + 0.5, y + 0.5
            inside = False
            for r in pts:
                for (ax, ay), (bx, by) in zip(r, r[1:]):
                    if (ay > cy) != (by > cy) and cx < ax + (cy - ay) * (bx - ax) / (by - ay):
                        inside = not inside
            if inside or any(crosses(a, b, x, y) for r in pts for a, b in zip(r, r[1:])):
                out.add((x, y))
    return out


@pytest.mark.parametrize("code,z", [("BAS", 11), ("SAM", 10), ("EMA", 11), ("NAS", 12)])
def test_a_region_holds_every_tile_that_touches_its_boundary_and_no_other(code, z):
    got = {(x, y) for y, x0, x1 in OB.region_rows(code, z) for x in range(x0, x1 + 1)}
    assert got == _brute(OB.regions()[code].rings, z)


def test_the_whole_region_is_planned_sites_or_not():
    # building zooms everywhere in the boundary: the cities, and open desert
    # far from any site; nothing across the border
    for code, (lat, lon) in CITIES.items():
        x, y = OB.lonlat_to_tile(lon, lat, 18)
        assert any(r[0] == y and r[1] <= x <= r[2] for r in OB.region_rows(code, 18)), code
    x, y = OB.lonlat_to_tile(45.0, 30.2, 18)                     # Al-Muthanna desert
    assert any(r[0] == y and r[1] <= x <= r[2] for r in OB.region_rows("SAM", 18))
    x, y = OB.lonlat_to_tile(47.977, 29.376, 16)                 # Kuwait City
    assert not any(r[0] == y and r[1] <= x <= r[2] for r in OB.region_rows("BAS", 16))
    # the estimate is the plan, zoom by zoom, about 4x per zoom in
    est = OB.region_estimate(["BAS", "SAM"], max_zoom=16)
    for c in ("BAS", "SAM"):
        n = sum(OB.region_count(c, z) for z in (14, 15, 16))
        assert est[c] == (n, pytest.approx(n * OB.DETAIL_TILE_KB / 1000))
        assert OB.region_count(c, 17) / OB.region_count(c, 16) == pytest.approx(4, rel=0.03)
    assert len(_tiles_of("BAS", 15)) == OB.region_count("BAS", 14) + OB.region_count("BAS", 15)
    bas = OB.region_estimate(["BAS"])["BAS"][0]                  # zooms 14-18
    assert 1_200_000 < bas < 1_600_000
    # only the tiles inside the boundary: far fewer than its box
    w, s_, e, n_ = OB.regions()["SAM"].bbox
    box = sum(1 for _ in OB.tiles_in((w, s_, e, n_), 14))
    assert OB.region_count("SAM", 14) < 0.75 * box


# --------------------------------------------------------------------------- #
# the growing pack (one region's building-scale imagery)
# --------------------------------------------------------------------------- #
def _jpg(i: int) -> bytes:
    return b"\xff\xd8" + i.to_bytes(4, "little") * 8


def _read_all(path):
    from pmtiles.reader import MmapSource, all_tiles
    with open(path, "rb") as f:
        return dict(all_tiles(MmapSource(f)))


def test_a_growing_pack_is_a_complete_pack_at_each_checkpoint(tmp_path):
    from pmtiles.tile import zxy_to_tileid

    from rfopt.geo.pack_writer import DATA, SLOTS, GrowingPack
    path = tmp_path / "detail_bas.pmtiles"
    tiles = [(15, x, y) for x in range(19000, 19012) for y in range(13300, 13310)]
    with GrowingPack(path, metadata={"name": "t", "source_id": "S"}) as gp:
        assert OB.read_header(path) is None                      # nothing to draw yet
        for i, t in enumerate(tiles[:60]):
            assert gp.add(zxy_to_tileid(*t), _jpg(i))
        assert gp.checkpoint(bounds=BOX)["tiles"] == 60
        head = OB.read_header(path)
        assert head["tile_type"] == "jpeg" and (head["min_zoom"], head["max_zoom"]) == (15, 15)
        assert head["bounds"] == pytest.approx(BOX)
        assert _read_all(path) == {t: _jpg(i) for i, t in enumerate(tiles[:60])}
        for i, t in enumerate(tiles[60:], 60):
            gp.add(zxy_to_tileid(*t), _jpg(i))
        gp.checkpoint(bounds=BOX)
        assert len(_read_all(path)) == len(tiles)
        assert not gp.add(1, b"not an image") and not gp.add(2, b"\x89PNG....")
    # reopened: it holds what it had; what came after the last checkpoint is gone
    with GrowingPack(path) as gp:
        assert gp.loaded == len(tiles) and gp.metadata["source_id"] == "S"
        ids = [zxy_to_tileid(*t) for t in tiles]
        assert gp.holds(ids).all() and not gp.holds([zxy_to_tileid(15, 1, 1)]).any()
        size = path.stat().st_size
        gp.add(zxy_to_tileid(15, 1, 1), _jpg(999) * 100)          # then the app is closed
    assert path.stat().st_size > size
    with GrowingPack(path) as gp:
        assert gp.loaded == len(tiles) and path.stat().st_size == size
        assert not gp.holds([zxy_to_tileid(15, 1, 1)]).any()
    raw = path.read_bytes()
    assert int.from_bytes(raw[56:64], "little") == DATA            # tile data offset
    assert int.from_bytes(raw[8:16], "little") in SLOTS             # root in a slot


def test_a_big_pack_gets_leaf_directories_and_switches_root_slots(tmp_path):
    from pmtiles.reader import MmapSource, Reader
    from pmtiles.tile import zxy_to_tileid

    from rfopt.geo.pack_writer import GrowingPack
    path = tmp_path / "detail_sam.pmtiles"
    tiles = [(16, x, y) for x in range(40000, 40100) for y in range(26000, 26100)]
    roots = []
    with GrowingPack(path) as gp:
        for i, t in enumerate(tiles[:5000]):
            gp.add(zxy_to_tileid(*t), _jpg(i))
        gp.checkpoint(bounds=BOX)
        roots.append(int.from_bytes(path.read_bytes()[8:16], "little"))
        for i, t in enumerate(tiles[5000:], 5000):
            gp.add(zxy_to_tileid(*t), _jpg(i))
        gp.checkpoint(bounds=BOX)
        roots.append(int.from_bytes(path.read_bytes()[8:16], "little"))
    head = OB.read_header(path)
    assert head["tiles"] == len(tiles) and roots[0] != roots[1]
    raw = path.read_bytes()
    assert int.from_bytes(raw[48:56], "little") > 0                 # leaf directories
    with open(path, "rb") as f:
        r = Reader(MmapSource(f))
        for i in (0, 4999, 5000, 7777, len(tiles) - 1):
            assert r.get(*tiles[i]) == _jpg(i)


def test_a_tile_added_twice_keeps_the_newest_and_placeholders_are_left_out(tmp_path):
    from pmtiles.tile import zxy_to_tileid

    from rfopt.geo.pack_writer import GrowingPack
    path = tmp_path / "detail_ema.pmtiles"
    grey = _jpg(7)
    with GrowingPack(path) as gp:
        gp.add(zxy_to_tileid(14, 9000, 6000), _jpg(1))
        gp.add(zxy_to_tileid(14, 9000, 6000), _jpg(2))            # fetched again
        for x in range(20):                                        # zoom 14: kept
            gp.add(zxy_to_tileid(14, 9100 + x, 6000), grey)
        for x in range(20):                                        # zoom 16: a placeholder
            gp.add(zxy_to_tileid(16, 36000 + x, 24000), grey)
        gp.add(zxy_to_tileid(16, 36100, 24000), _jpg(3))
        st = gp.checkpoint(bounds=BOX, drop_repeats_from=15, repeat_limit=16)
    got = _read_all(path)
    assert st == {"tiles": 22, "dropped": 20} and len(got) == 22
    assert got[(14, 9000, 6000)] == _jpg(2) and got[(16, 36100, 24000)] == _jpg(3)
    assert not any(z == 16 and v == grey for (z, _, _), v in got.items())


# # --------------------------------------------------------------------------- #
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
# the building-scale download (Esri World Imagery with the user's ArcGIS key)
# --------------------------------------------------------------------------- #
def test_the_esri_imagery_needs_the_users_key(packs_dir, tmp_path, monkeypatch, small_region):
    for v in ("RFOPT_ARCGIS_KEY", "ARCGIS_API_KEY", "RFOPT_DETAIL_URL"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("RFOPT_CACHE_DIR", str(tmp_path / "cache"))
    assert OB.arcgis_key() == ""
    with pytest.raises(OB.UpdateError, match="ArcGIS API key"):
        OB.detail_sources()
    res = OB.update(("detail_bas",), log=lambda m: None)
    assert res["detail_bas"].startswith("failed") and "ArcGIS API key" in res["detail_bas"]
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


def test_one_pack_per_region():
    assert OB.DETAIL_KEYS == ("detail_bas", "detail_nas", "detail_sam", "detail_ema")
    assert [OB.PACKS[k].file for k in OB.DETAIL_KEYS] == [
        "detail_bas.pmtiles", "detail_nas.pmtiles", "detail_sam.pmtiles", "detail_ema.pmtiles"]
    assert "Basrah" in OB.PACKS["detail_bas"].title and "Esri" in OB.PACKS["detail_bas"].attribution
    assert "detail" not in OB.PACKS


def test_a_key_never_reaches_a_message_or_the_pack(packs_dir, tile_server, no_wait,
                                                    small_region):
    secret = tile_server + "?blankTile=false&token=S3CRET"
    assert "S3CRET" not in OB.redact(secret) and OB.redact(secret).endswith("token=***")
    with pytest.raises(ConnectionError) as e:
        OB.http_get("http://127.0.0.1:9/0/0/0.png?token=S3CRET", tries=1)
    assert "S3CRET" not in str(e.value)
    res = OB.update(("detail_bas",), detail_max=15, detail_url=secret, log=lambda m: None)
    assert res["detail_bas"].startswith("ok"), res
    from pmtiles.reader import MmapSource, Reader
    with open(packs_dir / "detail_bas.pmtiles", "rb") as f:
        meta = Reader(MmapSource(f)).metadata()
    assert "S3CRET" not in str(meta) and meta["source"].endswith("token=***")
    assert "Esri" in meta["attribution"] and meta["region"] == "BAS"
    assert b"S3CRET" not in (packs_dir / "detail_bas.pmtiles").read_bytes()


def test_the_whole_region_comes_down_and_the_pack_carries_on(packs_dir, tile_server,
                                                             no_wait, small_region):
    want = _tiles_of("BAS")
    res = OB.update(("detail_bas",), detail_url=tile_server, log=lambda m: None)
    assert res["detail_bas"].startswith("ok") and f"{len(want):,} new" in res["detail_bas"]
    head = OB.read_header(packs_dir / "detail_bas.pmtiles")
    assert (head["min_zoom"], head["max_zoom"]) == (14, 18) and head["tiles"] == len(want)
    assert head["bounds"] == pytest.approx(small_region.bbox, abs=1e-6)
    got = _read_all(packs_dir / "detail_bas.pmtiles")
    assert set(got) == set(want)
    z, x, y = want[-1]
    assert got[(z, x, y)] == _png(z * 1000 + x + y)
    # the same region again: nothing is fetched but the probe
    _Tiles.hits = []
    res = OB.update(("detail_bas",), detail_url=tile_server, log=lambda m: None)
    assert res["detail_bas"].startswith("ok") and "0 new" in res["detail_bas"]
    assert len(_Tiles.hits) == 1
    # a sharper zoom: only its tiles
    _Tiles.hits = []
    res = OB.update(("detail_bas",), detail_max=19, detail_url=tile_server, log=lambda m: None)
    assert res["detail_bas"].startswith("ok")
    assert set(_Tiles.hits[1:]) == {t for t in _tiles_of("BAS", 19) if t[0] == 19}
    assert OB.read_header(packs_dir / "detail_bas.pmtiles")["max_zoom"] == 19
    # start over: zooms 14-16 only
    OB.update(("detail_bas",), detail_max=16, detail_url=tile_server, detail_fresh=True,
              log=lambda m: None)
    assert OB.read_header(packs_dir / "detail_bas.pmtiles")["tiles"] == len(_tiles_of("BAS", 16))


def test_a_source_that_dies_stops_the_fetch_and_the_next_update_finishes(
        packs_dir, tile_server, no_wait, small_region):
    want = _tiles_of("BAS")
    z14 = [t for t in want if t[0] == 14]
    _Tiles.broken = set(want) - set(z14)          # every tile past zoom 14 fails
    res = OB.update(("detail_bas",), detail_url=tile_server, log=lambda m: None)
    assert res["detail_bas"].startswith("incomplete") and "press Update again" in res["detail_bas"]
    tries = [h for h in _Tiles.hits[1:] if h in _Tiles.broken]
    assert len(set(tries)) < len(_Tiles.broken) / 2            # it stopped, not ground on
    assert OB.read_header(packs_dir / "detail_bas.pmtiles")["tiles"] == len(z14)
    _Tiles.broken, _Tiles.hits = set(), []
    res = OB.update(("detail_bas",), detail_url=tile_server, log=lambda m: None)
    assert res["detail_bas"].startswith("ok")
    assert set(_Tiles.hits[1:]) == set(want) - set(z14)
    assert OB.read_header(packs_dir / "detail_bas.pmtiles")["tiles"] == len(want)


def test_another_source_starts_the_region_over_and_placeholders_are_left_out(
        packs_dir, tile_server, no_wait, small_region):
    OB.update(("detail_bas",), detail_max=15, detail_url=tile_server, log=lambda m: None)
    _Tiles.blank_from, _Tiles.hits = 17, []
    other = tile_server + "?v=2"
    res = OB.update(("detail_bas",), detail_url=other, log=lambda m: None)
    assert res["detail_bas"].startswith("ok") and "0 kept" in res["detail_bas"]
    head = OB.read_header(packs_dir / "detail_bas.pmtiles")
    assert head["max_zoom"] == 16          # one grey picture past z16: left out


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


def test_the_map_is_told_each_region_packs_zooms_and_box(packs_dir, tile_server, no_wait,
                                                         small_region):
    import _map_assets as MA
    OB.update(("detail_bas",), detail_max=17, detail_url=tile_server, log=lambda m: None)
    cfg = MA.offline_basemap_config("Satellite")
    bas = cfg["packs"]["detail_bas"]
    assert bas["installed"] and (bas["min_zoom"], bas["native_max"]) == (14, 17)
    assert bas["bounds"] == pytest.approx(list(small_region.bbox), abs=1e-6)
    assert not any(cfg["packs"][k]["installed"] for k in OB.DETAIL_KEYS[1:])
    assert not cfg["packs"]["imagery"]["installed"]
    assert cfg["packs"]["imagery"]["native_max"] == OB.PACKS["imagery"].native_max == 13
    import _tile_proxy as TP
    with urllib.request.urlopen(TP.packs_url() + "detail_bas.pmtiles", timeout=5) as r:
        assert r.read(7) == b"PMTiles"


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
    assert set(cfg["packs"]) == {"vector", "night", "earth", "imagery", *OB.DETAIL_KEYS}
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
    # every installed region pack, each asked only inside its own box
    each = js[js.index("function details("):js.index('if (mode === "Dark"')]
    assert 'k.indexOf("detail_") === 0 && has(k)' in each and "o.bounds = L.latLngBounds" in each
    # Satellite: the building-scale packs over the regional one; Blue Marble
    # only at country scale. Coverage keeps its imagery as it was.
    assert "if (sat) {" in day and "details({zIndex: 3})" in day
    assert "maxZoom: 12" in day and 'var sat = mode === "Satellite"' in day
    # Night: the same imagery graded to night (a colour grade, no sharpening),
    # the city lights screened over it, the glowing streets on top
    assert 'details({zIndex: 2, className: "rf-night-sat"})' in night
    assert 'raster("imagery"' in night and "maxZoom: packs.imagery.native_max" in night
    assert "rf-night-glow" in night and "rf-night-roads" in night
    assert ".rf-night-glow{mix-blend-mode:screen}" in js
    grade = js[js.index(".rf-night-sat{"):js.index("}", js.index(".rf-night-sat{"))]
    assert "brightness(" in grade and "blur" not in grade and "url(" not in grade


def test_the_offline_map_box_picks_regions_and_estimates_their_download(
        tmp_path, monkeypatch, put_resource):
    for v in ("RFOPT_ARCGIS_KEY", "ARCGIS_API_KEY", "RFOPT_DETAIL_URL", "RFOPT_BASEMAP_DIR"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("RFOPT_CACHE_DIR", str(tmp_path))
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    from test_site_map_ticket import _kmz
    put_resource("kmz", "R5_Sites.kmz", _kmz(), "Site KMZ")
    at = AppTest.from_file(str(APP / "views/site_map.py"), default_timeout=300)
    at.run()
    assert not at.exception, at.exception
    assert not [w for w in at.text_input if w.key == "sm_off_key"]     # only once ticked
    at.checkbox(key="sm_off_det").check().run()
    assert not at.exception, at.exception
    key = at.text_input(key="sm_off_key")
    assert key.value == "" and key.proto.type == key.proto.PASSWORD
    regions = at.multiselect(key="sm_off_areas")
    assert regions.label == "Governorate / Region boundary"
    assert list(regions.options) == ["BAS · Basrah", "NAS · Nasiriyah", "SAM · Samawah",
                                     "EMA · Amarah"]
    assert regions.value == []                       # nothing started: nothing picked
    assert not [s for s in at.selectbox if s.key == "sm_off_rad"]       # no site radius
    assert at.button(key="sm_off_go").disabled
    regions.select("BAS").select("SAM").run()
    cap = " ".join(c.value for c in at.caption)
    est = OB.region_estimate(["BAS", "SAM"])
    assert f"Basrah ≈ {est['BAS'][0]:,} tiles" in cap and f"Samawah ≈ {est['SAM'][0]:,}" in cap
    assert f"Total ≈ {est['BAS'][0] + est['SAM'][0]:,} tiles" in cap
    at.selectbox(key="sm_off_mz").select("z16 · 2 m").run()
    est16 = OB.region_estimate(["BAS", "SAM"], max_zoom=16)
    assert f"Basrah ≈ {est16['BAS'][0]:,} tiles" in " ".join(c.value for c in at.caption)
    assert at.button(key="sm_off_go").disabled                        # no key yet
    assert any("ArcGIS API key" in w.value for w in at.warning)
    # a key: kept on this PC (beside the packs), the update can start
    at.text_input(key="sm_off_key").input("my-key").run()
    assert not at.button(key="sm_off_go").disabled
    assert (tmp_path / "resources" / "arcgis_api_key.txt").read_text() == "my-key"
    # a region already started is picked next time: Update carries it on
    (tmp_path / "resources" / "basemap").mkdir(parents=True, exist_ok=True)
    (tmp_path / "resources" / "basemap" / "detail_ema.pmtiles").write_bytes(b"\0" * 16384)
    at2 = AppTest.from_file(str(APP / "views/site_map.py"), default_timeout=300)
    at2.run()
    at2.checkbox(key="sm_off_det").check().run()
    assert at2.multiselect(key="sm_off_areas").value == ["EMA"]
