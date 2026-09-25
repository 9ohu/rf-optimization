"""Antenna geometry: distance, bearing, downtilt and vertical-pattern maths.

Conventions
-----------
* All tilts are **total downtilt below the horizon in degrees**
  (mechanical + electrical), positive pointing down.
* Antenna height ``h`` is height of the antenna centre above the *served*
  ground / UE plane, in metres.
* The vertical half-power beamwidth ``vbw`` defaults to 6.5 deg
  (typical panel antenna, ~14-18 dBi).

Ground distances
----------------
    boresight hits ground at        d_bore = h / tan(theta)
    far  3-dB edge hits ground at    d_far  = h / tan(theta - vbw/2)
    near 3-dB edge hits ground at    d_near = h / tan(theta + vbw/2)

``d_far`` is used as the practical coverage edge (3 dB down on the far side).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

_EARTH_R = 6371008.8      # mean Earth radius, m
DEFAULT_VBW = 6.5         # deg, vertical half-power beamwidth
DEFAULT_SLA = 25.0        # dB, side-lobe / pattern floor (3GPP-style)
UE_HEIGHT_M = 1.5


# --------------------------------------------------------------------------- #
# Spherical geometry
# --------------------------------------------------------------------------- #
def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
    return float(2 * _EARTH_R * math.asin(min(1.0, math.sqrt(a))))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2, degrees clockwise from north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def angular_offset_deg(azimuth: float, bearing: float) -> float:
    """Smallest absolute angle between a sector azimuth and a bearing (0-180)."""
    d = abs((azimuth - bearing + 180.0) % 360.0 - 180.0)
    return d


# --------------------------------------------------------------------------- #
# Vertical geometry
# --------------------------------------------------------------------------- #
def _tan_deg(a: float) -> float:
    return math.tan(math.radians(a))


def boresight_ground_distance_m(height_m: float, total_tilt_deg: float,
                                ue_height_m: float = UE_HEIGHT_M) -> float:
    h = max(height_m - ue_height_m, 1.0)
    t = max(total_tilt_deg, 0.05)
    return h / _tan_deg(t)


def coverage_edge_distance_m(height_m: float, total_tilt_deg: float,
                             vbw_deg: float = DEFAULT_VBW,
                             ue_height_m: float = UE_HEIGHT_M,
                             cap_m: float = 25000.0) -> float:
    """Distance where the far 3-dB edge of the vertical beam meets the ground.

    When the far 3-dB ray points at or above the horizon (very shallow tilt),
    the geometric distance is unbounded - we return ``cap_m`` to signal
    "effectively unlimited / horizon-limited".
    """
    h = max(height_m - ue_height_m, 1.0)
    ang = total_tilt_deg - vbw_deg / 2.0
    if ang <= 0.15:
        return cap_m
    return min(h / _tan_deg(ang), cap_m)


def elevation_angle_to_point_deg(height_m: float, distance_m: float,
                                 ue_height_m: float = UE_HEIGHT_M) -> float:
    """Down-angle from the antenna horizon to a point at ground distance d."""
    h = max(height_m - ue_height_m, 0.5)
    return math.degrees(math.atan2(h, max(distance_m, 1.0)))


def optimal_downtilt_deg(
    height_m: float,
    target_distance_m: float,
    *,
    vbw_deg: float = DEFAULT_VBW,
    mode: str = "edge",
    ue_height_m: float = UE_HEIGHT_M,
) -> float:
    """Recommended total downtilt to serve out to ``target_distance_m``.

    mode="edge"       put the far 3-dB edge at the target  (coverage to cell edge)
                      theta = atan(h/d) + vbw/2
    mode="boresight"  point the main lobe centre at the target (targeted fix)
                      theta = atan(h/d)
    """
    base = elevation_angle_to_point_deg(height_m, target_distance_m, ue_height_m)
    theta = base + (vbw_deg / 2.0 if mode == "edge" else 0.0)
    return round(max(0.0, min(theta, 16.0)), 1)


def vertical_pattern_gain_db(offset_deg: float, vbw_deg: float = DEFAULT_VBW,
                             sla_db: float = DEFAULT_SLA) -> float:
    """Relative vertical pattern gain at an angular offset from boresight.

    3GPP-style parabola:  G(x) = -min(12 (x / vbw)^2, SLA)  [dB, <= 0]
    """
    return -min(12.0 * (offset_deg / vbw_deg) ** 2, sla_db)


def tilt_gain_delta_db(
    height_m: float,
    target_distance_m: float,
    current_tilt_deg: float,
    proposed_tilt_deg: float,
    *,
    vbw_deg: float = DEFAULT_VBW,
    sla_db: float = DEFAULT_SLA,
    ue_height_m: float = UE_HEIGHT_M,
) -> float:
    """Change in vertical-pattern gain toward a target if tilt is changed.

    Positive => the proposed tilt delivers *more* energy toward the target.
    """
    elev = elevation_angle_to_point_deg(height_m, target_distance_m, ue_height_m)
    g_cur = vertical_pattern_gain_db(abs(elev - current_tilt_deg), vbw_deg, sla_db)
    g_new = vertical_pattern_gain_db(abs(elev - proposed_tilt_deg), vbw_deg, sla_db)
    return round(g_new - g_cur, 2)


# --------------------------------------------------------------------------- #
@dataclass
class AntennaGeometry:
    """Bundle of the geometry outputs for one cell, ready to explain."""
    height_m: float
    azimuth_deg: float
    mech_tilt_deg: float
    elec_tilt_deg: float
    vbw_deg: float = DEFAULT_VBW

    @property
    def total_tilt_deg(self) -> float:
        return self.mech_tilt_deg + self.elec_tilt_deg

    def boresight_distance_m(self) -> float:
        return boresight_ground_distance_m(self.height_m, self.total_tilt_deg)

    def coverage_edge_m(self) -> float:
        return coverage_edge_distance_m(self.height_m, self.total_tilt_deg,
                                        self.vbw_deg)

    def describe(self) -> dict:
        return {
            "height_m": self.height_m,
            "azimuth_deg": self.azimuth_deg,
            "mech_tilt_deg": self.mech_tilt_deg,
            "elec_tilt_deg": self.elec_tilt_deg,
            "total_tilt_deg": round(self.total_tilt_deg, 1),
            "boresight_distance_m": round(self.boresight_distance_m()),
            "coverage_edge_m": round(self.coverage_edge_m()),
        }
