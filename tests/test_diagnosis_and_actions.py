"""The injected problems in the sample set must be found and actioned."""

import pytest

EXPECT = {
    "R5-001_L2100_S1": "overshooting",
    "R5-003_L1800_S2": "coverage",
    "R5-005_L1800_S1": "interference",
    "R5-006_L1800_S3": "high_utilization",
    "R5-007_L2100_S2": "accessibility",
    "R5-008_L1800_S1": "availability",
    "R5-009_L2100_S3": "handover",
    "R5-002_L1800_S1": "traffic_imbalance",
}


def test_all_injected_problems_diagnosed(analysis):
    found = {(d.entity_id, d.problem_class) for d in analysis.diagnoses}
    for cell, pc in EXPECT.items():
        assert (cell, pc) in found, f"missing {pc} on {cell}"


def test_every_diagnosis_has_evidence(analysis):
    for d in analysis.diagnoses:
        assert d.evidence, d.title
        assert 0.0 < d.confidence <= 1.0


def test_recommendations_are_structured(analysis):
    assert analysis.recommendations
    for r in analysis.recommendations:
        assert r.problem and r.root_cause and r.action_type
        assert r.parameter and r.recommended_value
        assert r.monitor_kpis
        assert r.priority in {"P1", "P2", "P3", "P4"}
        assert 0.0 < r.confidence <= 1.0


def test_overshoot_recommends_more_downtilt(analysis):
    recs = [r for r in analysis.recommendations
            if r.cell_id == "R5-001_L2100_S1" and r.category == "overshooting"]
    assert recs, "no overshoot action for the overshooting cell"
    assert "tilt" in recs[0].action_type.lower()


def test_congestion_cell_gets_capacity_or_lb_action(analysis):
    recs = [r for r in analysis.recommendations
            if r.cell_id == "R5-006_L1800_S3"]
    kinds = " ".join(r.action_type.lower() for r in recs)
    assert "load balancing" in kinds or "capacity" in kinds


def test_no_spurious_overshoot_without_site_db(sample_data):
    from rfopt.pipeline import run_analysis
    r = run_analysis(str(sample_data / "r5_lte_kpi_hourly.xlsx"),
                     region_filter="R5", env_kind="urban")  # no site_db
    overs = [d for d in r.diagnoses if d.problem_class == "overshooting"]
    assert len(overs) <= 4, f"{len(overs)} overshoot diags without a site DB"


def test_complaint_reduce_ret_for_excessive_downtilt():
    from rfopt.actions.propagation import Environment
    from rfopt.actions.recommend import (CellContext, ComplaintContext,
                                         recommend_for_complaint)
    ctx = CellContext(cell_id="C", antenna_height_m=38.5, azimuth_deg=20,
                      mech_tilt_deg=0.0, elec_tilt_deg=7.0, ret_unit="tenths",
                      env=Environment(kind="suburban"))
    cc = ComplaintContext(cell=ctx, distance_m=1800, bearing_deg_from_site=30,
                          measured_rsrp_dbm=-114)
    rec = recommend_for_complaint(cc)
    assert "tilt" in rec.action_type.lower()
    assert "reduce" in rec.recommended_value.lower()
    assert "excessive downtilt" in rec.root_cause.lower()


def test_complaint_no_tilt_change_when_already_aligned():
    from rfopt.actions.propagation import Environment
    from rfopt.actions.recommend import (CellContext, ComplaintContext,
                                         recommend_for_complaint)
    ctx = CellContext(cell_id="C", antenna_height_m=25, azimuth_deg=0,
                      mech_tilt_deg=0.0, elec_tilt_deg=1.0,
                      env=Environment(kind="suburban"))
    cc = ComplaintContext(cell=ctx, distance_m=1500, bearing_deg_from_site=2,
                          measured_rsrp_dbm=-116)
    rec = recommend_for_complaint(cc)
    assert "power" in rec.action_type.lower() or "site" in rec.action_type.lower()
