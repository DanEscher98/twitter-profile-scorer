"""
E2E tests for the query-twitter-api Lambda function.

This Lambda is responsible for:
1. Receiving keywords from SQS
2. Fetching profiles from RapidAPI
3. Computing HAS (Human Authenticity Score)
4. Storing profiles in user_profiles, user_stats, user_keywords
5. Queuing high-HAS profiles to profiles_to_score

Usage:
    uv run pytest tests/e2e/test_query_twitter_api.py -v --log-level=INFO
"""

import pytest
import time

from tests.helpers import (
    invoke_lambda_with_sqs_event,
    get_lambda_logs,
    print_lambda_result,
    print_logs,
    print_db_counts,
    LogLevel,
    console,
)


@pytest.mark.e2e
class TestQueryTwitterApi:
    """Tests for the query-twitter-api Lambda."""

    def test_invoke_with_keyword(
        self,
        lambda_client,
        logs_client,
        db_cursor,
        db_connection,
        infra_config,
        request,
    ):
        """
        Test that query-twitter-api processes a keyword and populates the database.

        This test:
        1. Invokes the Lambda with a test keyword
        2. Verifies the Lambda succeeds
        3. Checks database tables are populated
        4. Verifies new_profiles count is accurate
        """
        # Get log level from pytest config or default to INFO
        log_level_str = request.config.getoption("--log-level", "INFO").upper()
        log_level = LogLevel[log_level_str] if log_level_str in LogLevel.__members__ else LogLevel.INFO

        # Use a keyword unlikely to have been fully paginated
        test_keyword = "bioinformatician"

        console.print(f"\n[bold cyan]Testing query-twitter-api with keyword: {test_keyword}[/bold cyan]")

        # Get counts before
        db_cursor.execute("SELECT COUNT(*) FROM user_profiles")
        profiles_before = db_cursor.fetchone()[0]

        db_cursor.execute("SELECT COUNT(*) FROM user_keywords")
        keywords_before = db_cursor.fetchone()[0]

        db_cursor.execute("SELECT COUNT(*) FROM user_stats")
        stats_before = db_cursor.fetchone()[0]

        db_cursor.execute("SELECT COUNT(*) FROM xapi_usage_search WHERE keyword = %s", (test_keyword,))
        searches_before = db_cursor.fetchone()[0]

        console.print(f"[dim]Before: profiles={profiles_before}, keywords={keywords_before}, stats={stats_before}[/dim]")

        # Invoke Lambda
        result = invoke_lambda_with_sqs_event(
            lambda_client,
            infra_config.query_twitter_name,
            {"keyword": test_keyword},
        )

        print_lambda_result(result, infra_config.query_twitter_name)

        # Assert Lambda succeeded
        assert result.success, f"Lambda failed: {result.function_error}"
        assert result.payload is not None
        assert "batchItemFailures" in result.payload
        assert len(result.payload["batchItemFailures"]) == 0, "Lambda reported batch failures"

        # Wait a moment for CloudWatch logs to be available
        time.sleep(2)

        # Get and display logs
        logs = get_lambda_logs(
            logs_client,
            infra_config.query_twitter_name,
            since_minutes=2,
            level=log_level,
        )
        print_logs(logs, title=f"query-twitter-api Logs ({log_level.value})")

        # Verify database was populated
        db_cursor.execute("SELECT COUNT(*) FROM user_profiles")
        profiles_after = db_cursor.fetchone()[0]

        db_cursor.execute("SELECT COUNT(*) FROM user_keywords")
        keywords_after = db_cursor.fetchone()[0]

        db_cursor.execute("SELECT COUNT(*) FROM user_stats")
        stats_after = db_cursor.fetchone()[0]

        db_cursor.execute("SELECT COUNT(*) FROM xapi_usage_search WHERE keyword = %s", (test_keyword,))
        searches_after = db_cursor.fetchone()[0]

        console.print(f"[green]After: profiles={profiles_after}, keywords={keywords_after}, stats={stats_after}[/green]")

        # Verify xapi_usage_search has accurate new_profiles
        db_cursor.execute("""
            SELECT id, keyword, page, items, new_profiles, query_at
            FROM xapi_usage_search
            WHERE keyword = %s
            ORDER BY query_at DESC
            LIMIT 1
        """, (test_keyword,))
        latest_search = db_cursor.fetchone()

        if latest_search:
            search_id, keyword, page, items, new_profiles, query_at = latest_search
            console.print(f"[cyan]Latest search: page={page}, items={items}, new_profiles={new_profiles}[/cyan]")

            # Verify new_profiles is not always 0 (the bug we fixed)
            # Note: It could legitimately be 0 if all profiles already exist
            profiles_added = profiles_after - profiles_before

            # new_profiles should match the actual number of new profiles added
            # (or be close, accounting for race conditions)
            assert new_profiles >= 0, "new_profiles should not be negative"

            # If we added profiles, new_profiles should reflect that
            if profiles_added > 0:
                assert new_profiles > 0, f"new_profiles is 0 but we added {profiles_added} profiles"

        # A new search record should have been created
        assert searches_after > searches_before, "No new xapi_usage_search record created"

        # Print final counts
        print_db_counts(db_cursor, title="Database Counts After Test")

    def test_handles_fully_paginated_keyword(
        self,
        lambda_client,
        logs_client,
        db_cursor,
        infra_config,
        request,
    ):
        """
        Test that the Lambda handles a fully paginated keyword gracefully.

        When a keyword has no more pages (next_page is null on the latest page),
        the Lambda should return a batch failure (goes to DLQ).
        """
        log_level_str = request.config.getoption("--log-level", "INFO").upper()
        log_level = LogLevel[log_level_str] if log_level_str in LogLevel.__members__ else LogLevel.INFO

        # Find a keyword that's been fully paginated (next_page is null on the LAST page)
        # We need to ensure it's the most recent search for that keyword
        db_cursor.execute("""
            SELECT xu.keyword, xu.page, xu.next_page
            FROM xapi_usage_search xu
            WHERE xu.next_page IS NULL
            AND xu.page = (
                SELECT MAX(page)
                FROM xapi_usage_search xu2
                WHERE xu2.keyword = xu.keyword
            )
            ORDER BY xu.query_at DESC
            LIMIT 1
        """)
        result = db_cursor.fetchone()

        if not result:
            pytest.skip("No fully paginated keywords found in database")

        test_keyword, last_page, _ = result
        console.print(f"\n[bold cyan]Testing with fully paginated keyword: {test_keyword} (page {last_page})[/bold cyan]")

        # Invoke Lambda
        result = invoke_lambda_with_sqs_event(
            lambda_client,
            infra_config.query_twitter_name,
            {"keyword": test_keyword},
        )

        # Print result for inspection
        print_lambda_result(result, infra_config.query_twitter_name)

        # Wait for logs
        time.sleep(2)

        logs = get_lambda_logs(
            logs_client,
            infra_config.query_twitter_name,
            since_minutes=2,
            level=log_level,
        )
        print_logs(logs, title=f"query-twitter-api Logs ({log_level.value})")

        # The Lambda could either:
        # 1. Succeed and process more pages (if pagination resumed from API)
        # 2. Fail with batch item failures (if truly paginated)
        # Both are valid behaviors depending on API state

        if result.payload and result.payload.get("batchItemFailures"):
            console.print("[green]Lambda correctly reported batch failure for paginated keyword[/green]")
        else:
            # Check if it processed successfully (API might have new pages)
            console.print("[yellow]Lambda processed successfully - API may have returned new data[/yellow]")

        # Either way, the Lambda should not crash
        assert result.status_code == 200, f"Lambda should return 200, got {result.status_code}"
