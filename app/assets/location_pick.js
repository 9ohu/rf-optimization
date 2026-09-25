// Select Location: a map tool that drops one marker, shows its coordinates in
// a window tied to it by a leader line, and keeps them live while the marker
// is dragged. Wrapped by LocationPick (Python), which defines MAP, ICON_PIN
// and ICON_COPY. Everything happens inside the map: nothing is sent to
// Python, nothing reruns, nothing reloads.
var RF = window.RF = window.RF || {};
var root = MAP.getContainer();
var HOST = window;
try { if (window.parent && window.parent.document) HOST = window.parent; } catch (e) { HOST = window; }
var DEC = 6;

// ---- the tool button: the marker tool of the top-right toolbar, under the
// draw-circle and delete tools ----------------------------------------------
var Tool = L.Control.extend({
  options: {position: 'topright'},
  onAdd: function () {
    var box = L.DomUtil.create('div', 'leaflet-bar leaflet-control');
    var a = L.DomUtil.create('a', 'rf-pick-btn', box);
    a.href = '#';
    a.title = 'Place a marker — live latitude / longitude, copy';
    a.setAttribute('role', 'button');
    a.innerHTML = ICON_PIN;
    L.DomEvent.on(a, 'click', L.DomEvent.stop).on(a, 'click', function () {
      setMode(!picking);
    });
    L.DomEvent.disableClickPropagation(box);
    this._a = a;
    return box;
  }
});
var tool = new Tool();
tool.addTo(MAP);

// ---- the window and its leader line --------------------------------------------
var lead = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
lead.setAttribute('class', 'rf-pick-lead');
lead.innerHTML = '<line x1="0" y1="0" x2="0" y2="0"/><circle cx="0" cy="0" r="2.6"/>';
lead.style.display = 'none';
root.appendChild(lead);
var leadLine = lead.querySelector('line'), leadDot = lead.querySelector('circle');

var win = L.DomUtil.create('div', 'rf-pick-win', root);
win.hidden = true;
win.innerHTML = '<div class="rf-pick-h"><span class="rf-pick-ico">' + ICON_PIN + '</span>'
  + '<span class="rf-pick-t">Selected Location</span>'
  + '<span class="rf-pick-state sel"><i></i><b>SELECTED</b></span>'
  + '<button class="rf-pick-x" title="Remove the selected location">&#10005;</button></div>'
  + '<div class="rf-pick-b"><div class="rf-pick-vals">'
  + '<div class="rf-pick-k">Latitude</div><div class="rf-pick-v rf-pick-lat"></div>'
  + '<div class="rf-pick-k">Longitude</div><div class="rf-pick-v rf-pick-lon"></div></div>'
  + '<button class="rf-pick-copy" title="Copy LAT, LONG">' + ICON_COPY + '<span>Copy</span></button></div>';
L.DomEvent.disableClickPropagation(win);
L.DomEvent.disableScrollPropagation(win);
var elLat = win.querySelector('.rf-pick-lat'), elLon = win.querySelector('.rf-pick-lon');
var elState = win.querySelector('.rf-pick-state'), elStateTxt = elState.querySelector('b');
var btnCopy = win.querySelector('.rf-pick-copy'), lblCopy = btnCopy.querySelector('span');

var PIN_HTML = '<div class="rf-pick-pin"><span class="rf-pick-ring"></span>'
  + '<svg viewBox="0 0 34 44" width="34" height="44">'
  + '<path d="M17 42C15.6 40.2 4 27.4 4 18a13 13 0 0 1 26 0c0 9.4-11.6 22.2-13 24z" '
  + 'fill="#FB923C" stroke="#FFF7ED" stroke-width="1.6"/>'
  + '<circle cx="17" cy="18" r="5.4" fill="#0B1F33"/>'
  + '<circle cx="17" cy="18" r="2.7" fill="#20BFFF"/></svg></div>';

var picking = false, marker = null, dragging = false, frame = 0, copyTimer = null;

function setMode(on) {
  picking = !!on;
  HOST.__rfPickMode = picking;
  L.DomUtil[picking ? 'addClass' : 'removeClass'](tool._a, 'rf-tool-on');
  L.DomUtil[picking ? 'addClass' : 'removeClass'](root, 'rf-picking');
  if (picking && RF.cancelRuler) RF.cancelRuler();
}

function setLive(on) {
  dragging = on;
  elState.className = 'rf-pick-state ' + (on ? 'live' : 'sel');
  elStateTxt.textContent = on ? 'LIVE' : 'SELECTED';
  var el = marker && marker._icon;
  if (el) L.DomUtil[on ? 'addClass' : 'removeClass'](el, 'live');
}

// ---- dragging the pin -----------------------------------------------------------
// Its own pointer-event drag rather than Leaflet's marker drag: it ends on
// release for mouse, touch and pen alike, and the map does not pan underneath.
var drag = null;
function bindDrag(icon) {
  L.DomEvent.on(icon, 'mousedown touchstart dblclick', L.DomEvent.stopPropagation);
  icon.addEventListener('pointerdown', function (e) {
    if (!marker || (e.pointerType === 'mouse' && e.button !== 0)) return;
    e.preventDefault();
    e.stopPropagation();
    var start = MAP.mouseEventToContainerPoint(e);
    drag = {id: e.pointerId, icon: icon, moved: false, start: start,
            grab: MAP.latLngToContainerPoint(marker.getLatLng()).subtract(start)};
    try { icon.setPointerCapture(e.pointerId); } catch (err) {}
    MAP.dragging.disable();
    document.addEventListener('pointermove', onDragMove, true);
    document.addEventListener('pointerup', onDragEnd, true);
    document.addEventListener('pointercancel', onDragEnd, true);
  });
}
function onDragMove(e) {
  if (!drag || e.pointerId !== drag.id || !marker) return;
  var pt = MAP.mouseEventToContainerPoint(e);
  if (!drag.moved) {
    if (pt.distanceTo(drag.start) < 3) return;       // still a click, not a drag
    drag.moved = true;
    setLive(true);
  }
  marker.setLatLng(MAP.containerPointToLatLng(pt.add(drag.grab)));
  render();
}
function onDragEnd(e) {
  if (!drag || (e && e.pointerId !== undefined && e.pointerId !== drag.id)) return;
  document.removeEventListener('pointermove', onDragMove, true);
  document.removeEventListener('pointerup', onDragEnd, true);
  document.removeEventListener('pointercancel', onDragEnd, true);
  try { drag.icon.releasePointerCapture(drag.id); } catch (err) {}
  var moved = drag.moved;
  drag = null;
  MAP.dragging.enable();
  if (moved) { setLive(false); save(); render(); }
}

function place(ll) {
  if (!marker) {
    marker = L.marker(ll, {keyboard: false, zIndexOffset: 1000,
      icon: L.divIcon({className: 'rf-pick-mk', iconSize: [34, 44], iconAnchor: [17, 42],
                       html: PIN_HTML})});
    marker.addTo(MAP);
    bindDrag(marker._icon);
  } else {
    marker.setLatLng(ll);
  }
  win.hidden = false;
  lead.style.display = '';
  setLive(false);
  save();
  render();
}

function remove() {
  if (drag) onDragEnd();
  if (marker) { MAP.removeLayer(marker); marker = null; }
  win.hidden = true;
  lead.style.display = 'none';
  dragging = false;
  HOST.__rfPick = null;
}

function save() {
  if (!marker) return;
  var ll = marker.getLatLng();
  HOST.__rfPick = [ll.lat, ll.lng];
}

function clamp(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }

// above the marker when it fits; otherwise right, left, then below — always
// inside the map, and clear of the Sector Details drawer when it is open
function position(pt) {
  var W = root.clientWidth, H = root.clientHeight;
  var w = win.offsetWidth, h = win.offsetHeight, pad = 8, gap = 26;
  var right = W - pad - (root.classList.contains('rf-drawer-open') ? 384 : 0);
  if (right - pad < w) right = W - pad;
  var head = {x: pt.x, y: pt.y - 40}, mid = {x: pt.x, y: pt.y - 22};
  var fitsV = function (y) { return y >= pad && y + h <= H - pad; };
  var fitsH = function (x) { return x >= pad && x + w <= right; };
  var x, y, side = null;
  x = clamp(pt.x - w / 2, pad, right - w); y = head.y - gap - h;
  if (fitsV(y)) side = 'top';
  if (!side) { x = pt.x + 20 + gap; y = clamp(mid.y - h / 2, pad, H - pad - h); if (fitsH(x)) side = 'right'; }
  if (!side) { x = pt.x - 20 - gap - w; y = clamp(mid.y - h / 2, pad, H - pad - h); if (fitsH(x)) side = 'left'; }
  if (!side) { x = clamp(pt.x - w / 2, pad, right - w); y = pt.y + gap; if (fitsV(y)) side = 'bottom'; }
  if (!side) { side = 'top'; x = clamp(pt.x - w / 2, pad, right - w); y = clamp(head.y - gap - h, pad, H - pad - h); }
  win.style.transform = 'translate(' + Math.round(x) + 'px,' + Math.round(y) + 'px)';

  var ax, ay, tx, ty;
  if (side === 'top') { ax = clamp(pt.x, x + 16, x + w - 16); ay = y + h; tx = head.x; ty = head.y; }
  else if (side === 'bottom') { ax = clamp(pt.x, x + 16, x + w - 16); ay = y; tx = pt.x; ty = pt.y + 2; }
  else if (side === 'right') { ax = x; ay = clamp(mid.y, y + 16, y + h - 16); tx = pt.x + 12; ty = mid.y; }
  else { ax = x + w; ay = clamp(mid.y, y + 16, y + h - 16); tx = pt.x - 12; ty = mid.y; }
  leadLine.setAttribute('x1', ax); leadLine.setAttribute('y1', ay);
  leadLine.setAttribute('x2', tx); leadLine.setAttribute('y2', ty);
  leadDot.setAttribute('cx', ax); leadDot.setAttribute('cy', ay);
}

function render() {
  frame = 0;
  if (!marker || win.hidden) return;
  var ll = marker.getLatLng();
  elLat.textContent = ll.lat.toFixed(DEC);
  elLon.textContent = ll.lng.toFixed(DEC);
  var pt = MAP.latLngToContainerPoint(ll);
  var inView = pt.x >= 0 && pt.y >= 0 && pt.x <= root.clientWidth && pt.y <= root.clientHeight;
  win.style.visibility = inView ? '' : 'hidden';
  lead.style.visibility = inView ? '' : 'hidden';
  if (inView) position(pt);
}
function schedule() { if (!frame) frame = requestAnimationFrame(render); }

// ---- selecting: the tool's click is its own ---------------------------------
MAP.on('click', function (e) {
  if (!picking || e.rfSynthetic) return;
  e.rfSynthetic = true;          // the ruler and the coverage read-out leave it alone
  place(e.latlng);
});

win.querySelector('.rf-pick-x').onclick = function () { remove(); setMode(false); };

function copied(ok) {
  clearTimeout(copyTimer);
  btnCopy.classList.toggle('ok', ok);
  lblCopy.textContent = ok ? '✓ Copied' : 'Ctrl+C';
  copyTimer = setTimeout(function () {
    btnCopy.classList.remove('ok');
    lblCopy.textContent = 'Copy';
  }, 1600);
}
function selectValues() {
  try {
    var r = document.createRange();
    r.setStartBefore(elLat);
    r.setEndAfter(elLon);
    var s = window.getSelection();
    s.removeAllRanges();
    s.addRange(r);
  } catch (e) {}
}
btnCopy.onclick = function () {
  if (!marker) return;
  var ll = marker.getLatLng();
  var text = ll.lat.toFixed(DEC) + ', ' + ll.lng.toFixed(DEC);
  function fallback() {
    var ta = document.createElement('textarea'), ok = false;
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '-1000px';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    if (!ok) selectValues();
    copied(ok);
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(function () { copied(true); }, fallback);
  } else {
    fallback();
  }
};

// another map tool takes over: the selection goes
RF.cancelPick = function () { setMode(false); remove(); };
MAP.on('draw:drawstart draw:deletestart draw:editstart', function () { RF.cancelPick(); });
L.DomEvent.on(document, 'keydown', function (ev) {
  if (ev.key === 'Escape' && picking) setMode(false);
});

MAP.on('move', schedule);
MAP.on('resize', schedule);
MAP.on('zoomstart', function () { root.classList.add('rf-pick-zooming'); });
MAP.on('zoomend', function () { root.classList.remove('rf-pick-zooming'); schedule(); });

// the map is rebuilt on a rerun (full screen, a KPI change, a sector click):
// the selection and the tool come back as they were
MAP.whenReady(function () {
  if (HOST.__rfPick) place(L.latLng(HOST.__rfPick[0], HOST.__rfPick[1]));
  if (HOST.__rfPickMode) setMode(true);
});
