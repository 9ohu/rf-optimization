/* RF Optimization — the Sites map's offline basemap.
 *
 * Every mode is drawn from the local map packs only (see
 * rfopt/geo/offline_basemap.py), served by the app's local relay:
 *   region.pmtiles   vector: streets, water, places (OpenStreetMap / Protomaps)
 *   night.pmtiles    raster: NASA Black Marble night lights
 *   earth.pmtiles    raster: NASA Blue Marble (country scale, 500 m)
 *   imagery.pmtiles  raster: Sentinel-2 cloudless (regional scale, 10 m), optional
 *   detail.pmtiles   raster: Esri World Imagery (building scale, 0.5 m) around
 *                    the sites, optional
 * No request ever leaves the machine. A pack that is not installed is left
 * out, and the map says which one is missing.
 *
 * Dark / Streets use the Protomaps "dark" / "light" flavours unchanged
 * (protomaps/basemaps, BSD-3-Clause). Night Satellite and the roads & names
 * over the day imagery are flavours of that same schema, defined here.
 */
(function () {
  "use strict";

  // the Protomaps "dark" flavour (protomaps/basemaps 5.x, BSD-3-Clause):
  // the base every custom flavour below starts from
  var DARK = {background:"#34373d",earth:"#1f1f1f",park_a:"#1c2421",park_b:"#192a24",hospital:"#252424",industrial:"#222222",school:"#262323",wood_a:"#202121",wood_b:"#202121",pedestrian:"#1e1e1e",scrub_a:"#222323",scrub_b:"#222323",glacier:"#1c1c1c",sand:"#212123",beach:"#28282a",aerodrome:"#1e1e1e",runway:"#333333",water:"#31353f",zoo:"#222323",military:"#242323",tunnel_other_casing:"#141414",tunnel_minor_casing:"#141414",tunnel_link_casing:"#141414",tunnel_major_casing:"#141414",tunnel_highway_casing:"#141414",tunnel_other:"#292929",tunnel_minor:"#292929",tunnel_link:"#292929",tunnel_major:"#292929",tunnel_highway:"#292929",pier:"#333333",buildings:"#111111",minor_service_casing:"#1f1f1f",minor_casing:"#1f1f1f",link_casing:"#1f1f1f",major_casing_late:"#1f1f1f",highway_casing_late:"#1f1f1f",other:"#333333",minor_service:"#333333",minor_a:"#3d3d3d",minor_b:"#333333",link:"#3d3d3d",major_casing_early:"#1f1f1f",major:"#3d3d3d",highway_casing_early:"#1f1f1f",highway:"#474747",railway:"#000000",boundaries:"#5b6374",bridges_other_casing:"#2b2b2b",bridges_minor_casing:"#1f1f1f",bridges_link_casing:"#1f1f1f",bridges_major_casing:"#1f1f1f",bridges_highway_casing:"#1f1f1f",bridges_other:"#333333",bridges_minor:"#333333",bridges_link:"#3d3d3d",bridges_major:"#3d3d3d",bridges_highway:"#474747",roads_label_minor:"#525252",roads_label_minor_halo:"#1f1f1f",roads_label_major:"#666666",roads_label_major_halo:"#1f1f1f",ocean_label:"#717784",subplace_label:"#525252",subplace_label_halo:"#1f1f1f",city_label:"#7a7a7a",city_label_halo:"#212121",state_label:"#3d3d3d",state_label_halo:"#1f1f1f",country_label:"#5c5c5c",address_label:"#525252",address_label_halo:"#1f1f1f",pois:{blue:"#4299BB",green:"#30C573",lapis:"#2B5CEA",pink:"#EF56BA",red:"#F2567A",slategray:"#93939F",tangerine:"#F19B6E",turquoise:"#00C3D4"},landcover:{grassland:"rgba(30, 41, 31, 1)",barren:"rgba(38, 38, 36, 1)",urban_area:"rgba(28, 28, 28, 1)",farmland:"rgba(31, 36, 32, 1)",glacier:"rgba(43, 43, 43, 1)",scrub:"rgba(34, 36, 30, 1)",forest:"rgba(28, 41, 37, 1)"}};

  function flavour(over) {
    var f = JSON.parse(JSON.stringify(DARK));
    Object.keys(over).forEach(function (k) {
      if (over[k] && typeof over[k] === "object") {
        f[k] = Object.assign({}, f[k] || {}, over[k]);
      } else {
        f[k] = over[k];
      }
    });
    return f;
  }

  // Night Satellite: black land, navy water, the road network glowing amber
  // like sodium street light, lit built-up areas, warm place names — over the
  // NASA night lights, in the app's dark navy / cyan telecom palette.
  var NIGHT = flavour({
    background: "#02040a", earth: "#04070e", water: "#051a30",
    park_a: "#050a0f", park_b: "#050a0f", wood_a: "#050a0f", wood_b: "#050a0f",
    scrub_a: "#05080d", scrub_b: "#05080d", sand: "#06080c", beach: "#08101a",
    hospital: "#1a1208", industrial: "#140f08", school: "#140f08", military: "#0b0d12",
    pedestrian: "#1c1409", aerodrome: "#0b0f17", runway: "#3a3f4a", zoo: "#060a0f",
    glacier: "#0a0d12", pier: "#15202e", buildings: "#3a2710",
    highway: "#ffb54d", highway_casing_early: "#3a2206", highway_casing_late: "#3a2206",
    major: "#f59e0b", major_casing_early: "#2a1804", major_casing_late: "#2a1804",
    link: "#e8900c", link_casing: "#2a1804",
    minor_a: "#b86a12", minor_b: "#8a4f10", minor_casing: "#1a1005",
    minor_service: "#6b3d0c", minor_service_casing: "#120b04", other: "#5a340b",
    railway: "#4b5a72", boundaries: "#2fa8e0",
    bridges_highway: "#ffb54d", bridges_major: "#f59e0b", bridges_link: "#e8900c",
    bridges_minor: "#b86a12", bridges_other: "#5a340b",
    tunnel_highway: "#7a4b12", tunnel_major: "#6b4210", tunnel_link: "#6b4210",
    tunnel_minor: "#4a2e0c", tunnel_other: "#3a240a",
    roads_label_minor: "#c89a5a", roads_label_minor_halo: "#02040a",
    roads_label_major: "#ffcf7a", roads_label_major_halo: "#02040a",
    ocean_label: "#5fb6e6", subplace_label: "#e8c99a", subplace_label_halo: "#02040a",
    city_label: "#fff1d6", city_label_halo: "#02040a",
    state_label: "#7f93ad", state_label_halo: "#02040a", country_label: "#a9bdd6",
    address_label: "#a07a45", address_label_halo: "#02040a",
    landcover: {grassland: "#05080d", barren: "#06080c", urban_area: "#3b2508",
                farmland: "#05090c", glacier: "#0a0d12", scrub: "#05080d",
                forest: "#050a0f"}
  });

  // roads and names over day imagery: light roads with a dark casing
  var OVER_SAT = flavour({
    highway: "#ffe38a", highway_casing_early: "#1b1b1b", highway_casing_late: "#1b1b1b",
    major: "#f8fafc", major_casing_early: "#1b1b1b", major_casing_late: "#1b1b1b",
    link: "#f1f5f9", link_casing: "#1b1b1b", minor_a: "#e2e8f0", minor_b: "#cbd5e1",
    minor_casing: "#1b1b1b", minor_service: "#cbd5e1", other: "#cbd5e1",
    bridges_highway: "#ffe38a", bridges_major: "#f8fafc", bridges_minor: "#e2e8f0",
    boundaries: "#f8fafc", railway: "#94a3b8",
    roads_label_minor: "#f1f5f9", roads_label_minor_halo: "#000000",
    roads_label_major: "#ffffff", roads_label_major_halo: "#000000",
    city_label: "#ffffff", city_label_halo: "#000000",
    subplace_label: "#f1f5f9", subplace_label_halo: "#000000",
    state_label: "#e2e8f0", state_label_halo: "#000000", country_label: "#ffffff",
    ocean_label: "#bae6fd", address_label: "#e2e8f0", address_label_halo: "#000000"
  });

  // the vector layers whose fills would hide the imagery under them
  var FILLS = {earth: 1, landcover: 1, landuse: 1, natural: 1};

  function onlyLines(rules) {
    return rules.filter(function (r) { return !FILLS[r.dataLayer]; });
  }

  function css(text) {
    var s = document.createElement("style");
    s.textContent = text;
    document.head.appendChild(s);
  }

  // Night Satellite over day imagery: the imagery graded to night (dark,
  // cool, colour drained — a colour grade, its pixels untouched), the NASA
  // city lights screened over it so they glow without hiding it
  css(".rf-night-roads canvas{filter:drop-shadow(0 0 1.6px rgba(255,170,60,.9))" +
      " drop-shadow(0 0 5px rgba(255,140,30,.35))}" +
      ".rf-night-lights{filter:saturate(1.35) brightness(1.15) contrast(1.1)}" +
      ".rf-night-glow{mix-blend-mode:screen}" +
      ".rf-night-sat{filter:grayscale(.6) sepia(.4) hue-rotate(185deg) saturate(1.5)" +
      " brightness(.36) contrast(1.3)}" +
      ".rf-bm-note{background:rgba(7,21,37,.92);color:#cbd5e1;border:1px solid #1E3A5F;" +
      "border-radius:8px;padding:5px 9px;font:600 11px 'Segoe UI',system-ui,sans-serif;" +
      "max-width:330px;box-shadow:0 3px 12px rgba(0,0,0,.45)}" +
      ".rf-bm-note b{color:#FACC15}");

  function note(map, text) {
    var ctl = L.control({position: "bottomleft"});
    ctl.onAdd = function () {
      var d = L.DomUtil.create("div", "rf-bm-note");
      d.innerHTML = text;
      return d;
    };
    ctl.addTo(map);
  }

  window.rfBasemap = function (map, cfg) {
    var P = window.protomapsL, T = window.pmtiles;
    var packs = cfg.packs || {}, base = cfg.base, mode = cfg.mode;
    var mapEl = map.getContainer();
    var missing = [];
    if (!P || !T || !base) {
      mapEl.style.background = "#0B1F33";
      note(map, "<b>Offline map</b> — the map libraries did not load.");
      return;
    }
    function url(key) { return base + packs[key].file; }
    function has(key) { return !!(packs[key] && packs[key].installed); }
    function raster(key, opts) {
      var p = packs[key];
      return T.leafletRasterLayer(new T.PMTiles(url(key)), Object.assign({
        maxNativeZoom: p.native_max, maxZoom: 22, attribution: p.attribution,
        keepBuffer: 3, updateWhenZooming: false
      }, opts || {}));
    }
    function vector(opts) {
      return P.leafletLayer(Object.assign({
        url: url("vector"), maxDataZoom: packs.vector.native_max, maxZoom: 22,
        attribution: packs.vector.attribution, lang: cfg.lang || "en"
      }, opts || {}));
    }
    var refPane = cfg.refPane || undefined;

    if (mode === "Dark" || mode === "Streets") {
      mapEl.style.background = mode === "Dark" ? DARK.background : "#e8e8e8";
      if (has("vector")) {
        vector({flavor: mode === "Dark" ? "dark" : "light", zIndex: 1}).addTo(map);
      } else { missing.push("vector"); }
    } else if (mode === "Night Satellite") {
      mapEl.style.background = NIGHT.background;
      // the day imagery, graded to night: the regional imagery only at the
      // zooms it holds (never stretched into a blur), the building-scale
      // pack from there in, so blocks and streets read
      var graded = false;
      if (has("imagery")) {
        raster("imagery", {zIndex: 1, minZoom: 10, maxZoom: packs.imagery.native_max,
                           className: "rf-night-sat"}).addTo(map);
        graded = true;
      }
      if (has("detail")) {
        raster("detail", {zIndex: 2, minZoom: packs.detail.min_zoom,
                          className: "rf-night-sat"}).addTo(map);
        graded = true;
      } else { missing.push("detail"); }
      var lit = has("night");
      if (lit) {
        var lights = raster("night", {
          zIndex: 3, className: "rf-night-lights" + (graded ? " rf-night-glow" : "")
        }).addTo(map);
        // NASA's night lights stop at 500 m pixels: fade them out as the map
        // zooms in, where the glowing streets (and the imagery) take over.
        // Over the imagery they fade faster — a bright city core screened
        // over it at street zoom would wash the night out.
        var FADE = [1, 0.8, 0.55, 0.35, 0.22, 0.12];        // zoom 10 .. 15+
        var fade = function () {
          var z = map.getZoom();
          lights.setOpacity(graded
            ? FADE[Math.max(0, Math.min(FADE.length - 1, Math.round(z) - 10))]
            : (z <= 10 ? 1 : z >= 16 ? 0.35 : 1 - (z - 10) * 0.108));
        };
        map.on("zoomend", fade);
        fade();
      } else { missing.push("night"); }
      if (has("vector")) {
        var under = lit || graded;
        var night = {
          paintRules: under ? onlyLines(P.paintRules(NIGHT)) : P.paintRules(NIGHT),
          labelRules: P.labelRules(NIGHT, cfg.lang || "en"),
          backgroundColor: under ? undefined : NIGHT.background,
          className: "rf-night-roads", zIndex: 4
        };
        if (refPane) { night.pane = refPane; }
        vector(night).addTo(map);
      } else { missing.push("vector"); }
    } else {                                   // Satellite, Coverage
      mapEl.style.background = "#0B1F33";
      var sat = mode === "Satellite";
      var day = false;
      // Satellite: NASA Blue Marble at country scale only — its 500 m pixels
      // are never stretched over a street; the imagery packs draw those
      if (has("earth")) {
        raster("earth", sat ? {zIndex: 1, maxZoom: 12} : {zIndex: 1}).addTo(map);
        day = true;
      }
      if (has("imagery")) { raster("imagery", {zIndex: 2}).addTo(map); day = true; }
      if (!has("imagery")) { missing.push("imagery"); }
      // the building-scale imagery over the rest, around the sites
      if (sat && has("detail")) {
        raster("detail", {zIndex: 3, minZoom: packs.detail.min_zoom}).addTo(map);
        day = true;
      }
      if (sat && !has("detail")) { missing.push("detail"); }
      if (has("vector")) {
        var over = day ? {
          paintRules: onlyLines(P.paintRules(OVER_SAT)),
          labelRules: P.labelRules(OVER_SAT, cfg.lang || "en"), zIndex: 4
        } : {flavor: "dark", zIndex: 4};
        if (refPane) { over.pane = refPane; }
        vector(over).addTo(map);
      } else { missing.push("vector"); }
    }
    if (missing.length) {
      var names = {vector: "streets &amp; places", night: "night lights",
                   imagery: "regional imagery (10 m)",
                   detail: "building-scale imagery (0.5 m)", earth: "satellite"};
      note(map, "<b>Offline map</b> — not installed: " +
           missing.map(function (k) { return names[k] || k; }).join(", ") +
           ". Map layers &amp; Analysis → Offline map → Update.");
    }
  };
})();
