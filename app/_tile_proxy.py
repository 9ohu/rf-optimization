"""A local relay for the basemap tiles, for when the browser cannot reach them.

On the GlobalProtect VPN the maps came up without their roads, place names and
terrain: the browser's own requests to the tile hosts (Esri, OpenStreetMap)
were blocked, dropped or broken by the tunnel's TLS inspection, while the app
itself kept working. This relay runs inside the app, on 127.0.0.1, and fetches
a tile on the browser's behalf — through the operating system's proxy
settings, trusting the Windows certificate store when the `truststore` package
is installed (the corporate root the VPN re-signs with lives there) — and keeps
every tile it fetched on disk, so an area seen once opens even offline.

The map still asks the tile hosts directly first; `_map_assets.TileGuard`
moves a layer to the relay only when its direct tiles keep failing. Only the
tile services the maps use can be fetched: it is not an open proxy.

The relay also serves the offline basemap packs (`rfopt.geo.offline_basemap`)
to the Sites map: `/pm/<pack file>`, read from the local pack folder with byte
ranges (what the PMTiles reader in the browser asks for). That path never goes
to the Internet.
"""

from __future__ import annotations

import hashlib
import re
import ssl
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# the hosts a tile may come from; the first that answers wins
_ESRI = ("https://server.arcgisonline.com/ArcGIS/rest/services",
         "https://services.arcgisonline.com/ArcGIS/rest/services")
_OSM = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
_UA = "RF-Optimizer tile relay (+https://www.openstreetmap.org/copyright)"
_TILE = re.compile(r"^/t/([0-9a-f]{12})/(\d{1,2})/(\d{1,7})/(\d{1,7})$")
_PACK = re.compile(r"^/pm/([a-z0-9_]{1,40}\.pmtiles)$")
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")

_lock = threading.Lock()
_server: ThreadingHTTPServer | None = None
_routes: dict[str, list[str]] = {}          # key -> upstream templates ({z} {y} {x})


def upstreams(template: str) -> list[str]:
    """The upstream templates a map tile URL may be relayed from, or [] when
    it is not a tile service the maps use."""
    t = str(template)
    if t == "Esri.WorldImagery":
        t = _ESRI[0] + "/World_Imagery/MapServer/tile/{z}/{y}/{x}"
    if t in ("OpenStreetMap", "OpenStreetMap.Mapnik") or t.startswith(
            ("https://tile.openstreetmap.org/", "https://{s}.tile.openstreetmap.org/")):
        return [_OSM]
    for host in _ESRI:
        if t.startswith(host):
            path = t[len(host):]
            return [h + path for h in _ESRI]
    return []


def _ssl_context() -> ssl.SSLContext:
    try:
        import truststore                  # the OS certificate store (Windows / macOS)
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        return ssl.create_default_context()


def _cache_dir() -> Path:
    from rfopt.resources.store import root
    return root() / "tiles"


def fetch(key: str, z: int, y: int, x: int, *, timeout: float = 12.0) -> bytes | None:
    """One tile: from the disk cache, else from the first upstream that has it."""
    templates = _routes.get(key)
    if not templates:
        return None
    path = _cache_dir() / key / str(z) / str(y) / f"{x}.tile"
    if path.exists():
        try:
            return path.read_bytes()
        except OSError:
            pass
    ctx = _ssl_context()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler(),              # the OS / environment proxy
        urllib.request.HTTPSHandler(context=ctx))
    for tpl in templates:
        url = tpl.replace("{z}", str(z)).replace("{y}", str(y)).replace("{x}", str(x))
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        for _ in range(2):
            try:
                with opener.open(req, timeout=timeout) as r:
                    if r.status != 200:
                        break
                    data = r.read()
                if data:
                    try:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        tmp = path.with_suffix(".part")
                        tmp.write_bytes(data)
                        tmp.replace(path)
                    except OSError:
                        pass
                    return data
            except Exception:
                continue
    return None


def _pack_file(name: str) -> Path | None:
    """A local pack by its file name — only the packs the maps know."""
    from rfopt.geo.offline_basemap import PACKS, folder
    if name not in {p.file for p in PACKS.values()}:
        return None
    path = folder() / name
    return path if path.is_file() else None


class _Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers",
                         "Content-Range, Content-Length, ETag, Accept-Ranges")

    def do_OPTIONS(self):  # noqa: N802 - a browser's preflight (Range, private network)
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range, If-Match, Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def _send_pack(self, path: Path, head_only: bool = False):
        """A local pack, whole or the byte range asked for."""
        size = path.stat().st_size
        etag = f'"{int(path.stat().st_mtime)}-{size}"'
        rng = _RANGE.match((self.headers.get("Range") or "").strip())
        start, end = 0, size - 1
        if rng and (rng.group(1) or rng.group(2)):
            if rng.group(1):
                start = int(rng.group(1))
                end = min(int(rng.group(2)), size - 1) if rng.group(2) else size - 1
            else:                                  # the last n bytes
                start = max(0, size - int(rng.group(2)))
            if start >= size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self._cors()
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        length = end - start + 1
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "no-cache")
        self._cors()
        self.end_headers()
        if head_only:
            return
        with open(path, "rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(left, 1 << 20))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def do_HEAD(self):  # noqa: N802
        pm = _PACK.match(self.path.split("?", 1)[0])
        path = _pack_file(pm.group(1)) if pm else None
        if path is None:
            self.send_response(404)
            self._cors()
            self.end_headers()
            return
        self._send_pack(path, head_only=True)

    def do_GET(self):  # noqa: N802 - the http.server hook
        pm = _PACK.match(self.path.split("?", 1)[0])
        if pm:
            path = _pack_file(pm.group(1))
            if path is None:
                self.send_response(404)
                self._cors()
                self.end_headers()
                return
            try:
                self._send_pack(path)
            except (BrokenPipeError, ConnectionResetError):
                pass                               # the map moved on
            return
        m = _TILE.match(self.path.split("?", 1)[0])
        data = fetch(m.group(1), *(int(g) for g in m.groups()[1:])) if m else None
        if data is None:
            self.send_response(404)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return
        kind = ("image/png" if data[:4] == b"\x89PNG" else
                "image/jpeg" if data[:2] == b"\xff\xd8" else "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=604800")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):          # quiet
        pass


def _start() -> ThreadingHTTPServer | None:
    global _server
    with _lock:
        if _server is None:
            try:
                _server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
                _server.daemon_threads = True
            except OSError:
                return None
            threading.Thread(target=_server.serve_forever, name="rf-tile-relay",
                             daemon=True).start()
        return _server


def packs_url() -> str | None:
    """The relay's base URL for the offline map packs (`<base>/<pack file>`),
    starting the relay the first time; None when it cannot start."""
    srv = _start()
    if srv is None:
        return None
    return f"http://127.0.0.1:{srv.server_address[1]}/pm/"


def relay_url(template: str) -> str | None:
    """The relay's URL template for a map's tile URL, starting the relay the
    first time; None for a tile source the relay does not serve."""
    ups = upstreams(template)
    if not ups:
        return None
    srv = _start()
    if srv is None:
        return None
    key = hashlib.sha1("|".join(ups).encode()).hexdigest()[:12]
    _routes[key] = ups
    return f"http://127.0.0.1:{srv.server_address[1]}/t/{key}/{{z}}/{{y}}/{{x}}"


__all__ = ["fetch", "packs_url", "relay_url", "upstreams"]
