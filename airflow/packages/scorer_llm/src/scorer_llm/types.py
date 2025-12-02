"""LLM scoring types with strict Pydantic validation."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from scorer_utils import StrictModel


class LabelItem(StrictModel):
    """Single label result from LLM response."""

    handle: str
    label: bool | None = Field(description="Trivalent: true=match, false=no match, null=uncertain")
    reason: str = Field(max_length=200)


class LabelResponse(StrictModel):
    """Validated LLM response containing label items."""

    items: list[LabelItem]


class AudienceConfig(StrictModel):
    """Audience configuration for LLM scoring prompts."""

    target_profile: str = Field(alias="targetProfile")
    sector: Literal["academia", "industry", "government", "ngo", "healthcare", "pharma", "custom"]
    high_signals: list[str] = Field(alias="highSignals")
    low_signals: list[str] = Field(alias="lowSignals")
    null_signals: list[str] | None = Field(default=None, alias="nullSignals")
    domain_context: str = Field(alias="domainContext")
    notes: str | None = None

    # Metadata (set after loading)
    config_name: str = Field(default="", exclude=True)

    model_config = {  # type: ignore[misc]
        **StrictModel.model_config,
        "populate_by_name": True,
    }


class ProfileToLabel(StrictModel):
    """Profile data sent to LLM for labeling."""

    twitter_id: str
    handle: str
    name: str
    bio: str | None
    category: str | None
    followers: int
    likely_is: str
