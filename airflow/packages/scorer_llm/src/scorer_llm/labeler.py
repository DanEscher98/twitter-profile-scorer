"""LLM labeling with multi-provider support via LangChain."""

from __future__ import annotations

import json

from pydantic import ValidationError

from scorer_llm.registry import ModelConfig, get_registry
from scorer_llm.types import AudienceConfig, LabelItem, ProfileToLabel
from scorer_utils import get_logger

log = get_logger("scorer_llm.labeler")


def _build_prompt(profiles: list[ProfileToLabel], audience: AudienceConfig) -> str:
    """Build the labeling prompt with profiles in TOON format.

    TOON format is a simplified structured text format that's easy
    for LLMs to parse while being token-efficient.
    """
    # Build profile list in TOON format
    profiles_toon = []
    for p in profiles:
        profile_str = f"""---
handle: {p.handle}
name: {p.name}
bio: {p.bio or "N/A"}
followers: {p.followers}
category: {p.category or "N/A"}
likely_is: {p.likely_is}
---"""
        profiles_toon.append(profile_str)

    profiles_section = "\n".join(profiles_toon)

    # Build signals section
    high_signals = "\n".join(f"  - {s}" for s in audience.high_signals)
    low_signals = "\n".join(f"  - {s}" for s in audience.low_signals)
    null_signals = ""
    if audience.null_signals:
        null_signals = "\nNULL signals (uncertain, return null):\n" + "\n".join(
            f"  - {s}" for s in audience.null_signals
        )

    return f"""You are a profile classifier for {audience.sector} sector.

TARGET PROFILE:
{audience.target_profile}

DOMAIN CONTEXT:
{audience.domain_context}

{audience.notes or ""}

HIGH signals (return true):
{high_signals}

LOW signals (return false):
{low_signals}
{null_signals}

PROFILES TO LABEL:
{profiles_section}

INSTRUCTIONS:
- Return a JSON array with one object per profile
- Each object must have: handle, label, reason
- label: true (matches target), false (does not match), null (uncertain)
- reason: 1-2 sentence explanation (max 200 chars)
- Be conservative: when uncertain, return null

OUTPUT FORMAT:
[
  {{"handle": "example", "label": true, "reason": "Clear match for target profile"}}
]

Return ONLY the JSON array, no other text."""


def label_batch(
    profiles: list[ProfileToLabel],
    model_config: ModelConfig,
    audience: AudienceConfig,
) -> list[dict]:
    """Label a batch of profiles using the specified model.

    Args:
        profiles: List of profiles to label.
        model_config: Model configuration from registry.
        audience: Audience configuration for prompt.

    Returns:
        List of dicts with twitter_id, label, reason.
    """
    if not profiles:
        return []

    log.info(
        "labeling_batch",
        model=model_config.alias,
        profiles=len(profiles),
    )

    # Get chat model
    registry = get_registry()
    chat_model = registry.get_chat_model(model_config.alias)

    # Build prompt
    prompt = _build_prompt(profiles, audience)

    try:
        # Invoke model
        response = chat_model.invoke(prompt)
        content = response.content

        # Extract JSON from response
        if isinstance(content, str):
            # Try to find JSON array in response
            json_str = _extract_json(content)
            items_data = json.loads(json_str)
        else:
            log.error("unexpected_response_type", type=type(content).__name__)
            return []

        # Validate with Pydantic
        validated_items = []
        for item in items_data:
            try:
                label_item = LabelItem.model_validate(item)
                validated_items.append(label_item)
            except ValidationError as e:
                log.warning("invalid_label_item", error=str(e), item=item)

        # Map handles to twitter_ids
        handle_to_id = {p.handle.lower(): p.twitter_id for p in profiles}
        results = []

        for item in validated_items:
            twitter_id = handle_to_id.get(item.handle.lower())
            if twitter_id:
                results.append({
                    "twitter_id": twitter_id,
                    "label": item.label,
                    "reason": item.reason,
                })
            else:
                log.warning("handle_not_found", handle=item.handle)

        log.info(
            "labeling_complete",
            model=model_config.alias,
            input=len(profiles),
            output=len(results),
        )

        return results  # noqa: TRY300

    except json.JSONDecodeError as e:
        log.exception("json_parse_error", error=str(e))
        return []
    except Exception as e:
        # Handle quota/rate limit errors gracefully
        error_str = str(e).lower()
        if "rate" in error_str or "quota" in error_str or "limit" in error_str:
            log.warning(
                "rate_limit_hit",
                model=model_config.alias,
                action="PURCHASE_TOKENS_OR_WAIT",
            )
        else:
            log.exception("labeling_error", error=str(e))
        return []


def _extract_json(text: str) -> str:
    """Extract JSON array from LLM response text.

    Handles responses that may have markdown code blocks or extra text.
    """
    # Try to find JSON array directly
    text = text.strip()

    # Remove markdown code blocks
    if text.startswith("```"):
        # Find the closing ```
        lines = text.split("\n")
        json_lines = []
        in_block = False
        for line in lines:
            if line.startswith("```") and not in_block:
                in_block = True
                continue
            if line.startswith("```") and in_block:
                break
            if in_block:
                json_lines.append(line)
        text = "\n".join(json_lines)

    # Find array bounds
    start = text.find("[")
    end = text.rfind("]")

    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]

    # Return as-is and let json.loads handle errors
    return text
