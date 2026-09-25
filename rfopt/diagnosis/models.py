"""Diagnosis data model."""

from __future__ import annotations

from dataclasses import dataclass, field

# problem_class -> (human title, default RF domain)
PROBLEM_CLASSES: dict[str, str] = {
    "coverage": "Poor coverage / weak RSRP",
    "poor_rsrp": "Poor RSRP",
    "poor_rsrq": "Poor RSRQ / pilot pollution",
    "interference": "High interference (UL or DL)",
    "overshooting": "Cell overshooting",
    "high_utilization": "High utilisation / congestion",
    "congestion": "Congestion",
    "low_throughput": "Low throughput",
    "throughput": "Low throughput",
    "accessibility": "Accessibility degraded",
    "retainability": "Retainability degraded (drops)",
    "handover": "Handover problems",
    "availability": "Cell availability issue",
    "traffic_imbalance": "Traffic / load imbalance",
    "antenna_tilt": "Possible antenna / tilt problem",
}

_SEV_RANK = {"info": 0, "warning": 1, "critical": 2}


@dataclass
class Diagnosis:
    entity_level: str                 # cell | sector | site
    entity_id: str
    site_id: str
    technology: str
    problem_class: str
    title: str
    severity: str                     # "warning" | "critical"
    confidence: float                 # 0..1
    evidence: list[str] = field(default_factory=list)
    kpis: dict[str, float] = field(default_factory=dict)
    related_kpis: list[str] = field(default_factory=list)
    trend_note: str = ""
    when: str = ""
    period: str = "day"
    traffic_gb: float = 0.0
    tags: list[str] = field(default_factory=list)

    @property
    def severity_rank(self) -> int:
        return _SEV_RANK.get(self.severity, 1)

    @property
    def category(self) -> str:
        return self.problem_class

    def as_dict(self) -> dict:
        return {
            "level": self.entity_level, "entity": self.entity_id,
            "site": self.site_id, "tech": self.technology,
            "problem_class": self.problem_class, "title": self.title,
            "severity": self.severity, "confidence": round(self.confidence, 2),
            "traffic_gb": round(self.traffic_gb, 2),
            "when": self.when, "period": self.period,
            "trend": self.trend_note,
            "evidence": " | ".join(self.evidence),
            "tags": ",".join(self.tags),
        }

    def as_text(self) -> str:
        head = (f"[{self.severity.upper()}] {self.title}  "
                f"({self.entity_level} {self.entity_id}, conf "
                f"{self.confidence:.0%})")
        lines = [head]
        if self.trend_note:
            lines.append(f"  trend: {self.trend_note}")
        for e in self.evidence:
            lines.append(f"  - {e}")
        return "\n".join(lines)
