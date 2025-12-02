"""System prompt generation from AudienceConfig."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scoring.llm.types import AudienceConfig


def build_system_prompt(audience: AudienceConfig) -> str:
    """Build the system prompt from audience configuration.

    The system prompt defines the classifier role, target profile,
    signals, and expected output format.

    Args:
        audience: Audience configuration with signals and context.

    Returns:
        System prompt string for the LLM.
    """
    high_signals = "\n".join(f"  - {s}" for s in audience.high_signals)
    low_signals = "\n".join(f"  - {s}" for s in audience.low_signals)

    return f"""You are a profile classifier for the {audience.sector} sector.

TARGET PROFILE:
{audience.target_profile}

DOMAIN CONTEXT:
{audience.domain_context}

HIGH signals (return true):
{high_signals}

LOW signals (return false):
{low_signals}

INSTRUCTIONS:
- Analyze each profile provided by the user in TOON format
- Return a JSON array with one object per profile
- Each object must have: handle, label, reason
- label: true (matches target), false (does not match), null (uncertain)
- reason: 1-2 sentence explanation (max 20 words)
- Be conservative: when uncertain, return null

OUTPUT FORMAT:
[
  {{"handle": string, "label": boolean|null, "reason": string}}
]

Return ONLY the JSON array, no other text."""
