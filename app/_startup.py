"""The startup screen: the one loading the system shows, while it really prepares.

One continuous animation over one background — the Huawei RF Optimization
night view of Iraq (`static/startup/rf_startup_bg.*`, served by the app) —
never a sequence of pictures. The scene itself plays on its own: the view
lights up, the tower beacons blink, the Iraq map is revealed from Baghdad
outward, the city nodes light up, data runs along the network links. The
loading panel under the title follows the real preparation (`_warmup`): its
status is the task running, its bar the share of tasks done, its four steps
(Data Resources · Network Data · Map Services · Analysis Engine) turn active
then done as their tasks finish. It says Ready only when the preparation is
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
MIN_SHOW = 4.2              # s: the map reveal plays through before Ready
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


def markup() -> str:
    """The layer's HTML: background, veil, lights, streams, panel, Ready."""
    pts = {**CITIES, **NODES}
    links = "".join(
        f'<path class="lk" d="{_link_path(pts[a], pts[b], bend)}"/>'
        f'<path class="st" style="animation-delay:{0.35 * k:.2f}s" '
        f'd="{_link_path(pts[a], pts[b], bend)}"/>'
        for k, (a, b, bend) in enumerate(LINKS))
    sx, sy = _VW / 2392, _VH / 898
    trails = (f'<path class="tr" transform="scale({sx:.4f} {sy:.4f})" '
              'd="M0,760 C420,640 760,905 1180,780 S1720,640 2392,720"/>'
              f'<path class="tr t2" transform="scale({sx:.4f} {sy:.4f})" '
              'd="M0,820 C520,700 900,880 1320,820 S1900,760 2392,800"/>')
    lights = "".join(f'<i class="tw" style="left:{x}%;top:{y}%;animation-delay:{d}s"></i>'
                     for x, y, d in TOWER_LIGHTS)
    cities = "".join(f'<i class="cn" style="left:{x}%;top:{y}%;--d:{k}"></i>'
                     for k, (x, y) in enumerate(CITIES.values()))
    ok = _svg('<path d="m5 12.5 4.5 4.5L19 7.5"/>', "ok")
    steps = "".join(f'<div class="stp" data-i="{k}">{_svg(_ICON[ic], "ic")}<span>{label}</span>'
                    f'<b class="mk"><em></em>{ok}</b></div>' for k, (label, ic) in enumerate(STEPS))
    return f"""
<div class="rfs-stage">
  <img class="rfs-bg" src="{BG_URL}" alt="" draggable="false" decoding="sync">
  <div class="rfs-veil"></div>
  <svg class="rfs-net" viewBox="0 0 {_VW} {_VH}" preserveAspectRatio="none">
    <g class="rfs-trails">{trails}</g><g class="rfs-links">{links}</g>
  </svg>
  {lights}{cities}
  <div class="rfs-panel">
    <div class="rfs-status"><span>Initialising RF Optimization</span></div>
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
.st-key-rf_startup_bus { display: none !important; }
#rf-startup { position: fixed; inset: 0; z-index: 2147483600; overflow: hidden; cursor: default;
  background: radial-gradient(ellipse at 60% 45%, #0a1a33 0, #050e1f 55%, #030916 100%);
  font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; color: #E2E8F0;
  -webkit-font-smoothing: antialiased; text-rendering: geometricPrecision;
  opacity: 1; transition: opacity .9s ease; }
#rf-startup.leave { opacity: 0; pointer-events: none; }
/* the picture at its own size, centred without any transform (nothing is
   resampled twice, and text over it stays on whole pixels) */
#rf-startup .rfs-stage { position: absolute; inset: 0; margin: auto;
  width: max(100vw, calc(64vh * ASPECT)); height: calc(max(100vw, calc(64vh * ASPECT)) / ASPECT);
  container-type: inline-size;
  -webkit-mask-image: linear-gradient(to bottom, transparent 0, #000 6%, #000 94%, transparent 100%);
          mask-image: linear-gradient(to bottom, transparent 0, #000 6%, #000 94%, transparent 100%); }
#rf-startup .rfs-bg { position: absolute; inset: 0; width: 100%; height: 100%; display: block;
  user-select: none; image-rendering: auto; opacity: .3; transition: opacity 1.4s ease; }
#rf-startup.s1 .rfs-bg { opacity: 1; }
#rf-startup .rfs-dim { position: absolute; inset: 0; background: rgba(3, 9, 22, 0);
  transition: background .8s ease; pointer-events: none; }
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
@keyframes rfsBlink { 0%, 100% { opacity: .25; } 45% { opacity: 1; } }
/* city nodes: lit one after the other as the map is revealed */
#rf-startup .cn { position: absolute; width: 1.1cqw; height: 1.1cqw; margin: -.55cqw 0 0 -.55cqw;
  border-radius: 50%; opacity: 0; mix-blend-mode: screen;
  background: radial-gradient(circle, #fff7e0 0, #ffb347 30%, rgba(255, 150, 40, 0) 70%);
  box-shadow: 0 0 2.2cqw .7cqw rgba(255, 160, 60, .45);
  transition: opacity .7s ease; transition-delay: calc(.6s + var(--d) * .28s); }
#rf-startup .cn::after { content: ""; position: absolute; inset: -.9cqw; border-radius: 50%;
  border: .12cqw solid rgba(32, 191, 255, .75); opacity: 0; }
#rf-startup.s2 .cn { opacity: 1; }
#rf-startup.s3 .cn::after { animation: rfsRing 2.6s ease-out infinite;
  animation-delay: calc(var(--d) * .45s); }
@keyframes rfsRing { 0% { opacity: .8; transform: scale(.35); } 100% { opacity: 0; transform: scale(1.6); } }
/* network links and the data moving along them: crisp strokes, a light glow */
#rf-startup .rfs-net { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible;
  mix-blend-mode: screen; }
#rf-startup .rfs-links { opacity: 0; transition: opacity 1.2s ease .9s; }
#rf-startup.s2 .rfs-links { opacity: 1; }
#rf-startup .lk { fill: none; stroke: rgba(56, 170, 255, .25); stroke-width: 1.2;
  vector-effect: non-scaling-stroke; }
#rf-startup .st { fill: none; stroke: #9be2ff; stroke-width: 2.4; stroke-linecap: round;
  vector-effect: non-scaling-stroke; stroke-dasharray: 14 240;
  filter: drop-shadow(0 0 3px #20bfff); animation: rfsFlow 2.8s linear infinite; }
@keyframes rfsFlow { from { stroke-dashoffset: 254; } to { stroke-dashoffset: 0; } }
#rf-startup .tr { fill: none; stroke: rgba(150, 215, 255, .6); stroke-width: 2; stroke-linecap: round;
  vector-effect: non-scaling-stroke; stroke-dasharray: 120 2600;
  filter: drop-shadow(0 0 4px #1597ff); animation: rfsTrail 6s linear infinite; }
#rf-startup .tr.t2 { animation-duration: 8s; animation-delay: -3s; opacity: .7; }
@keyframes rfsTrail { from { stroke-dashoffset: 2720; } to { stroke-dashoffset: 0; } }
/* the loading panel under the title */
#rf-startup .rfs-panel { position: absolute; left: 10.4%; top: 66.5%; width: 33%;
  opacity: 0; transition: opacity .8s ease; }
#rf-startup.s1 .rfs-panel { opacity: 1; transition-delay: .5s; }
#rf-startup.ready .rfs-panel { opacity: 0; transition-delay: 0s; }
#rf-startup .rfs-status { height: 1.8cqw; font-size: max(13px, 1.2cqw); font-weight: 600;
  color: #56c8ff; letter-spacing: .005em; white-space: nowrap; }
#rf-startup .rfs-status span { display: inline-block; transition: opacity .25s ease; }
#rf-startup .rfs-status span.out { opacity: 0; }
#rf-startup .rfs-bar { display: flex; align-items: center; gap: 1cqw; margin-top: .7cqw; }
#rf-startup .rfs-track { flex: 1; height: max(6px, .55cqw); border-radius: 99px;
  background: rgba(20, 45, 80, .8); border: 1px solid rgba(80, 150, 230, .3); overflow: hidden; }
#rf-startup .rfs-track i { display: block; height: 100%; width: 0; border-radius: inherit;
  background: linear-gradient(90deg, #1565ff, #20bfff 70%, #8fe4ff);
  box-shadow: 0 0 10px rgba(32, 191, 255, .6); }
#rf-startup .rfs-bar b { min-width: 3.6cqw; font-size: max(13px, 1.05cqw); font-weight: 600;
  color: #F1F5F9; font-variant-numeric: tabular-nums; }
#rf-startup .rfs-steps { display: grid; grid-template-columns: repeat(4, 1fr); gap: .6cqw;
  margin-top: 1.3cqw; margin-right: 4.6cqw; }
#rf-startup .stp { display: flex; flex-direction: column; align-items: center; gap: .35cqw;
  color: #7390b3; transition: color .5s ease; }
#rf-startup .stp .ic { width: max(18px, 1.7cqw); height: max(18px, 1.7cqw); }
#rf-startup .stp span { font-size: max(11px, .76cqw); white-space: nowrap; }
#rf-startup .stp .mk { position: relative; width: max(12px, 1.05cqw); height: max(12px, 1.05cqw);
  margin-top: .15cqw; }
#rf-startup .stp .mk em { position: absolute; inset: 0; border-radius: 50%;
  border: 1.5px solid #3d5878; transition: all .4s ease; }
#rf-startup .stp .ok { position: absolute; inset: -1px; width: calc(100% + 2px); height: calc(100% + 2px);
  color: #fff; opacity: 0; transition: opacity .35s ease; }
#rf-startup .stp.active { color: #56c8ff; }
#rf-startup .stp.active .mk em { border-color: #20bfff;
  background: radial-gradient(circle, #20bfff 0 38%, transparent 42%);
  box-shadow: 0 0 8px rgba(32, 191, 255, .8); animation: rfsPulse 1.2s ease-in-out infinite; }
#rf-startup .stp.done { color: #d6e9ff; }
#rf-startup .stp.done .mk em { border-color: #1597ff; background: #1597ff;
  box-shadow: 0 0 7px rgba(21, 151, 255, .7); }
#rf-startup .stp.done .ok { opacity: 1; }
@keyframes rfsPulse { 50% { box-shadow: 0 0 14px rgba(32, 191, 255, 1); } }
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
  #rf-startup .stp.active .mk em { animation: none !important; }
}
""".replace("ASPECT", f"{_ASPECT:.4f}")

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
  var fill = q('.rfs-track i'), pct = q('.rfs-bar b'), label = q('.rfs-status span');
  var steps = root.querySelectorAll('.stp');
  var total = 0, done = 0, shownPct = 0, serverReady = false, ended = false, readyAt = 0;
  var t0 = performance.now(), lastWord = t0, raf = 0, timers = [];

  function setStatus(text) {
    if (!text || label.textContent === text) return;
    label.classList.add('out');
    timers.push(setTimeout(function () { label.textContent = text; label.classList.remove('out'); }, 220));
  }
  function setStep(k) {
    for (var i = 0; i !== steps.length; i++) {
      steps[i].classList.toggle('done', i < k);
      steps[i].classList.toggle('active', i === k);
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

  // the bar follows the tasks done; it only eases between real values
  function frame(now) {
    var target = total ? 100 * done / total : 0;
    shownPct += (target - shownPct) * 0.12;
    if (Math.abs(target - shownPct) < 0.05) shownPct = target;
    fill.style.width = shownPct.toFixed(2) + '%';
    pct.textContent = Math.floor(shownPct + 1e-6) + '%';
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
      if (m.type === 'begin') { total = m.total || 0; }
      else if (m.type === 'task') { setStatus(m.label); setStep(m.step); }
      else if (m.type === 'done') { done = Math.min(total, done + 1); }
      else if (m.type === 'ready') {
        serverReady = true; done = total;
        setStatus('Almost Ready');
      }
    }
  };
  requestAnimationFrame(function () { root.classList.add('s1'); });
  timers.push(setTimeout(function () { root.classList.add('s2'); }, 1500));
  timers.push(setTimeout(function () { root.classList.add('s3'); }, 3400));
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
           "stall": STALL}
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
