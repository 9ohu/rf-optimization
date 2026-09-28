"""The Sites map's basemap, offline.

The map draws its basemap from local map packs only — single-file PMTiles
archives kept in the data folder (`folder()`), served to the map by the app's
local relay (`app/_tile_proxy.py`, with byte ranges) and drawn in the browser
by protomaps-leaflet / pmtiles (`app/static/vendor/protomaps`). Nothing is
asked of a tile server while the map is used: with the Internet off, zoom and
pan keep working on what the packs hold.

The packs:

* ``region.pmtiles`` — streets, water, land use, boundaries and places, as
  vector tiles (OpenStreetMap, in the Protomaps basemap schema). The Dark and
  Streets maps are drawn from it, and the roads / names over the satellite
  and night maps. Vector tiles are drawn at any zoom: past the pack's last
  zoom (15) they are scaled, still sharp.
* ``night.pmtiles`` — NASA Black Marble 2016 night lights (public domain), the
  base of the Night Satellite map.
* ``earth.pmtiles`` — NASA Blue Marble (public domain): day satellite at
  country scale, under the street-scale imagery.
* ``imagery.pmtiles`` (optional, large) — Sentinel-2 cloudless 2016 by EOX
  (CC BY 4.0): day satellite at street scale, for the Satellite and Coverage
  maps.

The Internet is used only by `update()` — the explicit "Update offline map"
action (Sites map, Map layers & Analysis → Offline map, or
``python tools/update_offline_map.py``). It fetches the packs for Iraq at
country zooms and for the sites' region at street zooms. A pack folder copied
from another PC works the same.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import shutil
import ssl
import struct
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Pack:
    key: str
    file: str
    kind: str              # "vector" | "raster"
    title: str
    attribution: str
    native_max: int        # the last zoom its tiles hold; drawn larger beyond


PACKS: dict[str, Pack] = {
    "vector": Pack("vector", "region.pmtiles", "vector",
                   "Streets, water and places (OpenStreetMap)",
                   "© OpenStreetMap contributors · Protomaps", 15),
    "night": Pack("night", "night.pmtiles", "raster",
                  "Night lights (NASA Black Marble 2016)",
                  "Night lights: NASA Black Marble", 8),
    "earth": Pack("earth", "earth.pmtiles", "raster",
                  "Day satellite, country scale (NASA Blue Marble)",
                  "Imagery: NASA Blue Marble / GIBS", 8),
    "imagery": Pack("imagery", "imagery.pmtiles", "raster",
                    "Day satellite, street scale (Sentinel-2 cloudless 2016)",
                    "Sentinel-2 cloudless 2016 by EOX (modified Copernicus data)", 13),
}

# lon / lat boxes: the whole country at country zooms, the sites' region at
# street zooms (`region_bbox` narrows it to where the sites are)
IRAQ = (38.7, 29.0, 48.8, 37.5)
R5 = (42.3, 29.0, 48.7, 33.0)            # Basra, Dhi Qar, Maysan, Muthanna

# where each pack is fetched from by `update()`
PROTOMAPS_BUILDS = "https://build.protomaps.com/{date}.pmtiles"
PROTOMAPS_INDEX = "https://build-metadata.protomaps.dev/builds.json"
_GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/{layer}/default/{time}/" \
        "GoogleMapsCompatible_Level8/{{z}}/{{y}}/{{x}}.{ext}"
RASTER_SOURCES = {
    # several spellings of the same layer: the first that answers is used
    "night": [_GIBS.format(layer="VIIRS_Black_Marble", time=t, ext="png")
              for t in ("2016-01-01", "default", "")],
    "earth": [_GIBS.format(layer=lyr, time=t, ext="jpeg")
              for lyr in ("BlueMarble_NextGeneration", "BlueMarble_ShadedRelief")
              for t in ("2004-08-01", "default", "")],
    "imagery": ["https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/g/"
                "{z}/{y}/{x}.jpg"],
}
_UA = "RF-Optimizer offline map update"


# --------------------------------------------------------------------------- #
# the packs on disk
# --------------------------------------------------------------------------- #
def folder() -> Path:
    """Where the packs live: RFOPT_BASEMAP_DIR, else the app's data folder."""
    env = os.environ.get("RFOPT_BASEMAP_DIR")
    if env:
        return Path(env)
    from rfopt.resources.store import root
    return root() / "basemap"


def pack_path(key: str) -> Path:
    return folder() / PACKS[key].file


_TILE_TYPE = {1: "mvt", 2: "png", 3: "jpeg", 4: "webp", 5: "avif", 6: "mlt"}


def read_header(path) -> dict | None:
    """The PMTiles v3 header of a pack (127 bytes), or None when it is not one."""
    try:
        with open(path, "rb") as f:
            b = f.read(127)
    except OSError:
        return None
    if len(b) < 127 or b[:7] != b"PMTiles" or b[7] != 3:
        return None
    min_lon, min_lat, max_lon, max_lat = struct.unpack_from("<4i", b, 102)
    return {"tile_type": _TILE_TYPE.get(b[99], "unknown"), "min_zoom": b[100],
            "max_zoom": b[101],
            "bounds": (min_lon / 1e7, min_lat / 1e7, max_lon / 1e7, max_lat / 1e7),
            "tiles": struct.unpack_from("<Q", b, 72)[0]}


def status() -> dict[str, dict | None]:
    """key -> what the pack on disk holds (None when it is not installed)."""
    out: dict[str, dict | None] = {}
    for key, pack in PACKS.items():
        p = folder() / pack.file
        h = read_header(p) if p.is_file() else None
        if h is None:
            out[key] = None
            continue
        st = p.stat()
        out[key] = {**h, "size_mb": st.st_size / 1e6,
                    "updated": dt.datetime.fromtimestamp(st.st_mtime)}
    return out


def installed(key: str) -> bool:
    p = pack_path(key)
    return p.is_file() and read_header(p) is not None


# --------------------------------------------------------------------------- #
# tile arithmetic
# --------------------------------------------------------------------------- #
def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    lat = max(min(lat, 85.0511), -85.0511)
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def tiles_in(bbox, z: int):
    """(x, y) of every tile of zoom `z` touching the lon / lat box."""
    x0, y0 = lonlat_to_tile(bbox[0], bbox[3], z)
    x1, y1 = lonlat_to_tile(bbox[2], bbox[1], z)
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            yield x, y


def plan(bands) -> list[tuple[int, int, int]]:
    """(z, x, y) of every tile the (min zoom, max zoom, bbox) bands ask for."""
    seen: set = set()
    for zmin, zmax, bbox in bands:
        for z in range(zmin, zmax + 1):
            for x, y in tiles_in(bbox, z):
                seen.add((z, x, y))
    return sorted(seen)


def region_bbox(lats, lons, margin_km: float = 15.0):
    """The sites' extent, with a margin — the street-zoom area of the packs.
    Falls back to R5 when no site has a position."""
    pts = [(float(a), float(o)) for a, o in zip(lats, lons)
           if a is not None and o is not None and math.isfinite(float(a))
           and math.isfinite(float(o)) and 28 < float(a) < 38 and 38 < float(o) < 49]
    if not pts:
        return R5
    la = [p[0] for p in pts]
    lo = [p[1] for p in pts]
    dlat = margin_km / 111.32
    dlon = margin_km / (111.32 * math.cos(math.radians(sum(la) / len(la))))
    return (min(lo) - dlon, min(la) - dlat, max(lo) + dlon, max(la) + dlat)


# --------------------------------------------------------------------------- #
# the network (only `update` uses it)
# --------------------------------------------------------------------------- #
def _ssl_context() -> ssl.SSLContext:
    try:
        import truststore                  # the OS certificate store (VPN re-signing)
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        return ssl.create_default_context()


_local = threading.local()


def _opener():
    op = getattr(_local, "opener", None)
    if op is None:
        op = urllib.request.build_opener(urllib.request.ProxyHandler(),
                                         urllib.request.HTTPSHandler(context=_ssl_context()))
        _local.opener = op
    return op


def http_get(url: str, *, byte_range: tuple[int, int] | None = None,
             timeout: float = 60.0, tries: int = 4) -> bytes | None:
    """GET a URL (or `byte_range` = (offset, length) of it). None on 404."""
    headers = {"User-Agent": _UA}
    if byte_range is not None:
        off, n = byte_range
        headers["Range"] = f"bytes={off}-{off + n - 1}"
    last: Exception | None = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with _opener().open(req, timeout=timeout) as r:
                data = r.read()
            if byte_range is not None and len(data) > byte_range[1]:
                # a server that ignored the range sent the whole file
                data = data[byte_range[0]:byte_range[0] + byte_range[1]]
            return data
        except urllib.error.HTTPError as e:
            if e.code in (404, 204):
                return None
            last = e
        except Exception as e:             # a dropped packet on the VPN: retry
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise ConnectionError(f"{url}: {last}")


class UpdateError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# packs from rasters (NASA GIBS, EOX)
# --------------------------------------------------------------------------- #
def _probe(templates, z=5, x=20, y=13) -> str:
    """The first template that answers with an image (tile over Iraq)."""
    for tpl in templates:
        try:
            data = http_get(tpl.format(z=z, x=x, y=y), tries=2, timeout=30)
        except ConnectionError:
            continue
        if data and (data[:4] == b"\x89PNG" or data[:2] == b"\xff\xd8"):
            return tpl
    raise UpdateError("no answer from " + ", ".join(sorted({t.split('/')[2] for t in templates})))


def build_raster_pack(templates, bands, out: Path, *, attribution: str, name: str,
                      progress=None, workers: int = 6) -> int:
    """Fetch the tiles of `bands` and write them as one raster PMTiles archive.
    Returns the number of tiles written."""
    from pmtiles.tile import Compression, TileType, zxy_to_tileid
    from pmtiles.writer import Writer

    tpl = templates if isinstance(templates, str) else _probe(templates)
    todo = plan(bands)
    tmp = Path(tempfile.mkdtemp(prefix="rf_pack_", dir=out.parent))
    done = [0]
    kinds: set = set()
    written: list[int] = []                 # tile ids on disk (list.append is atomic)

    def one(zxy):
        z, x, y = zxy
        data = http_get(tpl.format(z=z, x=x, y=y))
        if data and (data[:4] == b"\x89PNG" or data[:2] == b"\xff\xd8"):
            tid = zxy_to_tileid(z, x, y)
            (tmp / str(tid)).write_bytes(data)
            written.append(tid)
            kinds.add("png" if data[:4] == b"\x89PNG" else "jpeg")
        done[0] += 1
        if progress:
            progress(name, done[0], len(todo))

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(one, todo))
        ids = sorted(written)
        if not ids:
            raise UpdateError(f"{name}: no tile came back")
        if len(kinds) > 1:
            raise UpdateError(f"{name}: the source mixed PNG and JPEG tiles")
        part = out.with_suffix(".part")
        with open(part, "wb") as f:
            w = Writer(f)
            for tid in ids:
                w.write_tile(tid, (tmp / str(tid)).read_bytes())
            lon0, lat0, lon1, lat1 = _union(bands)
            w.finalize({"tile_type": TileType.PNG if kinds == {"png"} else TileType.JPEG,
                        "tile_compression": Compression.NONE,
                        "min_lon_e7": int(lon0 * 1e7), "min_lat_e7": int(lat0 * 1e7),
                        "max_lon_e7": int(lon1 * 1e7), "max_lat_e7": int(lat1 * 1e7),
                        "center_zoom": 7, "center_lon_e7": int((lon0 + lon1) / 2 * 1e7),
                        "center_lat_e7": int((lat0 + lat1) / 2 * 1e7)},
                       {"name": name, "attribution": attribution, "source": tpl})
        part.replace(out)
        return len(ids)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _union(bands):
    return (min(b[2][0] for b in bands), min(b[2][1] for b in bands),
            max(b[2][2] for b in bands), max(b[2][3] for b in bands))


# --------------------------------------------------------------------------- #
# the vector pack: a region cut out of a Protomaps OpenStreetMap build
# --------------------------------------------------------------------------- #
def latest_vector_build() -> str:
    """The URL of the newest Protomaps daily build that answers."""
    try:
        raw = http_get(PROTOMAPS_INDEX, tries=2, timeout=30)
        builds = sorted((b.get("key", "") for b in json.loads(raw or b"[]")
                         if str(b.get("key", "")).endswith(".pmtiles")), reverse=True)
        for key in builds[:5]:
            url = PROTOMAPS_BUILDS.format(date=key[:-len(".pmtiles")])
            if http_get(url, byte_range=(0, 127), tries=2):
                return url
    except Exception:
        pass
    today = dt.date.today()
    for back in range(0, 21):
        url = PROTOMAPS_BUILDS.format(date=(today - dt.timedelta(days=back)).strftime("%Y%m%d"))
        try:
            if http_get(url, byte_range=(0, 127), tries=1, timeout=30):
                return url
        except ConnectionError:
            continue
    raise UpdateError("no Protomaps build answered (build.protomaps.com)")


def extract_region(get_bytes, bands, out: Path, *, name: str = "region",
                   progress=None, workers: int = 4, gap: int = 1 << 18,
                   batch: int = 32 << 20) -> int:
    """Copy the tiles of `bands` out of a PMTiles v3 archive read through
    `get_bytes(offset, length)` (a URL with byte ranges, or a local file) into a
    new archive at `out`. Returns the number of tiles written.

    Only the directories that hold a wanted tile are read, and the wanted tiles'
    bytes are fetched in a few large ranges, so a region of a planet-size
    archive comes down in minutes, not one request per tile."""
    import gzip
    from bisect import bisect_left

    from pmtiles.tile import (Compression, deserialize_directory, deserialize_header,
                              zxy_to_tileid)
    from pmtiles.writer import Writer

    head = deserialize_header(get_bytes(0, 127))
    want = sorted({zxy_to_tileid(z, x, y) for z, x, y in plan(bands)})
    if not want:
        raise UpdateError("empty region")

    def wanted_in(lo: int, hi: int) -> list[int]:
        i = bisect_left(want, lo)
        j = bisect_left(want, hi)
        return want[i:j]

    found: list[tuple[int, int, int]] = []        # (tile id, offset, length)
    level = [(head["root_offset"], head["root_length"], 0, 1 << 62)]
    leaf_base = head["leaf_directory_offset"]
    depth = 0
    while level and depth < 5:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            dirs = list(ex.map(lambda d: deserialize_directory(get_bytes(d[0], d[1])), level))
        nxt = []
        for (_, _, lo, hi), entries in zip(level, dirs):
            for i, e in enumerate(entries):
                end = entries[i + 1].tile_id if i + 1 < len(entries) else hi
                if e.run_length == 0:
                    if wanted_in(e.tile_id, end):
                        nxt.append((leaf_base + e.offset, e.length, e.tile_id, end))
                else:
                    for tid in wanted_in(e.tile_id, e.tile_id + e.run_length):
                        found.append((tid, e.offset, e.length))
        level, depth = nxt, depth + 1
        if progress:
            progress(name + " (index)", depth, depth + (1 if level else 0))
    if not found:
        raise UpdateError(f"{name}: the source has no tile in the region")

    # the tile bytes, in a few large ranges
    spans = sorted({(off, n) for _, off, n in found})
    runs: list[list] = []
    for off, n in spans:
        if runs and off - (runs[-1][0] + runs[-1][1]) <= gap and \
                off + n - runs[-1][0] <= batch:
            runs[-1][1] = max(runs[-1][1], off + n - runs[-1][0])
            runs[-1][2].append((off, n))
        else:
            runs.append([off, n, [(off, n)]])
    data_base = head["tile_data_offset"]
    tmp = Path(tempfile.mkdtemp(prefix="rf_region_", dir=out.parent))
    done = [0]

    def fetch(run):
        start, length, parts = run
        blob = get_bytes(data_base + start, length)
        for off, n in parts:
            (tmp / f"{off}_{n}").write_bytes(blob[off - start:off - start + n])
        done[0] += 1
        if progress:
            progress(name, done[0], len(runs))

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(fetch, runs))
        part = out.with_suffix(".part")
        try:
            meta_raw = get_bytes(head["metadata_offset"], head["metadata_length"])
            if head.get("internal_compression") == Compression.GZIP:
                meta_raw = gzip.decompress(meta_raw)
            meta = json.loads(meta_raw or b"{}")
        except Exception:
            meta = {}
        meta.update({"name": name, "attribution": PACKS["vector"].attribution})
        found.sort()
        with open(part, "wb") as f:
            w = Writer(f)
            for tid, off, n in found:
                w.write_tile(tid, (tmp / f"{off}_{n}").read_bytes())
            lon0, lat0, lon1, lat1 = _union(bands)
            w.finalize({"tile_type": head["tile_type"],
                        "tile_compression": head["tile_compression"],
                        "min_lon_e7": int(lon0 * 1e7), "min_lat_e7": int(lat0 * 1e7),
                        "max_lon_e7": int(lon1 * 1e7), "max_lat_e7": int(lat1 * 1e7),
                        "center_zoom": 8, "center_lon_e7": int((lon0 + lon1) / 2 * 1e7),
                        "center_lat_e7": int((lat0 + lat1) / 2 * 1e7)}, meta)
        part.replace(out)
        return len(found)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def url_reader(url: str):
    return lambda off, n: http_get(url, byte_range=(off, n)) or b""


def file_reader(path):
    def get(off, n):
        with open(path, "rb") as f:
            f.seek(off)
            return f.read(n)
    return get


# --------------------------------------------------------------------------- #
# the one explicit update
# --------------------------------------------------------------------------- #
def bands_for(key: str, region=R5, *, imagery_max: int = 13, vector_max: int = 15):
    """(min zoom, max zoom, bbox) bands of each pack: the country at country
    zooms, the sites' region at street zooms."""
    if key == "vector":
        return [(0, 9, IRAQ), (10, vector_max, region)]
    if key in ("night", "earth"):
        return [(0, 8, IRAQ)]
    if key == "imagery":
        return [(0, 9, IRAQ), (10, imagery_max, region)]
    raise KeyError(key)


def update(keys=("vector", "night", "earth"), *, region=R5, vector_source: str | None = None,
           imagery_max: int = 13, vector_max: int = 15, progress=None, log=print) -> dict:
    """Fetch the packs named in `keys` (the explicit, manual map-data update —
    the only step that uses the Internet). A pack is replaced only once its
    new file is complete; a failed pack keeps the old one. Returns
    key -> "ok (n tiles)" / the error."""
    folder().mkdir(parents=True, exist_ok=True)
    result: dict[str, str] = {}
    for key in keys:
        pack = PACKS[key]
        out = folder() / pack.file
        try:
            if key == "vector":
                src = vector_source or latest_vector_build()
                log(f"{pack.title}: from {src}")
                get = file_reader(src) if Path(src).is_file() else url_reader(src)
                n = extract_region(get, bands_for(key, region, vector_max=vector_max),
                                   out, name=pack.title, progress=progress)
            else:
                log(f"{pack.title}: fetching")
                n = build_raster_pack(RASTER_SOURCES[key],
                                      bands_for(key, region, imagery_max=imagery_max),
                                      out, attribution=pack.attribution, name=pack.title,
                                      progress=progress)
            result[key] = f"ok ({n:,} tiles, {out.stat().st_size / 1e6:,.1f} MB)"
        except Exception as e:                     # keep the old pack
            for leftover in (out.with_suffix(".part"),):
                leftover.unlink(missing_ok=True)
            result[key] = f"failed: {e}"
        log(f"{pack.title}: {result[key]}")
    return result


__all__ = ["IRAQ", "PACKS", "Pack", "R5", "RASTER_SOURCES", "UpdateError", "bands_for",
           "build_raster_pack", "extract_region", "file_reader", "folder", "http_get",
           "installed", "latest_vector_build", "lonlat_to_tile", "pack_path", "plan",
           "read_header", "region_bbox", "status", "tiles_in", "update", "url_reader"]
