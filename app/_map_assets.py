"""What the app's Leaflet maps load, and how their basemap tiles behave.

Why the Sites map was slow on VPN, and turned into dark squares while zooming:

* folium's page head loads its libraries from six CDN hosts — 14 files, most of
  them (Bootstrap, jQuery, Font Awesome, glyphicons, awesome-markers) unused on
  these maps. The map cannot start until every one of them answers; on a VPN
  each new host costs a DNS lookup and a TCP + TLS handshake through the
  tunnel, and one slow host holds the whole map back. `use_local_libraries`
  serves Leaflet and Leaflet.draw from the app itself (`app/static/vendor`,
  Streamlit static serving) and drops the rest.
* Leaflet asks for the tiles of every zoom level a wheel or pinch passes
  through and abandons them; on a slow link the tiles of the level you stop on
  queue behind them, and until they arrive the map shows its bare background.
  `add_basemap` loads a zoom's tiles once the zoom ends, keeps more tiles loaded
  around the view for panning, and lays a low-resolution copy of the same
  basemap underneath (a handful of zoom-10 tiles, cached after the first view),
  so an area whose sharp tiles are still on their way is drawn soft, not empty.
* A tile that failed (a dropped packet on the VPN) stayed blank until the map
  was rebuilt. `TileGuard` retries it, backing off, and shows a small "loading
  basemap" note while tiles are on their way; the map stays usable meanwhile.
* On GlobalProtect the browser's own requests to the tile hosts could fail
  outright, leaving the map without roads, names or terrain. Each basemap and
  overlay carries a `fallback` URL — the app's local tile relay
  (`_tile_proxy`), which fetches through the system proxy and the Windows
  certificate store and caches on disk — and `TileGuard` moves a layer to it
  once its direct tiles keep failing.
"""

from __future__ import annotations

import json
from pathlib import Path

import folium
from branca.element import MacroElement
from jinja2 import Template

VENDOR = Path(__file__).resolve().parent / "static" / "vendor"
LOCAL = "/app/static/vendor"
_FILES = {"leaflet_js": "leaflet/leaflet.js", "leaflet_css": "leaflet/leaflet.css",
          "draw_js": "leaflet.draw/leaflet.draw.js",
          "draw_css": "leaflet.draw/leaflet.draw.css"}
_CDN = {"leaflet_js": "https://cdn.jsdelivr.net/npm/leaflet@1.9.3/dist/leaflet.js",
        "leaflet_css": "https://cdn.jsdelivr.net/npm/leaflet@1.9.3/dist/leaflet.css",
        "draw_js": "https://cdnjs.cloudflare.com/ajax/libs/leaflet.draw/1.0.2/leaflet.draw.js",
        "draw_css": "https://cdnjs.cloudflare.com/ajax/libs/leaflet.draw/1.0.2/leaflet.draw.css"}
UNDERLAY_ZOOM = 10


def served_locally() -> bool:
    """The vendored libraries are on disk and the app serves its static folder."""
    try:
        import streamlit as st
        on = bool(st.get_option("server.enableStaticServing"))
    except Exception:
        on = False
    return on and all((VENDOR / f).exists() for f in _FILES.values())


def library_urls() -> dict:
    local = served_locally()
    return {k: (f"{LOCAL}/{f}" if local else _CDN[k]) for k, f in _FILES.items()}


def use_local_libraries(fmap: folium.Map, draw=None) -> None:
    """Leaflet (and Leaflet.draw) only — from the app when it serves them."""
    urls = library_urls()
    fmap.default_js = [("leaflet", urls["leaflet_js"])]
    fmap.default_css = [("leaflet_css", urls["leaflet_css"])]
    if draw is not None:
        draw.default_js = [("leaflet_draw_js", urls["draw_js"])]
        draw.default_css = [("leaflet_draw_css", urls["draw_css"])]


def _tile_kw(max_zoom: int, native_zoom: int | None, attr: str | None,
             tiles: str | None = None) -> dict:
    kw = dict(max_zoom=int(max_zoom), max_native_zoom=int(native_zoom or max_zoom),
              control=False, update_when_zooming=False)
    if attr:
        kw["attr"] = attr
    fallback = _fallback(tiles) if tiles else None
    if fallback:
        kw["fallback"] = fallback           # read by TileGuard: the local tile relay
    return kw


def _fallback(tiles: str) -> str | None:
    try:
        from _tile_proxy import relay_url
        return relay_url(tiles)
    except Exception:
        return None


def add_basemap(fmap: folium.Map, tiles: str, *, attr: str | None = None,
                max_zoom: int = 19, native_zoom: int | None = None) -> None:
    """The basemap over a low-resolution copy of itself."""
    native = int(native_zoom or max_zoom)
    folium.TileLayer(tiles, name="basemap-underlay", keep_buffer=1, z_index=0,
                     class_name="rf-underlay",
                     **_tile_kw(max_zoom, min(UNDERLAY_ZOOM, native), attr, tiles)).add_to(fmap)
    folium.TileLayer(tiles, name="basemap", keep_buffer=4, z_index=1,
                     **_tile_kw(max_zoom, native, attr, tiles)).add_to(fmap)


def add_overlay(fmap: folium.Map, tiles: str, *, name: str, attr: str = "Esri",
                max_zoom: int = 19, native_zoom: int | None = None, pane: str | None = None):
    kw = _tile_kw(max_zoom, native_zoom, attr, tiles)
    if pane:
        kw["pane"] = pane
    return folium.TileLayer(tiles, name=name, overlay=True, keep_buffer=4, z_index=2,
                            **kw).add_to(fmap)


class TileGuard(MacroElement):
    """Retry a failed tile, and say so while the basemap is still loading."""
    _template = Template("""
        {% macro header(this, kwargs) %}
        <style>
        .rf-tiles { background: rgba(7, 21, 37, .88); color: #CBD5E1; border: 1px solid #1E3A5F;
                    border-radius: 7px; padding: 2px 9px; font: 600 11px 'Segoe UI', system-ui, sans-serif;
                    box-shadow: 0 2px 8px rgba(0, 0, 0, .4); pointer-events: none; }
        .rf-tiles i { display: inline-block; width: 7px; height: 7px; border-radius: 50%;
                      background: #20BFFF; margin-right: 6px; animation: rfPulse 1s infinite alternate; }
        @keyframes rfPulse { from { opacity: .3 } to { opacity: 1 } }
        </style>
        {% endmacro %}
        {% macro script(this, kwargs) %}
        (function () {
          var m = {{ this._parent.get_name() }};
          var RETRIES = {{ this.retries }}, FAILS = {{ this.fails }};
          var waiting = {}, n = 0, shownAt = 0, timer = null;
          var note = L.control({position: 'bottomright'});
          note.onAdd = function () {
            var d = L.DomUtil.create('div', 'rf-tiles');
            d.style.display = 'none';
            return d;
          };
          note.addTo(m);
          var el = note.getContainer();
          function sync() {
            clearTimeout(timer);
            timer = setTimeout(function () {
              el.style.display = n > 0 ? '' : 'none';
              if (n > 0) el.innerHTML = '<i></i>Loading basemap · ' + n + ' tile' + (n === 1 ? '' : 's');
            }, n > 0 ? 400 : 120);
          }
          function add(id) { if (!waiting[id]) { waiting[id] = 1; n++; sync(); } }
          function drop(id) { if (waiting[id]) { delete waiting[id]; n--; sync(); } }
          function watch(layer) {
            if (!(layer instanceof L.TileLayer) || layer.__rfGuard) return;
            layer.__rfGuard = true;
            var tag = L.stamp(layer) + ':';
            function id(e) { return tag + layer._tileCoordsToKey(e.coords); }
            layer.__rfOk = 0; layer.__rfErr = 0;
            layer.on('tileloadstart', function (e) { e.tile.__rfTry = 0; add(id(e)); });
            layer.on('tileload', function (e) { layer.__rfOk++; drop(id(e)); });
            layer.on('tileunload', function (e) { drop(id(e)); });
            layer.on('tileerror', function (e) {
              var t = e.tile, key = id(e), k = (t.__rfTry || 0) + 1;
              layer.__rfErr++;
              // the tile host keeps failing from this browser (a VPN that blocks
              // or re-signs it): move the layer to the app's local tile relay
              var fb = layer.options.fallback;
              if (fb && !layer.__rfRelay && layer.__rfErr >= FAILS
                  && layer.__rfErr > 2 * layer.__rfOk) {
                layer.__rfRelay = true;
                layer.__rfOk = 0; layer.__rfErr = 0;
                waiting = {}; n = 0; sync();
                layer.setUrl(fb);
                return;
              }
              if (k > RETRIES) { drop(key); return; }
              t.__rfTry = k;
              var url = layer.getTileUrl(e.coords);
              setTimeout(function () {
                if (!layer._map || !t.parentNode) { drop(key); return; }
                t.src = url + (url.indexOf('?') < 0 ? '?' : '&') + 'rfretry=' + k;
              }, 700 * k * k);
            });
          }
          m.eachLayer(watch);
          m.on('layeradd', function (e) { watch(e.layer); });
        })();
        {% endmacro %}
    """)

    def __init__(self, retries: int = 3, fails: int = 4):
        super().__init__()
        self._name = "TileGuard"
        self.retries = int(retries)
        self.fails = int(fails)             # direct failures before the relay


class MousePositionControl(MacroElement):
    """The cursor's latitude / longitude, bottom-left — no plugin to fetch."""
    _template = Template("""
        {% macro header(this, kwargs) %}
        <style>
        .leaflet-control-mouseposition { font: 11px/1.5 'Segoe UI', system-ui, sans-serif;
                                         margin: 0 0 6px 8px !important; }
        </style>
        {% endmacro %}
        {% macro script(this, kwargs) %}
        (function () {
          var m = {{ this._parent.get_name() }};
          var c = L.control({position: 'bottomleft'});
          c.onAdd = function () {
            var d = L.DomUtil.create('div', 'leaflet-control-mouseposition');
            d.textContent = {{ this.prefix }};
            return d;
          };
          c.addTo(m);
          var el = c.getContainer();
          m.on('mousemove', function (e) {
            el.textContent = {{ this.prefix }} + ' ' + e.latlng.lat.toFixed({{ this.digits }})
              + ' , ' + e.latlng.lng.toFixed({{ this.digits }});
          });
        })();
        {% endmacro %}
    """)

    def __init__(self, prefix: str = "lat, lon:", digits: int = 5):
        super().__init__()
        self._name = "MousePositionControl"
        self.prefix = json.dumps(prefix)
        self.digits = int(digits)


def pin_icon(colour: str, glyph: str = "") -> folium.DivIcon:
    """A map pin drawn in SVG — no icon font or marker plugin to download."""
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="32" viewBox="0 0 24 32">'
           f'<path d="M12 1C5.9 1 1 5.8 1 11.8 1 20.4 12 31 12 31s11-10.6 11-19.2C23 5.8 18.1 1 12 1z" '
           f'fill="{colour}" stroke="#0B1F33" stroke-width="1.5"/>'
           '<circle cx="12" cy="11.5" r="4.2" fill="#0B1F33"/>'
           + glyph + "</svg>")
    return folium.DivIcon(html=svg, icon_size=(24, 32), icon_anchor=(12, 31), class_name="rf-pin")
