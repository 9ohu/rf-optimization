"""Fetch the Sites map's offline map packs (the one step that uses the Internet).

    python scripts/update_offline_map.py                 # streets, night lights, day satellite
    python scripts/update_offline_map.py --imagery       # + street-scale imagery (large)
    python scripts/update_offline_map.py --bbox 47.5 30.2 48.2 30.8
    python scripts/update_offline_map.py --vector-source D:\\maps\\iraq.pmtiles

Without --bbox the street-level area is the sites of the site KMZ in Data
Resources (with a 15 km margin), else R5. The packs go to the app's data
folder (see `rfopt.geo.offline_basemap.folder`); copy that folder to another PC
to use the same map there. Same as Map layers & Analysis → Offline map →
Update offline map on the Sites map.
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
                    help="also the street-scale Sentinel-2 imagery (large)")
    ap.add_argument("--only", nargs="+", choices=list(OB.PACKS),
                    help="fetch only these packs")
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("W", "S", "E", "N"),
                    help="street-level area, lon / lat")
    ap.add_argument("--vector-source", help="a PMTiles build (URL or file) to cut the "
                                            "streets pack from")
    ap.add_argument("--vector-maxzoom", type=int, default=15)
    ap.add_argument("--imagery-maxzoom", type=int, default=13)
    a = ap.parse_args(argv)

    keys = a.only or (["vector", "night", "earth"] + (["imagery"] if a.imagery else []))
    region = tuple(a.bbox) if a.bbox else _sites_region()
    print(f"Offline map packs -> {OB.folder()}")
    print(f"Street-level area (W S E N): {', '.join(f'{v:.3f}' for v in region)}")
    last = {}

    def progress(stage, done, total):
        pct = int(100 * done / max(total, 1))
        if last.get(stage) != pct:
            last[stage] = pct
            print(f"\r  {stage}: {done:,}/{total:,} ({pct}%)", end="", flush=True)

    res = OB.update(keys, region=region, vector_source=a.vector_source,
                    imagery_max=a.imagery_maxzoom, vector_max=a.vector_maxzoom,
                    progress=progress, log=lambda m: print("\n" + m))
    print()
    return 0 if all(v.startswith("ok") for v in res.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
