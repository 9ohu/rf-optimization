// The Sector Details drawer inside the Sites map. Wrapped by SectorDrawer
// (Python), which defines MAP, CFG (a site's data built into the map, or null),
// ICON_TOWER and ICON_CHART. A beam or tower click goes to Python through
// st_folium's own click path; Python publishes the selected site's data beside
// the map (#rf-drawer-payload in the page), and the drawer reads it from there —
// so opening a sector never changes the map's script, and the map (its tiles,
// its view) is not rebuilt. Switching sectors, tabs and time stays here.
var RF = window.RF = window.RF || {};
RF.map = RF.map || MAP;
var D = null, cur = 0, tab = 'overview', hl = null, renderNow = null;
var svgR = L.svg({padding: 0.5});
var root = MAP.getContainer();
var TABS = [['overview', 'Overview'], ['ep', 'EP Details'], ['kpi', 'KPI Details'],
            ['cells', 'Cell Info'], ['history', 'History']];
var RAT = {'2G': '#A78BFA', '3G': '#2DD4BF', '4G': '#60A5FA', '5G': '#F472B6'};
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
  });
}
function num(v, d) {
  return (v == null || !isFinite(v)) ? '–'
    : Number(v).toLocaleString('en-US', {maximumFractionDigits: d == null ? 1 : d});
}
function store(k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
function load(k) { try { return JSON.parse(sessionStorage.getItem(k)); } catch (e) { return null; } }

// ---- talking to Python: st_folium sends its value on a map click -----------
function setGlobal(token, latlng) {
  try {
    var g = window.__GLOBAL_DATA__;
    g.last_object_clicked_tooltip = token + ' · #' + Date.now();
    g.last_object_clicked = latlng ? {lat: latlng.lat, lng: latlng.lng} : null;
    g.last_object_clicked_count = (g.last_object_clicked_count || 0) + 1;
    return true;
  } catch (e) { return false; }
}
RF.send = function (token, latlng) {
  if (setGlobal(token, latlng)) {
    MAP.fire('click', {latlng: latlng || MAP.getCenter(), rfSynthetic: true});
  }
};
// bubbles: the click is a real one that reaches the map by itself (a beam).
// The token is written after every other click handler has run: st_folium
// binds its own handler to the map's layers (tower badges included), and it
// would otherwise overwrite the token with the layer's tooltip text.
RF.select = function (sid, latlng, bubbles) {
  loading(sid);
  setTimeout(function () {
    if (bubbles) setGlobal(sid, latlng); else RF.send(sid, latlng);
  }, 0);
};
RF.openSite = function (site, idx, sids, latlng) {
  if (!sids || !sids.length) return;
  var j = (RF.siteWorst && idx.length === sids.length) ? RF.siteWorst(idx) : 0;
  RF.select(sids[Math.max(j, 0)], latlng, false);
};

// ---- the drawer shell -------------------------------------------------------
var dw = L.DomUtil.create('div', 'rf-dw', root);
dw.hidden = true;
L.DomEvent.disableClickPropagation(dw);
L.DomEvent.disableScrollPropagation(dw);
function topBar() {
  return '<div class="rf-dw-top"><div class="rf-dw-ttl">' + ICON_TOWER + 'Sector Details</div>'
    + '<button class="rf-dw-x" title="Close">&#10005;</button></div>';
}
function open() { dw.hidden = false; root.classList.add('rf-drawer-open'); }
function bindClose() {
  dw.querySelector('.rf-dw-x').onclick = function () {
    dw.hidden = true;
    root.classList.remove('rf-drawer-open');
    if (hl) { MAP.removeLayer(hl); hl = null; }
    RF.selSite = null;
    if (RF.repaintTowers) RF.repaintTowers();
    RF.send('__close__');
  };
}
function loading(sid) {
  open();
  dw.innerHTML = topBar() + '<div class="rf-dw-body"><div class="rf-empty">Opening '
    + esc(sid) + '…</div></div>';
  bindClose();
}

function show(payload) {
  D = payload;
  if (!D) {
    if (!dw.hidden) { dw.hidden = true; root.classList.remove('rf-drawer-open'); }
    if (hl) { MAP.removeLayer(hl); hl = null; }
    RF.selSite = null;
    renderNow = null;
    if (RF.repaintTowers) RF.repaintTowers();
    return;
  }
  cur = D.sel || 0;
  var saved = load('rf_sec');
  if (saved && saved.rev === D.rev) {
    for (var q = 0; q < D.sectors.length; q++) if (D.sectors[q].id === saved.sid) cur = q;
  }
  tab = load('rf_tab') || (RF.kpi ? 'kpi' : 'overview');
  RF.selSite = D.site.id;

  var K = function () { return RF.kpi; };
  var bandOf = function (s, t) {
    var k = K();
    if (!k || !s.codes) return null;
    if (t === undefined) t = RF.t;
    return k.bands[s.codes.charCodeAt(t + 1) - 48];
  };
  var valueOf = function (s, t) {
    if (t === undefined) t = RF.t;
    return t < 0 ? s.window : (s.series ? s.series[t] : null);
  };
  var statusOf = function (s) {
    var b = bandOf(s);
    if (b) return {label: b.key === 'none' ? 'No data' : (b.label || b.interval), colour: b.colour};
    return {label: D.site.air_label, colour: D.site.air_colour};
  };
  var badge = function (st) {
    return '<span class="rf-badge" style="--c:' + st.colour + '"><i></i>' + esc(st.label) + '</span>';
  };
  var kv = function (rows) {
    return '<div class="rf-kv">' + rows.filter(function (r) { return r[1] != null && r[1] !== ''; })
      .map(function (r) { return '<span>' + esc(r[0]) + '</span><b>' + esc(r[1]) + '</b>'; })
      .join('') + '</div>';
  };
  var table = function (t, statusCol) {
    if (!t || !t.rows.length) return '';
    var h = '<div class="rf-tbl-wrap"><table class="rf-tbl"><thead><tr>'
      + t.cols.map(function (c) { return '<th>' + esc(c) + '</th>'; }).join('') + '</tr></thead><tbody>';
    t.rows.forEach(function (r) {
      h += '<tr>' + r.map(function (v, j) {
        var dot = '';
        if (t.cols[j] === 'RAT') dot = '<span class="rf-dot2" style="--c:' + (RAT[v] || '#94A3B8') + '"></span>';
        if (j === statusCol) {
          var s = String(v).toLowerCase();
          var col = /deactiv|inactive|off|down|block/.test(s) ? '#94A3B8'
            : /activ|on ?air|up|normal/.test(s) ? '#22C55E' : '#64748B';
          dot = '<span class="rf-dot2" style="--c:' + col + '"></span>';
        }
        return '<td>' + dot + esc(v) + '</td>';
      }).join('') + '</tr>';
    });
    return h + '</tbody></table></div>';
  };

  // ---- tabs ------------------------------------------------------------------
  var overview = function (s) {
    var k = K(), h = '<div class="rf-tiles">'
      + '<div class="rf-tile"><span>Azimuth</span><b>' + num(s.azimuth, 0) + '°</b></div>'
      + '<div class="rf-tile"><span>RET</span><b>' + num(s.ret, 1) + '°</b></div>'
      + '<div class="rf-tile"><span>Height</span><b>' + num(s.height, 1) + ' m</b></div>'
      + '<div class="rf-tile"><span>Cells</span><b>' + (s.cells ? s.cells.rows.length : 0) + '</b></div></div>';
    if (k) {
      var st = statusOf(s);
      h += '<div class="rf-sec-t">' + esc(k.label) + ' <small>' + esc(RF.timeLabel()) + '</small></div>'
        + '<div class="rf-kc"><div class="rf-kc-val"><b>' + RF.fmt(valueOf(s))
        + '<small>' + esc(k.unit) + '</small></b>' + badge(st) + '</div></div>';
    }
    h += '<div class="rf-sec-t">Site</div>' + kv([
      ['Site ID', D.site.id], ['Site name', D.site.name], ['Sector', s.id],
      ['KMZ status', s.status], ['Technologies', D.site.techs.join(' · ')],
      ['Topology', D.site.topology.join(' · ')],
      ['Sectors on site', D.sectors.length]]);
    return h;
  };
  var ep = function (s) {
    var h = '<div class="rf-sec-t">EP Details / Parameter Tracker'
      + (D.files.ep ? ' <small>' + esc(D.files.ep) + '</small>' : '') + '</div>';
    if (!D.files.ep) return h + '<div class="rf-empty">No EP tracker loaded — add it under '
      + '<b>Data sources</b> in the sidebar.</div>';
    if (!s.ep || !s.ep.rows.length) return h + '<div class="rf-empty">' + esc(s.ep_note) + '</div>';
    return h + table(s.ep, s.ep.cols.indexOf('Status'))
      + '<div class="rf-note2">' + s.ep.rows.length + ' cell(s) · ' + esc(s.id) + '</div>';
  };
  var rangeBar = function (k, v, colour) {
    var lo = k.range[0], hi = k.range[1];
    if (lo == null || hi == null || !(hi > lo)) return '';
    var pct = function (x) { return Math.max(0, Math.min(100, 100 * (x - lo) / (hi - lo))); };
    var h = '<div class="rf-rwrap"><div class="rf-rbar">' + k.segs.map(function (g) {
      return '<i style="width:' + (pct(g[1]) - pct(g[0])).toFixed(2) + '%;background:' + g[2] + '"></i>';
    }).join('') + '</div>';
    if (v != null && isFinite(v)) h += '<span class="rf-rdot" style="left:' + pct(v).toFixed(2) + '%;--c:' + colour + '"></span>';
    h += '</div><div class="rf-rticks"><span class="l" style="left:0">' + RF.fmt(lo) + '</span>';
    if (k.rule) [k.rule.critical, k.rule.warning].forEach(function (x) {
      if (x > lo && x < hi) h += '<span style="left:' + pct(x).toFixed(2) + '%">' + RF.fmt(x) + '</span>';
    });
    return h + '<span class="r" style="left:100%">' + RF.fmt(hi) + '</span></div>';
  };
  var kpiTab = function (s) {
    var k = K();
    if (!k) return '<div class="rf-empty">No KPI on the map — pick one under <b>KPI analysis</b> '
      + 'in the sidebar, and its value for this sector shows here.</div>';
    var st = statusOf(s), v = valueOf(s), T = k.times.length, r = k.rule, rule = '';
    if (r) rule = r.direction === 'up'
      ? 'OK ≥ ' + RF.fmt(r.warning) + ' · critical < ' + RF.fmt(r.critical)
      : 'OK ≤ ' + RF.fmt(r.warning) + ' · critical > ' + RF.fmt(r.critical);
    var h = '<div class="rf-kc"><div class="rf-kc-h"><div class="rf-kc-ico">' + ICON_CHART + '</div>'
      + '<div class="rf-kc-name">' + esc(k.label) + '</div></div>'
      + '<div class="rf-kc-val"><b>' + RF.fmt(v) + '<small>' + esc(k.unit) + '</small></b>'
      + badge(st) + '</div>' + rangeBar(k, v, st.colour)
      + '<div class="rf-stats"><div><span>Min</span><b>' + RF.fmt(s.stats.min) + '</b></div>'
      + '<div><span>' + (k.how === 'sum' ? 'Avg / step' : 'Average') + '</span><b>' + RF.fmt(s.stats.avg) + '</b></div>'
      + '<div><span>Max</span><b>' + RF.fmt(s.stats.max) + '</b></div></div></div>';
    if (T > 0) {
      var t = RF.t, lbl = t < 0 ? 'Whole window' : k.times[t];
      h += '<div class="rf-kc"><div class="rf-sec-t" style="margin-top:0">Time <small>' + T
        + ' timestamps</small></div><div class="rf-tc">' + RF.controlsHtml()
        + '</div><div class="rf-tc-lbl"><b>' + esc(t < 0 ? 'Whole window' : lbl.slice(11)) + '</b><span>'
        + esc(t < 0 ? k.window : lbl.slice(0, 10) + ' · ' + (t + 1) + ' / ' + T) + '</span></div></div>';
    }
    h += '<div class="rf-kc">' + kv([
      ['Timestamp', RF.timeLabel()], ['Window', k.window],
      [k.how === 'sum' ? 'Window total' : 'Window value', RF.withUnit(s.window)],
      ['Threshold', rule || 'None set for this KPI'], ['Unit', k.unit || '–'],
      ['Aggregation', (k.how === 'sum' ? 'Sum' : 'Mean') + ' of the sector\'s cells per step'],
      ['Value from', s.src === 'site' ? 'Site (per-NodeB export)' : s.src === 'sector' ? 'Sector cells' : 'Not in the file'],
      ['Data source', k.file]]) + '</div>';
    return h;
  };
  var cells = function (s) {
    var h = '<div class="rf-sec-t">Cells / Bands <small>' + esc(D.files.kmz) + '</small></div>';
    if (!s.cells || !s.cells.rows.length) return h + '<div class="rf-empty">No cells on this sector '
      + 'for the technology shown.</div>';
    return h + table(s.cells, s.cells.cols.indexOf('Status'));
  };
  var chart = function (s, k) {
    var W = 340, H = 160, L0 = 34, R0 = 8, T0 = 10, B0 = 22, xs = s.series, n = xs.length;
    var lo = s.stats.min, hi = s.stats.max;
    if (k.rule) [k.rule.warning, k.rule.critical].forEach(function (x) {
      if (x >= lo - (hi - lo) * .5 && x <= hi + (hi - lo) * .5) { lo = Math.min(lo, x); hi = Math.max(hi, x); }
    });
    if (!(hi > lo)) { hi = lo + 1; lo = lo - 1; }
    var X = function (j) { return L0 + (n > 1 ? j * (W - L0 - R0) / (n - 1) : 0); };
    var Y = function (v) { return T0 + (hi - v) * (H - T0 - B0) / (hi - lo); };
    var g = '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none">';
    [lo, hi].forEach(function (v) {
      g += '<line x1="' + L0 + '" x2="' + (W - R0) + '" y1="' + Y(v) + '" y2="' + Y(v) + '" stroke="#16324F"/>'
        + '<text x="' + (L0 - 4) + '" y="' + (Y(v) + 3) + '" fill="#94A3B8" font-size="9" text-anchor="end">' + RF.fmt(v) + '</text>';
    });
    if (k.rule) [[k.rule.warning, '#FACC15'], [k.rule.critical, '#EF4444']].forEach(function (z) {
      if (z[0] > lo && z[0] < hi) g += '<line x1="' + L0 + '" x2="' + (W - R0) + '" y1="' + Y(z[0]) + '" y2="' + Y(z[0])
        + '" stroke="' + z[1] + '" stroke-dasharray="4 3" stroke-width="1"/>';
    });
    var path = '', pen = false;
    for (var j = 0; j < n; j++) {
      if (xs[j] == null) { pen = false; continue; }
      path += (pen ? 'L' : 'M') + X(j).toFixed(1) + ' ' + Y(xs[j]).toFixed(1);
      pen = true;
    }
    g += '<path d="' + path + '" fill="none" stroke="#20BFFF" stroke-width="1.8"/>';
    if (RF.t >= 0 && RF.t < n) {
      g += '<line x1="' + X(RF.t) + '" x2="' + X(RF.t) + '" y1="' + T0 + '" y2="' + (H - B0) + '" stroke="#F8FAFC" stroke-width="1" opacity=".6"/>';
      if (xs[RF.t] != null) g += '<circle cx="' + X(RF.t) + '" cy="' + Y(xs[RF.t]) + '" r="3.5" fill="#F8FAFC"/>';
    }
    g += '<text x="' + L0 + '" y="' + (H - 6) + '" fill="#94A3B8" font-size="9">' + esc(k.times[0].slice(5)) + '</text>'
      + '<text x="' + (W - R0) + '" y="' + (H - 6) + '" fill="#94A3B8" font-size="9" text-anchor="end">'
      + esc(k.times[n - 1].slice(5)) + '</text></svg>';
    return g;
  };
  var history = function (s) {
    var k = K(), h = '';
    if (!k) h += '<div class="rf-empty">No KPI on the map — pick one under <b>KPI analysis</b> to see '
      + 'this sector over the file\'s window.</div>';
    else if (!s.stats.n) h += '<div class="rf-empty">No ' + esc(k.label) + ' data for this sector in '
      + esc(k.file) + '.</div>';
    else h += '<div class="rf-sec-t">' + esc(k.label) + ' <small>' + s.stats.n + ' of '
      + k.times.length + ' timestamps</small></div><div class="rf-hist">' + chart(s, k) + '</div>';
    return h;
  };
  var BODY = {overview: overview, ep: ep, kpi: kpiTab, cells: cells, history: history};

  var render = function () {
    var s = D.sectors[cur], st = statusOf(s), body = dw.querySelector('.rf-dw-body');
    var scroll = body ? body.scrollTop : 0;
    var chips = [D.site.topology.join(' / '), D.site.techs.join(' / '), 'Sector ' + s.n,
                 D.site.air_label].filter(Boolean)
      .map(function (c) { return '<span class="rf-chip2">' + esc(c) + '</span>'; }).join('');
    var secs = D.sectors.length > 1 ? '<div class="rf-secs">' + D.sectors.map(function (x, j) {
      return '<button data-s="' + j + '" class="' + (j === cur ? 'on' : '') + '"><i style="--c:'
        + statusOf(x).colour + '"></i>Sector ' + esc(x.n) + '</button>';
    }).join('') + '</div>' : '';
    dw.innerHTML = topBar()
      + '<div class="rf-dw-head"><div class="rf-dw-ico">' + ICON_TOWER + '</div><div class="rf-dw-id">'
      + '<div class="rf-dw-name">' + esc(D.site.name) + '</div><div class="rf-dw-sub">'
      + esc(D.site.id) + ' · Sector ' + esc(s.n) + '</div></div>' + badge(st) + '</div>'
      + '<div class="rf-dw-meta">' + chips + '</div>' + secs
      + '<div class="rf-tabs">' + TABS.map(function (x) {
        return '<button data-t="' + x[0] + '" class="' + (x[0] === tab ? 'on' : '') + '">' + x[1] + '</button>';
      }).join('') + '</div><div class="rf-dw-body">' + BODY[tab](s) + '</div>';
    bindClose();
    Array.prototype.forEach.call(dw.querySelectorAll('.rf-secs button'), function (b) {
      b.onclick = function () {
        cur = +this.getAttribute('data-s');
        store('rf_sec', {rev: D.rev, sid: D.sectors[cur].id});
        highlight();
        render();
      };
    });
    Array.prototype.forEach.call(dw.querySelectorAll('.rf-tabs button'), function (b) {
      b.onclick = function () { tab = this.getAttribute('data-t'); store('rf_tab', tab); render(); };
    });
    var tc = dw.querySelector('.rf-tc');
    if (tc && RF.bindControls) { RF.bindControls(tc); RF.paintControls(tc); }
    var nb = dw.querySelector('.rf-dw-body');
    if (nb) nb.scrollTop = scroll;
  };
  var wedge = function (lat, lon, az, r) {
    var K1 = Math.PI / 180, c = Math.cos(lat * K1) || 1e-9, ring = [[lat, lon]];
    for (var i = 0; i <= 10; i++) {
      var b = (az - 23 + 4.6 * i) * K1;
      ring.push([lat + r * Math.cos(b) / 111320, lon + r * Math.sin(b) / (111320 * c)]);
    }
    return ring;
  };
  var highlight = function () {
    if (hl) { MAP.removeLayer(hl); hl = null; }
    var s = D.sectors[cur];
    if (s.lat == null || s.azimuth == null) return;
    hl = L.polygon(wedge(s.lat, s.lon, s.azimuth, s.r), {renderer: svgR, color: '#F8FAFC',
      weight: 3, fillColor: '#20BFFF', fillOpacity: 0.35, className: 'rf-sel-beam',
      interactive: false}).addTo(MAP);
  };

  renderNow = render;
  open();
  highlight();
  render();
  MAP.whenReady(function () { setTimeout(function () { if (RF.repaintTowers) RF.repaintTowers(); }, 200); });
}
if (RF.onTime) RF.onTime(function () { if (!dw.hidden && renderNow) renderNow(); });

// the selected site's data, as the page publishes it beside the map
var lastRev = null;
function poll() {
  var el = null;
  try { el = window.parent.document.getElementById('rf-drawer-payload'); } catch (e) {}
  if (!el) return;
  var rev = el.getAttribute('data-rev') || '';
  if (rev === lastRev) return;
  lastRev = rev;
  var txt = (el.textContent || '').trim();
  try { show(txt ? JSON.parse(txt) : null); } catch (e) {}
}
if (CFG) { lastRev = CFG.rev; show(CFG); }
poll();
setInterval(poll, 250);
