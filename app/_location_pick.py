"""The Sites map's Select Location tool.

A map button that drops one marker where the map is clicked and shows its
latitude / longitude in a window tied to it, live while the marker is dragged,
with a copy button. It lives entirely in the map (assets/location_pick.js):
nothing is sent to Python while it is used, so nothing reruns or reloads.
"""

from __future__ import annotations

import json
from pathlib import Path

from branca.element import MacroElement
from jinja2 import Template

from _ui import ICONS

_COPY_SVG = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
             'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
             '<rect x="9" y="9" width="11" height="11" rx="2"/>'
             '<path d="M5 15V6a2 2 0 0 1 2-2h9"/></svg>')

PICK_CSS = """
.rf-pick-btn { display: flex !important; align-items: center; justify-content: center;
    width: 30px; height: 30px; }
.rf-pick-btn svg { width: 16px; height: 16px; }
.leaflet-bar a.rf-pick-btn.rf-tool-on { background-color: #1597FF !important; color: #fff !important; }
.leaflet-container.rf-picking { cursor: crosshair; }
.rf-picking .leaflet-interactive:not(.rf-pick-mk), .rf-picking .rf-tw-wrap,
.rf-picking .sm-lbl { pointer-events: none !important; }

.rf-pick-win { position: absolute; left: 0; top: 0; z-index: 900; width: 268px;
    box-sizing: border-box; background: rgba(11, 31, 51, .96);
    border: 1px solid rgba(32, 191, 255, .55); border-radius: 12px;
    box-shadow: 0 8px 26px rgba(0, 0, 0, .55), 0 0 18px rgba(32, 191, 255, .18);
    color: #E2E8F0; font: 12px/1.4 'Segoe UI', system-ui, sans-serif; cursor: default;
    will-change: transform; transition: opacity .12s ease; }
.rf-pick-win[hidden] { display: none; }
.rf-pick-zooming .rf-pick-win, .rf-pick-zooming .rf-pick-lead { opacity: 0; }
.rf-pick-h { display: flex; align-items: center; gap: 8px; padding: 8px 8px 8px 12px;
    border-bottom: 1px solid #1E3A5F; }
.rf-pick-ico { flex: 0 0 24px; height: 24px; border-radius: 50%; display: flex;
    align-items: center; justify-content: center; color: #20BFFF;
    background: rgba(32, 191, 255, .12); border: 1px solid rgba(32, 191, 255, .45); }
.rf-pick-ico svg { width: 14px; height: 14px; }
.rf-pick-t { flex: 1; font-weight: 700; font-size: 13px; color: #F1F5F9; white-space: nowrap; }
.rf-pick-state { display: inline-flex; align-items: center; gap: 5px; font-size: 10.5px;
    font-weight: 700; letter-spacing: .06em; }
.rf-pick-state i { width: 7px; height: 7px; border-radius: 50%; }
.rf-pick-state.sel { color: #22C55E; }
.rf-pick-state.sel i { background: #22C55E; }
.rf-pick-state.live { color: #20BFFF; }
.rf-pick-state.live i { background: #20BFFF; animation: rfPickPulse 1s infinite; }
@keyframes rfPickPulse {
    0% { box-shadow: 0 0 0 0 rgba(32, 191, 255, .7); }
    70% { box-shadow: 0 0 0 6px rgba(32, 191, 255, 0); }
    100% { box-shadow: 0 0 0 0 rgba(32, 191, 255, 0); } }
.rf-pick-x { width: 22px; height: 22px; padding: 0; border: 0; border-radius: 6px;
    background: transparent; color: #94A3B8; font: 12px/22px 'Segoe UI', system-ui, sans-serif;
    cursor: pointer; }
.rf-pick-x:hover { background: #15406B; color: #F8FAFC; }
.rf-pick-b { display: flex; align-items: center; gap: 10px; padding: 8px 12px 11px; }
.rf-pick-vals { flex: 1; min-width: 0; }
.rf-pick-k { margin-top: 3px; color: #94A3B8; font-size: 10.5px; font-weight: 600;
    letter-spacing: .06em; text-transform: uppercase; }
.rf-pick-v { color: #F8FAFC; font-size: 16px; font-weight: 600; line-height: 1.3;
    font-variant-numeric: tabular-nums; user-select: text; -webkit-user-select: text;
    cursor: text; }
.rf-pick-copy { flex: 0 0 auto; display: flex; align-items: center; gap: 6px; height: 40px;
    padding: 0 12px; border: 1px solid #1E3A5F; border-radius: 9px; background: #0D2945;
    color: #20BFFF; font: 600 12.5px 'Segoe UI', system-ui, sans-serif; cursor: pointer;
    white-space: nowrap; }
.rf-pick-copy:hover { background: #15406B; border-color: #1597FF; }
.rf-pick-copy.ok { color: #22C55E; border-color: rgba(34, 197, 94, .55); }
.rf-pick-copy svg { width: 16px; height: 16px; }
.rf-pick-lead { position: absolute; left: 0; top: 0; width: 100%; height: 100%; z-index: 899;
    pointer-events: none; overflow: visible; transition: opacity .12s ease; }
.rf-pick-lead line { stroke: #20BFFF; stroke-width: 1.6; }
.rf-pick-lead circle { fill: #20BFFF; }

/* the selected-location marker only; tower badges are not touched */
.rf-pick-mk { background: transparent; border: 0; touch-action: none; }
.rf-pick-pin { position: relative; width: 34px; height: 44px; cursor: grab;
    filter: drop-shadow(0 0 6px rgba(251, 146, 60, .6)) drop-shadow(0 2px 3px rgba(0, 0, 0, .5)); }
.rf-pick-pin svg { position: relative; display: block; }
.rf-pick-ring { position: absolute; left: 50%; top: 42px; width: 24px; height: 10px;
    margin: -5px 0 0 -12px; border-radius: 50%; border: 2px solid rgba(32, 191, 255, .9);
    box-shadow: 0 0 10px rgba(32, 191, 255, .7); }
.rf-pick-mk.live .rf-pick-ring { animation: rfPickRing 1s infinite; }
@keyframes rfPickRing { 0% { transform: scale(.8); opacity: 1; }
    100% { transform: scale(1.7); opacity: 0; } }
.rf-pick-mk.live .rf-pick-pin { cursor: grabbing; }
"""


class LocationPick(MacroElement):
    """Select Location: one marker, its live coordinates, a copy button."""
    _template = Template("""
        {% macro header(this, kwargs) %}
        <style>{{ this.css }}</style>
        {% endmacro %}
        {% macro script(this, kwargs) %}
        (function () {
          var MAP = {{ this._parent.get_name() }};
          var ICON_PIN = {{ this.pin }};
          var ICON_COPY = {{ this.copy }};
          {{ this.js }}
        })();
        {% endmacro %}
    """)

    def __init__(self):
        super().__init__()
        self._name = "LocationPick"
        self.css = PICK_CSS
        self.pin = json.dumps(ICONS["pin"])
        self.copy = json.dumps(_COPY_SVG)
        self.js = (Path(__file__).resolve().parent / "assets" / "location_pick.js"
                   ).read_text(encoding="utf-8")
