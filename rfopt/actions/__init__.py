"""Physics / geometry engine for computed optimisation recommendations."""

from rfopt.actions.geometry import (
    haversine_m,
    bearing_deg,
    angular_offset_deg,
    boresight_ground_distance_m,
    coverage_edge_distance_m,
    optimal_downtilt_deg,
    vertical_pattern_gain_db,
    tilt_gain_delta_db,
    AntennaGeometry,
)
from rfopt.actions.propagation import (
    cost231_hata_pathloss_db,
    freespace_pathloss_db,
    predict_rsrp_dbm,
    rs_power_delta_for_target_db,
    Environment,
)
from rfopt.actions.recommend import (
    recommend_for_cell,
    recommend_for_complaint,
    Recommendation,
    CellContext,
    ComplaintContext,
)

__all__ = [
    "haversine_m", "bearing_deg", "angular_offset_deg",
    "boresight_ground_distance_m", "coverage_edge_distance_m",
    "optimal_downtilt_deg", "vertical_pattern_gain_db", "tilt_gain_delta_db",
    "AntennaGeometry", "cost231_hata_pathloss_db", "freespace_pathloss_db",
    "predict_rsrp_dbm", "rs_power_delta_for_target_db", "Environment",
    "recommend_for_cell", "recommend_for_complaint", "Recommendation",
    "CellContext", "ComplaintContext",
]
