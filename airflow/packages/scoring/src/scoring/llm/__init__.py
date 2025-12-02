"""LLM-based profile scoring with multi-provider support.

Supports Anthropic (Claude), Google (Gemini), and Groq (Llama) models
for labeling profiles against audience configurations.
"""

from scoring.llm.labeler import label_batch
from scoring.llm.prompt import build_system_prompt
from scoring.llm.registry import ModelConfig, ModelRegistry, Provider, get_registry
from scoring.llm.types import (
    AudienceConfig,
    LabelBatchResponse,
    LabelItem,
    LabelMetadata,
    LabelResult,
    ProfileToLabel,
)

__all__ = [
    "AudienceConfig",
    "LabelBatchResponse",
    "LabelItem",
    "LabelMetadata",
    "LabelResult",
    "ModelConfig",
    "ModelRegistry",
    "ProfileToLabel",
    "Provider",
    "build_system_prompt",
    "get_registry",
    "label_batch",
]
