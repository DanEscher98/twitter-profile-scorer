"""Rename xapi_usage_search to api_search_usage.

Revision ID: 003_rename_xapi_table
Revises: 002_add_platform_column
Create Date: 2025-12-01

BREAKING CHANGE: Requires coordinated TypeScript code update.
Deploy sequence: 1) Update TS code, 2) Run this migration, 3) Deploy TS
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "003_rename_xapi_table"
down_revision: str | None = "002_add_platform_column"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Rename table and add platform column."""
    conn = op.get_bind()

    # Pre-migration validation
    result = conn.execute(sa.text("SELECT COUNT(*) FROM xapi_usage_search"))
    pre_count = result.scalar()
    print(f"Pre-migration: {pre_count} search records")

    # Rename table
    op.rename_table("xapi_usage_search", "api_search_usage")

    # Rename unique constraint/index (PostgreSQL renames indexes with table)
    op.execute("ALTER INDEX IF EXISTS uq_xapi_usage_search RENAME TO uq_api_search_usage")

    # Add platform column
    op.add_column(
        "api_search_usage",
        sa.Column("platform", sa.VARCHAR(20), nullable=False, server_default="twitter"),
    )

    # Create platform index
    op.create_index("idx_api_search_usage_platform", "api_search_usage", ["platform"])

    # Post-migration validation
    result = conn.execute(sa.text("SELECT COUNT(*) FROM api_search_usage"))
    post_count = result.scalar()

    if pre_count != post_count:
        raise RuntimeError(f"Data loss detected! Pre: {pre_count}, Post: {post_count}")

    # Verify FK still works (user_keywords.search_id)
    result = conn.execute(
        sa.text("""
            SELECT COUNT(*) FROM user_keywords uk
            LEFT JOIN api_search_usage asu ON uk.search_id = asu.id
            WHERE uk.search_id IS NOT NULL AND asu.id IS NULL
        """)
    )
    orphaned = result.scalar()

    if orphaned and orphaned > 0:
        raise RuntimeError(f"FK integrity broken! {orphaned} orphaned user_keywords records")

    print(f"Post-migration: {post_count} search records, FK integrity verified")


def downgrade() -> None:
    """Revert to xapi_usage_search."""
    conn = op.get_bind()

    # Check for non-twitter platforms before downgrade
    result = conn.execute(
        sa.text("SELECT COUNT(*) FROM api_search_usage WHERE platform != 'twitter'")
    )
    non_twitter = result.scalar()

    if non_twitter and non_twitter > 0:
        raise RuntimeError(
            f"Cannot downgrade: {non_twitter} non-Twitter search records exist."
        )

    op.drop_index("idx_api_search_usage_platform")
    op.drop_column("api_search_usage", "platform")
    op.execute("ALTER INDEX IF EXISTS uq_api_search_usage RENAME TO uq_xapi_usage_search")
    op.rename_table("api_search_usage", "xapi_usage_search")
