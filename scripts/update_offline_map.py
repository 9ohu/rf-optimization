"""Fetch the Sites map's offline map packs (the one step that uses the Internet).

    python scripts/update_offline_map.py                 # streets, night lights, day satellite
    python scripts/update_offline_map.py --imagery       # + regional imagery, 10 m (large)
    python scripts/update_offline_map.py --detail BAS --arcgis-key KEY
                                                         # + building-scale imagery, 0.5 m,
                                                         #   for the whole of Basrah
    python scripts/update_offline_map.py --only --detail NAS SAM     # just those regions
    python scripts/update_offline_map.py --bbox 47.5 30.2 48.2 30.8
    python scripts/update_offline_map.py --vector-source D:\\maps\\iraq.pmtiles

Without --bbox the street-level area is the sites of the site KMZ in Data
Resources (with a 15 km margin), else R5. The building-scale imagery (Esri
World Imagery) is fetched for whole governorates — BAS Basrah, NAS Nasiriyah,
SAM Samawah, EMA Amarah: every tile inside the boundary — with your ArcGIS
API key (--arcgis-key, RFOPT_ARCGIS_KEY, or the key saved in the app). Each
region's pack keeps what it has and carries on where an earlier update
stopped (--detail-fresh starts over).
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


def _sites_region():
    try:
        from rfopt.ingest.kmz_sites import load_kmz_sites
        from rfopt.resources import store
        files = [f for f in store.resource("kmz").files if f.path.is_file()]
        if not files:
            return OB.R5
        ks = load_kmz_sites(str(files[0].path), region=None)
        return OB.region_bbox(ks.sectors["latitude"], ks.sectors["longitude"])
    except Exception:
        return OB.R5


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--imagery", action="store_true",
                    help="also the regional Sentinel-2 imagery, 10 m (large)")
    ap.add_argument("--detail", nargs="+", metavar="REGION", type=str.upper,
                    choices=list(OB.REGION_NAMES),
                    help="also the building-scale Esri World Imagery, 0.5 m, for these "
                         "whole governorates: BAS Basrah, NAS Nasiriyah, SAM Samawah, "
                         "EMA Amarah (ArcGIS API key; very large)")
    ap.add_argument("--arcgis-key", help="ArcGIS API key for --detail (else "
                                         "RFOPT_ARCGIS_KEY or the key saved in the app)")
    ap.add_argument("--detail-maxzoom", type=int, default=OB.DETAIL_MAX,
                    choices=(16, 17, 18, 19))
    ap.add_argument("--detail-url", help="another licensed imagery service for --detail "
                                         "({z}/{x}/{y} tile URL) instead of Esri")
    ap.add_argument("--detail-fresh", action="store_true",
                    help="fetch the building-scale imagery again from scratch")
    ap.add_argument("--only", nargs="*", choices=list(OB.PACKS),
                    help="fetch only these packs (with --detail: only the regions)")
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("W", "S", "E", "N"),
                    help="street-level area, lon / lat")
    ap.add_argument("--vector-source", help="a PMTiles build (URL or file) to cut the "
                                            "streets pack from")
    ap.add_argument("--vector-maxzoom", type=int, default=15)
    ap.add_argument("--imagery-maxzoom", type=int, default=13)
    a = ap.parse_args(argv)

    regions = [OB.detail_key(c) for c in (a.detail or [])]
    if a.only is not None:
        keys = list(a.only) + regions
    else:
        keys = ["vector", "night", "earth"] + (["imagery"] if a.imagery else []) + regions
    if not keys:
        ap.error("nothing to fetch")
    region = tuple(a.bbox) if a.bbox else _sites_region()
    print(f"Offline map packs -> {OB.folder()}")
    print(f"Street-level area (W S E N): {', '.join(f'{v:.3f}' for v in region)}")
    for c, (tiles, mb) in OB.region_estimate(a.detail or [],
                                             max_zoom=a.detail_maxzoom).items():
        print(f"Building-scale imagery, {OB.REGION_NAMES[c]}: ~{tiles:,} tiles, "
              f"~{mb / 1000:,.1f} GB (estimate, zoom {OB.DETAIL_MIN}-{a.detail_maxzoom})")
    last = {}

    def progress(stage, done, total):
        pct = int(100 * done / max(total, 1))
        if last.get(stage) != pct:
            last[stage] = pct
            print(f"\r  {stage}: {done:,}/{total:,} ({pct}%)", end="", flush=True)

    res = OB.update(keys, region=region, vector_source=a.vector_source,
                    imagery_max=a.imagery_maxzoom, vector_max=a.vector_maxzoom,
                    detail_max=a.detail_maxzoom, arcgis_key=a.arcgis_key,
                    detail_url=a.detail_url, detail_fresh=a.detail_fresh,
                    progress=progress, log=lambda m: print("\n" + m))
    print()
    return 0 if all(v.startswith("ok") for v in res.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
