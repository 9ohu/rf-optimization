"""KPI engine: thresholds, roll-ups, trend and anomaly detection."""

from rfopt.kpi.thresholds import (
    ThresholdSet,
    ThresholdRule,
    load_thresholds,
    Severity,
)
from rfopt.kpi.analyze import (
    aggregate,
    evaluate_thresholds,
    busy_hour_table,
    KpiBreach,
)
from rfopt.kpi.anomaly import detect_anomalies, TrendResult

__all__ = [
    "ThresholdSet",
    "ThresholdRule",
    "load_thresholds",
    "Severity",
    "aggregate",
    "evaluate_thresholds",
    "busy_hour_table",
    "KpiBreach",
    "detect_anomalies",
    "TrendResult",
]
