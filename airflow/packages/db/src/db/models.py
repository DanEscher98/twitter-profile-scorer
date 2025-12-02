"""SQLModel database models matching the existing Drizzle schema."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import Column, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlmodel import Field, SQLModel


class Platform(StrEnum):
    """Supported social media platforms."""

    TWITTER = "twitter"
    BLUESKY = "bluesky"


class TwitterUserType(StrEnum):
    """Classification of user accounts from HAS scoring."""

    HUMAN = "Human"
    CREATOR = "Creator"
    ENTITY = "Entity"
    BOT = "Bot"
    OTHER = "Other"


# =============================================================================
# Core Models
# =============================================================================


class UserProfile(SQLModel, table=True):
    """Core profile data. Maps to user_profiles table."""

    __tablename__ = "user_profiles"  # type: ignore[assignment]

    twitter_id: str = Field(primary_key=True, max_length=25)
    handle: str = Field(max_length=255, index=True)
    name: str = Field(max_length=255)
    bio: str | None = Field(default=None, sa_type=Text)
    created_at: str = Field(max_length=100)
    follower_count: int | None = Field(default=None)
    location: str | None = Field(default=None, max_length=255)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    got_by_keywords: list[str] | None = Field(
        default=None,
        sa_column=Column(ARRAY(Text)),
    )
    can_dm: bool | None = Field(default=None)
    category: str | None = Field(default=None, max_length=255)
    human_score: Decimal | None = Field(default=None, decimal_places=4, max_digits=5)
    likely_is: TwitterUserType | None = Field(default=None)
    platform: Platform = Field(default=Platform.TWITTER, max_length=20)



class ProfileScore(SQLModel, table=True):
    """LLM scoring results. Maps to profile_scores table."""

    __tablename__ = "profile_scores"  # type: ignore[assignment]

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    twitter_id: str = Field(foreign_key="user_profiles.twitter_id", max_length=25, index=True)
    label: bool | None = Field(default=None)  # Trivalent: true/false/null
    reason: str | None = Field(default=None, sa_type=Text)
    scored_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)
    scored_by: str = Field(max_length=100, index=True)
    audience: str | None = Field(default=None, max_length=100, index=True)


class ApiSearchUsage(SQLModel, table=True):
    """API call tracking and pagination state. Renamed from xapi_usage_search."""

    __tablename__ = "api_search_usage"  # type: ignore[assignment]

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    ids_hash: str = Field(max_length=16)
    keyword: str = Field(max_length=255)
    items: int = Field(default=20)
    retries: int = Field(default=1)
    next_page: str | None = Field(default=None, sa_type=Text)
    page: int = Field(default=0)
    new_profiles: int = Field(default=0)
    query_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    platform: Platform = Field(default=Platform.TWITTER, max_length=20)


class ProfileToScore(SQLModel, table=True):
    """Queue of profiles pending LLM evaluation. Maps to profiles_to_score table."""

    __tablename__ = "profiles_to_score"  # type: ignore[assignment]

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    twitter_id: str = Field(
        foreign_key="user_profiles.twitter_id",
        max_length=25,
        unique=True,
    )
    handle: str = Field(max_length=255)
    added_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)


class UserKeyword(SQLModel, table=True):
    """Many-to-many linking profiles to search keywords."""

    __tablename__ = "user_keywords"  # type: ignore[assignment]

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    twitter_id: str = Field(
        foreign_key="user_profiles.twitter_id",
        max_length=25,
        index=True,
    )
    keyword: str = Field(max_length=255, index=True)
    search_id: UUID | None = Field(
        foreign_key="api_search_usage.id",
        default=None,
    )
    added_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class UserStats(SQLModel, table=True):
    """Raw numeric stats for ML training. Maps to user_stats table."""

    __tablename__ = "user_stats"  # type: ignore[assignment]

    twitter_id: str = Field(
        foreign_key="user_profiles.twitter_id",
        primary_key=True,
        max_length=25,
    )
    followers: int | None = Field(default=None)
    following: int | None = Field(default=None)
    statuses: int | None = Field(default=None)
    favorites: int | None = Field(default=None)
    listed: int | None = Field(default=None)
    media: int | None = Field(default=None)
    verified: bool | None = Field(default=None)
    blue_verified: bool | None = Field(default=None)
    default_profile: bool | None = Field(default=None)
    default_image: bool | None = Field(default=None)
    sensitive: bool | None = Field(default=None)
    can_dm: bool | None = Field(default=None)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class KeywordStats(SQLModel, table=True):
    """Keyword pool statistics. Maps to keyword_stats table."""

    __tablename__ = "keyword_stats"  # type: ignore[assignment]

    keyword: str = Field(primary_key=True, max_length=255)
    semantic_tags: list[str] | None = Field(
        default=None,
        sa_column=Column(ARRAY(Text)),
    )
    profiles_found: int = Field(default=0)
    avg_human_score: Decimal = Field(default=Decimal(0), decimal_places=3, max_digits=4)
    label_rate: Decimal = Field(default=Decimal(0), decimal_places=3, max_digits=4)
    still_valid: bool = Field(default=True)
    pages_searched: int = Field(default=0)
    high_quality_count: int = Field(default=0)
    low_quality_count: int = Field(default=0)
    first_search_at: datetime | None = Field(default=None)
    last_search_at: datetime | None = Field(default=None)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
