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
  city lights of the Night Satellite map.
* ``earth.pmtiles`` — NASA Blue Marble (public domain): day satellite at
  country scale only (500 m pixels, zooms 0-8). It is never stretched into
  street zooms.
* ``imagery.pmtiles`` (optional, large) — Sentinel-2 cloudless 2016 by EOX
  (CC BY 4.0): day satellite at regional scale, up to zoom 13 (16 m pixels
  at Basra; Sentinel-2 itself sees 10 m) — fields, rivers, the outline of a
  town, never single buildings. It is the sharpest openly licensed imagery.
* ``detail.pmtiles`` (optional, large) — Esri World Imagery (Maxar Vivid,
  about 0.3-0.6 m in Iraq's cities): day satellite at building scale, zooms
  14-18 (0.5 m pixels at zoom 18), around every site (`detail_rings`). It is
  fetched through ArcGIS with the user's own API key (`arcgis_key`), from the
  World Imagery (for Export) service — Esri's service for taking the imagery
  offline. The Satellite and Night Satellite maps draw it over the others.

The Internet is used only by `update()` — the explicit "Update offline map"
action (Sites map, Map layers & Analysis → Offline map, or
``python scripts/update_offline_map.py``). It fetches the packs for Iraq at
country zooms, for the sites' region at street zooms and around each site at
building zooms. A pack folder copied from another PC works the same.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import shutil
import ssl
import struct
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
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
                    "Day satellite, regional scale (Sentinel-2 cloudless 2016, 10 m)",
                    "Sentinel-2 cloudless 2016 by EOX (modified Copernicus data)", 13),
    "detail": Pack("detail", "detail.pmtiles", "raster",
                   "Day satellite, building scale (Esri World Imagery, 0.5 m)",
                   "Imagery © Esri, Maxar, Earthstar Geographics", 18),
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
# the building-scale imagery: Esri World Imagery through ArcGIS, with the
# user's API key — first the World Imagery (for Export) service (Esri's
# service for offline use), then the same layer on the ArcGIS Location
# Platform endpoints. `blankTile=false`: a tile the imagery does not reach
# answers 404 instead of a grey "Map data not yet available" picture.
_ESRI_REST = "/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
ESRI_DETAIL = [
    "https://tiledbasemaps.arcgis.com" + _ESRI_REST + "?blankTile=false&token={key}",
    "https://ibasemaps-api.arcgis.com" + _ESRI_REST + "?blankTile=false&token={key}",
    "https://static-map-tiles-api.arcgis.com/arcgis/rest/services/"
    "static-basemap-tiles-service/v1/arcgis/imagery/static/tile/{z}/{y}/{x}?token={key}",
]
ESRI_DETAIL_ID = "Esri World Imagery"
# the building-scale pack around each site: the radius at zoom 18 (0.5 m
# pixels), doubled at each zoom out (`detail_rings`)
DETAIL_MIN, DETAIL_MAX = 14, 18
DETAIL_RADIUS_KM = 0.5
DETAIL_RADIUS_CAP_KM = 12.0          # past this the regional imagery does
DETAIL_TILE_KB = 18                  # an Esri JPEG tile, on average (estimates)
_UA = "RF-Optimizer offline map update"
_SECRET = re.compile(r"(?i)([?&](?:token|key|api_?key|access_token)=)[^&#]+")


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


def site_points(lats, lons) -> list[tuple[float, float]]:
    """(lat, lon) of every site with a position in Iraq."""
    pts = []
    for a, o in zip(lats, lons):
        try:
            a, o = float(a), float(o)
        except (TypeError, ValueError):
            continue
        if math.isfinite(a) and math.isfinite(o) and 28 < a < 38 and 38 < o < 49:
            pts.append((a, o))
    return pts


def region_bbox(lats, lons, margin_km: float = 15.0):
    """The sites' extent, with a margin — the street-zoom area of the packs.
    Falls back to R5 when no site has a position."""
    pts = site_points(lats, lons)
    if not pts:
        return R5
    la = [p[0] for p in pts]
    lo = [p[1] for p in pts]
    dlat = margin_km / 111.32
    dlon = margin_km / (111.32 * math.cos(math.radians(sum(la) / len(la))))
    return (min(lo) - dlon, min(la) - dlat, max(lo) + dlon, max(la) + dlat)


def detail_rings(max_zoom: int = DETAIL_MAX, radius_km: float = DETAIL_RADIUS_KM,
                 min_zoom: int = DETAIL_MIN) -> list[tuple[int, float]]:
    """(zoom, radius km) of the building-scale pack: `radius_km` around a site
    at zoom 18, doubled at each zoom out and halved at each zoom in — the
    screen around a site at every zoom, for about the same tiles per zoom."""
    return [(z, min(radius_km * 2.0 ** (18 - z), DETAIL_RADIUS_CAP_KM))
            for z in range(int(min_zoom), int(max_zoom) + 1)]


def _tile_lat(y: int, n: int) -> float:
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))


def site_tiles(points, rings) -> list[tuple[int, int, int]]:
    """(z, x, y) of every tile that comes within a ring's radius of a site."""
    seen: set = set()
    for z, r_km in rings:
        n = 2 ** z
        for lat, lon in points:
            kx = 111.32 * max(math.cos(math.radians(lat)), 0.01)
            dlat, dlon = r_km / 111.32, r_km / kx
            x0, y0 = lonlat_to_tile(lon - dlon, lat + dlat, z)
            x1, y1 = lonlat_to_tile(lon + dlon, lat - dlat, z)
            for x in range(x0, x1 + 1):
                lon0, lon1 = x / n * 360.0 - 180.0, (x + 1) / n * 360.0 - 180.0
                dx = (min(max(lon, lon0), lon1) - lon) * kx
                for y in range(y0, y1 + 1):
                    if (z, x, y) in seen:
                        continue
                    lat1, lat0 = _tile_lat(y, n), _tile_lat(y + 1, n)
                    dy = (min(max(lat, lat0), lat1) - lat) * 111.32
                    if dx * dx + dy * dy <= r_km * r_km:   # the tile's nearest point
                        seen.add((z, x, y))
    return sorted(seen)


def rings_bbox(points, rings):
    """The lon / lat box the rings around the sites cover."""
    r = max(rk for _, rk in rings)
    la = [p[0] for p in points]
    lo = [p[1] for p in points]
    dlat = r / 111.32
    dlon = r / (111.32 * math.cos(math.radians(sum(la) / len(la))))
    return (min(lo) - dlon, min(la) - dlat, max(lo) + dlon, max(la) + dlat)


def detail_estimate(points, *, max_zoom: int = DETAIL_MAX,
                    radius_km: float = DETAIL_RADIUS_KM) -> tuple[int, float]:
    """(tiles, MB) the building-scale pack would hold around these sites."""
    if not points:
        return 0, 0.0
    n = len(site_tiles(points, detail_rings(max_zoom, radius_km)))
    return n, n * DETAIL_TILE_KB / 1000.0


# --------------------------------------------------------------------------- #
# the ArcGIS key (the building-scale imagery only)
# --------------------------------------------------------------------------- #
def _key_file() -> Path:
    """Beside the pack folder, never in it: a copied pack folder carries no key."""
    from rfopt.resources.store import root
    return root() / "arcgis_api_key.txt"


def arcgis_key() -> str:
    """The user's ArcGIS API key: RFOPT_ARCGIS_KEY / ARCGIS_API_KEY, else the
    one saved on this PC ("" when none)."""
    for env in ("RFOPT_ARCGIS_KEY", "ARCGIS_API_KEY"):
        v = os.environ.get(env, "").strip()
        if v:
            return v
    try:
        return _key_file().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save_arcgis_key(key: str) -> None:
    """Keep the key on this PC (an empty key forgets it)."""
    p = _key_file()
    key = (key or "").strip()
    if not key:
        p.unlink(missing_ok=True)
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(key, encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def redact(url: str) -> str:
    """A URL with its key / token hidden — for messages and pack metadata."""
    return _SECRET.sub(r"\1***", str(url))


def detail_sources(key: str | None = None, url: str | None = None) -> tuple[list[str], str]:
    """The building-scale imagery's tile URLs, and the source's name kept in
    the pack. `url` (or RFOPT_DETAIL_URL) is another licensed imagery
    service ({z}/{x}/{y} template) used instead of Esri."""
    url = url or os.environ.get("RFOPT_DETAIL_URL", "").strip()
    if url:
        return [url], redact(url)
    key = (key or arcgis_key()).strip()
    if not key:
        raise UpdateError("the building-scale imagery needs an ArcGIS API key "
                          "(Offline map → ArcGIS API key)")
    k = urllib.parse.quote(key, safe="")
    return [t.replace("{key}", k) for t in ESRI_DETAIL], ESRI_DETAIL_ID


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
    raise ConnectionError(f"{redact(url)}: {last}")


class UpdateError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# packs from rasters (NASA GIBS, EOX, Esri)
# --------------------------------------------------------------------------- #
REPEAT_LIMIT = 16        # the same picture on this many street tiles: a placeholder
FAIL_STREAK = 40         # this many failed tiles in a row: the source is gone, stop


def tile_url(template: str, z: int, x: int, y: int) -> str:
    return template.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))


def _is_image(data) -> bool:
    return bool(data) and (data[:4] == b"\x89PNG" or data[:2] == b"\xff\xd8")


def _probe(templates, z=5, x=20, y=13, *, hint: str = "") -> str:
    """The first template that answers with an image (tile over Iraq)."""
    for tpl in templates:
        try:
            data = http_get(tile_url(tpl, z, x, y), tries=2, timeout=30)
        except ConnectionError:
            continue
        if _is_image(data):
            return tpl
    raise UpdateError("no answer from "
                      + ", ".join(sorted({t.split('/')[2] for t in templates})) + hint)


def pack_tiles(path) -> tuple[dict[int, tuple[int, int]], dict, str]:
    """Every tile of a local raster pack: tile id -> (offset, length) in the
    file, with the pack's metadata and tile type."""
    from pmtiles.reader import MmapSource, Reader
    from pmtiles.tile import deserialize_directory, deserialize_header

    get = file_reader(path)
    head = deserialize_header(get(0, 127))
    out: dict[int, tuple[int, int]] = {}
    todo = [(head["root_offset"], head["root_length"])]
    while todo:
        off, n = todo.pop()
        for e in deserialize_directory(get(off, n)):
            if e.run_length == 0:
                todo.append((head["leaf_directory_offset"] + e.offset, e.length))
            else:
                for i in range(e.run_length):
                    out[e.tile_id + i] = (head["tile_data_offset"] + e.offset, e.length)
    with open(path, "rb") as f:
        meta = Reader(MmapSource(f)).metadata()
    return out, meta, (read_header(path) or {}).get("tile_type", "unknown")


def _swap(part: Path, out: Path, tries: int = 20) -> None:
    """Put the new pack in place. On Windows that fails while the map's relay
    is reading the old one: wait for it rather than lose a finished download."""
    for i in range(tries):
        try:
            part.replace(out)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.5)


def build_raster_pack(templates, bands, out: Path, *, attribution: str, name: str,
                      progress=None, workers: int = 6, todo=None, source_id: str = "",
                      reuse: bool = False, keep_going: bool = False,
                      drop_repeats: bool = False, stash: Path | None = None,
                      report: dict | None = None) -> int:
    """Fetch the tiles of `bands` (or the (z, x, y) of `todo`) and write them as
    one raster PMTiles archive. Returns the number of tiles written.

    `reuse`: the pack at `out` grows instead of being replaced — every tile it
    already holds from the same source is kept and only the others are
    fetched, so another area adds to it. `stash`: a folder the fetched tiles
    wait in until the pack is written, kept if the update stops (the app
    closed, the network gone) and taken up by the next one, so a long download
    carries on where it stopped. `keep_going`: a tile that keeps failing is
    left out (counted in `report`) instead of failing the pack; FAIL_STREAK
    failures in a row stop the fetch and keep what came. `drop_repeats`: one
    picture repeated on many street-zoom tiles (a "no imagery here"
    placeholder) is left out."""
    from pmtiles.tile import Compression, TileType, tileid_to_zxy, zxy_to_tileid
    from pmtiles.writer import Writer

    tpl = templates if isinstance(templates, str) else _probe(templates)
    todo = plan(bands) if todo is None else list(todo)
    source_id = source_id or redact(tpl)
    lon0, lat0, lon1, lat1 = _union(bands)

    # what the pack on disk already holds from this source: kept, read from it
    # while the new file is written (never copied twice)
    held: dict[int, tuple[int, int]] = {}
    kinds: set = set()
    if reuse and out.is_file() and read_header(out):
        try:
            index, meta, kind = pack_tiles(out)
            if meta.get("source_id") == source_id and kind in ("png", "jpeg"):
                held, kinds = index, {kind}
                b = read_header(out)["bounds"]
                lon0, lat0 = min(lon0, b[0]), min(lat0, b[1])
                lon1, lat1 = max(lon1, b[2]), max(lat1, b[3])
        except Exception:
            held = {}
    total = len(todo)
    todo = [t for t in todo if zxy_to_tileid(*t) not in held]

    fetched: list[int] = []                 # tile ids on disk (list.append is atomic)
    looks: dict[int, int] = {}              # tile id -> hash of its bytes
    failed: list[str] = []
    streak = [0]
    stop = threading.Event()

    def keep(tid: int, data: bytes) -> None:
        fetched.append(tid)
        looks[tid] = hash(data)
        kinds.add("png" if data[:4] == b"\x89PNG" else "jpeg")

    # the tiles a stopped update already fetched (same source only)
    if stash is not None:
        stash.mkdir(parents=True, exist_ok=True)
        mark = stash / "source.txt"
        if not mark.is_file() or mark.read_text(encoding="utf-8") != source_id:
            shutil.rmtree(stash, ignore_errors=True)
            stash.mkdir(parents=True, exist_ok=True)
            mark.write_text(source_id, encoding="utf-8")
        rest = []
        for t in todo:
            tid = zxy_to_tileid(*t)
            if (stash / str(tid)).is_file():
                keep(tid, (stash / str(tid)).read_bytes())
            else:
                rest.append(t)
        todo, tmp = rest, stash
    else:
        tmp = Path(tempfile.mkdtemp(prefix="rf_pack_", dir=out.parent))
    done = [total - len(todo)]

    def one(zxy):
        if stop.is_set():
            return
        z, x, y = zxy
        try:
            data = http_get(tile_url(tpl, z, x, y))
            streak[0] = 0
        except ConnectionError as e:
            if not keep_going:
                raise
            failed.append(str(e))
            streak[0] += 1
            if streak[0] >= FAIL_STREAK:
                stop.set()
            data = None
        if _is_image(data):
            tid = zxy_to_tileid(z, x, y)
            part = tmp / f"{tid}.part"
            part.write_bytes(data)
            part.replace(tmp / str(tid))           # never half a tile in the stash
            keep(tid, data)
        done[0] += 1
        if progress:
            progress(name, done[0], total)

    ok = False
    try:
        if progress:
            progress(name, done[0], total)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(one, todo))
        new = set(fetched)
        dropped = 0
        if drop_repeats and new:
            street = Counter(looks[t] for t in new if tileid_to_zxy(t)[0] >= 15)
            same = {h for h, c in street.items() if c >= REPEAT_LIMIT}
            bad = {t for t in new if looks[t] in same and tileid_to_zxy(t)[0] >= 15}
            dropped, new = len(bad), new - bad
        if report is not None:
            report.update(kept=len(held), fetched=len(new), failed=total - done[0] + len(failed),
                          dropped=dropped, error=failed[0] if failed else "")
        ids = sorted(new | set(held))
        if not ids:
            raise UpdateError(f"{name}: no tile came back"
                              + (f" ({failed[0]})" if failed else ""))
        if len(kinds) > 1:
            raise UpdateError(f"{name}: the source mixed PNG and JPEG tiles")
        part = out.with_suffix(".part")
        old = open(out, "rb") if held else None
        try:
            with open(part, "wb") as f:
                w = Writer(f)
                for tid in ids:
                    if tid in new:
                        src = tmp / str(tid)
                        w.write_tile(tid, src.read_bytes())
                        src.unlink()               # the disk holds each tile once
                    else:
                        off, n = held[tid]
                        old.seek(off)
                        w.write_tile(tid, old.read(n))
                w.finalize({"tile_type": TileType.PNG if kinds == {"png"} else TileType.JPEG,
                            "tile_compression": Compression.NONE,
                            "min_lon_e7": int(lon0 * 1e7), "min_lat_e7": int(lat0 * 1e7),
                            "max_lon_e7": int(lon1 * 1e7), "max_lat_e7": int(lat1 * 1e7),
                            "center_zoom": max(min(7, tileid_to_zxy(ids[-1])[0]),
                                               tileid_to_zxy(ids[0])[0]),
                            "center_lon_e7": int((lon0 + lon1) / 2 * 1e7),
                            "center_lat_e7": int((lat0 + lat1) / 2 * 1e7)},
                           {"name": name, "attribution": attribution, "source": redact(tpl),
                            "source_id": source_id})
        finally:
            if old is not None:
                old.close()
        _swap(part, out)
        ok = True
        return len(ids)
    finally:
        if ok or stash is None:                    # a stopped update keeps its stash
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
    zooms, the sites' region at street zooms. (The building-scale pack is
    planned around each site instead: `detail_rings`.)"""
    if key == "vector":
        return [(0, 9, IRAQ), (10, vector_max, region)]
    if key in ("night", "earth"):
        return [(0, 8, IRAQ)]
    if key == "imagery":
        return [(0, 9, IRAQ), (10, imagery_max, region)]
    raise KeyError(key)


def update_detail(out: Path, sites, *, max_zoom: int = DETAIL_MAX,
                  radius_km: float = DETAIL_RADIUS_KM, key: str | None = None,
                  url: str | None = None, fresh: bool = False, progress=None,
                  log=print) -> tuple[int, dict]:
    """Fetch the building-scale imagery around `sites` ((lat, lon) pairs) into
    the pack at `out`, adding to what it already holds (`fresh`: start over).
    Returns (tiles in the pack, report)."""
    pack = PACKS["detail"]
    sites = list(sites)
    if not sites:
        raise UpdateError("no site has a position — the building-scale imagery is "
                          "fetched around the sites")
    templates, source_id = detail_sources(key, url)
    rings = detail_rings(max_zoom, radius_km)
    todo = site_tiles(sites, rings)
    z0 = rings[0][0]
    x, y = lonlat_to_tile(sites[0][1], sites[0][0], z0)
    tpl = _probe(templates, z0, x, y, hint="" if source_id != ESRI_DETAIL_ID else
                 " — check the ArcGIS API key (it needs the basemaps privilege)")
    log(f"{pack.title}: {len(todo):,} tiles around {len(sites):,} sites, zoom "
        f"{z0}-{rings[-1][0]}, from {redact(tpl).split('/')[2]}")
    report: dict = {}
    stash = out.with_name(out.stem + ".download")
    if fresh:
        shutil.rmtree(stash, ignore_errors=True)
    n = build_raster_pack(tpl, [(z0, rings[-1][0], rings_bbox(sites, rings))], out,
                          attribution=pack.attribution, name=pack.title,
                          progress=progress, workers=8, todo=todo, source_id=source_id,
                          reuse=not fresh, keep_going=True, drop_repeats=True,
                          stash=stash, report=report)
    return n, report


def update(keys=("vector", "night", "earth"), *, region=R5, vector_source: str | None = None,
           imagery_max: int = 13, vector_max: int = 15, sites=(),
           detail_max: int = DETAIL_MAX, detail_radius_km: float = DETAIL_RADIUS_KM,
           arcgis_key: str | None = None, detail_url: str | None = None,
           detail_fresh: bool = False, progress=None, log=print) -> dict:
    """Fetch the packs named in `keys` (the explicit, manual map-data update —
    the only step that uses the Internet). A pack is replaced only once its
    new file is complete; a failed pack keeps the old one. `sites`: (lat, lon)
    of the sites the building-scale pack ("detail") is fetched around.
    Returns key -> "ok (n tiles)" / "incomplete (...)" / the error."""
    folder().mkdir(parents=True, exist_ok=True)
    result: dict[str, str] = {}
    for key in keys:
        pack = PACKS[key]
        out = folder() / pack.file
        try:
            note = ""
            if key == "vector":
                src = vector_source or latest_vector_build()
                log(f"{pack.title}: from {src}")
                get = file_reader(src) if Path(src).is_file() else url_reader(src)
                n = extract_region(get, bands_for(key, region, vector_max=vector_max),
                                   out, name=pack.title, progress=progress)
            elif key == "detail":
                n, rep = update_detail(out, sites, max_zoom=detail_max,
                                       radius_km=detail_radius_km, key=arcgis_key,
                                       url=detail_url, fresh=detail_fresh,
                                       progress=progress, log=log)
                note = f"; {rep['fetched']:,} new, {rep['kept']:,} kept"
                if rep["failed"]:
                    result[key] = (f"incomplete ({n:,} tiles, "
                                   f"{out.stat().st_size / 1e6:,.1f} MB{note}; "
                                   f"{rep['failed']:,} tiles could not be fetched: "
                                   f"{rep['error']}) — press Update again to fetch "
                                   "only the missing tiles")
                    log(f"{pack.title}: {result[key]}")
                    continue
            else:
                log(f"{pack.title}: fetching")
                n = build_raster_pack(RASTER_SOURCES[key],
                                      bands_for(key, region, imagery_max=imagery_max),
                                      out, attribution=pack.attribution, name=pack.title,
                                      progress=progress)
            result[key] = f"ok ({n:,} tiles, {out.stat().st_size / 1e6:,.1f} MB{note})"
        except Exception as e:                     # keep the old pack
            for leftover in (out.with_suffix(".part"),):
                leftover.unlink(missing_ok=True)
            result[key] = f"failed: {redact(str(e))}"
        log(f"{pack.title}: {result[key]}")
    return result


__all__ = ["DETAIL_MAX", "DETAIL_MIN", "DETAIL_RADIUS_KM", "ESRI_DETAIL", "IRAQ", "PACKS",
           "Pack", "R5", "RASTER_SOURCES", "UpdateError", "arcgis_key", "bands_for",
           "build_raster_pack", "detail_estimate", "detail_rings", "detail_sources",
           "extract_region", "file_reader", "folder", "http_get", "installed",
           "latest_vector_build", "lonlat_to_tile", "pack_path", "pack_tiles", "plan",
           "read_header", "redact", "region_bbox", "rings_bbox", "save_arcgis_key",
           "site_points", "site_tiles", "status", "tile_url", "tiles_in", "update",
           "update_detail", "url_reader"]
