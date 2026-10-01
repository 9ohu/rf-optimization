# How to run the RF Optimizer

## Start the app

Double-click **`Run RF Optimizer.bat`** in this folder.
A black window opens (keep it open) and your browser goes to
**http://localhost:8601**. To stop the app, close the black window.

## Two pages (left sidebar)

### 📋 Dashboard  — the daily worklist

1. Put the day's files on your **Desktop** (or Downloads):
   * `Target <date>.xlsx`  — the ticket list
   * `4G Monitoring Hourly KPI's-… .zip`  — the 24 h hourly KPI export
   (the `WK.. Engineering Parameter Tracker` on `D:\WeLink_data_files\...` is
   picked up automatically.)
2. Press **Analyse worklist** (first run ≈ 15 s, then ≈ 3–10 s).
3. Tabs:
   * **Worklist** – every ticket: `priority` (P1–P4), `likely_cause`,
     `kpi_evidence` (the measured proof), `next_check` (what to do). Filter,
     search, and inspect any site's sectors.
   * **Map** – R5 sites; today's tickets coloured P1–P4, dot size ∝ tickets.
   * **Summary** – priority × cause, sites with the most tickets.
   * **Site flags** – every parameter flag found.
   * **Export** – **Excel report** (4 sheets) and **worklist KMZ**.

### 🗺️ Site Map

**Every** R5 tower and sector beam, drawn from your Google-Earth
**`R5_Sites.kmz`** – put it in `~/Downloads` (the page finds it automatically)
or use **Upload R5_Sites.kmz** in the sidebar. The KMZ is the *only* site-data
source; the whole map updates when you drop in a newer file.

It's a real Leaflet map (folium):

* **Topology** (sidebar) – `All / 4G / 3G / 2G`. Picks which sectors are shown
  and which cells open when you click.
* **Beam length (m)** (sidebar slider) – shorten it in dense areas so sectors
  don't overlap; lengthen it when zoomed out.
* **Layers ☰** (top-right of the map) – toggle **Sector beams**, **Sectors**,
  **Site-ID labels**, **Distance lines**.
* **Tools (top-left of the map, work in full-screen):**
  * ⛶ full-screen
  * 📏 **Measure** – click along the map, double-click to finish; shows the
    running distance, just like Google Earth
  * ／ **Line**, ● **Circle**, 📍 **Marker** – draw with the mouse; the app
    reports the line length / circle radius / pin lat-lon underneath, and the
    shape stays until you press **Clear drawings**
  * bottom-left corner always shows the **cursor's lat / lon**
* **Click a sector dot** to see every cell on it: **cell name, band, azimuth,
  height, RET** (and status). RET falls back to the antenna's actual tilt when
  a cell has no MAX RET of its own.
* **Find a site** / **Complaint location** search bars re-centre the map on
  the hit — **every tower stays drawn**, nothing is hidden.
* **Distance lines** (sidebar toggle) – a complaint lat/lon search draws a
  line to each of the *N* nearest sites (slider, default 6) with its distance;
  the best-pointed site's line is green.
* **Export today's worklist KMZ** (shown once the Dashboard has run) – one
  P1–P4 pin per ticket site for Google Earth; locations come from the KMZ.

### 🌙 Offline basemap (Sites map)

The Sites map draws every basemap — **Dark, Streets, Satellite, Coverage and
Night Satellite** — from local map packs, with **no Internet**. Fetch the packs
once (this is the only step that uses the Internet):

* in the app: **Map layers & Analysis → Offline map → Update offline map**
  (tick *Regional satellite* for the Sentinel-2 pack and *Building-scale
  satellite* for the sharp imagery around the sites), or
* from a terminal: `python scripts/update_offline_map.py` (`--imagery`,
  `--detail --arcgis-key KEY --areas BAS`; `--help` for the options).

**Building-scale satellite (sharp buildings, rooftops and streets).** The
Satellite and Night Satellite maps need the *building-scale* pack: Esri World
Imagery (Maxar aerial / satellite photography, about 0.3–0.6 m in Iraq's
cities), stored up to zoom 18 (0.5 m pixels) around every site. It is
licensed imagery, fetched with **your own ArcGIS API key**:

1. Get an ArcGIS account — an ArcGIS Online organisational account, or an
   *ArcGIS Location Platform* developer account (free tier,
   https://location.arcgis.com) — and an **API key with the basemaps
   privilege**.
2. Sites map → Map layers & Analysis → **Offline map** → tick
   *Building-scale satellite around the sites*, paste the key (it is kept on
   this PC only, never in the map packs).
3. Pick the **areas** (BAS, NAS, EMA, SAM), the sharp radius around each site
   and the sharpest zoom; the box shows the tile count and size before you
   start (roughly 4–5 GB for about 2,000 sites at the defaults). Press
   **Update offline map**.
4. Large networks: one area at a time — the pack keeps what it already has
   and adds each area; an update that was cut short carries on where it
   stopped when you press Update again.

Without that pack the satellite maps fall back to Sentinel-2 (10 m — fields
and town outlines, no single buildings), the sharpest openly licensed imagery.

The packs are saved to `~/.rfopt_cache/resources/basemap` (or the folder in
`RFOPT_BASEMAP_DIR`). Copy that folder to another PC to use the same offline
map there. Sources: OpenStreetMap (streets, places), NASA Black Marble (night
lights), NASA Blue Marble (country-scale satellite), Sentinel-2 cloudless 2016
by EOX (regional satellite, CC BY 4.0), Esri World Imagery (building-scale
satellite: Esri, Maxar, Earthstar Geographics; used under your ArcGIS
account's terms).

## Where things are saved

Reports/KMZ you build in the app download to your browser's Downloads.
The first parse of the big param / KPI files is cached under `~/.rfopt_cache`
(delete it any time to force a fresh parse).

## Bringing back the KPI-engine pages

The generic KPI analysis / diagnosis / cell-deep-dive / reports pages live in
`app/_advanced/` and are not shown. To re-enable, uncomment the block in
`app/Home.py`.

## Command line

```
.venv\Scripts\python.exe -c "from rfopt.complaints.worklist import run_worklist; from rfopt.ingest.cellparams import load_cell_params; from rfopt.ingest.hourly_kpi import load_hourly_kpi; p=load_cell_params(r'D:\WeLink_data_files\swx1351646\ReceiveFiles\WK37 Engineering Parameter Tracker-06092026.xlsx').df; k=load_hourly_kpi(r'C:\Users\swx1351646\Desktop\<hourly kpi>.zip').df; run_worklist(r'C:\Users\swx1351646\Desktop\Target 8-Sep.xlsx', p, 'out/Target_8-Sep_RF_analysis.xlsx', kpi=k)"
```
