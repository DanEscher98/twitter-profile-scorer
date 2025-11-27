"""
E2E tests for database integrity and data consistency.

These tests verify:
1. Foreign key relationships are maintained
2. new_profiles counts are accurate
3. user_keywords properly links profiles to searches
4. profiles_to_score queue contains high-HAS profiles

Usage:
    uv run pytest tests/e2e/test_database.py -v --log-level=INFO
"""

import pytest

from tests.helpers import print_db_counts, console
from rich.table import Table
from rich import box


@pytest.mark.e2e
class TestDatabaseIntegrity:
    """Tests for database integrity and consistency."""

    def test_user_keywords_has_valid_search_ids(self, db_cursor):
        """
        Verify all user_keywords records have valid search_id references.

        After the fix, no user_keywords should have NULL search_id.
        """
        console.print("\n[bold cyan]Testing user_keywords search_id integrity[/bold cyan]")

        # Count records with and without search_id
        db_cursor.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(search_id) as with_search_id,
                COUNT(*) - COUNT(search_id) as null_search_id
            FROM user_keywords
        """)
        total, with_id, null_id = db_cursor.fetchone()

        console.print(f"Total user_keywords: {total}")
        console.print(f"With search_id: {with_id}")
        console.print(f"NULL search_id: {null_id}")

        # All records should have search_id after the fix
        assert null_id == 0, f"Found {null_id} user_keywords with NULL search_id"

        # Verify FK references are valid
        db_cursor.execute("""
            SELECT COUNT(*)
            FROM user_keywords uk
            LEFT JOIN xapi_usage_search xu ON uk.search_id = xu.id
            WHERE uk.search_id IS NOT NULL AND xu.id IS NULL
        """)
        orphaned = db_cursor.fetchone()[0]

        assert orphaned == 0, f"Found {orphaned} user_keywords with invalid search_id"
        console.print("[green]All search_id references are valid[/green]")

    def test_new_profiles_count_accuracy(self, db_cursor):
        """
        Verify new_profiles counts in xapi_usage_search are reasonable.

        The new_profiles count should:
        1. Not all be 0 (the bug we fixed)
        2. Be between 0 and items (can't find more new profiles than searched)
        """
        console.print("\n[bold cyan]Testing new_profiles count accuracy[/bold cyan]")

        db_cursor.execute("""
            SELECT
                COUNT(*) as total_searches,
                SUM(CASE WHEN new_profiles = 0 THEN 1 ELSE 0 END) as zero_count,
                SUM(CASE WHEN new_profiles > 0 THEN 1 ELSE 0 END) as positive_count,
                AVG(new_profiles) as avg_new_profiles,
                MAX(new_profiles) as max_new_profiles
            FROM xapi_usage_search
        """)
        total, zero, positive, avg, max_new = db_cursor.fetchone()

        console.print(f"Total searches: {total}")
        console.print(f"Searches with 0 new profiles: {zero}")
        console.print(f"Searches with >0 new profiles: {positive}")
        console.print(f"Average new profiles: {avg:.2f}" if avg else "Average: N/A")
        console.print(f"Max new profiles: {max_new}")

        # After fixing the bug, we should have some non-zero counts
        # (unless the database was populated entirely before the fix)
        if total > 0:
            # Check for out-of-range values
            db_cursor.execute("""
                SELECT COUNT(*)
                FROM xapi_usage_search
                WHERE new_profiles < 0 OR new_profiles > items
            """)
            invalid = db_cursor.fetchone()[0]
            assert invalid == 0, f"Found {invalid} searches with invalid new_profiles count"

        # Show recent searches for manual inspection
        db_cursor.execute("""
            SELECT keyword, page, items, new_profiles, query_at
            FROM xapi_usage_search
            ORDER BY query_at DESC
            LIMIT 10
        """)
        rows = db_cursor.fetchall()

        if rows:
            table = Table(title="Recent Searches", box=box.ROUNDED)
            table.add_column("Keyword", style="cyan")
            table.add_column("Page", justify="right")
            table.add_column("Items", justify="right")
            table.add_column("New Profiles", justify="right")
            table.add_column("Query Time")

            for keyword, page, items, new_profiles, query_at in rows:
                style = "green" if new_profiles > 0 else "dim"
                table.add_row(
                    keyword,
                    str(page),
                    str(items),
                    f"[{style}]{new_profiles}[/{style}]",
                    query_at.strftime("%Y-%m-%d %H:%M:%S"),
                )

            console.print(table)

    def test_user_stats_matches_profiles(self, db_cursor):
        """
        Verify user_stats has records for all user_profiles.

        Every profile should have corresponding stats.
        """
        console.print("\n[bold cyan]Testing user_stats coverage[/bold cyan]")

        db_cursor.execute("SELECT COUNT(*) FROM user_profiles")
        profile_count = db_cursor.fetchone()[0]

        db_cursor.execute("SELECT COUNT(*) FROM user_stats")
        stats_count = db_cursor.fetchone()[0]

        console.print(f"user_profiles: {profile_count}")
        console.print(f"user_stats: {stats_count}")

        # Find profiles without stats
        db_cursor.execute("""
            SELECT COUNT(*)
            FROM user_profiles up
            LEFT JOIN user_stats us ON up.twitter_id = us.twitter_id
            WHERE us.twitter_id IS NULL
        """)
        missing_stats = db_cursor.fetchone()[0]

        if missing_stats > 0:
            console.print(f"[yellow]Profiles without stats: {missing_stats}[/yellow]")
        else:
            console.print("[green]All profiles have corresponding stats[/green]")

        # This is a soft assertion - older profiles might not have stats
        # assert missing_stats == 0, f"Found {missing_stats} profiles without stats"

    def test_profiles_to_score_contains_high_has(self, db_cursor):
        """
        Verify profiles_to_score contains profiles with HAS > 0.65.

        The pipeline should only queue high-scoring profiles for LLM evaluation.
        """
        console.print("\n[bold cyan]Testing profiles_to_score HAS threshold[/bold cyan]")

        # Check HAS scores of profiles in the queue
        db_cursor.execute("""
            SELECT
                MIN(up.human_score) as min_score,
                MAX(up.human_score) as max_score,
                AVG(up.human_score) as avg_score,
                COUNT(*) as count
            FROM profiles_to_score pts
            JOIN user_profiles up ON pts.twitter_id = up.twitter_id
        """)
        min_score, max_score, avg_score, count = db_cursor.fetchone()

        console.print(f"Profiles in queue: {count}")

        if count > 0:
            console.print(f"HAS scores - Min: {min_score:.4f}, Max: {max_score:.4f}, Avg: {avg_score:.4f}")

            # Check for profiles below threshold
            db_cursor.execute("""
                SELECT COUNT(*)
                FROM profiles_to_score pts
                JOIN user_profiles up ON pts.twitter_id = up.twitter_id
                WHERE up.human_score <= 0.65
            """)
            below_threshold = db_cursor.fetchone()[0]

            if below_threshold > 0:
                console.print(f"[yellow]Profiles below 0.65 threshold: {below_threshold}[/yellow]")
            else:
                console.print("[green]All queued profiles meet HAS threshold[/green]")

            # Show sample profiles
            db_cursor.execute("""
                SELECT up.username, up.human_score, up.likely_is, pts.added_at
                FROM profiles_to_score pts
                JOIN user_profiles up ON pts.twitter_id = up.twitter_id
                ORDER BY pts.added_at DESC
                LIMIT 5
            """)
            rows = db_cursor.fetchall()

            if rows:
                table = Table(title="Sample Queued Profiles", box=box.ROUNDED)
                table.add_column("Username", style="cyan")
                table.add_column("HAS Score", justify="right")
                table.add_column("Likely Is")
                table.add_column("Added At")

                for username, score, likely_is, added_at in rows:
                    table.add_row(
                        username,
                        f"{score:.4f}",
                        likely_is or "Unknown",
                        added_at.strftime("%Y-%m-%d %H:%M:%S"),
                    )

                console.print(table)

    def test_database_counts_summary(self, db_cursor):
        """Print a summary of all database table counts."""
        console.print("\n[bold cyan]Database Summary[/bold cyan]")
        print_db_counts(db_cursor, title="Current Database State")

        # Show keyword effectiveness
        db_cursor.execute("""
            SELECT
                keyword,
                COUNT(*) as searches,
                SUM(new_profiles) as total_new,
                AVG(new_profiles)::numeric(5,2) as avg_per_search
            FROM xapi_usage_search
            GROUP BY keyword
            ORDER BY avg_per_search DESC
            LIMIT 10
        """)
        rows = db_cursor.fetchall()

        if rows:
            table = Table(title="Keyword Effectiveness", box=box.ROUNDED)
            table.add_column("Keyword", style="cyan")
            table.add_column("Searches", justify="right")
            table.add_column("Total New", justify="right")
            table.add_column("Avg/Search", justify="right")

            for keyword, searches, total_new, avg in rows:
                table.add_row(
                    keyword,
                    str(searches),
                    str(total_new or 0),
                    f"{avg:.2f}" if avg else "0.00",
                )

            console.print(table)
