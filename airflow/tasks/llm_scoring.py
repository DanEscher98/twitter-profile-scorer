"""LLM scoring logic.

This module handles:
- Fetching profiles to score from the queue
- Loading audience configurations
- Calling LLM providers for labeling
- NO database writes (that's handled by storage.py)
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from db import ProfileScore, ProfileToScore, UserProfile, UserStats, get_session
from scoring.llm import AudienceConfig, LabelBatchResponse, ModelConfig, label_batch
from scoring.llm.registry import get_registry
from sqlalchemy import and_, func
from utils import get_logger

if TYPE_CHECKING:
    from sqlmodel import Session

log = get_logger("tasks.llm_scoring")


@dataclass
class ScoringResult:
    """Result from LLM scoring operation."""

    model_alias: str
    model_full_name: str
    profiles_fetched: int
    labels_produced: int
    input_tokens: int
    output_tokens: int
    call_cost: float
    skipped: bool
    skip_reason: str | None


def should_invoke_model(model_alias: str) -> tuple[bool, float]:
    """Check if model should be invoked based on probability.

    Args:
        model_alias: Model alias to check.

    Returns:
        Tuple of (should_invoke, roll_value).
    """
    registry = get_registry()
    config = registry.resolve(model_alias)

    roll = random.random()  # noqa: S311
    should_invoke = roll < config.probability

    log.info(
        "probability_check",
        model=model_alias,
        roll=round(roll, 3),
        threshold=config.probability,
        invoke=should_invoke,
    )

    return should_invoke, roll


def fetch_profiles_to_score(
    model_full_name: str,
    batch_size: int,
    llm_threshold: float = 0.55,
) -> list[dict]:
    """Fetch profiles that haven't been scored by this model.

    Args:
        model_full_name: Full model name to check against scored_by.
        batch_size: Maximum profiles to fetch.
        llm_threshold: Minimum HAS for inclusion.

    Returns:
        List of profile dicts ready for labeling.
    """
    log.info(
        "fetching_profiles_to_score",
        model=model_full_name,
        batch_size=batch_size,
    )

    with get_session() as session:
        profiles = _query_unscored_profiles(session, model_full_name, batch_size, llm_threshold)

        log.info("profiles_fetched", count=len(profiles))

        return [
            {
                "twitter_id": p.twitter_id,
                "handle": p.handle,
                "name": p.name or "",
                "bio": p.bio,
                "category": p.category,
                "followers": p.followers or 0,
                "likely_is": p.likely_is or "Other",
            }
            for p in profiles
        ]


def _query_unscored_profiles(
    session: Session,
    model_full_name: str,
    batch_size: int,
    llm_threshold: float,
) -> list:
    """Query profiles not yet scored by this model."""
    # Subquery for already-scored profiles
    scored_subq = (
        session.query(ProfileScore.twitter_id)
        .filter(ProfileScore.scored_by == model_full_name)
        .subquery()
    )

    # Query profiles in queue, not scored by this model, with valid data
    return (
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
                UserProfile.human_score > str(llm_threshold),
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


def load_audience_config(config_name: str) -> AudienceConfig:
    """Load audience configuration from JSON file.

    Args:
        config_name: Config name without extension (e.g., "thelai_customers.v3").

    Returns:
        Validated AudienceConfig.

    Raises:
        FileNotFoundError: If config file not found.
    """
    search_paths = [
        Path("/opt/airflow/audiences") / f"{config_name}.json",
        Path(__file__).parent.parent / "dags" / "audiences" / f"{config_name}.json",
        Path.cwd() / "audiences" / f"{config_name}.json",
    ]

    for path in search_paths:
        if path.exists():
            log.debug("loading_audience_config", path=str(path))
            with path.open() as f:
                data = json.load(f)
            # Set metadata - need to create new instance since frozen
            return AudienceConfig.model_validate({**data, "config_name": config_name})

    msg = f"Audience config not found: {config_name}"
    raise FileNotFoundError(msg)


def score_profiles_with_llm(
    model_alias: str,
    profiles: list[dict],
    audience_config: AudienceConfig,
) -> LabelBatchResponse:
    """Score profiles using an LLM model.

    Args:
        model_alias: Model alias to use.
        profiles: Profiles to score (as dicts).
        audience_config: Audience configuration for prompts.

    Returns:
        LabelBatchResponse with results and token/cost metadata.
    """
    from scoring.llm.types import LabelMetadata, ProfileToLabel

    registry = get_registry()
    model_config: ModelConfig = registry.resolve(model_alias)

    empty_response = LabelBatchResponse(
        results=[],
        metadata=LabelMetadata(input_tokens=0, output_tokens=0, call_cost=0.0),
        model=model_alias,
    )

    if not profiles:
        log.info("no_profiles_to_score", model=model_alias)
        return empty_response

    log.info(
        "scoring_with_llm",
        model=model_alias,
        full_name=model_config.full_name,
        profiles=len(profiles),
    )

    # Convert dicts to ProfileToLabel objects
    profile_objects = [
        ProfileToLabel(
            twitter_id=p["twitter_id"],
            handle=p["handle"],
            name=p["name"],
            bio=p["bio"],
            category=p["category"],
            followers=p["followers"],
            likely_is=p["likely_is"],
        )
        for p in profiles
    ]

    # Call LLM - returns LabelBatchResponse with results and metadata
    response = label_batch(profile_objects, model_config, audience_config)

    # Log token usage and cost
    log.info(
        "llm_scoring_complete",
        model=model_alias,
        input_count=len(profiles),
        output_count=len(response.results),
        input_tokens=response.metadata.input_tokens,
        output_tokens=response.metadata.output_tokens,
        call_cost=f"${response.metadata.call_cost:.6f}",
    )

    return response
