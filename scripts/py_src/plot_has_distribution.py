#!/usr/bin/env python3
"""
Plot Human Score distribution from the database.
Usage: cd scripts && uv run py_src/plot_has_distribution.py
"""

import os
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import psycopg2
import seaborn as sns
from dotenv import load_dotenv

# Load environment from root
load_dotenv(Path(__file__).parent.parent.parent / ".env")


def get_connection():
    """Get database connection from DATABASE_URL."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("Error: DATABASE_URL not set")
        print("Run: export DATABASE_URL=$(cd infra && uv run pulumi stack output db_connection_string --show-secrets)")
        sys.exit(1)
    return psycopg2.connect(database_url)


def main():
    conn = get_connection()

    # Fetch human scores from user_profiles with stats from user_stats
    df = pd.read_sql(
        """
        SELECT
            p.username,
            p.human_score,
            s.followers,
            s.following,
            s.statuses
        FROM user_profiles p
        LEFT JOIN user_stats s ON p.twitter_id = s.twitter_id
        WHERE p.human_score IS NOT NULL
        """,
        conn,
    )
    conn.close()

    if df.empty:
        print("No profiles found in database")
        return

    # Convert human_score to float
    df["human_score"] = df["human_score"].astype(float)

    print(f"Loaded {len(df)} profiles")
    print(f"\nHuman Score Statistics:")
    print(df["human_score"].describe())

    # Set up the figure
    fig, axes = plt.subplots(2, 2, figsize=(12, 10))
    fig.suptitle("Profile Scorer - Human Score Distribution Analysis", fontsize=14)

    # 1. Human score histogram
    ax1 = axes[0, 0]
    sns.histplot(df["human_score"], bins=50, kde=True, ax=ax1)
    ax1.axvline(x=0.55, color="orange", linestyle="--", label="Threshold (0.55)")
    ax1.axvline(x=0.65, color="red", linestyle="--", label="High Score (0.65)")
    ax1.set_xlabel("Human Score")
    ax1.set_ylabel("Count")
    ax1.set_title("Human Score Distribution")
    ax1.legend()

    # 2. Score vs Followers (log scale)
    ax2 = axes[0, 1]
    df_with_followers = df[df["followers"].notna() & (df["followers"] > 0)]
    if not df_with_followers.empty:
        scatter = ax2.scatter(
            df_with_followers["followers"],
            df_with_followers["human_score"],
            alpha=0.5,
            c=df_with_followers["human_score"],
            cmap="RdYlGn",
            s=10,
        )
        ax2.set_xscale("log")
        ax2.set_xlabel("Followers (log scale)")
        ax2.set_ylabel("Human Score")
        ax2.set_title("Score vs Followers")
        plt.colorbar(scatter, ax=ax2, label="Score")

    # 3. Score vs Status count
    ax3 = axes[1, 0]
    df_with_statuses = df[df["statuses"].notna() & (df["statuses"] > 0)]
    if not df_with_statuses.empty:
        scatter2 = ax3.scatter(
            df_with_statuses["statuses"],
            df_with_statuses["human_score"],
            alpha=0.5,
            c=df_with_statuses["human_score"],
            cmap="RdYlGn",
            s=10,
        )
        ax3.set_xscale("log")
        ax3.set_xlabel("Status Count (log scale)")
        ax3.set_ylabel("Human Score")
        ax3.set_title("Score vs Status Count")

    # 4. Following/Followers ratio vs Score
    ax4 = axes[1, 1]
    df_with_both = df[(df["followers"].notna()) & (df["following"].notna()) & (df["followers"] > 0)]
    if not df_with_both.empty:
        df_with_both = df_with_both.copy()
        df_with_both["ff_ratio"] = df_with_both["following"] / (df_with_both["followers"] + 1)
        df_filtered = df_with_both[df_with_both["ff_ratio"] < 10]  # Filter extreme ratios
        if not df_filtered.empty:
            scatter3 = ax4.scatter(
                df_filtered["ff_ratio"],
                df_filtered["human_score"],
                alpha=0.5,
                c=df_filtered["human_score"],
                cmap="RdYlGn",
                s=10,
            )
            ax4.set_xlabel("Following/Followers Ratio")
            ax4.set_ylabel("Human Score")
            ax4.set_title("Score vs Following/Followers Ratio")

    plt.tight_layout()

    # Save plot
    output_path = Path(__file__).parent.parent / "output" / "human_score_distribution.png"
    output_path.parent.mkdir(exist_ok=True)
    plt.savefig(output_path, dpi=150)
    print(f"\nPlot saved to: {output_path}")

    plt.show()


if __name__ == "__main__":
    main()
