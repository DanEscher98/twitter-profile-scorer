"""Keyword sampling and validation logic.

This module handles:
- Selecting valid keywords from keyword_stats
- Checking pagination availability per platform
- Returning keywords that still have searchable pages
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from db import ApiSearchUsage, KeywordStats, Platform, get_session
from utils import get_logger

if TYPE_CHECKING:
    from sqlmodel import Session

log = get_logger("tasks.keywords")


def get_valid_keywords(
    count: int,
    platform: Platform = Platform.TWITTER,
) -> list[str]:
    """Get a sample of valid keywords for a platform.

    A keyword is valid if:
    1. It has still_valid=True in keyword_stats
    2. It has pagination available (no search yet, or has next_page cursor)

    Args:
        count: Maximum number of keywords to return.
        platform: Platform to check pagination for.

    Returns:
        List of keyword strings.
    """
    log.info("fetching_valid_keywords", count=count, platform=platform)

    with get_session() as session:
        keywords = _fetch_valid_keywords(session)

        if not keywords:
            log.warning("no_valid_keywords_found")
            return []

        # Shuffle for variety
        random.shuffle(keywords)

        # Filter by pagination availability
        selected = _filter_by_pagination(session, keywords, count, platform)

        log.info("keywords_selected", count=len(selected), platform=platform)
        return selected


def _fetch_valid_keywords(session: Session) -> list[str]:
    """Fetch keywords marked as still_valid."""
    results = (
        session.query(KeywordStats.keyword)
        .filter(KeywordStats.still_valid.is_(True))
        .order_by(KeywordStats.avg_human_score.desc())
        .all()
    )
    return [r[0] for r in results]


def _filter_by_pagination(
    session: Session,
    keywords: list[str],
    count: int,
    platform: Platform,
) -> list[str]:
    """Filter keywords that still have pagination available."""
    selected: list[str] = []

    for keyword in keywords:
        if len(selected) >= count:
            break

        if _has_pagination_available(session, keyword, platform):
            selected.append(keyword)
        else:
            log.debug("keyword_exhausted", keyword=keyword, platform=platform)

    return selected


def _has_pagination_available(
    session: Session,
    keyword: str,
    platform: Platform,
) -> bool:
    """Check if keyword still has pages to search for a platform."""
    latest = (
        session.query(ApiSearchUsage)
        .filter(
            ApiSearchUsage.keyword == keyword,
            ApiSearchUsage.platform == platform,
        )
        .order_by(ApiSearchUsage.page.desc())
        .first()
    )

    # Has pages if: no search yet OR has next_page cursor
    return latest is None or latest.next_page is not None


def get_pagination_state(
    keyword: str,
    platform: Platform = Platform.TWITTER,
) -> tuple[str | None, int]:
    """Get current pagination state for a keyword.

    Args:
        keyword: The search keyword.
        platform: Platform to check.

    Returns:
        Tuple of (cursor, page_number). cursor is None if no previous search.
    """
    with get_session() as session:
        latest = (
            session.query(ApiSearchUsage)
            .filter(
                ApiSearchUsage.keyword == keyword,
                ApiSearchUsage.platform == platform,
            )
            .order_by(ApiSearchUsage.page.desc())
            .first()
        )

        if latest is None:
            return None, 0

        return latest.next_page, latest.page + 1
