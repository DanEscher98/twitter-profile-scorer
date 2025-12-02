"""LLM scoring types with strict Pydantic validation."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field
from utils import StrictModel


class LabelItem(StrictModel):
    """Single label result from LLM response."""

    handle: str
    label: bool | None = Field(description="Trivalent: true=match, false=no match, null=uncertain")
    reason: str = Field(max_length=200)


class LabelMetadata(StrictModel):
    """Token usage and cost metadata for LLM calls."""

    input_tokens: int = Field(description="Tokens in system + user prompts")
    output_tokens: int = Field(description="Tokens in model response")
    call_cost: float = Field(description="Cost in USD for this call")


class LabelResult(StrictModel):
    """Single labeled profile result."""

    twitter_id: str
    label: bool | None
    reason: str


class LabelBatchResponse(StrictModel):
    """Complete response from label_batch with results and metadata."""

    results: list[LabelResult]
    metadata: LabelMetadata
    model: str = Field(description="Model alias used for labeling")


class AudienceConfig(StrictModel):
    """Audience configuration for LLM scoring prompts."""

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        validate_assignment=True,
        use_enum_values=True,
        populate_by_name=True,
    )

    target_profile: str = Field(alias="targetProfile")
    sector: Literal["academia", "industry", "government", "ngo", "healthcare", "pharma", "custom"]
    high_signals: list[str] = Field(alias="highSignals")
    low_signals: list[str] = Field(alias="lowSignals")
    null_signals: list[str] | None = Field(default=None, alias="nullSignals")
    domain_context: str = Field(alias="domainContext")
    notes: str | None = None

    # Metadata (set after loading)
    config_name: str = Field(default="", exclude=True)


class ProfileToLabel(StrictModel):
    """Profile data sent to LLM for labeling."""

    twitter_id: str
    handle: str
    name: str
    bio: str | None
    category: str | None
    followers: int
    likely_is: str
