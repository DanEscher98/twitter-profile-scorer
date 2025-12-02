"""Add platform column to user_profiles.

Revision ID: 002_add_platform_column
Revises: 001_baseline
Create Date: 2025-12-01

Adds platform support for multi-platform profile storage (Twitter, BlueSky).
Existing records default to 'twitter' for backward compatibility.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "002_add_platform_column"
down_revision: str | None = "001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add platform column with safety checks."""
    conn = op.get_bind()

    # Pre-migration count
    result = conn.execute(sa.text("SELECT COUNT(*) FROM user_profiles"))
    pre_count = result.scalar()
    print(f"Pre-migration: {pre_count} profiles")

    # Add platform column with default
    op.add_column(
        "user_profiles",
        sa.Column("platform", sa.VARCHAR(20), nullable=False, server_default="twitter"),
    )

    # Create index for platform queries
    op.create_index("idx_user_profiles_platform", "user_profiles", ["platform"])
    op.create_index(
        "idx_user_profiles_platform_handle",
        "user_profiles",
        ["platform", "handle"],
    )

    # Post-migration validation
    result = conn.execute(sa.text("SELECT COUNT(*) FROM user_profiles"))
    post_count = result.scalar()

    result = conn.execute(
        sa.text("SELECT COUNT(*) FROM user_profiles WHERE platform = 'twitter'")
    )
    twitter_count = result.scalar()

    if pre_count != post_count:
        raise RuntimeError(f"Data loss detected! Pre: {pre_count}, Post: {post_count}")

    if twitter_count != post_count:
        raise RuntimeError(
            f"Platform default failed! Twitter: {twitter_count}, Total: {post_count}"
        )

    print(f"Post-migration: {post_count} profiles, all set to 'twitter'")


def downgrade() -> None:
    """Remove platform column."""
    conn = op.get_bind()

    # Check for non-twitter platforms before downgrade
    result = conn.execute(
        sa.text("SELECT COUNT(*) FROM user_profiles WHERE platform != 'twitter'")
    )
    non_twitter = result.scalar()

    if non_twitter and non_twitter > 0:
        raise RuntimeError(
            f"Cannot downgrade: {non_twitter} non-Twitter profiles exist. "
            "Manually migrate or delete these records first."
        )

    op.drop_index("idx_user_profiles_platform_handle")
    op.drop_index("idx_user_profiles_platform")
    op.drop_column("user_profiles", "platform")
