"""HAS types and result models."""

from __future__ import annotations

from decimal import Decimal

from scorer_utils import StrictModel


class HASScoreBreakdown(StrictModel):
    """Breakdown of HAS score components."""

    bio_score: Decimal
    engagement_score: Decimal
    account_age_score: Decimal
    verification_score: Decimal


class HASResult(StrictModel):
    """Result from HAS computation."""

    score: Decimal  # 0.0000 - 1.0000
    likely_is: str  # Human, Creator, Entity, Bot, Other
    breakdown: HASScoreBreakdown
