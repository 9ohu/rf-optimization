"""Turn a Diagnosis + Recommendation into an RF-engineer-style write-up."""

from __future__ import annotations

import json
import os
import textwrap
from dataclasses import dataclass, field

from rfopt.actions.recommend import Recommendation
from rfopt.diagnosis.models import Diagnosis

_SECTIONS = [
    "summary", "why_it_happens", "confirming_kpis", "recommended_action",
    "why_this_action", "parameter_change", "expected_impact", "risks",
    "monitor_after", "rollback",
]


@dataclass
class Narrative:
    entity_id: str
    problem: str
    source: str                       # "template" | "claude"
    sections: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0
    priority: str = "P3"

    def as_markdown(self) -> str:
        titles = {
            "summary": "Summary",
            "why_it_happens": "Why this is happening",
            "confirming_kpis": "KPIs that confirm the issue",
            "recommended_action": "Recommended action",
            "why_this_action": "Why this action",
            "parameter_change": "Parameter / value to change",
            "expected_impact": "Expected impact",
            "risks": "Risks / side effects",
            "monitor_after": "KPIs to monitor after the change",
            "rollback": "Rollback / iteration plan",
        }
        out = [f"### {self.entity_id} - {self.problem}",
               f"*Priority {self.priority} | engine confidence "
               f"{self.confidence:.0%} | narrative: {self.source}*", ""]
        for key in _SECTIONS:
            if self.sections.get(key):
                out.append(f"**{titles[key]}**")
                out.append(self.sections[key].strip())
                out.append("")
        return "\n".join(out)

    def as_text(self) -> str:
        md = self.as_markdown()
        return md.replace("### ", "").replace("**", "").replace("*", "")


# --------------------------------------------------------------------------- #
def _case_facts(diag: Diagnosis, rec: Recommendation) -> dict:
    return {
        "entity": rec.cell_id or diag.entity_id,
        "site": rec.site_id or diag.site_id,
        "technology": diag.technology,
        "problem": rec.problem or diag.title,
        "problem_class": rec.category,
        "severity": diag.severity,
        "engine_confidence": round(rec.confidence, 2),
        "priority": rec.priority,
        "root_cause": rec.root_cause,
        "diagnosis_evidence": diag.evidence,
        "recommendation_evidence": rec.evidence,
        "trend": diag.trend_note,
        "action_type": rec.action_type,
        "parameter": rec.parameter,
        "current_value": rec.current_value,
        "recommended_value": rec.recommended_value,
        "calculation": rec.math_notes,
        "rationale": rec.rationale,
        "expected_impact": rec.expected_impact,
        "risks": rec.risks,
        "monitor_kpis": rec.monitor_kpis,
        "alternatives": rec.alternatives,
    }


# --------------------------------------------------------------------------- #
class TemplateNarrator:
    """Offline, deterministic prose - no LLM, no network."""

    source = "template"

    def narrate(self, diag: Diagnosis, rec: Recommendation) -> Narrative:
        f = _case_facts(diag, rec)
        ev = f["diagnosis_evidence"] or f["recommendation_evidence"]
        trend = f" The trend is also unfavourable: {f['trend']}." if f["trend"] else ""

        summary = (
            f"{f['entity']} ({f['technology']}, site {f['site']}) shows "
            f"{f['problem'].lower()} at {f['severity']} level. "
            f"The engine's assessment is that this is "
            f"{'a ' if not f['root_cause'][:1].islower() else ''}"
            f"{f['root_cause'][0].lower() + f['root_cause'][1:]}"
            f"{trend} Priority {f['priority']}, confidence "
            f"{f['engine_confidence']:.0%}."
        )

        why = f["root_cause"]
        if f["calculation"]:
            why += ("\n\nThe supporting geometry / link-budget maths:\n"
                    + "\n".join(f"- {m}" for m in f["calculation"]))

        confirming = ("The following KPIs corroborate the diagnosis (each is a "
                      "measured value, not an assumption):\n"
                      + "\n".join(f"- {e}" for e in ev))

        rec_action = (f"{f['action_type']}.\n\n"
                      f"{f['recommended_value']}")

        why_action = (f["rationale"]
                      + "\n\nThis is chosen over the alternatives because the "
                      "corroborating KPIs point specifically at this mechanism; "
                      "acting on a different lever would not move the KPIs that "
                      "are actually breached.")
        if f["alternatives"]:
            why_action += ("\n\nAlternatives kept in reserve:\n"
                           + "\n".join(f"- {a}" for a in f["alternatives"]))

        param = (f"Parameter: {f['parameter']}\n"
                 f"Current:   {f['current_value']}\n"
                 f"Target:    {f['recommended_value']}")

        impact = f["expected_impact"]
        risks = "\n".join(f"- {r}" for r in f["risks"])
        monitor = (", ".join(f["monitor_kpis"])
                   + ".\n\nBaseline these for at least 3-7 days before the "
                   "change and compare the same weekday/hour profile after, so "
                   "traffic variation is not mistaken for an effect.")
        rollback = (
            "The change is parameter-only and reversible. If, after 24-72 h, "
            "the primary KPI has not improved by a meaningful margin, or any "
            "guard KPI (drops, RSRQ, HO success, PRB on neighbours) regresses "
            "beyond its threshold, revert to the recorded current value and "
            "re-evaluate with the alternatives. Apply in one capped step and "
            "iterate rather than making the full theoretical change at once."
        )

        return Narrative(
            entity_id=f["entity"], problem=f["problem"], source=self.source,
            confidence=f["engine_confidence"], priority=f["priority"],
            sections={
                "summary": summary, "why_it_happens": why,
                "confirming_kpis": confirming, "recommended_action": rec_action,
                "why_this_action": why_action, "parameter_change": param,
                "expected_impact": impact, "risks": risks,
                "monitor_after": monitor, "rollback": rollback,
            },
        )


# --------------------------------------------------------------------------- #
_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a senior RF optimisation engineer reviewing an automated analysis.
    A deterministic engine has already done the maths and produced a diagnosis
    and a recommended action, given to you as JSON.

    Your job is ONLY to explain it the way an experienced engineer would in a
    change record: clear, specific, and honest about uncertainty. You must:
      * Use only the numbers present in the JSON. Never invent KPI values,
        distances, tilts, or targets.
      * Keep the engine's recommended parameter and target value exactly as
        given. You may explain them; you may not change them.
      * If the evidence is thin, say so.
      * Be concise. No marketing language.

    Return a JSON object with these string fields:
      summary, why_it_happens, confirming_kpis, recommended_action,
      why_this_action, parameter_change, expected_impact, risks,
      monitor_after, rollback
    """)


class ClaudeNarrator:
    """Optional LLM phrasing via the Anthropic API. Falls back on any error."""

    source = "claude"

    def __init__(self, api_key: str | None = None,
                 model: str = "claude-sonnet-5", max_tokens: int = 1500):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.model = model
        self.max_tokens = max_tokens
        self._fallback = TemplateNarrator()
        self._client = None
        if self.api_key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self.api_key)
            except Exception:
                self._client = None

    @property
    def available(self) -> bool:
        return self._client is not None

    def narrate(self, diag: Diagnosis, rec: Recommendation) -> Narrative:
        if not self.available:
            return self._fallback.narrate(diag, rec)
        facts = _case_facts(diag, rec)
        try:
            msg = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content":
                           "Engine output:\n```json\n"
                           + json.dumps(facts, indent=2, default=str)
                           + "\n```\nReturn only the JSON object."}],
            )
            text = "".join(getattr(b, "text", "") for b in msg.content)
            text = text[text.find("{"): text.rfind("}") + 1]
            data = json.loads(text)
            sections = {k: str(data.get(k, "")).strip() for k in _SECTIONS}
            if not sections.get("summary"):
                raise ValueError("empty narrative")
            return Narrative(
                entity_id=facts["entity"], problem=facts["problem"],
                source=self.source, confidence=facts["engine_confidence"],
                priority=facts["priority"], sections=sections,
            )
        except Exception:
            n = self._fallback.narrate(diag, rec)
            n.source = "template (Claude fallback)"
            return n


# --------------------------------------------------------------------------- #
def build_narrator(use_ai: bool = True, api_key: str | None = None,
                   model: str = "claude-sonnet-5"):
    if use_ai and (api_key or os.environ.get("ANTHROPIC_API_KEY")):
        c = ClaudeNarrator(api_key=api_key, model=model)
        if c.available:
            return c
    return TemplateNarrator()


def narrate_case(diag: Diagnosis, rec: Recommendation, *, use_ai: bool = False,
                 api_key: str | None = None) -> Narrative:
    return build_narrator(use_ai, api_key).narrate(diag, rec)
