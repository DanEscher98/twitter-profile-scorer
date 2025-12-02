"""HAS (Human Authenticity Score) computation.

Port of packages/has-scorer/src/scorer.ts
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from scoring.heuristic.types import HASResult, HASScoreBreakdown

if TYPE_CHECKING:
    from db.models import UserStats


def compute_has(stats: UserStats, bio: str | None) -> HASResult:
    """Compute Human Authenticity Score for a profile.

    Args:
        stats: User statistics from Twitter API.
        bio: Profile bio/description text.

    Returns:
        HASResult with score, classification, and breakdown.
    """
    bio_score = _compute_bio_score(bio)
    engagement_score = _compute_engagement_score(stats)
    account_age_score = _compute_account_age_score(stats)
    verification_score = _compute_verification_score(stats)

    total = bio_score + engagement_score + account_age_score + verification_score
    total = min(Decimal("1.0000"), max(Decimal("0.0000"), total))

    likely_is = _classify(total, stats)

    return HASResult(
        score=total.quantize(Decimal("0.0001")),
        likely_is=likely_is,
        breakdown=HASScoreBreakdown(
            bio_score=bio_score.quantize(Decimal("0.0001")),
            engagement_score=engagement_score.quantize(Decimal("0.0001")),
            account_age_score=account_age_score.quantize(Decimal("0.0001")),
            verification_score=verification_score.quantize(Decimal("0.0001")),
        ),
    )


def _compute_bio_score(bio: str | None) -> Decimal:
    """Score based on bio presence and quality."""
    if not bio:
        return Decimal("0.00")

    length = len(bio.strip())

    if length < 10:
        return Decimal("0.05")
    if length < 50:
        return Decimal("0.10")
    if length < 100:
        return Decimal("0.15")
    if length < 160:
        return Decimal("0.20")

    return Decimal("0.25")


def _compute_engagement_score(stats: UserStats) -> Decimal:
    """Score based on follower/following ratio and activity."""
    followers = stats.followers or 0
    following = stats.following or 0
    statuses = stats.statuses or 0

    if following == 0:
        following = 1

    ratio = followers / following
    score = Decimal("0.00")

    # Follower ratio component (0.0 - 0.20)
    if ratio > 10:
        score += Decimal("0.20")  # Influencer pattern
    elif ratio > 2:
        score += Decimal("0.15")
    elif ratio > 0.5:
        score += Decimal("0.10")  # Normal human pattern
    elif ratio > 0.1:
        score += Decimal("0.05")

    # Activity component (0.0 - 0.15)
    if statuses > 1000:
        score += Decimal("0.15")
    elif statuses > 100:
        score += Decimal("0.10")
    elif statuses > 10:
        score += Decimal("0.05")

    return min(Decimal("0.35"), score)


def _compute_account_age_score(stats: UserStats) -> Decimal:
    """Score based on account maturity indicators."""
    listed = stats.listed or 0
    favorites = stats.favorites or 0

    score = Decimal("0.00")

    # Listed count (being added to lists indicates established account)
    if listed > 100:
        score += Decimal("0.15")
    elif listed > 10:
        score += Decimal("0.10")
    elif listed > 0:
        score += Decimal("0.05")

    # Favorites (liking posts indicates human engagement)
    if favorites > 1000:
        score += Decimal("0.10")
    elif favorites > 100:
        score += Decimal("0.05")

    return min(Decimal("0.25"), score)


def _compute_verification_score(stats: UserStats) -> Decimal:
    """Score based on verification and profile completeness."""
    score = Decimal("0.00")

    # Legacy verification (pre-Musk era)
    if stats.verified:
        score += Decimal("0.10")

    # Blue verification
    if stats.blue_verified:
        score += Decimal("0.03")  # Lower weight than legacy

    # Profile completeness (inverse indicators)
    if not stats.default_profile:
        score += Decimal("0.01")  # Customized theme
    if not stats.default_image:
        score += Decimal("0.01")  # Custom avatar

    return min(Decimal("0.15"), score)


def _classify(score: Decimal, stats: UserStats) -> str:
    """Classify account type based on score and patterns."""
    followers = stats.followers or 0
    following = stats.following or 0

    if score < Decimal("0.30"):
        return "Bot"

    if stats.verified and followers > 10000 and (stats.default_profile or stats.default_image):
        return "Entity"

    if followers > 1000 and following > 0 and followers / following > 10:
        return "Creator"

    if score >= Decimal("0.55"):
        return "Human"

    return "Other"
