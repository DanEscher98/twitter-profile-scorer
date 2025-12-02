"""Pytest fixtures and configuration for Airflow tests."""

from __future__ import annotations

import os

# Set environment variables BEFORE any imports that might load settings
# This is necessary because settings are cached at import time
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("APP_MODE", "development")
os.environ.setdefault("LOG_LEVEL", "silent")
os.environ.setdefault("TWITTERX_APIKEY", "test-api-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")

from collections.abc import Iterator
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    from search_profiles import TwitterApiUser


@pytest.fixture
def mock_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set up mock environment variables for testing."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("APP_MODE", "development")
    monkeypatch.setenv("LOG_LEVEL", "silent")
    monkeypatch.setenv("TWITTERX_APIKEY", "test-api-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")


@pytest.fixture
def mock_db_session() -> Iterator[MagicMock]:
    """Mock database session context manager."""
    mock_session = MagicMock()

    with patch("db.client.get_session") as mock_get_session:
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
        yield mock_session


@pytest.fixture
def sample_twitter_user() -> TwitterApiUser:
    """Create a sample Twitter API user for testing."""
    from search_profiles import TwitterApiUser, TwitterLegacy

    return TwitterApiUser(
        rest_id="123456789",
        is_blue_verified=True,
        legacy=TwitterLegacy(
            screen_name="testuser",
            name="Test User",
            description="A test user bio for testing purposes",
            created_at="Sat Jan 01 00:00:00 +0000 2020",
            followers_count=1000,
            friends_count=500,
            statuses_count=5000,
            favourites_count=2000,
            listed_count=50,
            media_count=100,
            location="San Francisco, CA",
            can_dm=True,
            default_profile=False,
            default_profile_image=False,
            possibly_sensitive=False,
            verified=False,
        ),
        professional=None,
    )


@pytest.fixture
def sample_user_stats() -> MagicMock:
    """Create sample UserStats for HAS computation."""
    stats = MagicMock()
    stats.twitter_id = "123456789"
    stats.followers = 1000
    stats.following = 500
    stats.statuses = 5000
    stats.favorites = 2000
    stats.listed = 50
    stats.media = 100
    stats.verified = False
    stats.blue_verified = True
    stats.default_profile = False
    stats.default_image = False
    stats.sensitive = False
    stats.can_dm = True
    return stats


@pytest.fixture
def sample_audience_config() -> dict:
    """Create sample audience configuration."""
    return {
        "targetProfile": "Academic researcher in life sciences",
        "sector": "academia",
        "highSignals": ["PhD", "Professor", "Researcher", "University"],
        "lowSignals": ["Crypto", "NFT", "Marketing", "Sales"],
        "nullSignals": ["Student", "Intern"],
        "domainContext": "Life sciences academic research community",
        "notes": "Focus on established researchers with publications",
    }


@pytest.fixture
def sample_keyword_stats() -> list[MagicMock]:
    """Create sample keyword stats for testing."""
    stats = []
    for i, keyword in enumerate(["researcher", "scientist", "professor"]):
        stat = MagicMock()
        stat.keyword = keyword
        stat.still_valid = True
        stat.avg_human_score = Decimal(f"0.{7 - i}")
        stats.append(stat)
    return stats


@pytest.fixture
def sample_api_search_usage() -> MagicMock:
    """Create sample API search usage record."""
    usage = MagicMock()
    usage.keyword = "researcher"
    usage.page = 5
    usage.next_page = "cursor_abc123"
    return usage
