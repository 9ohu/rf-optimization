"""AI narrative layer.

The deterministic engine (diagnosis + actions) is always the source of truth.
This layer only *phrases* the result as an RF-optimisation engineer would:

  * ``TemplateNarrator``  - offline, no dependencies, structured prose.
  * ``ClaudeNarrator``    - optional; uses the Anthropic API when a key is set,
                            constrained to the numbers the engine produced.

``build_narrator(...)`` picks Claude if it is available and configured, else
the template narrator.
"""

from rfopt.ai.narrative import (
    Narrative,
    TemplateNarrator,
    ClaudeNarrator,
    build_narrator,
    narrate_case,
)

__all__ = [
    "Narrative", "TemplateNarrator", "ClaudeNarrator", "build_narrator",
    "narrate_case",
]
