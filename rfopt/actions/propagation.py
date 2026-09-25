"""Link-budget and path-loss helpers for coverage / RS-power reasoning.

These are planning-grade approximations (COST-231 Hata / free space), good for
*relative* reasoning - "how many dB do we need" and "will a tilt/power change
plausibly close the gap" - not for absolute prediction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from rfopt.actions.geometry import (
    UE_HEIGHT_M, elevation_angle_to_point_deg, vertical_pattern_gain_db,
    DEFAULT_VBW,
)


@dataclass
class Environment:
    kind: str = "urban"            # urban | suburban | rural
    frequency_mhz: float = 1800.0
    # RS EPRE (dBm per resource element) at the antenna connector.
    # 20 MHz / 2x40 W ~ 15.2 dBm/RE; keep configurable per cell.
    rs_epre_dbm: float = 15.2
    tx_antenna_gain_dbi: float = 17.0
    cable_loss_db: float = 1.5
    ue_antenna_gain_dbi: float = 0.0
    body_loss_db: float = 3.0
    indoor_loss_db: float = 0.0    # add when the complaint is indoor
    # Practical EPRE head-room before PDSCH power sharing / PA limits bite.
    rs_power_up_headroom_db: float = 3.0
    rs_power_down_headroom_db: float = 6.0


def freespace_pathloss_db(distance_m: float, frequency_mhz: float) -> float:
    d_km = max(distance_m, 1.0) / 1000.0
    return 32.44 + 20 * math.log10(d_km) + 20 * math.log10(frequency_mhz)


def cost231_hata_pathloss_db(
    distance_m: float,
    env: Environment,
    *,
    bs_height_m: float = 30.0,
    ue_height_m: float = UE_HEIGHT_M,
) -> float:
    """COST-231 Hata median path loss.

    Valid roughly for f 1500-2000 MHz, hb 30-200 m, d 1-20 km - we clamp and
    still use it below 1 km for relative reasoning.
    """
    f = min(max(env.frequency_mhz, 150.0), 2600.0)
    hb = min(max(bs_height_m, 20.0), 200.0)
    hm = min(max(ue_height_m, 1.0), 10.0)
    d_km = max(distance_m, 20.0) / 1000.0

    a_hm = (1.1 * math.log10(f) - 0.7) * hm - (1.56 * math.log10(f) - 0.8)
    c = 3.0 if env.kind == "urban" else 0.0
    L = (46.3 + 33.9 * math.log10(f) - 13.82 * math.log10(hb) - a_hm
         + (44.9 - 6.55 * math.log10(hb)) * math.log10(d_km) + c)
    if env.kind == "suburban":
        L -= 2 * (math.log10(f / 28.0)) ** 2 + 5.4
    elif env.kind == "rural":
        L -= 4.78 * (math.log10(f)) ** 2 - 18.33 * math.log10(f) + 40.94
    return L


def predict_rsrp_dbm(
    distance_m: float,
    env: Environment,
    *,
    bs_height_m: float = 30.0,
    total_tilt_deg: float = 4.0,
    az_offset_deg: float = 0.0,
    h_bw_deg: float = 65.0,
    vbw_deg: float = DEFAULT_VBW,
    model: str = "cost231",
) -> float:
    """Very rough serving RSRP estimate at a ground point."""
    if model == "freespace":
        pl = freespace_pathloss_db(distance_m, env.frequency_mhz)
    else:
        pl = cost231_hata_pathloss_db(distance_m, env, bs_height_m=bs_height_m)

    elev = elevation_angle_to_point_deg(bs_height_m, distance_m)
    v_gain = vertical_pattern_gain_db(abs(elev - total_tilt_deg), vbw_deg)
    h_gain = -min(12.0 * (az_offset_deg / h_bw_deg) ** 2, 25.0)

    rsrp = (env.rs_epre_dbm + env.tx_antenna_gain_dbi - env.cable_loss_db
            + v_gain + h_gain
            + env.ue_antenna_gain_dbi - env.body_loss_db - env.indoor_loss_db
            - pl)
    return round(rsrp, 1)


def rs_power_delta_for_target_db(
    current_rsrp_dbm: float,
    target_rsrp_dbm: float,
    env: Environment,
    *,
    tilt_gain_db: float = 0.0,
) -> dict:
    """How much RS-power change is needed / possible to hit a target RSRP.

    ``tilt_gain_db`` is any improvement already credited to a tilt change, so
    power only has to cover the remainder.
    """
    gap = target_rsrp_dbm - current_rsrp_dbm - tilt_gain_db
    if gap <= 0:
        return {"needed_db": round(gap, 1), "applied_db": 0.0,
                "residual_db": round(gap, 1), "limited": False,
                "note": "Target already met by geometry / tilt change."}
    applied = min(gap, env.rs_power_up_headroom_db)
    return {
        "needed_db": round(gap, 1),
        "applied_db": round(applied, 1),
        "residual_db": round(gap - applied, 1),
        "limited": applied < gap,
        "note": ("RS-power boost alone cannot close the gap; combine with tilt / "
                 "azimuth or a coverage layer."
                 if applied < gap else
                 "RS-power boost can close the remaining gap."),
    }


def inter_site_distance_hint_m(env_kind: str) -> float:
    return {"urban": 500.0, "suburban": 1200.0, "rural": 3500.0}.get(env_kind, 1000.0)
