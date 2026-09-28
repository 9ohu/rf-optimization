"""Map-side visuals for the Sites page: dark Leaflet chrome, status-coloured
tower badges with an information card, and the per-site / per-sector status
behind them.

The beams, click targets, ruler and view-keeping stay exactly as they were;
this only changes how the towers look and what the map's controls look like.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
from branca.element import MacroElement
from jinja2 import Template

from _kpi_map import BAND_LABEL, KPI_BAND, threshold_rule
from _ui import ICONS

# air status when no KPI is shown. Planned reads as "not yet" (gray).
AIR = {"onair": "#1597FF", "planned": "#94A3B8", "offair": "#475569"}
AIR_LABEL = {"onair": "On air", "planned": "Planned", "offair": "Off air"}

MAP_CSS = """
.leaflet-container { background: #0B1F33; font-family: 'Segoe UI', system-ui, sans-serif; }
/* controls: zoom, home, ruler, draw — one dark toolbar family */
.leaflet-bar { border: 1px solid #1E3A5F !important; border-radius: 9px !important;
               box-shadow: 0 3px 12px rgba(0, 0, 0, .45) !important; overflow: hidden; }
.leaflet-bar a, .leaflet-bar a:hover { background-color: #0D2945 !important; color: #E2E8F0 !important;
               border-bottom-color: #1E3A5F !important; }
.leaflet-bar a:hover { background-color: #15406B !important; }
.leaflet-bar a.leaflet-disabled { background-color: #0B1F33 !important; color: #475569 !important; }
.rf-home-btn, .sm-ruler-btn { display: flex !important; align-items: center; justify-content: center;
               width: 30px; height: 30px; }
.rf-home-btn svg, .sm-ruler-btn svg { width: 16px; height: 16px; }
a.sm-ruler-btn.sm-tool-on { background-color: #d6336c !important; color: #fff !important; }
.sm-ruler-tag { background: rgba(7, 21, 37, .92); color: #fff; border: 1px solid #1E3A5F;
    font: 600 11px/1.4 'Segoe UI', system-ui, sans-serif; white-space: nowrap;
    padding: 3px 8px; border-radius: 6px; box-shadow: 0 1px 4px rgba(0, 0, 0, .35);
    transform: translate(12px, -22px); }
.sm-ruler-tag .sm-az { color: #ffd0e0; margin-left: 5px; }
/* leaflet-draw's sprite is grey-on-light: invert its toolbars, and give them
   the exact inverse of the dark tool colours so they come out matching */
.leaflet-draw-toolbar { filter: invert(1); }
.leaflet-draw-toolbar.leaflet-bar { border-color: #E1C5A0 !important;
                                    box-shadow: 0 3px 12px rgba(255, 255, 255, .45) !important; }
.leaflet-draw-toolbar a, .leaflet-draw-toolbar a:hover {
    background-color: #F2D6BA !important; border-bottom-color: #E1C5A0 !important; }
.leaflet-draw-toolbar a:hover { background-color: #EABF94 !important; }
.leaflet-draw-toolbar a.leaflet-disabled { background-color: #F4E0CC !important; opacity: .6; }
.leaflet-draw-actions a { background: #0D2945 !important; color: #E2E8F0 !important; }
.leaflet-control-attribution { background: rgba(7, 21, 37, .78) !important; color: #94A3B8 !important; }
.leaflet-control-attribution a { color: #20BFFF !important; }
.leaflet-control-scale-line { background: rgba(7, 21, 37, .72); color: #E2E8F0;
                              border-color: #94A3B8; border-width: 0 2px 2px; }
.leaflet-control-mouseposition { background: rgba(7, 21, 37, .82) !important; color: #E2E8F0 !important;
                                 border: 1px solid #1E3A5F; border-radius: 6px; padding: 2px 8px !important; }
.leaflet-tooltip { background: #0B1F33; color: #E2E8F0; border: 1px solid #1E3A5F;
                   border-radius: 7px; box-shadow: 0 3px 12px rgba(0, 0, 0, .5); font-size: 12px; }
.leaflet-tooltip-top:before { border-top-color: #1E3A5F; }
.leaflet-tooltip-bottom:before { border-bottom-color: #1E3A5F; }
.leaflet-tooltip-left:before { border-left-color: #1E3A5F; }
.leaflet-tooltip-right:before { border-right-color: #1E3A5F; }

/* tower badge: a status ring, the tower glyph, a soft glow of the same
   colour. The disc is see-through so the beams under it still read. */
.rf-tw-wrap { background: transparent; border: 0; }
.rf-tw { position: relative; }
.rf-tw-halo { position: absolute; inset: -30%; border-radius: 50%; pointer-events: none;
    background: radial-gradient(circle, color-mix(in srgb, var(--c) 38%, transparent) 0%, transparent 68%); }
.rf-tw-core { position: absolute; inset: 0; border-radius: 50%; display: flex; align-items: center;
    justify-content: center; background: rgba(7, 21, 37, .55); border: 2px solid var(--c); color: var(--c);
    box-shadow: 0 0 10px color-mix(in srgb, var(--c) 70%, transparent); cursor: pointer; }
.rf-tw-core svg { width: 58%; height: 58%; }
.rf-tw:hover .rf-tw-core { background: rgba(7, 21, 37, .85); }

/* the tower's information card */
.rf-pop .leaflet-popup-content-wrapper { background: #0B1F33; color: #E2E8F0; border: 1px solid #1E3A5F;
    border-radius: 12px; box-shadow: 0 8px 26px rgba(0, 0, 0, .55); }
.rf-pop .leaflet-popup-content { margin: 12px 14px; font: 12.5px/1.45 'Segoe UI', system-ui, sans-serif; }
.rf-pop .leaflet-popup-tip { background: #0B1F33; border: 1px solid #1E3A5F; }
.rf-pop a.leaflet-popup-close-button { color: #94A3B8; }
.rf-pop-h { display: flex; gap: 10px; align-items: center; margin-bottom: 8px; }
.rf-pop-ico { flex: 0 0 34px; height: 34px; border-radius: 50%; display: flex; align-items: center;
    justify-content: center; border: 2px solid var(--c); color: var(--c); background: #071525;
    box-shadow: 0 0 10px color-mix(in srgb, var(--c) 60%, transparent); }
.rf-pop-ico svg { width: 19px; height: 19px; }
.rf-pop-t { font-weight: 700; color: #F8FAFC; font-size: 13.5px; }
.rf-pop-s { color: #94A3B8; font-size: 11.5px; }
.rf-pop-cap { color: #20BFFF; font-size: 11.5px; font-weight: 600; margin: 0 0 4px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rf-pop-r { display: grid; grid-template-columns: 1fr auto 64px; gap: 9px; align-items: center;
    padding: 3px 0; border-top: 1px solid #16324F; }
.rf-pop-k { color: #CBD5E1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rf-pop-chip { display: inline-block; width: 8px; height: 8px; border-radius: 2px;
    margin-right: 6px; vertical-align: middle; }
.rf-pop-v { color: #F1F5F9; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
.rf-pop-bar { height: 5px; border-radius: 3px; background: #16324F; overflow: hidden; }
.rf-pop-bar i { display: block; height: 100%; border-radius: 3px; }
.rf-pop-f { display: flex; align-items: center; gap: 7px; margin-top: 8px; padding-top: 7px;
    border-top: 1px solid #1E3A5F; font-weight: 600; }
.rf-pop-f i { width: 9px; height: 9px; border-radius: 50%; display: inline-block; flex: 0 0 9px; }
"""


def band_rank(key: str) -> float:
    """How bad a band is, for picking a site's worst sector. Magnitude bands
    (a KPI with no threshold) rank by size instead."""
    key = str(key)
    if key == "critical":
        return 40
    if key == "warning":
        return 30
    if key == "ok":
        return 10
    if key.startswith("ok") and key[2:].isdigit():
        return 10 + int(key[2:])
    if key.startswith("b") and key[1:].isdigit():
        return 10 + int(key[1:])
    return 0


def worst_band_per_site(sectors: pd.DataFrame, band_of: dict) -> dict[str, str]:
    """A site shows its worst sector — the one an engineer would open first."""
    if sectors.empty:
        return {}
    d = pd.DataFrame({"site_id": sectors["site_id"].astype(str).to_numpy(),
                      "band": [band_of.get(s, "none") for s in sectors["sector_id"]]})
    d["rank"] = d["band"].map(band_rank)
    idx = d.groupby("site_id")["rank"].idxmax()
    return dict(zip(d.loc[idx, "site_id"], d.loc[idx, "band"]))


def fmt_value(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "no data"
    if not np.isfinite(v):
        return "no data"
    if abs(v) >= 1e5:
        return f"{v:,.0f}"
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def worst_sectors(draw: pd.DataFrame, band_of: dict, value_of: dict, kpi: str,
                  n: int = 6) -> pd.DataFrame:
    """The sectors to open first for this KPI: worst band, then worst value.

    With a threshold, "worst" follows its direction (low availability, high
    PRB). Without one there is no bad end, so this is simply the highest.
    """
    rule = threshold_rule(kpi)
    up = rule is not None and rule.direction == "up"
    d = pd.DataFrame({"sector_id": draw["sector_id"].astype(str).to_numpy(),
                      "band": [band_of.get(x, "none") for x in draw["sector_id"]],
                      "value": [value_of.get(x, np.nan) for x in draw["sector_id"]]})
    d["value"] = pd.to_numeric(d["value"], errors="coerce")
    d = d[d["band"].ne("none") & d["value"].notna()].drop_duplicates("sector_id")
    d["rank"] = d["band"].map(band_rank)
    return (d.sort_values(["rank", "value"], ascending=[False, up])
            .head(n).reset_index(drop=True))


def tower_points(draw: pd.DataFrame, sites: pd.DataFrame, *,
                 kpi: str | None = None, band_of: dict | None = None,
                 value_of: dict | None = None, colour_of: dict | None = None,
                 spec: list | tuple = (), index_of: dict | None = None) -> list:
    """One badge per drawn site: [lat, lon, id, name, colour, status, sub, rows,
    caption, sector indices, sector ids, [k, n]].

    [k, n]: the site is the k-th of n sites at exactly the same position (the
    KMZ has such pairs); the map sets them side by side instead of one badge
    hiding the other.

    With a KPI the badge takes its worst sector's colour and the card lists
    every sector's value; without one it shows the site's air status and each
    sector's azimuth. A row is [label, value, bar % or None, colour].
    """
    if draw.empty:
        return []
    band_of, value_of = band_of or {}, value_of or {}
    colour_of = colour_of or {}
    text_of = {k: t for k, _, t in spec}
    worst: dict = {}
    lo = hi = 0.0
    if kpi:
        vals = pd.to_numeric(pd.Series([value_of.get(s) for s in draw["sector_id"]],
                                       dtype="float64"), errors="coerce")
        if vals.notna().any():
            lo, hi = float(vals.min()), float(vals.max())
        worst = worst_band_per_site(draw, band_of)

    rows_of: dict[str, list] = {}
    # each site's sectors by their place among the drawn ones, so the map
    # can recolour a tower for the hour on the time slider and open its
    # worst sector
    idx_of: dict[str, list] = {}
    for r in draw.sort_values(["site_id", "sector"]).itertuples(index=False):
        label = f"S{int(r.sector)}" if pd.notna(r.sector) else str(r.sector_id)
        if kpi:
            b = band_of.get(r.sector_id, "none")
            v = value_of.get(r.sector_id)
            pct = None
            if v is not None and np.isfinite(v):
                pct = 100.0 if hi == lo else round((v - lo) / (hi - lo) * 100, 1)
            row = [label, fmt_value(v), pct, colour_of.get(b, KPI_BAND["none"])]
        else:
            txt = (f"az {r.azimuth_deg:.0f}°" if pd.notna(r.azimuth_deg)
                   and np.isfinite(r.azimuth_deg) else "az –")
            if pd.notna(r.ret_deg) and np.isfinite(r.ret_deg):
                txt += f" · RET {r.ret_deg:.1f}°"
            row = [label, txt, None, AIR.get(r.air, AIR["onair"])]
        rows_of.setdefault(str(r.site_id), []).append(row)
        at = (index_of or {}).get(r.sector_id)
        if at is not None:
            real = pd.notna(r.azimuth_deg) and np.isfinite(r.azimuth_deg)
            idx_of.setdefault(str(r.site_id), []).append(
                (int(at), str(r.sector_id), bool(real)))

    pts = []
    shown = sites[sites["site_id"].astype(str).isin(rows_of)]
    at = (shown["latitude"].astype(float).round(6).astype(str) + ","
          + shown["longitude"].astype(float).round(6).astype(str))
    place_k = at.groupby(at).cumcount().to_numpy()
    place_n = at.map(at.value_counts()).to_numpy()
    for j, s in enumerate(shown.itertuples(index=False)):
        sid = str(s.site_id)
        name = str(s.site_name).strip() if pd.notna(s.site_name) else ""
        if kpi:
            b = worst.get(sid, "none")
            colour = colour_of.get(b, KPI_BAND["none"])
            label = BAND_LABEL.get(b) or ("OK" if b.startswith("ok") else "")
            if b == "none":
                status = "No data for this KPI"
            elif label:
                status = f"{label} · worst sector {text_of.get(b, '')}".strip()
            else:
                status = f"Highest sector {text_of.get(b, '')}".strip()
            caption = kpi
        else:
            colour = AIR.get(s.air, AIR["onair"])
            status = AIR_LABEL.get(s.air, str(s.status))
            caption = ""
        sub = [f"{int(s.sectors)} sectors"]
        for tech, col in (("4G", "n_4g"), ("3G", "n_3g"), ("2G", "n_2g")):
            n = pd.to_numeric(getattr(s, col), errors="coerce")
            if pd.notna(n) and n > 0:
                sub.append(f"{tech} {int(n)}")
        if pd.notna(s.height) and np.isfinite(s.height):
            sub.append(f"h {s.height:.0f} m")
        rows = list(rows_of[sid])
        # a tower opens a physical sector: the KMZ's empty sector-0 placemarks
        # (no azimuth) only count when a site has nothing else
        pairs = idx_of.get(sid, [])
        pairs = [x for x in pairs if x[2]] or pairs
        seen: set = set()
        pairs = [x for x in pairs if not (x[1] in seen or seen.add(x[1]))]
        pts.append([round(float(s.latitude), 6), round(float(s.longitude), 6), sid,
                    name or sid, colour, status, " · ".join(sub), rows, caption,
                    [x[0] for x in pairs], [x[1] for x in pairs],
                    [int(place_k[j]), int(place_n[j])]])
    return pts


class TowerMarkers(MacroElement):
    """Status-coloured tower badges, client-side.

    Zoomed out every site is a small dot on one canvas. The dots are built once
    and only restyled when the zoom changes their size (rebuilding 1,700 of them
    on every pan was work the map did while you dragged it). From `min_zoom` the
    sites in view are badges that open their information card, growing a little
    with the zoom; badges already on the map are kept on pan, so an open card
    stays open. More than `CAP` sites in view: the ones nearest the centre get
    badges and the rest stay dots — a site is never left off the map. Sites at
    exactly the same position are set side by side.
    """
    _template = Template("""
        {% macro script(this, kwargs) %}
        (function () {
          var m = {{ this._parent.get_name() }};
          var pts = {{ this.points }};
          var MINZ = {{ this.min_zoom }}, CAP = 600, SVG = {{ this.svg }};
          var RF = window.RF = window.RF || {};
          var canvas = L.canvas({padding: 0.4});
          var dots = L.layerGroup().addTo(m), spill = L.layerGroup().addTo(m);
          var towers = L.layerGroup().addTo(m);
          var live = {}, liveSize = 0, dotMk = [], dotsOn = false, dotRad = -1, timer = null;
          function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g,
            function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;',
                                   '"': '&quot;', "'": '&#39;'}[c]; }); }
          function sizeFor(z) { return z >= 16 ? 30 : (z >= 15 ? 26 : 22); }
          function radFor(z) { return z >= 12 ? 3.4 : (z >= 10 ? 2.6 : 2); }
          // p[9]: the site's sector indices, p[10]: their ids. With a KPI on
          // the map, the colour and status follow the hour on the slider.
          function colourOf(p) {
            return (RF.siteColour && p[9] && p[9].length) ? (RF.siteColour(p[9]) || p[4]) : p[4];
          }
          function statusOf(p) {
            return (RF.siteStatus && p[9] && p[9].length) ? (RF.siteStatus(p[9]) || p[5]) : p[5];
          }
          function dotStyle(mk, p, rad) {
            var sel = RF.selSite === p[2];
            mk.setStyle({color: sel ? '#F8FAFC' : '#071525', weight: sel ? 2.5 : 1,
                         fillColor: colourOf(p)});
            mk.setRadius(sel ? rad + 3 : rad);
          }
          function dot(p, rad) {
            var mk = L.circleMarker([p[0], p[1]], {renderer: canvas, radius: rad, weight: 1,
              color: '#071525', fillColor: colourOf(p), fillOpacity: 0.95, interactive: false});
            dotStyle(mk, p, rad);
            return mk;
          }
          function paint(mk) {
            var el = mk._icon && mk._icon.firstChild;
            if (!el) return;
            el.style.setProperty('--c', colourOf(mk.__p));
            el.classList.toggle('rf-tw-sel', RF.selSite === mk.__p[2]);
          }
          RF.repaintTowers = function () {
            if (m.getZoom() < MINZ) {
              for (var i = 0; i < dotMk.length; i++) dotStyle(dotMk[i], pts[i], dotRad);
              return;
            }
            for (var id in live) paint(live[id]);
            spill.eachLayer(function (mk) { dotStyle(mk, mk.__p, radFor(m.getZoom())); });
          };
          function refresh() {
            var z = m.getZoom(), i, p;
            if (z < MINZ) {
              towers.clearLayers(); spill.clearLayers(); live = {}; liveSize = 0;
              var rad = radFor(z);
              if (!dotMk.length) for (i = 0; i < pts.length; i++) dotMk.push(dot(pts[i], rad));
              if (!dotsOn) { for (i = 0; i < dotMk.length; i++) dots.addLayer(dotMk[i]); dotsOn = true; }
              if (rad !== dotRad) {
                for (i = 0; i < dotMk.length; i++) dotStyle(dotMk[i], pts[i], rad);
                dotRad = rad;
              }
              return;
            }
            if (dotsOn) { dots.clearLayers(); dotsOn = false; }
            var b = m.getBounds().pad(0.15), c = m.getCenter(), inView = [];
            for (i = 0; i < pts.length; i++) {
              p = pts[i];
              if (!b.contains([p[0], p[1]])) continue;
              var dy = p[0] - c.lat, dx = p[1] - c.lng;
              inView.push([dx * dx + dy * dy, i]);
            }
            if (inView.length > CAP) inView.sort(function (a, b) { return a[0] - b[0]; });
            var size = sizeFor(z);
            if (size !== liveSize) { towers.clearLayers(); live = {}; liveSize = size; }
            var keep = {};
            spill.clearLayers();
            for (var k = 0; k < inView.length; k++) {
              p = pts[inView[k][1]];
              if (k >= CAP) { var sp = dot(p, radFor(z)); sp.__p = p; spill.addLayer(sp); continue; }
              keep[p[2]] = 1;
              if (live[p[2]]) { paint(live[p[2]]); continue; }
              var off = p[11] && p[11][1] > 1 ? (p[11][0] - (p[11][1] - 1) / 2) * (size + 4) : 0;
              var mk = L.marker([p[0], p[1]], {keyboard: false, riseOnHover: true,
                icon: L.divIcon({className: 'rf-tw-wrap', iconSize: [size, size],
                  iconAnchor: [size / 2 - off, size / 2],
                  html: '<div class="rf-tw" style="width:' + size + 'px;height:'
                    + size + 'px"><span class="rf-tw-halo"></span>'
                    + '<span class="rf-tw-core">' + SVG + '</span></div>'})});
              mk.__p = p;
              // a tower opens its site in the Sector Details drawer
              mk.bindTooltip(function (layer) {
                var q = layer.__p;
                return '<b>' + esc(q[3]) + '</b> · ' + esc(q[2]) + '<br>'
                  + esc(statusOf(q));
              }, {direction: 'top', offset: [off, -size / 2]});
              mk.on('click', function (e) {
                if (RF.openSite) RF.openSite(this.__p[2], this.__p[9] || [],
                                             this.__p[10] || [], e.latlng);
              });
              mk.on('add', function () { paint(this); });
              towers.addLayer(mk); live[p[2]] = mk;
            }
            for (var id in live) {
              if (!keep[id]) { towers.removeLayer(live[id]); delete live[id]; }
            }
          }
          // a zoom fires moveend and zoomend: one refresh for both
          function later() { clearTimeout(timer); timer = setTimeout(refresh, 40); }
          m.on('moveend zoomend', later);
          m.whenReady(function () { setTimeout(refresh, 120); });
        })();
        {% endmacro %}
    """)

    def __init__(self, points: list, min_zoom: int = 14):
        super().__init__()
        self._name = "TowerMarkers"
        # the card rows and caption stay in Python: the drawer shows them
        self.points = json.dumps([p[:7] + [[], ""] + p[9:] for p in points])
        self.min_zoom = int(min_zoom)
        self.svg = json.dumps(ICONS["tower"])


class HomeView(MacroElement):
    """A map button under the zoom buttons: back to the whole region."""
    _template = Template("""
        {% macro script(this, kwargs) %}
        (function () {
          var m = {{ this._parent.get_name() }};
          var Home = L.Control.extend({
            options: {position: 'topleft'},
            onAdd: function () {
              var box = L.DomUtil.create('div', 'leaflet-bar leaflet-control');
              var a = L.DomUtil.create('a', 'rf-home-btn', box);
              a.href = '#';
              a.title = 'Back to the whole region';
              a.innerHTML = {{ this.svg }};
              L.DomEvent.on(a, 'click', L.DomEvent.stop).on(a, 'click', function () {
                m.setView([{{ this.lat }}, {{ this.lon }}], {{ this.zoom }});
              });
              L.DomEvent.disableClickPropagation(box);
              return box;
            }
          });
          new Home().addTo(m);
        })();
        {% endmacro %}
    """)

    def __init__(self, lat: float, lon: float, zoom: int):
        super().__init__()
        self._name = "HomeView"
        self.lat, self.lon, self.zoom = float(lat), float(lon), int(zoom)
        self.svg = json.dumps(ICONS["target"])


class SiteLabels(MacroElement):
    """Site-name labels, client-side: only the sites in view, only zoomed in.

    Avoids re-rendering 1,600 markers on every Streamlit run and the one-frame
    lag of doing the in-view filter in Python.  Anchored high enough to clear
    the tower badge underneath.
    """
    _template = Template("""
        {% macro script(this, kwargs) %}
        (function () {
          var m = {{ this._parent.get_name() }};
          var pts = {{ this.points }};
          var MINZ = {{ this.min_zoom }}, CAP = 400, live = {}, timer = null;
          var grp = L.layerGroup().addTo(m);
          function esc(s){ return String(s).replace(/[&<>"]/g, function(c){
            return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
          function refresh() {
            if (m.getZoom() < MINZ) { grp.clearLayers(); live = {}; return; }
            var b = m.getBounds(), c = m.getCenter(), inView = [], keep = {};
            for (var i = 0; i < pts.length; i++) {
              if (!b.contains([pts[i][0], pts[i][1]])) continue;
              var dy = pts[i][0] - c.lat, dx = pts[i][1] - c.lng;
              inView.push([dx * dx + dy * dy, i]);
            }
            if (inView.length > CAP) {
              inView.sort(function (a, b) { return a[0] - b[0]; });
              inView.length = CAP;
            }
            for (var k = 0; k < inView.length; k++) {
              var j = inView[k][1], p = pts[j];
              keep[j] = 1;
              if (live[j]) continue;
              // sites at the very same position: one name under the other
              var down = (p[3] || 0) * 14;
              live[j] = L.marker([p[0], p[1]], {
                interactive: false, keyboard: false,
                icon: L.divIcon({className: 'sm-lbl', iconSize: [190, 16],
                  iconAnchor: [95, {{ this.anchor_y }} - down],
                  html: '<div style="font:700 11px system-ui;color:{{ this.fg }};'
                    + 'text-align:center;white-space:nowrap;pointer-events:none;'
                    + 'text-shadow:0 0 3px {{ this.halo }},0 0 3px {{ this.halo }}'
                    + ',0 0 3px {{ this.halo }}">' + esc(p[2]) + '</div>'})
              });
              grp.addLayer(live[j]);
            }
            for (var id in live) {
              if (!keep[id]) { grp.removeLayer(live[id]); delete live[id]; }
            }
          }
          function later() { clearTimeout(timer); timer = setTimeout(refresh, 60); }
          m.on('moveend zoomend', later);
          m.whenReady(function () { setTimeout(refresh, 150); });
        })();
        {% endmacro %}
    """)

    def __init__(self, points, fg="#111", halo="#fff", min_zoom=13, anchor_y=23):
        super().__init__()
        self._name = "SiteLabels"
        self.points = json.dumps(points)
        self.fg = fg
        self.halo = halo
        self.min_zoom = int(min_zoom)
        self.anchor_y = int(anchor_y)
