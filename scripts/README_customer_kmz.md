# R5 customer KMZ — weekly refresh

`build_customer_kmz.py` regenerates the **"R5 Sites - Full Visualization"** KMZ
you send to the customer (Asiacell) from two inputs:

| input | supplies | where it lives |
|---|---|---|
| **arrows KMZ** (`--arrows`) | site **location**, number of **sectors** + azimuth, On Air (blue arrow) vs Planned (red arrow) | the KMZ you maintain in Google Earth, e.g. `Desktop\KMZ_20260909U_v2.kmz` |
| **Engineering Parameter Tracker** (`--tracker`) | the **cell details** in every sector balloon — 2G/3G/4G cells + RET | `D:\WeLink_data_files\...\ReceiveFiles\WK## Engineering Parameter Tracker-*.xlsx` |

Output: `<name>.kmz` (same five folders as before — On Air / Planned / Off Air
sectors, Telecom Towers 3D lattice, Site Names) **plus `<name>.review.csv`**
listing everything worth a human glance before the file goes out.

## Run it

```
cd C:\Users\swx1351646\rf-optimizer
.venv\Scripts\python.exe scripts\build_customer_kmz.py ^
    --arrows  "C:\Users\swx1351646\Desktop\KMZ_20260909U_v2.kmz" ^
    --tracker "D:\WeLink_data_files\swx1351646\ReceiveFiles\WK37 Engineering Parameter Tracker-06092026.xlsx" ^
    --previous "C:\Users\swx1351646\Downloads\R5_Sites.kmz" ^
    --out     "C:\Users\swx1351646\Desktop\R5_Sites_Customer_20260909.kmz"
```

With no arguments it picks the newest `KMZ_*.kmz` on the Desktop, the newest
`WK* Engineering Parameter Tracker*.xlsx` in the ReceiveFiles folder, and writes
`Desktop\R5_Sites_Customer_<today>.kmz`. `--previous` is optional — give it last
week's KMZ and the review file will call out every site that is new since then.

Takes ~20 s (pandas + python-calamine read the 40 MB workbook).

## Status rules

1. **`ARB…` in the site name** → Off Air (the border-road sites). Place names
   that merely contain the letters — Marbad, Garbi, Arbatalaf — are **not**
   treated as ARB.
2. **Every cell deactivated in the tracker** → Off Air, whatever the arrow colour.
3. **red arrows** → Planned  · **blue arrows** → On Air.
4. **In the tracker but missing from the arrows KMZ** → kept as On Air and
   flagged `missing-from-arrows` (add it to your Google-Earth master).
5. Cell balloons are always joined from the tracker on the `SITEID-Sn` key,
   including the *Deactive* sheets. A site the tracker has never heard of
   (brand-new Planned site) gets "No … record found" balloons.

## review.csv categories

| category | meaning / what to do |
|---|---|
| `no-coords` | site has no usable position (placeholder `30.0, 47.0` and not in the tracker) — **omitted from the KMZ**; send me the lat/long |
| `missing-from-arrows` | active in the tracker, absent from the arrows KMZ — add it to your Google-Earth file |
| `moved` | arrows-KMZ position differs from the tracker's by >150 m — the arrows position was used; check the big ones |
| `extra-arrow` | the arrows KMZ draws more sectors than the tracker knows — usually a stray arrow, occasionally a real new sector |
| `new-site` | not in last week's KMZ |
| `off-air` | why a site landed in Off Air |

## Folders in the output

| folder | what it is |
|---|---|
| On Air / Planned / Off Air sectors | the ground coverage footprint of every sector — two nested polygons, the 3 dB main lobe and a brighter inner core. The **outer one carries the cell balloon**. |
| 3D coverage beams | the beam itself, from the antenna on the mast down to the edge of the footprint, plus a boresight line. **Shipped switched off** — it is the heaviest layer; tick it on in Google Earth when you want the 3D view. |
| Telecom Towers (3D lattice) | one mast per site, drawn at its real height |
| Site Names | the label layer |

## Coverage-beam model

Each sector is drawn as a real antenna main lobe, not a pie slice:

* footprint radius at a horizontal offset `x` from boresight follows the 3GPP
  panel pattern as a **power** ratio — `r(x) = R · 10^(-min(12(x/65°)², 25 dB)/10)`.
  Half the reach at the ±32.5° 3 dB points, pinched to nothing by ±90°.
* `R` is anchored on the **boresight ground distance** `h / tan(total downtilt)`
  stretched 1.35× toward the far 3 dB edge, then clamped to 150–800 m so a whole
  region stays readable. Using the far 3 dB edge directly is useless at the 2–4°
  tilts most of R5 runs — it sits above the horizon and every lobe hits the cap.
* **total downtilt = mechanical + electrical**, read per sector from the tracker
  (LTE `M-DOWNTILT` + `MAX RET`, else GSM, else UMTS; 3.0° when nothing is on
  file). Tilt and the modelled reach are printed in every balloon.

Tune `HBW_DEG`, `LOBE_MIN_M` / `LOBE_MAX_M` at the top of the script to taste.

## Tower model

A tapered 4-leg self-support lattice mast drawn at its **true antenna height**
(`TOWER_SCALE = 1.0` — raise it if you want them more prominent from far out):

* legs taper from a wide footing to a narrow platform, `TOWER_PANELS` (6) bays
* zigzag bracing on all four faces, a belt at every bay boundary
* red/white **aviation banding** plus a mast-top beacon on anything ≥ 30 m
  (`AVIATION_BAND_MIN_M`); shorter masts stay galvanised grey
* one **panel antenna on the platform per sector, at its true azimuth** — so the
  mast itself shows you where the site points
* microwave dish on one face, equipment shelter offset at the footing

Azimuths for a tracked site come from the tracker (LTE preferred, then GSM, then
UMTS; a bare `0` is treated as "not filled in" and falls back to an even split).
