# Project status / handoff  (updated 2026-09-25)

## 2026-09-25 (cont.) - Sleep Analysis: the modifications the R5 team asked for

Additions to the page as it stands, nothing rebuilt:

- **Tickets by RF Analysis** joins Tickets by Closure Code and Tickets by User;
  the three now sit in one full-width row under the four count cards (three
  charts beside the cards left neither a closure code nor a card readable).
  It counts column FE, never the closure code.
- **An RF Analysis filter** (column FE) beside the seven that were there.
- **The table**: Status (column ER, "Sleep" for this population) and Distance
  (the subscriber's Log/Lat to the site that served them, from the EP tracker
  and the site KMZ). The Ticket ID moved to the first column, which is the one
  the table pins, so it stays in view as the rest scrolls sideways.
- **Current KPI Status** is a donut of Issue against Normal over the judged
  KPIs of the serving sector, each one's reading listed beside the ring.
- **KPI Trend** offers exactly PRB Utilization, 4G Availability, 4G
  Interference, Flow Control and RTWP — the last two are counted for the NodeB,
  so they draw the site's own line — and carries **Copy Chart**, the same
  script Draw Data and Bulk Draw use.
- **Sector Comparison** now reaches the sites around the serving one (EP
  tracker, within 2 km) and reads: Sector Type (Serving Sector / Same Site /
  Neighbor Site), Sector, Cell Name, KPI Issue, Max Value, Average Value,
  Distance, Another Issue Impacted, Status. The serving row is green and leads.
- **The map** is taller and switches between Satellite and Coverage (the
  measured grid over the imagery, as the Sites map draws it). It adds the
  neighbouring sites and their sectors, and says the distance to the serving
  site, the bearing to the subscriber and how far off the serving sector's
  boresight that is.
- **The description** reads as an engineer's own: "The serving sector is
  BAS3245-2 and the distance to the serving site is 907 m. ..." — every number
  still from the data, nothing about AI.

Under it, `Evidence` now holds a `Stat` per KPI (top / mean / low / hours past
the line), which is what the comparison reads; the named fields (`prb_max`,
`rssi_max` ...) read from it, so the verdicts did not change: 755 Solve, 923
Not Solve, 218 Not Checked. Tests: 23 in `tests/test_sleep_analysis.py`; suite
289.

## 2026-09-25 - Sleep Analysis (phase 1): is the sleep ticket's issue still there

A new page under Complaints, `views/sleep_analysis.py` (+ `app/_sleep.py`,
`rfopt/sleep/analysis.py`). It reads the History of Tickets export already in
Data Resources — no new upload, no new parser, nothing written — keeps the
tickets closed as **Sleep** under the R5 team's eight closure codes (1,896 of
the 1,948 sleep tickets in the current file) and asks the network whether the
issue is still there.

**The check follows the ticket**, chosen from its RF Analysis where that names
one and from its closure code otherwise:

| check | evidence | line |
|---|---|---|
| utilization | the serving sector's hourly DL PRB | `dl_prb_util` critical (85 %) |
| interference | the sector's hourly UL interference | `ul_rssi_dbm` critical (-105 dBm) |
| flow control | the site's 3G flow-control counter | any hour with a drop |
| coverage / indoor | the measured RSRP where the subscriber was | `avg_rsrp_dbm` warning (-105 dBm) |
| planned | the site KMZ for column BP, then the coverage at that point | On Air / Still Not On Air |

Every line is the operator's own (`config/thresholds_lte.yaml`), read through
the same `canonical_name` the rest of the app uses.

- **The serving sector is column FB**, never the whole site: `BAS0480-2` is
  site BAS0480 sector 2, and the sector's hour is its worst carrier (the EP
  tracker says which cells it holds, as Bulk Draw reads it).
- **Solve / Not Solve / Not Checked.** A check with nothing to read answers Not
  Checked — the reference image's "Pending (Not Checked)" card — rather than
  counting a ticket as solved on no evidence. On the current data: 755 Solve,
  923 Not Solve, 218 Not Checked.
- **The description is the check's own working**: "Serving sector BAS0438-2 is
  still experiencing high utilization. Maximum PRB utilization reached 99 %,
  with high PRB observed for 28 hours of the 58 measured." No sentence carries
  a number the check did not read.
- **RSRP** is the nearest measured coverage grid to the subscriber's Log/Lat
  (blank beyond 150 m). All 1,896 points are looked up in one pass through a
  KD-tree — a point at a time walked every grid row and took 172 s.
- **The table** is the Tickets Details component, which now takes its columns
  from the caller (`show(columns=…, open_key=…, sort_field=…)`): 19 columns in
  the order the team asked for, Excel-style header filters, search, sort,
  sideways scroll, Excel export, and a row click that opens the ticket below.
  Tickets Details itself is unchanged. Two coloured label kinds were added to
  the component's JS (`verdict`, `air`).
- **Problem Time** (column AA) is not in the history parser and that parser was
  not touched: `_sleep.problem_times()` reads that one column from the same
  file by its own header and joins it on the HPSM id.
- **The selected ticket** shows the five panels of the reference: Current KPI
  Status, Sector Comparison (the site's other sectors, the serving one marked),
  KPI Trend (Draw Data's own chart, hour by hour, never a daily average), Map &
  User Location (the site, the sector beams with the serving one lit, the
  subscriber's point, fitted to both) and the Description.

Phase 1 only: no Report View, no PowerPoint, no PDF. The reference image's
other tabs (Location Analysis, History, Recommended Action) are not built —
there is nothing behind them yet that is not already in these five panels.

Tests: `tests/test_sleep_analysis.py` (19) — the population, the sector split,
the plan site carried only where the ticket is about one, the worst-carrier
hour, the RSRP lookup, each verdict and its comment, and the page. Suite 285.

## 2026-09-24 (cont.) - The data monitor is gone; Data Resources is the one place

The user asked for every Data Monitor panel out of the application and the
Data Resources page left exactly as it is. What went:

- the sidebar **"Data (from Data Resources)"** panel on the Dashboard, the
  Daily Worklist, History of Tickets and Delay Tickets Analysis;
- the sidebar **"KPI Data"** panel of every KPI page (`_kpi_workspace._files`
  still reads the files and still clears drawn charts when other KPI Data is
  applied — it just says nothing) and of Bulk Draw;
- the map's sidebar **"Data sources"** expander and its **"Data sources"**
  card at the foot of the page (the row is two panels wide now), and the file
  listings beside the map's KPI and coverage pickers;
- `R._resources.source_card()` itself, which existed only to draw those cards.

What stayed: the Data Resources page, untouched — its uploads, staging,
Apply / Replace, Active Data, storage and URL; the "Open Data Resources" links
that appear when a page has no data yet; the map legend and the complaints
card that name the file a drawing or a number came from; the Dashboard strip's
"n of 4 datasets" line, which the user asked for with the page.

**Data Resources is listed in the sidebar again** (`app/_ui.py` no longer
hides it). It was hidden on 2026-09-20 while every page carried a "Manage in
Data Resources" link; with those links gone the page would have had no way in
but its URL, and the user asked for the sidebar item to stay.

Tests: the five that asserted the panels now assert their absence
(`test_data_resources.py::test_no_other_page_uploads_anything`,
`test_dashboard.py::test_the_dashboard_is_the_landing_page_and_data_resources_is_listed`,
`test_app_pages.py`, `test_kpi_many_files.py` x2); suite 266 passed.

## 2026-09-24 - Bulk Draw: Copy Chart, and the report as a PowerPoint

Six updates the user asked for, all additive — the KPI logic, the EP mapping,
Per Site / Per Sector, the chart design, the Excel export and Single Draw are
untouched.

- **Copy Chart** on every individual chart (and the combined one), the very
  script Draw Data uses: `COPY_JS` moved out of `app/views/kpi_draw.py` into
  `app/_charts.py`, and both pages inject it. The card head is now Draw Data's
  own `.sm-chart-head` — the title on the left, "4G · <KPI>" where the trend
  verdict sits, the button on the right. The card's key is `rf_card_kb_` plus
  the group key with `\W` replaced ("BAS0438|2" → `BAS0438_2`): Streamlit
  writes `|` as `-` in the `st-key-` class, and the script looks the card up by
  that class, so a Per Sector chart could not be copied before.
- **The report is a PowerPoint** (`rfopt/reports/bulk_deck.py`, `Deck` +
  `build`), nothing else: no HTML, no PDF. Its dress is the Report Export's own
  (`rfopt/reports/kpi_report`) — the navy slide, the rounded panel cards, the
  cyan rule under the header, the Huawei wordmark, the cover with KPI /
  technology / drawn by / charts / window / project / prepared.
- **Six charts to a slide**, three across and two down, each under its title
  ("BAS3114", or "BAS3114 - Sector 2") with no description below it; the extra
  slides come automatically (40 charts → 7 slides). The header reads
  "Charts 1–6" and the slide is numbered "n / total".
- **The combined chart** comes after the individual ones, on a slide of its
  own; its legend is drawn only when 14 names or fewer would fit, otherwise the
  line under the title says how many cells are in it.
- **Nothing about AI** anywhere in the package (a test greps the .pptx bytes).
- **The charts are the drawn ones.** kaleido is now installed (added to
  requirements), so `chart_pngs()` renders the very figures the page draws and
  the deck places those pictures. They are written together in one call
  (`plotly.io.write_images`): a picture at a time re-opens the engine's browser
  and costs ~8 s each. The chart is drawn on nothing — the page shows through
  it — so `_lighter()` lays each picture on the deck's own card colour and
  gives it a 256-colour palette: the same thing to look at, a fifth of the
  bytes, 33 MB → 5.6 MB for 78 charts.
- **Without kaleido** nothing breaks: `chart_pngs()` returns None for each and
  the deck draws the same lines as a native PowerPoint chart — same series,
  same hours, same Draw Data colours (`LINE_COLOURS`), no markers, the chart
  area navy and the plot area transparent.
- Real data (Book4.xlsx, 4G): Per Sector 77 charts → 15 slides (1 + 13 + 1),
  Per Site 68 → 14, a 3G site-level KPI 8 → 4; 5.6 MB and ~60 s for the 77
  (the spinner says "Making the report…"), ~3 s where the lines are drawn
  natively. Tests: `tests/test_kpi_bulk.py` (15); suite 266 passed.

## 2026-09-23 - Draw Data: Bulk Draw beside Single Draw

User: a list of Site ID + Sector (their Book4.xlsx, 133 rows), a chart for each,
with the EP tracker deciding the cells. Draw Data now has two modes at the top —
**Single Draw** (the page exactly as it was) and **Bulk Draw** (`app/_kpi_bulk.py`).
Bulk Draw adds no drawing of its own: same panels (`panels_for`), same chart
(`_charts.kpi_figure`), same workbook (`write_pivot_workbook`).

- **The list**: an Excel (or CSV) of Site ID + Sector only, columns found by
  name, sites upper-cased, rows de-duplicated, previewed in step 1. No cell IDs
  are asked for.
- **Site → Sector → Cells** comes from the EP tracker (`load_ep_all`, LTE /
  UMTS / GSM by the technology drawn). The export's own sector name is wrong
  for re-homed cells — BAS3114's `L_NewPort_BAS3114-6` is EP sector 2, the
  export calls it S6 — so the tracker wins; a cell the tracker does not list
  keeps the export's own sector (2,032 of 13,151 objects, mostly new L2600
  carriers) instead of vanishing. Where nothing in the export belongs to a
  sector at all (3G: the object is the NodeB), the site's object answers for
  the sector asked.
- **Per Site** ignores the Sector column: every object of the site, one chart
  per site. **Per Sector** uses Site + Sector: the sector's cells, one chart per
  row. A row that finds nothing is listed ("not drawn"), never dropped quietly.
- **Output options**: Combined Chart (the drawn cells, each once), Export KPI
  Data to Excel (the Draw Data workbook over exactly those cells, into
  Downloads), Export Report, Add Site/Cell Information (cells + bands under
  each chart), Auto Open Charts.
- **Report**: a PowerPoint — see 2026-09-24 below.
- **Results**: Individual Charts / Combined Chart tabs, a site search, 12 to a
  page, two charts a row (a cell legend needs the width).
- Real data: 78 rows → 68 site charts / 77 sector charts in ~3 s after the
  export is read, 287-cell workbook, 5 MB report.
- Tests: `tests/test_kpi_bulk.py` (8); suite 259 passed.

### 2026-09-23 (cont.) - Bulk Draw: site-level KPIs, and the technology switch

The user hit `ValueError: No objects to concatenate`: a run drawn on 4G was
re-rendered against whatever technology the bar showed afterwards, so its KPI
was looked for in the 3G files. A drawn run now keeps the files of its own
technology, and an empty frame list says which KPI is missing instead of
reaching `pd.concat([])`.

They also asked for KPIs that only exist per site (3G flow control among them).
`_kpi_bulk.plan_for` decides each chart's level from the data itself:
- cells that actually hold the KPI → the cells are the chart's lines (as before);
- only the site's own object holds it → one series named the Site ID
  (`panels_for(level="Site")`), the chart title stays the Site ID, and the
  information line reads "site-level KPI · <object>";
- Per Sector never repeats a site line under a sector: those rows are listed as
  "<KPI> is a site-level KPI in this export — it cannot be split by sector
  (draw Per Site)", and when that is every row the page says so at the top.
- A cell with no value for the chosen KPI drops out of its chart; a group with
  nothing left is listed, not drawn empty.
- Real 3G export: 1,506 objects, all NodeBs — Flow Control Per Site draws 68
  site charts, Per Sector draws none and says why. Tests: 11; suite 262 passed.

## 2026-09-20 - Dashboard: the executive overview, and Data Resources out of the sidebar

User's design image: one landing page that summarises the app. New page
`app/views/overview.py` (+ `app/_dashboard.py`), first in the sidebar and the
default page (url `home`); the old daily worklist keeps its page and URL under
the name **Daily Worklist**. No new data source: every panel reads what a page
already reads, through Data Resources.

- **Header / strip**: title, "Network Performance | KPI | Tickets | Sites",
  the app's date and project chips, then Last Updated (newest dataset applied),
  the KPI window, how many of the 4 datasets are in the store, and the app's
  own name (Shamsaldin Ali).
- **Cards**: Total Sites (KMZ, on air / planned / off air), Sites with KPI
  Issues (of the judged sites, critical / warning), Total Tickets (history),
  Target Tickets (`R.target().records`), Sleep Tickets.
- **Network Health Overview**: KPI Health ring (`site_status` distribution),
  Site Status ring (KMZ air status), Closed Tickets by Engineer ring (Aws
  Waheeb, Shams Aldin Ali, Mahmoud Dhari Essa — `_dashboard.ENGINEERS`).
- **KPI Issues by Type** (`problem_ranking`, sites breaching, names slanted),
  **Top 10 Sup Districts** (`_kpi_region.affected_by_area`), **Network
  Overview Map** (folium, the Sites map's dark Esri basemap: every KMZ site by
  air status, KPI-issue sites, the day's Target sites; `returned_objects=[]`
  so panning never reruns Python).
- **Tickets by City / by Group** rings, **Reopened Tickets by User** (Reopen
  Count >= 1, the three engineers), **KPI Issues Trend** (each day of the
  window judged as the window is, `_dashboard._by_day`; a partial last day is
  said so), **Tickets Trend** (RF group only, by the month of Create Time),
  **Key Insights** (most affected KPI, most affected Sup District, highest
  ticket city, Shams Aldin Ali's tickets, RF SLA breaches).
- Pictures are the app's own: History of Tickets' bars / donuts / legends
  (`_ticket_history`) and the report preview's line chart (`_svg_charts`).
- **Data Resources**: page, uploads, store and links all untouched; only its
  sidebar entry is hidden (`_ui` CSS on the nav link) and the Sites map's two
  "Manage in Data Resources" links are gone, as asked. It stays reachable at
  /data_resources and from every other page's Data panel.
- Real data (scratch store): 1,711 sites (1,490/188/33), 748 of 1,512 judged
  sites with an issue (218 critical), 19,667 tickets, 83 target tickets, 1,948
  sleep; 3G DL flow-control drops 426 sites; Markaz Al-Basrah 146; Basrah
  12,514 (63.6%); RF SLA breach 113 of 8,000. First open after a restart reads
  the exports (~20 s, the same reads the other pages cache and share).
- Tests: `tests/test_dashboard.py` (7) + the page in `test_app_pages`; suite
  251 passed.

## 2026-09-19 (cont.) - Tickets Details: a new search always shows what it found

User: after one search, a second search kept showing the first ticket (their
screenshot: IM6569852). Not reproducible on the scratch store (Enter, button,
after row clicks, tab switches, fresh session all fine), so the search was made
robust against every cause that could not be ruled out:
- No form any more: the box searches on Enter, on leaving it, or on the
  button (`td_go`); `autocomplete="off"` so the browser's list of earlier
  entries cannot take the Enter without searching.
- The ticket shown is a table pick (`td_open`) only while that ticket is one
  the table holds (the search's, else every one); a leftover pick is dropped,
  so a search always shows a ticket it found.
- Test `test_a_new_search_always_shows_a_ticket_it_found`; suite 243 passed.

## 2026-09-19 (cont.) - History of Tickets: Tickets Details as its tab, Excel-style table

User: Tickets Details must not be its own page but the second tab of History
of Tickets (like KPI Analysis | Report Export); History filters multi-select +
a Time Period; an RF Analysis column chart; Recent Tickets removed; one ticket
table with 24 named columns, **no column letters anywhere**, each column
filtered in its own header like Excel (multi-pick, sort, search, scroll
sideways, Excel export, no separate filter panel); SLA Target Time as the date;
Diagnostic Comment whole with Copy. "Do not redesign / add other features."

- **Tabs**: `views/ticket_history.py` draws a segmented control `th_view`
  (kept across pages in `th_view_kept`); only the open tab is drawn; footer on
  both. `views/ticket_details.py` deleted, its body is `_ticket_details.render(
  df, file)`; Home.py lists only History of Tickets under Complaints.
- **Filters**: `st.multiselect` each (none = all); `th_applied` holds lists and
  `period`. Time Period = a popover button (`th_period_box`, shows the applied
  period) holding a radio All Period / Custom Period (`th_period`) and a date
  range (`th_days`, DD/MM/YYYY); new dates picked while the choice is unchanged
  become the period. Period = Create Time (column N) days, both ends included.
  `H.apply_filters` still takes a single string (older state).
- **Charts**: "Tickets by RF Analysis" = `H.vbars(..., w=680, h=300, tilt=35)`
  (names slanted) in Recent Tickets' place; blanks noted under it (5,928 in the
  real file). Recent Tickets, `H.table`, `TABLE_COLS` gone. Labels without
  letters: "User", "Tickets by User", "Ticket Status: Completed … · Running …",
  card labels "Planned Site ID", "Sector Serving", "RF Analysis", "Closure
  Code", "Latitude", "Longitude", "Create Time", "Diagnostic Comment".
- **The table** = CCv2 component `rf_ticket_table` (`app/_ticket_table.py`,
  `app/assets/ticket_table.js` + `.css`; no package, no CDN). 24 columns in the
  user's order; Status = column B (Running / Completed). Header menu per column:
  sort (A-Z, smallest-largest, oldest-newest), Clear Filter From, Search,
  (Select All) + ticked values - Excel semantics: values among rows the other
  filters keep, a search ticks what it finds, times filtered by day (newest
  first), blanks as (Blanks), 1,000 values listed at once. Sticky header, # and
  HPSM Incident ID pinned, sideways scroll; pages 10/25/50/100; Clear filters;
  Export to Excel built in the browser (inline strings, real datetimes, numbers
  numeric, Draw Data header look, frozen + autofilter header; deflate via
  CompressionStream) of every filtered/sorted row. Row click -> trigger `open`
  -> `td_open` (ticket shown above); comment cell -> the whole comment in a
  window over the table, with Copy.
- **Transfer**: the rows (dictionary-encoded columns, newest first) go to the
  browser once per file + session (`td_table_sent`, JS module cache by sha1),
  5.8 MB for the real file; later runs send ~1 KB (search = row positions
  only). If the browser lacks them it sends trigger `need` and they are sent
  again. Filters / sort / page size stay in the browser across tab and page
  switches (reset for a new file).
- Checked on the real file (scratch store, 8606): Jan 5-26 period = 212, March
  Create Time = 1,264, Basrah = 12,514, Amarah + Samawah = 3,999, Reopen sort
  8/7/7, "shams" = 5,302; full export 19,667 rows, 3.3 MB, 0.6 s, every part
  CRC + XML valid; a one-ticket export rebuilt in Python is byte-identical and
  opens in openpyxl with dates/numbers/comment intact.
- Gotchas: CCv2 components register per Streamlit runtime - `_table()`
  declares it again when the running app lacks it (each AppTest starts its
  own). The browser pane blocks a page's fetch to another localhost port. No
  Node here: JS is checked in the browser only.
- Tests: `test_ticket_history.py` (11), `test_ticket_details.py` (8); suite 242
  passed (1 known failure deselected).

## 2026-09-19 (cont.) - Tickets Details (History ticket data)

(Superseded above: now the second tab of History of Tickets, with the
Excel-style table; the filter grid / st.dataframe below are gone.)

User's design image: one history ticket in full + every ticket in a table. There
was no such page (the only "Ticket Details" is the Daily Target view inside
Delay Tickets Analysis, left as it is): new page `app/views/ticket_details.py`
(+ `app/_ticket_details.py`) under Complaints after History of Tickets, reading
`R.history()`. History of Tickets itself is unchanged.

- Reader `rfopt/complaints/history.py` gains the detail fields: by header, else
  the user's letters - Planned Site ID BP ("Site ID(SD Check)"), Closure Code BQ
  ("Closure Code(Incident Diagnostic)"), Diagnostic Comment BS, Create Time BT
  ("CreateTime(incident diagnostic)"), Sector Serving FB, Longitude FC, Latitude
  FD, RF Analysis FE; by header only - Service Ticket ID ("Ticket ID", F), SLA
  Target Time (G, a real datetime), SLA Status, IS CMC, Reopen Count, Affected
  Services, Submit Time ("SubmitTime(incident diagnostic)", D), Expected
  Resolution Time (BM).
- Page: Search Ticket (HPSM Incident ID / Service Ticket ID / Site ID: exact,
  else contains; a site shows its newest ticket); Ticket Information = 18 icon
  cards; Diagnostic Comment (BS) box, pre-wrap, scrolls, Copy button (JS,
  clipboard + execCommand fallback); Planned Site Information only when BP is
  set or the status is Sleep; Time Information (5 cards); All Tickets table =
  st.dataframe (Status / SLA Status as coloured chips, single-row selection
  opens that ticket above), a filter per column (text contains for IDs, value
  pick, day pick for times), Clear filters, pages of 10/25/50/100.
- Real file: 1,881 tickets with BP (1,625 Sleep); Expected Resolution on 2,842.
- Tests: `tests/test_ticket_details.py` (7); suite 238 passed.

## 2026-09-19 (cont.) - History of Tickets (History ticket resource)

User: a "History of Tickets" dashboard (their Huawei-style design image) over
the R5 ticket history (CC Process export, e.g. CC Process_20260917175109.xlsx:
19,667 tickets, 161 columns), uploaded by hand.

- **Resource** `history` ("History ticket", 6th in `store.KINDS`), one file,
  replaced as a whole; `_resources.inspect` validates it, `R.history()` reads the
  active file (cached per content path).
- **Reader** `rfopt/complaints/history.py`: columns by header, else by the
  letters the user named (A Group, B Ticket Status, H City, I Site ID, M User,
  N Created At, Z Sub District, BF HPSM Incident ID, DG Closure Time, ER
  Status) - letters only stand in once >= 3 headers matched. City "Maysan /
  Emarah" -> Amarah (Basrah, Nasiriyah, Samawah); user "hw.shams.aldin.ali" ->
  Shams Aldin Ali. States: Closed = B Completed (= ER Close), Sleep, Pending,
  In Progress = any other Running status (Resolve, Reopen, Reject); they add up
  to the total. Real file: 16,275 / 106 / 1,338 / 1,948.
- **Page** `app/views/ticket_history.py` (+ `app/_ticket_history.py`: counting
  by HPSM ID, SVG bars / donuts, cards, table) under Complaints in the sidebar:
  filters City, Sup District, User (M User), Status, Group in a form (Apply /
  Reset); 5 cards; User hbars (top 7 + Others, View All), Status donut (ER
  statuses + column B line, View All), Group donut (View All); Top 20 Sites
  (site 0 excluded), City vbars, Sup District hbars (top 7 + Others) with
  Count / Percentage; Recent Tickets (5 latest, View All = every filtered
  ticket); footer "Last Updated" = the export time in the file name. Blank
  user / Sup District are left out of those rankings and noted under them.
- Tests: `tests/test_ticket_history.py` (8); suite 231 passed.

## 2026-09-19 (cont.) - Report Export: Draw Data Excel, spikes, hourly charts

User's exact list (nothing else changed):

- **Excel = the Draw Data export itself**: `kpi_report.build_xlsx` calls
  `kpi_pivot.write_pivot_workbook` unchanged (banner, Row Labels | duplex/RNC |
  one column per hour, heat map, flags), on `KpiReport.excel_rows()` = the
  affected cells' hourly rows. No tickets sheet any more. Checked on the real
  3G export: 260 rows vs Draw Data's 1,495, each row identical, same formats.
- **Sudden spikes** (`rfopt.kpi.anomaly.sudden_spikes`): per cell/NodeB, an
  hour > 6 robust sigmas (MAD, flat floor 1 % of level, >= 6 hours) from its
  own median in the bad direction AND reaching the KPI's warning level (or the
  user's threshold if milder). Affected = beyond threshold OR spike. Real flow
  control (500k/24 h): 145 beyond + 115 spike-only = 260 NodeBs (0 -> 100k).
- Slide 2 hourly always (a per-24 h threshold is not drawn on it); slide 3 no
  City panel; slide 4 = one hourly line chart of the 10 worst sites
  (`site_lines` = `trends.series_for` at Site level, Draw Data colours, SVG
  `time_lines` in the preview, native LINE chart in the .pptx); slide 6 titled
  "Received Tickets", columns Ticket ID, Site ID, City (Target's), SLA Target
  Time (Target's own field; the old "SLA" was the SLA Status), Create Time,
  Problem Time, Is CMC, KPI Value (the site at the problem hour).
- The KPI Analysis tab's own judgement is unchanged (spikes are the report's).
- Tests: `tests/test_kpi_report.py` rewritten (16); suite 223 passed.

## 2026-09-19 - KPI Data holds many files

User: "why can I only upload two [KPI files] ... sometimes I want many files
related to KPI". KPI Data kept one file per technology, so a third file (another
4G query) pushed an active one out.

- **Store** (`rfopt/resources/store.py`): KPI Data (`MANY`) holds any number of
  files. An upload is added beside the active files; it replaces one only when
  it is a **newer copy** of it (`same_export`: same technology, every KPI of
  the old file, >= 90 % of its cells (`cell_cover`, from a 64-hash
  `object_sketch` in the summary + object counts), >= half of its hours again,
  and it ends no earlier). Anything else (another KPI set, area, period, or a
  file that would lose a KPI / cells) stays. No summary -> never removed.
  `Resource.drop` = active files Apply deletes; `set_drop` (the user's Keep
  box), `unstage` (take a new file back out). The same file again is refused
  ("already active"). `_resources.stage_files` first completes older active
  summaries (`kpi_set`, `object_sketch`) so files stored before this are judged
  on what they hold. Registry swaps retry for 2 s on a Windows "Access is
  denied" (`_swap`; virus scanner / reader holding the file).
- **Page**: KPI card "Add / Replace" (3 files + "N more"); the review lists the
  new files (Remove each) and every active file with **Keep** (a newer copy
  comes in unticked, under "Replaced — deleted on Apply"), plus "After Apply:
  N files active — x new, y kept, z deleted".
- **Pages read a technology's files as one export**: `merge_hourly` (rfopt
  ingest) = one row per object and hour, the most recent file's value where two
  files share an hour (`R.kpi_groups` orders each technology by data end).
  KPI Analysis / Draw Data / Report Export via `_kpi_workspace.Combined`
  (`combine`, `raw`, `index_of`, `src_key`, `sniff`); Complaints
  `_load_tracks` per technology; Sites one KPI list per technology
  (`_kpi_group_id`, `_sector_kpis` over the files that carry the KPI);
  Dashboard `_shared.load_kpi_files` (every 4G file; a file without worklist
  KPIs adds only notes).
- Checked on a scratch copy of the real store: the real 4G export split in two
  KPI halves merges back identical (2.5 s for 750k rows); health of the real 4G
  file + a newer copy + a 7-KPI split + a small extra query = the single file's
  (28,852 object/KPI rows, same values and states). Browser: upload through the
  field, Keep / Remove, Apply -> 5 KPI files, restart keeps them; KPI Analysis,
  Draw Data, Report Export, Complaints, Sites, Dashboard all ran on them.
- Tests: `tests/test_kpi_many_files.py` (15) + a swap-retry test; suite 214 passed,
  1 skipped (+ the known Desktop-audit failure).

## 2026-09-18 (cont.) - KPI Analysis: two tabs, one placement, Report Export

- **One placement for every page** (`_kpi_region.site_regions`): Governorate
  (admin1: Basrah, Dhi Qar, Maysan, Muthanna) → City (admin2 district: Basrah,
  Zubair, Qurna …) → Sup District (admin3, as before). "City" used to mean the
  governorate; it is now the district. KPI Analysis, Report Export and
  Complaints (Governorate / Sup District / City columns) all use it.
- **KPI Analysis page = two tabs** (`ka_view`): KPI Analysis and Report Export.
  Filters: Technology (bar), KPI, Governorate, Sup District (multi), Site, Time
  Period (re-judges the hours); City and Prefix are not filters. Governorate /
  Sup District / City ranked bars + tables + map (several picked Sup Districts
  outlined). Table search covers every column.
- **Report Export** (`app/_kpi_report.py`, `rfopt/reports/kpi_report.py`): own
  filters + user threshold; the KPI is judged by `_kpi_health.object_values`
  per cell with the threshold swapped into the KPI's own rule (direction kept).
  One `KpiReport` feeds the 6-slide preview (HTML + SVG charts), the .pptx
  (python-pptx, native charts, `assets/report/report_cover.jpg` = the Basrah
  bridge, Huawei logo from the user's Huawei-master deck, no slogans) and the
  2-sheet .xlsx (all cells, exceeded highlighted; tickets). Saved to the Desktop
  (registry Desktop folder; `RFOPT_DESKTOP` overrides — the tests set it).
- **Timelines name one KPI**: Complaints ticket worksheet and KPI details pick
  the KPI (lead / picked row first); Before / Problem Window / After, Peak Time,
  KPI Value, Threshold, Status. `noc.site_timeline(kpi=...)`.
- Complaints search also matches the Complaint (HPSM) ID.
- Known: `test_real_complaint_pipeline_runs` fails on the new
  `Desktop\Audit\CC Process_20260917175109.xlsx` (also on the code before this).

## 2026-09-18 - Data Resources: one store for every dataset

User: "centralize all application data in ONE place so I do not have to upload
the same files repeatedly in different pages" — five resources: Site Details
Data (EP), KPI Data, KMZ Data, Complaint Data, Coverage Data.

- **Store** `rfopt/resources/store.py`, under `~/.rfopt_cache/resources/`:
  files by content (`files/<sha1>/<name>`) + `registry.json` (schema 2; atomic
  write, read back, verified copy in `registry.prev.json`). **One active
  dataset per resource — no versions, no history** (user, later on 18 Sep:
  "completely remove the Version/History management"). An upload is a
  *pending* replacement: validated + previewed, active data untouched; Apply
  makes it active and deletes the files it replaced; Cancel drops it. KPI Data
  keeps one file per technology (many files since 2026-09-19), Complaint Data one per role (Daily Target /
  CC history); EP, KMZ, Coverage are replaced whole. Delete = explicit, with a
  confirmation. A schema-1 (versioned) registry is converted on read: Current
  files stay, Previous / Archived are deleted.
- **Page** `app/views/data_resources.py` (sidebar: Data -> Data Resources):
  resource cards (Replace / View), Replace panel Select File -> Validate ->
  Preview (new file, what it replaces, what stays) -> Cancel / Apply, View
  (+ preview, download), **Active Data** table (File Name, Data Type, Upload
  Date, File Size, Status, Actions: View / Replace / Download / Delete),
  Auto-Saved status from `store.health()`.
- **Every page reads `app/_resources.py`**; no page has an upload field any
  more (Sites, KPI Analysis, Draw Data, Complaints, Dashboard). This replaces
  the Sites `sm_uploaded_*` files, the Complaints `complaints/daily_target.json`
  store (`target_store` is now an adapter) and the in-memory KPI / coverage
  lists (`sm_kpi_kept`, `kpi_kept`, `sm_cov_kept`) — KPI and coverage data are
  now kept on disk, by the user's request.
- **Manual upload only — the app never searches the computer** (user rule):
  no Desktop / Downloads / ReceiveFiles lookup anywhere (`_shared.find_file`
  and the "Found on this PC" list are gone; a test fails on any `glob` /
  `walk` / `listdir` outside the app's own storage). The only move-in is the
  one-time import of uploads the app itself kept in `RFOPT_CACHE_DIR`
  (`sm_uploaded_*`, `complaints/daily_target.json`).
- Coverage Data = DL Coverage Insight grids (read) + LAT / log files (kept,
  classified by name; no page reads them yet).
- Tests: `tests/conftest.py` gives every test its own empty store; the
  `put_resource` fixture applies a file. `tests/test_data_resources.py`.

## 2026-09-11 (cont.) - KPI Analysis, the third page

User: "add a new section below Site Map ... name it KPI Analysis ... upload the
4G KPI file and the 3G KPI file ... generate a new Excel report ... same
approach and structure we previously created in the R5 KPI Analysis chat ...
from an RF Optimization Engineer perspective, not just an Excel conversion."

That chat is not in the local session history, but its output is: the team's
own pivot workbooks in `~/Downloads` (`4G KPIs Hourly_6.xlsx`, `3G KPIs
Hourly.xlsx`). **The structure to keep = one sheet per KPI, a row per cell /
NodeB, a column per hour** ("Average of <KPI>", `Row Labels`, 4G carries a
`Cell FDD TDD Indication` column, 3G an `RNC` one). The 4G workbook's five
sheets are UL Inter / Data_Volume_GB / LTE_Availability / DL_PRB_Utilization /
DL_Throughput - that is `DEFAULT_PIVOTS_4G`, in that order, on purpose.

**The files** (both raw NPM query results, 6 banner lines then the header):
- 4G `4G Monitoring Hourly KPI's-...zip` - 71 columns, per cell per hour. The
  existing `load_hourly_kpi` already reads it: 416k rows, 12,259 cells, 1,439
  sites, R5-only as exported.
- 3G `SHAMS-3G_Query_Result_...zip` - **per NodeB**, only 3 KPIs: availability,
  `VS.IPPM.Rtt.Means(ms)`, `VS.RscGroup.FlowCtrol.DL.DropNum`. New
  `load_hourly_kpi_3g` + two UMTS KPI defs (`ipmm_rtt_ms`,
  `dl_flowctrl_drops`). It refuses a pivot .xlsx - it needs the raw export.

**Thresholds are set from the data, not from habit**: R5's median NodeB sits at
0.33 ms RTT and 50 flow-control drops a day (P75 = 21k, P90 = 143k), so the
YAML says 10/20 ms and 50k/500k. The first guess (30/60 ms, 500/5000 drops)
raised 2,609 findings, nearly all noise.

**`rfopt/kpi/hourly_report.py`** does the reading: `analyse_4g` / `analyse_3g`
-> roll-ups, busy hour, network hourly profile, findings; `build_kpi_workbook`
-> ~22 sheets. Findings come from the **existing** `diagnose_frame` + the same
YAML the Dashboard uses, so a cell called out here reads the same there.
- **Busy-hour capacity check** (the real gain): a cell at 95% PRB for five
  evening hours and 30% otherwise averages 46% and slips past every daily
  threshold. Capacity KPIs are judged a second time on their p95 hour, which
  is what surfaces SAM1290-2 (99.6% at 20:00, 1 TB/day).
- Criticals -> Action List, warnings -> Watchlist. On WK37: 1,617 cells to
  action, 3,694 on the watchlist, 283 NodeBs (the NAS Iub congestion on 09-09
  is the headline - 4.3M flow-control drops on NAS5753).
- P1 = critical **and** above this report's own median cell traffic, so the
  split still works whether the file holds 6 hours or a week.

**`xlsxwriter` is a new dependency.** The hourly sheets are ~400k cells each;
openpyxl wrote the workbook in 240s / 28 MB, xlsxwriter in 46s / 15 MB. Only
five 4G pivots are written by default for the same reason (the page's
multiselect exposes the other six).

Timings on the real files: load ~0.2s warm (parquet cache), analysis ~22s,
workbook ~80s. 86 tests pass.

## 2026-09-11 (cont.) - Site Map: the EP tracker as a second data source

User: "add field in map sidebar to upload the EP details by upload the EP
excel file."

- Sidebar is now **two** file fields, both built by `_file_source()` in
  `app/views/site_map.py` (one helper, same persistence rules): the KMZ, and
  the weekly **Engineering Parameter tracker** (.xlsx). An upload is copied to
  `~/.rfopt_cache/sm_uploaded_EP_tracker.xlsx` so it is still loaded next time
  the tool opens; order is upload -> session path -> last upload on disk ->
  auto-found (`*Engineering Parameter*.xlsx` in ReceiveFiles/Desktop/Downloads).
- Read **lazily** (`_load_ep_path`, `cache_resource`) - only when a sector is
  opened, so the map never waits on a 30 MB workbook. All three sheets plus
  their **Deactive** twins: a sector the KMZ still calls On Air while the
  tracker has it deactivated should be visible, not missing (BAS0236 is one).
- Click a sector -> **EP details** table under the cell table: EARFCN, BW, PCI,
  mod3, RSI, RS power, M/E/total tilt, max RET, antenna, status. Matched on
  `sector_id`, falling back to cell name. Blanks print "—": Streamlit renders a
  numeric NaN as "None", so the columns are formatted to text first.

**Two `rfopt/ingest/cellparams.py` bugs this exposed** (both pre-existing, both
also affecting the Dashboard's UMTS/GSM options, which simply never worked):

1. `df.get("bandwidth", "").astype(str)` - the `""` default is a str, not a
   Series, so any sheet missing a column raised `AttributeError`. GSM and UMTS
   carry far fewer columns than LTE, so **neither sheet could be read at all**.
   New `_txt(df, col)` returns an empty Series instead; used in all 4 spots.
2. GSM sectors came out **S43**: that sheet's Sector column reads "BAS0043-S3"
   and the code grabbed the first digits it saw - the site code. New
   `_sector_no()` reads a trailing "S<n>" or "-<n>", never the site code. This
   is also what lines GSM cells up with the KMZ (cell -7 *is* sector 3).

`load_cell_params`'s parquet cache key now carries `_PARSE_V` - **bump it when
`_parse_params` changes**, or `~/.rfopt_cache` keeps serving the old parser's
rows (this cost a debugging round: the fix looked like it had done nothing).

WK37 = 26k R5 cells across the three sheets; 91.6% of the KMZ's 4,900 sectors
match. Verified live: BAS0043-S3 shows all six of its cells, 2G/3G/4G, matching
the KMZ cell table above it. 79 tests pass.

## 2026-09-11 (cont.) - Site Map: a new drawing vanished after the reload

User: "directly the line disappears after the map loading" (ruler, but the
same bug hits circle/marker too).

**Cause**: `all_drawings` is in `returned_objects`, so a new drawing reruns
Python and st_folium remounts the iframe. But that rerun's `fmap` is built
*before* `out` (this run's `all_drawings`) is known - it only reads `saved`
(the OLD `session_state["sm_draw"]`), so the fresh map never has the
just-drawn shape. Only *after* `st_folium()` returns does the code learn
about it and update session_state - with nothing forcing another rerun, the
next map build (whenever that happens) is the first one that would actually
include it, so meanwhile the shape flashes once and is gone.

**Fix**: `st.rerun()` right after `st.session_state["sm_draw"] = _drawings`.
Costs one extra remount per new shape, but the very next build reads the
now-current `saved` and bakes the shape in from the start. Verified live
(synthetic Leaflet events + waiting out both reruns): the ruler line was
still on the map afterwards. 78 tests pass.

## 2026-09-11 (cont.) - Site Map: professional toolbar + a real ruler

User: "redesign the map tool ... professional ... the rule when I used them
add azimuth as well ... show to me the distance and azimuth."

- **`_Ruler`** (new `MacroElement`, same file) replaces Leaflet's stock
  `MeasureControl` (distance-only). Click a start point; a dashed preview
  line follows the cursor with a floating `distance · azimuth°` tag; click
  again to finish. Finished line -> `map.fire(L.Draw.Event.CREATED, {layer,
  layerType:'polyline'})` with `layer.feature.properties = {distance_m,
  bearing_deg}` — rides Leaflet-Draw's own pipeline into `all_drawings`,
  so it reaches Python via the same bridge as the sector-dot fix, and is
  deletable with the existing 🗑 tool. No new Python-side plumbing needed.
- Dropped `Draw`'s `polyline` option (the ruler replaces it) — that was the
  source of the stock grey "Finish / Delete last point / Cancel" bar in the
  user's screenshot (only appears for polyline/polygon's multi-vertex flow).
  Toolbar is now ruler / circle / marker / delete, one `.leaflet-bar` column.
- Read-out + persisted-redraw tooltip for lines now say "ruler" and include
  azimuth: `"ruler — 1.46 km (1,465 m) · azimuth 41°"`.
- Softened `_MAP_CSS`: 10px radius + lighter border + softer shadow on the
  search boxes / Display-settings panel / full-screen button, so the floating
  UI reads as one system instead of mismatched stock controls.
- Verified live (synthetic Leaflet events): ruler live tag showed
  `"1.46 km 41°"` while dragging, matching read-out after finishing; sector
  click and circle/marker draw unaffected. 78 tests pass.

## 2026-09-11 - Site Map: sector-click was dead; uploaded KMZ now persists

User: "why the tool not working" (sector click did nothing, no detail panel).

- **Root cause**: `_SectorDots`'s click handler wrote the sector id into
  `window.__GLOBAL_DATA__` but never told streamlit-folium's frontend about
  it — a Leaflet `circleMarker` click does not bubble up to the map's own
  `click` handler on its own, so st_folium never shipped the value to Python.
  Fixed by having the handler `m.fire('click', {latlng, ...})` after updating
  the global — that's the event st_folium actually listens for. Verified live
  (`target.fire('click')` in the browser -> detail panel opens).
- **Uploaded KMZ now persists across restarts.** User: "I need when I upload
  the KMZ file, keep this map till I close the tool and open it again." The
  upload used to land in the OS temp dir keyed only by `st.session_state`,
  which is empty on a fresh process. It's now copied to
  `~/.rfopt_cache/sm_uploaded_R5_Sites.kmz` (+ a `.name.txt` sidecar for the
  caption) and checked **before** the Downloads/Desktop auto-find, so it's
  still the active map next launch. Sidebar caption: "using your last
  upload: <name>". New **"✕ forget this upload"** button clears it back to
  auto-find. Verified with a from-scratch `AppTest` session (no prior
  `session_state`) that it picks the persisted file up.
- 78 tests pass (+1 skipped, unrelated).

## 2026-09-09 - Site Map: pan / zoom no longer reloads

User: "every move / zoom makes the map load for a few seconds." Cause was
`st_folium` rerunning Python (and re-mounting the iframe) on every `center` /
`zoom` change.

- **`returned_objects` is now just `["last_object_clicked_tooltip",
  "all_drawings"]`** — only a sector-dot click or a new drawing reruns Python.
  Pan / zoom / measure are 100 % client-side now (verified: no iframe re-mount,
  no `stStatusWidget`).
- View is held **client-side** by **`_ViewKeep`** (a MacroElement, sessionStorage
  key `sm_view`): saves on `moveend`/`zoomend`, restores on the next mount. A
  fresh search passes `jump=[lat,lon,zoom]` to override it once. Replaces the
  old `sm_view` session-state round-trip. `_jump` still computed from
  `_new_search and focus`.
- **Sector dots are now SVG + client-side (`_SectorDots`), not canvas.** Leaflet
  canvas hit-testing silently fails with ~10 k paths on the map, so real clicks
  on a canvas dot never reached st_folium. `_SectorDots` draws `L.circleMarker`
  (explicit `L.svg()` renderer) for just the in-view sectors (cap 900); a click
  writes the sector id to `window.__GLOBAL_DATA__.last_object_clicked_tooltip`
  and bubbles to st_folium's map-click handler → Python. Dots are bigger now
  (r 6 at z≥15, white ring). `_ZoomTune` **deleted** (was only for the old
  canvas dots; also killed a per-zoom walk over 11 k layers).
- `_SiteLabels` markers got `pointer-events:none` (`.sm-lbl`) so a label can't
  eat a dot click.

## 2026-09-09 - Site Map: site-name labels + search actually jumps

- **Labels show the site NAME, not the ID** — the KMZ `site_name` is already
  `Name_SITEID` (e.g. `NewPort_BAS3114(MM)`), so that string is the label.
- Labels are now a **client-side `_SiteLabels` MacroElement** (not 1,600 Python
  `folium.Marker`s): an `L.layerGroup` that on `moveend`/`zoomend` rebuilds
  divIcons for just the sites in `map.getBounds()`, only at zoom ≥ 13, capped
  300. Lag-free (the old Python in-view filter trailed the view by a render).
  `iconAnchor` puts the name just **above** the tower. Colour follows the
  basemap (`_lbl_fg/_lbl_halo`). Toggle = "Site names" in the Display panel.
- **Search now recentres the map.** streamlit-folium reuses the Leaflet
  instance and ignores a fresh `folium.Map(location=…)`, so a new site /
  complaint search also passes `center=`/`zoom=` **props** to `st_folium`
  (`_jump` flag) which the frontend honours live; `sm_view` is pinned to the
  target that render so it doesn't snap back. Clearing the search leaves the
  map where it is.

## 2026-09-08 (cont.) - Site Map: basemap switcher

`_BASEMAPS` (top of `site_map.py`) + a **Basemap** radio at the top of the
Display panel — **Streets** (OSM) / **Satellite** (Esri World Imagery + Esri
World_Transportation & World_Boundaries_and_Places reference overlays for roads
+ place names) / **Dark** (Esri Canvas/World_Dark_Gray_Base + …_Reference).
All key-free Esri/OSM endpoints — **CartoDB now needs an API key**, so the old
`"CartoDB dark_matter"` string is out. `max_zoom` per basemap (Dark caps at 16).
Label text + tower-dot colour adapt: `_lbl_fg/_lbl_halo` (light-on-dark for
Dark), `_dot_c` (grey / **yellow on Satellite** / grey). Beam wedges unchanged
(blue reads fine on imagery). Map re-mounts on switch; `sm_view` keeps position.

## 2026-09-08 (cont.) - Site Map full-screen keeps the panels

The Leaflet `Fullscreen` plugin was **dropped**; full-screen is now a Streamlit
"pseudo" full-screen so the search + Display panels come with it:

- **⛶ button** = a real `st.button` in `st.container(key="sm_fsbtn")`, floated
  top-right over the Leaflet tool strip (which is pushed down 42 px by a
  `<style>` injected into the folium `<head>`: `.leaflet-top.leaflet-right
  {margin-top:42px}`). Click → toggles `st.session_state["sm_fs"]` + `st.rerun`.
- While `sm_fs`: `_FS_CSS` pins `.st-key-sm_mapwrap` `position:fixed; inset:0;
  z-index:2147483000` and `display:none`s the sidebar / header / toolbar; the
  overlays (absolute) ride along; `st_folium(height=_FS_MAP_H=940)` instead of
  640.
- **The catch & fix:** streamlit-folium hard-codes `#map_div` height at mount
  and never re-measures, so after the wrapper grows Leaflet keeps its old tile
  grid. The st_folium component iframe is **same-origin** and exposes
  `window.map`, so a `components.html(height=0)` script (rendered only when
  `sm_fs`) reaches `window.parent…iframe.contentWindow.map` and fires
  `invalidateSize()` + `setView(c,z,{reset:true})` on a few timeouts
  (200-3000 ms) → tiles re-lay to fill the viewport.
- `_FS_MAP_H` is a fixed 940 (can't read viewport height in Python without a
  dep); good for ~1080p, clips a bit on shorter screens, letterboxes (grey
  `#e9e9e9`) on taller ones. Overlays stay top-anchored so they're always on
  the map.
- New import: `import streamlit.components.v1 as components`.

## 2026-09-08 (cont.) - Site Map controls moved **onto the map**

User asked for everything on the map itself (`app/views/site_map.py`):

- **Map tools → top-right.** `MeasureControl` / `Draw` are `position="topright"`,
  stacked under the ⛶ button. The folium `LayerControl` and `Fullscreen` plugins
  were both **removed** — layers are Python-side checkboxes in the Display
  panel, full-screen is the pseudo one (section above).
- **⚙ Display settings** = an `st.expander` floating over the **top-left** of the
  map (`st.container(key="sm_disp")` + `_MAP_CSS`, `position:absolute`). Click to
  open → **Topology** (All/4G/3G/2G) · **Layers** (Sector beams / Sector dots /
  Site-ID labels / Distance lines / Today's worklist pins) · **Sizing** (Beam
  length m, Distance lines — nearby sites, Only-today's-worklist toggle).
  Unchecking *Sector beams* / *Sector dots* skips building those features
  (real perf win, not just `show=False`).
- **Search moved onto the map** too: the site-ID + complaint-lat/lon boxes are a
  `st.container(key="sm_search")` floated top-left, just above the Display panel.
- The **metrics row moved below the map**.
- **Layout mechanic:** `.st-key-sm_mapwrap` is `position:relative` with
  `gap:0 !important` (Streamlit's flex `gap` between the stacked child containers
  otherwise pushes the map down ~32 px and the overlays float in the void). The
  two overlay containers are `position:absolute; z-index:1000/1001`.
- Full-screen now carries the panels too — see the newer section above. 64 tests
  still green.

## 2026-09-08 (cont.) - Site Map is now a **folium / Leaflet** map

Replaced plotly with **folium + streamlit-folium** (`app/views/site_map.py`)
because plotly-on-Streamlit cannot do interactive map drawing. New deps in
`requirements.txt` / `pyproject.toml`: `folium>=0.17`, `streamlit-folium>=0.20`.

- All 4,900 sector wedges are a single `folium.GeoJson` FeatureCollection
  (`prefer_canvas=True` -> 60 fps); the clickable handles are another GeoJson
  of points with a `GeoJsonTooltip` = `"<sector_id> · <site> S<n> · az … · RET …"`.
- **In-map tools (work in full-screen)**: `Fullscreen`, `MeasureControl`
  (Google-Earth ruler), `Draw` (polyline / circle / marker), `MousePosition`
  (live cursor lat/lon, bottom-left), `LayerControl` (top-right: Sector beams /
  Sectors / Site-ID labels / Distance lines).
- `st_folium(..., returned_objects=["last_object_clicked_tooltip",
  "all_drawings", "center", "zoom"])`. A sector-dot click -> the tooltip's first
  ` · `-token is the sector_id -> `sm_sel_sector` -> the detail table.
  `all_drawings` -> a readout line per shape (line km + bearing / circle radius /
  pin lat-lon), persisted in `st.session_state["sm_draw"]` and re-drawn each run
  (Leaflet.draw's own layer is wiped by the st_folium reload). **Clear
  drawings** button.
- View is kept across the st_folium reload: `out["center"]`/`out["zoom"]` ->
  `st.session_state["sm_view"]` -> next map's `location`/`zoom_start`; a fresh
  search overrides it.
- 64 tests. `MeasureControl`/`Draw` verified live (`LayerControl` since removed —
  see the newer section above); drawn-circle round-trip verified
  ("⭕ circle — r = 350 m @ 30.51288, 47.81486").
- **Zoom-fade of the handle dots** is a `_ZoomTune` **`MacroElement`** (not an
  inline `folium.Element`): it walks the layer tree *recursively* (map ->
  FeatureGroup -> GeoJson -> CircleMarkers) and calls `l.setRadius(r)` — the
  old inline hook used a non-recursive `m.eachLayer` + `setStyle({radius})`,
  which `L.CircleMarker` ignores, so the fade never fired. First pass runs off
  `map.whenReady()`. `r` = 4 / 3 / 2 / 0 px at zoom ≥15 / ≥13 / ≥11 / below.

## 2026-09-08 (cont.) - Site Map was KMZ-driven with plotly (superseded above)

`rfopt/ingest/kmz_sites.py` parses the user's Google-Earth **`R5_Sites.kmz`**
(`~/Downloads`, 1.6 MB) -> `load_kmz_sites()` -> `.cells` (30,259 R5 cells: one
row per 2G/3G/4G cell with azimuth, band, PCI/EARFCN/BW/TAC/RS-Power/MAX-RET
for 4G; Tilt/CPICH/SAC/PSC/UARFCN for 3G; BCCH/BSIC/TCH/E-tilt/antenna/BSC/LAC
for 2G), `.sectors` (4,900), `.sites` (1,635). Balloon HTML is regex-split per
`<Placemark>`, `#onair/#planned/#offair` only; site lat/lon = first Polygon
coord. Parses in ~2.5 s, parquet-cached (cells+sectors stacked, `_rt` col).
RS Power `182 -> 18.2 dBm`; MAX RET / electrical tilt `50 -> 5.0 deg`.

`app/views/site_map.py` rewritten:
- **Data source = the KMZ only** (auto-found in `~/Downloads`/Desktop, or
  sidebar upload). No parameter-tracker fallback - the sidebar has just
  Topology / Site-data-KMZ / Display (user asked to drop the LTE-tracker
  field, 2026-09-08).
- **Every** site + sector beam is drawn always (one `fill="toself"` Scattermap
  trace per air-status, `None`-separated wedges) - not just on search.
- Sidebar **Topology** radio All/4G/3G/2G - filters which sectors draw AND
  which cell table opens on click.
- Click a **sector handle** (dot at the beam centroid, `customdata=sector_id`,
  `on_select="rerun"`) -> **one simple table**: cell name, band, azimuth,
  height, RET, status (respects the Topology filter). `uirevision` unchanged
  on click so the map does not recenter.
- **RET column**: `cells.ret_deg` = the cell's own tilt (4G MAX RET / 3G-2G
  electrical tilt) else the sector's RET-actual readout (`ret_deg` on sectors,
  from the "RET Actual Tilt" balloon section) else the median sibling tilt.
  ~95% of cells now carry a RET value (was ~86%). `cells.band_label` is a
  short tag (L1800/L2100/L2600(TDD)/U900/U2100/G900/G1800) from the cell-name
  prefix + EARFCN.
- Beam wedges are ~130 m by default with a sidebar **Beam length (m)** slider
  (40-400); a steeper tilt shortens the wedge a little. `_SCHEMA_SIG` (sha1 of
  the frame columns) in the KMZ parquet cache key -> a column change
  auto-invalidates old caches.
- **A search never hides towers** (2026-09-08): `draw` = every sector always
  (only `only_worklist` filters it); a site / lat-lon search only sets `focus`
  = map centre + zoom (`uirevision`). Site labels near the focus point only
  (<=4 km) so 1600 labels don't all render.
- **Distance lines**: on-map toggle `show_lines` + sidebar slider `n_lines`
  (1-12, default 6). A complaint lat/lon search draws one grey line per nearest
  site (pin->site, `None`-separated single trace) with a midpoint **distance-only**
  label; the best-pointed site's line is green.
- **On-map control strip** (2026-09-08): the 3 toggles (Sector beams / Site-ID
  labels / Distance lines) moved out of the sidebar into a transparent
  `st.container(key="sm_mapctrl", horizontal=True)` floated over the plot via
  `_OVERLAY_CSS` (`.st-key-sm_mapctrl`, negative `margin-bottom`). Sidebar keeps
  the two sliders + the worklist toggle.
- **Map tools** expander above the plot: **Ruler** (`_hav`/`_brg` -> km + bearing,
  magenta line + label), **Circle** (`_circle()` polygon + `r = N m` label),
  **Placemarks** (`st.session_state["sm_pm"]` list, Add / Clear). A tool also
  re-centres the map when no search is active. `scattermap.Line` has no `dash`
  -> the ruler is `lines+markers`, solid.
- Kept: site-ID search, complaint lat/lon search + nearest-sites table.
- **Worklist KMZ export** (shown when a Dashboard run is in session) now builds
  site locations from the KMZ (`_kmz_site_db(SECT)`), not the param tracker.
  The generic "all R5 sites" KMZ button was dropped (redundant with the user's
  own `R5_Sites.kmz`).
- 62 tests (+7 `test_kmz_sites.py`).

## The daily flow (this is the product now)
User drops, each morning:
- `~/Desktop/Target <date>.xlsx` - the day's ticket worklist (site id + city,
  no coords). ~115-120 tickets, mostly Basrah "Data Service".
- `~/Desktop/4G Monitoring Hourly KPI's-Upd..._Query_Result_<ts>.zip` - a full
  24 h hourly 4G KPI export (long CSV inside; ~72 cols; ~300k rows/day;
  per eNodeB-sector-carrier). Operator = Asiacell.
- (`WK37 Engineering Parameter Tracker` on `D:\WeLink_data_files\...
  \ReceiveFiles` - the cell param DB; refreshed weekly.)

Run: app **Dashboard** page (auto-finds the files) or
`rfopt.complaints.run_worklist(target, params_df, out, kpi=kpi_df,
history_counts=...)`. Output `out/Target_<date>_RF_analysis.xlsx`:
Worklist (P1-P4 + likely_cause + kpi_evidence + next_check), Summary
(priority x cause + top sites), Site flags, Sector params.

### UI (2026-09-08 restructure - user request)
`app/Home.py` = `st.navigation` entry point, **only 2 pages**: **Dashboard**
(`app/views/dashboard.py` - the worklist + Map tab + Export tab) and
**Site Map** (`app/views/site_map.py` - R5 map, sector beams, worklist overlay,
KMZ export). The 6 KPI-engine pages moved to `app/_advanced/` (not registered;
re-enable via the commented block in Home.py). Shared loaders +
file-finder in `app/_shared.py`. `.streamlit/config.toml` disables the file
watcher (daily tool).

KMZ: `build_site_kmz` writes a **"Site IDs" folder** with a `LabelStyle` so
every site ID shows at its lat/lon in Google Earth; `build_worklist_kmz`
pins each worklist site (labelled by ID, coloured + foldered P1-P4).

Perf: **~18 s cold, ~3 s warm** (calamine .xlsx engine + parquet cache in
`~/.rfopt_cache`, keyed on file mtime; safe to delete).

Latest: Target 8-Sep, 116 tickets, 09-07 full-day KPI -> P1 63 / P2 25.
Dominant causes **interference 45, congestion 39** - each with per-sector
evidence ("S2 DL PRB 97%", "S4 UL RSSI -84 dBm, ~34 dB above floor").
R5 is capacity + UL-interference constrained.


## 2026-09-08 - cell parameters + daily worklist (DONE)

Received two files (paths granted this session):
- `D:\WeLink_data_files\swx1351646\ReceiveFiles\WK37 Engineering Parameter
  Tracker-06092026.xlsx` - the **cell-details file**. Sheets GSM/UMTS/LTE
  (+*Deactive*) + RET. LTE sheet = 86k rows; **12,752 R5 LTE cells / 1,473
  sites**, "Region 5". Has AZIMUTH, GROUDHEIGHT, M-DOWNTILT, ELECTRICAL
  DOWNTILT (`[40,40,40,42]` = 0.1 deg branch array -> 4.0-4.2 deg), MAX RET
  (= max of that array, NOT the antenna limit), RS POWER (mixed dB / 0.1 dB /
  0), PCI/MOD3/RSI, FREQUENCY BAND (1=L2100/10MHz, 3=L1800/20MHz, 41=N41/20MHz),
  SUB_DISTRICT (100% filled). ~14% of R5 cells have NO electrical tilt.
  RET sheet is U2100/U900 only - ignore for LTE.
- `C:\Users\swx1351646\Desktop\Target 7-Sep.xlsx` - the **daily worklist**:
  119 tickets, Ticket ID / HPSM ID / City / Site ID / Problem Time / MSISDN /
  Affected Service / Diagnostic Comment. **No coordinates, no sub-district.**
  99 sites, mostly Basrah, mostly "Data Service". 16 rows have Site ID "0".

Built:
- `rfopt/ingest/cellparams.py` - `load_cell_params()` (parses arrays, cleans RS
  power, dedupes, R5 filter) + `params_to_site_db()` (feeds the complaint engine
  real tilt).
- `rfopt/complaints/site_audit.py` - `audit_site()` -> flags: missing_ret,
  tilt_imbalance (>=3 deg, info), over_tilt, ret_at_max, azimuth_gap (>=3
  sectors, >=170 deg), azimuth_overlap, narrow_band_only, single_carrier,
  pci_reuse_site, dup_pci_sector, rs_power_imbalance, isolated_site,
  few_sectors. Each -> severity + note + complaint category.
- `rfopt/complaints/worklist.py` - `load_worklist()`, `process_worklist()`
  (per-ticket: site audit + comment-signal parse + history count -> likely
  cause + `next_check` + P1-P4 priority), `write_worklist_report()` (4-sheet
  xlsx), `run_worklist()`.
- `app/pages/7_Daily_Worklist.py`.
- Tests: `tests/test_cellparams_worklist.py`. **54 passing.**
- Delivered: `out/Target_7-Sep_RF_analysis.xlsx` (28 P1, ~24 sites with
  missing RET + high repeat complaints, e.g. BAS0453 = RET missing x3, 32 past).

### 2026-09-08 - KPI integration (DONE)
- `rfopt/ingest/hourly_kpi.py` - `load_hourly_kpi()` for the Asiacell R5 hourly
  export: the long CSV (`Original_Data_R5_Hourly.csv[.zip]`, 72 cols, ~119k
  rows/day, per eNodeB-sector) **and** the 5-sheet pivot xlsx (`4G KPIs
  Hourly.xlsx`). Maps ~23 operator KPIs to the canonical schema. Per-sector
  (no band). Operator = **Asiacell**.
- `rfopt/complaints/worklist.py::KpiContext` - pre-computes per-site worst-hour
  / daily sector stats + R5 percentiles; `verdict()` -> (status, category,
  evidence, severe). `process_worklist(..., kpi=df)` now sets `kpi_status /
  kpi_verdict / kpi_evidence` and a **measured KPI issue overrides** the
  parameter guess (confidence 0.65). Priority uses `kpi_severe`.
- App page 7 has a KPI uploader (auto-finds `~/Downloads/*R5*Hourly*.zip` etc).
- On Target 7-Sep + the 09-07 KPI (night hours 00-08 only): **74/119 tickets
  get a measured verdict** - congestion 35, interference 32 (each with
  per-sector evidence like "S3 DL PRB 93% (worst hr)"). P1 39 / P2 34.
- **Caveat baked into the output**: the 09-07 export is night-only and a day
  after most tickets, so verdicts use the site's worst hour in the available
  window. A full-day export removes the caveat.
- Tests: `test_cellparams_worklist.py` extended. **59 passing.**

### Remaining / ideas
- No RSRP/RSRQ/SINR/CQI/TA in the hourly export -> coverage still can't be
  *measured*, only inferred. If an MR / coverage KPI export exists, add it.
- `~/Downloads` also has `rf-kpi-tickets.jsx`, `rf-kpi-watch-sites.kml` - an
  earlier tool the user built; not integrated, don't touch unless asked.

---
# Project status / handoff  (paused 2026-09-07)

Working dir: `C:\Users\swx1351646\rf-optimizer`  (moved out of the Claude scratch
workspace — pyarrow won't load from very deep Windows paths).
venv: `.venv\Scripts\python.exe`.  Run tests: `pytest -q`  (45 passing).
Run app: `streamlit run app/Home.py`.

## The user & their data

RF optimisation engineer, responsible for **region R5** = southern Iraq
(Basra / Nasiriyah / Amara / Samawa).  R5 site IDs start with **BAS, NAS, EMA,
SAM** — always filter to these.

Files in `C:\Users\swx1351646\Desktop\Audit\` (granted this session):

| file | what it is |
|---|---|
| `Copy of DB R5.xlsx u.xlsx` | **R5 site database**, Sheet3: `Foldr Name, Name, Longitude, latitude, Direction(azimuth), Sector, Function, Status, Height`. 1,646 sites / 4,933 sectors. **No tilt, no beamwidth.** Sheet1 = pasted KML. Sheet2 = 7-row scratch. |
| `CC Process_20260829095431.xlsx` (+ 3 older) | **customer-complaint tickets**, ~20k rows, sheet1. Has per-ticket `Latitude/Longitude` (~9.9k filled), `Sector Serving` (e.g. `BAS0136-2`), `Site Name`, `Cell Name`, `Sub District`, `City`, and the engineer's `RF Analysis` / `Closure Code` / `Root Cause` / `Diagnostic Comment`. |

### Decisions the user gave
1. **Scope** = complaint analysis + KMZ + audit (not bulk KPI monitoring yet).
2. **RET unit = tenths** (value `40` means `4.0°`).
3. A **cell-details file** (all-Iraq) is coming — I must filter to BAS/NAS/EMA/SAM.
   Need columns: cell id/name, sector, **electrical tilt / RET**, mechanical
   tilt, ideally vbw / hbw / band.
4. A **slim complaint sample** is coming with: ticket ID, MSISDN, problem time
   (ISO `2026-09-05T19:04:20.000Z`), create time, site ID, **and only a
   sub-district / area name — NO per-ticket lat/long.**  (problem time = when
   the customer called; create time = when CC opened the ticket.)

## KEY OPEN FINDING (deal with this next)

Sub-district names geocode to only **±5–12 km** (see
`config/r5_area_geocode.csv`, built from the historical CC files: 88 areas,
only 46 with spread <6 km & n≥8).  That is **too coarse for per-ticket RF
geometry** (can't say "52° off azimuth" when the point is uncertain by 8 km).

So without per-ticket coordinates the complaint analysis must degrade to:
* **site / sector-level hotspots** — complaint counts per serving site & sector,
  ranked; join site DB for isolation (nearest-neighbour distance), which sector
  dominates, planned sites nearby.
* **sub-district heat map** — coarse centroid is fine for a heat layer, not for
  per-ticket angles.
* per-ticket geometry only when coords ARE present (the CC Process file has
  them — recommended the user keep using that, or add lat/long to the slim
  export).

### Next steps

DONE 2026-09-07:
1. ✅ `loader.py` geocodes the sub-district via `config/r5_area_geocode.csv`
   → `coord_source` = `ticket` | `subdistrict` | `""`; adds `geo_spread_km`.
2. ✅ `analyze.py`: `loc_source="subdistrict"` → coarse buckets only
   (`coverage_or_capacity` / `new_site_needed` at >8 km), NO azimuth/nearest/
   dominance claims, confidence ≤ 0.35, explicit ±km caveat in the finding.
   (Centroids break nearest-site logic — one area is served by 100+ sites.)
3. ✅ `aggregate_by_site()` + `aggregate_by_subdistrict()` in the complaints
   module; app page has "Site hotspots" + "Area hotspots" tabs; complaint KMZ
   now has a "Site hotspots" folder (donut pins scaled/coloured by count).
   47→46 tests? currently **46 passing**.

STILL TO DO:
4. Tell the user: slim file (site + sub-district only) = **site/area hotspot
   analysis only**; per-ticket geometry needs the customer lat/long (the full
   CC Process export has it).
5. When the **cell-details file** lands: add a loader (reuse `ingest/sitedb.py`
   `_MAP`, it already has RET/tilt aliases), join into the site frame so
   `elec_tilt_deg` is real, drop the "assumed RET" caveat in
   `analyze._attach_action`.
6. Regenerate the R5 site KMZ with real tilt once available.
7. (opportunity) User has a separate daily "4G KPIs Hourly" workflow — a skill
   `r5-hourly-kpi-report` now exists; files in `~/Downloads/4G KPIs Hourly*.xlsx`.
   The KPI engine (rfopt/kpi, rfopt/diagnosis, rfopt/pipeline) is already built
   and could plug in. Not started — out of the current "complaint" scope.

## What IS built and working

```
rfopt/
  ingest/schema.py        canonical LTE/UMTS/GSM KPI catalogue
  ingest/mapping.py        Huawei counter-name -> canonical (config/huawei_kpi_mapping.yaml)
  ingest/loader.py         KPI Excel/CSV -> canonical frame
  ingest/sitedb.py         *** site-DB loader + R5 filter (BAS/NAS/EMA/SAM), lat/lon un-swap
  kpi/thresholds.py        config/thresholds_{lte,umts,gsm}.yaml
  kpi/analyze.py           vectorised roll-ups + threshold breaches
  kpi/anomaly.py           robust trend / level-shift / anomaly
  diagnosis/engine.py      rule engine, KPI pattern -> problem class (+ geometry gated on real ISD)
  actions/geometry.py      haversine, bearing, downtilt maths, 3GPP vertical pattern
  actions/propagation.py   COST-231 Hata, RSRP predict, RS-power delta
  actions/recommend.py     *** recommend_for_cell + recommend_for_complaint
                           (boresight-mode tilt; "RET 70->40" example reproduces)
  ai/narrative.py          TemplateNarrator (offline) + ClaudeNarrator (optional)
  geo/kmz.py               *** build_site_kmz + build_complaint_kmz (no deps, raw KML/zip)
  complaints/loader.py     *** flexible complaint-ticket column mapping + R5 filter
  complaints/analyze.py    *** per-ticket geometry triage + categories + engineer-verdict audit
  pipeline.py              run_analysis(...) KPI orchestrator
  cli.py                   python -m rfopt {analyze,complaint,sample}
app/
  Home.py + pages/1..5     KPI app
  pages/6_Complaint_Analysis.py   *** complaint app (site DB + tickets -> categories/audit/KMZ)
config/
  r5_area_geocode.csv      *** NEW: sub-district -> centroid (coarse, ±5-12km)
scripts/generate_sample_data.py   synthetic Huawei demo (R5, 10 sites, 9 injected faults)
tests/                     45 passing (real Audit files used when present, else synthetic)
```

### Complaint engine categories (geometry-first)
`new_site_needed`, `wrong_server`, `off_axis_azimuth`, `far_coverage`,
`near_on_axis_other`, `interference`, `congestion`, `indoor`, `availability`,
`not_rf`, `insufficient_data`.

### Calibration note given to the user
Geometry-only triage (coordinates only, no RSRP/MR/KPI) matched the engineer's
historical calls ~36% (agree+partial).  It is a screening + measurement-
automation tool, not a replacement.  Improves once cell-details + KPI land.

## Deliverables sent to the user this session
* `sample_rf_report.md` — sample KPI optimisation report (synthetic data)
* `R5_sites.kmz` — 1,635 R5 sites, sector beams (884 KB)
* `r5_preview.png` — R5 geography sanity plot

## Gotchas
* Deep-path Windows: keep the project at a short path (pyarrow DLL load).
* plotly ≥6: `go.Scattermap` not `Scattermapbox` (handled in page 4).
* `pd.DataFrame.to_excel(writer, sheet_name=...)` — keyword required in new pandas.
* Streamlit sidebar auto-collapses < ~800 px; `initial_sidebar_state="expanded"` set.
* Console prints mangle `°` on Windows cp1252 — code uses "deg" in user-facing strings.
