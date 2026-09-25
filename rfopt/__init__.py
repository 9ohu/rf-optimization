"""rfopt - Intelligent RF Optimization Tool.

A KPI-driven RF optimization assistant for 2G/3G/4G (LTE-focused) networks.

Pipeline
--------
    ingest   -> load Excel/CSV, map vendor counters to a canonical schema
    kpi      -> threshold checks, trend / anomaly detection, roll-ups
    diagnosis-> rule engine: KPI patterns  -> RF problem classes + evidence
    actions  -> physics/geometry engine: computed RET / power / tilt / azimuth /
                load-balancing / neighbour / capacity recommendations
    ai       -> optional Claude layer: RF-engineer-style narrative
    geo      -> KMZ generation (sector beams, overshoot, health colouring)
    reports  -> annotated Excel / HTML output

The diagnosis and action engines are fully deterministic and work offline.
The AI layer is a narrative wrapper and never the source of truth.
"""

__version__ = "0.1.0"
