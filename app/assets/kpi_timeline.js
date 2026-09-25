// The KPI time machine inside the Sites map. Wrapped by KpiTimeline (Python),
// which defines MAP (the Leaflet map), BEAMS (the beams GeoJSON layer) and
// CFG (the packed frames). t = -1 is the whole window, 0..T-1 the export's
// own timestamps. Everything that shows the KPI listens to RF.setT.
var RF = window.RF = window.RF || {};
var K = CFG;
var B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
var A = {};
for (var a = 0; a < 64; a++) A[B64.charCodeAt(a)] = a;
var NB = K.bands.length, NONE = NB - 1, N = K.src.length, T = K.times.length;
var listeners = RF._timeFns = RF._timeFns || [];

RF.map = MAP;
RF.kpi = K;
RF.t = -1;
RF.onTime = function (fn) { listeners.push(fn); };
RF.code = function (i, t) {
  if (t === undefined) t = RF.t;
  var s = K.codes[t + 1];
  return (s && i != null && i >= 0) ? s.charCodeAt(K.src[i]) - 48 : NONE;
};
RF.band = function (i, t) { return K.bands[RF.code(i, t)]; };
RF.value = function (i, t) {
  if (!K.vals || i == null || i < 0) return null;
  if (t === undefined) t = RF.t;
  var s = K.vals[t + 1], row = K.src[i];
  var q = A[s.charCodeAt(2 * row)] * 64 + A[s.charCodeAt(2 * row + 1)];
  return q === 4095 ? null : K.vmin + q * K.step;
};
RF.fmt = function (v) {
  if (v == null || !isFinite(v)) return '–';
  var x = Math.abs(v), d = x >= 1000 ? 0 : x >= 100 ? 1 : x >= 1 ? 2 : 3;
  return Number(v).toLocaleString('en-US', {maximumFractionDigits: d});
};
RF.withUnit = function (v) {
  return RF.fmt(v) + (v != null && isFinite(v) && K.unit ? ' ' + K.unit : '');
};
RF.timeLabel = function (t) {
  if (t === undefined) t = RF.t;
  return t < 0 ? 'Whole window' : K.times[t];
};
RF.esc = function (s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
  });
};

// ---- towers: the worst sector of a site at this hour ---------------------
RF.siteWorst = function (idx) {
  var best = -1, at = -1;
  for (var j = 0; j < idx.length; j++) {
    var r = K.bands[RF.code(idx[j])].rank;
    if (r > best) { best = r; at = j; }
  }
  return at;
};
RF.siteColour = function (idx) {
  var j = RF.siteWorst(idx);
  return j < 0 ? null : K.bands[RF.code(idx[j])].colour;
};
RF.siteStatus = function (idx) {
  var j = RF.siteWorst(idx);
  if (j < 0) return null;
  var b = K.bands[RF.code(idx[j])];
  var when = RF.t >= 0 ? ' · ' + K.times[RF.t] : '';
  return b.key === 'none' ? 'No ' + K.label + ' data' + when
    : (b.label || b.interval) + ' · worst sector' + when;
};
RF.tipFor = function (i, base) {
  var b = RF.band(i), v = RF.value(i);
  var txt = b.key === 'none' ? 'no data'
    : (v != null ? RF.withUnit(v) : b.interval) + (b.label ? ' · ' + b.label : '');
  return base + ' · ' + K.label + ': ' + txt
    + (RF.t >= 0 ? ' · ' + K.times[RF.t].slice(5) : '');
};

function counts(t) {
  var c = [], s = K.codes[t + 1], i;
  for (i = 0; i < NB; i++) c.push(0);
  for (i = 0; i < N; i++) c[s.charCodeAt(K.src[i]) - 48]++;
  return c;
}

// ---- beams ----------------------------------------------------------------
function paintBeams() {
  if (!BEAMS) return;
  BEAMS.eachLayer(function (l) {
    var p = l.feature && l.feature.properties;
    if (!p || p.i == null) return;
    var col = RF.band(p.i).colour;
    l.setStyle({color: col, fillColor: col});
  });
}

// ---- legend (bottom-left): the bands and how many sectors sit in each ------
var legend = L.control({position: 'bottomleft'});
legend.onAdd = function () {
  var d = L.DomUtil.create('div', 'sm-legend');
  L.DomEvent.disableClickPropagation(d);
  L.DomEvent.disableScrollPropagation(d);
  return d;
};
legend.addTo(MAP);
function paintLegend(c) {
  var el = legend.getContainer(), total = Math.max(N, 1), h = '', j;
  h += '<div class="sm-lg-t">' + RF.esc(K.label) + '</div>';
  for (j = 0; j < NB; j++) {
    var b = K.bands[j];
    if (b.key === 'none' && !c[j]) continue;
    h += '<div class="sm-lg-row"><span class="sm-lg-chip" style="display:inline-block;'
      + 'width:14px;height:14px;background:' + b.colour + '"></span>'
      + '<span class="sm-lg-r">' + RF.esc(b.key === 'none' ? 'no data' : b.interval) + '</span>'
      + '<span class="sm-lg-n">(' + c[j].toLocaleString('en-US') + ', '
      + (100 * c[j] / total).toFixed(2) + '%)</span>'
      + (b.label ? '<span class="sm-lg-b">' + RF.esc(b.label) + '</span>' : '') + '</div>';
  }
  h += '<div class="sm-lg-note">' + RF.esc(RF.t < 0 ? K.window : K.times[RF.t])
    + (RF.t < 0 ? ' · window' : '') + '</div>';
  el.innerHTML = h;
}

// ---- the KPI cards above the map (same-origin page) -----------------------
function paintCards(c) {
  var doc;
  try { doc = window.parent.document; } catch (e) { return; }
  var cards = doc.querySelectorAll('.rf-kpi[data-rf-card]');
  if (!cards.length) return;
  var ok = 0, byKey = {}, total = Math.max(N, 1);
  K.bands.forEach(function (b, j) {
    byKey[b.key] = c[j];
    if (b.key.indexOf('ok') === 0) ok += c[j];
  });
  var val = {good: ok, warning: byKey.warning || 0, critical: byKey.critical || 0,
             measured: N - c[NONE], top: NB > 1 ? c[NB - 2] : 0, none: c[NONE]};
  Array.prototype.forEach.call(cards, function (card) {
    var k = card.getAttribute('data-rf-card');
    if (!(k in val)) return;
    var n = val[k], v = card.querySelector('.rf-kpi-val'),
        p = card.querySelector('.rf-kpi-pct'), bar = card.querySelector('.rf-kpi-bar i'),
        note = card.querySelector('.rf-kpi-note');
    if (v) v.textContent = n.toLocaleString('en-US');
    if (p) p.textContent = Math.round(100 * n / total) + '%';
    if (bar) bar.style.width = (100 * n / total).toFixed(1) + '%';
    if (note) {
      if (card.getAttribute('data-note0') == null) card.setAttribute('data-note0', note.textContent);
      note.textContent = card.getAttribute('data-note0') + (RF.t >= 0 ? ' · ' + K.times[RF.t] : '');
    }
  });
}

// ---- the time bar (bottom centre) -------------------------------------------
var bar = null, playTimer = null;
function controlsHtml(cls) {
  return '<button class="rf-prev" title="Previous timestamp">&#8249;</button>'
    + '<button class="rf-play" title="Play / pause">&#9654;</button>'
    + '<button class="rf-next" title="Next timestamp">&#8250;</button>'
    + '<input class="rf-range ' + (cls || '') + '" type="range" min="0" max="'
    + Math.max(T - 1, 0) + '" step="1" value="0">';
}
RF.bindControls = function (root) {
  root.querySelector('.rf-prev').onclick = function () { RF.pause(); RF.setT(RF.t <= 0 ? 0 : RF.t - 1); };
  root.querySelector('.rf-next').onclick = function () { RF.pause(); RF.setT(Math.min(T - 1, RF.t + 1)); };
  root.querySelector('.rf-play').onclick = function () { playTimer ? RF.pause() : RF.play(); };
  root.querySelector('.rf-range').oninput = function () { RF.pause(); RF.setT(+this.value); };
};
RF.paintControls = function (root) {
  var r = root.querySelector('.rf-range'), pos = RF.t < 0 ? 0 : RF.t;
  r.value = pos;
  r.classList.toggle('win', RF.t < 0);
  r.style.setProperty('--p', (T > 1 ? 100 * pos / (T - 1) : 0) + '%');
  root.querySelector('.rf-play').innerHTML = playTimer ? '&#10074;&#10074;' : '&#9654;';
};
RF.controlsHtml = controlsHtml;
if (T > 0) {
  bar = L.DomUtil.create('div', 'rf-tb', MAP.getContainer());
  bar.innerHTML = '<button class="rf-win" title="The whole window, as the cards and '
    + 'report show it">Window</button>' + controlsHtml()
    + '<div class="rf-tb-lbl"><b></b><span></span></div>';
  L.DomEvent.disableClickPropagation(bar);
  L.DomEvent.disableScrollPropagation(bar);
  RF.bindControls(bar);
  bar.querySelector('.rf-win').onclick = function () { RF.pause(); RF.setT(-1); };
}
function paintBar() {
  if (!bar) return;
  RF.paintControls(bar);
  bar.querySelector('.rf-win').classList.toggle('on', RF.t < 0);
  var lbl = bar.querySelector('.rf-tb-lbl');
  if (RF.t < 0) {
    lbl.querySelector('b').textContent = 'Whole window';
    lbl.querySelector('span').textContent = T + ' timestamps';
  } else {
    var s = K.times[RF.t];
    lbl.querySelector('b').textContent = s.slice(11);
    lbl.querySelector('span').textContent = s.slice(0, 10) + ' · ' + (RF.t + 1) + ' / ' + T;
  }
}

RF.play = function () {
  if (T < 2) return;
  if (RF.t < 0 || RF.t >= T - 1) RF.setT(0);
  playTimer = setInterval(function () {
    if (RF.t >= T - 1) { RF.pause(); return; }
    RF.setT(RF.t + 1);
  }, 900);
  RF.setT(RF.t);
};
RF.pause = function () {
  if (playTimer) { clearInterval(playTimer); playTimer = null; RF.setT(RF.t); }
};

RF.setT = function (t) {
  t = Math.max(-1, Math.min(T - 1, t | 0));
  RF.t = t;
  try { sessionStorage.setItem(K.store, String(t)); } catch (e) {}
  var c = counts(t);
  paintBeams();
  paintLegend(c);
  paintBar();
  paintCards(c);
  if (RF.repaintTowers) RF.repaintTowers();
  for (var j = 0; j < listeners.length; j++) {
    try { listeners[j](t); } catch (e) { if (window.console) console.error(e); }
  }
};

MAP.whenReady(function () {
  var t = -1;
  try {
    var s = sessionStorage.getItem(K.store);
    if (s != null && +s >= -1 && +s < T) t = +s;
  } catch (e) {}
  setTimeout(function () { RF.setT(t); }, 60);
  // Streamlit may redraw the cards on a rerun that does not rebuild the map
  setInterval(function () { paintCards(counts(RF.t)); }, 1500);
});
