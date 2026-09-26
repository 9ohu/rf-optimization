"""A ticket re-analysed at the subscriber's actual location.

The Daily Target names a site, not a cell, so the general analysis
(`correlate.analyse_ticket`) judges the site as a whole: every KPI is the
site's worst cell hour by hour. Once the subscriber's real latitude /
longitude is known, the question changes — which sector actually serves that
point, and what did *that* sector do around the problem time?

This module answers it with the same rules as the general analysis:

* `best_server` picks the on-air sector best pointed at the point — the
  same distance × off-boresight score the Sites map uses for a searched
  lat, lon;
* `sector_frame` / `tracks_for` build the judged KPI tracks per sector
  (`build_tracks` on sector ids), falling back to the site's own track for a
  KPI the export only has per site (3G / 2G NodeB counters);
* `reanalyse` runs `analyse_ticket` on that sector, reads the measured RSRP
  at the point, and writes the Description from the result.

Nothing is invented: a value the data does not carry reads "N/A".
"""

from __future__ import annotations

import dataclasses
import math
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from rfopt.complaints.correlate import (INSUFFICIENT, TicketAnalysis, analyse_ticket,
                                        build_tracks)

NA = "N/A"
_M_PER_DEG = 111_320.0
_SECTOR_OF_OBJ = re.compile(r"[-_ ]([1-9])\s*$")


# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #
def metres(lat1, lon1, lat2, lon2) -> float:
    dy = (lat2 - lat1) * _M_PER_DEG
    dx = (lon2 - lon1) * _M_PER_DEG * math.cos(math.radians((lat1 + lat2) / 2))
    return float(math.hypot(dx, dy))


def bearing(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def az_gap(a, b) -> float:
    d = abs(float(a) - float(b)) % 360.0
    return 360.0 - d if d > 180.0 else d


def fmt_metres(m) -> str:
    if m is None or not np.isfinite(m):
        return NA
    return f"{m / 1000:.2f} km" if m >= 1000 else f"{m:.0f} m"


@dataclass
class Server:
    sector_id: str
    site_id: str
    distance_m: float
    bearing_deg: float           # from the site to the subscriber
    azimuth_deg: float
    az_diff_deg: float           # off the sector's boresight


def best_server(lat: float, lon: float, sectors: pd.DataFrame, *,
                nearest_sites: int = 6) -> Server | None:
    """The on-air sector best pointed at (lat, lon): among the nearest sites,
    the lowest distance × (1 + (off-boresight / 65°)²) — the Sites map's rule.
    `sectors`: sector_id, site_id, latitude, longitude, azimuth_deg (on air)."""
    if sectors is None or len(sectors) == 0 or not (np.isfinite(lat) and np.isfinite(lon)):
        return None
    s = sectors.dropna(subset=["latitude", "longitude", "azimuth_deg"])
    if s.empty:
        return None
    coslat = math.cos(math.radians(lat))
    d = np.hypot((s["latitude"].to_numpy(float) - lat) * _M_PER_DEG,
                 (s["longitude"].to_numpy(float) - lon) * _M_PER_DEG * coslat)
    s = s.assign(dist_m=d)
    near = (s.groupby("site_id")["dist_m"].min().nsmallest(nearest_sites).index)
    best, best_score = None, None
    for r in s[s["site_id"].isin(near)].itertuples(index=False):
        brg = bearing(r.latitude, r.longitude, lat, lon)
        off = az_gap(r.azimuth_deg, brg)
        score = r.dist_m * (1.0 + (off / 65.0) ** 2)
        if best_score is None or score < best_score:
            best_score = score
            best = Server(str(r.sector_id), str(r.site_id), float(r.dist_m), float(brg),
                          float(r.azimuth_deg), float(off))
    return best


def sector_of(site_id: str, obj: str) -> str:
    """The sector a KPI object (a cell name) sits on — the hourly loader's own
    rule — or "" when the name carries none."""
    m = _SECTOR_OF_OBJ.search(str(obj or ""))
    site = str(site_id or "").strip().upper()
    return f"{site}-S{m.group(1)}" if m and site else ""


# --------------------------------------------------------------------------- #
# KPI tracks per sector
# --------------------------------------------------------------------------- #
def sector_frame(df: pd.DataFrame) -> pd.DataFrame:
    """An hourly frame (`load_hourly_raw`) keyed by sector instead of site, so
    `build_tracks` keeps each sector's worst cell per hour. Rows with no sector
    (a 3G / 2G NodeB counter, "-S0") are left out."""
    if df is None or df.empty or "sector_id" not in df.columns:
        return pd.DataFrame(columns=getattr(df, "columns", []))
    sec = df["sector_id"].astype(str).str.upper()
    keep = ~sec.str.endswith("-S0")
    return df[keep].assign(site_id=sec[keep])


def sector_tracks(frames) -> list:
    """frames: as for `build_tracks` — (kind, source, frame, judged)."""
    return build_tracks([(k, s, sector_frame(df), j) for k, s, df, j in frames])


def tracks_for(sector_id: str, site_id: str, sec_tracks, site_tracks) -> list:
    """The judged tracks for one sector, keyed by the sector id: its own
    where the export has the sector, else its site's (a NodeB counter)."""
    sector_id, site_id = str(sector_id).upper(), str(site_id).upper()
    out, have = [], set()
    for tr in sec_tracks or []:
        if sector_id in tr.by_site:
            out.append(dataclasses.replace(tr, by_site={sector_id: tr.by_site[sector_id]}))
            have.add((tr.kind, tr.canon))
    for tr in site_tracks or []:
        if (tr.kind, tr.canon) not in have and site_id in tr.by_site:
            out.append(dataclasses.replace(tr, by_site={sector_id: tr.by_site[site_id]}))
    return out


# --------------------------------------------------------------------------- #
# the re-analysis
# --------------------------------------------------------------------------- #
@dataclass
class Relocated:
    lat: float
    lon: float
    server: Server | None
    analysis: TicketAnalysis
    rsrp: float | None = None            # dBm, measured at the point
    rsrp_note: str = ""
    description: str = ""
    tracks: list = field(default_factory=list)

    @property
    def sector_id(self) -> str:
        return self.server.sector_id if self.server else ""

    @property
    def key(self) -> str:
        """What the tracks are keyed by: the serving sector."""
        return self.sector_id


def lead_check(analysis: TicketAnalysis):
    lead = sorted([c for c in analysis.checks if c.sev > 0],
                  key=lambda c: (-c.sev, -c.breach_hours))
    return lead[0] if lead else None


def describe(server: Server | None, analysis: TicketAnalysis, rsrp: float | None) -> str:
    """The Description, from the re-analysis alone."""
    rsrp_txt = f"{rsrp:.1f} dBm" if rsrp is not None and np.isfinite(rsrp) else NA
    if server is None:
        return ("No on-air sector could be found near the user location. "
                f"The RSRP measurement at the user location is {rsrp_txt}.")
    return (f"The serving sector is {server.sector_id}, with a distance of "
            f"{fmt_metres(server.distance_m)} from the user location and an azimuth "
            f"difference of {server.az_diff_deg:.0f}°. {analysis.site_issue.rstrip('.')}. "
            f"The RSRP measurement at the user location is {rsrp_txt}.")


def reanalyse(lat: float, lon: float, problem_time, sectors: pd.DataFrame,
              sec_tracks, site_tracks, window_h: float, grids=None) -> Relocated:
    """The ticket again, at (lat, lon): its serving sector, that sector's KPIs
    in the correlation window, the measured RSRP at the point."""
    server = best_server(lat, lon, sectors)
    if server is None:
        analysis = TicketAnalysis(INSUFFICIENT, "Unknown", "",
                                  "No on-air sector near the user location", "", "Unknown",
                                  "", "", False, "", "", [])
        tracks = []
    else:
        tracks = tracks_for(server.sector_id, server.site_id, sec_tracks, site_tracks)
        analysis = analyse_ticket(server.sector_id, problem_time, tracks, window_h)
        if analysis.site_issue.startswith(f"Site {server.sector_id} "):
            analysis.site_issue = "Sector" + analysis.site_issue[4:]
    rsrp, note = None, ""
    if grids:
        from rfopt.sleep.analysis import rsrp_at
        p = rsrp_at(lat, lon, grids)
        if p is not None:
            rsrp = float(p.rsrp)
            note = f"{p.metres:.0f} m from the point, {p.mr:,} MRs"
        else:
            note = "no measured grid within 150 m of the point"
    else:
        note = "no coverage grid loaded"
    return Relocated(float(lat), float(lon), server, analysis, rsrp, note,
                     describe(server, analysis, rsrp), tracks)


def parse_latlon(text: str):
    """"30.5082, 47.7831" (either order) -> (lat, lon), or None."""
    nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", str(text or ""))]
    if len(nums) < 2:
        return None
    a, b = nums[0], nums[1]
    if 28.5 <= a <= 34 and 43 <= b <= 50:
        return a, b
    if 28.5 <= b <= 34 and 43 <= a <= 50:
        return b, a
    return None


__all__ = ["NA", "Relocated", "Server", "az_gap", "bearing", "best_server", "describe",
           "fmt_metres", "lead_check", "metres", "parse_latlon", "reanalyse", "sector_frame",
           "sector_of", "sector_tracks", "tracks_for"]
