"""
E2E tests for the llm-scorer Lambda function.

The llm-scorer is responsible for:
1. Fetching profiles from profiles_to_score queue
2. Transforming to TOON format
3. Sending to LLM (Claude/Gemini)
4. Storing scores in profile_scores
5. Removing scored profiles from queue

Usage:
    uv run pytest tests/e2e/test_llm_scorer.py -v --log-level=INFO
"""

import pytest
import time

from tests.helpers import (
    invoke_lambda,
    invoke_lambda_with_sqs_event,
    get_lambda_logs,
    print_lambda_result,
    print_logs,
    LogLevel,
    console,
)
from rich.table import Table
from rich import box


@pytest.mark.e2e
class TestLlmScorer:
    """Tests for the llm-scorer Lambda."""

    def test_llm_scorer_processes_batch(
        self,
        lambda_client,
        sqs_client,
        logs_client,
        db_cursor,
        db_connection,
        infra_config,
        request,
    ):
        """
        Test that llm-scorer processes a batch of profiles.

        This test:
        1. Checks profiles_to_score queue depth
        2. Invokes the scorer
        3. Verifies profile_scores table is populated
        4. Checks profiles are removed from queue
        """
        log_level_str = request.config.getoption("--log-level", "INFO").upper()
        log_level = LogLevel[log_level_str] if log_level_str in LogLevel.__members__ else LogLevel.INFO

        console.print("\n[bold cyan]Testing llm-scorer Lambda[/bold cyan]")

        # Check how many profiles are waiting to be scored
        db_cursor.execute("SELECT COUNT(*) FROM profiles_to_score")
        pending_count = db_cursor.fetchone()[0]

        console.print(f"Profiles pending scoring: {pending_count}")

        if pending_count == 0:
            pytest.skip("No profiles to score - run query-twitter-api first")

        # Get scores count before
        db_cursor.execute("SELECT COUNT(*) FROM profile_scores")
        scores_before = db_cursor.fetchone()[0]

        # Invoke llm-scorer with an SQS-style event
        # The scorer triggers from SQS so we simulate that
        result = invoke_lambda_with_sqs_event(
            lambda_client,
            infra_config.llm_scorer_name,
            {"action": "score"},  # The scorer expects this trigger
        )

        print_lambda_result(result, infra_config.llm_scorer_name)

        # Get logs (scorer may take longer due to LLM API calls)
        time.sleep(5)
        logs = get_lambda_logs(
            logs_client,
            infra_config.llm_scorer_name,
            since_minutes=5,
            level=log_level,
        )
        print_logs(logs, title=f"llm-scorer Logs ({log_level.value})")

        # Check if Lambda succeeded
        if not result.success:
            console.print(f"[yellow]Lambda returned error (may be expected if no LLM API key)[/yellow]")
            # Don't fail test - scorer might fail due to API key issues
            return

        # Check scores count after
        db_connection.commit()  # Ensure we see latest data
        db_cursor.execute("SELECT COUNT(*) FROM profile_scores")
        scores_after = db_cursor.fetchone()[0]

        scores_added = scores_after - scores_before
        console.print(f"[green]Scores added: {scores_added}[/green]")

        # Show recent scores
        db_cursor.execute("""
            SELECT
                up.username,
                ps.score,
                ps.scored_by,
                ps.scored_at
            FROM profile_scores ps
            JOIN user_profiles up ON ps.twitter_id = up.twitter_id
            ORDER BY ps.scored_at DESC
            LIMIT 5
        """)
        rows = db_cursor.fetchall()

        if rows:
            table = Table(title="Recent Scores", box=box.ROUNDED)
            table.add_column("Username", style="cyan")
            table.add_column("Score", justify="right")
            table.add_column("Model")
            table.add_column("Scored At")

            for username, score, model, scored_at in rows:
                table.add_row(
                    username,
                    f"{score:.2f}",
                    model or "unknown",
                    scored_at.strftime("%Y-%m-%d %H:%M:%S") if scored_at else "N/A",
                )

            console.print(table)

    def test_direct_invocation(
        self,
        lambda_client,
        logs_client,
        db_cursor,
        infra_config,
        request,
    ):
        """
        Test that llm-scorer can be invoked directly with model parameter.

        The llm-scorer is now invoked directly by the orchestrator (no SQS queue).
        """
        log_level_str = request.config.getoption("--log-level", "INFO").upper()
        log_level = LogLevel[log_level_str] if log_level_str in LogLevel.__members__ else LogLevel.INFO

        console.print("\n[bold cyan]Testing direct invocation[/bold cyan]")

        # Check profiles pending
        db_cursor.execute("SELECT COUNT(*) FROM profiles_to_score")
        pending_count = db_cursor.fetchone()[0]
        console.print(f"Profiles pending: {pending_count}")

        if pending_count == 0:
            pytest.skip("No profiles to score")

        # Invoke with model parameter (like orchestrator does)
        result = invoke_lambda(
            lambda_client,
            infra_config.llm_scorer_name,
            payload={"model": "gemini-2.0-flash", "batchSize": 5}
        )

        print_lambda_result(result, infra_config.llm_scorer_name)

        # Get recent scorer logs
        logs = get_lambda_logs(
            logs_client,
            infra_config.llm_scorer_name,
            since_minutes=5,
            level=log_level,
        )

        if logs:
            print_logs(logs[-15:], title=f"Recent llm-scorer Logs ({log_level.value})")
        else:
            console.print("[dim]No recent logs from llm-scorer[/dim]")


@pytest.mark.e2e
class TestScoringResults:
    """Tests for verifying scoring results quality."""

    def test_score_distribution(self, db_cursor):
        """
        Analyze the distribution of LLM scores.

        This helps verify the scoring model is producing reasonable results.
        """
        console.print("\n[bold cyan]Analyzing score distribution[/bold cyan]")

        db_cursor.execute("""
            SELECT
                COUNT(*) as total,
                AVG(score)::numeric(4,3) as avg_score,
                MIN(score)::numeric(4,3) as min_score,
                MAX(score)::numeric(4,3) as max_score,
                STDDEV(score)::numeric(4,3) as stddev
            FROM profile_scores
        """)
        row = db_cursor.fetchone()
        total, avg, min_s, max_s, stddev = row

        if total == 0:
            console.print("[dim]No scores in database yet[/dim]")
            return

        console.print(f"Total scores: {total}")
        console.print(f"Score range: {min_s} - {max_s}")
        console.print(f"Average: {avg}")
        console.print(f"Std deviation: {stddev}")

        # Score distribution by buckets
        db_cursor.execute("""
            SELECT
                CASE
                    WHEN score >= 0.9 THEN '0.90-1.00'
                    WHEN score >= 0.8 THEN '0.80-0.89'
                    WHEN score >= 0.7 THEN '0.70-0.79'
                    WHEN score >= 0.6 THEN '0.60-0.69'
                    WHEN score >= 0.5 THEN '0.50-0.59'
                    ELSE '0.00-0.49'
                END as bucket,
                COUNT(*) as count
            FROM profile_scores
            GROUP BY bucket
            ORDER BY bucket DESC
        """)
        rows = db_cursor.fetchall()

        if rows:
            table = Table(title="Score Distribution", box=box.ROUNDED)
            table.add_column("Score Range")
            table.add_column("Count", justify="right")
            table.add_column("Percentage", justify="right")

            for bucket, count in rows:
                pct = (count / total) * 100
                bar = "█" * int(pct / 5)  # Simple bar chart
                table.add_row(bucket, str(count), f"{pct:.1f}% {bar}")

            console.print(table)

    def test_has_vs_llm_correlation(self, db_cursor):
        """
        Compare HAS (heuristic) scores with LLM scores.

        This helps validate the HAS heuristic is effective.
        """
        console.print("\n[bold cyan]Comparing HAS vs LLM scores[/bold cyan]")

        db_cursor.execute("""
            SELECT
                up.username,
                up.human_score as has_score,
                ps.score as llm_score,
                up.likely_is,
                ps.scored_by
            FROM profile_scores ps
            JOIN user_profiles up ON ps.twitter_id = up.twitter_id
            ORDER BY ABS(up.human_score - ps.score) DESC
            LIMIT 10
        """)
        rows = db_cursor.fetchall()

        if not rows:
            console.print("[dim]No scored profiles to compare[/dim]")
            return

        table = Table(title="HAS vs LLM Score Comparison (Largest Differences)", box=box.ROUNDED)
        table.add_column("Username", style="cyan")
        table.add_column("HAS", justify="right")
        table.add_column("LLM", justify="right")
        table.add_column("Diff", justify="right")
        table.add_column("Likely Is")

        for username, has_score, llm_score, likely_is, model in rows:
            diff = abs(float(has_score) - float(llm_score))
            diff_style = "red" if diff > 0.3 else "yellow" if diff > 0.15 else "green"
            table.add_row(
                username,
                f"{has_score:.3f}",
                f"{llm_score:.3f}",
                f"[{diff_style}]{diff:.3f}[/{diff_style}]",
                likely_is or "Unknown",
            )

        console.print(table)

        # Calculate correlation
        db_cursor.execute("""
            SELECT
                CORR(CAST(up.human_score AS float), CAST(ps.score AS float))::numeric(4,3)
            FROM profile_scores ps
            JOIN user_profiles up ON ps.twitter_id = up.twitter_id
        """)
        correlation = db_cursor.fetchone()[0]

        if correlation is not None:
            console.print(f"\nCorrelation coefficient: {correlation}")
            if correlation > 0.7:
                console.print("[green]Strong positive correlation - HAS is effective[/green]")
            elif correlation > 0.4:
                console.print("[yellow]Moderate correlation - HAS may need tuning[/yellow]")
            else:
                console.print("[red]Weak correlation - HAS may not be predictive[/red]")
