"""Profile Search DAG.

Searches for profiles on social media platforms and stores them in the database.

Flow:
1. get_keywords -> Sample valid keywords per platform
2. search_profiles -> Query API for each keyword (parallel)
3. store_results -> Persist profiles and queue for scoring (parallel)

Schedule: Every 15 minutes
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.sdk import dag, task
from db import Platform
from utils import get_logger

from tasks import get_config
from tasks.keywords import get_pagination_state, get_valid_keywords
from tasks.search import SearchResult, process_profiles, search_profiles_for_keyword
from tasks.storage import StorageResult, store_search_results

log = get_logger("dag.profile_search")


@dag(
    dag_id="profile_search",
    description="Search social media APIs for profiles matching keywords",
    schedule="*/15 * * * *",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "airflow",
        "retries": 2,
        "retry_delay": timedelta(minutes=2),
    },
    tags=["profile-scorer", "search", "twitter"],
)
def profile_search_dag() -> None:
    """Profile search pipeline."""
    config = get_config()

    @task
    def get_keywords_task(
        count: int = config.keyword_count,
        platform: str = Platform.TWITTER.value,
    ) -> list[str]:
        """Get valid keywords for searching.

        Returns:
            List of keyword strings to search.
        """
        log.info("task_get_keywords", count=count, platform=platform)
        keywords = get_valid_keywords(count, Platform(platform))

        if not keywords:
            log.warning("no_keywords_available")

        return keywords

    @task
    def search_profiles_task(keyword: str, platform: str = Platform.TWITTER.value) -> dict:
        """Search API for profiles matching keyword.

        Returns:
            Dict with search metadata and serialized results.
        """
        log.info("task_search_profiles", keyword=keyword, platform=platform)

        # Get pagination state
        cursor, page = get_pagination_state(keyword, Platform(platform))

        if cursor is None and page > 0:
            log.warning("keyword_exhausted", keyword=keyword)
            return {
                "keyword": keyword,
                "platform": platform,
                "users_found": 0,
                "has_next": False,
                "skipped": True,
            }

        # Search API
        search_result = search_profiles_for_keyword(
            keyword,
            items=config.items_per_search,
            cursor=cursor,
            page=page,
            platform=Platform(platform),
        )

        # Process profiles (compute HAS)
        processed = process_profiles(search_result)

        # Serialize for XCom
        return {
            "keyword": keyword,
            "platform": platform,
            "page": search_result.page,
            "users_found": len(search_result.users),
            "has_next": search_result.next_cursor is not None,
            "next_cursor": search_result.next_cursor,
            "skipped": False,
            # Serialize processed profiles
            "profiles": [
                {
                    "user_rest_id": p.user.rest_id,
                    "user_legacy": p.user.legacy.model_dump(),
                    "user_is_blue_verified": p.user.is_blue_verified,
                    "user_professional": (
                        p.user.professional.model_dump() if p.user.professional else None
                    ),
                    "has_score": str(p.has_result.score),
                    "has_likely_is": p.has_result.likely_is,
                    "keyword": p.keyword,
                    "platform": p.platform.value,
                }
                for p in processed
            ],
        }

    @task
    def store_results_task(search_data: dict) -> dict:
        """Store search results in database.

        Returns:
            Dict with storage statistics.
        """
        if search_data.get("skipped"):
            return {
                "keyword": search_data["keyword"],
                "new_profiles": 0,
                "updated_profiles": 0,
                "queued_for_scoring": 0,
                "skipped": True,
            }

        log.info("task_store_results", keyword=search_data["keyword"])

        # Reconstruct objects from serialized data
        from decimal import Decimal

        from db import Platform as PlatformEnum
        from scoring.heuristic import HASResult, HASScoreBreakdown
        from search_profiles import TwitterApiUser, TwitterLegacy, TwitterProfessional

        from tasks.search import ProcessedProfile

        profiles = search_data.get("profiles", [])
        if not profiles:
            return {
                "keyword": search_data["keyword"],
                "new_profiles": 0,
                "updated_profiles": 0,
                "queued_for_scoring": 0,
                "skipped": False,
            }

        # Reconstruct ProcessedProfile objects
        processed_profiles = []
        for p in profiles:
            professional = None
            if p["user_professional"]:
                professional = TwitterProfessional.model_validate(p["user_professional"])

            user = TwitterApiUser(
                rest_id=p["user_rest_id"],
                is_blue_verified=p["user_is_blue_verified"],
                legacy=TwitterLegacy.model_validate(p["user_legacy"]),
                professional=professional,
            )

            # Create minimal HASResult (we only need score and likely_is)
            has_result = HASResult(
                score=Decimal(p["has_score"]),
                likely_is=p["has_likely_is"],
                breakdown=HASScoreBreakdown(
                    bio_score=Decimal(0),
                    engagement_score=Decimal(0),
                    account_age_score=Decimal(0),
                    verification_score=Decimal(0),
                ),
            )

            processed_profiles.append(
                ProcessedProfile(
                    user=user,
                    has_result=has_result,
                    keyword=p["keyword"],
                    platform=PlatformEnum(p["platform"]),
                )
            )

        # Create SearchResult for metadata
        search_result = SearchResult(
            users=[p.user for p in processed_profiles],
            next_cursor=search_data.get("next_cursor"),
            keyword=search_data["keyword"],
            platform=PlatformEnum(search_data["platform"]),
            page=search_data["page"],
        )

        # Store
        config = get_config()
        result: StorageResult = store_search_results(
            search_result,
            processed_profiles,
            has_threshold=config.has_threshold,
        )

        return {
            "keyword": search_data["keyword"],
            "new_profiles": result.new_profiles,
            "updated_profiles": result.updated_profiles,
            "queued_for_scoring": result.queued_for_scoring,
            "skipped": False,
        }

    @task
    def summarize_task(storage_results: list[dict]) -> dict:
        """Summarize pipeline results."""
        total_new = sum(r.get("new_profiles", 0) for r in storage_results)
        total_updated = sum(r.get("updated_profiles", 0) for r in storage_results)
        total_queued = sum(r.get("queued_for_scoring", 0) for r in storage_results)
        keywords_processed = len([r for r in storage_results if not r.get("skipped")])

        log.info(
            "pipeline_summary",
            keywords_processed=keywords_processed,
            new_profiles=total_new,
            updated_profiles=total_updated,
            queued_for_scoring=total_queued,
        )

        return {
            "keywords_processed": keywords_processed,
            "new_profiles": total_new,
            "updated_profiles": total_updated,
            "queued_for_scoring": total_queued,
        }

    # DAG flow
    keywords = get_keywords_task()
    search_results = search_profiles_task.expand(keyword=keywords)
    storage_results = store_results_task.expand(search_data=search_results)
    summarize_task(storage_results)


# Instantiate DAG
profile_search_dag()
