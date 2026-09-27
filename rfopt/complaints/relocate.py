"""A ticket re-analysed at the subscriber's actual location.

Without a location the Daily Target ticket is analysed per site, on every cell
of the site (`correlate.analyse_ticket`). Once the subscriber's latitude /
longitude is approved on the Sites map:

* **The site is the ticket's own.** A valid Site ID in the ticket is used as
  it is; its serving sector is the sector of that site facing the user
  location (the smallest gap between its azimuth and the bearing from the
  site to the user, `facing_sector`). Only when the ticket carries no Site ID
  (empty or 0) does the distance × off-boresight equation pick the serving
  sector among the on-air sectors around the point (`best_server`) — and it
  is used for nothing else.
* **KPIs on the serving sector:** every cell of it, on the same correlation
  window (`tracks_for`, falling back to the site's own track for a KPI the
  export measures per site, flow control always).
* **RSRP from the grid alone:** the grid cell the user location falls in
  (`rfopt.geo.coverage.grid_cell_rsrp`) — independent of the serving sector.
  A location with no grid, or RSRP at or below the poor line, is a Coverage
  Issue in the result; with good coverage the KPI analysis decides.
* **Neighbour sectors:** a few sectors of other sites around the user
  location (`neighbour_sectors`), each analysed on all of its cells —
  context around the user, not part of the verdict.

Nothing is invented: a value the data does not carry reads "N/A".
"""

from __future__ import annotations

import dataclasses
import math
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from rfopt.complaints.correlate import (INSUFFICIENT, MAIN, TicketAnalysis, analyse_ticket,
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
        # a site-level KPI (flow control) is never read per sector
        if tr.level != "site" and sector_id in tr.by_site:
            out.append(tr.only(sector_id))
            have.add((tr.kind, tr.canon))
    for tr in site_tracks or []:
        if (tr.kind, tr.canon) not in have and site_id in tr.by_site:
            out.append(tr.only(site_id, sector_id))
    return out


# --------------------------------------------------------------------------- #
# the re-analysis
# --------------------------------------------------------------------------- #
NEIGHBOURS = 3              # neighbour sectors analysed around a user location
BEST, TICKET = "best server", "ticket site"


def ticket_site(value) -> str:
    """The ticket's Site ID, or "" when it names none (empty, 0, not an ID)."""
    v = "" if value is None else str(value).strip().upper()
    if v in ("", "0", "NAN", "NONE", "NA", "N/A", "NOT AVAILABLE"):
        return ""
    m = re.search(r"[A-Z]{2,4}\d{3,6}", v)
    return m.group(0) if m else ""


def facing_sector(lat: float, lon: float, site_sectors: pd.DataFrame) -> Server | None:
    """The sector of one site facing (lat, lon): the smallest gap between its
    azimuth and the bearing from the site to the point."""
    need = ["latitude", "longitude", "azimuth_deg"]
    s = (site_sectors.dropna(subset=need)
         if site_sectors is not None and set(need) <= set(site_sectors.columns)
         else pd.DataFrame())
    if s.empty or not (np.isfinite(lat) and np.isfinite(lon)):
        return None
    best = None
    for r in s.itertuples(index=False):
        brg = bearing(r.latitude, r.longitude, lat, lon)
        off = az_gap(r.azimuth_deg, brg)
        if best is None or off < best.az_diff_deg:
            best = Server(str(r.sector_id), str(r.site_id),
                          metres(r.latitude, r.longitude, lat, lon), float(brg),
                          float(r.azimuth_deg), float(off))
    return best


def neighbour_sectors(lat: float, lon: float, sectors: pd.DataFrame, serving_site: str,
                      *, most: int = NEIGHBOURS, nearest_sites: int = 6) -> list[Server]:
    """A few sectors of other sites around (lat, lon): among the nearest sites,
    each site's sector best pointed at the point (the Sites map's distance ×
    off-boresight score), the best `most` of them — one per site."""
    if sectors is None or len(sectors) == 0 or not (np.isfinite(lat) and np.isfinite(lon)):
        return []
    s = sectors.dropna(subset=["latitude", "longitude", "azimuth_deg"])
    s = s[s["site_id"].astype(str).str.upper() != str(serving_site).upper()]
    if s.empty:
        return []
    coslat = math.cos(math.radians(lat))
    d = np.hypot((s["latitude"].to_numpy(float) - lat) * _M_PER_DEG,
                 (s["longitude"].to_numpy(float) - lon) * _M_PER_DEG * coslat)
    s = s.assign(dist_m=d)
    near = s.groupby("site_id")["dist_m"].min().nsmallest(nearest_sites).index
    per_site: dict = {}
    for r in s[s["site_id"].isin(near)].itertuples(index=False):
        brg = bearing(r.latitude, r.longitude, lat, lon)
        off = az_gap(r.azimuth_deg, brg)
        score = r.dist_m * (1.0 + (off / 65.0) ** 2)
        if r.site_id not in per_site or score < per_site[r.site_id][0]:
            per_site[r.site_id] = (score, Server(str(r.sector_id), str(r.site_id),
                                                 float(r.dist_m), float(brg),
                                                 float(r.azimuth_deg), float(off)))
    return [srv for _, srv in sorted(per_site.values(), key=lambda x: x[0])[:most]]


@dataclass
class Neighbour:
    server: Server
    analysis: TicketAnalysis
    tracks: list = field(default_factory=list)

    @property
    def sector_id(self) -> str:
        return self.server.sector_id


@dataclass
class Relocated:
    lat: float
    lon: float
    server: Server | None
    analysis: TicketAnalysis
    rsrp: float | None = None            # dBm, the grid cell at the point
    rsrp_note: str = ""
    description: str = ""
    tracks: list = field(default_factory=list)
    how: str = ""                        # TICKET: the ticket's site · BEST: the equation
    no_grid: bool = False                # coverage loaded, no grid at the point
    coverage_issue: bool = False
    neighbours: list = field(default_factory=list)

    @property
    def sector_id(self) -> str:
        return self.server.sector_id if self.server else ""

    @property
    def key(self) -> str:
        """What the tracks are keyed by: the serving sector."""
        return self.sector_id


def lead_check(analysis: TicketAnalysis):
    lead = sorted([c for c in analysis.checks if c.sev > 0],
                  key=lambda c: (-c.sev, c.canon not in MAIN, -c.breach_hours))
    return lead[0] if lead else None


def describe(server: Server | None, analysis: TicketAnalysis, rsrp: float | None,
             how: str = "", no_grid: bool = False) -> str:
    """The Description, from the re-analysis alone."""
    rsrp_txt = (f"{rsrp:.1f} dBm" if rsrp is not None and np.isfinite(rsrp)
                else "not available: no RSRP grid at the user location" if no_grid else NA)
    if server is None:
        return (f"{analysis.site_issue.rstrip('.')}. "
                f"The RSRP measurement at the user location is {rsrp_txt}.")
    chosen = (" (the ticket names no site: the best server by distance and azimuth)"
              if how == BEST else "")
    return (f"The serving sector is {server.sector_id}{chosen}, with a distance of "
            f"{fmt_metres(server.distance_m)} from the user location and an azimuth "
            f"difference of {server.az_diff_deg:.0f}°. {analysis.site_issue.rstrip('.')}. "
            f"The RSRP measurement at the user location is {rsrp_txt}.")


def _with_coverage(analysis: TicketAnalysis, rsrp: float | None, line: float,
                   no_grid: bool) -> TicketAnalysis:
    """Poor coverage at the user location is a complaint issue of its own: a
    Coverage Issue. The KPI checks stay as they are, shown beside it."""
    from rfopt.complaints.correlate import TECHNICAL, UNKNOWN
    what = ("No RSRP grid at the user location — Coverage Issue" if no_grid else
            f"Poor coverage at the user location — RSRP {rsrp:.1f} dBm "
            f"(at or below {line:g} dBm) — Coverage Issue")
    kpi = (f"; KPI: {analysis.site_issue}" if analysis.problem_detected in ("Yes", "Possible")
           else "")
    return dataclasses.replace(
        analysis, classification=TECHNICAL, problem_detected="Yes", problem_type="Coverage",
        site_issue=what + kpi,
        evidence="; ".join(x for x in (what, analysis.evidence) if x),
        resolution=(analysis.resolution if analysis.problem_detected in ("Yes", "Possible")
                    else UNKNOWN),
        resolution_evidence=(analysis.resolution_evidence
                             or "coverage is read from the RSRP grid, which has no hours"),
        manual_check=False)


def _sector_analysis(server: Server, problem_time, sec_tracks, site_tracks, window_h):
    tracks = tracks_for(server.sector_id, server.site_id, sec_tracks, site_tracks)
    analysis = analyse_ticket(server.sector_id, problem_time, tracks, window_h)
    if analysis.site_issue.startswith(f"Site {server.sector_id} "):
        analysis.site_issue = "Sector" + analysis.site_issue[4:]
    return analysis, tracks


def reanalyse(lat: float, lon: float, problem_time, sectors: pd.DataFrame,
              sec_tracks, site_tracks, window_h: float, grids=None, *,
              site_id=None, site_sectors: pd.DataFrame | None = None) -> Relocated:
    """The ticket again, at (lat, lon).

    `site_id`: the ticket's Site ID. When it names a site, its sector facing
    the point (among `site_sectors`, the site's sectors in the KMZ) serves; when
    it names none, the best server among the on-air `sectors`. Then that
    sector's KPIs on every cell, the RSRP of the grid cell at the point, and a
    few neighbour sectors, each on every cell."""
    site = ticket_site(site_id)
    if site:
        pool = site_sectors if site_sectors is not None else sectors
        mine = (pool[pool["site_id"].astype(str).str.upper() == site]
                if pool is not None and len(pool) else pd.DataFrame())
        server, how = facing_sector(lat, lon, mine), TICKET
        missing = f"Site {site} has no sector with an azimuth in the site KMZ"
    else:
        server, how = best_server(lat, lon, sectors), BEST
        missing = "No on-air sector near the user location"
    if server is None:
        analysis = TicketAnalysis(INSUFFICIENT, "Unknown", "", missing, "", "Unknown",
                                  "", "", False, "", "", [])
        tracks = []
    else:
        analysis, tracks = _sector_analysis(server, problem_time, sec_tracks, site_tracks,
                                            window_h)
    # RSRP: the grid cell the user location falls in — never the serving sector
    from rfopt.geo.coverage import grid_cell_rsrp, poor_rsrp_line
    rsrp, note, no_grid, issue = None, "", False, False
    if grids:
        cell = grid_cell_rsrp(lat, lon, grids)
        line = poor_rsrp_line()
        if cell is not None:
            rsrp = float(cell.rsrp)
            note = f"grid cell of {cell.cell_m:.0f} m, {cell.mr:,.0f} MRs"
            issue = rsrp <= line
        else:
            no_grid = issue = True
            note = "no RSRP grid at the user location"
        if issue:
            analysis = _with_coverage(analysis, rsrp, line, no_grid)
    else:
        note = "no coverage grid loaded"
    neighbours = []
    if server is not None:
        for nb in neighbour_sectors(lat, lon, sectors, server.site_id):
            a, t = _sector_analysis(nb, problem_time, sec_tracks, site_tracks, window_h)
            neighbours.append(Neighbour(nb, a, t))
    return Relocated(float(lat), float(lon), server, analysis, rsrp, note,
                     describe(server, analysis, rsrp, how, no_grid), tracks, how, no_grid,
                     issue, neighbours)


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


__all__ = ["BEST", "NA", "NEIGHBOURS", "Neighbour", "Relocated", "Server", "TICKET", "az_gap",
           "bearing", "best_server", "describe", "facing_sector", "fmt_metres", "lead_check",
           "metres", "neighbour_sectors", "parse_latlon", "reanalyse", "sector_frame",
           "sector_of", "sector_tracks", "ticket_site", "tracks_for"]
