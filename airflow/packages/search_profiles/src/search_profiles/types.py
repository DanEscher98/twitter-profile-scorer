"""Twitter API response types with strict Pydantic validation."""

from __future__ import annotations

from pydantic import Field
from utils import StrictModel


class TwitterProfessionalCategory(StrictModel):
    """Professional category from Twitter API."""

    name: str


class TwitterProfessional(StrictModel):
    """Professional info from Twitter API."""

    category: list[TwitterProfessionalCategory] = Field(default_factory=list)


class TwitterLegacy(StrictModel):
    """Legacy user fields from Twitter API (most profile data lives here)."""

    screen_name: str
    name: str
    description: str | None = None
    created_at: str
    followers_count: int
    friends_count: int  # Following count
    statuses_count: int
    favourites_count: int
    listed_count: int
    media_count: int
    location: str | None = None
    can_dm: bool = False
    default_profile: bool = False
    default_profile_image: bool = False
    possibly_sensitive: bool = False
    verified: bool = False


class TwitterApiUser(StrictModel):
    """User object from Twitter API response."""

    rest_id: str
    is_blue_verified: bool = False
    legacy: TwitterLegacy
    professional: TwitterProfessional | None = None


class TwitterSearchResponse(StrictModel):
    """Response from Twitter search API."""

    users: list[TwitterApiUser]
    next_cursor: str | None = None
