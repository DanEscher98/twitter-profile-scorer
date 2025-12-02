"""LLM labeling with multi-provider support via LangChain.

Optimizations:
- Uses ChatPromptTemplate for efficient prompt construction
- Anthropic: prompt caching via cache_control on system message
- Extracts token counts from response metadata when available
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import TYPE_CHECKING

from langchain_core.prompts import ChatPromptTemplate
from pydantic import ValidationError
from toon_format import count_tokens, encode
from utils import get_logger

from scoring.llm.prompt import build_system_prompt
from scoring.llm.registry import ModelConfig, Provider, get_registry
from scoring.llm.types import (
    AudienceConfig,
    LabelBatchResponse,
    LabelItem,
    LabelMetadata,
    LabelResult,
    ProfileToLabel,
)

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import BaseMessage

log = get_logger("scoring.llm.labeler")

# Model pricing per million tokens (USD)
MODEL_PRICING: dict[str, dict[str, Decimal]] = {
    "claude-haiku-4.5": {"input": Decimal("0.80"), "output": Decimal("4.00")},
    "claude-sonnet-4.5": {"input": Decimal("3.00"), "output": Decimal("15.00")},
    "claude-opus-4.5": {"input": Decimal("15.00"), "output": Decimal("75.00")},
    "gemini-flash-2.0": {"input": Decimal("0.10"), "output": Decimal("0.40")},
    "gemini-flash-1.5": {"input": Decimal("0.075"), "output": Decimal("0.30")},
    "meta-maverick-17b": {"input": Decimal("0.00"), "output": Decimal("0.00")},
}

DEFAULT_PRICING = {"input": Decimal("1.00"), "output": Decimal("5.00")}

# Prompt template for non-Anthropic providers
CLASSIFICATION_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", "{system_prompt}"),
    ("human", "Please classify the following profiles:\n\n```toon\n{profiles_toon}\n```"),
])


def _calculate_cost(model_alias: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost in USD for token usage."""
    pricing = MODEL_PRICING.get(model_alias, DEFAULT_PRICING)
    one_million = Decimal(1_000_000)
    input_cost = (Decimal(input_tokens) / one_million) * pricing["input"]
    output_cost = (Decimal(output_tokens) / one_million) * pricing["output"]
    return float(input_cost + output_cost)


def _prepare_profiles_toon(profiles: list[ProfileToLabel]) -> str:
    """Convert profiles to TOON-encoded string."""
    data = [
        {
            "handle": p.handle,
            "name": p.name,
            "bio": p.bio or "N/A",
            "followers": p.followers,
            "category": p.category or "N/A",
        }
        for p in profiles
    ]
    return encode(data)


def _invoke_anthropic(
    chat_model: BaseChatModel,
    system_prompt: str,
    profiles_toon: str,
) -> BaseMessage:
    """Invoke Anthropic model with prompt caching."""
    messages: list[dict[str, object]] = [
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
        {
            "role": "user",
            "content": f"Please classify the following profiles:\n\n```toon\n{profiles_toon}\n```",
        },
    ]
    return chat_model.invoke(messages)


def _invoke_standard(
    chat_model: BaseChatModel,
    system_prompt: str,
    profiles_toon: str,
) -> BaseMessage:
    """Invoke model using standard ChatPromptTemplate."""
    chain = CLASSIFICATION_TEMPLATE | chat_model
    return chain.invoke({
        "system_prompt": system_prompt,
        "profiles_toon": profiles_toon,
    })


def _extract_token_counts(response: BaseMessage, fallback_input: str) -> tuple[int, int]:
    """Extract token counts from response metadata or estimate."""
    input_tokens = 0
    output_tokens = 0

    # Try usage_metadata (LangChain standard)
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        input_tokens = response.usage_metadata.get("input_tokens", 0)
        output_tokens = response.usage_metadata.get("output_tokens", 0)

    # Fallback to estimation
    if not input_tokens:
        input_tokens = count_tokens(fallback_input)
    if not output_tokens:
        content = response.content if isinstance(response.content, str) else ""
        output_tokens = count_tokens(content)

    return input_tokens, output_tokens


def _log_anthropic_cache_stats(response: BaseMessage, audience_name: str) -> None:
    """Log Anthropic cache statistics if available."""
    if not hasattr(response, "usage_metadata") or not response.usage_metadata:
        return

    details = response.usage_metadata.get("input_token_details", {})
    cache_read = details.get("cache_read", 0)
    cache_creation = details.get("cache_creation", 0)

    if cache_read or cache_creation:
        log.info(
            "anthropic_cache_stats",
            audience=audience_name,
            cache_read=cache_read,
            cache_creation=cache_creation,
        )


def _parse_labels(
    content: str,
    profiles: list[ProfileToLabel],
) -> list[LabelResult]:
    """Parse LLM response and map to LabelResult objects."""
    json_str = _extract_json(content)
    items_data = json.loads(json_str)

    # Validate items
    validated_items: list[LabelItem] = []
    for item in items_data:
        try:
            validated_items.append(LabelItem.model_validate(item))
        except ValidationError as e:
            log.warning("invalid_label_item", error=str(e), item=item)

    # Map handles to twitter_ids
    handle_to_id = {p.handle.lower(): p.twitter_id for p in profiles}
    results: list[LabelResult] = []

    for item in validated_items:
        twitter_id = handle_to_id.get(item.handle.lower())
        if twitter_id:
            results.append(LabelResult(twitter_id=twitter_id, label=item.label, reason=item.reason))
        else:
            log.warning("handle_not_found", handle=item.handle)

    return results


def _extract_json(text: str) -> str:
    """Extract JSON array from LLM response text."""
    text = text.strip()

    # Handle markdown code blocks
    if text.startswith("```"):
        lines = text.split("\n")
        json_lines = []
        in_block = False
        for line in lines:
            if line.startswith("```") and not in_block:
                in_block = True
            elif line.startswith("```") and in_block:
                break
            elif in_block:
                json_lines.append(line)
        text = "\n".join(json_lines)

    # Find JSON array
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]

    return text


def label_batch(
    profiles: list[ProfileToLabel],
    model_config: ModelConfig,
    audience: AudienceConfig,
) -> LabelBatchResponse:
    """Label a batch of profiles using the specified model.

    Uses provider-specific optimizations:
    - Anthropic: Prompt caching via cache_control on system message
    - All providers: Token counts from response metadata when available
    """
    empty_response = LabelBatchResponse(
        results=[],
        metadata=LabelMetadata(input_tokens=0, output_tokens=0, call_cost=0.0),
        model=model_config.alias,
    )

    if not profiles:
        return empty_response

    log.info(
        "labeling_batch",
        model=model_config.alias,
        provider=model_config.provider.value,
        profiles=len(profiles),
    )

    registry = get_registry()
    chat_model = registry.get_chat_model(model_config.alias)

    system_prompt = build_system_prompt(audience)
    profiles_toon = _prepare_profiles_toon(profiles)

    try:
        # Invoke with provider-specific optimization
        if model_config.provider == Provider.ANTHROPIC:
            response = _invoke_anthropic(chat_model, system_prompt, profiles_toon)
            _log_anthropic_cache_stats(response, audience.config_name or "unknown")
        else:
            response = _invoke_standard(chat_model, system_prompt, profiles_toon)

        content = response.content
        if not isinstance(content, str):
            log.error("unexpected_response_type", type=type(content).__name__)
            return empty_response

        # Extract token counts and calculate cost
        fallback_input = system_prompt + profiles_toon
        input_tokens, output_tokens = _extract_token_counts(response, fallback_input)
        call_cost = _calculate_cost(model_config.alias, input_tokens, output_tokens)

        # Parse response
        results = _parse_labels(content, profiles)

        log.info(
            "labeling_complete",
            model=model_config.alias,
            input_profiles=len(profiles),
            output_results=len(results),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            call_cost=f"${call_cost:.6f}",
        )

        return LabelBatchResponse(
            results=results,
            metadata=LabelMetadata(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                call_cost=call_cost,
            ),
            model=model_config.alias,
        )

    except json.JSONDecodeError as e:
        log.exception("json_parse_error", error=str(e))
        return empty_response
    except Exception as e:
        _log_error(e, model_config.alias)
        return empty_response


def _log_error(e: Exception, model_alias: str) -> None:
    """Log error with appropriate level based on error type."""
    error_str = str(e).lower()
    if "rate" in error_str or "quota" in error_str or "limit" in error_str:
        log.warning("rate_limit_hit", model=model_alias, action="PURCHASE_TOKENS_OR_WAIT")
    else:
        log.exception("labeling_error", error=str(e))
