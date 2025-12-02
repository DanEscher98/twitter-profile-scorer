"""Tests for Airflow DAGs.

These tests verify DAG structure and integrity following Airflow testing best practices.
"""

from __future__ import annotations

import pytest
from airflow.models import DagBag


@pytest.fixture(scope="module")
def dag_bag() -> DagBag:
    """Load all DAGs from the dags folder."""
    return DagBag(dag_folder="dags", include_examples=False)


class TestDagIntegrity:
    """Test DAG integrity and structure."""

    def test_dag_bag_import(self, dag_bag: DagBag) -> None:
        """Test that all DAGs can be imported without errors."""
        assert dag_bag.import_errors == {}, f"DAG import errors: {dag_bag.import_errors}"

    def test_profile_search_dag_exists(self, dag_bag: DagBag) -> None:
        """Test profile_search DAG exists."""
        assert "profile_search" in dag_bag.dags
        dag = dag_bag.dags["profile_search"]
        assert dag is not None

    def test_llm_scoring_dag_exists(self, dag_bag: DagBag) -> None:
        """Test llm_scoring DAG exists."""
        assert "llm_scoring" in dag_bag.dags
        dag = dag_bag.dags["llm_scoring"]
        assert dag is not None

    def test_keyword_stats_dag_exists(self, dag_bag: DagBag) -> None:
        """Test keyword_stats_update DAG exists."""
        assert "keyword_stats_update" in dag_bag.dags
        dag = dag_bag.dags["keyword_stats_update"]
        assert dag is not None


class TestProfileSearchDag:
    """Tests specific to profile_search DAG."""

    def test_dag_schedule(self, dag_bag: DagBag) -> None:
        """Test DAG schedule interval."""
        dag = dag_bag.dags["profile_search"]
        assert dag.schedule == "*/15 * * * *"

    def test_dag_catchup_disabled(self, dag_bag: DagBag) -> None:
        """Test DAG catchup is disabled."""
        dag = dag_bag.dags["profile_search"]
        assert dag.catchup is False

    def test_dag_max_active_runs(self, dag_bag: DagBag) -> None:
        """Test DAG max active runs."""
        dag = dag_bag.dags["profile_search"]
        assert dag.max_active_runs == 1

    def test_dag_has_tasks(self, dag_bag: DagBag) -> None:
        """Test DAG has expected tasks."""
        dag = dag_bag.dags["profile_search"]
        task_ids = [task.task_id for task in dag.tasks]

        assert "get_keywords_task" in task_ids
        assert "search_profiles_task" in task_ids
        assert "store_results_task" in task_ids
        assert "summarize_task" in task_ids


class TestLlmScoringDag:
    """Tests specific to llm_scoring DAG."""

    def test_dag_schedule(self, dag_bag: DagBag) -> None:
        """Test DAG schedule interval."""
        dag = dag_bag.dags["llm_scoring"]
        # Offset schedule: 7,22,37,52 * * * *
        assert "7,22,37,52" in dag.schedule

    def test_dag_catchup_disabled(self, dag_bag: DagBag) -> None:
        """Test DAG catchup is disabled."""
        dag = dag_bag.dags["llm_scoring"]
        assert dag.catchup is False

    def test_dag_has_tasks(self, dag_bag: DagBag) -> None:
        """Test DAG has expected tasks."""
        dag = dag_bag.dags["llm_scoring"]
        task_ids = [task.task_id for task in dag.tasks]

        assert "get_model_configs_task" in task_ids
        assert "score_with_model_task" in task_ids
        assert "summarize_task" in task_ids


class TestKeywordStatsDag:
    """Tests specific to keyword_stats_update DAG."""

    def test_dag_schedule(self, dag_bag: DagBag) -> None:
        """Test DAG schedule interval (daily at 2 AM)."""
        dag = dag_bag.dags["keyword_stats_update"]
        assert dag.schedule == "0 2 * * *"

    def test_dag_catchup_disabled(self, dag_bag: DagBag) -> None:
        """Test DAG catchup is disabled."""
        dag = dag_bag.dags["keyword_stats_update"]
        assert dag.catchup is False

    def test_dag_has_tasks(self, dag_bag: DagBag) -> None:
        """Test DAG has expected tasks."""
        dag = dag_bag.dags["keyword_stats_update"]
        task_ids = [task.task_id for task in dag.tasks]

        assert "get_keywords_task" in task_ids
        assert "calculate_stats_task" in task_ids
        assert "upsert_stats_task" in task_ids
        assert "summarize_task" in task_ids


class TestDagTags:
    """Test DAG tags for organization."""

    def test_profile_search_tags(self, dag_bag: DagBag) -> None:
        """Test profile_search DAG has correct tags."""
        dag = dag_bag.dags["profile_search"]
        assert "profile-scorer" in dag.tags

    def test_llm_scoring_tags(self, dag_bag: DagBag) -> None:
        """Test llm_scoring DAG has correct tags."""
        dag = dag_bag.dags["llm_scoring"]
        assert "profile-scorer" in dag.tags
        assert "llm" in dag.tags

    def test_keyword_stats_tags(self, dag_bag: DagBag) -> None:
        """Test keyword_stats_update DAG has correct tags."""
        dag = dag_bag.dags["keyword_stats_update"]
        assert "profile-scorer" in dag.tags
        assert "stats" in dag.tags
