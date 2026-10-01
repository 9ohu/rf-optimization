"""Fetch the Sites map's offline map packs (the one step that uses the Internet).

    python scripts/update_offline_map.py                 # streets, night lights, day satellite
    python scripts/update_offline_map.py --imagery       # + regional imagery, 10 m (large)
    python scripts/update_offline_map.py --detail --arcgis-key KEY --areas BAS
                                                         # + building-scale imagery, 0.5 m,
                                                         #   around the Basra sites
    python scripts/update_offline_map.py --only detail --areas NAS   # add another area
    python scripts/update_offline_map.py --bbox 47.5 30.2 48.2 30.8
    python scripts/update_offline_map.py --vector-source D:\\maps\\iraq.pmtiles

Without --bbox the street-level area is the sites of the site KMZ in Data
Resources (with a 15 km margin), else R5. The building-scale imagery (Esri
World Imagery) is fetched around those sites with your ArcGIS API key
(--arcgis-key, RFOPT_ARCGIS_KEY, or the key saved in the app); the pack keeps
what it has and adds the sites asked for now (--detail-fresh starts over).
The packs go to the app's data folder (see `rfopt.geo.offline_basemap.folder`);
copy that folder to another PC to use the same map there. Same as Map layers &
Analysis → Offline map → Update offline map on the Sites map.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfopt.geo import offline_basemap as OB  # noqa: E402


def _kmz_sites():
    """(site ID, lat, lon) of every site of the site KMZ in Data Resources."""
    try:
        import pandas as pd

        from rfopt.ingest.kmz_sites import load_kmz_sites
        from rfopt.resources import store
        files = [f for f in store.resource("kmz").files if f.path.is_file()]
        if not files:
            return []
        s = load_kmz_sites(str(files[0].path), region=None).sectors
        s = s.assign(latitude=pd.to_numeric(s["latitude"], errors="coerce"),
                     longitude=pd.to_numeric(s["longitude"], errors="coerce"))
        s = s.groupby("site_id", as_index=False)[["latitude", "longitude"]].mean()
        return list(zip(s["site_id"], s["latitude"], s["longitude"]))
    except Exception:
        return []


def _area(site_id) -> str:
    import re
    m = re.match(r"\s*([A-Za-z]+)", str(site_id))
    return m.group(1).upper() if m else "OTHER"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--imagery", action="store_true",
                    help="also the regional Sentinel-2 imagery, 10 m (large)")
    ap.add_argument("--detail", action="store_true",
                    help="also the building-scale Esri World Imagery, 0.5 m, around "
                         "the sites (ArcGIS API key; very large)")
    ap.add_argument("--arcgis-key", help="ArcGIS API key for --detail (else "
                                         "RFOPT_ARCGIS_KEY or the key saved in the app)")
    ap.add_argument("--areas", nargs="+", metavar="AREA",
                    help="--detail only around these areas' sites (site ID prefix: "
                         "BAS, NAS, EMA, SAM)")
    ap.add_argument("--detail-radius", type=float, default=OB.DETAIL_RADIUS_KM,
                    help="km of full sharpness around each site (default %(default)s)")
    ap.add_argument("--detail-maxzoom", type=int, default=OB.DETAIL_MAX, choices=(17, 18, 19))
    ap.add_argument("--detail-url", help="another licensed imagery service for --detail "
                                         "({z}/{x}/{y} tile URL) instead of Esri")
    ap.add_argument("--detail-fresh", action="store_true",
                    help="fetch the building-scale imagery again from scratch")
    ap.add_argument("--only", nargs="+", choices=list(OB.PACKS),
                    help="fetch only these packs")
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("W", "S", "E", "N"),
                    help="street-level area, lon / lat")
    ap.add_argument("--vector-source", help="a PMTiles build (URL or file) to cut the "
                                            "streets pack from")
    ap.add_argument("--vector-maxzoom", type=int, default=15)
    ap.add_argument("--imagery-maxzoom", type=int, default=13)
    a = ap.parse_args(argv)

    keys = a.only or (["vector", "night", "earth"] + (["imagery"] if a.imagery else [])
                      + (["detail"] if a.detail else []))
    sites = _kmz_sites()
    region = tuple(a.bbox) if a.bbox else (
        OB.region_bbox([s[1] for s in sites], [s[2] for s in sites]))
    if a.areas:                     # the building-scale imagery only
        want = {x.upper() for x in a.areas}
        sites = [s for s in sites if _area(s[0]) in want]
    points = OB.site_points([s[1] for s in sites], [s[2] for s in sites])
    print(f"Offline map packs -> {OB.folder()}")
    print(f"Street-level area (W S E N): {', '.join(f'{v:.3f}' for v in region)}")
    if "detail" in keys:
        if a.bbox:                  # the sites inside the box
            points = [p for p in points
                      if a.bbox[0] <= p[1] <= a.bbox[2] and a.bbox[1] <= p[0] <= a.bbox[3]]
        tiles, mb = OB.detail_estimate(points, max_zoom=a.detail_maxzoom,
                                       radius_km=a.detail_radius)
        print(f"Building-scale imagery: {len(points):,} sites, ~{tiles:,} tiles, "
              f"~{mb / 1000:,.1f} GB (estimate)")
    last = {}

    def progress(stage, done, total):
        pct = int(100 * done / max(total, 1))
        if last.get(stage) != pct:
            last[stage] = pct
            print(f"\r  {stage}: {done:,}/{total:,} ({pct}%)", end="", flush=True)

    res = OB.update(keys, region=region, vector_source=a.vector_source,
                    imagery_max=a.imagery_maxzoom, vector_max=a.vector_maxzoom,
                    sites=points, detail_max=a.detail_maxzoom,
                    detail_radius_km=a.detail_radius, arcgis_key=a.arcgis_key,
                    detail_url=a.detail_url, detail_fresh=a.detail_fresh,
                    progress=progress, log=lambda m: print("\n" + m))
    print()
    return 0 if all(v.startswith("ok") for v in res.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
