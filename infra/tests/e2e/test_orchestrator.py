"""
E2E tests for the orchestrator Lambda function.

The orchestrator is the heartbeat of the pipeline:
1. Gets keywords from keyword-engine
2. Sends keywords to the keywords-queue (triggers query-twitter-api)
3. Checks profiles_to_score count
4. Invokes llm-scorer directly when work exists

Usage:
    uv run pytest tests/e2e/test_orchestrator.py -v --log-level=INFO
"""

import pytest
import time

from tests.helpers import (
    invoke_lambda,
    get_queue_stats,
    get_lambda_logs,
    print_lambda_result,
    print_queue_stats,
    print_logs,
    LogLevel,
    console,
)


@pytest.mark.e2e
class TestOrchestrator:
    """Tests for the orchestrator Lambda."""

    def test_orchestrator_populates_queues(
        self,
        lambda_client,
        sqs_client,
        logs_client,
        infra_config,
        request,
    ):
        """
        Test that the orchestrator populates the keywords queue.

        This test:
        1. Records queue depths before
        2. Invokes the orchestrator
        3. Verifies keywords were queued
        4. Checks scoring jobs were queued (if profiles pending)
        """
        log_level_str = request.config.getoption("--log-level", "INFO").upper()
        log_level = LogLevel[log_level_str] if log_level_str in LogLevel.__members__ else LogLevel.INFO

        console.print("\n[bold cyan]Testing orchestrator Lambda[/bold cyan]")

        # Get queue stats before
        keywords_before = get_queue_stats(sqs_client, infra_config.keywords_queue_url)

        print_queue_stats([keywords_before])
        console.print("[dim]Queue stats before invocation[/dim]")

        # Invoke orchestrator
        result = invoke_lambda(lambda_client, infra_config.orchestrator_name)

        print_lambda_result(result, infra_config.orchestrator_name)

        # Assert Lambda succeeded
        assert result.success, f"Orchestrator failed: {result.function_error}"
        assert result.payload is not None

        # Check response structure
        payload = result.payload
        assert "keywordsQueued" in payload, "Response should contain keywordsQueued"
        assert "errors" in payload, "Response should contain errors array"

        keywords_queued = payload["keywordsQueued"]
        scoring_invocations = payload.get("scoringInvocations", 0)
        errors = payload["errors"]

        console.print(f"[green]Keywords queued: {keywords_queued}[/green]")
        console.print(f"[green]Scoring invocations: {scoring_invocations}[/green]")

        if errors:
            console.print(f"[yellow]Errors: {errors}[/yellow]")

        # Wait a moment for queues to update
        time.sleep(2)

        # Get queue stats after
        keywords_after = get_queue_stats(sqs_client, infra_config.keywords_queue_url)

        print_queue_stats([keywords_after])
        console.print("[dim]Queue stats after invocation[/dim]")

        # Verify keywords were queued (account for concurrent consumption)
        # The queue might have fewer messages if Lambdas started consuming immediately
        assert keywords_queued > 0, "Orchestrator should queue at least one keyword"

        # Get logs
        time.sleep(2)
        logs = get_lambda_logs(
            logs_client,
            infra_config.orchestrator_name,
            since_minutes=2,
            level=log_level,
        )
        print_logs(logs, title=f"orchestrator Logs ({log_level.value})")

    def test_keyword_engine_returns_keywords(
        self,
        lambda_client,
        logs_client,
        infra_config,
        request,
    ):
        """
        Test that keyword-engine returns keywords and stats.

        The keyword-engine returns:
        - keywords: list of keywords to search
        - stats: keyword yield statistics from xapi_usage_search
        """
        log_level_str = request.config.getoption("--log-level", "INFO").upper()
        log_level = LogLevel[log_level_str] if log_level_str in LogLevel.__members__ else LogLevel.INFO

        console.print("\n[bold cyan]Testing keyword-engine Lambda[/bold cyan]")

        # Invoke keyword-engine
        result = invoke_lambda(lambda_client, infra_config.keyword_engine_name)

        print_lambda_result(result, infra_config.keyword_engine_name)

        # Assert Lambda succeeded
        assert result.success, f"keyword-engine failed: {result.function_error}"
        assert result.payload is not None

        # Check response structure
        payload = result.payload
        assert "keywords" in payload, "Response should contain 'keywords'"
        keywords = payload["keywords"]
        assert isinstance(keywords, list), "keywords should be a list"
        assert len(keywords) > 0, "Should return at least one keyword"
        assert all(isinstance(k, str) for k in keywords), "All keywords should be strings"

        console.print(f"[green]Keywords returned: {keywords}[/green]")

        # Check stats if present
        if "stats" in payload:
            stats = payload["stats"]
            console.print(f"[cyan]Stats: {stats.get('totalSearches', 0)} total searches[/cyan]")

        # Get logs
        time.sleep(2)
        logs = get_lambda_logs(
            logs_client,
            infra_config.keyword_engine_name,
            since_minutes=2,
            level=log_level,
        )
        print_logs(logs, title=f"keyword-engine Logs ({log_level.value})")


@pytest.mark.e2e
@pytest.mark.slow
class TestOrchestratorIntegration:
    """Integration tests that verify the full orchestrator → queue → Lambda flow."""

    def test_full_pipeline_flow(
        self,
        lambda_client,
        sqs_client,
        logs_client,
        db_cursor,
        infra_config,
        request,
    ):
        """
        Test the full pipeline: orchestrator → queue → query-twitter-api.

        This test:
        1. Invokes the orchestrator
        2. Waits for keywords to be processed
        3. Verifies database was populated
        """
        log_level_str = request.config.getoption("--log-level", "INFO").upper()
        log_level = LogLevel[log_level_str] if log_level_str in LogLevel.__members__ else LogLevel.INFO

        console.print("\n[bold cyan]Testing full pipeline flow[/bold cyan]")

        # Record database state before
        db_cursor.execute("SELECT COUNT(*) FROM user_profiles")
        profiles_before = db_cursor.fetchone()[0]

        db_cursor.execute("SELECT COUNT(*) FROM xapi_usage_search")
        searches_before = db_cursor.fetchone()[0]

        console.print(f"[dim]Before: profiles={profiles_before}, searches={searches_before}[/dim]")

        # Invoke orchestrator
        result = invoke_lambda(lambda_client, infra_config.orchestrator_name)
        assert result.success, f"Orchestrator failed: {result.function_error}"

        keywords_queued = result.payload.get("keywordsQueued", 0)
        console.print(f"[cyan]Orchestrator queued {keywords_queued} keywords[/cyan]")

        if keywords_queued == 0:
            pytest.skip("No keywords were queued (may be at API limit)")

        # Wait for the queue to be processed
        # With 3 concurrent Lambdas and 5 keywords, should be quick
        console.print("[dim]Waiting for keywords to be processed...[/dim]")

        max_wait = 120  # 2 minutes max
        poll_interval = 10

        for _ in range(max_wait // poll_interval):
            time.sleep(poll_interval)

            stats = get_queue_stats(sqs_client, infra_config.keywords_queue_url)
            console.print(f"[dim]Queue: {stats.messages_available} available, {stats.messages_in_flight} in flight[/dim]")

            if stats.total == 0:
                console.print("[green]Queue is empty - all keywords processed[/green]")
                break
        else:
            console.print("[yellow]Timeout waiting for queue - some messages may still be processing[/yellow]")

        # Give a moment for final DB writes
        time.sleep(5)

        # Check database state after
        db_cursor.execute("SELECT COUNT(*) FROM user_profiles")
        profiles_after = db_cursor.fetchone()[0]

        db_cursor.execute("SELECT COUNT(*) FROM xapi_usage_search")
        searches_after = db_cursor.fetchone()[0]

        profiles_added = profiles_after - profiles_before
        searches_added = searches_after - searches_before

        console.print(f"[green]After: profiles={profiles_after} (+{profiles_added}), searches={searches_after} (+{searches_added})[/green]")

        # Verify progress was made
        assert searches_added > 0, "No new searches recorded"

        # Get logs from query-twitter-api
        logs = get_lambda_logs(
            logs_client,
            infra_config.query_twitter_name,
            since_minutes=5,
            level=log_level,
        )
        print_logs(logs[-20:], title=f"query-twitter-api Logs (last 20, {log_level.value})")

        # Check for any errors in the logs
        error_logs = [l for l in logs if l["level"] == "error"]
        if error_logs:
            console.print(f"[yellow]Found {len(error_logs)} error logs[/yellow]")
            print_logs(error_logs, title="Error Logs")
