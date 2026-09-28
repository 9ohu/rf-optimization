"""The startup screen: the one loading the system shows, while it really prepares.

One continuous animation over one background — the Huawei RF Analysis
night view of Iraq (`static/startup/rf_startup_bg.*`, served by the app) —
never a sequence of pictures. The scene itself plays on its own: the view
lights up, the tower beacons blink, a comet — the tower's signal — flies from
the tower to the heart of Baghdad; where it lands day breaks over the dark Iraq
map, spreading from Baghdad outward, the cities light up as it reaches them and
the links between them draw themselves and carry data. Two backbones stay live:
the tower to Baghdad, and the foot of the picture to Basra. The loading panel
under the title follows the real preparation (`_warmup`): its status is the
task running, its dial the share of tasks done — one quarter per step, each
filled by its own step's tasks — beside the four steps (Data Resources ·
Network Data · Map Services · Analysis Engine), each Standby, Syncing, then
Online as its tasks finish. It says Ready only when the preparation is
complete, then fades into the app.

Every animated element is placed in the background's own coordinates (per cent
of the picture), so it stays on its tower light or city at any window size.
Nothing is scaled or blurred: text is live text, the lights and lines are
vector, the picture is drawn once at its own resolution. The layer lives
outside Streamlit's page (added to the document body), so the app loads
underneath it and nothing of the app changes once it has gone. Esc or "Skip"
hides it; a reduced-motion setting stills the streams.
"""

from __future__ import annotations

import base64
import json
import math
from pathlib import Path

import streamlit as st

STATIC = Path(__file__).resolve().parent / "static" / "startup"
_URL = "/app/static/startup/"


BG_NAMES = ("rf_startup_bg.png", "rf_startup_bg.jpg", "rf_startup_bg.jpeg", "rf_startup_bg.webp")


def background() -> tuple[str, float]:
    """(URL, width / height) of the startup background: the largest of
    `static/startup/rf_startup_bg.(png|jpg|jpeg|webp)` — put the original
    high-resolution file there under one of these names to use it."""
    from PIL import Image
    best = None
    for p in (STATIC / n for n in BG_NAMES):
        if not p.exists():
            continue
        try:
            w, h = Image.open(p).size
        except Exception:
            continue
        if best is None or w * h > best[1] * best[2]:
            best = (p, w, h)
    if best is None:
        return _URL + "rf_startup_bg.png", 2392 / 898
    p, w, h = best
    return _URL + p.name, w / h


BG_URL, _ASPECT = background()
_VW, _VH = round(1000 * _ASPECT), 1000     # the network layer's own units

# the background's own coordinates, per cent of its width / height
TOWER_LIGHTS = [(10.13, 26.82, 0.0), (10.07, 33.59, 0.6), (5.91, 54.07, 1.1), (11.61, 55.23, 1.7)]
CITIES = {                              # the lit city nodes under the map pins
    "baghdad": (73.68, 49.75), "basra": (86.33, 71.25), "mosul": (70.96, 26.45),
    "erbil": (76.11, 33.67), "anbar": (60.72, 51.92),
}
NODES = {"east": (81.17, 59.65), "south": (73.85, 71.15), "west": (58.33, 19.24),
         "tower": (10.07, 33.59)}
# the network links the data streams run along (from, to, bend in % of width)
LINKS = [("anbar", "baghdad", -3), ("baghdad", "mosul", 3), ("mosul", "erbil", -3),
         ("erbil", "baghdad", 3), ("baghdad", "east", -2), ("east", "basra", 3),
         ("baghdad", "south", -3), ("south", "basra", -3), ("anbar", "mosul", 4),
         ("west", "mosul", -3), ("tower", "baghdad", -20)]
# the second backbone: from the foot of the picture, along the city's light
# streams, up to Basra
BASRA_FROM = (14.5, 100.0)

STEPS = [("Data Resources", "db"), ("Network Data", "net"),
         ("Map Services", "map"), ("Analysis Engine", "engine")]
LAUNCH, FLY = 1.0, 1.9      # s: the comet leaves the tower, and its flight to Baghdad
REVEAL = 2.4                # s: day spreading from Baghdad over the map
MIN_SHOW = 5.4              # s: the comet and the map reveal play through before Ready
STALL = 120                 # s without a word from the server: get out of the way

_ICON = {
    "db": '<ellipse cx="12" cy="5.5" rx="7" ry="2.8"/><path d="M5 5.5v6.5c0 1.5 3.1 2.8 7 2.8s7-1.3 '
          '7-2.8V5.5"/><path d="M5 12v6.5c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8V12"/>',
    "net": '<circle cx="12" cy="5" r="2.2"/><circle cx="5" cy="18.5" r="2.2"/><circle cx="19" '
           'cy="18.5" r="2.2"/><path d="M12 7.2V12m0 0-5.5 4.8M12 12l5.5 4.8"/>',
    "map": '<path d="m9 4-5 2v14l5-2 6 2 5-2V4l-5 2z"/><path d="M9 4v14M15 6v14"/>',
    "engine": '<circle cx="12" cy="12" r="3"/><path d="M12 2.8v2.6M12 18.6v2.6M21.2 12h-2.6M5.4 '
              '12H2.8M18.5 5.5l-1.8 1.8M7.3 16.7l-1.8 1.8M18.5 18.5l-1.8-1.8M7.3 7.3 5.5 5.5"/>',
}


def _svg(d: str, cls: str = "") -> str:
    return (f'<svg class="{cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{d}</svg>')


def _link_path(a, b, bend: float) -> str:
    """A gentle curve between two points of the picture, in the layer's units."""
    (x1, y1), (x2, y2) = a, b
    X1, Y1, X2, Y2 = x1 * _VW / 100, y1 * _VH / 100, x2 * _VW / 100, y2 * _VH / 100
    mx, my = (X1 + X2) / 2, (Y1 + Y2) / 2
    dx, dy = X2 - X1, Y2 - Y1
    n = (dx * dx + dy * dy) ** 0.5 or 1.0
    k = bend * _VW / 100
    cx, cy = mx - dy / n * k, my + dx / n * k
    return f"M{X1:.0f},{Y1:.0f} Q{cx:.0f},{cy:.0f} {X2:.0f},{Y2:.0f}"


def _reach(pt) -> float:
    """When day reaches a point of the map, in s after the comet lands: by its
    distance from Baghdad."""
    bx, by = CITIES["baghdad"]
    d = ((pt[0] - bx) * _ASPECT) ** 2 + (pt[1] - by) ** 2
    return round(0.15 + 1.9 * min(1.0, d ** 0.5 / 35), 2)


def _xy(pt) -> tuple[float, float]:
    return pt[0] * _VW / 100, pt[1] * _VH / 100


def _dial() -> str:
    """The loading dial: four quarter arcs, one per step, round the share done."""
    r, gap = 100, 7
    c = 2 * math.pi * r
    seg = c * (90 - gap) / 360
    arcs = "".join(
        f'<circle class="sgt" cx="125" cy="125" r="{r}" stroke-dasharray="{seg:.2f} {c:.2f}" '
        f'transform="rotate({-90 + 90 * k + gap / 2} 125 125)"/>'
        f'<circle class="sg" data-i="{k}" cx="125" cy="125" r="{r}" stroke-dasharray="0 {c:.2f}" '
        f'transform="rotate({-90 + 90 * k + gap / 2} 125 125)"/>' for k in range(4))
    labels = ""
    for k in range(4):
        m = math.radians(-90 + 90 * k + 45)
        labels += (f'<text class="sl" data-i="{k}" x="{125 + 124 * math.cos(m):.1f}" '
                   f'y="{129 + 124 * math.sin(m):.1f}" text-anchor="middle">0{k + 1}</text>')
    return f"""<div class="rfs-dial"><div class="rfs-sweep"></div>
      <svg viewBox="0 0 250 250" aria-hidden="true">
        <circle class="rg1" cx="125" cy="125" r="116"/><circle class="rg2" cx="125" cy="125" r="84"/>
        <circle class="rgc" cx="125" cy="125" r="72"/>{arcs}
        <circle class="hd" cx="125" cy="25" r="7"/>{labels}
      </svg>
      <div class="rfs-num"><b><i>0</i><small>%</small></b><span>SYSTEM LOAD</span></div></div>"""


def markup() -> str:
    """The layer's HTML: background, veil, lights, comet, streams, panel, Ready."""
    pts = {**CITIES, **NODES}
    links = ""
    for k, (a, b, bend) in enumerate(LINKS):
        d = _link_path(pts[a], pts[b], bend)
        if a == "tower":                    # the backbone the comet draws
            links += (f'<path class="lk bk" pathLength="1" d="{d}"/>'
                      f'<path class="st" style="--dl:0s;animation-delay:{0.35 * k:.2f}s" d="{d}"/>')
            continue
        dl = max(_reach(pts[a]), _reach(pts[b])) + 0.1 + 0.05 * k
        links += (f'<path class="lk" pathLength="1" style="--dl:{dl:.2f}s" d="{d}"/>'
                  f'<path class="st" style="--dl:{dl:.2f}s;animation-delay:{0.35 * k:.2f}s" '
                  f'd="{d}"/>')
    (x1, y1), (x2, y2) = _xy(BASRA_FROM), _xy(CITIES["basra"])
    basra = (f'<path class="bk2" pathLength="1" style="--dl:{_reach(CITIES["basra"]) + 0.2:.2f}s" '
             f'd="M{x1:.0f},{y1:.0f} C{x1 + 390:.0f},{y1 - 37:.0f} {x2 - 480:.0f},{y2 + 176:.0f} '
             f'{x2:.0f},{y2:.0f}"/>')
    # the blue light trails of the picture, from the tower towards the map
    sx, sy = _VW / 1672, _VH / 940          # the picture's own pixels
    trails = (f'<path class="tr" transform="scale({sx:.4f} {sy:.4f})" '
              'd="M0,470 C250,540 430,690 720,770 S1010,690 1100,592"/>'
              f'<path class="tr t2" transform="scale({sx:.4f} {sy:.4f})" '
              'd="M0,560 C260,650 470,820 800,845 S1080,730 1180,650"/>')
    bx, by = _xy(CITIES["baghdad"])
    tx, ty = _xy(NODES["tower"])
    fx = (f'<circle class="rfs-dawn" cx="{bx:.0f}" cy="{by:.0f}" r="10" opacity="0"/>'
          '<g class="rfs-pk">' + '<circle r="5" opacity="0"/>' * 7 + '</g>'
          '<g class="rfs-tail">' + '<circle r="1" opacity="0"/>' * 34 + '</g>'
          '<circle class="rfs-head" r="22" opacity="0"/>'
          f'<circle class="rfs-w1" cx="{bx:.0f}" cy="{by:.0f}" r="10" opacity="0"/>'
          f'<circle class="rfs-w2" cx="{bx:.0f}" cy="{by:.0f}" r="10" opacity="0"/>'
          f'<circle class="rfs-flare" cx="{tx:.0f}" cy="{ty:.0f}" r="10" opacity="0"/>')
    defs = ('<defs><radialGradient id="rfsHead"><stop offset="0" stop-color="#ffffff"/>'
            '<stop offset=".25" stop-color="#e6fbff"/><stop offset=".55" stop-color="rgba(64,200,255,.8)"/>'
            '<stop offset="1" stop-color="rgba(32,191,255,0)"/></radialGradient>'
            '<radialGradient id="rfsDawn"><stop offset="0" stop-color="rgba(255,236,200,.55)"/>'
            '<stop offset=".35" stop-color="rgba(120,200,255,.22)"/>'
            '<stop offset="1" stop-color="rgba(32,191,255,0)"/></radialGradient></defs>')
    lights = "".join(f'<i class="tw" style="left:{x}%;top:{y}%;animation-delay:{d}s"></i>'
                     for x, y, d in TOWER_LIGHTS)
    cities = "".join(f'<i class="cn" style="left:{x}%;top:{y}%;--d:{k};--dl:{_reach((x, y)):.2f}s"></i>'
                     for k, (x, y) in enumerate(CITIES.values()))
    ok = _svg('<path d="m5 12.5 4.5 4.5L19 7.5"/>', "ok")
    steps = "".join(
        f'<div class="stp" data-i="{k}"><b class="mk">{_svg(_ICON[ic], "ic")}{ok}</b>'
        f'<div><div class="nm">{label}</div><div class="sa"><em class="w">STANDBY</em>'
        f'<em class="y">SYNCING · <i>0%</i></em><em class="o">ONLINE</em></div></div></div>'
        for k, (label, ic) in enumerate(STEPS))
    return f"""
<div class="rfs-stage">
  <img class="rfs-bg" src="{BG_URL}" alt="" draggable="false" decoding="sync">
  <div class="rfs-veil"></div>
  <svg class="rfs-net" viewBox="0 0 {_VW} {_VH}" preserveAspectRatio="none">{defs}
    <g class="rfs-trails">{trails}</g><g class="rfs-links">{links}{basra}</g><g class="rfs-fx">{fx}</g>
  </svg>
  {lights}{cities}
  <div class="rfs-panel">
    <div class="rfs-status"><span>Initialising RF Analysis</span></div>
    <div class="rfs-hud">{_dial()}<div class="rfs-steps">{steps}</div></div>
  </div>
</div>
<div class="rfs-ready">
  <div class="rfs-check">{_svg('<path d="m6 12.5 4 4L18.5 8"/>')}</div>
  <div class="rfs-ready-t">Ready</div>
  <div class="rfs-ready-s">Launching RF Analysis Platform…</div>
</div>
<button class="rfs-skip" type="button">Skip</button>"""


CSS = """
@property --rfs-r { syntax: '<percentage>'; inherits: false; initial-value: 0%; }
@property --rfs-e { syntax: '<percentage>'; inherits: false; initial-value: 0%; }
.st-key-rf_startup_bus { display: none !important; }
#rf-startup { position: fixed; inset: 0; z-index: 2147483600; overflow: hidden; cursor: default;
  background: radial-gradient(ellipse at 60% 45%, #0a1a33 0, #050e1f 55%, #030916 100%);
  font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; color: #E2E8F0;
  -webkit-font-smoothing: antialiased; text-rendering: geometricPrecision;
  opacity: 1; transition: opacity .9s ease; }
#rf-startup.leave { opacity: 0; pointer-events: none; }
/* the picture fills the whole screen (16:9: a laptop screen exactly), centred
   without any transform, never stretched out of its proportions */
#rf-startup .rfs-stage { position: absolute; inset: 0; margin: auto;
  width: max(100vw, calc(100vh * ASPECT)); height: calc(max(100vw, calc(100vh * ASPECT)) / ASPECT);
  container-type: inline-size; }
#rf-startup .rfs-bg { position: absolute; inset: 0; width: 100%; height: 100%; display: block;
  user-select: none; image-rendering: auto; opacity: .3; transition: opacity 1.4s ease; }
#rf-startup.s1 .rfs-bg { opacity: 1; }
#rf-startup .rfs-dim { position: absolute; inset: 0; background: rgba(3, 9, 22, 0);
  transition: background .8s ease; pointer-events: none; }
/* the map, all dark until the comet lands, then day spreads from Baghdad */
#rf-startup .rfs-veil { position: absolute; left: 46%; top: 0; width: 54%; height: 100%;
  background: rgba(3, 9, 22, .9); --rfs-r: 0%; --rfs-e: 0%;
  -webkit-mask-image: radial-gradient(circle at 51.3% 49.8%, transparent var(--rfs-r), #000 calc(var(--rfs-r) + var(--rfs-e))),
                      linear-gradient(to right, transparent, #000 14%);
  -webkit-mask-composite: source-in;
  mask-image: radial-gradient(circle at 51.3% 49.8%, transparent var(--rfs-r), #000 calc(var(--rfs-r) + var(--rfs-e))),
              linear-gradient(to right, transparent, #000 14%);
  mask-composite: intersect;
  transition: --rfs-r REVEALs cubic-bezier(.45, .05, .3, 1), --rfs-e .3s ease; }
#rf-startup.s2 .rfs-veil { --rfs-r: 125%; --rfs-e: 16%; }
/* tower beacons */
#rf-startup .tw { position: absolute; width: .9cqw; height: .9cqw; margin: -.45cqw 0 0 -.45cqw;
  border-radius: 50%; background: radial-gradient(circle, #ffd7d0 0, #ff3b30 35%, rgba(255, 40, 30, 0) 70%);
  box-shadow: 0 0 1.6cqw .5cqw rgba(255, 45, 35, .55); animation: rfsBlink 2.4s ease-in-out infinite;
  mix-blend-mode: screen; }
@keyframes rfsBlink { 0%, 100% { opacity: .25; } 45% { opacity: 1; } }
/* city nodes: lit as the day reaches them */
#rf-startup .cn { position: absolute; width: 1.1cqw; height: 1.1cqw; margin: -.55cqw 0 0 -.55cqw;
  border-radius: 50%; opacity: 0; mix-blend-mode: screen;
  background: radial-gradient(circle, #fff7e0 0, #ffb347 30%, rgba(255, 150, 40, 0) 70%);
  box-shadow: 0 0 2.2cqw .7cqw rgba(255, 160, 60, .45);
  transition: opacity .6s ease; transition-delay: var(--dl); }
#rf-startup .cn::after { content: ""; position: absolute; inset: -.9cqw; border-radius: 50%;
  border: .12cqw solid rgba(32, 191, 255, .75); opacity: 0; }
#rf-startup.s2 .cn { opacity: 1; }
#rf-startup.s3 .cn::after { animation: rfsRing 2.6s ease-out infinite;
  animation-delay: calc(var(--d) * .45s); }
@keyframes rfsRing { 0% { opacity: .8; transform: scale(.35); } 100% { opacity: 0; transform: scale(1.6); } }
/* network links and the data moving along them: crisp strokes, a light glow */
#rf-startup .rfs-net { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible;
  mix-blend-mode: screen; }
#rf-startup .lk { fill: none; stroke: rgba(120, 200, 255, .5); stroke-width: 1.4;
  stroke-dasharray: 1 1; stroke-dashoffset: 1;
  transition: stroke-dashoffset .8s ease var(--dl); }
#rf-startup.s2 .lk { stroke-dashoffset: 0; }
#rf-startup .lk.bk { stroke: rgba(150, 220, 255, .55); stroke-width: 1.8; stroke-dasharray: 0 1;
  stroke-dashoffset: 0; transition: none; filter: drop-shadow(0 0 4px #20bfff); }
#rf-startup .bk2 { fill: none; stroke: rgba(170, 230, 255, .85); stroke-width: 2.2;
  stroke-dasharray: 1 1; stroke-dashoffset: 1;
  filter: drop-shadow(0 0 5px #20bfff); transition: stroke-dashoffset 1.1s ease var(--dl); }
#rf-startup.s2 .bk2 { stroke-dashoffset: 0; }
#rf-startup .rfs-head, #rf-startup .rfs-tail circle, #rf-startup .rfs-pk circle,
#rf-startup .rfs-flare { fill: url(#rfsHead); }
#rf-startup .rfs-tail circle { fill: #bff0ff; }
#rf-startup .rfs-dawn { fill: url(#rfsDawn); }
#rf-startup .rfs-w1, #rf-startup .rfs-w2 { fill: none; stroke: #bff0ff; stroke-width: 3; }
#rf-startup .rfs-w2 { stroke: #8fe4ff; stroke-width: 1.6; }
#rf-startup .st { fill: none; stroke: #9be2ff; stroke-width: 2.4; stroke-linecap: round;
  opacity: 0; transition: opacity .5s ease calc(var(--dl) + .7s);
  vector-effect: non-scaling-stroke; stroke-dasharray: 14 240;
  filter: drop-shadow(0 0 3px #20bfff); animation: rfsFlow 2.8s linear infinite; }
#rf-startup.s2 .st { opacity: 1; }
@keyframes rfsFlow { from { stroke-dashoffset: 254; } to { stroke-dashoffset: 0; } }
#rf-startup .tr { fill: none; stroke: rgba(150, 215, 255, .6); stroke-width: 2; stroke-linecap: round;
  vector-effect: non-scaling-stroke; stroke-dasharray: 120 2600;
  filter: drop-shadow(0 0 4px #1597ff); animation: rfsTrail 6s linear infinite; }
#rf-startup .tr.t2 { animation-duration: 8s; animation-delay: -3s; opacity: .7; }
@keyframes rfsTrail { from { stroke-dashoffset: 2720; } to { stroke-dashoffset: 0; } }
/* the loading panel under the title */
#rf-startup .rfs-panel { position: absolute; left: 17.2%; top: 47.5%; width: 32%;
  opacity: 0; transition: opacity .8s ease; }
#rf-startup.s1 .rfs-panel { opacity: 1; transition-delay: .5s; }
#rf-startup.ready .rfs-panel { opacity: 0; transition-delay: 0s; }
#rf-startup .rfs-status { height: 1.8cqw; font-size: max(13px, 1.2cqw); font-weight: 600;
  color: #56c8ff; letter-spacing: .005em; white-space: nowrap;
  text-shadow: 0 1px 3px rgba(0, 0, 0, .8); }
#rf-startup .rfs-status span { display: inline-block; transition: opacity .25s ease; }
#rf-startup .rfs-status span.out { opacity: 0; }
#rf-startup .rfs-hud { display: flex; align-items: center; gap: 1.35cqw; margin-top: .7cqw;
  padding: .95cqw 1.15cqw; border-radius: 16px;
  background: linear-gradient(180deg, rgba(10, 26, 48, .86), rgba(5, 14, 28, .9));
  border: 1px solid rgba(64, 160, 255, .26);
  box-shadow: 0 16px 44px rgba(0, 0, 0, .5), inset 0 1px 0 rgba(160, 215, 255, .08); }
#rf-startup .rfs-dial { position: relative; flex: 0 0 13cqw; width: 13cqw; height: 13cqw; }
#rf-startup .rfs-dial svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
#rf-startup .rfs-sweep { position: absolute; inset: 14.8%; border-radius: 50%;
  background: conic-gradient(from 0deg, rgba(32, 191, 255, 0) 0 70%, rgba(32, 191, 255, .05) 80%,
                             rgba(32, 191, 255, .35) 100%);
  animation: rfsSpin 2.6s linear infinite; }
#rf-startup .rg1, #rf-startup .rg2 { fill: none; transform-box: fill-box; transform-origin: center; }
#rf-startup .rg1 { stroke: rgba(32, 191, 255, .35); stroke-width: 1.2; stroke-dasharray: 2 7;
  animation: rfsSpin 9s linear infinite; }
#rf-startup .rg2 { stroke: rgba(32, 191, 255, .28); stroke-width: 1; stroke-dasharray: 26 10 4 10;
  animation: rfsSpin 14s linear infinite reverse; }
@keyframes rfsSpin { to { transform: rotate(360deg); } }
#rf-startup .rgc { fill: rgba(3, 9, 22, .55); stroke: rgba(32, 191, 255, .2); stroke-width: 1; }
#rf-startup .sgt, #rf-startup .sg { fill: none; stroke-width: 10; }
#rf-startup .sgt { stroke: rgba(148, 163, 184, .16); }
#rf-startup .sg { stroke: #20bfff; filter: drop-shadow(0 0 4px #20bfff); }
#rf-startup .hd { fill: #e8fbff; opacity: 0; filter: drop-shadow(0 0 6px #8fe4ff); }
#rf-startup .sl { font-size: 11px; font-weight: 700; letter-spacing: 1px; fill: #56708f; }
#rf-startup .sl.on { fill: #e8fbff; }
#rf-startup .sl.ok { fill: #20bfff; }
#rf-startup .rfs-num { position: absolute; inset: 0; display: flex; flex-direction: column;
  align-items: center; justify-content: center; text-align: center; }
#rf-startup .rfs-num b { font-size: 2.6cqw; font-weight: 700; color: #F8FAFC; line-height: 1;
  font-variant-numeric: tabular-nums; text-shadow: 0 0 18px rgba(32, 191, 255, .55); }
#rf-startup .rfs-num i { font-style: normal; }
#rf-startup .rfs-num small { font-size: 1.15cqw; color: #8fdcff; }
#rf-startup .rfs-num span { margin-top: .35cqw; font-size: max(9px, .58cqw); letter-spacing: .24em;
  color: #7fa6d0; }
#rf-startup .rfs-steps { flex: 1; display: flex; flex-direction: column; gap: .65cqw; }
#rf-startup .stp { display: flex; align-items: center; gap: .65cqw; color: #56708f; }
#rf-startup .stp .mk { position: relative; flex: 0 0 auto; width: max(26px, 1.75cqw);
  height: max(26px, 1.75cqw); border-radius: 50%; display: flex; align-items: center;
  justify-content: center; border: 2px solid rgba(148, 163, 184, .3); box-sizing: border-box;
  transition: background .4s ease, border-color .4s ease, box-shadow .4s ease; }
#rf-startup .stp .ic { width: 55%; height: 55%; }
#rf-startup .stp .ok { position: absolute; width: 58%; height: 58%; color: #fff; opacity: 0;
  stroke-width: 2.8; stroke-dasharray: 30; stroke-dashoffset: 30;
  transition: stroke-dashoffset .4s ease .1s, opacity .2s ease; }
#rf-startup .stp .nm { font-size: max(12px, .8cqw); font-weight: 600; color: #8a9ab2;
  white-space: nowrap; transition: color .4s ease; }
#rf-startup .stp .sa { margin-top: .1cqw; font-size: max(9.5px, .6cqw); letter-spacing: .06em;
  font-variant-numeric: tabular-nums; white-space: nowrap; }
#rf-startup .stp .sa em { font-style: normal; display: none; }
#rf-startup .stp .sa i { font-style: normal; }
#rf-startup .stp .sa .w { display: inline; color: #56708f; }
#rf-startup .stp.active { color: #8fdcff; }
#rf-startup .stp.active .mk { border-color: #20bfff; animation: rfsPulse 1.2s ease-in-out infinite; }
#rf-startup .stp.active .nm, #rf-startup .stp.done .nm { color: #F1F5F9; }
#rf-startup .stp.active .sa .w, #rf-startup .stp.done .sa .w { display: none; }
#rf-startup .stp.active .sa .y { display: inline; color: #56c8ff; }
#rf-startup .stp.done .sa .o { display: inline; color: #34d7a0; }
#rf-startup .stp.done .mk { border-color: #20bfff; background: linear-gradient(135deg, #1c8dff, #0b5fd6);
  box-shadow: 0 0 12px rgba(32, 191, 255, .6); }
#rf-startup .stp.done .ic { display: none; }
#rf-startup .stp.done .ok { opacity: 1; stroke-dashoffset: 0; }
@keyframes rfsPulse { 0%, 100% { box-shadow: 0 0 6px rgba(32, 191, 255, .45); }
  50% { box-shadow: 0 0 16px rgba(32, 191, 255, 1); } }
/* ready */
#rf-startup.ready .rfs-dim { background: rgba(3, 9, 22, .55); }
#rf-startup .rfs-ready { position: absolute; inset: 0; margin: auto; width: 520px; height: 260px;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  text-align: center; opacity: 0; pointer-events: none; transition: opacity .7s ease;
  background: radial-gradient(ellipse at center, rgba(3, 9, 22, .82) 0, rgba(3, 9, 22, .5) 45%,
                              transparent 72%); }
#rf-startup.ready .rfs-ready { opacity: 1; }
#rf-startup .rfs-check { width: 96px; height: 96px; margin-bottom: 14px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center; color: #56c8ff;
  border: 3px solid #20bfff; background: radial-gradient(circle, rgba(21, 101, 255, .25), rgba(3, 9, 22, .2) 70%);
  box-shadow: 0 0 28px rgba(32, 191, 255, .65), inset 0 0 18px rgba(32, 191, 255, .35); }
#rf-startup .rfs-check svg { width: 52px; height: 52px; stroke-width: 2.4;
  stroke-dasharray: 30; stroke-dashoffset: 30; }
#rf-startup.ready .rfs-check svg { animation: rfsTick .6s .25s ease-out forwards; }
@keyframes rfsTick { to { stroke-dashoffset: 0; } }
#rf-startup .rfs-ready-t { font-size: 30px; font-weight: 700; color: #F8FAFC; }
#rf-startup .rfs-ready-s { margin-top: 6px; font-size: 15px; color: #CBD5E1; }
#rf-startup .rfs-skip { position: absolute; right: 22px; bottom: 18px; z-index: 1;
  background: rgba(7, 21, 37, .6); color: #94A3B8; border: 1px solid rgba(80, 130, 190, .35);
  border-radius: 8px; padding: 5px 14px; font: 600 12px 'Segoe UI', system-ui, sans-serif;
  cursor: pointer; }
#rf-startup .rfs-skip:hover { color: #E2E8F0; border-color: #1597FF; }
@media (prefers-reduced-motion: reduce) {
  #rf-startup .st, #rf-startup .tr, #rf-startup .tw, #rf-startup.s3 .cn::after,
  #rf-startup .stp.active .mk, #rf-startup .rfs-sweep, #rf-startup .rg1,
  #rf-startup .rg2 { animation: none !important; }
}
""".replace("ASPECT", f"{_ASPECT:.4f}").replace("REVEALs", f"{REVEAL}s")

JS = """
(function () {
  var KEY = 'rf_startup_done', doc = document, cfg = __CFG__;
  var noop = { msg: function () {} };
  var seen = false;
  try { seen = !!sessionStorage.getItem(KEY); } catch (e) {}
  // already prepared, and this tab has seen the startup: nothing to show
  if ((cfg.warm && seen) || doc.getElementById('rf-startup')) { window.rfStartup = noop; return; }
  var st = doc.createElement('style'); st.id = 'rf-startup-css'; st.textContent = cfg.css;
  doc.head.appendChild(st);
  var root = doc.createElement('div'); root.id = 'rf-startup'; root.innerHTML = cfg.html;
  root.setAttribute('role', 'status'); root.setAttribute('aria-live', 'polite');
  var dim = doc.createElement('div'); dim.className = 'rfs-dim';
  root.querySelector('.rfs-stage').appendChild(dim);
  doc.body.appendChild(root);
  try { sessionStorage.setItem(KEY, '1'); } catch (e) {}

  var q = function (s) { return root.querySelector(s); };
  var pct = q('.rfs-num i'), label = q('.rfs-status span');
  var steps = root.querySelectorAll('.stp'), segs = root.querySelectorAll('.sg');
  var slabs = root.querySelectorAll('.sl'), head = q('.hd');
  var total = 0, done = 0, shownPct = 0, serverReady = false, ended = false, readyAt = 0;
  var stepTotal = [0, 0, 0, 0], stepDone = [0, 0, 0, 0], stepShown = [0, 0, 0, 0], current = -1;
  var t0 = performance.now(), lastWord = t0, raf = 0, timers = [];
  // the dial: four quarter arcs, 7 degrees apart, round a circle of radius 100
  var CIRC = 2 * Math.PI * 100, SEG = CIRC * 83 / 360;

  // the scene: the comet, day breaking over the map, the backbones' packets
  var reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  var LAUNCH = cfg.launch, FLY = reduce ? 0 : cfg.fly, ARRIVE = LAUNCH + FLY;
  var bk = q('.lk.bk'), BKL = bk.getTotalLength();
  var bk2 = q('.bk2'), B2L = bk2.getTotalLength(), BASRA_AT = ARRIVE + cfg.basraAt;
  var comet = q('.rfs-head'), flare = q('.rfs-flare'), w1 = q('.rfs-w1'), w2 = q('.rfs-w2');
  var dawn = q('.rfs-dawn'), tail = root.querySelectorAll('.rfs-tail circle');
  var pks = root.querySelectorAll('.rfs-pk circle');
  function clamp(x) { return x < 0 ? 0 : x > 1 ? 1 : x; }
  function out3(x) { return 1 - Math.pow(1 - x, 3); }
  function io3(x) { return x < .5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; }
  function at(el, k, v) { el.setAttribute(k, v); }
  function scene(t) {
    // the flare at the tower beacon as the signal leaves
    var lf = clamp((t - LAUNCH + .35) / .45), lo = 1 - clamp((t - LAUNCH) / .5);
    at(flare, 'r', 8 + 32 * lf); at(flare, 'opacity', reduce ? 0 : lf * lo);
    // the comet, its tail, and the backbone it leaves behind
    var fly = FLY ? clamp((t - LAUNCH) / FLY) : 1, s = io3(fly) * BKL;
    var flying = FLY && t >= LAUNCH && t < ARRIVE;
    if (flying) {
      var h = bk.getPointAtLength(s);
      at(comet, 'cx', h.x); at(comet, 'cy', h.y); at(comet, 'r', 20 + 5 * Math.sin(t * 30));
      at(comet, 'opacity', 1);
      for (var i = 0; i !== tail.length; i++) {
        var back = s - (i + 1) * 8.5, c = tail[i];
        if (back < 0) { at(c, 'opacity', 0); continue; }
        var pt = bk.getPointAtLength(back), f = 1 - i / tail.length, j = (i % 3 - 1) * 2 * (1 - f);
        at(c, 'cx', pt.x + j); at(c, 'cy', pt.y - j); at(c, 'r', 1 + 9 * f * f); at(c, 'opacity', .85 * f);
      }
    } else if (comet.getAttribute('opacity') !== '0') {
      at(comet, 'opacity', 0);
      for (var k = 0; k !== tail.length; k++) at(tail[k], 'opacity', 0);
    }
    bk.style.strokeDasharray = (t < LAUNCH ? 0 : flying ? s / BKL : 1) + ' 1';
    // the landing: two shock waves and the first light of day over Baghdad
    var a1 = clamp((t - ARRIVE) / 1.2), a2 = clamp((t - ARRIVE - .25) / 1.5), d = clamp((t - ARRIVE) / 1.6);
    at(w1, 'r', 10 + 220 * out3(a1)); at(w1, 'opacity', t >= ARRIVE ? (1 - a1) * .95 : 0);
    at(w2, 'r', 10 + 390 * out3(a2)); at(w2, 'opacity', t >= ARRIVE + .25 ? (1 - a2) * .7 : 0);
    at(dawn, 'r', 40 + 570 * out3(d));
    at(dawn, 'opacity', t >= ARRIVE ? (d < .25 ? d / .25 : 1 - .75 * clamp((d - .25) / .75)) : 0);
    // data that keeps running on the two backbones
    for (var n = 0; n !== pks.length; n++) {
      var on, u, p2;
      if (n < 4) { on = t > ARRIVE + .5; u = ((t - ARRIVE) / 2.2 + n / 4) % 1; }
      else { on = t > BASRA_AT; u = ((t - BASRA_AT) / 2 + (n - 4) / 3) % 1; }
      if (!on || reduce) { at(pks[n], 'opacity', 0); continue; }
      p2 = (n < 4 ? bk : bk2).getPointAtLength((n < 4 ? BKL : B2L) * u);
      var g = Math.sin(Math.PI * u);
      at(pks[n], 'cx', p2.x); at(pks[n], 'cy', p2.y); at(pks[n], 'r', 5 + 3 * g); at(pks[n], 'opacity', g);
    }
  }

  function setStatus(text) {
    if (!text || label.textContent === text) return;
    label.classList.add('out');
    timers.push(setTimeout(function () { label.textContent = text; label.classList.remove('out'); }, 220));
  }
  function setStep(k) {
    current = k;
    for (var i = 0; i !== steps.length; i++) {
      steps[i].classList.toggle('done', i < k);
      steps[i].classList.toggle('active', i === k);
      slabs[i].classList.toggle('ok', i < k);
      slabs[i].classList.toggle('on', i === k);
    }
  }
  function finish() {
    if (ended) return;
    ended = true;
    timers.forEach(clearTimeout); cancelAnimationFrame(raf);
    root.classList.add('leave');
    setTimeout(function () { root.remove(); st.remove(); }, 950);
    doc.removeEventListener('keydown', onKey, true);
    window.rfStartup = noop;
  }
  function onKey(e) { if (e.key === 'Escape') finish(); }
  doc.addEventListener('keydown', onKey, true);
  q('.rfs-skip').addEventListener('click', finish);

  // the dial follows the tasks done; it only eases between real values
  function frame(now) {
    var t = (now - t0) / 1000;
    scene(t);
    var target = total ? 100 * done / total : 0;
    shownPct += (target - shownPct) * 0.12;
    if (Math.abs(target - shownPct) < 0.05) shownPct = target;
    pct.textContent = Math.floor(shownPct + 1e-6);
    for (var k = 0; k !== 4; k++) {
      var goal = stepTotal[k] ? stepDone[k] / stepTotal[k] : (k < current ? 1 : 0);
      if (k < current) goal = 1;
      stepShown[k] += (goal - stepShown[k]) * 0.12;
      if (Math.abs(goal - stepShown[k]) < 0.002) stepShown[k] = goal;
      segs[k].setAttribute('stroke-dasharray', (SEG * stepShown[k]).toFixed(2) + ' ' + CIRC.toFixed(2));
      var y = steps[k].querySelector('.y i');
      if (y) y.textContent = Math.floor(stepShown[k] * 100) + '%';
    }
    if (current >= 0 && current < 4) {
      var ang = (-90 + 90 * current + 3.5 + 83 * stepShown[current]) * Math.PI / 180;
      head.setAttribute('cx', 125 + 100 * Math.cos(ang)); head.setAttribute('cy', 125 + 100 * Math.sin(ang));
      head.style.opacity = 1;
    } else { head.style.opacity = 0; }
    if (serverReady && !readyAt && shownPct >= 100 && (now - t0) / 1000 >= cfg.minShow) {
      readyAt = now;
      setStep(steps.length);
      setStatus('Ready');
      timers.push(setTimeout(function () { root.classList.add('ready'); }, 350));
      timers.push(setTimeout(finish, 1700));
    }
    if (!serverReady && (now - lastWord) / 1000 > cfg.stall) { finish(); return; }
    if (!ended) raf = requestAnimationFrame(frame);
  }
  window.rfStartup = {
    msg: function (m) {
      lastWord = performance.now();
      if (m.type === 'begin') { total = m.total || 0; stepTotal = m.steps || stepTotal; }
      else if (m.type === 'task') { setStatus(m.label); setStep(m.step); }
      else if (m.type === 'done') {
        done = Math.min(total, done + 1);
        if (m.step >= 0 && m.step < 4) stepDone[m.step] = Math.min(stepTotal[m.step] || 1e9, stepDone[m.step] + 1);
      }
      else if (m.type === 'ready') {
        serverReady = true; done = total; stepDone = stepTotal.slice();
        setStatus('Almost Ready');
      }
    }
  };
  requestAnimationFrame(function () { root.classList.add('s1'); });
  // day breaks where the comet lands
  timers.push(setTimeout(function () { root.classList.add('s2'); }, ARRIVE * 1000));
  timers.push(setTimeout(function () { root.classList.add('s3'); }, (ARRIVE + 1.3) * 1000));
  raf = requestAnimationFrame(frame);
})();
"""


def _encoded(js: str) -> str:
    """st.html sanitises a script whose text holds markup (this one carries the
    layer's) and drops it whole: it rides base64-encoded, run by a loader free
    of any markup."""
    b64 = base64.b64encode(js.encode("utf-8")).decode("ascii")
    return ("<script>(function(){var s=atob('" + b64 + "'),b=new Uint8Array(s.length);"
            "for(var i=0;i!==s.length;i++)b[i]=s.charCodeAt(i);"
            "new Function(new TextDecoder().decode(b))();})();</script>")


def show(warm: bool = False) -> None:
    """Put the startup screen up. `warm`: everything is already prepared in
    this server process — a tab that has seen the startup then skips it."""
    cfg = {"css": CSS, "html": markup(), "warm": bool(warm), "minShow": MIN_SHOW,
           "stall": STALL, "launch": LAUNCH, "fly": FLY,
           "basraAt": _reach(CITIES["basra"]) + 0.2 + 1.1}
    js = JS.replace("__CFG__", json.dumps(cfg).replace("</", "<\\/"))
    st.html(_encoded(js), unsafe_allow_javascript=True)


def message(m: dict) -> str:
    """One preparation message for the screen, as a script free of markup."""
    return ("<script>window.rfStartup&&window.rfStartup.msg("
            + json.dumps(m).replace("<", "\\u003c").replace(">", "\\u003e") + ")</script>")


def run_preparation() -> list[dict]:
    """The real preparation (`_warmup`), reported to the startup screen as it
    goes; the screen says Ready when it is done. Returns the tasks' results."""
    import _warmup
    bus = st.container(key="rf_startup_bus")

    def report(m: dict) -> None:
        bus.html(message(m), unsafe_allow_javascript=True)

    return _warmup.run(report)


__all__ = ["BG_URL", "CITIES", "LINKS", "STEPS", "background", "markup", "message",
           "run_preparation", "show"]
