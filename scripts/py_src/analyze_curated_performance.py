#!/usr/bin/env python3
"""
Analyze Curated Profile Performance

Analyzes how well the scoring system performs by comparing curated profiles
(marked with @customers keyword) against the general pool.

Hypothesis: If all @customers have score > 0.7, the system has low false negatives.

Metrics:
- Distribution of curated profiles in final ranked list
- False negative rate (curated profiles scoring < threshold)
- Score statistics comparison (curated vs general pool)
- Percentile analysis of curated profiles

Usage:
  cd scripts && uv run py_src/analyze_curated_performance.py

Output:
  scripts/output/<timestamp>-false_negatives_performance.01.png
  scripts/output/<timestamp>-curated-analysis.json
  scripts/output/<timestamp>-curated-analysis.txt
"""

import json
import os
import sys
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import psycopg2
import seaborn as sns
from dotenv import load_dotenv

# Load environment from root
load_dotenv(Path(__file__).parent.parent.parent / ".env")

# Scoring weights
HAS_WEIGHT = 0.2
LLM_WEIGHT = 0.8
HIGH_SCORE_THRESHOLD = 0.7
MEDIUM_SCORE_THRESHOLD = 0.6

# Curated keyword marker
CURATED_KEYWORD = "@customers"


def get_connection():
    """Get database connection from DATABASE_URL."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("Error: DATABASE_URL not set")
        print("Run: export DATABASE_URL=$(cd infra && uv run pulumi stack output db_connection_string --show-secrets)")
        sys.exit(1)
    return psycopg2.connect(database_url)


def fetch_all_scores(conn) -> pd.DataFrame:
    """Fetch all profiles with their scores and compute final score."""
    query = """
    WITH llm_avg AS (
        SELECT
            twitter_id,
            AVG(score::float) as avg_llm_score,
            COUNT(*) as llm_count,
            array_agg(DISTINCT scored_by) as models
        FROM profile_scores
        GROUP BY twitter_id
    ),
    profile_keywords AS (
        SELECT
            twitter_id,
            array_agg(DISTINCT keyword) as keywords,
            bool_or(keyword = %s) as is_curated
        FROM user_keywords
        GROUP BY twitter_id
    )
    SELECT
        p.twitter_id,
        p.username,
        p.display_name,
        p.bio,
        p.human_score::float as has_score,
        p.likely_is,
        COALESCE(l.avg_llm_score, 0) as avg_llm_score,
        COALESCE(l.llm_count, 0) as llm_count,
        l.models,
        COALESCE(k.keywords, ARRAY[]::text[]) as keywords,
        COALESCE(k.is_curated, false) as is_curated,
        s.followers
    FROM user_profiles p
    LEFT JOIN llm_avg l ON p.twitter_id = l.twitter_id
    LEFT JOIN profile_keywords k ON p.twitter_id = k.twitter_id
    LEFT JOIN user_stats s ON p.twitter_id = s.twitter_id
    ORDER BY p.twitter_id
    """

    df = pd.read_sql(query, conn, params=(CURATED_KEYWORD,))

    # Compute final score
    # If has LLM scores: 0.2 * HAS + 0.8 * AVG_LLM
    # If no LLM scores: use HAS directly
    df["has_llm"] = df["llm_count"] > 0
    df["final_score"] = np.where(
        df["has_llm"],
        HAS_WEIGHT * df["has_score"] + LLM_WEIGHT * df["avg_llm_score"],
        df["has_score"]
    )

    return df


def analyze_performance(df: pd.DataFrame) -> dict:
    """Analyze scoring system performance."""
    curated = df[df["is_curated"]]
    general = df[~df["is_curated"]]
    scored = df[df["has_llm"]]
    curated_scored = curated[curated["has_llm"]]

    # Basic counts
    stats = {
        "total_profiles": len(df),
        "total_curated": len(curated),
        "total_scored": len(scored),
        "curated_scored": len(curated_scored),
        "curated_unscored": len(curated) - len(curated_scored),
    }

    # Curated score distribution
    if len(curated_scored) > 0:
        stats["curated_mean"] = curated_scored["final_score"].mean()
        stats["curated_median"] = curated_scored["final_score"].median()
        stats["curated_min"] = curated_scored["final_score"].min()
        stats["curated_max"] = curated_scored["final_score"].max()
        stats["curated_std"] = curated_scored["final_score"].std()

        # False negative analysis
        stats["curated_above_07"] = (curated_scored["final_score"] >= HIGH_SCORE_THRESHOLD).sum()
        stats["curated_above_06"] = (curated_scored["final_score"] >= MEDIUM_SCORE_THRESHOLD).sum()
        stats["curated_below_06"] = (curated_scored["final_score"] < MEDIUM_SCORE_THRESHOLD).sum()

        stats["false_negative_rate_07"] = 1 - (stats["curated_above_07"] / len(curated_scored))
        stats["false_negative_rate_06"] = 1 - (stats["curated_above_06"] / len(curated_scored))

    # General pool stats (scored only)
    general_scored = general[general["has_llm"]]
    if len(general_scored) > 0:
        stats["general_mean"] = general_scored["final_score"].mean()
        stats["general_median"] = general_scored["final_score"].median()
        stats["general_std"] = general_scored["final_score"].std()
        stats["general_above_07"] = (general_scored["final_score"] >= HIGH_SCORE_THRESHOLD).sum()
        stats["general_above_06"] = (general_scored["final_score"] >= MEDIUM_SCORE_THRESHOLD).sum()

    # Percentile analysis - where do curated profiles rank?
    if len(scored) > 0 and len(curated_scored) > 0:
        all_scores_sorted = scored["final_score"].sort_values(ascending=False).reset_index(drop=True)
        total_scored = len(all_scores_sorted)

        curated_percentiles = []
        for score in curated_scored["final_score"]:
            rank = (all_scores_sorted >= score).sum()
            percentile = (total_scored - rank) / total_scored * 100
            curated_percentiles.append(100 - percentile)  # Top X%

        stats["curated_avg_percentile"] = np.mean(curated_percentiles)
        stats["curated_median_percentile"] = np.median(curated_percentiles)
        stats["curated_in_top_10pct"] = sum(p <= 10 for p in curated_percentiles)
        stats["curated_in_top_25pct"] = sum(p <= 25 for p in curated_percentiles)
        stats["curated_in_top_50pct"] = sum(p <= 50 for p in curated_percentiles)

    return stats


def generate_report(df: pd.DataFrame, stats: dict) -> str:
    """Generate text report."""
    curated = df[df["is_curated"]]
    curated_scored = curated[curated["has_llm"]]

    report = f"""CURATED PROFILE PERFORMANCE ANALYSIS
=====================================
Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}

HYPOTHESIS TEST
---------------
If all curated profiles (@customers) score >= 0.7, the system has low false negatives.

DATASET OVERVIEW
----------------
Total profiles in DB: {stats['total_profiles']:,}
Total with LLM scores: {stats['total_scored']:,}
Curated profiles (@customers): {stats['total_curated']}
  - With LLM scores: {stats['curated_scored']}
  - Without LLM scores: {stats['curated_unscored']}

SCORING FORMULA
---------------
Final Score = 0.2 × HAS + 0.8 × AVG_LLM (when LLM scores exist)
Final Score = HAS (when no LLM scores)

CURATED PROFILE STATISTICS
--------------------------
"""

    if stats.get("curated_mean"):
        report += f"""Mean score: {stats['curated_mean']:.4f}
Median score: {stats['curated_median']:.4f}
Std deviation: {stats['curated_std']:.4f}
Min score: {stats['curated_min']:.4f}
Max score: {stats['curated_max']:.4f}

FALSE NEGATIVE ANALYSIS
-----------------------
Curated profiles >= 0.7 (HIGH): {stats['curated_above_07']}/{stats['curated_scored']} ({stats['curated_above_07']/stats['curated_scored']*100:.1f}%)
Curated profiles >= 0.6 (MEDIUM): {stats['curated_above_06']}/{stats['curated_scored']} ({stats['curated_above_06']/stats['curated_scored']*100:.1f}%)
Curated profiles < 0.6 (FALSE NEG): {stats['curated_below_06']}/{stats['curated_scored']} ({stats['curated_below_06']/stats['curated_scored']*100:.1f}%)

False Negative Rate (threshold 0.7): {stats['false_negative_rate_07']*100:.1f}%
False Negative Rate (threshold 0.6): {stats['false_negative_rate_06']*100:.1f}%
"""

    if stats.get("general_mean"):
        report += f"""
GENERAL POOL COMPARISON (scored profiles)
-----------------------------------------
General pool mean: {stats['general_mean']:.4f}
General pool median: {stats['general_median']:.4f}
General pool std: {stats['general_std']:.4f}
General >= 0.7: {stats['general_above_07']:,}
General >= 0.6: {stats['general_above_06']:,}

Curated mean vs General mean: {stats['curated_mean'] - stats['general_mean']:+.4f}
"""

    if stats.get("curated_avg_percentile"):
        report += f"""
PERCENTILE ANALYSIS (where curated profiles rank)
-------------------------------------------------
Average percentile: Top {stats['curated_avg_percentile']:.1f}%
Median percentile: Top {stats['curated_median_percentile']:.1f}%
In top 10%: {stats['curated_in_top_10pct']}/{stats['curated_scored']}
In top 25%: {stats['curated_in_top_25pct']}/{stats['curated_scored']}
In top 50%: {stats['curated_in_top_50pct']}/{stats['curated_scored']}
"""

    # List curated profiles below threshold
    below_threshold = curated_scored[curated_scored["final_score"] < MEDIUM_SCORE_THRESHOLD]
    if len(below_threshold) > 0:
        report += f"""
FALSE NEGATIVES (curated profiles < 0.6)
----------------------------------------
"""
        for _, row in below_threshold.sort_values("final_score").iterrows():
            bio_preview = (row["bio"] or "No bio")[:50].replace("\n", " ")
            report += f"@{row['username']}: {row['final_score']:.4f} (HAS={row['has_score']:.2f}, LLM={row['avg_llm_score']:.2f}) - {bio_preview}...\n"

    # Top curated profiles
    report += f"""
TOP 10 CURATED PROFILES
-----------------------
"""
    for _, row in curated_scored.nlargest(10, "final_score").iterrows():
        bio_preview = (row["bio"] or "No bio")[:40].replace("\n", " ")
        report += f"@{row['username']}: {row['final_score']:.4f} - {bio_preview}...\n"

    # Conclusion
    report += f"""
CONCLUSION
----------
"""
    if stats.get("false_negative_rate_07"):
        if stats["false_negative_rate_07"] <= 0.1:
            report += "✓ EXCELLENT: Less than 10% false negatives at 0.7 threshold.\n"
        elif stats["false_negative_rate_07"] <= 0.2:
            report += "◐ GOOD: Less than 20% false negatives at 0.7 threshold.\n"
        else:
            report += f"✗ NEEDS IMPROVEMENT: {stats['false_negative_rate_07']*100:.1f}% false negatives at 0.7 threshold.\n"

        if stats["false_negative_rate_06"] <= 0.05:
            report += "✓ EXCELLENT: Less than 5% false negatives at 0.6 threshold.\n"
        elif stats["false_negative_rate_06"] <= 0.1:
            report += "◐ GOOD: Less than 10% false negatives at 0.6 threshold.\n"
        else:
            report += f"✗ NEEDS IMPROVEMENT: {stats['false_negative_rate_06']*100:.1f}% false negatives at 0.6 threshold.\n"

    if stats.get("curated_avg_percentile"):
        if stats["curated_avg_percentile"] <= 15:
            report += f"✓ EXCELLENT: Curated profiles rank in top {stats['curated_avg_percentile']:.1f}% on average.\n"
        elif stats["curated_avg_percentile"] <= 30:
            report += f"◐ GOOD: Curated profiles rank in top {stats['curated_avg_percentile']:.1f}% on average.\n"
        else:
            report += f"✗ NEEDS IMPROVEMENT: Curated profiles only rank in top {stats['curated_avg_percentile']:.1f}% on average.\n"

    return report


def generate_json_summary(df: pd.DataFrame, stats: dict) -> dict:
    """Generate JSON summary of findings."""
    curated = df[df["is_curated"]]
    curated_scored = curated[curated["has_llm"]]
    general = df[~df["is_curated"]]
    general_scored = general[general["has_llm"]]

    # Get false negatives details
    false_negatives = []
    if len(curated_scored) > 0:
        below_threshold = curated_scored[curated_scored["final_score"] < MEDIUM_SCORE_THRESHOLD]
        for _, row in below_threshold.iterrows():
            false_negatives.append({
                "username": row["username"],
                "final_score": round(float(row["final_score"]), 4),
                "has_score": round(float(row["has_score"]), 4),
                "avg_llm_score": round(float(row["avg_llm_score"]), 4),
                "likely_is": row["likely_is"],
                "bio_preview": (row["bio"] or "")[:100].replace("\n", " ")
            })

    # Get top performers
    top_performers = []
    if len(curated_scored) > 0:
        for _, row in curated_scored.nlargest(5, "final_score").iterrows():
            top_performers.append({
                "username": row["username"],
                "final_score": round(float(row["final_score"]), 4),
                "has_score": round(float(row["has_score"]), 4),
                "avg_llm_score": round(float(row["avg_llm_score"]), 4),
            })

    summary = {
        "generated_at": time.strftime('%Y-%m-%dT%H:%M:%SZ'),
        "scoring_formula": {
            "with_llm": "0.2 × HAS + 0.8 × AVG_LLM",
            "without_llm": "HAS (direct)",
            "has_weight": HAS_WEIGHT,
            "llm_weight": LLM_WEIGHT,
        },
        "thresholds": {
            "high_score": HIGH_SCORE_THRESHOLD,
            "medium_score": MEDIUM_SCORE_THRESHOLD,
        },
        "dataset": {
            "total_profiles": int(stats["total_profiles"]),
            "total_with_llm_scores": int(stats["total_scored"]),
            "curated_profiles": int(stats["total_curated"]),
            "curated_with_llm_scores": int(stats["curated_scored"]),
            "curated_without_llm_scores": int(stats["curated_unscored"]),
            "general_pool_with_llm_scores": int(len(general_scored)),
        },
        "curated_statistics": {},
        "general_statistics": {},
        "false_negative_analysis": {},
        "percentile_analysis": {},
        "false_negatives": false_negatives,
        "top_performers": top_performers,
        "conclusion": {},
    }

    # Curated statistics
    if stats.get("curated_mean"):
        summary["curated_statistics"] = {
            "mean": round(float(stats["curated_mean"]), 4),
            "median": round(float(stats["curated_median"]), 4),
            "std_deviation": round(float(stats["curated_std"]), 4),
            "min": round(float(stats["curated_min"]), 4),
            "max": round(float(stats["curated_max"]), 4),
        }
        summary["false_negative_analysis"] = {
            "above_high_threshold": int(stats["curated_above_07"]),
            "above_medium_threshold": int(stats["curated_above_06"]),
            "below_medium_threshold": int(stats["curated_below_06"]),
            "false_negative_rate_at_07": round(float(stats["false_negative_rate_07"]), 4),
            "false_negative_rate_at_06": round(float(stats["false_negative_rate_06"]), 4),
            "false_negative_count": len(false_negatives),
        }

    # General statistics
    if stats.get("general_mean"):
        summary["general_statistics"] = {
            "mean": round(float(stats["general_mean"]), 4),
            "median": round(float(stats["general_median"]), 4),
            "std_deviation": round(float(stats["general_std"]), 4),
            "above_high_threshold": int(stats["general_above_07"]),
            "above_medium_threshold": int(stats["general_above_06"]),
        }

    # Percentile analysis
    if stats.get("curated_avg_percentile"):
        summary["percentile_analysis"] = {
            "average_percentile": round(float(stats["curated_avg_percentile"]), 2),
            "median_percentile": round(float(stats["curated_median_percentile"]), 2),
            "in_top_10_percent": int(stats["curated_in_top_10pct"]),
            "in_top_25_percent": int(stats["curated_in_top_25pct"]),
            "in_top_50_percent": int(stats["curated_in_top_50pct"]),
        }

    # Conclusion
    if stats.get("false_negative_rate_07"):
        fnr_07 = stats["false_negative_rate_07"]
        fnr_06 = stats["false_negative_rate_06"]
        avg_pct = stats.get("curated_avg_percentile", 50)

        summary["conclusion"] = {
            "fnr_07_rating": "excellent" if fnr_07 <= 0.1 else "good" if fnr_07 <= 0.2 else "needs_improvement",
            "fnr_06_rating": "excellent" if fnr_06 <= 0.05 else "good" if fnr_06 <= 0.1 else "needs_improvement",
            "percentile_rating": "excellent" if avg_pct <= 15 else "good" if avg_pct <= 30 else "needs_improvement",
            "overall_assessment": (
                "System performs well at identifying quality profiles"
                if fnr_06 <= 0.1 and avg_pct <= 25
                else "System needs tuning to reduce false negatives"
            ),
        }

    return summary


def create_visualization(df: pd.DataFrame, stats: dict, output_path: Path):
    """Create visualization of curated profile performance with detailed labels."""
    curated = df[df["is_curated"]]
    general = df[~df["is_curated"]]
    scored = df[df["has_llm"]]
    curated_scored = curated[curated["has_llm"]]
    general_scored = general[general["has_llm"]]

    fig = plt.figure(figsize=(18, 14))
    gs = fig.add_gridspec(3, 3, hspace=0.35, wspace=0.3)

    # 1. Score distribution comparison (violin plot)
    ax1 = fig.add_subplot(gs[0, :2])
    if len(curated_scored) > 0 and len(general_scored) > 0:
        # Sample general pool to avoid overcrowding
        general_sample = general_scored[["final_score"]].sample(
            min(1000, len(general_scored)), random_state=42
        ).assign(group="General Pool")
        plot_data = pd.concat([
            curated_scored[["final_score"]].assign(group="Curated (@customers)"),
            general_sample
        ])
        sns.violinplot(data=plot_data, x="group", y="final_score", ax=ax1,
                       palette=["#2ecc71", "#3498db"], inner="box")
        ax1.axhline(y=0.7, color="red", linestyle="--", alpha=0.7, label="High threshold (0.7)")
        ax1.axhline(y=0.6, color="orange", linestyle="--", alpha=0.7, label="Medium threshold (0.6)")
        ax1.set_ylabel("Final Score (0.2×HAS + 0.8×LLM)", fontsize=10)
        ax1.set_xlabel("")
        ax1.set_title("Score Distribution: Curated Profiles vs General Pool\n(Violin width shows density of scores)", fontsize=11)
        ax1.legend(loc="lower right", fontsize=9)

    # 2. Curated profile score histogram
    ax2 = fig.add_subplot(gs[0, 2])
    if len(curated_scored) > 0:
        ax2.hist(curated_scored["final_score"], bins=20, color="#2ecc71", edgecolor="white", alpha=0.8)
        ax2.axvline(x=0.7, color="red", linestyle="--", linewidth=2, label="High (0.7)")
        ax2.axvline(x=0.6, color="orange", linestyle="--", linewidth=2, label="Medium (0.6)")
        ax2.axvline(x=curated_scored["final_score"].mean(), color="purple", linestyle="-",
                    linewidth=2, label=f"Mean ({curated_scored['final_score'].mean():.2f})")
        ax2.set_xlabel("Final Score (higher = better match)", fontsize=10)
        ax2.set_ylabel("Number of Profiles", fontsize=10)
        ax2.set_title(f"Curated Profiles Score Histogram\n(n={len(curated_scored)} manually verified profiles)", fontsize=10)
        ax2.legend(fontsize=8)

    # 3. Percentile distribution of curated profiles
    ax3 = fig.add_subplot(gs[1, 0])
    if len(scored) > 0 and len(curated_scored) > 0:
        all_scores_sorted = scored["final_score"].sort_values(ascending=False).reset_index(drop=True)
        total = len(all_scores_sorted)

        percentiles = []
        for score in curated_scored["final_score"]:
            rank = (all_scores_sorted >= score).sum()
            percentile = rank / total * 100
            percentiles.append(percentile)

        ax3.hist(percentiles, bins=10, color="#9b59b6", edgecolor="white", alpha=0.8)
        ax3.axvline(x=10, color="green", linestyle="--", alpha=0.7, label="Top 10%")
        ax3.axvline(x=25, color="blue", linestyle="--", alpha=0.7, label="Top 25%")
        ax3.set_xlabel("Percentile Rank (lower = better)", fontsize=10)
        ax3.set_ylabel("Number of Curated Profiles", fontsize=10)
        ax3.set_title("Curated Profiles Ranking Position\n(Where known-good profiles appear in the sorted list)", fontsize=10)
        ax3.legend(fontsize=8)

    # 4. HAS vs LLM scatter for curated
    ax4 = fig.add_subplot(gs[1, 1])
    if len(curated_scored) > 0:
        scatter = ax4.scatter(curated_scored["has_score"], curated_scored["avg_llm_score"],
                              c=curated_scored["final_score"], cmap="RdYlGn",
                              s=50, alpha=0.8, edgecolors="black", linewidth=0.5)
        ax4.plot([0, 1], [0, 1], "k--", alpha=0.3, label="Perfect agreement")
        ax4.set_xlabel("HAS Score (Human Authenticity Score)", fontsize=10)
        ax4.set_ylabel("Average LLM Score", fontsize=10)
        ax4.set_title("HAS vs LLM Agreement for Curated Profiles\n(Color = final score, diagonal = perfect agreement)", fontsize=10)
        ax4.set_xlim(0, 1)
        ax4.set_ylim(0, 1)
        cbar = plt.colorbar(scatter, ax=ax4)
        cbar.set_label("Final Score", fontsize=9)

    # 5. Score buckets comparison (FALSE NEGATIVE BREAKDOWN)
    ax5 = fig.add_subplot(gs[1, 2])
    if len(curated_scored) > 0:
        curated_high = (curated_scored["final_score"] >= 0.7).sum()
        curated_med = ((curated_scored["final_score"] >= 0.6) & (curated_scored["final_score"] < 0.7)).sum()
        curated_low = (curated_scored["final_score"] < 0.6).sum()

        categories = ["High Score\n(≥0.7)\nCORRECT", "Medium Score\n(0.6-0.7)\nMARGINAL", "Low Score\n(<0.6)\nFALSE NEG"]
        counts = [curated_high, curated_med, curated_low]
        colors = ["#2ecc71", "#f39c12", "#e74c3c"]

        bars = ax5.bar(categories, counts, color=colors, edgecolor="black")
        ax5.set_ylabel("Number of Curated Profiles", fontsize=10)
        ax5.set_title("False Negative Analysis\n(How many known-good profiles fall below thresholds)", fontsize=10)

        for bar, count in zip(bars, counts):
            pct = count / len(curated_scored) * 100
            ax5.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.2,
                     f"{count}\n({pct:.1f}%)", ha="center", fontsize=9, fontweight="bold")

    # 6. Cumulative distribution comparison
    ax6 = fig.add_subplot(gs[2, :2])
    if len(curated_scored) > 0 and len(general_scored) > 0:
        curated_sorted = np.sort(curated_scored["final_score"])
        general_sorted = np.sort(general_scored["final_score"])

        curated_cdf = np.arange(1, len(curated_sorted) + 1) / len(curated_sorted)
        general_cdf = np.arange(1, len(general_sorted) + 1) / len(general_sorted)

        ax6.plot(curated_sorted, curated_cdf, label=f"Curated (n={len(curated_sorted)})",
                 color="#2ecc71", linewidth=2)
        ax6.plot(general_sorted, general_cdf, label=f"General (n={len(general_sorted):,})",
                 color="#3498db", linewidth=2, alpha=0.7)
        ax6.axvline(x=0.7, color="red", linestyle="--", alpha=0.5, label="High (0.7)")
        ax6.axvline(x=0.6, color="orange", linestyle="--", alpha=0.5, label="Medium (0.6)")
        ax6.set_xlabel("Final Score (higher = better match)", fontsize=10)
        ax6.set_ylabel("Cumulative Proportion (% of profiles at or below score)", fontsize=10)
        ax6.set_title("Cumulative Distribution: Curated vs General\n(Curves further RIGHT indicate higher scores. Curated profiles should be shifted right of general pool.)", fontsize=10)
        ax6.legend(fontsize=9)
        ax6.grid(True, alpha=0.3)

    # 7. Summary stats table
    ax7 = fig.add_subplot(gs[2, 2])
    ax7.axis("off")

    if stats.get("curated_mean"):
        summary_data = [
            ["Curated Profiles", str(stats["curated_scored"])],
            ["Mean Score", f"{stats['curated_mean']:.3f}"],
            ["Median Score", f"{stats['curated_median']:.3f}"],
            ["Above 0.7 (Correct)", f"{stats['curated_above_07']} ({stats['curated_above_07']/stats['curated_scored']*100:.1f}%)"],
            ["Above 0.6 (Acceptable)", f"{stats['curated_above_06']} ({stats['curated_above_06']/stats['curated_scored']*100:.1f}%)"],
            ["FALSE NEG RATE (0.7)", f"{stats['false_negative_rate_07']*100:.1f}%"],
            ["FALSE NEG RATE (0.6)", f"{stats['false_negative_rate_06']*100:.1f}%"],
            ["Avg Percentile Rank", f"Top {stats.get('curated_avg_percentile', 0):.1f}%"],
        ]

        table = ax7.table(
            cellText=summary_data,
            colLabels=["Metric", "Value"],
            loc="center",
            cellLoc="left",
            colWidths=[0.6, 0.4],
        )
        table.auto_set_font_size(False)
        table.set_fontsize(10)
        table.scale(1.2, 1.8)
        # Highlight false negative rows
        for i, row in enumerate(summary_data):
            if "FALSE NEG" in row[0]:
                table[(i + 1, 0)].set_facecolor("#ffcccc")
                table[(i + 1, 1)].set_facecolor("#ffcccc")
    ax7.set_title("Key Performance Metrics", y=0.95, fontsize=11, fontweight="bold")

    # Main title
    fig.suptitle(
        f"FALSE NEGATIVE PERFORMANCE ANALYSIS\n"
        f"Curated Profiles (@customers): {stats['curated_scored']} scored | General Pool: {len(general_scored):,} scored\n"
        f"Goal: Minimize false negatives (known-good profiles scoring below threshold)",
        fontsize=13, fontweight="bold"
    )

    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Plot saved to: {output_path}")


def main():
    print("Connecting to database...")
    conn = get_connection()

    print("Fetching all profiles and scores...")
    df = fetch_all_scores(conn)
    conn.close()

    print(f"Loaded {len(df)} profiles")
    print(f"  - {df['has_llm'].sum()} with LLM scores")
    print(f"  - {df['is_curated'].sum()} curated (@customers)")

    print("\nAnalyzing performance...")
    stats = analyze_performance(df)

    # Generate report
    report = generate_report(df, stats)
    print("\n" + "=" * 60)
    print(report)
    print("=" * 60)

    # Save outputs
    timestamp = int(time.time())
    output_dir = Path(__file__).parent.parent / "output"
    output_dir.mkdir(exist_ok=True)

    # Save text report
    report_path = output_dir / f"{timestamp}-curated-analysis.txt"
    report_path.write_text(report)
    print(f"\nText report saved to: {report_path}")

    # Save JSON summary
    json_summary = generate_json_summary(df, stats)
    json_path = output_dir / f"{timestamp}-curated-analysis.json"
    json_path.write_text(json.dumps(json_summary, indent=2))
    print(f"JSON summary saved to: {json_path}")

    # Create visualization with renamed filename
    plot_path = output_dir / f"{timestamp}-false_negatives_performance.01.png"
    create_visualization(df, stats, plot_path)

    # Show plot
    plt.show()


if __name__ == "__main__":
    main()
