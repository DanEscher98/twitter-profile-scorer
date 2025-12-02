"""Keyword Statistics Update DAG.

Updates keyword_stats table with aggregated metrics from related tables.
Runs daily to calculate:
- Profile counts and quality metrics per keyword
- Label rates (percentage of true labels)
- Pagination state (still_valid flag)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from airflow.decorators import dag, task
from sqlalchemy import func

from scorer_db import (
    ApiSearchUsage,
    KeywordStats,
    ProfileScore,
    UserKeyword,
    UserProfile,
    get_session,
)
from scorer_utils import get_logger

log = get_logger("dag.keyword_stats")


@dag(
    dag_id="keyword_stats_update",
    description="Update keyword statistics and quality metrics",
    schedule="0 2 * * *",  # Daily at 2 AM
    start_date=datetime(2025, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "airflow",
        "retries": 1,
        "retry_delay": timedelta(minutes=5),
    },
    tags=["profile-scorer", "stats"],
)
def keyword_stats_pipeline() -> None:
    """Keyword statistics update DAG."""

    @task
    def get_all_keywords() -> list[str]:
        """Get all distinct keywords from api_search_usage."""
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

    @task
    def calculate_keyword_stats(keyword: str) -> dict:
        """Calculate statistics for a single keyword."""
        with get_session() as session:
            # Profile counts and HAS averages
            profile_stats = (
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

            # Label rate calculation
            llm_stats = (
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

            # Pagination stats
            search_stats = (
                session.query(
                    func.max(ApiSearchUsage.page).label("pages_searched"),
                    func.min(ApiSearchUsage.query_at).label("first_search_at"),
                    func.max(ApiSearchUsage.query_at).label("last_search_at"),
                )
                .filter(ApiSearchUsage.keyword == keyword)
                .first()
            )

            # Check if keyword still has pages
            latest_page = (
                session.query(ApiSearchUsage)
                .filter(ApiSearchUsage.keyword == keyword)
                .order_by(ApiSearchUsage.page.desc())
                .first()
            )
            still_valid = latest_page is None or latest_page.next_page is not None

            # Calculate label rate
            total_labeled = llm_stats.total_labeled if llm_stats else 0
            true_labels = llm_stats.true_labels if llm_stats else 0
            label_rate = true_labels / total_labeled if total_labeled > 0 else 0.0

            return {
                "keyword": keyword,
                "profiles_found": profile_stats.profiles_found or 0,
                "avg_human_score": float(profile_stats.avg_human_score or 0),
                "label_rate": label_rate,
                "still_valid": still_valid,
                "pages_searched": search_stats.pages_searched or 0,
                "high_quality_count": profile_stats.high_quality_count or 0,
                "low_quality_count": profile_stats.low_quality_count or 0,
                "first_search_at": (
                    search_stats.first_search_at.isoformat()
                    if search_stats.first_search_at
                    else None
                ),
                "last_search_at": (
                    search_stats.last_search_at.isoformat()
                    if search_stats.last_search_at
                    else None
                ),
            }

    @task
    def upsert_keyword_stats(stats: dict) -> None:
        """Upsert keyword statistics to database."""
        with get_session() as session:
            existing = session.get(KeywordStats, stats["keyword"])

            if existing:
                # Update existing
                existing.profiles_found = stats["profiles_found"]
                existing.avg_human_score = Decimal(str(round(stats["avg_human_score"], 3)))
                existing.label_rate = Decimal(str(round(stats["label_rate"], 3)))
                existing.still_valid = stats["still_valid"]
                existing.pages_searched = stats["pages_searched"]
                existing.high_quality_count = stats["high_quality_count"]
                existing.low_quality_count = stats["low_quality_count"]
                if stats["first_search_at"]:
                    existing.first_search_at = datetime.fromisoformat(stats["first_search_at"])
                if stats["last_search_at"]:
                    existing.last_search_at = datetime.fromisoformat(stats["last_search_at"])
                existing.updated_at = datetime.utcnow()
            else:
                # Insert new
                keyword_stat = KeywordStats(
                    keyword=stats["keyword"],
                    profiles_found=stats["profiles_found"],
                    avg_human_score=Decimal(str(round(stats["avg_human_score"], 3))),
                    label_rate=Decimal(str(round(stats["label_rate"], 3))),
                    still_valid=stats["still_valid"],
                    pages_searched=stats["pages_searched"],
                    high_quality_count=stats["high_quality_count"],
                    low_quality_count=stats["low_quality_count"],
                    first_search_at=(
                        datetime.fromisoformat(stats["first_search_at"])
                        if stats["first_search_at"]
                        else None
                    ),
                    last_search_at=(
                        datetime.fromisoformat(stats["last_search_at"])
                        if stats["last_search_at"]
                        else None
                    ),
                )
                session.add(keyword_stat)

            log.info(
                "keyword_stats_updated",
                keyword=stats["keyword"],
                profiles=stats["profiles_found"],
                still_valid=stats["still_valid"],
            )

    @task
    def log_summary(keywords: list[str]) -> None:
        """Log pipeline summary."""
        log.info("keyword_stats_complete", keywords_updated=len(keywords))

    # DAG Task Flow
    keywords = get_all_keywords()

    # Calculate stats for each keyword (dynamic mapping)
    stats = calculate_keyword_stats.expand(keyword=keywords)

    # Upsert stats to database
    upsert_keyword_stats.expand(stats=stats)

    # Log summary
    log_summary(keywords)


# Instantiate DAG
keyword_stats_pipeline()
