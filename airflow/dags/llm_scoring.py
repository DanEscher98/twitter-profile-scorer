"""LLM Scoring DAG.

Scores profiles in the queue using multiple LLM providers.

Flow:
1. check_queue -> Check if profiles need scoring
2. score_with_model -> Score with each model (parallel, probability-based)
3. store_results -> Persist labels to database
4. summarize -> Log results

Schedule: Every 15 minutes (offset from search)
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.sdk import dag, task
from scoring.llm.registry import get_registry
from utils import get_logger

from tasks import get_config
from tasks.llm_scoring import (
    fetch_profiles_to_score,
    load_audience_config,
    score_profiles_with_llm,
    should_invoke_model,
)
from tasks.storage import store_label_results

log = get_logger("dag.llm_scoring")


@dag(
    dag_id="llm_scoring",
    description="Score queued profiles using LLM providers",
    schedule="7,22,37,52 * * * *",  # Offset from profile_search
    start_date=datetime(2025, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "airflow",
        "retries": 1,
        "retry_delay": timedelta(minutes=3),
    },
    tags=["profile-scorer", "llm", "scoring"],
)
def llm_scoring_dag() -> None:
    """LLM scoring pipeline."""
    config = get_config()

    @task
    def get_model_configs_task() -> list[dict]:
        """Get model configurations for scoring.

        Returns:
            List of model config dicts.
        """
        registry = get_registry()
        configs = []

        for alias in config.model_aliases:
            model_config = registry.resolve(alias)
            configs.append({
                "alias": model_config.alias,
                "full_name": model_config.full_name,
                "probability": model_config.probability,
                "batch_size": model_config.default_batch_size,
            })

        log.info("models_configured", count=len(configs))
        return configs

    @task
    def score_with_model_task(model_config: dict) -> dict:
        """Score profiles with a specific model.

        Uses probability-based invocation.

        Returns:
            Dict with scoring results including token usage and cost.
        """
        alias = model_config["alias"]
        full_name = model_config["full_name"]
        batch_size = model_config["batch_size"]

        log.info("task_score_with_model", model=alias)

        # Check probability
        should_invoke, roll = should_invoke_model(alias)
        if not should_invoke:
            return {
                "model_alias": alias,
                "model_full_name": full_name,
                "profiles_fetched": 0,
                "labels_produced": 0,
                "labels_stored": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "call_cost": 0.0,
                "skipped": True,
                "skip_reason": f"probability_check_failed (roll={roll:.3f})",
            }

        # Fetch profiles
        config = get_config()
        profiles = fetch_profiles_to_score(
            full_name,
            batch_size,
            llm_threshold=config.llm_threshold,
        )

        if not profiles:
            return {
                "model_alias": alias,
                "model_full_name": full_name,
                "profiles_fetched": 0,
                "labels_produced": 0,
                "labels_stored": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "call_cost": 0.0,
                "skipped": False,
                "skip_reason": "no_profiles_in_queue",
            }

        # Load audience config
        audience_config = load_audience_config(config.default_audience)

        # Score with LLM - returns LabelBatchResponse with metadata
        response = score_profiles_with_llm(alias, profiles, audience_config)

        # Store results
        stored = 0
        if response.results:
            # Convert LabelResult objects to dicts for storage
            labels_dicts = [
                {
                    "twitter_id": r.twitter_id,
                    "label": r.label,
                    "reason": r.reason,
                }
                for r in response.results
            ]
            stored = store_label_results(labels_dicts, full_name, config.default_audience)

        # Log cost info
        log.info(
            "model_scoring_cost",
            model=alias,
            input_tokens=response.metadata.input_tokens,
            output_tokens=response.metadata.output_tokens,
            call_cost=f"${response.metadata.call_cost:.6f}",
        )

        return {
            "model_alias": alias,
            "model_full_name": full_name,
            "profiles_fetched": len(profiles),
            "labels_produced": len(response.results),
            "labels_stored": stored,
            "input_tokens": response.metadata.input_tokens,
            "output_tokens": response.metadata.output_tokens,
            "call_cost": response.metadata.call_cost,
            "skipped": False,
            "skip_reason": None,
        }

    @task
    def summarize_task(scoring_results: list[dict]) -> dict:
        """Summarize scoring results including token usage and costs."""
        total_fetched = sum(r.get("profiles_fetched", 0) for r in scoring_results)
        total_labeled = sum(r.get("labels_produced", 0) for r in scoring_results)
        total_stored = sum(r.get("labels_stored", 0) for r in scoring_results)
        total_input_tokens = sum(r.get("input_tokens", 0) for r in scoring_results)
        total_output_tokens = sum(r.get("output_tokens", 0) for r in scoring_results)
        total_cost = sum(r.get("call_cost", 0.0) for r in scoring_results)
        models_invoked = len([r for r in scoring_results if not r.get("skipped")])
        models_skipped = len([r for r in scoring_results if r.get("skipped")])

        log.info(
            "scoring_summary",
            models_invoked=models_invoked,
            models_skipped=models_skipped,
            profiles_fetched=total_fetched,
            labels_produced=total_labeled,
            labels_stored=total_stored,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            total_cost=f"${total_cost:.6f}",
        )

        return {
            "models_invoked": models_invoked,
            "models_skipped": models_skipped,
            "profiles_fetched": total_fetched,
            "labels_produced": total_labeled,
            "labels_stored": total_stored,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_cost": total_cost,
        }

    # DAG flow
    model_configs = get_model_configs_task()
    scoring_results = score_with_model_task.expand(model_config=model_configs)
    summarize_task(scoring_results)


# Instantiate DAG
llm_scoring_dag()
