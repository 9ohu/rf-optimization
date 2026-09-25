# Intelligent RF Optimization Tool

KPI analysis → RF diagnosis → **computed** optimisation actions → engineer‑style
narrative, for 2G/3G/4G networks (LTE‑focused). Vendor profile: **Huawei**
(U2020 / PRS counter and KPI names), with a configurable mapping layer for any
export template.

The diagnosis and action engines are **deterministic and offline** – every
recommendation carries the KPIs and the geometry/link‑budget maths it was
derived from. The AI layer only *phrases* that result as an RF engineer would;
it is never the source of truth.

```
ingest    Excel/CSV  → vendor counter names → canonical schema
kpi       thresholds, hourly/daily roll‑ups (cell/sector/site), trend + anomaly
diagnosis rule engine: KPI patterns → problem classes + evidence + confidence
actions   physics/geometry: RET · RS power · tilt · azimuth · load‑balancing ·
          neighbour · capacity · interference — all calculated, not fixed
ai        optional Claude layer → root cause / evidence / parameter / target /
          impact / risks / KPIs to monitor after the change
geo       KMZ map (sector beams, health colouring, overshoot rays) for R5 sites
reports   annotated Excel workbook · Markdown report · CSV action plan
```

---

## Quick start

```bash
cd rf-optimizer
python -m venv .venv
.venv\Scripts\activate                # Windows  (source .venv/bin/activate on *nix)
pip install -r requirements.txt

python scripts/generate_sample_data.py   # creates sample_data/ (R5, 10 sites, 14 d)
streamlit run app/Home.py                # the web app
```

> **Windows path length:** keep the project folder path short (e.g.
> `C:\Users\<you>\rf-optimizer`). Some wheels (pyarrow) fail to load from very
> deep paths (>260 chars).

### Web app (primary interface)

| Page | What it does |
|---|---|
| **Home** | Upload KPI export + site database, set technology / region / environment / RET unit, run the analysis, see the dashboard |
| **KPI Analysis** | KPI tables by cell/sector/site, hourly profiles, threshold breaches, trends & anomalies, busy hour |
| **Diagnosis & Actions** | Every diagnosed problem and its computed action (root cause → parameter → target → impact → risks → monitor); optional AI narrative per action |
| **Cell Deep‑Dive** | One cell's KPIs, trends and geometry **+ the RET / tilt / azimuth calculator** (complaint location by distance+bearing or lat/lon) with a vertical‑pattern plot |
| **Site Map** | Interactive sector map coloured by health + **KMZ export** for Google Earth |
| **Reports** | Generate & download the Excel workbook / Markdown report / CSV action plan |

### Command line

```bash
# full pipeline → console + Excel + Markdown + KMZ
python -m rfopt analyze KPI.xlsx --site-db sites.csv --region R5 \
    --env suburban --out-dir out --excel --markdown --kmz

# the worked RET example (RET is in 0.1° units here: 70 = 7.0°)
python -m rfopt complaint --height 38.5 --azimuth 20 --ret 70 --ret-unit tenths \
    --distance 1800 --bearing 30 --env suburban --meas-rsrp -114

python -m rfopt sample        # regenerate the demo data set
```

### Python

```python
from rfopt.pipeline import run_analysis
r = run_analysis("KPI.xlsx", site_db="sites.csv", region_filter="R5",
                 env_kind="suburban")
r.summary(); r.diagnoses; r.recommendations           # objects
r.diagnoses_df(); r.recommendations_df()               # DataFrames
```

---

## Input data

### KPI export (`.xlsx` / `.csv`)

One row per **cell per time bucket**. A title row above the header is handled
automatically. Any column set works – columns are matched to the canonical
schema by `config/huawei_kpi_mapping.yaml` (case / unit / punctuation
insensitive) with a fuzzy fallback; unmapped columns are listed in the UI for
manual mapping. Add your operator's exact headers to that YAML once and every
future upload maps itself.

Expected dimensions (any reasonable alias): time, `eNodeB Name`, `Cell Name`,
optionally `Cell ID`, `Frequency Band`, `Region`. Hourly vs daily is
auto‑detected. Sector id is derived from the cell name
(`R5-002_L1800_S1` → sector `R5-002-S1`), so co‑sited carriers group correctly.

Recognised LTE KPIs include: RRC/E‑RAB/RACH setup success, E‑RAB & context drop
rate, HO success (intra/inter/X2/S1) and ping‑pong, DL/UL user throughput,
latency, RSRP/RSRQ/SINR/CQI, DL/UL BLER, % poor RSRP, QPSK ratio, average &
P95 TA distance, DL/UL PRB & PDCCH utilisation, connected/active users, RRC
rejections, DL/UL data volume, cell availability, UL RSSI / interference.
Thresholds live in `config/thresholds_{lte,umts,gsm}.yaml` and are editable in
the UI per session.

### Site database (`.csv` / `.xlsx`) — optional but recommended

Enables geometry‑based analysis (tilt / azimuth / overshoot / real inter‑site
distance) and the KMZ map. One row per cell:

| column | notes |
|---|---|
| `cell_id` | must match the KPI `Cell Name` |
| `site_id`, `latitude`, `longitude` | site location |
| `antenna_height_m`, `azimuth_deg` | |
| `mech_tilt_deg`, `elec_tilt_deg` | electrical tilt = current RET (in degrees; if your RET is 0.1° units, set "RET unit = tenths" in the app) |
| `vbw_deg`, `hbw_deg` | vertical / horizontal beamwidth (defaults 6.5 / 65) |

Without a site database the tool still runs (KPI + threshold + trend + the
non‑geometry diagnoses); overshoot/coverage geometry checks are skipped rather
than guessed.

---

## How the action engine computes things

* **Optimal downtilt** to a target distance `d` at antenna height `h`:
  `θ* = atan(h/d)` (aim the beam peak at the point) or `θ* + VBW/2` (treat the
  point as the 3 dB cell edge). Change is capped at 3°/step for a controlled
  iteration.
* **Vertical pattern gain** toward a point: 3GPP‑style
  `G(x) = −min(12·(x/VBW)², SLA)` – used to quantify the dB gained/lost by a
  tilt change and to decide whether tilt is even the right lever.
* **RS power** delta = RSRP gap to target minus any tilt gain, capped by EPRE
  head‑room; flags when power alone can't close the gap (→ coverage layer / new
  site / DAS).
* **Overshoot** = P95 TA distance vs the real inter‑site distance (mean of the
  3 nearest sites from the site DB), corroborated by strong‑RSRP/poor‑RSRQ and
  inter‑freq HO.
* **Load balancing** target = PRB above 70 %, moved via reselection priority /
  `qOffsetFreq` / inter‑freq A5 / MLB, or CIO between co‑sited cells for
  imbalance.
* **Confidence** rises with the number of corroborating KPIs, data volume and
  the presence of geometry; falls when inputs are missing.

---

## AI narrative layer

Default is the built‑in **template narrator** – fully offline, produces the
same ten‑section structure (summary, why, confirming KPIs, action, why this
action, parameter/value, expected impact, risks, monitor‑after, rollback).

To use **Claude** instead: `pip install anthropic`, then either set
`ANTHROPIC_API_KEY` or paste a key in the app sidebar (or `--ai` on the CLI).
The model is constrained to the numbers the engine produced and cannot change
the recommended parameter or target; it falls back to the template on any
error.

---

## Tests

```bash
pytest -q
```

Covers the geometry/propagation maths, the Huawei name mapping, that all nine
injected problems in the sample set are diagnosed and actioned, the "no
spurious overshoot without a site DB" guard, the worked RET example, report /
KMZ output, and a headless render of every Streamlit page.

## Layout

```
rfopt/           the engine (importable library)
  ingest/        schema.py · mapping.py · loader.py
  kpi/           thresholds.py · analyze.py · anomaly.py
  diagnosis/     models.py · engine.py   (rules)
  actions/       geometry.py · propagation.py · recommend.py
  ai/            narrative.py             (template + Claude)
  geo/           kmz.py
  reports/       excel_report.py · text_report.py
  pipeline.py    run_analysis(...) orchestrator
  cli.py         python -m rfopt ...
app/             Streamlit app (Home.py + pages/)
config/          threshold + KPI-mapping YAML (edit these)
scripts/         generate_sample_data.py
tests/
```
