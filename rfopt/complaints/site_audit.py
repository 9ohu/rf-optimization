"""Per-site RF parameter health check.

Given the engineering-parameter frame (rfopt.ingest.cellparams) for one site,
flag things that plausibly explain a coverage / data complaint *without* KPI:

  tilt          over-tilt for the mast height, tilt imbalance between sectors,
                electrical tilt at/above the antenna max, missing RET
  antenna plan  large azimuth gap (coverage hole), sector azimuth overlap,
                too few sectors
  capacity      only the narrow-band layer, single carrier, N41-only
  PCI / MOD3    collision within the site or with a near neighbour
  power         RS-power inconsistent across co-sited sectors / missing
  isolation     nearest R5 site far away (from the param lat/lon)

Each flag carries a severity, a plain-English note, and which complaint
category it points to.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from rfopt.actions.geometry import (angular_offset_deg,
                                    elevation_angle_to_point_deg)

_SEV = {"info": 0, "warning": 1, "critical": 2}


@dataclass
class Flag:
    code: str
    severity: str
    note: str
    points_to: str            # complaint category this would explain
    sectors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity,
                "points_to": self.points_to,
                "sectors": ",".join(self.sectors), "note": self.note}


@dataclass
class SiteAudit:
    site_id: str
    enodeb_name: str = ""
    latitude: float | None = None
    longitude: float | None = None
    n_sectors: int = 0
    n_cells: int = 0
    bands: list[str] = field(default_factory=list)
    height_m: float | None = None
    azimuths: list[float] = field(default_factory=list)
    tilt_summary: str = ""
    nearest_site: str = ""
    nearest_site_m: float | None = None
    flags: list[Flag] = field(default_factory=list)
    sector_table: list[dict] = field(default_factory=list)
    _rs_power_unknown: bool = False

    def site_found_ok(self) -> bool:
        return (self.n_sectors > 0
                and not any(f.code == "site_not_in_params" for f in self.flags))

    @property
    def severity(self) -> str:
        if not self.flags:
            return "ok"
        return max((f.severity for f in self.flags), key=lambda s: _SEV[s])

    def likely_causes(self) -> list[str]:
        seen, out = set(), []
        for f in sorted(self.flags, key=lambda f: -_SEV[f.severity]):
            if f.points_to not in seen:
                seen.add(f.points_to)
                out.append(f.points_to)
        return out

    def headline(self) -> str:
        if not self.flags:
            return "No parameter issue found - check KPI / neighbours / indoor."
        f = sorted(self.flags, key=lambda f: -_SEV[f.severity])[0]
        return f"{f.code}: {f.note}"

    def as_row(self) -> dict:
        return {
            "site_id": self.site_id, "enodeb_name": self.enodeb_name,
            "severity": self.severity, "n_sectors": self.n_sectors,
            "bands": ",".join(self.bands), "height_m": self.height_m,
            "azimuths": ",".join(f"{a:.0f}" for a in self.azimuths),
            "tilt_summary": self.tilt_summary,
            "nearest_site": self.nearest_site,
            "nearest_site_m": (round(self.nearest_site_m)
                               if self.nearest_site_m else None),
            "n_flags": len(self.flags),
            "flags": " | ".join(f"{f.code}({f.severity})" for f in self.flags),
            "likely_causes": ", ".join(self.likely_causes()),
            "headline": self.headline(),
        }


# --------------------------------------------------------------------------- #
def _sector_azimuth_gaps(azimuths: list[float]) -> tuple[float, float]:
    """Return (largest gap between adjacent sectors, its centre bearing)."""
    a = sorted(set(round(x) % 360 for x in azimuths if x == x))
    if len(a) < 2:
        return 360.0, (a[0] + 180) % 360 if a else 0.0
    gaps = [(a[(i + 1) % len(a)] - a[i]) % 360 for i in range(len(a))]
    j = int(np.argmax(gaps))
    centre = (a[j] + gaps[j] / 2) % 360
    return float(max(gaps)), float(centre)


def audit_site(
    site_id: str,
    params: pd.DataFrame,
    *,
    all_sites_xy: pd.DataFrame | None = None,
    max_ret_default: float = 10.0,
) -> SiteAudit:
    g = params[params["site_id"].str.upper() == site_id.upper()].copy()
    a = SiteAudit(site_id=site_id.upper())
    if g.empty:
        a.flags.append(Flag("site_not_in_params", "warning",
                            f"{site_id} not found in the engineering parameter "
                            "file - cannot check parameters.", "insufficient_data"))
        return a

    a.enodeb_name = str(g["enodeb_name"].iloc[0])
    a.latitude = float(g["latitude"].median()) if g["latitude"].notna().any() else None
    a.longitude = float(g["longitude"].median()) if g["longitude"].notna().any() else None
    a.height_m = round(float(g["antenna_height_m"].median()), 1) \
        if g["antenna_height_m"].notna().any() else None
    a.n_cells = len(g)
    a.bands = sorted(x for x in g["band_label"].dropna().unique())

    sect = (g.groupby("sector_num", dropna=False)
            .agg(sector_id=("sector_id", "first"),
                 azimuth=("azimuth_deg", "median"),
                 elec_tilt=("elec_tilt_deg", "median"),
                 mech_tilt=("mech_tilt_deg", "median"),
                 total_tilt=("total_tilt_deg", "median"),
                 max_ret=("max_ret_deg", "median"),
                 rs_power=("rs_power_dbm", "median"),
                 bands=("band_label", lambda s: ",".join(sorted(s.dropna().unique()))),
                 min_bw=("bandwidth_mhz", "min"),
                 max_bw=("bandwidth_mhz", "max"),
                 pcis=("pci", lambda s: sorted({int(x) for x in s.dropna()})),
                 n_cells=("cell_id", "size"))
            .reset_index())
    a.n_sectors = len(sect)
    a.azimuths = [float(x) for x in sect["azimuth"].dropna()]
    a.sector_table = sect.to_dict("records")

    tilts = sect["total_tilt"].dropna()
    if len(tilts):
        a.tilt_summary = (f"{tilts.min():.1f}-{tilts.max():.1f} deg "
                          f"(median {tilts.median():.1f})")

    F = a.flags.append
    h = a.height_m or 25.0

    # ---- tilt --------------------------------------------------------
    miss = sect[sect["elec_tilt"].isna()]
    if len(miss):
        F(Flag("missing_ret", "critical" if len(miss) == len(sect) else "warning",
               f"{len(miss)}/{len(sect)} sectors have no electrical tilt in the "
               f"parameter file (RET read failure / not provisioned) - a stuck "
               f"or wrong RET is a common coverage-complaint cause.",
               "far_coverage", list(miss["sector_id"].astype(str))))
    good = sect.dropna(subset=["total_tilt"])
    if len(good) >= 2 and (good["total_tilt"].max() - good["total_tilt"].min()) >= 3.0:
        hi = good.loc[good["total_tilt"].idxmax()]
        lo = good.loc[good["total_tilt"].idxmin()]
        F(Flag("tilt_imbalance", "info",
               f"Sector tilts differ by "
               f"{good['total_tilt'].max() - good['total_tilt'].min():.1f} deg "
               f"({lo['sector_id']} {lo['total_tilt']:.1f} vs {hi['sector_id']} "
               f"{hi['total_tilt']:.1f}). Often deliberate (different terrain) - "
               f"but if the complaint is on {hi['sector_id']} it may be "
               f"under-covering, or on {lo['sector_id']} it may be overshooting.",
               "far_coverage",
               [str(hi["sector_id"]), str(lo["sector_id"])]))
    for _, s in good.iterrows():
        # "excessive downtilt" heuristic: total tilt well beyond what a 300 m
        # cell edge would need for this mast.
        need_edge = elevation_angle_to_point_deg(h, 300.0) + 3.25
        if s["total_tilt"] >= max(need_edge + 2.0, 7.0):
            F(Flag("over_tilt", "warning",
                   f"{s['sector_id']} total downtilt {s['total_tilt']:.1f} deg "
                   f"on a {h:.0f} m mast is steep - 3 dB edge only "
                   f"~{h / math.tan(math.radians(max(s['total_tilt'] - 3.25, .3))):.0f} m. "
                   f"Reduce RET if the complaint is at range.", "far_coverage",
                   [str(s["sector_id"])]))
        mr = s["max_ret"] if pd.notna(s["max_ret"]) else max_ret_default
        if pd.notna(s["elec_tilt"]) and s["elec_tilt"] >= mr - 0.1 and s["elec_tilt"] >= 6:
            F(Flag("ret_at_max", "info",
                   f"{s['sector_id']} electrical tilt {s['elec_tilt']:.1f} deg is "
                   f"at the recorded max ({mr:.1f}) - no room to downtilt "
                   f"further electrically; use mechanical tilt if needed.",
                   "far_coverage", [str(s["sector_id"])]))

    # ---- antenna plan ---------------------------------------------
    if a.n_sectors <= 2:
        F(Flag("few_sectors", "info",
               f"Only {a.n_sectors} sector(s) - wide areas between beams; a "
               f"complaint off-beam is expected.", "off_axis_azimuth",
               list(sect["sector_id"].astype(str))))
    if a.n_sectors >= 3:
        gap, centre = _sector_azimuth_gaps(a.azimuths)
        if gap >= 170:
            F(Flag("azimuth_gap", "warning" if gap >= 190 else "info",
                   f"Largest gap between sector azimuths is {gap:.0f} deg "
                   f"(around bearing {centre:.0f} deg) - possible coverage hole "
                   f"there; a complaint in that direction may need a sector "
                   f"re-point or a new sector.", "off_axis_azimuth"))
        pairs = sorted(round(x) % 360 for x in a.azimuths)
        for i in range(len(pairs)):
            d = angular_offset_deg(pairs[i], pairs[(i + 1) % len(pairs)])
            if 0 < d <= 35:
                F(Flag("azimuth_overlap", "info",
                       f"Two sectors only {d:.0f} deg apart "
                       f"({pairs[i]:.0f}/{pairs[(i+1) % len(pairs)]:.0f}) - "
                       f"redundant coverage / possible pilot pollution one way, "
                       f"a hole the other.", "off_axis_azimuth"))
                break

    # ---- capacity ------------------------------------------------
    all_bw = g["bandwidth_mhz"].dropna()
    wide = [b for b in a.bands if b in ("L1800", "L2600", "N41")]
    if not wide and a.bands:
        F(Flag("narrow_band_only", "warning",
               f"Only narrow-band layer(s) present ({', '.join(a.bands)}). "
               f"A busy sector here throttles fast - 'Data Service' complaints "
               f"are likely capacity.", "congestion"))
    elif len(a.bands) == 1:
        F(Flag("single_carrier", "info",
               f"Single carrier ({a.bands[0]}) - no offload layer for busy "
               f"hour.", "congestion"))
    if len(all_bw) and all_bw.max() <= 10:
        F(Flag("all_10mhz", "info",
               "Every carrier is <=10 MHz - limited peak throughput.",
               "congestion"))

    # ---- PCI / MOD3 within the site ---------------------------
    for _, s in sect.iterrows():
        pcs = s["pcis"] or []
        if len(pcs) != len(set(pcs)):
            F(Flag("dup_pci_sector", "warning",
                   f"{s['sector_id']} lists repeated PCIs {pcs} - a stale / "
                   f"double-provisioned cell.", "interference",
                   [str(s["sector_id"])]))
    same_band = g.dropna(subset=["pci", "earfcn"])
    for (bnd, earf), grp in same_band.groupby(["band", "earfcn"]):
        pci_counts = grp["pci"].value_counts()
        clash = pci_counts[pci_counts > 1]
        if len(clash):
            F(Flag("pci_reuse_site", "warning",
                   f"PCI {list(clash.index.astype(int))} used on >1 cell of the "
                   f"same carrier ({bnd}/{int(earf)}) at this site - RS "
                   f"collision / handover ambiguity.", "interference"))

    # ---- power -------------------------------------------------
    rsp = sect["rs_power"].dropna()
    if len(rsp) >= 2 and (rsp.max() - rsp.min()) >= 2.0:
        F(Flag("rs_power_imbalance", "warning",
               f"RS power varies {rsp.min():.1f}-{rsp.max():.1f} dBm across "
               f"sectors - the low sector under-covers relative to its "
               f"neighbours.", "far_coverage"))
    rs_unknown = sect["rs_power"].isna().all()

    # ---- isolation ------------------------------------------
    if all_sites_xy is not None and a.latitude is not None:
        others = all_sites_xy[all_sites_xy.index != site_id.upper()]
        if len(others):
            dl = (others["latitude"] - a.latitude) * 111_320.0
            do = (others["longitude"] - a.longitude) * 111_320.0 * \
                math.cos(math.radians(a.latitude))
            dist = np.hypot(dl, do)
            k = int(np.argmin(dist.to_numpy()))
            a.nearest_site = str(others.index[k])
            a.nearest_site_m = float(dist.iloc[k])
            if a.nearest_site_m > 3000:
                F(Flag("isolated_site", "warning",
                       f"Nearest R5 site {a.nearest_site} is "
                       f"{a.nearest_site_m/1000:.1f} km away - this site covers "
                       f"a large area alone; edge complaints need a new site / "
                       f"low-band, not tuning.", "new_site_needed"))

    a._rs_power_unknown = rs_unknown       # noted, not a flag
    return a


def audit_sites(site_ids, params: pd.DataFrame, **kw) -> dict[str, SiteAudit]:
    sx = (params.dropna(subset=["latitude", "longitude"])
          .groupby(params["site_id"].str.upper())[["latitude", "longitude"]]
          .median())
    return {sid: audit_site(sid, params, all_sites_xy=sx, **kw)
            for sid in dict.fromkeys(s.upper() for s in site_ids if s)}
