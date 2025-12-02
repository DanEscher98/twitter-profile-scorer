"""Profile Scoring Pipeline DAG.

Replaces the Lambda-based orchestration with Airflow TaskFlow API.
Runs every 15 minutes to:
1. Select valid keywords from the database
2. Search Twitter API for each keyword and insert profiles
3. Score high-quality profiles with LLM models (probability-based)
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

from airflow.decorators import dag, task
from pydantic import BaseModel

if TYPE_CHECKING:
    from sqlmodel import Session

# Local imports (available when DAG is loaded)
from scorer_db import (
    ApiSearchUsage,
    KeywordStats,
    Platform,
    ProfileScore,
    ProfileToScore,
    UserKeyword,
    UserProfile,
    UserStats,
    get_session,
)
from scorer_has import compute_has
from scorer_llm.registry import ModelConfig, get_registry
from scorer_llm.types import AudienceConfig, ProfileToLabel
from scorer_twitter import TwitterApiUser, TwitterClient
from scorer_utils import get_logger

log = get_logger("dag.profile_scoring")


# =============================================================================
# Configuration
# =============================================================================


class ScoringConfig(BaseModel):
    """Pipeline configuration."""

    keyword_count: int = 5
    items_per_search: int = 20
    has_threshold: float = 0.65  # Minimum HAS to queue for LLM scoring
    llm_threshold: float = 0.55  # Minimum HAS for LLM scoring query
    default_audience: str = "thelai_customers.v3"


DEFAULT_CONFIG = ScoringConfig()


# =============================================================================
# DAG Definition
# =============================================================================


@dag(
    dag_id="profile_scoring",
    description="Twitter profile search and LLM scoring pipeline",
    schedule="*/15 * * * *",  # Every 15 minutes
    start_date=datetime(2025, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "airflow",
        "retries": 1,
        "retry_delay": timedelta(minutes=2),
    },
    tags=["profile-scorer", "twitter", "llm"],
)
def profile_scoring_pipeline() -> None:  # noqa: PLR0915
    """Profile scoring pipeline DAG."""

    @task
    def get_keywords(count: int = DEFAULT_CONFIG.keyword_count) -> list[str]:
        """Select valid keywords for search.

        Queries keyword_stats where still_valid=True, shuffles, and checks
        pagination availability for each keyword.
        """
        with get_session() as session:
            # Get valid keywords ordered by avg human score
            keywords = (
                session.query(KeywordStats)
                .filter(KeywordStats.still_valid.is_(True))
                .order_by(KeywordStats.avg_human_score.desc())
                .all()
            )

            if not keywords:
                log.warning("no_valid_keywords_found")
                return []

            # Shuffle for variety
            random.shuffle(keywords)

            # Filter by pagination availability
            selected: list[str] = []
            for kw in keywords:
                if len(selected) >= count:
                    break

                # Check if keyword still has pages
                latest = (
                    session.query(ApiSearchUsage)
                    .filter(ApiSearchUsage.keyword == kw.keyword)
                    .order_by(ApiSearchUsage.page.desc())
                    .first()
                )

                # Has pages if no search yet or has next_page cursor
                if latest is None or latest.next_page is not None:
                    selected.append(kw.keyword)
                else:
                    log.debug("keyword_exhausted", keyword=kw.keyword)

            log.info("keywords_selected", count=len(selected))
            return selected

    @task
    def search_profiles(keyword: str) -> dict[str, int]:
        """Search Twitter API for a keyword and insert profiles.

        Returns:
            Dict with new_profiles and human_profiles counts.
        """
        log.info("processing_keyword", keyword=keyword)

        with get_session() as session:
            # Get pagination state
            latest = (
                session.query(ApiSearchUsage)
                .filter(ApiSearchUsage.keyword == keyword)
                .order_by(ApiSearchUsage.page.desc())
                .first()
            )

            cursor = latest.next_page if latest else None
            page = (latest.page + 1) if latest else 0

            if latest and latest.next_page is None:
                log.warning("keyword_fully_paginated", keyword=keyword)
                return {"new_profiles": 0, "human_profiles": 0}

            # Search Twitter API
            with TwitterClient() as client:
                response = client.search_users(
                    keyword,
                    items=DEFAULT_CONFIG.items_per_search,
                    cursor=cursor,
                )

            if not response.users:
                log.info("no_users_found", keyword=keyword)
                return {"new_profiles": 0, "human_profiles": 0}

            # Count new profiles before inserting metadata
            new_count = 0
            for user in response.users:
                existing = session.get(UserProfile, user.rest_id)
                if existing is None:
                    new_count += 1

            # Generate IDs hash
            ids_list = sorted(u.rest_id for u in response.users)
            ids_hash = hashlib.md5(  # noqa: S324
                ",".join(ids_list).encode()
            ).hexdigest()[:16]

            # Insert search metadata (MUST be before user operations - FK constraint)
            search_id = uuid4()
            search_record = ApiSearchUsage(
                id=search_id,
                ids_hash=ids_hash,
                keyword=keyword,
                items=DEFAULT_CONFIG.items_per_search,
                retries=1,
                next_page=response.next_cursor,
                page=page,
                new_profiles=new_count,
                platform=Platform.TWITTER,
            )
            session.add(search_record)
            session.flush()  # Ensure search record is available for FK

            # Process users
            human_count = 0
            for user in response.users:
                profile, _is_new = _upsert_user(session, user, keyword, search_id)

                # Upsert stats
                _upsert_stats(session, user)

                # Queue for LLM if high HAS
                if (
                    profile.human_score is not None
                    and float(profile.human_score) > DEFAULT_CONFIG.has_threshold
                ):
                    human_count += 1
                    _queue_for_scoring(session, profile)

            log.info(
                "search_complete",
                keyword=keyword,
                users=len(response.users),
                new=new_count,
                human=human_count,
            )

            return {"new_profiles": new_count, "human_profiles": human_count}

    @task
    def score_profiles_with_model(model_alias: str) -> dict[str, int]:
        """Score profiles using a specific LLM model.

        Uses probability-based invocation from model registry.
        """
        registry = get_registry()
        config = registry.resolve(model_alias)

        # Probability check (S311 acceptable - not cryptographic)
        roll = random.random()  # noqa: S311
        if roll >= config.probability:
            log.info(
                "model_skipped_by_probability",
                model=model_alias,
                roll=round(roll, 3),
                threshold=config.probability,
            )
            return {"labeled": 0, "errors": 0, "skipped": True}

        log.info(
            "model_selected",
            model=model_alias,
            full_name=config.full_name,
            roll=round(roll, 3),
        )

        with get_session() as session:
            # Get profiles to score
            profiles = _get_profiles_to_score(
                session,
                config.full_name,
                config.default_batch_size,
            )

            if not profiles:
                log.info("no_profiles_to_score", model=model_alias)
                return {"labeled": 0, "errors": 0, "skipped": False}

            # Load audience config
            audience_config = _load_audience_config(DEFAULT_CONFIG.default_audience)

            # Label with LLM
            labels = _label_profiles(profiles, config, audience_config)

            # Store results
            labeled = 0
            errors = 0
            for label_result in labels:
                try:
                    _insert_label(
                        session,
                        label_result,
                        config.full_name,
                        audience_config.config_name,
                    )
                    labeled += 1
                except Exception as e:
                    log.exception("label_insert_error", error=str(e))
                    errors += 1

            log.info(
                "scoring_complete",
                model=model_alias,
                labeled=labeled,
                errors=errors,
            )

            return {"labeled": labeled, "errors": errors, "skipped": False}

    @task
    def aggregate_results(
        search_results: list[dict[str, int]],
        scoring_results: list[dict[str, int]],
    ) -> dict[str, int]:
        """Aggregate pipeline results for logging."""
        total_new = sum(r.get("new_profiles", 0) for r in search_results)
        total_human = sum(r.get("human_profiles", 0) for r in search_results)
        total_labeled = sum(r.get("labeled", 0) for r in scoring_results)
        total_errors = sum(r.get("errors", 0) for r in scoring_results)

        log.info(
            "pipeline_complete",
            new_profiles=total_new,
            human_profiles=total_human,
            labeled=total_labeled,
            errors=total_errors,
        )

        return {
            "new_profiles": total_new,
            "human_profiles": total_human,
            "labeled": total_labeled,
            "errors": total_errors,
        }

    # DAG Task Flow
    keywords = get_keywords()

    # Search profiles for each keyword (dynamic mapping)
    search_results = search_profiles.expand(keyword=keywords)

    # Score with multiple models (probability-based)
    model_aliases = ["meta-maverick-17b", "claude-haiku-4.5", "gemini-flash-2.0"]
    scoring_results = score_profiles_with_model.expand(model_alias=model_aliases)

    # Aggregate and log results
    aggregate_results(search_results, scoring_results)


# =============================================================================
# Helper Functions
# =============================================================================


def _upsert_user(
    session: Session,
    user: TwitterApiUser,
    keyword: str,
    search_id: str,
) -> tuple[UserProfile, bool]:
    """Upsert user profile and create keyword association.

    Returns:
        Tuple of (profile, is_new).
    """
    # Compute HAS
    has_result = compute_has(
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
        bio=user.legacy.description,
        created_at=user.legacy.created_at,
    )

    # Check if profile exists
    existing = session.get(UserProfile, user.rest_id)
    is_new = existing is None

    if is_new:
        # Insert new profile
        profile = UserProfile(
            twitter_id=user.rest_id,
            handle=user.legacy.screen_name,
            name=user.legacy.name,
            bio=user.legacy.description,
            created_at=user.legacy.created_at,
            follower_count=user.legacy.followers_count,
            location=user.legacy.location,
            can_dm=user.legacy.can_dm,
            category=_extract_category(user),
            human_score=Decimal(str(round(has_result.score, 4))),
            likely_is=has_result.likely_is,
            got_by_keywords=[keyword],
            platform=Platform.TWITTER,
        )
        session.add(profile)
    else:
        profile = existing
        # Append keyword if not already present
        if profile.got_by_keywords is None:
            profile.got_by_keywords = [keyword]
        elif keyword not in profile.got_by_keywords:
            profile.got_by_keywords = [*profile.got_by_keywords, keyword]
        profile.updated_at = datetime.utcnow()

    # Add keyword association
    user_keyword = UserKeyword(
        twitter_id=user.rest_id,
        keyword=keyword,
        search_id=search_id,
    )
    session.merge(user_keyword)

    return profile, is_new


def _upsert_stats(session: Session, user: TwitterApiUser) -> None:
    """Upsert user stats."""
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


def _queue_for_scoring(session: Session, profile: UserProfile) -> None:
    """Queue profile for LLM scoring if not already queued."""
    existing = (
        session.query(ProfileToScore)
        .filter(ProfileToScore.twitter_id == profile.twitter_id)
        .first()
    )

    if existing is None:
        to_score = ProfileToScore(
            twitter_id=profile.twitter_id,
            handle=profile.handle,
        )
        session.add(to_score)


def _extract_category(user: TwitterApiUser) -> str | None:
    """Extract category from user's professional field."""
    if user.professional and user.professional.category:
        categories = user.professional.category
        if categories:
            return categories[0].name
    return None


def _get_profiles_to_score(
    session: Session,
    model_full_name: str,
    batch_size: int,
) -> list[ProfileToLabel]:
    """Get profiles that haven't been scored by this model."""
    from sqlalchemy import and_, func

    # Subquery for already-scored profiles
    scored_subq = (
        session.query(ProfileScore.twitter_id)
        .filter(ProfileScore.scored_by == model_full_name)
        .subquery()
    )

    # Query profiles in queue, not scored by this model, with valid data
    profiles = (
        session.query(
            UserProfile.twitter_id,
            UserProfile.handle,
            UserProfile.name,
            UserProfile.bio,
            UserProfile.category,
            UserProfile.likely_is,
            UserStats.followers,
        )
        .join(ProfileToScore, ProfileToScore.twitter_id == UserProfile.twitter_id)
        .outerjoin(UserStats, UserStats.twitter_id == UserProfile.twitter_id)
        .filter(
            and_(
                UserProfile.twitter_id.notin_(scored_subq),
                UserProfile.human_score > str(DEFAULT_CONFIG.llm_threshold),
                UserProfile.bio.isnot(None),
                UserProfile.bio != "",
                UserProfile.name.isnot(None),
                UserProfile.name != "",
            )
        )
        .order_by(func.random())
        .limit(batch_size)
        .all()
    )

    return [
        ProfileToLabel(
            twitter_id=p.twitter_id,
            handle=p.handle,
            name=p.name or "",
            bio=p.bio,
            category=p.category,
            followers=p.followers or 0,
            likely_is=p.likely_is or "Other",
        )
        for p in profiles
    ]


def _load_audience_config(config_name: str) -> AudienceConfig:
    """Load audience configuration from JSON file."""
    import json
    from pathlib import Path

    # Search paths for audience configs
    search_paths = [
        Path("/opt/airflow/audiences") / f"{config_name}.json",
        Path(__file__).parent / "audiences" / f"{config_name}.json",
        Path.cwd() / "audiences" / f"{config_name}.json",
    ]

    for path in search_paths:
        if path.exists():
            with path.open() as f:
                data = json.load(f)
            config = AudienceConfig.model_validate(data)
            config.config_name = config_name
            return config

    msg = f"Audience config not found: {config_name}"
    raise FileNotFoundError(msg)


def _label_profiles(
    profiles: list[ProfileToLabel],
    model_config: ModelConfig,
    audience_config: AudienceConfig,
) -> list[dict]:
    """Label profiles using LLM.

    Returns list of dicts with twitter_id, label, reason.
    """
    from scorer_llm.labeler import label_batch

    return label_batch(profiles, model_config, audience_config)


def _insert_label(
    session: Session,
    label_result: dict,
    model_full_name: str,
    audience_name: str,
) -> None:
    """Insert or update profile label."""
    # Check for existing
    existing = (
        session.query(ProfileScore)
        .filter(
            ProfileScore.twitter_id == label_result["twitter_id"],
            ProfileScore.scored_by == model_full_name,
        )
        .first()
    )

    if existing:
        # Update existing
        existing.label = label_result["label"]
        existing.reason = label_result["reason"]
        existing.audience = audience_name
        existing.scored_at = datetime.utcnow()
    else:
        # Insert new
        score = ProfileScore(
            twitter_id=label_result["twitter_id"],
            label=label_result["label"],
            reason=label_result["reason"],
            scored_by=model_full_name,
            audience=audience_name,
        )
        session.add(score)


# Instantiate DAG
profile_scoring_pipeline()
