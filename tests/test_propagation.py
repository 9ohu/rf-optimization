import pytest

from rfopt.actions.propagation import (Environment, cost231_hata_pathloss_db,
                                       freespace_pathloss_db, predict_rsrp_dbm,
                                       rs_power_delta_for_target_db)


def test_pathloss_increases_with_distance():
    env = Environment(kind="urban", frequency_mhz=1800)
    assert (cost231_hata_pathloss_db(2000, env)
            > cost231_hata_pathloss_db(500, env))


def test_urban_worse_than_rural():
    near = Environment(kind="urban", frequency_mhz=1800)
    far = Environment(kind="rural", frequency_mhz=1800)
    assert cost231_hata_pathloss_db(3000, near) > cost231_hata_pathloss_db(3000, far)


def test_freespace_20log_decade():
    a = freespace_pathloss_db(1000, 1800)
    b = freespace_pathloss_db(10000, 1800)
    assert (b - a) == pytest.approx(20.0, abs=0.1)


def test_predict_rsrp_drops_with_distance():
    env = Environment()
    assert (predict_rsrp_dbm(300, env, total_tilt_deg=4)
            > predict_rsrp_dbm(3000, env, total_tilt_deg=4))


def test_rs_power_delta_capped_by_headroom():
    env = Environment(rs_power_up_headroom_db=3.0)
    out = rs_power_delta_for_target_db(-115, -100, env)
    assert out["applied_db"] == 3.0
    assert out["limited"] is True
    assert out["residual_db"] == pytest.approx(12.0, abs=0.1)


def test_rs_power_delta_none_needed_when_target_met():
    env = Environment()
    out = rs_power_delta_for_target_db(-95, -100, env)
    assert out["applied_db"] == 0.0
    assert out["limited"] is False
