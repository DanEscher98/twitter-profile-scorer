#!/usr/bin/env python3
"""
Analyze profile_scores by model - generates insights and visualization.
Usage: cd scripts && uv run py_src/analyze_profile_scores.py
Output: scripts/output/<timestamp>-statsprofilescores.png
"""

import os
import sys
import time
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

    # Fetch profile scores with HAS for comparison
    df = pd.read_sql(
        """
        SELECT
            ps.scored_by as model,
            ps.score as llm_score,
            ps.scored_at,
            p.human_score as has_score,
            p.username,
            p.likely_is
        FROM profile_scores ps
        JOIN user_profiles p ON ps.twitter_id = p.twitter_id
        ORDER BY ps.scored_at
        """,
        conn,
    )
    conn.close()

    if df.empty:
        print("No profile scores found in database")
        return

    # Convert scores to float
    df["llm_score"] = df["llm_score"].astype(float)
    df["has_score"] = df["has_score"].astype(float)

    print(f"Loaded {len(df)} profile scores")
    print(f"\nModels found: {df['model'].unique().tolist()}")

    # Print statistics by model
    print("\n" + "=" * 60)
    print("STATISTICS BY MODEL")
    print("=" * 60)

    for model in df["model"].unique():
        model_df = df[df["model"] == model]
        print(f"\n{model}:")
        print(f"  Count: {len(model_df)}")
        print(f"  LLM Score - Mean: {model_df['llm_score'].mean():.3f}, Std: {model_df['llm_score'].std():.3f}")
        print(f"  LLM Score - Min: {model_df['llm_score'].min():.2f}, Max: {model_df['llm_score'].max():.2f}")
        print(f"  HAS Score - Mean: {model_df['has_score'].mean():.3f}")

        # Score distribution buckets
        high = (model_df["llm_score"] >= 0.7).sum()
        medium = ((model_df["llm_score"] >= 0.4) & (model_df["llm_score"] < 0.7)).sum()
        low = (model_df["llm_score"] < 0.4).sum()
        print(f"  Distribution: High(>=0.7): {high}, Medium(0.4-0.7): {medium}, Low(<0.4): {low}")

        # Correlation with HAS
        if len(model_df) > 1:
            corr = model_df["llm_score"].corr(model_df["has_score"])
            print(f"  Correlation with HAS: {corr:.3f}")

    # Agreement analysis between models
    print("\n" + "=" * 60)
    print("MODEL AGREEMENT ANALYSIS")
    print("=" * 60)

    # Pivot to get scores by username and model
    pivot_df = df.pivot_table(index="username", columns="model", values="llm_score", aggfunc="first")

    if len(pivot_df.columns) >= 2:
        models = list(pivot_df.columns)
        for i, m1 in enumerate(models):
            for m2 in models[i + 1 :]:
                overlap = pivot_df[[m1, m2]].dropna()
                if len(overlap) > 0:
                    corr = overlap[m1].corr(overlap[m2])
                    diff = (overlap[m1] - overlap[m2]).abs().mean()
                    print(f"\n{m1} vs {m2}:")
                    print(f"  Overlap: {len(overlap)} profiles")
                    print(f"  Correlation: {corr:.3f}")
                    print(f"  Mean Absolute Difference: {diff:.3f}")

    # Set up the figure
    n_models = len(df["model"].unique())
    fig = plt.figure(figsize=(16, 12))

    # Create grid layout
    gs = fig.add_gridspec(3, 3, hspace=0.3, wspace=0.3)

    # 1. Score distribution by model (violin plot)
    ax1 = fig.add_subplot(gs[0, :2])
    sns.violinplot(data=df, x="model", y="llm_score", ax=ax1, inner="box", palette="Set2")
    ax1.set_xlabel("Model")
    ax1.set_ylabel("LLM Score")
    ax1.set_title("Score Distribution by Model")
    ax1.axhline(y=0.7, color="green", linestyle="--", alpha=0.5, label="High (0.7)")
    ax1.axhline(y=0.4, color="orange", linestyle="--", alpha=0.5, label="Low (0.4)")
    ax1.tick_params(axis="x", rotation=15)
    ax1.legend(loc="lower right")

    # 2. Count by model (bar chart)
    ax2 = fig.add_subplot(gs[0, 2])
    model_counts = df["model"].value_counts()
    bars = ax2.bar(range(len(model_counts)), model_counts.values, color=sns.color_palette("Set2", len(model_counts)))
    ax2.set_xticks(range(len(model_counts)))
    ax2.set_xticklabels([m.split("-")[0][:10] for m in model_counts.index], rotation=45, ha="right")
    ax2.set_ylabel("Count")
    ax2.set_title("Profiles Scored by Model")
    for i, v in enumerate(model_counts.values):
        ax2.text(i, v + 0.5, str(v), ha="center", fontsize=9)

    # 3. LLM Score vs HAS Score scatter (per model)
    ax3 = fig.add_subplot(gs[1, :2])
    for model in df["model"].unique():
        model_df = df[df["model"] == model]
        ax3.scatter(
            model_df["has_score"],
            model_df["llm_score"],
            alpha=0.5,
            label=model.split("-")[0][:15],
            s=20,
        )
    ax3.plot([0, 1], [0, 1], "k--", alpha=0.3, label="Perfect agreement")
    ax3.set_xlabel("HAS Score")
    ax3.set_ylabel("LLM Score")
    ax3.set_title("LLM Score vs HAS Score by Model")
    ax3.legend(loc="lower right", fontsize=8)
    ax3.set_xlim(0, 1)
    ax3.set_ylim(0, 1)

    # 4. Score distribution histogram overlay
    ax4 = fig.add_subplot(gs[1, 2])
    for model in df["model"].unique():
        model_df = df[df["model"] == model]
        ax4.hist(model_df["llm_score"], bins=20, alpha=0.5, label=model.split("-")[0][:10])
    ax4.set_xlabel("LLM Score")
    ax4.set_ylabel("Count")
    ax4.set_title("Score Distribution Overlay")
    ax4.legend(fontsize=8)

    # 5. Boxplot of score difference (LLM - HAS) by model
    ax5 = fig.add_subplot(gs[2, 0])
    df["score_diff"] = df["llm_score"] - df["has_score"]
    sns.boxplot(data=df, x="model", y="score_diff", ax=ax5, palette="Set2")
    ax5.axhline(y=0, color="red", linestyle="--", alpha=0.5)
    ax5.set_xlabel("Model")
    ax5.set_ylabel("LLM Score - HAS Score")
    ax5.set_title("Score Difference (LLM - HAS)")
    ax5.tick_params(axis="x", rotation=45)

    # 6. Heatmap of model correlations (if multiple models)
    ax6 = fig.add_subplot(gs[2, 1])
    if len(pivot_df.columns) >= 2:
        corr_matrix = pivot_df.corr()
        sns.heatmap(
            corr_matrix,
            annot=True,
            cmap="RdYlGn",
            vmin=-1,
            vmax=1,
            ax=ax6,
            fmt=".2f",
            xticklabels=[m.split("-")[0][:8] for m in corr_matrix.columns],
            yticklabels=[m.split("-")[0][:8] for m in corr_matrix.index],
        )
        ax6.set_title("Model Score Correlation")
    else:
        ax6.text(0.5, 0.5, "Need 2+ models\nfor correlation", ha="center", va="center", fontsize=12)
        ax6.set_xlim(0, 1)
        ax6.set_ylim(0, 1)
        ax6.set_title("Model Score Correlation")

    # 7. Summary statistics table
    ax7 = fig.add_subplot(gs[2, 2])
    ax7.axis("off")

    summary_data = []
    for model in df["model"].unique():
        model_df = df[df["model"] == model]
        summary_data.append(
            [
                model.split("-")[0][:12],
                len(model_df),
                f"{model_df['llm_score'].mean():.2f}",
                f"{model_df['llm_score'].std():.2f}",
                f"{model_df['llm_score'].corr(model_df['has_score']):.2f}",
            ]
        )

    table = ax7.table(
        cellText=summary_data,
        colLabels=["Model", "Count", "Mean", "Std", "HAS Corr"],
        loc="center",
        cellLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.2, 1.5)
    ax7.set_title("Summary Statistics", y=0.9)

    # Main title
    fig.suptitle(
        f"Profile Scores Analysis by Model\n(Total: {len(df)} scores, {df['username'].nunique()} unique profiles)",
        fontsize=14,
        fontweight="bold",
    )

    # Save plot with unix timestamp
    timestamp = int(time.time())
    output_path = Path(__file__).parent.parent / "output" / f"{timestamp}-statsprofilescores.png"
    output_path.parent.mkdir(exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"\nPlot saved to: {output_path}")

    plt.show()


if __name__ == "__main__":
    main()
