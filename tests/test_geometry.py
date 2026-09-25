import math

import pytest

from rfopt.actions.geometry import (angular_offset_deg, bearing_deg,
                                    boresight_ground_distance_m,
                                    coverage_edge_distance_m,
                                    elevation_angle_to_point_deg, haversine_m,
                                    optimal_downtilt_deg, tilt_gain_delta_db,
                                    vertical_pattern_gain_db)


def test_haversine_known_distance():
    # ~111.2 km per degree of latitude
    d = haversine_m(0.0, 0.0, 1.0, 0.0)
    assert 110_000 < d < 112_000


def test_bearing_cardinal():
    assert bearing_deg(0, 0, 1, 0) == pytest.approx(0, abs=1)      # north
    assert bearing_deg(0, 0, 0, 1) == pytest.approx(90, abs=1)     # east


def test_angular_offset_wraps():
    assert angular_offset_deg(10, 350) == pytest.approx(20)
    assert angular_offset_deg(350, 10) == pytest.approx(20)
    assert angular_offset_deg(0, 180) == pytest.approx(180)


def test_boresight_distance_matches_tan():
    d = boresight_ground_distance_m(30.0, 3.0, ue_height_m=0.0)
    assert d == pytest.approx(30 / math.tan(math.radians(3.0)), rel=1e-6)


def test_more_downtilt_pulls_coverage_in():
    near = coverage_edge_distance_m(30, 8.0)
    far = coverage_edge_distance_m(30, 3.0)
    assert near < far


def test_optimal_downtilt_points_at_target():
    # boresight mode: tilt should equal the elevation angle to the target
    h, d = 30.0, 1000.0
    t = optimal_downtilt_deg(h, d, mode="boresight")
    assert t == pytest.approx(elevation_angle_to_point_deg(h, d), abs=0.15)


def test_optimal_downtilt_edge_is_steeper_than_boresight():
    assert (optimal_downtilt_deg(30, 1200, mode="edge")
            > optimal_downtilt_deg(30, 1200, mode="boresight"))


def test_vertical_pattern_gain_peak_and_floor():
    assert vertical_pattern_gain_db(0.0) == pytest.approx(0.0)
    assert vertical_pattern_gain_db(90.0) == pytest.approx(-25.0)  # SLA floor
    assert vertical_pattern_gain_db(3.25, vbw_deg=6.5) == pytest.approx(-3.0, abs=0.1)


def test_tilt_gain_delta_sign_when_overtilted():
    # antenna at 40 m, target at 1800 m -> elevation ~1.3 deg.
    # currently at 7 deg (over-tilted): reducing toward ~4 deg gains energy.
    gain = tilt_gain_delta_db(40, 1800, current_tilt_deg=7.0,
                              proposed_tilt_deg=4.0)
    assert gain > 3.0
