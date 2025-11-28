#!/usr/bin/env python3
"""
Analyze profile scores for a single model - generates insights and word cloud.
Usage: cd scripts && uv run py_src/analyze_model_scores.py <model>
Example: cd scripts && uv run py_src/analyze_model_scores.py claude-haiku-4-5-20251001
Output: scripts/output/<timestamp>-modelscores-<model>.png
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


def get_available_models(conn):
    """Get list of models that have scores."""
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT scored_by FROM profile_scores ORDER BY scored_by")
    models = [row[0] for row in cursor.fetchall()]
    cursor.close()
    return models


def main():
    if len(sys.argv) < 2:
        conn = get_connection()
        models = get_available_models(conn)
        conn.close()
        print("Usage: uv run py_src/analyze_model_scores.py <model>")
        print("\nAvailable models:")
        for m in models:
            print(f"  - {m}")
        sys.exit(1)

    model = sys.argv[1]
    conn = get_connection()

    # Check if model exists
    available_models = get_available_models(conn)
    if model not in available_models:
        print(f"Error: Model '{model}' not found")
        print("\nAvailable models:")
        for m in available_models:
            print(f"  - {m}")
        conn.close()
        sys.exit(1)

    # Fetch profile scores for this model with profile info
    df = pd.read_sql(
        """
        SELECT
            ps.score as llm_score,
            ps.reason,
            ps.scored_at,
            p.twitter_id,
            p.username,
            p.display_name,
            p.bio,
            p.human_score as has_score,
            p.likely_is,
            p.category
        FROM profile_scores ps
        JOIN user_profiles p ON ps.twitter_id = p.twitter_id
        WHERE ps.scored_by = %s
        ORDER BY ps.scored_at
        """,
        conn,
        params=(model,),
    )

    # Fetch keywords for scored profiles
    keywords_df = pd.read_sql(
        """
        SELECT
            uk.twitter_id,
            uk.keyword
        FROM user_keywords uk
        WHERE uk.twitter_id IN (
            SELECT DISTINCT twitter_id
            FROM profile_scores
            WHERE scored_by = %s
        )
        """,
        conn,
        params=(model,),
    )
    conn.close()

    if df.empty:
        print(f"No scores found for model: {model}")
        return

    # Convert scores to float
    df["llm_score"] = df["llm_score"].astype(float)
    df["has_score"] = df["has_score"].astype(float)

    print(f"\n{'=' * 60}")
    print(f"MODEL ANALYSIS: {model}")
    print(f"{'=' * 60}")
    print(f"\nTotal profiles scored: {len(df)}")

    # Score statistics
    print(f"\n--- Score Statistics ---")
    print(f"Mean:   {df['llm_score'].mean():.3f}")
    print(f"Median: {df['llm_score'].median():.3f}")
    print(f"Std:    {df['llm_score'].std():.3f}")
    print(f"Min:    {df['llm_score'].min():.2f}")
    print(f"Max:    {df['llm_score'].max():.2f}")

    # Score distribution buckets
    high = (df["llm_score"] >= 0.7).sum()
    medium = ((df["llm_score"] >= 0.4) & (df["llm_score"] < 0.7)).sum()
    low = (df["llm_score"] < 0.4).sum()
    print(f"\n--- Score Distribution ---")
    print(f"High   (>= 0.7): {high:4d} ({100*high/len(df):.1f}%)")
    print(f"Medium (0.4-0.7): {medium:4d} ({100*medium/len(df):.1f}%)")
    print(f"Low    (< 0.4):  {low:4d} ({100*low/len(df):.1f}%)")

    # Correlation with HAS
    corr = df["llm_score"].corr(df["has_score"])
    print(f"\n--- Correlation with HAS ---")
    print(f"Pearson correlation: {corr:.3f}")

    # Top keywords analysis
    keyword_counts = keywords_df["keyword"].value_counts()
    print(f"\n--- Top 15 Keywords ---")
    for kw, count in keyword_counts.head(15).items():
        print(f"  {kw}: {count}")

    # Keyword score analysis
    keyword_scores = keywords_df.merge(df[["twitter_id", "llm_score"]], on="twitter_id")
    keyword_avg_scores = keyword_scores.groupby("keyword")["llm_score"].agg(["mean", "count"]).reset_index()
    keyword_avg_scores = keyword_avg_scores[keyword_avg_scores["count"] >= 5].sort_values("mean", ascending=False)

    print(f"\n--- Keywords by Average Score (min 5 profiles) ---")
    print("Top 5 (highest scores):")
    for _, row in keyword_avg_scores.head(5).iterrows():
        print(f"  {row['keyword']}: {row['mean']:.2f} (n={int(row['count'])})")
    print("Bottom 5 (lowest scores):")
    for _, row in keyword_avg_scores.tail(5).iterrows():
        print(f"  {row['keyword']}: {row['mean']:.2f} (n={int(row['count'])})")

    # Top and bottom scored profiles
    print(f"\n--- Top 5 Scored Profiles ---")
    for _, row in df.nlargest(5, "llm_score").iterrows():
        print(f"  @{row['username']}: {row['llm_score']:.2f} - {row['bio'][:60] if row['bio'] else 'No bio'}...")

    print(f"\n--- Bottom 5 Scored Profiles ---")
    for _, row in df.nsmallest(5, "llm_score").iterrows():
        print(f"  @{row['username']}: {row['llm_score']:.2f} - {row['bio'][:60] if row['bio'] else 'No bio'}...")

    # Create visualization
    fig = plt.figure(figsize=(16, 14))
    gs = fig.add_gridspec(3, 3, hspace=0.35, wspace=0.3)

    # 1. Score distribution histogram
    ax1 = fig.add_subplot(gs[0, 0])
    sns.histplot(df["llm_score"], bins=30, kde=True, ax=ax1, color="steelblue")
    ax1.axvline(x=0.7, color="green", linestyle="--", alpha=0.7, label="High (0.7)")
    ax1.axvline(x=0.4, color="orange", linestyle="--", alpha=0.7, label="Low (0.4)")
    ax1.axvline(x=df["llm_score"].mean(), color="red", linestyle="-", alpha=0.7, label=f"Mean ({df['llm_score'].mean():.2f})")
    ax1.set_xlabel("LLM Score")
    ax1.set_ylabel("Count")
    ax1.set_title("Score Distribution")
    ax1.legend(fontsize=8)

    # 2. Score vs HAS scatter
    ax2 = fig.add_subplot(gs[0, 1])
    scatter = ax2.scatter(
        df["has_score"],
        df["llm_score"],
        alpha=0.4,
        c=df["llm_score"],
        cmap="RdYlGn",
        s=15,
    )
    ax2.plot([0, 1], [0, 1], "k--", alpha=0.3, label="Perfect agreement")
    ax2.set_xlabel("HAS Score")
    ax2.set_ylabel("LLM Score")
    ax2.set_title(f"LLM vs HAS (corr: {corr:.2f})")
    ax2.set_xlim(0, 1)
    ax2.set_ylim(0, 1)
    plt.colorbar(scatter, ax=ax2, label="LLM Score")

    # 3. Score buckets pie chart
    ax3 = fig.add_subplot(gs[0, 2])
    sizes = [high, medium, low]
    labels = [f"High\n({high})", f"Medium\n({medium})", f"Low\n({low})"]
    colors = ["#2ecc71", "#f39c12", "#e74c3c"]
    ax3.pie(sizes, labels=labels, colors=colors, autopct="%1.1f%%", startangle=90)
    ax3.set_title("Score Distribution")

    # 4. Top keywords bar chart
    ax4 = fig.add_subplot(gs[1, :2])
    top_keywords = keyword_counts.head(20)
    bars = ax4.barh(range(len(top_keywords)), top_keywords.values, color="steelblue")
    ax4.set_yticks(range(len(top_keywords)))
    ax4.set_yticklabels(top_keywords.index, fontsize=9)
    ax4.invert_yaxis()
    ax4.set_xlabel("Profile Count")
    ax4.set_title("Top 20 Keywords (Profiles Found)")
    for i, v in enumerate(top_keywords.values):
        ax4.text(v + 0.5, i, str(v), va="center", fontsize=8)

    # 5. Word cloud of keywords
    ax5 = fig.add_subplot(gs[1, 2])
    try:
        from wordcloud import WordCloud

        # Create word frequency dict
        word_freq = keyword_counts.to_dict()
        wordcloud = WordCloud(
            width=400,
            height=300,
            background_color="white",
            colormap="viridis",
            max_words=50,
        ).generate_from_frequencies(word_freq)
        ax5.imshow(wordcloud, interpolation="bilinear")
        ax5.axis("off")
        ax5.set_title("Keyword Cloud")
    except ImportError:
        # Fallback if wordcloud not installed
        ax5.text(0.5, 0.5, "Install wordcloud:\npip install wordcloud", ha="center", va="center", fontsize=12)
        ax5.set_xlim(0, 1)
        ax5.set_ylim(0, 1)
        ax5.axis("off")
        ax5.set_title("Keyword Cloud (not available)")

    # 6. Keyword average scores
    ax6 = fig.add_subplot(gs[2, 0])
    if len(keyword_avg_scores) > 0:
        top_kw_scores = keyword_avg_scores.head(10)
        colors = plt.cm.RdYlGn(top_kw_scores["mean"].values)
        bars = ax6.barh(range(len(top_kw_scores)), top_kw_scores["mean"].values, color=colors)
        ax6.set_yticks(range(len(top_kw_scores)))
        ax6.set_yticklabels(top_kw_scores["keyword"].values, fontsize=8)
        ax6.invert_yaxis()
        ax6.set_xlabel("Average LLM Score")
        ax6.set_title("Top 10 Keywords by Avg Score")
        ax6.set_xlim(0, 1)
        ax6.axvline(x=0.7, color="green", linestyle="--", alpha=0.5)
        ax6.axvline(x=0.4, color="orange", linestyle="--", alpha=0.5)

    # 7. Score over time
    ax7 = fig.add_subplot(gs[2, 1])
    df["scored_at"] = pd.to_datetime(df["scored_at"])
    df_sorted = df.sort_values("scored_at")
    df_sorted["rolling_mean"] = df_sorted["llm_score"].rolling(window=50, min_periods=1).mean()
    ax7.plot(df_sorted["scored_at"], df_sorted["rolling_mean"], color="steelblue", linewidth=2)
    ax7.fill_between(df_sorted["scored_at"], df_sorted["rolling_mean"], alpha=0.3)
    ax7.set_xlabel("Date")
    ax7.set_ylabel("Rolling Mean Score (50)")
    ax7.set_title("Score Trend Over Time")
    ax7.tick_params(axis="x", rotation=30)

    # 8. Summary statistics table
    ax8 = fig.add_subplot(gs[2, 2])
    ax8.axis("off")

    summary_data = [
        ["Total Profiles", str(len(df))],
        ["Mean Score", f"{df['llm_score'].mean():.3f}"],
        ["Median Score", f"{df['llm_score'].median():.3f}"],
        ["Std Dev", f"{df['llm_score'].std():.3f}"],
        ["HAS Correlation", f"{corr:.3f}"],
        ["High (>=0.7)", f"{high} ({100*high/len(df):.1f}%)"],
        ["Medium (0.4-0.7)", f"{medium} ({100*medium/len(df):.1f}%)"],
        ["Low (<0.4)", f"{low} ({100*low/len(df):.1f}%)"],
        ["Unique Keywords", str(keywords_df["keyword"].nunique())],
    ]

    table = ax8.table(
        cellText=summary_data,
        colLabels=["Metric", "Value"],
        loc="center",
        cellLoc="left",
        colWidths=[0.6, 0.4],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1.2, 1.8)
    ax8.set_title("Summary Statistics", y=0.95)

    # Main title
    model_short = model.replace("-", " ").title()
    fig.suptitle(
        f"Profile Scores Analysis: {model}\n({len(df)} profiles scored)",
        fontsize=14,
        fontweight="bold",
    )

    # Save plot with unix timestamp
    timestamp = int(time.time())
    # Sanitize model name for filename
    model_safe = model.replace("/", "-").replace(":", "-")
    output_path = Path(__file__).parent.parent / "output" / f"{timestamp}-modelscores-{model_safe}.png"
    output_path.parent.mkdir(exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"\nPlot saved to: {output_path}")

    plt.show()


if __name__ == "__main__":
    main()
