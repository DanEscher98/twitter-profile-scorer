"""Initial baseline - mark existing schema as migrated.

Revision ID: 001_baseline
Revises: None
Create Date: 2025-12-01

This is a baseline migration that records the current schema state
without making any changes. It allows Alembic to track future migrations
while Drizzle ORM still manages the TypeScript codebase.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Verify expected tables exist (no changes made)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    expected_tables = [
        "user_profiles",
        "profile_scores",
        "profiles_to_score",
        "user_keywords",
        "user_stats",
        "xapi_usage_search",
        "keyword_stats",
    ]

    missing = set(expected_tables) - set(existing_tables)
    if missing:
        raise RuntimeError(f"Baseline check failed. Missing tables: {missing}")

    print(f"Baseline verified. Found {len(expected_tables)} expected tables.")


def downgrade() -> None:
    """Cannot downgrade baseline - this would require recreating from scratch."""
    raise RuntimeError("Cannot downgrade past baseline migration")
