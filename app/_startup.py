"""The startup screen: the RF Optimization platform initialising, once per browser tab.

One continuous animation over one background — the Huawei RF Optimization
night view of Iraq (`static/startup/rf_startup_bg.jpg`, served by the app) —
never a sequence of pictures:

  0 – 1.5 s   the view lights up, the tower beacons blink       Initialising
  1.5 – 3.5 s the map is revealed from Baghdad outward, the     Loading Network Data
              city nodes light up, data streams start
  3.5 – 10 s  the loading panel under the title: one progress   Processing Network Data
              bar, four steps turning active then done          Preparing Map Services
                                                                Preparing Analysis Engine
                                                                Almost Ready
  10 – 11 s   Ready, then the screen fades into the app

Every animated element is placed in the background's own coordinates (per
cent of the picture), so it stays on its tower light or city at any window
size. The layer lives outside Streamlit's page (added to the document body by
`show`), so the app loads underneath it and nothing of the app changes once it
has gone. Esc or "Skip" ends it at once; a reduced-motion setting shortens it
and stills the streams.
"""

from __future__ import annotations

import base64
import json

import streamlit as st

BG_URL = "/app/static/startup/rf_startup_bg.jpg"
_ASPECT = 2392 / 898                    # the background's width / height

# the background's own coordinates, per cent of its width / height
TOWER_LIGHTS = [(4.05, 34.0, 0.0), (4.05, 46.0, 0.6), (0.85, 74.5, 1.1), (6.0, 75.0, 1.7)]
CITIES = {                              # the lit city nodes under the map pins
    "baghdad": (72.33, 55.39), "basra": (87.96, 78.2), "mosul": (64.38, 25.38),
    "erbil": (75.67, 32.49), "anbar": (54.83, 61.22),
}
NODES = {"east": (79.82, 54.9), "south": (74.46, 83.65), "west": (40.55, 25.2),
         "tower": (4.05, 46.0)}
# the network links the data streams run along (from, to, bend in % of width)
LINKS = [("anbar", "baghdad", -3), ("baghdad", "mosul", 3), ("mosul", "erbil", -3),
         ("erbil", "baghdad", 3), ("baghdad", "east", -2), ("east", "basra", 3),
         ("baghdad", "south", -3), ("south", "basra", -3), ("anbar", "mosul", 4),
         ("west", "mosul", -3), ("tower", "anbar", -16)]

STEPS = [("Data Resources", "db"), ("Network Data", "net"),
         ("Map Services", "map"), ("Analysis Engine", "engine")]
# (second, per cent, status, step now active — a step before it is done)
TIMELINE = [(0.0, 0, "Initialising RF Optimization", -1),
            (2.2, 4, "Loading Network Data", 0),
            (4.0, 32, "Processing Network Data", 1),
            (6.0, 60, "Preparing Map Services", 2),
            (7.3, 78, "Preparing Analysis Engine", 3),
            (8.5, 92, "Almost Ready", 4),
            (9.6, 100, "Almost Ready", 4)]
READY_AT, LEAVE_AT = 10.0, 11.2

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
    """A gentle curve between two points of the picture, in its pixel space."""
    (x1, y1), (x2, y2) = a, b
    X1, Y1, X2, Y2 = x1 * 23.92, y1 * 8.98, x2 * 23.92, y2 * 8.98
    mx, my = (X1 + X2) / 2, (Y1 + Y2) / 2
    dx, dy = X2 - X1, Y2 - Y1
    n = (dx * dx + dy * dy) ** 0.5 or 1.0
    k = bend * 23.92
    cx, cy = mx - dy / n * k, my + dx / n * k
    return f"M{X1:.0f},{Y1:.0f} Q{cx:.0f},{cy:.0f} {X2:.0f},{Y2:.0f}"


def markup() -> str:
    """The layer's HTML: background, veil, lights, streams, panel, Ready."""
    pts = {**CITIES, **NODES}
    links = "".join(
        f'<path class="lk" d="{_link_path(pts[a], pts[b], bend)}"/>'
        f'<path class="st" style="animation-delay:{0.35 * k:.2f}s" '
        f'd="{_link_path(pts[a], pts[b], bend)}"/>'
        for k, (a, b, bend) in enumerate(LINKS))
    trails = ('<path class="tr" d="M0,760 C420,640 760,905 1180,780 S1720,640 2392,720"/>'
              '<path class="tr t2" d="M0,820 C520,700 900,880 1320,820 S1900,760 2392,800"/>')
    lights = "".join(f'<i class="tw" style="left:{x}%;top:{y}%;animation-delay:{d}s"></i>'
                     for x, y, d in TOWER_LIGHTS)
    cities = "".join(f'<i class="cn" style="left:{x}%;top:{y}%;--d:{k}"></i>'
                     for k, (x, y) in enumerate(CITIES.values()))
    ok = _svg('<path d="m5 12.5 4.5 4.5L19 7.5"/>', "ok")
    steps = "".join(f'<div class="stp" data-i="{k}">{_svg(_ICON[ic], "ic")}<span>{label}</span>'
                    f'<b class="mk"><em></em>{ok}</b></div>' for k, (label, ic) in enumerate(STEPS))
    return f"""
<div class="rfs-blur"></div>
<div class="rfs-stage">
  <img class="rfs-bg" src="{BG_URL}" alt="" draggable="false">
  <div class="rfs-veil"></div>
  <svg class="rfs-net" viewBox="0 0 2392 898" preserveAspectRatio="none">
    <defs><filter id="rfsGlow"><feGaussianBlur stdDeviation="5"/></filter></defs>
    <g class="rfs-trails">{trails}</g><g class="rfs-links">{links}</g>
  </svg>
  {lights}{cities}
  <div class="rfs-panel">
    <div class="rfs-status"><span></span></div>
    <div class="rfs-bar"><div class="rfs-track"><i></i></div><b>0%</b></div>
    <div class="rfs-steps">{steps}</div>
  </div>
</div>
<div class="rfs-ready">
  <div class="rfs-check">{_svg('<path d="m6 12.5 4 4L18.5 8"/>')}</div>
  <div class="rfs-ready-t">Ready</div>
  <div class="rfs-ready-s">Launching RF Optimization Platform…</div>
</div>
<button class="rfs-skip" type="button">Skip</button>"""


CSS = """
@property --rfs-r { syntax: '<percentage>'; inherits: false; initial-value: 0%; }
#rf-startup { position: fixed; inset: 0; z-index: 2147483600; background: #030916;
  overflow: hidden; font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
  color: #E2E8F0; opacity: 1; transition: opacity .9s ease; cursor: default; }
#rf-startup.leave { opacity: 0; pointer-events: none; }
#rf-startup .rfs-blur { position: absolute; inset: -40px; background: #030916 center/cover no-repeat;
  filter: blur(34px) brightness(.3) saturate(1.1); transform: scale(1.1); }
#rf-startup::after { content: ""; position: absolute; inset: 0; pointer-events: none;
  background: linear-gradient(to bottom, #030916 0, rgba(3, 9, 22, .55) 14%, transparent 24%,
                              transparent 76%, rgba(3, 9, 22, .55) 86%, #030916 100%); }
#rf-startup .rfs-stage { position: absolute; left: 50%; top: 50%;
  width: max(100vw, calc(64vh * ASPECT)); aspect-ratio: ASPECT;
  transform: translate(-50%, -50%) scale(1.035); container-type: inline-size;
  animation: rfsDrift 12s ease-out forwards;
  -webkit-mask-image: linear-gradient(to bottom, transparent 0, #000 7%, #000 93%, transparent 100%);
          mask-image: linear-gradient(to bottom, transparent 0, #000 7%, #000 93%, transparent 100%); }
@keyframes rfsDrift { to { transform: translate(-50%, -50%) scale(1); } }
#rf-startup .rfs-bg { position: absolute; inset: 0; width: 100%; height: 100%; display: block;
  user-select: none; filter: brightness(.25) saturate(.8);
  transition: filter 1.5s ease; }
#rf-startup.s1 .rfs-bg { filter: brightness(.95) saturate(1); }
#rf-startup.ready .rfs-bg { filter: brightness(.42) saturate(.85) blur(1.5px); transition-duration: .9s; }
/* the map, dark until it is revealed from Baghdad outward */
#rf-startup .rfs-veil { position: absolute; left: 46%; top: 0; width: 54%; height: 100%;
  background: rgba(3, 9, 22, .9); --rfs-r: 0%;
  -webkit-mask-image: radial-gradient(circle at 49% 55%, transparent var(--rfs-r), #000 calc(var(--rfs-r) + 16%)),
                      linear-gradient(to right, transparent, #000 14%);
  -webkit-mask-composite: source-in;
  mask-image: radial-gradient(circle at 49% 55%, transparent var(--rfs-r), #000 calc(var(--rfs-r) + 16%)),
              linear-gradient(to right, transparent, #000 14%);
  mask-composite: intersect; transition: --rfs-r 2s cubic-bezier(.45, .05, .3, 1); }
#rf-startup.s2 .rfs-veil { --rfs-r: 125%; }
/* tower beacons */
#rf-startup .tw { position: absolute; width: .9cqw; height: .9cqw; margin: -.45cqw 0 0 -.45cqw;
  border-radius: 50%; background: radial-gradient(circle, #ffd7d0 0, #ff3b30 35%, rgba(255, 40, 30, 0) 70%);
  box-shadow: 0 0 1.6cqw .5cqw rgba(255, 45, 35, .55); animation: rfsBlink 2.4s ease-in-out infinite;
  mix-blend-mode: screen; }
@keyframes rfsBlink { 0%, 100% { opacity: .25; transform: scale(.8); } 45% { opacity: 1; transform: scale(1.15); } }
/* city nodes: lit one after the other as the map is revealed */
#rf-startup .cn { position: absolute; width: 1.1cqw; height: 1.1cqw; margin: -.55cqw 0 0 -.55cqw;
  border-radius: 50%; opacity: 0; transform: scale(.3); mix-blend-mode: screen;
  background: radial-gradient(circle, #fff7e0 0, #ffb347 30%, rgba(255, 150, 40, 0) 70%);
  box-shadow: 0 0 2.2cqw .7cqw rgba(255, 160, 60, .45);
  transition: opacity .7s ease, transform .7s cubic-bezier(.2, .9, .3, 1.3);
  transition-delay: calc(.6s + var(--d) * .28s); }
#rf-startup .cn::after { content: ""; position: absolute; inset: -.9cqw; border-radius: 50%;
  border: .12cqw solid rgba(32, 191, 255, .75); opacity: 0; }
#rf-startup.s2 .cn { opacity: 1; transform: scale(1); }
#rf-startup.s3 .cn::after { animation: rfsRing 2.6s ease-out infinite;
  animation-delay: calc(var(--d) * .45s); }
@keyframes rfsRing { 0% { opacity: .8; transform: scale(.35); } 100% { opacity: 0; transform: scale(1.6); } }
/* network links and the data moving along them */
#rf-startup .rfs-net { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible;
  mix-blend-mode: screen; }
#rf-startup .rfs-links { opacity: 0; transition: opacity 1.2s ease .9s; }
#rf-startup.s2 .rfs-links { opacity: 1; }
#rf-startup .lk { fill: none; stroke: rgba(56, 170, 255, .22); stroke-width: 2; }
#rf-startup .st { fill: none; stroke: #7fd8ff; stroke-width: 3.2; stroke-linecap: round;
  stroke-dasharray: 34 560; filter: url(#rfsGlow) drop-shadow(0 0 4px #20bfff);
  animation: rfsFlow 2.8s linear infinite; }
@keyframes rfsFlow { from { stroke-dashoffset: 594; } to { stroke-dashoffset: 0; } }
#rf-startup .tr { fill: none; stroke: rgba(120, 200, 255, .55); stroke-width: 3; stroke-linecap: round;
  stroke-dasharray: 120 2600; filter: drop-shadow(0 0 6px #1597ff);
  animation: rfsTrail 6s linear infinite; }
#rf-startup .tr.t2 { animation-duration: 8s; animation-delay: -3s; opacity: .7; }
@keyframes rfsTrail { from { stroke-dashoffset: 2720; } to { stroke-dashoffset: 0; } }
/* the loading panel under the title */
#rf-startup .rfs-panel { position: absolute; left: 10.4%; top: 66.5%; width: 33%;
  opacity: 0; transform: translateY(1cqw); transition: opacity .8s ease, transform .8s ease; }
#rf-startup.s1 .rfs-panel { opacity: 1; transform: none; transition-delay: .5s; }
#rf-startup.ready .rfs-panel { opacity: 0; transform: translateY(-.6cqw); transition-delay: 0s; }
#rf-startup .rfs-status { height: 1.8cqw; font-size: 1.28cqw; font-weight: 600; color: #4cc3ff;
  letter-spacing: .01em; text-shadow: 0 0 1cqw rgba(32, 150, 255, .45); }
#rf-startup .rfs-status span { display: inline-block; transition: opacity .3s ease, transform .3s ease; }
#rf-startup .rfs-status span.out { opacity: 0; transform: translateY(-.4cqw); }
#rf-startup .rfs-bar { display: flex; align-items: center; gap: 1cqw; margin-top: .7cqw; }
#rf-startup .rfs-track { flex: 1; height: .62cqw; border-radius: 1cqw; background: rgba(20, 45, 80, .75);
  border: 1px solid rgba(80, 150, 230, .28); overflow: hidden; }
#rf-startup .rfs-track i { display: block; height: 100%; width: 0; border-radius: inherit;
  background: linear-gradient(90deg, #1565ff, #20bfff 70%, #8fe4ff);
  box-shadow: 0 0 1cqw rgba(32, 191, 255, .7); }
#rf-startup .rfs-bar b { width: 3.6cqw; font-size: 1.1cqw; font-weight: 600; color: #F1F5F9;
  font-variant-numeric: tabular-nums; }
#rf-startup .rfs-steps { display: grid; grid-template-columns: repeat(4, 1fr); gap: .6cqw;
  margin-top: 1.3cqw; margin-right: 4.6cqw; }
#rf-startup .stp { display: flex; flex-direction: column; align-items: center; gap: .35cqw;
  color: #6b86a8; transition: color .5s ease; }
#rf-startup .stp .ic { width: 1.75cqw; height: 1.75cqw; }
#rf-startup .stp span { font-size: .78cqw; white-space: nowrap; }
#rf-startup .stp .mk { position: relative; width: 1.05cqw; height: 1.05cqw; margin-top: .15cqw; }
#rf-startup .stp .mk em { position: absolute; inset: 0; border-radius: 50%;
  border: .1cqw solid #3d5878; transition: all .4s ease; }
#rf-startup .stp .ok { position: absolute; inset: -.1cqw; width: 1.25cqw; height: 1.25cqw;
  color: #fff; opacity: 0; transform: scale(.4); transition: all .4s cubic-bezier(.2, .9, .3, 1.4); }
#rf-startup .stp.active { color: #4cc3ff; }
#rf-startup .stp.active .mk em { border-color: #20bfff; background: radial-gradient(circle, #20bfff 0 38%, transparent 42%);
  box-shadow: 0 0 .8cqw rgba(32, 191, 255, .8); animation: rfsPulse 1.2s ease-in-out infinite; }
#rf-startup .stp.done { color: #cfe6ff; }
#rf-startup .stp.done .mk em { border-color: #1597ff; background: #1597ff; box-shadow: 0 0 .7cqw rgba(21, 151, 255, .7); }
#rf-startup .stp.done .ok { opacity: 1; transform: scale(1); }
@keyframes rfsPulse { 50% { box-shadow: 0 0 1.3cqw rgba(32, 191, 255, 1); } }
/* ready */
#rf-startup .rfs-ready { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -44%);
  z-index: 1; padding: 46px 90px; text-align: center; opacity: 0; pointer-events: none;
  background: radial-gradient(ellipse at center, rgba(3, 9, 22, .82) 0, rgba(3, 9, 22, .55) 45%,
                              transparent 72%);
  transition: opacity .7s ease, transform .7s ease; }
#rf-startup.ready .rfs-ready { opacity: 1; transform: translate(-50%, -50%); }
#rf-startup .rfs-check { width: 96px; height: 96px; margin: 0 auto 14px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center; color: #4cc3ff;
  border: 3px solid #20bfff; background: radial-gradient(circle, rgba(21, 101, 255, .25), rgba(3, 9, 22, .2) 70%);
  box-shadow: 0 0 28px rgba(32, 191, 255, .65), inset 0 0 18px rgba(32, 191, 255, .35); }
#rf-startup .rfs-check svg { width: 52px; height: 52px; stroke-width: 2.4;
  stroke-dasharray: 30; stroke-dashoffset: 30; }
#rf-startup.ready .rfs-check svg { animation: rfsTick .6s .25s ease-out forwards; }
@keyframes rfsTick { to { stroke-dashoffset: 0; } }
#rf-startup .rfs-ready-t { font-size: 30px; font-weight: 700; color: #F8FAFC; }
#rf-startup .rfs-ready-s { margin-top: 6px; font-size: 15px; color: #CBD5E1; }
#rf-startup .rfs-skip { position: absolute; z-index: 1; right: 22px; bottom: 18px; background: rgba(7, 21, 37, .55);
  color: #94A3B8; border: 1px solid rgba(80, 130, 190, .35); border-radius: 8px; padding: 5px 14px;
  font: 600 12px 'Segoe UI', system-ui, sans-serif; cursor: pointer; }
#rf-startup .rfs-skip:hover { color: #E2E8F0; border-color: #1597FF; }
@media (prefers-reduced-motion: reduce) {
  #rf-startup .st, #rf-startup .tr, #rf-startup .tw, #rf-startup.s3 .cn::after,
  #rf-startup .stp.active .mk em { animation: none !important; }
  #rf-startup .rfs-stage { animation: none; transform: translate(-50%, -50%); }
}
""".replace("ASPECT", f"{_ASPECT:.4f}")

JS = """
(function () {
  var KEY = 'rf_startup_done', doc = document;
  try { if (sessionStorage.getItem(KEY)) return; } catch (e) {}
  if (doc.getElementById('rf-startup')) return;
  var cfg = __CFG__;
  var reduced = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  var speed = reduced ? 0.5 : 1;
  var st = doc.createElement('style'); st.id = 'rf-startup-css'; st.textContent = cfg.css;
  doc.head.appendChild(st);
  var root = doc.createElement('div'); root.id = 'rf-startup'; root.innerHTML = cfg.html;
  root.setAttribute('role', 'status'); root.setAttribute('aria-label', 'Initialising RF Optimization');
  doc.body.appendChild(root);
  root.querySelector('.rfs-blur').style.backgroundImage = 'url(' + cfg.bg + ')';
  try { sessionStorage.setItem(KEY, '1'); } catch (e) {}

  var q = function (s) { return root.querySelector(s); };
  var fill = q('.rfs-track i'), pct = q('.rfs-bar b'), label = q('.rfs-status span');
  var steps = root.querySelectorAll('.stp'), tl = cfg.timeline, shown = null, step = -2;
  var t0 = null, timers = [], raf = 0, ended = false;

  function setStatus(text) {
    if (text === shown) return;
    shown = text;
    label.classList.add('out');
    timers.push(setTimeout(function () { label.textContent = text; label.classList.remove('out'); }, 260));
  }
  function setStep(k) {
    if (k === step) return;
    step = k;
    for (var i = 0; i < steps.length; i++) {
      steps[i].classList.toggle('done', i < k);
      steps[i].classList.toggle('active', i === k);
    }
  }
  function at(sec, fn) { timers.push(setTimeout(fn, sec * 1000 * speed)); }
  function frame(now) {
    if (t0 === null) t0 = now;
    var t = (now - t0) / 1000 / speed, i = 0;
    while (i < tl.length - 1 && t >= tl[i + 1][0]) i++;
    var a = tl[i], b = tl[Math.min(i + 1, tl.length - 1)];
    var f = b[0] > a[0] ? Math.min(1, Math.max(0, (t - a[0]) / (b[0] - a[0]))) : 1;
    var p = a[1] + (b[1] - a[1]) * (f * f * (3 - 2 * f));
    fill.style.width = p.toFixed(2) + '%';
    pct.textContent = Math.round(p) + '%';
    setStatus(a[2]); setStep(a[3]);
    if (!ended) raf = requestAnimationFrame(frame);
  }
  function finish() {
    if (ended) return;
    ended = true;
    timers.forEach(clearTimeout); cancelAnimationFrame(raf);
    root.classList.add('leave');
    setTimeout(function () { root.remove(); st.remove(); }, 950);
    doc.removeEventListener('keydown', onKey, true);
  }
  function onKey(e) { if (e.key === 'Escape') finish(); }
  doc.addEventListener('keydown', onKey, true);
  q('.rfs-skip').addEventListener('click', finish);

  function go() {
    requestAnimationFrame(function () { root.classList.add('s1'); });
    at(1.5, function () { root.classList.add('s2'); });
    at(3.4, function () { root.classList.add('s3'); });
    at(cfg.ready, function () { root.classList.add('ready'); });
    at(cfg.leave, finish);
    raf = requestAnimationFrame(frame);
  }
  var img = q('.rfs-bg');
  if (img.complete) go();
  else {
    var started = false, start = function () { if (!started) { started = true; go(); } };
    img.addEventListener('load', start); img.addEventListener('error', start);
    setTimeout(start, 1500);               // never wait long on the picture
  }
})();
"""


def show() -> None:
    """Add the startup screen, once per Streamlit session; in the browser it
    plays once per tab (sessionStorage), and the app loads underneath it."""
    if st.session_state.get("_rf_startup"):
        return
    st.session_state["_rf_startup"] = True
    cfg = {"css": CSS, "html": markup(), "bg": BG_URL, "timeline": TIMELINE,
           "ready": READY_AT, "leave": LEAVE_AT}
    js = JS.replace("__CFG__", json.dumps(cfg).replace("</", "<\\/"))
    # st.html sanitises its HTML, and a script whose text holds markup (this
    # one carries the layer's) is dropped whole: it rides base64-encoded, and a
    # loader free of any markup runs it
    b64 = base64.b64encode(js.encode("utf-8")).decode("ascii")
    st.html("<script>(function(){var s=atob('" + b64 + "'),b=new Uint8Array(s.length);"
            "for(var i=0;i!==s.length;i++)b[i]=s.charCodeAt(i);"
            "new Function(new TextDecoder().decode(b))();})();</script>",
            unsafe_allow_javascript=True)


__all__ = ["BG_URL", "CITIES", "LINKS", "STEPS", "TIMELINE", "markup", "show"]
