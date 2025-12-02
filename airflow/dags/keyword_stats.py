"""Keyword Stats Update DAG.

Daily maintenance DAG that recalculates keyword statistics.

Flow:
1. get_all_keywords -> Get all keywords from api_search_usage
2. calculate_stats -> Calculate stats per keyword (parallel)
3. upsert_stats -> Update keyword_stats table (parallel)
4. summarize -> Log results

Schedule: Daily at 2 AM UTC
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.sdk import dag, task
from utils import get_logger

from tasks.stats import calculate_keyword_stats, get_all_keywords, upsert_keyword_stats

log = get_logger("dag.keyword_stats")


@dag(
    dag_id="keyword_stats_update",
    description="Daily update of keyword statistics",
    schedule="0 2 * * *",  # Daily at 2 AM UTC
    start_date=datetime(2025, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "airflow",
        "retries": 1,
        "retry_delay": timedelta(minutes=5),
    },
    tags=["profile-scorer", "stats", "maintenance"],
)
def keyword_stats_dag() -> None:
    """Keyword statistics update pipeline."""

    @task
    def get_keywords_task() -> list[str]:
        """Get all keywords to update.

        Returns:
            List of keyword strings.
        """
        log.info("task_get_all_keywords")
        keywords = get_all_keywords()
        log.info("keywords_to_update", count=len(keywords))
        return keywords

    @task
    def calculate_stats_task(keyword: str) -> dict:
        """Calculate stats for a keyword.

        Returns:
            Dict with calculated statistics.
        """
        log.debug("task_calculate_stats", keyword=keyword)
        stats = calculate_keyword_stats(keyword)

        return {
            "keyword": stats.keyword,
            "profiles_found": stats.profiles_found,
            "avg_human_score": stats.avg_human_score,
            "label_rate": stats.label_rate,
            "still_valid": stats.still_valid,
            "pages_searched": stats.pages_searched,
            "high_quality_count": stats.high_quality_count,
            "low_quality_count": stats.low_quality_count,
            "first_search_at": stats.first_search_at.isoformat() if stats.first_search_at else None,
            "last_search_at": stats.last_search_at.isoformat() if stats.last_search_at else None,
        }

    @task
    def upsert_stats_task(stats_dict: dict) -> dict:
        """Upsert keyword stats to database.

        Returns:
            Dict with upsert result.
        """
        from datetime import datetime as dt

        from tasks.stats import KeywordStatData

        # Reconstruct dataclass
        first_at = stats_dict["first_search_at"]
        last_at = stats_dict["last_search_at"]

        stats = KeywordStatData(
            keyword=stats_dict["keyword"],
            profiles_found=stats_dict["profiles_found"],
            avg_human_score=stats_dict["avg_human_score"],
            label_rate=stats_dict["label_rate"],
            still_valid=stats_dict["still_valid"],
            pages_searched=stats_dict["pages_searched"],
            high_quality_count=stats_dict["high_quality_count"],
            low_quality_count=stats_dict["low_quality_count"],
            first_search_at=dt.fromisoformat(first_at) if first_at else None,
            last_search_at=dt.fromisoformat(last_at) if last_at else None,
        )

        upsert_keyword_stats(stats)

        return {
            "keyword": stats.keyword,
            "still_valid": stats.still_valid,
            "profiles_found": stats.profiles_found,
        }

    @task
    def summarize_task(upsert_results: list[dict]) -> dict:
        """Summarize stats update results."""
        total_keywords = len(upsert_results)
        valid_keywords = len([r for r in upsert_results if r.get("still_valid")])
        exhausted_keywords = total_keywords - valid_keywords
        total_profiles = sum(r.get("profiles_found", 0) for r in upsert_results)

        log.info(
            "stats_update_summary",
            total_keywords=total_keywords,
            valid_keywords=valid_keywords,
            exhausted_keywords=exhausted_keywords,
            total_profiles=total_profiles,
        )

        return {
            "total_keywords": total_keywords,
            "valid_keywords": valid_keywords,
            "exhausted_keywords": exhausted_keywords,
            "total_profiles": total_profiles,
        }

    # DAG flow
    keywords = get_keywords_task()
    stats = calculate_stats_task.expand(keyword=keywords)
    upsert_results = upsert_stats_task.expand(stats_dict=stats)
    summarize_task(upsert_results)


# Instantiate DAG
keyword_stats_dag()
