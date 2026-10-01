"""Sites — every R5 tower & sector on a Leaflet map (folium).

Data sources (all of them applied in Data Resources): the Google-Earth
**R5_Sites.kmz** and the weekly **Engineering Parameter tracker** (.xlsx),
which adds the per-cell EP details to the sector panel and feeds the Macro /
Micro / Indoor topology filter. Both remember their upload across restarts. The hourly KPI exports
(4G, 3G and 2G together — sidebar → KPI analysis) colour the sectors by one
KPI; the Technology picked decides whose KPIs are offered.

The page reads top to bottom: the header search (a site, a sector, or a
complaint's lat, lon), status cards, the map with its legend and layer
controls beside it, then information panels and the cell detail a beam click
opens. On the map: status-coloured tower badges (click one for its card), the
Google-Earth-style tools at the top-right (📏 ruler — distance + azimuth, live,
/ ⭕ draw circle / 📍 marker with live lat / lon + copy / 🗑 delete) and the full-screen toggle.

Full-screen is a Streamlit-level "pseudo" full-screen (`sm_fs` session flag +
`_FS_CSS`) that blows the map wrapper up to the viewport — the search and the
layer controls float over the map there, which the Leaflet full-screen plugin
can't do.
"""

from __future__ import annotations

import base64
import hashlib
import html as _html
import json
import math
import re

import folium
import numpy as np
import pandas as pd
import streamlit as st
from branca.element import MacroElement
from folium.map import CustomPane
from folium.plugins import Draw
from jinja2 import Template
from streamlit_folium import st_folium

from rfopt.actions.geometry import (angular_offset_deg, bearing_deg as _brg,
                                    haversine_m as _hav)
from rfopt.ingest.hourly_kpi import KpiFileInfo
from rfopt.ingest.site_status import apply_ep_status as _apply_ep_status
from _kpi_map import (KPI_BAND as _KPI_BAND,
                      kpi_choices as _kpi_choices,
                      apply_scheme as _apply_scheme,
                      band_scheme as _band_scheme,
                      sector_values as _sector_values)
from _map_ui import (AIR as _AIR, AIR_LABEL as _AIR_LABEL,
                     MAP_CSS as _LEAFLET_CSS, HomeView as _HomeView,
                     SiteLabels as _SiteLabels, TowerMarkers as _TowerMarkers,
                     tower_points as _tower_points)
from _coverage import Coverage as _Coverage, CoverageLayer as _CoverageLayer
from _location_pick import LocationPick as _LocationPick
from _kpi_time import js_json as _js_json, kpi_unit as _kpi_unit, parse_tip as _parse_tip
from _sector_drawer import (KpiTimeline as _KpiTimeline, MapAssets as _MapAssets,
                            SectorDrawer as _SectorDrawer,
                            kpi_config as _kpi_config,
                            sector_kpis as _sector_kpis_json,
                            store_key as _store_key, table_json as _table_json)
from _map_assets import (MousePositionControl as _MousePosition,
                         add_offline_basemap as _add_offline_basemap,
                         pin_icon as _pin_icon,
                         use_local_libraries as _use_local_libraries)
import _resources as R
import _complaints as _C
import _relocate as _RL
from rfopt.complaints.comment import ticket_comment as _ticket_comment
from rfopt.complaints.relocate import lead_check as _lead_check, parse_latlon as _parse_loc
from _ui import (card as _card, file_status as _file_status, header as _header,
                 swatch as _swatch, title_html as _title_html)

_M_PER_DEG = 111_320.0
_MEAS = "#d6336c"        # draw / measure accent

_NO_KPI = "— none —"     # the KPI picker's "colour by air status" entry

# the tracker's own topology vocabulary (its "Is Outdoor" column)
_TOPOLOGIES = ["All", "Macro", "Outdoor", "Micro", "Pico", "Indoor"]

# air status when no KPI is shown: the beam's outline, and its fill
_AIR_LINE = {"onair": "#20BFFF", "planned": "#CBD5E1", "offair": "#64748B"}
_AIR_FILLC = _AIR

_REGION = (30.95, 46.75, 8)          # the whole of R5


# the basemap modes. Every one is drawn offline, from the local map packs
# (`rfopt.geo.offline_basemap`, `_map_assets.add_offline_basemap`): no tile
# server is asked while the map is used. Satellite is the day imagery, at
# building scale (0.5 m) around the sites when that pack is installed;
# Coverage the day satellite under the measured LTE coverage grid; Night
# Satellite the same imagery graded to night under the NASA night lights and
# the glowing street network.
_BASEMAPS = {
    "Dark": {"max_zoom": 20},
    "Streets": {"max_zoom": 20},
    "Satellite": {"max_zoom": 20},
    "Coverage": {"max_zoom": 20},
    "Night Satellite": {"max_zoom": 20},
}
_DARK_BASEMAPS = ("Dark", "Night Satellite")      # light site names on these

# the map box, and the controls that float on it
_MAP_CSS = """
<style>
.st-key-sm_mapwrap {
    position: relative; gap: 0 !important; border: 1px solid #1E3A5F;
    border-radius: 12px; overflow: hidden; background: #0B1F33;
}
.st-key-sm_mapwrap > div[data-testid="stVerticalBlock"],
.st-key-sm_mapwrap div[data-testid="stLayoutWrapper"] { gap: 0 !important; }

/* layer controls floating on the map (full screen only) */
.st-key-sm_disp {
    position: absolute; top: 58px; left: 56px; z-index: 1001; width: 300px;
}
.st-key-sm_disp [data-testid="stExpander"] details {
    background: rgba(11, 31, 51, .97); border: 1px solid #1E3A5F;
    border-radius: 10px; box-shadow: 0 4px 18px rgba(0, 0, 0, .5);
}
.st-key-sm_disp [data-testid="stExpanderDetails"] {
    max-height: 520px; overflow-y: auto;
}

/* full-screen toggle — top-right, above the Leaflet tool stack, styled as
   one of those tools */
.st-key-sm_fsbtn {
    position: absolute; top: 10px; right: 10px; z-index: 1002; width: 34px;
}
.st-key-sm_fsbtn [data-testid="stTooltipHoverTarget"],
.st-key-sm_fsbtn div[data-testid="stButton"] { width: 34px; }
.st-key-sm_fsbtn button {
    width: 34px; height: 34px; min-height: 34px; padding: 0;
    background: #0D2945; border: 1px solid #1E3A5F; border-radius: 9px;
    box-shadow: 0 3px 12px rgba(0, 0, 0, .45); color: #E2E8F0;
}
.st-key-sm_fsbtn button:hover {
    background: #15406B; color: #fff; border-color: #1597FF;
}
.st-key-sm_kpi_list label p { font-size: 12.5px; }
/* the ticket's analysis beside its information: each value under its label */
.ca-kv.sm-an { grid-template-columns: minmax(96px, max-content) minmax(0, 1fr);
    gap: 4px 12px; font-size: 12px; }
.ca-kv.sm-an b { text-align: left; min-width: 0; overflow-wrap: anywhere; }
.ca-kv.sm-an .ca-badge { white-space: normal; }
.sm-an-note { color: #64748B; font-size: 11px; margin-top: 8px; }
/* the four panels under the map: one row, one size */
.st-key-sm_bottom [data-testid="stColumn"] > div { height: 100%; }
.sm-rs .ca-rsc { padding-top: 24px; }
.sm-rs .ca-evs { margin-top: 10px; }
.st-key-sm_drawer_payload { display: none !important; }

/* KPI selection floating on the map (full screen only), beside the layers */
.st-key-sm_fs_kpi {
    position: absolute; top: 58px; left: 368px; z-index: 1001;
    width: 270px; max-width: calc(100vw - 430px);
    background: rgba(11, 31, 51, .97); border: 1px solid #1E3A5F;
    border-radius: 10px; box-shadow: 0 4px 18px rgba(0, 0, 0, .5);
    padding: 6px 10px 8px;
}
.st-key-sm_fs_kpi [data-testid="stWidgetLabel"] p {
    font-size: 11px; font-weight: 600; color: #94A3B8;
    letter-spacing: .06em; text-transform: uppercase;
}
</style>
"""

# only rendered while the pseudo full-screen is ON: pin the map wrapper over the
# whole viewport, hide the rest of the Streamlit chrome, and float the header's
# search over the map.  The Leaflet map itself is sized in Python (`_FS_MAP_H`)
# since streamlit-folium hard-codes the map-div height at mount and never
# listens for a resize.
_FS_MAP_H = 940
_MAP_H = 800             # the map beside its panel (was 640 under the KPI cards)
_FS_CSS = """
<style>
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
header[data-testid="stHeader"], [data-testid="stToolbar"] {
    display: none !important;
}
[data-testid="stMainBlockContainer"] {
    padding: 0 !important; margin: 0 !important; max-width: none !important;
}
section[data-testid="stMain"] { overflow: hidden !important; }
.st-key-sm_mapwrap {
    position: fixed !important; inset: 0 !important;
    z-index: 2147483000 !important; border: 0 !important;
    border-radius: 0 !important;
}
.st-key-rf_header {
    position: fixed !important; top: 10px !important; left: 56px !important;
    z-index: 2147483001 !important; width: min(460px, calc(100vw - 200px)) !important;
    padding: 0 !important; background: transparent !important;
    border: 0 !important;
}
.st-key-rf_hdr_brand, .st-key-rf_hdr_chips { display: none !important; }
.st-key-sm_fs_hidden { display: none !important; }
.st-key-rf_header [data-testid="stTextInputRootElement"] {
    box-shadow: 0 3px 12px rgba(0, 0, 0, .5);
}
/* a dropdown's list opens in a layer on the page body: lift it above the
   full-screen map, or the KPI list opens hidden behind the map */
[data-testid="stSelectboxVirtualDropdown"] { z-index: 2147483002 !important; }
</style>
"""


# The panel beside the map as a drawer. Open: the Map layers & Analysis card
# (with a » button that folds it), Ticket ID and User Location. Folded: one
# narrow bar down the map's side — click it to open the panel again. Only
# classes change (`html.sm-drawer-closed`), so nothing reruns and every widget
# keeps its state; the map re-lays itself as its frame widens.
def _chevron(path: str, colour: str = "#20BFFF") -> str:
    """A chevron as an <img> data URI — what st.html lets through."""
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="16" '
           f'height="16"><path d="{path}" fill="none" stroke="{colour}" stroke-width="2.4" '
           'stroke-linecap="round" stroke-linejoin="round"/></svg>')
    return ('<img src="data:image/svg+xml;base64,'
            + base64.b64encode(svg.encode()).decode() + '" width="16" height="16" alt="">')


_CHEV_R, _CHEV_L = _chevron("M9 6l6 6-6 6"), _chevron("M15 6l-6 6 6 6")
_DRAWER_CLOSE = (f'<button type="button" class="sm-drawer-x" data-sm-drawer="close" '
                 f'title="Fold the panel — the map takes the whole width">{_CHEV_R}</button>')
_DRAWER_BAR = (f'<div class="sm-drawer-bar" data-sm-drawer="open" role="button" tabindex="0" '
               f'title="Open Map layers &amp; Analysis"><span class="sm-drawer-i">{_CHEV_L}'
               '</span><span class="sm-drawer-t">Map layers &amp; Analysis</span>'
               f'<span class="sm-drawer-i">{_CHEV_L}</span></div>')
_DRAWER_CSS = """
<style>
[data-testid="stColumn"]:has(.st-key-sm_drawer_bar),
[data-testid="stColumn"]:has(.st-key-sm_mapwrap) {
    transition: flex-basis .34s ease, width .34s ease, min-width .34s ease,
                max-width .34s ease;
}
.st-key-sm_drawer_bar { display: none !important; }
html.sm-drawer-closed [data-testid="stColumn"]:has(.st-key-sm_drawer_bar) {
    flex: 0 0 46px !important; width: 46px !important; min-width: 46px !important;
    max-width: 46px !important;
}
html.sm-drawer-closed [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"]
    .st-key-sm_drawer_bar) { flex-wrap: nowrap !important; }
html.sm-drawer-closed [data-testid="stColumn"]:has(.st-key-sm_mapwrap) {
    flex: 1 1 0 !important; width: auto !important; min-width: 0 !important;
    max-width: none !important;
}
html.sm-drawer-closed .st-key-sm_drawer_bar { display: block !important; }
html.sm-drawer-closed .st-key-rf_card_layers,
html.sm-drawer-closed .st-key-rf_card_sm_ticket,
html.sm-drawer-closed .st-key-rf_card_sm_loc { display: none !important; }
.sm-drawer-bar {
    height: 800px; width: 46px; box-sizing: border-box; cursor: pointer;
    display: flex; flex-direction: column; align-items: center;
    justify-content: space-between; padding: 14px 0; border-radius: 12px;
    background: linear-gradient(180deg, rgba(13, 41, 69, .92), rgba(7, 21, 37, .92));
    border: 1px solid #1E3A5F; box-shadow: inset 0 0 0 1px rgba(32, 191, 255, .08),
    0 4px 18px rgba(0, 0, 0, .35); color: #20BFFF; user-select: none;
    transition: background .2s ease, border-color .2s ease;
}
.sm-drawer-bar:hover { border-color: #1597FF;
    background: linear-gradient(180deg, rgba(21, 64, 107, .95), rgba(11, 31, 51, .95)); }
.sm-drawer-t { writing-mode: vertical-rl; transform: rotate(180deg);
    font: 700 12px 'Segoe UI', system-ui, sans-serif; letter-spacing: .14em;
    text-transform: uppercase; color: #CBD5E1; }
.sm-drawer-i { display: flex; }
.st-key-rf_card_layers { position: relative; }
.sm-drawer-x {
    position: absolute; top: 12px; right: 12px; z-index: 5; width: 28px; height: 28px;
    display: flex; align-items: center; justify-content: center; padding: 0;
    border-radius: 8px; border: 1px solid #1E3A5F; background: #0D2945; color: #20BFFF;
    cursor: pointer;
}
.sm-drawer-x:hover { background: #15406B; border-color: #1597FF; color: #fff; }
/* the Comment: ticket-ready text, copied as it is shown */
.sm-cm { margin-top: 10px; border: 1px solid #1E3A5F; border-radius: 10px;
    background: rgba(7, 21, 37, .55); }
.sm-cm-h { display: flex; align-items: center; justify-content: space-between;
    padding: 6px 10px; border-bottom: 1px solid #1E3A5F; color: #94A3B8;
    font: 600 11px 'Segoe UI', system-ui, sans-serif; letter-spacing: .06em;
    text-transform: uppercase; }
.sm-cm-copy { border: 1px solid #1E3A5F; background: #0D2945; color: #20BFFF;
    border-radius: 7px; padding: 2px 10px; font: 600 11px 'Segoe UI', system-ui,
    sans-serif; cursor: pointer; letter-spacing: 0; text-transform: none; }
.sm-cm-copy:hover { background: #15406B; color: #fff; }
.sm-cm-t { margin: 0; padding: 8px 10px; white-space: pre-wrap; word-break: break-word;
    font: 12px/1.5 'Segoe UI', system-ui, sans-serif; color: #E2E8F0;
    background: transparent; border: 0; }
</style>
"""
_DRAWER_JS = """
<script>
(function () {
  var W = window.parent || window, d = W.document, root = d.documentElement;
  var KEY = 'rf.sm.drawer';
  function apply(closed) { root.classList.toggle('sm-drawer-closed', !!closed); }
  try { apply(W.sessionStorage.getItem(KEY) === 'closed'); } catch (e) {}
  if (W.__rfSmDrawer) return;
  W.__rfSmDrawer = true;
  function find(e, attr) {
    var path = e.composedPath ? e.composedPath() : [e.target];
    for (var i = 0; i < path.length; i++) {
      var el = path[i];
      if (el && el.getAttribute && el.hasAttribute(attr)) return el;
    }
    return null;
  }
  function copy(text, btn) {
    function done() { var t = btn.textContent; btn.textContent = 'Copied ✓';
                      setTimeout(function () { btn.textContent = t; }, 1400); }
    try {
      if (W.navigator.clipboard && W.isSecureContext) {
        W.navigator.clipboard.writeText(text).then(done); return;
      }
    } catch (e) {}
    var ta = d.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    d.body.appendChild(ta); ta.select();
    try { d.execCommand('copy'); done(); } catch (e) {}
    d.body.removeChild(ta);
  }
  function onClick(e) {
    var t = find(e, 'data-sm-drawer');
    if (t) {
      var closed = t.getAttribute('data-sm-drawer') === 'close';
      apply(closed);
      try { W.sessionStorage.setItem(KEY, closed ? 'closed' : 'open'); } catch (e2) {}
      return;
    }
    var b = find(e, 'data-sm-copy');
    if (b) {
      var box = b.closest('.sm-cm'), pre = box && box.querySelector('.sm-cm-t');
      if (pre) copy(pre.textContent, b);
    }
  }
  d.addEventListener('click', onClick, true);
  d.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' || e.key === ' ') {
      var t = find(e, 'data-sm-drawer');
      if (t) { e.preventDefault(); onClick(e); }
    }
  }, true);
})();
</script>
"""


# --------------------------------------------------------------------------- #
# the page's data loaders live in `_site_data` (prepared at startup)
from _site_data import (load_ep_path as _load_ep_path,  # noqa: E402
                        load_kmz_path as _load_kmz_path,
                        topology_index as _topology_index)

_EP_TECH = {"4G": "LTE", "3G": "UMTS", "2G": "GSM"}


def _topology_mask(sect: pd.DataFrame, by_sector: dict, by_site: dict,
                   want: str) -> np.ndarray:
    tags = [by_sector.get(s) or by_site.get(t) or frozenset()
            for s, t in zip(sect["sector_id"].astype(str).str.upper(),
                            sect["site_id"].astype(str).str.upper())]
    return np.array([want in t for t in tags], dtype=bool)


def _wedge_ring(lat, lon, az, ang, radius_m, steps=10):
    """A closed [[lon, lat], …] ring for one sector wedge."""
    coslat = math.cos(math.radians(lat)) or 1e-9
    ring = [[lon, lat]]
    for i in range(steps + 1):
        b = math.radians(az - ang / 2 + ang * i / steps)
        ring.append([lon + radius_m * math.sin(b) / (_M_PER_DEG * coslat),
                     lat + radius_m * math.cos(b) / _M_PER_DEG])
    ring.append([lon, lat])
    return ring


def _beam_radius(base_m: float, tilt) -> float:
    t = float(tilt) if np.isfinite(tilt) and tilt > 0.3 else 4.0
    scale = min(max(1.3 - 0.06 * t, 0.65), 1.15)
    return base_m * scale


def _parse_latlon(text: str):
    nums = [float(x) for x in re.findall(r"-?\d+\.\d+|-?\d+", text or "")]
    if len(nums) < 2:
        return None
    a, b = nums[0], nums[1]
    if 28.5 <= a <= 34 and 43 <= b <= 50:
        return a, b
    if 28.5 <= b <= 34 and 43 <= a <= 50:
        return b, a
    return None


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def _kpi_header(path: str) -> tuple[str, list[str]]:
    """The export's technology and every KPI it carries, from its header alone
    (read once for every page, `_resources`)."""
    info = R.kpi_info(path)
    return info.kind, list(info.all_kpis)


def _kpi_group_id(group: list) -> str:
    """A technology's KPI files as one id: the file's own content hash for one
    file, a hash of the files' hashes for several — so a pick survives a
    restart, and a file added or removed is a new id."""
    shas = sorted(f.sha1 for _, _, f in group)
    if len(shas) == 1:
        return shas[0][:12]
    return hashlib.sha1("".join(shas).encode()).hexdigest()[:12]


# The exports are the active KPI Data of Data Resources, stored by content:
# a file's id is its content hash and its path holds that hash, so a new
# export can never be answered from the previous one's cache.
@st.cache_resource(show_spinner="Reading that KPI out of the export…",
                   max_entries=8)
def _sector_kpis(file_id: str, kpi: str, paths: tuple):
    """One KPI over the whole window, per sector and per site.

    A 4G export is per cell, so the loader's `sector_id` lands each value on
    the map's own `<site>-S<n>` beam. A 3G export is per NodeB and names no
    sector at all, so those rows are also rolled up per site, and every
    sector of that site takes the NodeB's value. The files of one technology
    that carry the KPI are read as one export (`merge_hourly`).
    """
    from rfopt.ingest.hourly_kpi import load_hourly_raw, merge_hourly
    from rfopt.kpi.trends import agg_how

    df = merge_hourly([load_hourly_raw(p, [kpi]) for p in paths])
    d = df[df[kpi].notna()]
    how = agg_how(kpi)
    no_sector = d["sector_id"].str.endswith("-S0")
    per_sector = d[~no_sector].groupby("sector_id", observed=True)[kpi].agg(how)
    per_site = d[no_sector].groupby("site_id", observed=True)[kpi].agg(how)
    window = (str(df["datetime"].min()), str(df["datetime"].max()))
    # the same rows, a column per timestamp of the file: the time slider
    from _kpi_time import series_pivots
    sec_t, site_t = series_pivots(df, kpi)
    return per_sector, per_site, window, sec_t, site_t


class _SectorHits(MacroElement):
    """Invisible, beam-shaped click targets — client-side + **SVG**.

    The visible beams are one canvas layer, and Leaflet's canvas hit-testing
    silently fails with ~10k paths, so a click has to land on SVG. These used
    to be circle markers, but a marker keeps its pixel size: zoomed out it
    swallowed the wedge it sat on and the map read as a field of dots. The
    target is now the beam's own shape with no stroke and no fill — nothing to
    see, and the whole beam answers a click or a hover. Only sectors in view
    get one, and only from zoom 11, where a click means something.
    """
    _template = Template("""
        {% macro script(this, kwargs) %}
        (function () {
          var m = {{ this._parent.get_name() }};
          var pts = {{ this.points }};    // [lat, lon, az, radius_m, sid, tip, i]
          var svgR = L.svg({padding: 0.6});
          var grp = L.layerGroup().addTo(m);
          var K = Math.PI / 180, M = 111320;
          function wedge(lat, lon, az, r) {    // the same 46-degree ring as Python
            var c = Math.cos(lat * K) || 1e-9, ring = [[lat, lon]];
            for (var i = 0; i <= 10; i++) {
              var b = (az - 23 + 4.6 * i) * K;
              ring.push([lat + r * Math.cos(b) / M,
                         lon + r * Math.sin(b) / (M * c)]);
            }
            return ring;
          }
          var live = {}, timer = null, CAP = 1200;
          function refresh() {
            if (m.getZoom() < 11) { grp.clearLayers(); live = {}; return; }
            var b = m.getBounds(), c = m.getCenter(), inView = [], keep = {};
            for (var i = 0; i < pts.length; i++) {
              if (!b.contains([pts[i][0], pts[i][1]])) continue;
              var dy = pts[i][0] - c.lat, dx = pts[i][1] - c.lng;
              inView.push([dx * dx + dy * dy, i]);
            }
            // more beams in view than targets: the ones nearest the centre answer
            if (inView.length > CAP) {
              inView.sort(function (a, b) { return a[0] - b[0]; });
              inView.length = CAP;
            }
            for (var k = 0; k < inView.length; k++) {
              var p = pts[inView[k][1]];
              keep[p[6]] = 1;
              if (live[p[6]]) continue;
              var cm = L.polygon(wedge(p[0], p[1], p[2], p[3]), {
                renderer: svgR, stroke: false, fillOpacity: 0
              });
              cm.__sid = p[4]; cm.__tip = p[5]; cm.__i = p[6];
              // with a KPI on the map the tooltip reads the hour on the slider
              cm.bindTooltip(function (layer) {
                var R = window.RF;
                return (R && R.kpi && layer.__i != null)
                  ? R.tipFor(layer.__i, layer.__tip) : layer.__tip;
              }, {sticky: true});
              cm.on('click', function (e) {
                var ll = e.latlng, R = window.RF;
                if (R && R.select) { R.select(this.__sid, ll, true); return; }
                try {
                  var g = window.__GLOBAL_DATA__;
                  g.last_object_clicked_tooltip = this.__sid + ' · #' + Date.now();
                  g.last_object_clicked = {lat: ll.lat, lng: ll.lng};
                  g.last_object_clicked_count =
                    (g.last_object_clicked_count || 0) + 1;
                } catch (err) {}
                // no manual map.fire('click') here: a polygon's click already
                // bubbles to the map, which is what st_folium listens on.
                // Firing it again delivered every click twice — and with the
                // ruler active, a measurement started on a beam ended on the
                // same spot.
              });
              grp.addLayer(cm);
              live[p[6]] = cm;
            }
            for (var id in live) {
              if (!keep[id]) { grp.removeLayer(live[id]); delete live[id]; }
            }
          }
          // a zoom fires moveend and zoomend: one refresh for both
          function later() { clearTimeout(timer); timer = setTimeout(refresh, 60); }
          m.on('moveend zoomend', later);
          m.whenReady(function () { setTimeout(refresh, 150); });
        })();
        {% endmacro %}
    """)

    def __init__(self, points):
        super().__init__()
        self._name = "SectorHits"
        self.points = json.dumps(points)


class _ViewKeep(MacroElement):
    """Hold the map view across Streamlit reruns *client-side* (sessionStorage),
    so panning / zooming never has to round-trip to Python.  A fresh search
    passes `jump = [lat, lon, zoom]` to override it once."""
    _template = Template("""
        {% macro script(this, kwargs) %}
        (function () {
          var m = {{ this._parent.get_name() }};
          var KEY = 'sm_view', jump = {{ this.jump }};
          function save() {
            try {
              var c = m.getCenter();
              sessionStorage.setItem(KEY, JSON.stringify(
                {lat: c.lat, lng: c.lng, z: m.getZoom()}));
            } catch (e) {}
          }
          if (jump) {
            m.setView([jump[0], jump[1]], jump[2]);
            save();
          } else {
            try {
              var s = JSON.parse(sessionStorage.getItem(KEY) || 'null');
              if (s && s.z != null) m.setView([s.lat, s.lng], s.z);
            } catch (e) {}
          }
          m.on('moveend zoomend', save);
        })();
        {% endmacro %}
    """)

    def __init__(self, jump=None):
        super().__init__()
        self._name = "ViewKeep"
        self.jump = json.dumps(list(jump)) if jump else "null"


class _Ruler(MacroElement):
    """A Google-Earth-style ruler: click a start point, move the mouse for a
    live distance-and-azimuth readout, click again to finish.

    Rendered as a real Leaflet control (`.leaflet-bar`) so it sits in the same
    toolbar column as the Draw buttons, styled to match. The finished line is
    handed to Leaflet-Draw's own `CREATED` pipeline (`map.fire(L.Draw.Event
    .CREATED, ...)`) so it reaches Python exactly like a drawn shape — no
    separate bridge to maintain, and it can be deleted with the 🗑 tool too.
    """
    _template = Template("""
        {% macro script(this, kwargs) %}
        (function () {
          var m = {{ this._parent.get_name() }};
          var R = 6371000;
          function rad(d) { return d * Math.PI / 180; }
          function dist(a, b) {
            var dLat = rad(b.lat - a.lat), dLng = rad(b.lng - a.lng);
            var s = Math.sin(dLat / 2) * Math.sin(dLat / 2) + Math.cos(rad(a.lat))
                  * Math.cos(rad(b.lat)) * Math.sin(dLng / 2) * Math.sin(dLng / 2);
            return 2 * R * Math.asin(Math.sqrt(s));
          }
          function brng(a, b) {
            var y = Math.sin(rad(b.lng - a.lng)) * Math.cos(rad(b.lat));
            var x = Math.cos(rad(a.lat)) * Math.sin(rad(b.lat)) - Math.sin(rad(a.lat))
                  * Math.cos(rad(b.lat)) * Math.cos(rad(b.lng - a.lng));
            return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
          }
          function fmt(d) { return d >= 1000 ? (d / 1000).toFixed(2) + ' km'
                                              : Math.round(d) + ' m'; }
          function tagHtml(d, b) {
            return '<div class="sm-ruler-tag">' + fmt(d)
                 + '<span class="sm-az">' + Math.round(b) + '°</span></div>';
          }

          var RulerControl = L.Control.extend({
            options: {position: 'topright'},
            onAdd: function () {
              var box = L.DomUtil.create('div', 'leaflet-bar leaflet-control');
              var a = L.DomUtil.create('a', 'sm-ruler-btn', box);
              a.href = '#';
              a.title = 'Measure distance & azimuth (click, click)';
              a.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16">'
                + '<path fill="none" stroke="currentColor" stroke-width="2" '
                + 'stroke-linecap="round" d="M4 16 16 4"/>'
                + '<path fill="none" stroke="currentColor" stroke-width="2" '
                + 'stroke-linecap="round" d="M6 14l2 2M9 11l2 2M12 8l2 2'
                + 'M15 5l2 2"/></svg>';
              L.DomEvent.on(a, 'click', L.DomEvent.stop).on(a, 'click', toggle);
              L.DomEvent.disableClickPropagation(box);
              this._link = a;
              return box;
            }
          });
          var ctl = new RulerControl();
          ctl.addTo(m);

          var active = false, pending = null, line = null, tag = null;
          var scratch = L.layerGroup().addTo(m);

          function setActive(v) {
            active = v;
            (window.RF = window.RF || {}).rulerActive = v;
            if (v && window.RF.cancelPick) window.RF.cancelPick();
            L.DomUtil[v ? 'addClass' : 'removeClass'](ctl._link, 'sm-tool-on');
            m.getContainer().style.cursor = v ? 'crosshair' : '';
            if (!v) reset();
          }
          function toggle() { setActive(!active); }
          (window.RF = window.RF || {}).cancelRuler = function () {
            if (active) setActive(false);
          };
          function reset() {
            pending = null;
            if (line) { scratch.removeLayer(line); line = null; }
            if (tag) { scratch.removeLayer(tag); tag = null; }
          }
          function onMove(e) {
            if (!active || !pending) return;
            var d = dist(pending, e.latlng), b = brng(pending, e.latlng);
            if (!line) {
              line = L.polyline([pending, e.latlng], {color: '#d6336c',
                weight: 2, dashArray: '2,7', interactive: false}).addTo(scratch);
            } else line.setLatLngs([pending, e.latlng]);
            var icon = L.divIcon({className: '', html: tagHtml(d, b),
                                  iconSize: [1, 1]});
            if (!tag) tag = L.marker(e.latlng, {interactive: false, icon: icon})
                             .addTo(scratch);
            else { tag.setLatLng(e.latlng); tag.setIcon(icon); }
          }
          function onClick(e) {
            if (!active || e.rfSynthetic) return;   // not the drawer's clicks
            L.DomEvent.stop(e);
            if (!pending) { pending = e.latlng; return; }
            var a = pending, b = e.latlng, d = dist(a, b), br = brng(a, b);
            reset();
            setActive(false);
            var final = L.polyline([a, b], {color: '#d6336c', weight: 3});
            final.feature = {type: 'Feature', properties: {
              distance_m: Math.round(d), bearing_deg: Math.round(br)}};
            m.fire(L.Draw.Event.CREATED, {layer: final, layerType: 'polyline'});
          }
          m.on('mousemove', onMove);
          m.on('click', onClick);
          L.DomEvent.on(document, 'keydown', function (ev) {
            if (ev.key === 'Escape' && active) setActive(false);
          });
        })();
        {% endmacro %}
    """)

    def __init__(self):
        super().__init__()
        self._name = "Ruler"


# --------------------------------------------------------------------------- #
# 1. header + sidebar — the view, the KPI, the data sources
# --------------------------------------------------------------------------- #
fs = bool(st.session_state.get("sm_fs"))
st.html(_MAP_CSS + _DRAWER_CSS + (_FS_CSS if fs else ""))
st.html(_DRAWER_JS, unsafe_allow_javascript=True)
st.html(_C._CSS)                # the Delay Tickets Analysis cards, charts and badges
st.html(_C.CA2_CSS)

q_raw = (_header("RF Optimization", "Sites · sectors on the map",
                 search_key="sm_q",
                 placeholder="Search a site, a sector, or a lat, lon…")
         or "").strip()
msg_slot = st.container()

# the map, and beside it: 1 Map layers & Analysis (Current view and KPI analysis
# folded into it), 2 Ticket ID, 3 User Location. In full screen the layer
# controls float on the map, and the view / KPI widgets are kept out of sight.
# The panel beside the map folds into a narrow drawer bar (its « / » buttons,
# client-side, remembered for the tab): the map then takes the whole width.
if not fs:
    with st.container(key="sm_row"):
        map_area, side = st.columns([3.2, 1], gap="small")
    with side.container(key="sm_drawer_bar"):
        st.html(_DRAWER_BAR)
    with side.container(key="rf_card_layers", border=True):
        st.html(_DRAWER_CLOSE + _title_html("Map layers & Analysis", "layers"))
        ctl_slot = st.container()
        view_box = st.expander("Current view", icon=":material/tune:", expanded=False)
        kpi_box = st.expander("KPI analysis", icon=":material/monitoring:",
                              expanded=False)
        offline_box = st.expander("Offline map", icon=":material/cloud_off:",
                                  expanded=False)
    ticket_card = side.container(key="rf_card_sm_ticket", border=True)
    loc_card = side.container(key="rf_card_sm_loc", border=True)
    legend_slot = None                     # set under the KPI list, below
else:
    map_area = st.container()
    _hidden = st.container(key="sm_fs_hidden")
    view_box, kpi_box = _hidden.container(), _hidden.container()
    ctl_slot = ticket_card = loc_card = legend_slot = offline_box = None

# every file comes from Data Resources (the Current version of each resource)
kmz_path, ep_path = R.kmz_path(), R.ep_path()
kmz_name = R.kmz_file().name if kmz_path else None

def _comment(tk: dict) -> str:
    """The ticket-ready Comment (`rfopt.complaints.comment`): at an approved
    user location its serving sector, distance and RSRP; without one the
    ticket's site."""
    a, re_ = tk["analysis"], tk["re"]
    if re_ is not None:
        srv = re_.server
        return _ticket_comment(a, sector=re_.sector_id,
                               distance_m=srv.distance_m if srv is not None else None,
                               rsrp=re_.rsrp, lat=re_.lat, lon=re_.lon,
                               coverage_issue=bool(re_.coverage_issue))
    return _ticket_comment(a, sector=tk["site"])


def _analysis_html(tk: dict) -> str:
    """The ticket's analysis as it stands: stage 1 (general, site level) or
    stage 2 (at the approved user location), and its ticket-ready Comment."""
    row, a, re_, lead = tk["row"], tk["analysis"], tk["re"], tk["lead"]
    if re_ is not None:
        at = _RL.load().get(str(row["Ticket ID"]), {}).get("at", "")
        stage = "2 · User location" + (f" (approved {at})" if at else "")
        sector = tk["sector"] or "—"
    else:
        stage = "1 · General analysis (site level)"
        sector = "— (no user location: the site's cells are analysed)"
    if lead is not None:
        kpi = f"{lead.label} {_C.fmt(lead.worst, lead.unit)} · {lead.status}"
    elif a.classification == _C.NO_ISSUE:
        kpi = "No KPI above its threshold"
    else:
        kpi = "—"
    status = _C._badge(a.classification, _C.CLASS_COLOUR[a.classification])
    if a.resolution:
        status += f" <small>{_C._esc(a.resolution)}</small>"
    pairs = [("Serving sector", _C._esc(sector)),
             ("Distance", _C._esc(row["Distance"])),
             ("Azimuth difference", _C._esc(f"{re_.server.az_diff_deg:.0f}°"
                                            if re_ is not None and re_.server else _C.NA)),
             ("RSRP", _C._esc(row["RSRP"])), ("KPI result", _C._esc(kpi)),
             ("Status", status)]
    return ('<div class="ca-kv sm-an">'
            + "".join(f"<span>{_C._esc(k)}</span><b>{v}</b>" for k, v in pairs)
            + '</div><div class="sm-cm"><div class="sm-cm-h"><span>Comment</span>'
              '<button type="button" class="sm-cm-copy" data-sm-copy title="Copy the '
              'Comment, ready to paste into the ticket">Copy</button></div>'
              f'<pre class="sm-cm-t">{_C._esc(_comment(tk))}</pre></div>'
            + f'<div class="sm-an-note">Stage {_C._esc(stage)} · correlation window '
              f"{_C._esc(WIN_LABEL)} — as on Delay Tickets Analysis.</div>")


def _stage_title(tk) -> str:
    if tk is None:
        return ""
    return "Stage 2 · User location" if tk["re"] is not None else "Stage 1 · General"


def _analysis_panel() -> None:
    st.html(_title_html("Analysis Result", "target", subtitle=_stage_title(TK)))
    if TK is None:
        st.caption("Search a Ticket ID to see its analysis.")
        return
    st.html(_analysis_html(TK))


def _kpi_chart(items: list, *, key: str, where: str, empty: str) -> None:
    """A KPI picker (the issue KPIs first) and the chart of the one picked:
    the Delay Tickets Analysis evidence chart, every cell its own line."""
    if not items:
        st.caption(empty)
        return
    labels = [f"{it['tag']} - {it['name']} · {it['state']}" for it in items]
    if ss.get(f"{key}_pick", 0) not in range(len(items)):
        ss[f"{key}_pick"] = 0
    pick = st.selectbox("KPI", list(range(len(items))), key=f"{key}_pick",
                        format_func=lambda i: labels[i], label_visibility="collapsed")
    item = items[pick]
    st.caption(f"{where} · {item['state']} {item['value']}"
               + (f" · {item['threshold']}" if item.get("threshold") else ""))
    st.plotly_chart(_C.evidence_figure(item, WIN_LABEL), key=f"{key}_fig",
                    config={"displayModeBar": False}, width="stretch")


def _serving_kpi_panel() -> None:
    where = (TK["sector"] if TK is not None and TK["re"] is not None
             else TK["site"] if TK is not None else "")
    st.html(_title_html("Serving Site KPI", "chart", subtitle=where))
    if TK is None:
        st.caption("Search a Ticket ID to see its serving site's KPIs.")
        return
    _kpi_chart(_C.serving_kpi_items(CTX, TK["row"]), key="sm_srv", where=where or _C.NA,
               empty="No judged KPI of the serving "
                     + ("sector" if TK["re"] is not None else "site")
                     + f" has data in the correlation window ({WIN_LABEL}).")


def _neighbour_kpi_panel() -> None:
    st.html(_title_html("Neighbour Sector KPI", "tower"))
    if TK is None:
        st.caption("Search a Ticket ID to see its neighbour sectors' KPIs.")
        return
    re_ = TK["re"]
    if re_ is None:
        st.caption("Neighbour sectors are the sectors of other sites facing the user "
                   "location — approve the subscriber's location in User Location.")
        return
    nbs = list(re_.neighbours or [])
    if not nbs:
        st.caption("No sector of another site faces the user location.")
        return
    if ss.get("sm_nb_sec", 0) not in range(len(nbs)):
        ss["sm_nb_sec"] = 0
    j = st.selectbox("Neighbour sector", list(range(len(nbs))), key="sm_nb_sec",
                     format_func=lambda i: f"{nbs[i].sector_id} · "
                                           f"{nbs[i].analysis.classification}",
                     label_visibility="collapsed")
    nb = nbs[j]
    _kpi_chart(_C.neighbour_kpi_items(CTX, TK["row"], nb), key="sm_nb", where=nb.sector_id,
               empty=f"No judged KPI of {nb.sector_id} has data in the correlation "
                     f"window ({WIN_LABEL}).")


# --------------------------------------------------------------------------- #
# the Daily Target ticket: the same analysed tickets Delay Tickets Analysis
# shows (`_complaints.load_workspace`, on its Correlation Window). A ticket
# searched here is found by its Ticket ID or HPSM Incident ID; stage 1 is its
# general (site-level) analysis, stage 2 the re-analysis at a user location
# approved here — stored per Ticket ID, so both pages show one result.
# --------------------------------------------------------------------------- #
ss = st.session_state
WIN_LABEL, WIN_H = _C.window_setting()
CTX, ctx_error = None, ""
try:
    CTX = _C.load_workspace(WIN_LABEL, WIN_H)
except Exception as exc:                   # a Daily Target that cannot be read
    ctx_error = f"The stored Daily Target could not be read: {exc}"


def _ticket_search() -> None:
    ss["sm_tid"] = (ss.get("sm_tid_in") or "").strip()
    ss.pop("sm_tid_rev", None)


def _loc_approve() -> None:
    tid, p = ss.get("sm_tid_open"), _parse_loc(ss.get("sm_loc_in", ""))
    if tid and p:
        _RL.approve(tid, *p)


def _loc_clear() -> None:
    ss["sm_loc_in"] = ""
    if ss.get("sm_tid_open"):
        _RL.clear(ss["sm_tid_open"])


# a ticket handed over by Delay Tickets Analysis (Open in Site Map): searched
# here as if typed, before the Ticket ID field is drawn
if "sm_tid_next" in ss:
    ss["sm_tid"] = ss["sm_tid_in"] = str(ss.pop("sm_tid_next"))
    ss.pop("sm_tid_rev", None)

TK = None            # the ticket searched: its row, analysis, stage-2 result
_tid_q = (ss.get("sm_tid") or "").strip()
if _tid_q and CTX is not None:
    _T, _Q = CTX.T, _tid_q.upper()
    _hit = _T[(_T["Ticket ID"].str.upper() == _Q) | (_T["_complaint"].str.upper() == _Q)]
    if _hit.empty:
        _hit = _T[_T["Ticket ID"].str.upper().str.contains(_Q, regex=False)
                  | _T["_complaint"].str.upper().str.contains(_Q, regex=False)]
    if len(_hit):
        _sel = _hit.iloc[0]
        _i = int(_sel["_i"])
        _re = CTX.re.get(_i)
        _a = CTX.analysis[_i]
        _lead = _lead_check(_a)
        _tracks = _re.tracks if _re is not None else CTX.tracks
        _col = next((t.column for t in _tracks if _lead is not None
                     and t.label == _lead.label and t.source == _lead.source), None)
        _site = _sel["Site ID"] if _sel["Site ID"] != _C.NA else ""
        TK = {"row": _sel, "i": _i, "re": _re, "analysis": _a, "lead": _lead,
              "kpi_column": _col, "site": _site,
              "sector": _sel["Serving Sector"] if _sel["Serving Sector"] != _C.NA else "",
              "matches": len(_hit)}
        ss["sm_tid_open"] = _sel["Ticket ID"]
        _rev = (f"{_sel['Ticket ID']}|{TK['sector']}|"
                + (f"{_re.lat:.6f},{_re.lon:.6f}" if _re is not None else "general"))
        TK["rev"] = _rev
        TK["new"] = ss.get("sm_tid_rev") != _rev
        if TK["new"]:
            # a ticket opened (or re-analysed): its serving sector opens, its
            # main issue KPI colours the map, the map moves to it — once
            ss["sm_tid_rev"] = _rev
            if TK["sector"]:
                ss["sm_sel_sector"] = TK["sector"]
            if _col:
                ss["sm_kpi_want"] = _col
            if _re is not None:
                ss["sm_loc_in"] = f"{_re.lat:.5f}, {_re.lon:.5f}"
            elif ss.get("sm_loc_last_tid") != _sel["Ticket ID"]:
                ss["sm_loc_in"] = ""
            ss["sm_loc_last_tid"] = _sel["Ticket ID"]
    else:
        ss.pop("sm_tid_open", None)
else:
    ss.pop("sm_tid_open", None)

if ticket_card is not None:
    with ticket_card:
        st.html(_title_html("Ticket ID", "ticket"))
        if "sm_tid_in" not in ss:
            ss["sm_tid_in"] = ss.get("sm_tid", "")
        _a1, _a2 = st.columns([3, 1.1], gap="small", vertical_alignment="center")
        _a1.text_input("Ticket ID", key="sm_tid_in", on_change=_ticket_search,
                       label_visibility="collapsed", autocomplete="off",
                       placeholder="Ticket ID / HPSM Incident ID…")
        _a2.button("Search", key="sm_tid_go", type="primary", width="stretch",
                   on_click=_ticket_search)
        if CTX is None:
            st.caption(ctx_error or "No Daily Target in Data Resources → Complaint Data.")
        elif _tid_q and TK is None:
            st.caption(f"No Daily Target ticket matches “{_tid_q}”.")
        elif TK is not None:
            _r = TK["row"]
            _pt = _r["Problem Time"]
            st.caption(" · ".join(x for x in (
                str(_r["Ticket ID"]), str(_r["_complaint"] or ""), TK["site"] or "no site",
                f"problem {_pt:%d %b %H:%M}" if pd.notna(_pt) else "") if x)
                + (f" — {TK['matches']} tickets match" if TK["matches"] > 1 else ""))
        # the same ticket on Delay Tickets Analysis, opened in its Ticket Details
        if st.button("Open in Delay Tickets Analysis", icon=":material/open_in_new:",
                     key="sm_open_ca", width="stretch", disabled=TK is None,
                     help="This ticket in Delay Tickets Analysis → Ticket Details"):
            _C.open_ticket(TK["row"]["Ticket ID"])
            st.switch_page("views/complaint_analysis.py")

if loc_card is not None:
    with loc_card:
        st.html(_title_html("User Location", "pin", subtitle="for re-analysis"))
        _b1, _b2 = st.columns([3, 1.1], gap="small", vertical_alignment="center")
        _b1.text_input("User location", key="sm_loc_in", label_visibility="collapsed",
                       autocomplete="off", disabled=TK is None,
                       placeholder="Lat, Lon (e.g. 30.5082, 47.7831)")
        _b2.button("Clear", key="sm_loc_clear", width="stretch", on_click=_loc_clear,
                   disabled=TK is None,
                   help="Clear the field; an approved location is removed and the "
                        "ticket goes back to its general analysis on both pages")
        _p_in = _parse_loc(ss.get("sm_loc_in", "")) if TK is not None else None
        _approved = ((round(TK["re"].lat, 5), round(TK["re"].lon, 5))
                     if TK is not None and TK["re"] is not None else None)
        _pending = _p_in is not None and (round(_p_in[0], 5), round(_p_in[1], 5)) != _approved
        st.button("Approve & Re-analyze", key="sm_loc_go", type="primary", width="stretch",
                  on_click=_loc_approve, disabled=not _pending)
        if TK is not None and (ss.get("sm_loc_in") or "").strip() and _p_in is None:
            st.caption("Enter the location as latitude, longitude inside R5.")
        elif _pending:
            st.caption("Location entered — the analysis is unchanged until "
                       "Approve & Re-analyze is pressed.")
        elif TK is not None:
            st.caption("Stage 2 · analysed at the approved user location."
                       if TK["re"] is not None else
                       "Stage 1 · general analysis — enter the subscriber's location "
                       "to re-analyse.")

with view_box:
    topo = st.segmented_control("Technology", ["All", "4G", "3G", "2G"],
                                default="All", required=True, key="sm_topo",
                                width="stretch")
    topology = st.selectbox("Topology", _TOPOLOGIES, key="sm_topology",
                            disabled=not ep_path)
    if not ep_path:
        topology = "All"

with kpi_box:
    # The active KPI Data of Data Resources: 4G, 3G and 2G exports together;
    # the Technology above decides whose KPIs are offered. The files of one
    # technology are one entry (a KPI split over several files — areas,
    # periods — colours every sector); its id comes from the files' content
    # hashes, so a pick survives a restart of the app.
    kpi_srcs = R.kpi_sources()

    kpi_col, kpi_name = None, ""
    kpi_sect = kpi_site = None
    kpi_window = ("", "")
    kpi_sec_t = kpi_site_t = None
    kpi_fid = kpi_file = ""
    kpi_headers: list[tuple] = []
    choices: dict = {}
    if not kpi_srcs:
        _file_status(None, empty="No KPI Data — add it in Data Resources")
    if kpi_srcs:
        # every KPI of each technology's files, read from their headers alone
        by_id = {_kpi_group_id(g): g for kind, g in R.kpi_groups()
                 if kind in KpiFileInfo.USABLE}
        for gid, g in by_id.items():
            kpis_of: list = []
            for _path, _info, _f in g:
                kpis_of += [k for k in _kpi_header(_path)[1] if k not in kpis_of]
            kpi_headers.append((gid, " + ".join(_f.name for _, _, _f in g), g[0][1].kind,
                                kpis_of))
        choices = _kpi_choices(kpi_headers, topo)
        pick_key = "sm_kpi_pick"
        _kept_pick = st.session_state.get("sm_kpi_pick_keep")
        if pick_key not in st.session_state and _kept_pick in choices:
            # back from another page: Streamlit dropped the radio's own state
            st.session_state[pick_key] = _kept_pick
        if not choices:
            if kpi_headers:
                st.caption(f"No {topo} KPI export among these files.")
        else:
            _want = st.session_state.pop("sm_kpi_want", None)
            _got = [o for o, c in choices.items() if _want and c.kpi == _want]
            if _got:
                # a ticket opened: its main issue KPI colours the map
                st.session_state[pick_key] = _got[0]
                st.session_state["sm_kpi_pick_keep"] = _got[0]
            if st.session_state.get(pick_key, _NO_KPI) not in choices:
                # its file was removed, or it belongs to another technology
                st.session_state[pick_key] = _NO_KPI
            current = st.session_state[pick_key]
            kq = st.text_input("Search", key="sm_kpi_q",
                               placeholder="Search KPIs",
                               label_visibility="collapsed")
            shown = ([o for o, c in choices.items()
                      if kq.lower() in c.label.lower()] if kq
                     else list(choices))
            if current != _NO_KPI and current not in shown:
                shown = [current] + shown        # the pick stays while searching
            _sig = tuple(choices[o].label for o in shown)
            if st.session_state.get("sm_kpi_sig") != _sig:
                # new labels (a file added or removed) make the browser draw a
                # fresh radio with nothing ticked: hand it the pick again
                st.session_state["sm_kpi_sig"] = _sig
                st.session_state[pick_key] = current
            # one KPI at a time: a sector can only be one colour
            with st.container(key="sm_kpi_list", height=260, border=True):
                choice = st.radio(
                    "KPI", [_NO_KPI] + shown, key=pick_key,
                    format_func=lambda o: o if o == _NO_KPI
                    else choices[o].label,
                    label_visibility="collapsed")
            st.session_state["sm_kpi_pick_keep"] = choice
            st.button("Clear", icon=":material/close:", width="stretch",
                      disabled=choice == _NO_KPI,
                      on_click=lambda k=pick_key: st.session_state.update(
                          {k: _NO_KPI}))
            if choice != _NO_KPI:
                picked = choices[choice]
                kpi_col, kpi_name = picked.kpi, picked.label
                _g = by_id[picked.file_id]
                kpi_fid = picked.file_id
                kpi_file = " + ".join(_f.name for _, _, _f in _g)
                (kpi_sect, kpi_site, kpi_window, kpi_sec_t,
                 kpi_site_t) = _sector_kpis(
                    picked.file_id, picked.kpi,
                    tuple(_p for _p, _i, _ in _g if picked.kpi in _i.all_kpis))
    if not fs:
        legend_slot = st.container()       # the KPI's legend card, under its list

# The measured coverage grids (DL Coverage Insight) of the Current Coverage
# Data in Data Resources; Basemap -> Coverage draws them.
cov_kept = R.coverage()

# --- resolve frames (KMZ is the sole source) ------------------------------- #
ks = _load_kmz_path(kmz_path) if kmz_path else None
if ks is None or not len(ks.sectors):
    st.info("No **KMZ Data** yet — upload the R5_Sites.kmz (from Google Earth) "
            "once in **Data Resources** (sidebar → Data). It carries every 2G / "
            "3G / 4G cell for R5, and is kept until a new version is applied.")
    R.link("Open Data Resources")
    st.stop()

SECT, CELLS = ks.sectors.copy(), ks.cells.copy()
# the KMZ may be behind: a site the EP tracker lists as active is On Air
_on_air = R.ep_on_air()
SECT, CELLS = _apply_ep_status(SECT, _on_air), _apply_ep_status(CELLS, _on_air)
for c in ("latitude", "longitude", "azimuth_deg", "antenna_height_m",
          "elec_tilt_deg", "ret_deg"):
    SECT[c] = _num(SECT[c])
SECT = SECT.dropna(subset=["latitude", "longitude"]).reset_index(drop=True)

SITES = (SECT.groupby("site_id", as_index=False)
         .agg(site_name=("site_name", "first"),
              latitude=("latitude", "mean"), longitude=("longitude", "mean"),
              sectors=("sector_id", "nunique"),
              n_2g=("n_2g", "sum"), n_3g=("n_3g", "sum"), n_4g=("n_4g", "sum"),
              height=("antenna_height_m", "median"),
              air=("air", lambda s: s.mode().iat[0] if len(s.mode()) else "onair"),
              status=("status", "first")))
site_ll = SITES.set_index("site_id")[["latitude", "longitude"]]

sel_sector = st.session_state.get("sm_sel_sector")


def _take_click(tip) -> bool:
    """A beam / tower click or the drawer's close, once per click."""
    global sel_sector
    if not tip or tip == st.session_state.get("sm_last_tip"):
        return False
    st.session_state["sm_last_tip"] = tip
    act, sid = _parse_tip(tip)
    if act == "close":
        st.session_state.pop("sm_sel_sector", None)
        sel_sector = None
    elif act == "select" and sid in set(SECT["sector_id"]):
        # an empty sector-0 placemark (no azimuth, no cells) is not a sector:
        # open the site's first real one instead
        site_rows = SECT[SECT["site_id"] == SECT.loc[SECT["sector_id"] == sid,
                                                     "site_id"].iloc[0]]
        real = site_rows[site_rows["azimuth_deg"].notna()].sort_values("sector")
        if len(real) and sid not in set(real["sector_id"]):
            sid = str(real["sector_id"].iloc[0])
        st.session_state["sm_sel_sector"] = sel_sector = sid
    else:
        return False
    return True


_prev = st.session_state.get("sm_folium")
_take_click(_prev.get("last_object_clicked_tooltip")
            if isinstance(_prev, dict) else None)

# --- the header search: a sector, a site (id or name), or a lat, lon ------- #
cll = _parse_latlon(q_raw)
site_q = sector_q = ""
if q_raw and not cll:
    _Q = q_raw.upper()
    _sec_hit = SECT[SECT["sector_id"].astype(str).str.upper() == _Q]
    if len(_sec_hit):
        sector_q = str(_sec_hit["sector_id"].iloc[0])
        site_q = str(_sec_hit["site_id"].iloc[0])
    else:
        _ids = SITES["site_id"].astype(str).str.upper()
        _names = SITES["site_name"].fillna("").astype(str).str.upper()
        ex = SITES.loc[_ids == _Q, "site_id"]
        pt = SITES.loc[_ids.str.contains(_Q, regex=False)
                       | _names.str.contains(_Q, regex=False), "site_id"]
        if len(ex):
            site_q = ex.iloc[0]
        elif len(pt):
            site_q = pt.iloc[0]
            if len(pt) > 1:
                msg_slot.caption(f"{len(pt)} sites match — showing **{site_q}** "
                                 f"({', '.join(pt.head(6))} …)")
        else:
            msg_slot.warning(f"No R5 site or sector matches '{q_raw}'.")
if sector_q and st.session_state.get("sm_last_q") != q_raw:
    # a sector search opens that sector, once — a later beam click still wins
    st.session_state["sm_sel_sector"] = sel_sector = sector_q
st.session_state["sm_last_q"] = q_raw


# --------------------------------------------------------------------------- #
# the Sector Details drawer: the tables it shows, and its payload
# --------------------------------------------------------------------------- #
_TECH_ORDER = {"2G": 0, "3G": 1, "4G": 2}
_BAND_ORDER = {"G900": 0, "U900": 1, "L900": 2, "G1800": 3, "L1800": 4,
               "U2100": 5, "L2100": 6, "L2300(TDD)": 7, "L2600(TDD)": 8}


_EP_COLS = [("cell_name", "Cell"), ("band_label", "Band"), ("earfcn", "EARFCN"),
            ("bandwidth_mhz", "BW MHz"), ("pci", "PCI"), ("mod3", "Mod3"),
            ("rsi", "RSI"), ("rs_power_dbm", "RS pwr dBm"),
            ("mech_tilt_deg", "M-tilt°"), ("elec_tilt_deg", "E-tilt°"),
            ("total_tilt_deg", "Total tilt°"), ("max_ret_deg", "Max RET°"),
            ("antenna_model", "Antenna"), ("status", "Status")]
_EP_WHOLE = ("EARFCN", "BW MHz", "PCI", "Mod3", "RSI")
_EP_DEC = ("RS pwr dBm", "M-tilt°", "E-tilt°", "Total tilt°", "Max RET°")


def _ep_key(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def _ep_num(s: pd.Series, dec: int) -> pd.Series:
    """Formatted as text, because Streamlit prints a numeric blank as 'None'."""
    return s.map(lambda v: "—" if pd.isna(v) else f"{v:.{dec}f}")


def _ep_sector_table(ep: pd.DataFrame, sector_id: str, kmz_cells: pd.DataFrame,
                     tech: str) -> pd.DataFrame:
    """The tracker's rows for one sector — matched on sector id, falling back
    to cell name for the sites the tracker numbers differently to the KMZ."""
    d = ep[ep["sector_id"].astype(str).str.upper() == str(sector_id).upper()]
    if d.empty and len(kmz_cells):
        names = {_ep_key(n) for n in kmz_cells["cell_name"]}
        d = ep[ep["cell_name"].map(_ep_key).isin(names)]
    if tech:
        d = d[d["technology"] == tech]
    if d.empty:
        return d
    out = pd.DataFrame({lbl: d[c] for c, lbl in _EP_COLS if c in d.columns})
    for c in _EP_WHOLE + _EP_DEC:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.dropna(axis=1, how="all").reset_index(drop=True)
    for c in out.columns:
        if c in _EP_WHOLE or c in _EP_DEC:
            out[c] = _ep_num(out[c], 1 if c in _EP_DEC else 0)
        else:
            out[c] = out[c].astype(str).str.strip().replace(
                {"": "—", "nan": "—", "None": "—"})
    return out


def _sector_cell_table(cells: pd.DataFrame, s0: pd.Series) -> pd.DataFrame:
    d = cells.copy()
    d["_o"] = d["technology"].map(_TECH_ORDER).fillna(9)
    d["_b"] = d["band_label"].map(_BAND_ORDER).fillna(9)
    d = d.sort_values(["_o", "_b", "cell_name"])
    az = pd.to_numeric(d["azimuth_deg"], errors="coerce").fillna(
        s0.get("azimuth_deg"))
    ret = pd.to_numeric(d["ret_deg"], errors="coerce")
    h = s0.get("antenna_height_m")
    return pd.DataFrame({
        "Cell": d["cell_name"].astype(str),
        "Band": d["band_label"].astype(str),
        "Azimuth°": az.round(0),
        "Height m": round(float(h), 1) if np.isfinite(h) else np.nan,
        "RET°": ret.round(1),
        "Status": d["status"].astype(str),
    }).reset_index(drop=True)


def _drawer_payload(sid: str, *, index_of: dict, beam_len: float,
                    kpi: dict | None) -> dict | None:
    """The selected site's real data for the Sector Details drawer: every
    sector of the site, its KMZ cells, its EP tracker rows and, with a KPI on
    the map, its values over the file's timestamps."""
    row = SECT[SECT["sector_id"] == sid]
    if row.empty:
        return None
    site_id = str(row["site_id"].iloc[0])
    secs = SECT[SECT["site_id"] == site_id]
    # the KMZ repeats empty sector-0 placemarks (no azimuth, no cells): only
    # a physical sector gets a button, once
    real = ((pd.to_numeric(secs["n_cells"], errors="coerce").fillna(0) > 0)
            | secs["azimuth_deg"].notna() | (secs["sector_id"] == sid))
    secs = secs[real].drop_duplicates("sector_id").sort_values("sector")
    site = SITES[SITES["site_id"] == site_id].iloc[0]
    site_cells = CELLS[CELLS["site_id"] == site_id]

    ep, ep_name, tags = None, None, set()
    if ep_path:
        ep_name = R.ep_file().name
        ep = _load_ep_path(ep_path)
        if len(ep):
            _by_sec, _by_site = _topology_index(ep_path)
            tags = set(_by_site.get(site_id.upper(), ()))
    rsi_of = ({_ep_key(n): v for n, v in zip(ep["cell_name"], ep["rsi"])
               if pd.notna(v)} if ep is not None and len(ep) and "rsi" in ep.columns
              else {})
    kpi_rows = (_sector_kpis_json(
        secs, kpi["sec"], kpi["site"],
        _sector_values(secs, kpi["per_sector"], kpi["per_site"]).to_numpy(),
        kpi["scheme"]) if kpi else [None] * len(secs))

    def num(v):
        v = pd.to_numeric(v, errors="coerce")
        return float(v) if pd.notna(v) and np.isfinite(v) else None

    sectors = []
    for j, s0 in enumerate(secs.itertuples(index=False)):
        cc = site_cells[site_cells["sector_id"] == s0.sector_id]
        if topo != "All":
            cc = cc[cc["technology"] == topo]
        cells_tbl = None
        if len(cc):
            d = cc.assign(_o=cc["technology"].map(_TECH_ORDER).fillna(9),
                          _b=cc["band_label"].map(_BAND_ORDER).fillna(9))
            d = d.sort_values(["_o", "_b", "cell_name"])
            code = pd.to_numeric(d["pci"], errors="coerce").fillna(
                pd.to_numeric(d["psc"], errors="coerce"))
            rsi = d["cell_name"].map(lambda n: rsi_of.get(_ep_key(n)))
            cells_tbl = pd.DataFrame({
                "RAT": d["technology"].astype(str),
                "Band": d["band_label"].astype(str),
                "Cell": d["cell_name"].astype(str),
                "PCI / PSC": code.map(lambda v: "–" if pd.isna(v) else f"{v:.0f}"),
                "RSI": pd.to_numeric(rsi, errors="coerce").map(
                    lambda v: "–" if pd.isna(v) else f"{v:.0f}"),
                "Azimuth°": pd.to_numeric(d["azimuth_deg"], errors="coerce")
                .fillna(s0.azimuth_deg).round(0),
                "RET°": pd.to_numeric(d["ret_deg"], errors="coerce").round(1),
                "Status": d["status"].astype(str)})
        ep_tbl, ep_note = None, ""
        if ep is not None:
            if not len(ep):
                ep_note = ("No R5 rows read from that workbook — it needs the "
                           "tracker's GSM / UMTS / LTE sheets.")
            else:
                ep_tbl = _ep_sector_table(ep, s0.sector_id, cc, _EP_TECH.get(topo))
                if not len(ep_tbl):
                    ep_tbl, ep_note = None, "This sector isn't in the tracker."
        az = num(s0.azimuth_deg)
        sectors.append({
            "id": str(s0.sector_id),
            "n": int(s0.sector) if pd.notna(s0.sector) else str(s0.sector_id),
            "azimuth": az, "ret": num(s0.ret_deg), "height": num(s0.antenna_height_m),
            "status": str(s0.status), "lat": float(s0.latitude),
            "lon": float(s0.longitude),
            "r": float(_beam_radius(beam_len, s0.elec_tilt_deg)),
            "i": index_of.get(str(s0.sector_id)),
            "cells": _table_json(cells_tbl), "ep": _table_json(ep_tbl),
            "ep_note": ep_note,
            **(kpi_rows[j] or {"series": None, "window": None, "codes": None,
                               "stats": {"n": 0}, "src": "none"})})

    name = str(site["site_name"]).strip() if pd.notna(site["site_name"]) else ""
    return {
        "site": {
            "id": site_id, "name": name or site_id,
            "air_label": _AIR_LABEL.get(site["air"], str(site["status"])),
            "air_colour": _AIR_LINE.get(site["air"], "#20BFFF"),
            "techs": [t for t in ("2G", "3G", "4G")
                      if (site_cells["technology"] == t).any()],
            "topology": sorted(tags)},
        "sectors": sectors,
        "sel": [x["id"] for x in sectors].index(str(sid)),
        "rev": f"{sid}|{st.session_state.get('sm_last_tip', '')}|{q_raw}",
        "files": {"kmz": kmz_name, "ep": ep_name},
    }


# --------------------------------------------------------------------------- #
# 2. the map, with its legend + layer controls beside it
# --------------------------------------------------------------------------- #
def _sync_fs_kpi():
    """The full-screen dropdown writes the one KPI pick the sidebar list reads."""
    pick = st.session_state.get("sm_kpi_pick_fs", _NO_KPI)
    st.session_state["sm_kpi_pick"] = pick
    st.session_state["sm_kpi_pick_keep"] = pick


def _fs_kpi_picker():
    """KPI selection on the full-screen map. It keeps no KPI of its own: it
    shows the sidebar's pick and writes back to it, so the sidebar list is on
    the same KPI when full screen is left."""
    if not choices:
        st.selectbox("KPI", [], placeholder="No KPI export loaded",
                     disabled=True, key="sm_kpi_pick_fs_none")
        return
    pick = st.session_state.get("sm_kpi_pick", _NO_KPI)
    st.session_state["sm_kpi_pick_fs"] = pick if pick in choices else _NO_KPI
    st.selectbox("KPI", [_NO_KPI] + list(choices), key="sm_kpi_pick_fs",
                 format_func=lambda o: o if o == _NO_KPI else choices[o].label,
                 on_change=_sync_fs_kpi)


def _layer_controls():
    """Basemap, layers and sizing — beside the map, or floating on it in full
    screen. The keys are the same either way, so a setting survives the switch."""
    basemap = st.segmented_control(
        "Basemap", list(_BASEMAPS), default="Dark", required=True,
        key="sm_basemap", width="stretch",
        help="Satellite shows clutter / water / terrain behind the beams; Night "
             "Satellite the city lights and the lit street network. Every basemap "
             "is drawn offline from the local map packs (Offline map, below).")
    lc = st.columns(2)
    show_beams = lc[0].checkbox("Sector beams", True, key="sm_l_beam")
    show_towers = lc[1].checkbox("Towers", True, key="sm_l_tower")
    show_labels = lc[0].checkbox("Site names", True, key="sm_l_lbl")
    show_lines = lc[1].checkbox("Distance lines", True, key="sm_l_line")
    beam_len = st.slider("Beam length (m)", 40, 400, 130, 10, key="sm_beamlen",
                         help="Shorten this in dense areas so sectors don't "
                              "overlap; lengthen it zoomed out.")
    n_lines = st.slider("Distance lines — nearby sites", 1, 12, 6, 1,
                        key="sm_nlines")
    return (basemap, show_beams, show_towers, show_labels, show_lines, beam_len,
            n_lines)


if ctl_slot is not None:
    with ctl_slot:
        ctl = _layer_controls()
else:
    ctl = None

saved = st.session_state.get("sm_draw") or []

with map_area, st.container(key="sm_mapwrap"):

    # -- full-screen toggle (top-right, over the Leaflet tool stack) -------- #
    # a callback, not `if button: rerun()`: the rerun cut the run short before
    # the layer controls were drawn, and Streamlit dropped their settings
    with st.container(key="sm_fsbtn"):
        st.button(":material/fullscreen_exit:" if fs else ":material/fullscreen:",
                  key="sm_fs_toggle",
                  help="Exit full screen" if fs else
                  "Full screen — the search and layer controls come along",
                  on_click=lambda: st.session_state.update(
                      sm_fs=not st.session_state.get("sm_fs", False)))

    # -- full screen: the layer controls float on the map ------------------- #
    if ctl is None:
        with st.container(key="sm_disp"):
            with st.expander("Map layers", icon=":material/layers:",
                             expanded=False):
                ctl = _layer_controls()

    # -- full screen: the same KPI pick, as a dropdown on the map ----------- #
    if fs:
        with st.container(key="sm_fs_kpi"):
            _fs_kpi_picker()
    (basemap, show_beams, show_towers, show_labels, show_lines, beam_len,
     n_lines) = ctl

    # one view at a time: the sectors coloured by a KPI, or measured coverage
    coverage_mode = basemap == "Coverage"
    cov = None
    if coverage_mode:
        kpi_col = None
        if cov_kept:
            cov = _Coverage(cov_kept)
        else:
            with msg_slot:
                st.info("Coverage view draws the LTE DL Coverage Insight grid — "
                        "add it under **Coverage Data** in Data Resources.",
                        icon=":material/signal_cellular_alt:")

    # -- technology + topology filter ------------------------------------- #
    tcol = {"2G": "n_2g", "3G": "n_3g", "4G": "n_4g"}.get(topo)
    SECT_view = SECT if tcol is None else SECT[_num(SECT[tcol]).fillna(0) > 0]
    if topology != "All":
        _by_sec, _by_site = _topology_index(ep_path)
        if _by_sec or _by_site:
            SECT_view = SECT_view[_topology_mask(SECT_view, _by_sec, _by_site,
                                                 topology)]
        else:
            msg_slot.caption("That EP workbook has no topology column — "
                             "showing every sector.")

    # -- search → focus / pin / nearest sites ---------------------------- #
    focus = None
    pin = None
    nearest_panel = None

    if site_q and site_q in site_ll.index:
        la, lo = site_ll.loc[site_q]
        focus = (float(la), float(lo), 16,
                 f"sector:{sector_q}" if sector_q else f"site:{site_q}")

    if cll:
        clat, clon = cll
        pin = (clat, clon)
        focus = (clat, clon, 14, f"coord:{clat:.5f},{clon:.5f}")
        dlat = (site_ll["latitude"] - clat) * _M_PER_DEG
        dlon = (site_ll["longitude"] - clon) * _M_PER_DEG * math.cos(
            math.radians(clat))
        dist = np.hypot(dlat, dlon)
        rows, best = [], None
        for sid, d in dist.nsmallest(max(int(n_lines), 4)).items():
            secs = []
            for sr in SECT[SECT["site_id"] == sid].itertuples():
                if not np.isfinite(sr.azimuth_deg):
                    continue
                brg = _brg(sr.latitude, sr.longitude, clat, clon)
                off = angular_offset_deg(float(sr.azimuth_deg), brg)
                secs.append((int(sr.sector), float(sr.azimuth_deg), off))
                score = d * (1 + (off / 65.0) ** 2)
                if best is None or score < best[0]:
                    best = (score, sid, int(sr.sector), off, d)
            rows.append({"site": sid, "distance_m": int(round(d)),
                         "lat": float(site_ll.loc[sid, "latitude"]),
                         "lon": float(site_ll.loc[sid, "longitude"]),
                         "sectors (az / off-axis)":
                         "  ".join(f"S{n}:{a:.0f}°/{o:.0f}°"
                                   for n, a, o in sorted(set(secs)))})
        nearest_panel = (pd.DataFrame(rows), best)

    # -- the ticket: its site, or its approved user location ------------ #
    if TK is not None:
        _re_m = TK["re"]
        _tk_focus = None
        if _re_m is not None:
            _tk_focus = (_re_m.lat, _re_m.lon, 16, f"ticket:{TK['rev']}")
        elif TK["site"] and TK["site"] in site_ll.index:
            _tla, _tlo = site_ll.loc[TK["site"]]
            _tk_focus = (float(_tla), float(_tlo), 16, f"ticket:{TK['rev']}")
        if _tk_focus is not None and (TK["new"] or focus is None):
            focus = _tk_focus

    draw = SECT_view

    # -- where to open the map ------------------------------------------- #
    # The live view is kept client-side by `_ViewKeep` (sessionStorage); Python
    # only decides the first-load default and whether a fresh search should jump.
    if focus:
        fla, flo, fz, frev = focus
    else:
        fla, flo, fz, frev = _REGION[0], _REGION[1], _REGION[2], "overview"
    _new_search = frev != st.session_state.get("sm_last_focus")
    st.session_state["sm_last_focus"] = frev
    _jump = [float(fla), float(flo), int(fz)] if (_new_search and focus) else None
    start_loc, start_zoom = [_REGION[0], _REGION[1]], _REGION[2]

    _bm = _BASEMAPS.get(basemap, _BASEMAPS["Dark"])
    _mz = _bm.get("max_zoom", 20)
    fmap = folium.Map(location=start_loc, zoom_start=start_zoom, prefer_canvas=True,
                      control_scale=True, tiles=None, max_zoom=_mz)
    if coverage_mode:
        # the coverage grid sits under the roads and place names; beams and
        # towers stay above both
        CustomPane("rfCoverage", z_index=250).add_to(fmap)
        CustomPane("rfRefs", z_index=300).add_to(fmap)
    _dark = basemap in _DARK_BASEMAPS
    _lbl_fg, _lbl_halo = ("#f0f0f0", "#000") if _dark else ("#111", "#fff")
    # the map lives in an iframe: its dark controls, tower badges and cards
    # need their styles inside it. Leave room at the top-right for the
    # Streamlit full-screen button; site-name labels must never swallow a
    # click meant for a beam.
    fmap.get_root().header.add_child(folium.Element(
        "<style>" + _LEAFLET_CSS
        + ".leaflet-top.leaflet-right{margin-top:42px}"
        ".leaflet-marker-icon.sm-lbl{pointer-events:none!important}</style>"))
    fmap.add_child(_MapAssets())

    # -- tools: zoom + home top-left; ruler above draw-circle/marker, right -- #
    fmap.add_child(_HomeView(*_REGION))
    fmap.add_child(_Ruler())
    _draw = Draw(position="topright", export=False,
         draw_options={"polyline": False, "polygon": False, "rectangle": False,
                       "circle": {"shapeOptions": {"color": _MEAS}},
                       "marker": False, "circlemarker": False},
         edit_options={"edit": False, "remove": True}).add_to(fmap)
    # Leaflet and Leaflet.draw from the app itself; nothing else from a CDN
    _use_local_libraries(fmap, _draw)
    # the basemap from the local map packs — offline, no tile server
    _add_offline_basemap(fmap, basemap if basemap in _BASEMAPS else "Dark",
                         ref_pane="rfRefs" if coverage_mode else None)
    # the marker tool, right under draw-circle and delete: one marker with its
    # latitude / longitude live while dragged, and a copy button - no reload
    fmap.add_child(_LocationPick())
    fmap.add_child(_MousePosition())

    # -- 2a. beam wedges (canvas) + invisible beam-shaped click targets (SVG) - #
    # colouring by a KPI replaces the air-status colours: one meaning at a
    # time, or the map says two things with the same paint
    kpi_band, kpi_val, kpi_legend, _band_colour = {}, {}, [], {}
    kpi_scheme = None
    if kpi_col and kpi_sect is not None:
        # a sector's own value, or its site's when the file has no sectors (3G)
        _vals = _sector_values(draw, kpi_sect, kpi_site)
        # decided once on the window; every hour on the slider uses it too
        kpi_scheme = _band_scheme(_vals, kpi_col)
        bands, kpi_legend = _apply_scheme(_vals, kpi_scheme), kpi_scheme.spec
        kpi_band, kpi_val = bands.to_dict(), _vals.to_dict()
        _band_colour = {k: c for k, c, _ in kpi_legend}
        _band_colour["none"] = _KPI_BAND["none"]

    fg_beams = folium.FeatureGroup(name="Sector beams", show=True)
    bf, hits = [], []
    # a sector's place among the drawn ones: the key into the time frames
    _index_of = {sid: i for i, sid in enumerate(draw["sector_id"].astype(str))}
    if show_beams:
        for _pos, r in enumerate(draw.itertuples()):
            if not np.isfinite(r.azimuth_deg):
                continue
            rad = _beam_radius(beam_len, r.elec_tilt_deg)
            key = (kpi_band.get(r.sector_id, "none") if kpi_col else r.air)
            bf.append({"type": "Feature", "properties": {"c": key, "i": _pos},
                       "geometry": {"type": "Polygon",
                                    "coordinates": [_wedge_ring(
                                        r.latitude, r.longitude,
                                        r.azimuth_deg, 46, rad)]}})
            tip = (f"{r.sector_id} · {r.site_id} S{int(r.sector)} · "
                   f"az {r.azimuth_deg:.0f}°")
            if np.isfinite(r.ret_deg):
                tip += f" · RET {r.ret_deg:.1f}°"
            hits.append([round(float(r.latitude), 6),
                         round(float(r.longitude), 6),
                         round(float(r.azimuth_deg), 1), round(float(rad), 1),
                         r.sector_id, tip, _pos])
    _line_c = _band_colour if kpi_col else _AIR_LINE
    _fill_c = _band_colour if kpi_col else _AIR_FILLC
    _beams_layer = None
    if bf:
        _beams_layer = folium.GeoJson({"type": "FeatureCollection", "features": bf},
                       style_function=lambda x: {
                           "color": _line_c.get(x["properties"]["c"], "#20BFFF"),
                           "weight": 1,
                           "fillColor": _fill_c.get(x["properties"]["c"],
                                                    "#1597FF"),
                           "fillOpacity": 0.6 if kpi_col else
                           (0.12 if coverage_mode else 0.3)},
                       smooth_factor=2).add_to(fg_beams)
    fg_beams.add_to(fmap)
    if hits:
        fmap.add_child(_SectorHits(hits))

    # -- 2c. tower badges (status colour + info card) + site-name labels -- #
    lbl = SITES[SITES["site_id"].isin(set(draw["site_id"]))].copy()
    _nm = lbl["site_name"].fillna("").astype(str).str.strip()
    lbl["label"] = _nm.where(_nm.ne(""), lbl["site_id"])
    if show_towers and len(lbl):
        _tp = _tower_points(draw, SITES, kpi=kpi_col, band_of=kpi_band,
                            value_of=kpi_val, colour_of=_band_colour,
                            spec=kpi_legend, index_of=_index_of)
        fmap.add_child(_TowerMarkers(_tp, min_zoom=14))
    if show_labels and len(lbl):
        _at = (lbl["latitude"].round(6).astype(str) + ","
               + lbl["longitude"].round(6).astype(str))
        _pts = [[round(float(r.latitude), 6), round(float(r.longitude), 6),
                 str(r.label), int(k)]
                for r, k in zip(lbl.itertuples(), _at.groupby(_at).cumcount())]
        fmap.add_child(_SiteLabels(_pts, fg=_lbl_fg, halo=_lbl_halo,
                                   min_zoom=13, anchor_y=34))

    # -- 2e. complaint pin + distance lines ---------------------- #
    if pin:
        plat, plon = pin
        folium.Marker([plat, plon],
                      icon=_pin_icon("#F8FAFC"),
                      tooltip=f"complaint  {plat:.5f}, {plon:.5f}").add_to(fmap)
        if show_lines and nearest_panel is not None and len(nearest_panel[0]):
            fg_lines = folium.FeatureGroup(name="Distance lines", show=True)
            tbl = nearest_panel[0].head(int(n_lines))
            best_sid = nearest_panel[1][1] if nearest_panel[1] else None
            for row in tbl.itertuples():
                is_best = row.site == best_sid
                folium.PolyLine([[plat, plon], [row.lat, row.lon]],
                                color="#22C55E" if is_best else "#94A3B8",
                                weight=3 if is_best else 1.5,
                                tooltip=f"{row.site} · {row.distance_m:,} m"
                                ).add_to(fg_lines)
                folium.Marker(
                    [(plat + row.lat) / 2, (plon + row.lon) / 2],
                    icon=folium.DivIcon(
                        icon_size=(70, 14), icon_anchor=(35, 7),
                        html=f'<div style="font:600 11px system-ui;'
                             f'color:{_lbl_fg};text-shadow:0 0 3px {_lbl_halo},'
                             f'0 0 3px {_lbl_halo};'
                             f'text-align:center">{row.distance_m:,} m</div>')
                ).add_to(fg_lines)
            fg_lines.add_to(fmap)

    # -- 2e'. the ticket's approved user location, and its serving site - #
    if TK is not None and TK["re"] is not None:
        _re_m = TK["re"]
        folium.Marker([_re_m.lat, _re_m.lon], icon=_pin_icon("#20BFFF"),
                      tooltip=f"User location · {TK['row']['Ticket ID']} · "
                              f"{_re_m.lat:.5f}, {_re_m.lon:.5f}").add_to(fmap)
        if _re_m.server is not None and _re_m.server.site_id in site_ll.index:
            _sla, _slo = site_ll.loc[_re_m.server.site_id]
            folium.PolyLine([[_re_m.lat, _re_m.lon], [float(_sla), float(_slo)]],
                            color="#22C55E", weight=3,
                            tooltip=f"Serving sector {_re_m.sector_id} · "
                                    f"{_re_m.server.distance_m:,.0f} m · "
                                    f"{_re_m.server.az_diff_deg:.0f}° off boresight"
                            ).add_to(fmap)

    # -- 2f. re-draw shapes the user drew before ---------------- #
    if saved:
        fg_draw = folium.FeatureGroup(name="My drawings", show=True)
        for d in saved:
            g = d.get("geometry") or {}
            p = d.get("properties") or {}
            if g.get("type") == "Point":
                lon, lat = g["coordinates"][:2]
                rad = p.get("radius")
                if rad:
                    folium.Circle([lat, lon], radius=float(rad), color=_MEAS,
                                  weight=2, fill=True, fill_opacity=0.06,
                                  tooltip=f"circle · r = {float(rad):,.0f} m"
                                  ).add_to(fg_draw)
                else:
                    folium.Marker([lat, lon], icon=_pin_icon("#FB923C"),
                                  tooltip=f"pin  {lat:.5f}, {lon:.5f}"
                                  ).add_to(fg_draw)
            elif g.get("type") == "LineString":
                pts = [[c[1], c[0]] for c in g["coordinates"]]
                tot = sum(_hav(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
                          for i in range(len(pts) - 1))
                brg = _brg(pts[0][0], pts[0][1], pts[-1][0], pts[-1][1])
                folium.PolyLine(pts, color=_MEAS, weight=3,
                                tooltip=f"ruler · {tot/1000:.2f} km · "
                                        f"{brg:.0f}°"
                                ).add_to(fg_draw)
        fg_draw.add_to(fmap)

    # -- 2g. the KPI over the file's own timestamps, and the drawer ---- #
    _kpi_ctx = None
    if kpi_col and kpi_scheme is not None and kpi_sec_t is not None:
        from rfopt.kpi.trends import agg_how as _agg_how
        _kpi_ctx = {"sec": kpi_sec_t, "site": kpi_site_t, "per_sector": kpi_sect,
                    "per_site": kpi_site, "scheme": kpi_scheme}
        fmap.add_child(_KpiTimeline(_kpi_config(
            label=kpi_name, kpi=kpi_col, unit=_kpi_unit(kpi_col),
            how=_agg_how(kpi_col), file_name=kpi_file, scheme=kpi_scheme,
            draw=draw, window_vals=_vals.to_numpy(), sec=kpi_sec_t,
            site=kpi_site_t, store=_store_key(kpi_fid, kpi_col)),
            _beams_layer.get_name() if _beams_layer is not None else None))
    # the drawer's code rides in the map, the selected site's data does not: it
    # is published beside the map (below). A beam click used to change the map's
    # script, and streamlit-folium rebuilt the whole map — tiles, beams, view —
    # for every sector opened.
    fmap.add_child(_SectorDrawer(None))
    _payload = (_drawer_payload(sel_sector, index_of=_index_of, beam_len=beam_len,
                                kpi=_kpi_ctx)
                if sel_sector and sel_sector in set(SECT["sector_id"]) else None)

    if cov is not None:
        fmap.add_child(_CoverageLayer(cov.layer_config()))

    fmap.add_child(_ViewKeep(jump=_jump))

    # `returned_objects` is deliberately tiny: only a sector-beam click or a new
    # drawing should rerun Python.  Pan / zoom / measure / a tower card stay
    # 100% client-side (no rerun, no reload flash) — `_ViewKeep` remembers the
    # view instead.
    out = st_folium(fmap, key="sm_folium",
                    height=_FS_MAP_H if fs else _MAP_H, use_container_width=True,
                    returned_objects=["last_object_clicked_tooltip",
                                      "all_drawings"])
    with st.container(key="sm_drawer_payload"):
        st.html(f'<div id="rf-drawer-payload" data-rev="'
                f'{_html.escape(_payload["rev"] if _payload else "none")}">'
                f'{_html.escape(_js_json(_payload)) if _payload else ""}</div>')

# --------------------------------------------------------------------------- #
# 3. react to map interaction
# --------------------------------------------------------------------------- #
if _take_click((out or {}).get("last_object_clicked_tooltip")):
    st.rerun()                  # the drawer lives in the map: rebuild it once

_drawings = (out or {}).get("all_drawings")
if _drawings is not None and _drawings != saved:
    # the map above was built *before* this new drawing was known, so it
    # rendered without it — st_folium then remounts the iframe on this rerun
    # (a new drawing is always in returned_objects), and without a further
    # rerun the fresh map never gets the shape back: it flashes and vanishes.
    # One more rerun rebuilds the map with `saved` already including it.
    st.session_state["sm_draw"] = _drawings
    saved = _drawings
    st.rerun()

# the offline map packs, and the one explicit update (the Internet, once)
if offline_box is not None:
    with offline_box:
        from _offline_map_ui import offline_map_box as _offline_map_box
        from rfopt.geo.offline_basemap import region_bbox as _region_bbox
        _offline_map_box(_region_bbox(SITES["latitude"], SITES["longitude"]),
                         SITES[["site_id", "latitude", "longitude"]])

# --------------------------------------------------------------------------- #
# 4. the legend card (beside the map), the panels under the map
# --------------------------------------------------------------------------- #

if legend_slot is not None:
    with legend_slot:
        if kpi_col and kpi_legend and not coverage_mode:
            _rule = kpi_scheme.rule if kpi_scheme is not None else None
            _n_t = (len(set(kpi_sec_t.columns) | set(kpi_site_t.columns))
                    if kpi_sec_t is not None else 0)
            if _rule is not None:
                _up = _rule.direction == "up"
                _thr = [(_swatch("square", _KPI_BAND["warning"]), "OK line",
                         f"{'≥' if _up else '≤'} {_rule.warning:g}"),
                        (_swatch("square", _KPI_BAND["critical"]), "Critical",
                         f"{'<' if _up else '>'} {_rule.critical:g}")]
            else:
                _thr = [(_swatch("info", "#94A3B8"), "Thresholds",
                         "none · shaded by size")]
            _card(kpi_name, [(_swatch("chart", "#20BFFF"), "Timestamps",
                              f"{_n_t:,}"), *_thr,
                             (_swatch("file", "#20BFFF"), kpi_file, "file")],
                  icon="chart",
                  note="The legend on the map counts the sectors at the time on "
                       "its time bar; click a sector for its details.")

# under the map, one row of three panels of one size: Serving Site KPI ·
# Neighbour Sector KPI · Analysis Result (a long panel scrolls inside its card)
_PANEL_H = 460
with st.container(key="sm_bottom"):
    _p = st.columns(3, gap="small")
    for _col, _key, _draw in ((_p[0], "rf_card_sm_srvkpi", _serving_kpi_panel),
                              (_p[1], "rf_card_sm_nbkpi", _neighbour_kpi_panel),
                              (_p[2], "rf_card_sm_result", _analysis_panel)):
        with _col, st.container(key=_key, border=True, height=_PANEL_H):
            _draw()
# read-out for whatever is drawn
if saved:
    lines = []
    for d in saved:
        g = d.get("geometry") or {}
        p = d.get("properties") or {}
        if g.get("type") == "LineString":
            pts = [[c[1], c[0]] for c in g["coordinates"]]
            tot = sum(_hav(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
                      for i in range(len(pts) - 1))
            brg = _brg(pts[0][0], pts[0][1], pts[-1][0], pts[-1][1])
            lines.append(f"📏 **ruler** — {tot/1000:.2f} km "
                         f"({tot:,.0f} m) · azimuth {brg:.0f}°")
        elif g.get("type") == "Point" and p.get("radius"):
            lon, lat = g["coordinates"][:2]
            lines.append(f"⭕ **circle** — r = {float(p['radius']):,.0f} m "
                         f"@ {lat:.5f}, {lon:.5f}")
        elif g.get("type") == "Point":
            lon, lat = g["coordinates"][:2]
            lines.append(f"📍 **pin** — {lat:.5f}, {lon:.5f}")
    ca, cb = st.columns([5, 1])
    ca.info("  \n".join(lines))
    if cb.button("Clear drawings", icon=":material/delete:", width="stretch"):
        st.session_state["sm_draw"] = []
        st.rerun()

# --------------------------------------------------------------------------- #
# 7. full-screen: streamlit-folium pins the Leaflet map-div height at mount and
#    never re-measures, so after the wrapper blows up we have to reach into the
#    (same-origin) component iframe and kick Leaflet into re-laying its tiles.
#    st.html runs the script in the page itself, where window.parent is the
#    page — the same document the old components iframe reached for.
# --------------------------------------------------------------------------- #
if fs:
    st.html(
        """
        <script>
        (function () {
          var pdoc;
          try { pdoc = window.parent.document; } catch (e) { return; }
          function kick() {
            var ifr = pdoc.querySelector('.st-key-sm_mapwrap iframe');
            if (!ifr) return;
            try {
              var w = ifr.contentWindow;
              if (!w || !w.map) return;
              var c = w.map.getCenter(), z = w.map.getZoom();
              w.map.invalidateSize(false);
              w.map.setView(c, z, {reset: true, animate: false});
            } catch (e) {}
          }
          [200, 500, 1000, 1800, 3000].forEach(function (t) {
            setTimeout(kick, t);
          });
          try { window.parent.addEventListener('resize', kick); } catch (e) {}
        })();
        </script>
        """,
        unsafe_allow_javascript=True,
    )
