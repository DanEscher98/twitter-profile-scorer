"""Keyword statistics calculation and update logic.

This module handles:
- Getting all keywords from api_search_usage
- Calculating stats per keyword (profiles, HAS, labels)
- Upserting keyword_stats table
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from db import (
    ApiSearchUsage,
    KeywordStats,
    ProfileScore,
    UserKeyword,
    UserProfile,
    get_session,
)
from sqlalchemy import func
from utils import get_logger

if TYPE_CHECKING:
    from sqlmodel import Session

log = get_logger("tasks.stats")


@dataclass
class KeywordStatData:
    """Calculated statistics for a keyword."""

    keyword: str
    profiles_found: int
    avg_human_score: float
    label_rate: float
    still_valid: bool
    pages_searched: int
    high_quality_count: int
    low_quality_count: int
    first_search_at: datetime | None
    last_search_at: datetime | None


def get_all_keywords() -> list[str]:
    """Get all distinct keywords from api_search_usage.

    Returns:
        List of unique keywords.
    """
    log.info("fetching_all_keywords")

    with get_session() as session:
        keywords = (
            session.query(ApiSearchUsage.keyword)
            .distinct()
            .order_by(ApiSearchUsage.keyword)
            .all()
        )

        keyword_list = [k[0] for k in keywords]
        log.info("keywords_found", count=len(keyword_list))
        return keyword_list


def calculate_keyword_stats(keyword: str) -> KeywordStatData:
    """Calculate statistics for a single keyword.

    Args:
        keyword: The keyword to calculate stats for.

    Returns:
        KeywordStatData with all calculated metrics.
    """
    log.debug("calculating_stats", keyword=keyword)

    with get_session() as session:
        # Profile counts and HAS averages
        profile_stats = _get_profile_stats(session, keyword)

        # Label rate calculation
        label_stats = _get_label_stats(session, keyword)

        # Pagination stats
        search_stats = _get_search_stats(session, keyword)

        # Check if keyword still has pages
        still_valid = _check_pagination_valid(session, keyword)

        # Calculate label rate
        total_labeled = label_stats["total_labeled"]
        true_labels = label_stats["true_labels"]
        label_rate = true_labels / total_labeled if total_labeled > 0 else 0.0

        return KeywordStatData(
            keyword=keyword,
            profiles_found=profile_stats["profiles_found"],
            avg_human_score=profile_stats["avg_human_score"],
            label_rate=label_rate,
            still_valid=still_valid,
            pages_searched=search_stats["pages_searched"],
            high_quality_count=profile_stats["high_quality_count"],
            low_quality_count=profile_stats["low_quality_count"],
            first_search_at=search_stats["first_search_at"],
            last_search_at=search_stats["last_search_at"],
        )


def _get_profile_stats(session: Session, keyword: str) -> dict:
    """Get profile statistics for a keyword."""
    result = (
        session.query(
            func.count(UserKeyword.twitter_id).label("profiles_found"),
            func.avg(UserProfile.human_score).label("avg_human_score"),
            func.count()
            .filter(UserProfile.human_score > Decimal("0.7"))
            .label("high_quality_count"),
            func.count()
            .filter(UserProfile.human_score < Decimal("0.4"))
            .label("low_quality_count"),
        )
        .join(UserProfile, UserKeyword.twitter_id == UserProfile.twitter_id)
        .filter(UserKeyword.keyword == keyword)
        .first()
    )

    return {
        "profiles_found": result.profiles_found or 0,
        "avg_human_score": float(result.avg_human_score or 0),
        "high_quality_count": result.high_quality_count or 0,
        "low_quality_count": result.low_quality_count or 0,
    }


def _get_label_stats(session: Session, keyword: str) -> dict:
    """Get label statistics for a keyword."""
    result = (
        session.query(
            func.count()
            .filter(ProfileScore.label.isnot(None))
            .label("total_labeled"),
            func.count()
            .filter(ProfileScore.label.is_(True))
            .label("true_labels"),
        )
        .join(UserKeyword, UserKeyword.twitter_id == ProfileScore.twitter_id)
        .filter(UserKeyword.keyword == keyword)
        .first()
    )

    return {
        "total_labeled": result.total_labeled or 0,
        "true_labels": result.true_labels or 0,
    }


def _get_search_stats(session: Session, keyword: str) -> dict:
    """Get search statistics for a keyword."""
    result = (
        session.query(
            func.max(ApiSearchUsage.page).label("pages_searched"),
            func.min(ApiSearchUsage.query_at).label("first_search_at"),
            func.max(ApiSearchUsage.query_at).label("last_search_at"),
        )
        .filter(ApiSearchUsage.keyword == keyword)
        .first()
    )

    return {
        "pages_searched": result.pages_searched or 0,
        "first_search_at": result.first_search_at,
        "last_search_at": result.last_search_at,
    }


def _check_pagination_valid(session: Session, keyword: str) -> bool:
    """Check if keyword still has pages to search."""
    latest_page = (
        session.query(ApiSearchUsage)
        .filter(ApiSearchUsage.keyword == keyword)
        .order_by(ApiSearchUsage.page.desc())
        .first()
    )
    return latest_page is None or latest_page.next_page is not None


def upsert_keyword_stats(stats: KeywordStatData) -> None:
    """Upsert keyword statistics to database.

    Args:
        stats: Calculated statistics to store.
    """
    log.debug("upserting_keyword_stats", keyword=stats.keyword)

    with get_session() as session:
        existing = session.get(KeywordStats, stats.keyword)

        if existing:
            _update_existing_stats(existing, stats)
        else:
            _insert_new_stats(session, stats)

        log.info(
            "keyword_stats_updated",
            keyword=stats.keyword,
            profiles=stats.profiles_found,
            still_valid=stats.still_valid,
        )


def _update_existing_stats(existing: KeywordStats, stats: KeywordStatData) -> None:
    """Update existing keyword stats record."""
    existing.profiles_found = stats.profiles_found
    existing.avg_human_score = Decimal(str(round(stats.avg_human_score, 3)))
    existing.label_rate = Decimal(str(round(stats.label_rate, 3)))
    existing.still_valid = stats.still_valid
    existing.pages_searched = stats.pages_searched
    existing.high_quality_count = stats.high_quality_count
    existing.low_quality_count = stats.low_quality_count
    if stats.first_search_at:
        existing.first_search_at = stats.first_search_at
    if stats.last_search_at:
        existing.last_search_at = stats.last_search_at
    existing.updated_at = datetime.utcnow()


def _insert_new_stats(session: Session, stats: KeywordStatData) -> None:
    """Insert new keyword stats record."""
    keyword_stat = KeywordStats(
        keyword=stats.keyword,
        profiles_found=stats.profiles_found,
        avg_human_score=Decimal(str(round(stats.avg_human_score, 3))),
        label_rate=Decimal(str(round(stats.label_rate, 3))),
        still_valid=stats.still_valid,
        pages_searched=stats.pages_searched,
        high_quality_count=stats.high_quality_count,
        low_quality_count=stats.low_quality_count,
        first_search_at=stats.first_search_at,
        last_search_at=stats.last_search_at,
    )
    session.add(keyword_stat)
