"""Profile search logic.

This module handles:
- Calling Twitter/BlueSky APIs for profile search
- Formatting raw API responses into normalized structures
- NO database operations (that's handled by storage.py)
"""

from __future__ import annotations

from dataclasses import dataclass

from db import Platform, UserStats
from scoring.heuristic import HASResult, compute_has
from search_profiles import TwitterApiUser, TwitterClient, TwitterSearchResponse
from utils import get_logger

log = get_logger("tasks.search")


@dataclass
class SearchResult:
    """Result from a profile search operation."""

    users: list[TwitterApiUser]
    next_cursor: str | None
    keyword: str
    platform: Platform
    page: int


@dataclass
class ProcessedProfile:
    """Profile data ready for storage."""

    user: TwitterApiUser
    has_result: HASResult
    keyword: str
    platform: Platform


def search_profiles_for_keyword(
    keyword: str,
    *,
    items: int = 20,
    cursor: str | None = None,
    page: int = 0,
    platform: Platform = Platform.TWITTER,
) -> SearchResult:
    """Search for profiles matching a keyword.

    This function ONLY handles API calls, not database operations.

    Args:
        keyword: Search keyword.
        items: Number of results to request.
        cursor: Pagination cursor from previous search.
        page: Current page number (for tracking).
        platform: Platform to search (currently only Twitter).

    Returns:
        SearchResult with users and pagination info.

    Raises:
        TwitterApiError: On API errors.
    """
    log.info(
        "searching_profiles",
        keyword=keyword,
        items=items,
        page=page,
        platform=platform,
        has_cursor=cursor is not None,
    )

    if platform != Platform.TWITTER:
        log.warning("platform_not_supported", platform=platform)
        return SearchResult(
            users=[],
            next_cursor=None,
            keyword=keyword,
            platform=platform,
            page=page,
        )

    with TwitterClient() as client:
        response: TwitterSearchResponse = client.search_users(
            keyword,
            items=items,
            cursor=cursor,
        )

    log.info(
        "search_complete",
        keyword=keyword,
        users_found=len(response.users),
        has_next=response.next_cursor is not None,
    )

    return SearchResult(
        users=response.users,
        next_cursor=response.next_cursor,
        keyword=keyword,
        platform=platform,
        page=page,
    )


def process_profiles(
    search_result: SearchResult,
) -> list[ProcessedProfile]:
    """Process raw API users into normalized profiles with HAS scores.

    This function computes HAS scores but does NOT persist to database.

    Args:
        search_result: Raw search results from API.

    Returns:
        List of processed profiles ready for storage.
    """
    processed: list[ProcessedProfile] = []

    for user in search_result.users:
        # Build stats for HAS computation
        stats = _build_user_stats(user)

        # Compute HAS
        has_result = compute_has(stats, user.legacy.description)

        processed.append(
            ProcessedProfile(
                user=user,
                has_result=has_result,
                keyword=search_result.keyword,
                platform=search_result.platform,
            )
        )

    log.info(
        "profiles_processed",
        count=len(processed),
        keyword=search_result.keyword,
    )

    return processed


def _build_user_stats(user: TwitterApiUser) -> UserStats:
    """Build UserStats from API response for HAS computation."""
    return UserStats(
        twitter_id=user.rest_id,
        followers=user.legacy.followers_count,
        following=user.legacy.friends_count,
        statuses=user.legacy.statuses_count,
        favorites=user.legacy.favourites_count,
        listed=user.legacy.listed_count,
        media=user.legacy.media_count,
        verified=user.legacy.verified,
        blue_verified=user.is_blue_verified,
        default_profile=user.legacy.default_profile,
        default_image=user.legacy.default_profile_image,
        sensitive=user.legacy.possibly_sensitive,
        can_dm=user.legacy.can_dm,
    )
