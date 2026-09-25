"""R5 Sup District and City boundaries: the official Iraq administrative areas.

The shapes are the OCHA Common Operational Dataset for Iraq — "Iraq -
Subnational Administrative Boundaries (COD-AB)", Iraq Central Statistics Office
via OCHA FISS, https://data.humdata.org/dataset/cod-ab-irq, licensed CC BY-IGO.
`assets/r5_admin_boundaries.geojson` keeps the R5 part of it: the 56 admin3
sub-districts and the 4 admin1 governorates of Al-Basrah, Maysan, Thi Qar and
Al-Muthanna, which the page calls Basrah, Amarah, Nasiriyah and Samawah (the EP
tracker's Basrah, Emarah, Nassriya and Samawa).

A site's Sup District is the sub-district whose boundary holds it. A site just
past a boundary (a coastline or border line drawn a little inside) is given the
nearest sub-district within `SNAP_M`; one farther out is counted as outside R5.
Nothing here draws or guesses a boundary the dataset does not hold.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

ASSET = Path(__file__).resolve().parent / "assets" / "r5_admin_boundaries.geojson"
SNAP_M = 2000.0
OUTSIDE = "Outside R5 sub-districts"
CITY_OF = {"Al-Basrah": "Basrah", "Maysan": "Amarah", "Thi Qar": "Nasiriyah",
           "Al-Muthanna": "Samawah"}
ATTRIBUTION = ("Boundaries: Iraq Central Statistics Office / OCHA, COD-AB "
               "(data.humdata.org/dataset/cod-ab-irq), CC BY-IGO")


@dataclass(frozen=True)
class Area:
    level: int             # 1 governorate · 3 sub-district
    name: str              # "Markaz Al-Basrah"
    name_ar: str
    pcode: str
    district: str          # admin2, for a sub-district
    governorate: str       # "Al-Basrah"
    city: str              # "Basrah" — the page's name for the governorate
    rings: tuple           # (outer, *holes), each ((lon, lat), ...)


def load_areas(data: dict | bytes | str) -> list[Area]:
    """The areas of a boundaries GeoJSON (the bundled asset's format)."""
    doc = json.loads(data) if isinstance(data, (bytes, str)) else data
    out = []
    for f in doc.get("features", []):
        p, g = f.get("properties") or {}, f.get("geometry") or {}
        polys = ([g["coordinates"]] if g.get("type") == "Polygon"
                 else g.get("coordinates", []) if g.get("type") == "MultiPolygon" else [])
        for rings in polys:
            rings = tuple(tuple((float(x), float(y)) for x, y, *_ in r) for r in rings)
            if not rings or len(rings[0]) < 3:
                continue
            gov = p.get("governorate", "")
            out.append(Area(int(p.get("level", 3)), p.get("name", ""), p.get("name_ar") or "",
                            p.get("pcode", ""), p.get("district") or "", gov,
                            CITY_OF.get(gov) or p.get("city") or gov, rings))
    return out


@lru_cache(maxsize=1)
def r5_areas() -> tuple:
    """The bundled R5 governorates and sub-districts (empty if the asset is missing)."""
    if not ASSET.exists():
        return ()
    return tuple(load_areas(ASSET.read_text(encoding="utf-8")))


def sub_districts(areas) -> list[Area]:
    return [a for a in areas if a.level == 3]


def governorates(areas) -> list[Area]:
    return [a for a in areas if a.level == 1]


def _in_ring(lons: np.ndarray, lats: np.ndarray, ring) -> np.ndarray:
    """Ray casting: True where the point is inside the closed ring."""
    r = np.asarray(ring, dtype=float)
    x0, y0 = r[:, 0], r[:, 1]
    x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
    out = np.zeros(len(lons), dtype=bool)
    for a, b, c, d in zip(x0, y0, x1, y1):
        if b == d:
            continue
        crosses = (b > lats) != (d > lats)
        out ^= crosses & (lons < (c - a) * (lats - b) / (d - b) + a)
    return out


def _edge_distance_m(lon: float, lat: float, ring) -> float:
    """Metres from a point to the nearest edge of a ring (local flat projection)."""
    r = np.asarray(ring, dtype=float)
    kx = 111_320.0 * math.cos(math.radians(lat))
    ky = 110_540.0
    x0, y0 = (r[:, 0] - lon) * kx, (r[:, 1] - lat) * ky
    x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
    dx, dy = x1 - x0, y1 - y0
    seg = dx * dx + dy * dy
    t = np.clip(np.where(seg > 0, -(x0 * dx + y0 * dy) / np.where(seg > 0, seg, 1), 0), 0, 1)
    return float(np.sqrt((x0 + t * dx) ** 2 + (y0 + t * dy) ** 2).min())


def area_of(lons, lats, areas, snap_m: float = SNAP_M) -> tuple[np.ndarray, np.ndarray]:
    """For each point: the area that holds it ("" when none within `snap_m`),
    and the metres it was snapped by (0 inside a boundary)."""
    lons = np.asarray(lons, dtype=float)
    lats = np.asarray(lats, dtype=float)
    names = np.full(len(lons), "", dtype=object)
    snapped = np.zeros(len(lons), dtype=float)
    known = np.isfinite(lons) & np.isfinite(lats)
    for a in areas:
        todo = known & (names == "")
        if not todo.any():
            break
        outer = np.asarray(a.rings[0])
        near = (todo & (lons >= outer[:, 0].min()) & (lons <= outer[:, 0].max())
                & (lats >= outer[:, 1].min()) & (lats <= outer[:, 1].max()))
        if not near.any():
            continue
        idx = np.flatnonzero(near)
        hit = _in_ring(lons[idx], lats[idx], a.rings[0])
        for hole in a.rings[1:]:
            hit &= ~_in_ring(lons[idx], lats[idx], hole)
        names[idx[hit]] = a.name
    for i in np.flatnonzero(known & (names == "")):
        best, dist = "", snap_m
        for a in areas:
            d = _edge_distance_m(lons[i], lats[i], a.rings[0])
            if d <= dist:
                best, dist = a.name, d
        if best:
            names[i], snapped[i] = best, dist
    return names, snapped


def geojson(areas) -> dict:
    """Areas as a GeoJSON FeatureCollection, one feature per polygon."""
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "properties": {"level": a.level, "name": a.name, "city": a.city, "pcode": a.pcode},
         "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in r] for r in a.rings]}}
        for a in areas]}
