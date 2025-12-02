"""Profile scoring: Heuristic (HAS) and LLM-based scoring.

This package provides two scoring mechanisms:
- heuristic: Human Authenticity Score (HAS) based on profile metrics
- llm: LLM-based labeling using multiple providers (Anthropic, Google, Groq)
"""

from scoring.heuristic import HASResult, HASScoreBreakdown, compute_has
from scoring.llm import AudienceConfig, ModelConfig, label_batch

__all__ = [
    # LLM scoring
    "AudienceConfig",
    # Heuristic scoring
    "HASResult",
    "HASScoreBreakdown",
    "ModelConfig",
    "compute_has",
    "label_batch",
]
