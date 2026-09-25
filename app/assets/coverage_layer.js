// LTE coverage grid on the Sites map. Wrapped by CoverageLayer (Python), which
// defines MAP (the Leaflet map) and CFG (levels, blocks, classes, stats).
// Blocks are PNGs whose pixels carry RSRP + MR count; they are coloured here
// with the configured classes, and a click reads the cell straight back.
var C = CFG, B = C.block, RF = window.RF = window.RF || {};
var D2R = Math.PI / 180;
RF.coverage = C;

function hexRgb(h) {
  h = String(h).replace('#', '');
  return [parseInt(h.substr(0, 2), 16), parseInt(h.substr(2, 2), 16),
          parseInt(h.substr(4, 2), 16)];
}
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
  });
}
function num(v, d) {
  return Number(v).toLocaleString('en-US', {maximumFractionDigits: d == null ? 0 : d});
}
function bandOf(v) {
  for (var j = 0; j < C.bands.length - 1; j++) {
    if (C.bands[j].lo !== null && v >= C.bands[j].lo) return j;
  }
  return C.bands.length - 1;
}

// one colour per 12-bit RSRP code
var LUT = new Uint8ClampedArray(4096 * 3);
for (var code = 0; code < 4096; code++) {
  var rgb = hexRgb(C.bands[bandOf(code / C.rsrp_scale - C.rsrp_offset)].colour);
  LUT[code * 3] = rgb[0]; LUT[code * 3 + 1] = rgb[1]; LUT[code * 3 + 2] = rgb[2];
}

function decode(d, i) {
  if (d[i + 3] < 128) return null;
  var q = (d[i] << 4) | (d[i + 1] >> 4), cc = ((d[i + 1] & 15) << 8) | d[i + 2];
  return {rsrp: q / C.rsrp_scale - C.rsrp_offset,
          mr: cc < C.count_exact ? cc : C.count_exact * Math.pow(C.count_ratio, cc - C.count_exact),
          approx: cc >= C.count_exact};
}
function mercY(lat) { var s = Math.sin(lat * D2R); return 0.5 * Math.log((1 + s) / (1 - s)); }
function latOf(y) { return (2 * Math.atan(Math.exp(y)) - Math.PI / 2) / D2R; }

if (!MAP.getPane('rfCoverage')) {
  var pane = MAP.createPane('rfCoverage');
  pane.style.zIndex = 250;
  pane.style.pointerEvents = 'none';
}

function blockBounds(lv, by, bx) {
  var la = C.lat0 + by * B * lv.cell_lat, lo = C.lon0 + bx * B * lv.cell_lon;
  return [la, lo, la + B * lv.cell_lat, lo + B * lv.cell_lon];
}
function levelFor(z, lat) {
  var mpp = 156543.03392 * Math.cos(lat * D2R) / Math.pow(2, z);
  for (var k = 0; k < C.levels.length; k++) {
    if (C.levels[k].cell_m / mpp >= C.min_px) return k;
  }
  return C.levels.length - 1;
}

// the image rows are even in latitude; Leaflet stretches an overlay evenly in
// Web-Mercator. Each output row takes the source row at its own latitude, so a
// cell sits exactly where it was measured, at any zoom.
function paint(ent, img) {
  var lv = C.levels[ent.k], bb = blockBounds(lv, ent.by, ent.bx);
  var cv = document.createElement('canvas');
  cv.width = B; cv.height = B;
  var cx = cv.getContext('2d', {willReadFrequently: true});
  cx.drawImage(img, 0, 0);
  var src = cx.getImageData(0, 0, B, B).data;
  var out = cx.createImageData(B, B), o = out.data;
  var yTop = mercY(bb[2]), yBot = mercY(bb[0]);
  for (var r = 0; r < B; r++) {
    var lat = latOf(yTop - (r + 0.5) * (yTop - yBot) / B);
    var sr = B - 1 - Math.floor((lat - bb[0]) / lv.cell_lat);
    sr = sr < 0 ? 0 : (sr > B - 1 ? B - 1 : sr);
    var so = sr * B * 4, oo = r * B * 4;
    for (var c = 0; c < B; c++, so += 4, oo += 4) {
      if (src[so + 3] < 128) continue;
      var q = ((src[so] << 4) | (src[so + 1] >> 4)) * 3;
      o[oo] = LUT[q]; o[oo + 1] = LUT[q + 1]; o[oo + 2] = LUT[q + 2]; o[oo + 3] = 255;
    }
  }
  cx.putImageData(out, 0, 0);
  ent.data = src;
  ent.overlay = L.imageOverlay(cv.toDataURL(), [[bb[0], bb[1]], [bb[2], bb[3]]],
    {pane: 'rfCoverage', opacity: C.opacity, className: 'rf-cov', interactive: false});
}

var loaded = {}, shown = {}, wanted = {}, pending = {}, level = -1;
function show(id) {
  var ent = loaded[id];
  if (!ent || !ent.overlay) return;
  ent.used = Date.now();
  if (!shown[id]) { ent.overlay.addTo(MAP); shown[id] = 1; }
}
function sweep() {
  for (var p in pending) if (wanted[p]) return;   // keep the old picture until the new one is in
  for (var s in shown) {
    if (!wanted[s]) { MAP.removeLayer(loaded[s].overlay); delete shown[s]; }
  }
  var idle = Object.keys(loaded).filter(function (i) {
    return !wanted[i] && !shown[i] && !pending[i];
  });
  if (idle.length > 36) {
    idle.sort(function (a, b) { return (loaded[a].used || 0) - (loaded[b].used || 0); });
    idle.slice(0, idle.length - 36).forEach(function (i) { delete loaded[i]; });
  }
}
function refresh() {
  var ctr = MAP.getCenter(), k = levelFor(MAP.getZoom(), ctr.lat), lv = C.levels[k];
  var b = MAP.getBounds().pad(0.15);
  level = k;
  wanted = {};
  lv.blocks.forEach(function (e) {
    var bb = blockBounds(lv, e[0], e[1]);
    if (bb[2] < b.getSouth() || bb[0] > b.getNorth()
        || bb[3] < b.getWest() || bb[1] > b.getEast()) return;
    var id = k + ':' + e[0] + ':' + e[1];
    wanted[id] = 1;
    if (loaded[id] && loaded[id].overlay) { show(id); return; }
    if (pending[id] || !e[2]) return;
    var ent = loaded[id] = {k: k, by: e[0], bx: e[1]};
    pending[id] = 1;
    var img = new Image();
    img.onload = function () {
      delete pending[id];
      try { paint(ent, img); } catch (err) { delete loaded[id]; }
      if (wanted[id]) show(id);
      sweep();
      paintLegend();
    };
    img.onerror = function () { delete pending[id]; delete loaded[id]; sweep(); };
    img.src = e[2];
  });
  sweep();
  paintLegend();
}

// ---- the legend -------------------------------------------------------------
var legend = L.control({position: 'bottomleft'});
legend.onAdd = function () {
  var d = L.DomUtil.create('div', 'sm-legend rf-cov-legend');
  L.DomEvent.disableClickPropagation(d);
  L.DomEvent.disableScrollPropagation(d);
  return d;
};
legend.addTo(MAP);
function paintLegend() {
  var el = legend.getContainer(), s = C.stats, h = '';
  h += '<div class="sm-lg-t">LTE Coverage</div><div class="sm-lg-sub">'
    + esc(C.rsrp_label) + ' · share of MRs / of grids</div>';
  C.bands.forEach(function (b, j) {
    var st = s.bands[j], gp = s.grids ? 100 * st.grids / s.grids : 0;
    h += '<div class="sm-lg-row"><span class="sm-lg-chip" style="display:inline-block;'
      + 'width:14px;height:14px;background:' + b.colour + '"></span>'
      + '<span class="sm-lg-r">' + esc(b.text) + '</span>'
      + '<span class="sm-lg-n">' + num(st.mr_pct, 1) + '% / ' + num(gp, 1) + '%</span>'
      + '<span class="sm-lg-b">' + esc(b.label) + '</span></div>';
  });
  h += '<div class="sm-lg-row"><span class="sm-lg-chip" style="display:inline-block;'
    + 'width:14px;height:14px;background:transparent;border:1px dashed #94A3B8"></span>'
    + '<span class="sm-lg-r">no samples</span><span class="sm-lg-n">not drawn</span>'
    + '<span class="sm-lg-b">No data</span></div>';
  var lv = C.levels[level < 0 ? 0 : level];
  h += '<div class="sm-lg-note">Samples: ' + num(s.mrs) + ' MRs · ' + num(s.grids)
    + ' grids<br>Coverage (≥ ' + num(s.covered_dbm) + ' dBm): ' + num(s.covered_pct, 1)
    + '% of MRs<br>Grid at this zoom: ' + num(lv.cell_m) + ' m'
    + (level > 0 ? ' · MR-weighted median' : '') + '</div>';
  el.innerHTML = h;
}

// ---- click a cell -----------------------------------------------------------
MAP.on('click', function (e) {
  if (e.rfSynthetic || RF.rulerActive || level < 0) return;
  var t = e.originalEvent && e.originalEvent.target;
  if (t && t.closest && t.closest('.leaflet-interactive, .rf-tw, .rf-dw, .rf-tb, '
      + '.sm-legend, .leaflet-control, .leaflet-popup')) return;
  var lv = C.levels[level], ll = e.latlng;
  var cy = Math.floor((ll.lat - C.lat0) / lv.cell_lat);
  var cx = Math.floor((ll.lng - C.lon0) / lv.cell_lon);
  var by = Math.floor(cy / B), bx = Math.floor(cx / B);
  var ent = loaded[level + ':' + by + ':' + bx], px = null;
  if (ent && ent.data && cy >= 0 && cx >= 0) {
    px = decode(ent.data, ((B - 1 - (cy - by * B)) * B + (cx - bx * B)) * 4);
  }
  var lat = C.lat0 + (cy + 0.5) * lv.cell_lat, lon = C.lon0 + (cx + 0.5) * lv.cell_lon;
  var size = num(lv.cell_m) + ' m';
  var h = '<div class="rf-cov-pop"><div class="rf-pop-t">LTE Coverage</div>';
  if (!px) {
    h += '<div class="rf-pop-s">No samples in this ' + size + ' cell</div>';
  } else {
    var b = C.bands[bandOf(px.rsrp)];
    h += '<div class="rf-cov-val"><b>' + num(px.rsrp, 1) + '</b> dBm'
      + '<span class="rf-cov-badge" style="--c:' + b.colour + '"><i></i>' + esc(b.label)
      + '</span></div>';
  }
  h += '<div class="rf-kvs">';
  if (px) {
    h += '<span>Samples</span><b>' + (px.approx ? '≈ ' : '') + num(px.mr) + ' MRs</b>'
      + '<span>RSRP</span><b>' + (level > 0 ? 'MR-weighted median' : 'grid value') + '</b>';
  }
  h += '<span>Cell</span><b>' + size + (level > 0 ? ' (from ' + num(C.levels[0].cell_m)
    + ' m grids)' : '') + '</b>'
    + '<span>Lat</span><b>' + lat.toFixed(5) + '</b>'
    + '<span>Lon</span><b>' + lon.toFixed(5) + '</b>'
    + '<span>Time</span><b>' + esc(C.time_note) + '</b>'
    + '<span>Source</span><b>' + esc(C.sources) + '</b></div></div>';
  L.popup({className: 'rf-pop', maxWidth: 300, minWidth: 220, autoPanPadding: [40, 40]})
    .setLatLng([lat, lon]).setContent(h).openOn(MAP);
});

var timer = null;
MAP.on('moveend zoomend', function () {
  clearTimeout(timer);
  timer = setTimeout(refresh, 60);
});
MAP.whenReady(function () { setTimeout(refresh, 80); });
