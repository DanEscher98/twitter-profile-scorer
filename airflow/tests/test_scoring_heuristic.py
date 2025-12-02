"""Tests for heuristic scoring (HAS algorithm)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from scoring.heuristic import HASResult, compute_has


class TestComputeHAS:
    """Tests for compute_has function."""

    def test_high_quality_profile(self, sample_user_stats: MagicMock) -> None:
        """Test HAS computation for a high-quality profile."""
        bio = "Professor of Molecular Biology at Stanford. Research focus on gene editing."

        result = compute_has(sample_user_stats, bio)

        assert isinstance(result, HASResult)
        assert result.score > Decimal("0.5")
        assert result.likely_is in ["Human", "Creator", "Entity", "Bot", "Other"]

    def test_low_quality_profile(self) -> None:
        """Test HAS computation for a low-quality profile."""
        stats = MagicMock()
        stats.followers = 10
        stats.following = 5000
        stats.statuses = 0
        stats.favorites = 0
        stats.listed = 0
        stats.media = 0
        stats.verified = False
        stats.blue_verified = False
        stats.default_profile = True
        stats.default_image = True
        stats.sensitive = False
        stats.can_dm = False

        result = compute_has(stats, None)

        assert result.score < Decimal("0.3")
        assert result.likely_is == "Bot"

    def test_empty_bio(self, sample_user_stats: MagicMock) -> None:
        """Test HAS computation with empty bio."""
        result = compute_has(sample_user_stats, None)

        assert result.breakdown.bio_score == Decimal("0.0000")

    def test_short_bio(self, sample_user_stats: MagicMock) -> None:
        """Test HAS computation with short bio."""
        result = compute_has(sample_user_stats, "Hello")

        assert result.breakdown.bio_score == Decimal("0.0500")

    def test_long_bio(self, sample_user_stats: MagicMock) -> None:
        """Test HAS computation with long bio."""
        long_bio = "A" * 200  # 200 character bio
        result = compute_has(sample_user_stats, long_bio)

        assert result.breakdown.bio_score == Decimal("0.2500")

    def test_verified_user(self, sample_user_stats: MagicMock) -> None:
        """Test HAS computation for verified user."""
        sample_user_stats.verified = True

        result = compute_has(sample_user_stats, "Test bio")

        assert result.breakdown.verification_score >= Decimal("0.10")

    def test_creator_classification(self) -> None:
        """Test creator classification for high follower ratio."""
        stats = MagicMock()
        stats.followers = 50000
        stats.following = 500
        stats.statuses = 10000
        stats.favorites = 5000
        stats.listed = 200
        stats.media = 500
        stats.verified = False
        stats.blue_verified = True
        stats.default_profile = False
        stats.default_image = False
        stats.sensitive = False
        stats.can_dm = True

        result = compute_has(stats, "Content creator and influencer")

        assert result.likely_is == "Creator"

    def test_breakdown_sum(self, sample_user_stats: MagicMock) -> None:
        """Test that breakdown components sum correctly."""
        bio = "Medium length bio for testing"
        result = compute_has(sample_user_stats, bio)

        breakdown = result.breakdown
        components_sum = (
            breakdown.bio_score
            + breakdown.engagement_score
            + breakdown.account_age_score
            + breakdown.verification_score
        )

        # Allow small difference due to max capping
        assert abs(float(components_sum) - float(result.score)) < 0.01
