"""Database storage operations.

This module handles ALL database persistence:
- Storing user profiles
- Recording API search usage
- Updating keyword stats
- Queueing profiles for LLM scoring
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from db import (
    ApiSearchUsage,
    ProfileScore,
    ProfileToScore,
    UserKeyword,
    UserProfile,
    UserStats,
    get_session,
)
from utils import get_logger

if TYPE_CHECKING:
    from sqlmodel import Session

    from tasks.search import ProcessedProfile, SearchResult

log = get_logger("tasks.storage")


@dataclass
class StorageResult:
    """Result from storing search results."""

    new_profiles: int
    updated_profiles: int
    queued_for_scoring: int
    search_id: UUID


def store_search_results(
    search_result: SearchResult,
    processed_profiles: list[ProcessedProfile],
    has_threshold: float = 0.65,
) -> StorageResult:
    """Store search results in database.

    This function handles:
    1. Recording the search in api_search_usage
    2. Upserting user profiles
    3. Upserting user stats
    4. Creating user_keywords associations
    5. Queueing high-HAS profiles for LLM scoring

    Args:
        search_result: Raw search result with metadata.
        processed_profiles: Profiles with computed HAS scores.
        has_threshold: Minimum HAS to queue for LLM scoring.

    Returns:
        StorageResult with counts of operations performed.
    """
    log.info(
        "storing_search_results",
        keyword=search_result.keyword,
        profiles=len(processed_profiles),
    )

    with get_session() as session:
        # Count new profiles before insert
        new_count = _count_new_profiles(session, processed_profiles)

        # Record search metadata
        search_id = _record_search(
            session,
            search_result,
            processed_profiles,
            new_count,
        )

        # Store profiles
        updated_count = 0
        queued_count = 0

        for profile in processed_profiles:
            is_new = _upsert_profile(session, profile, search_id)
            if not is_new:
                updated_count += 1

            _upsert_stats(session, profile)

            # Queue for LLM if high HAS
            if (
                float(profile.has_result.score) > has_threshold
                and _queue_for_scoring(session, profile)
            ):
                queued_count += 1

        result = StorageResult(
            new_profiles=new_count,
            updated_profiles=updated_count,
            queued_for_scoring=queued_count,
            search_id=search_id,
        )

        log.info(
            "storage_complete",
            keyword=search_result.keyword,
            new=result.new_profiles,
            updated=result.updated_profiles,
            queued=result.queued_for_scoring,
        )

        return result


def _count_new_profiles(
    session: Session,
    profiles: list[ProcessedProfile],
) -> int:
    """Count how many profiles don't exist yet."""
    count = 0
    for profile in profiles:
        existing = session.get(UserProfile, profile.user.rest_id)
        if existing is None:
            count += 1
    return count


def _record_search(
    session: Session,
    search_result: SearchResult,
    profiles: list[ProcessedProfile],
    new_count: int,
) -> UUID:
    """Record search metadata in api_search_usage."""
    # Generate IDs hash
    ids_list = sorted(p.user.rest_id for p in profiles)
    ids_hash = hashlib.md5(  # noqa: S324
        ",".join(ids_list).encode()
    ).hexdigest()[:16]

    search_id = uuid4()
    search_record = ApiSearchUsage(
        id=search_id,
        ids_hash=ids_hash,
        keyword=search_result.keyword,
        items=len(profiles),
        retries=1,
        next_page=search_result.next_cursor,
        page=search_result.page,
        new_profiles=new_count,
        platform=search_result.platform,
    )

    session.add(search_record)
    session.flush()  # Ensure ID is available for FK

    return search_id


def _upsert_profile(
    session: Session,
    profile: ProcessedProfile,
    search_id: UUID,
) -> bool:
    """Upsert user profile and keyword association.

    Returns:
        True if new profile, False if updated.
    """
    user = profile.user
    keyword = profile.keyword

    existing = session.get(UserProfile, user.rest_id)
    is_new = existing is None

    if is_new:
        # Insert new profile
        db_profile = UserProfile(
            twitter_id=user.rest_id,
            handle=user.legacy.screen_name,
            name=user.legacy.name,
            bio=user.legacy.description,
            created_at=user.legacy.created_at,
            follower_count=user.legacy.followers_count,
            location=user.legacy.location,
            can_dm=user.legacy.can_dm,
            category=_extract_category(user),
            human_score=Decimal(str(round(float(profile.has_result.score), 4))),
            likely_is=profile.has_result.likely_is,
            got_by_keywords=[keyword],
            platform=profile.platform,
        )
        session.add(db_profile)
    else:
        # Update existing
        db_profile = existing
        if db_profile.got_by_keywords is None:
            db_profile.got_by_keywords = [keyword]
        elif keyword not in db_profile.got_by_keywords:
            db_profile.got_by_keywords = [*db_profile.got_by_keywords, keyword]
        db_profile.updated_at = datetime.utcnow()

    # Add keyword association
    user_keyword = UserKeyword(
        twitter_id=user.rest_id,
        keyword=keyword,
        search_id=search_id,
    )
    session.merge(user_keyword)

    return is_new


def _upsert_stats(session: Session, profile: ProcessedProfile) -> None:
    """Upsert user stats."""
    user = profile.user
    stats = UserStats(
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
    session.merge(stats)


def _queue_for_scoring(session: Session, profile: ProcessedProfile) -> bool:
    """Queue profile for LLM scoring if not already queued.

    Returns:
        True if queued, False if already in queue.
    """
    existing = (
        session.query(ProfileToScore)
        .filter(ProfileToScore.twitter_id == profile.user.rest_id)
        .first()
    )

    if existing is not None:
        return False

    to_score = ProfileToScore(
        twitter_id=profile.user.rest_id,
        handle=profile.user.legacy.screen_name,
    )
    session.add(to_score)
    return True


def _extract_category(user: object) -> str | None:
    """Extract category from user's professional field."""
    if hasattr(user, "professional") and user.professional and user.professional.category:
        categories = user.professional.category
        if categories:
            return categories[0].name
    return None


def store_label_results(
    labels: list[dict],
    model_full_name: str,
    audience_name: str,
) -> int:
    """Store LLM labeling results in profile_scores.

    Args:
        labels: List of dicts with twitter_id, label, reason.
        model_full_name: Full model name for scored_by column.
        audience_name: Audience config name.

    Returns:
        Number of labels stored.
    """
    log.info(
        "storing_labels",
        count=len(labels),
        model=model_full_name,
        audience=audience_name,
    )

    stored = 0

    with get_session() as session:
        for label_result in labels:
            try:
                _upsert_label(session, label_result, model_full_name, audience_name)
                stored += 1
            except Exception as e:
                log.exception("label_store_error", error=str(e))

    log.info("labels_stored", count=stored)
    return stored


def _upsert_label(
    session: Session,
    label_result: dict,
    model_full_name: str,
    audience_name: str,
) -> None:
    """Insert or update a profile label."""
    existing = (
        session.query(ProfileScore)
        .filter(
            ProfileScore.twitter_id == label_result["twitter_id"],
            ProfileScore.scored_by == model_full_name,
        )
        .first()
    )

    if existing:
        existing.label = label_result["label"]
        existing.reason = label_result["reason"]
        existing.audience = audience_name
        existing.scored_at = datetime.utcnow()
    else:
        score = ProfileScore(
            twitter_id=label_result["twitter_id"],
            label=label_result["label"],
            reason=label_result["reason"],
            scored_by=model_full_name,
            audience=audience_name,
        )
        session.add(score)
