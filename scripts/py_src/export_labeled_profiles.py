#!/usr/bin/env python3
"""
Export labeled profiles to CSV for analysis.

Distribution: 30% true labels, 70% false/null labels
Total: 600 unique profiles

Usage:
    cd infra && uv run python ../scripts/py_src/export_labeled_profiles.py

Output:
    scripts/output/labeled_profiles_<timestamp>.csv
"""

import csv
import os
import random
import time
from datetime import datetime
from pathlib import Path

import pulumi
from pulumi import automation as auto


def get_db_connection_string() -> str:
    """Get database connection string from Pulumi stack."""
    stack = auto.select_stack(
        stack_name="dev",
        work_dir=str(Path(__file__).parent.parent.parent / "infra"),
    )
    outputs = stack.outputs()
    return outputs["db_connection_string"].value


def main():
    import psycopg2

    # Connect to database
    conn_str = get_db_connection_string()
    conn = psycopg2.connect(conn_str)
    cursor = conn.cursor()

    # Target distribution
    total_profiles = 600
    true_count = int(total_profiles * 0.30)  # 180
    other_count = total_profiles - true_count  # 420

    print(f"Target: {total_profiles} profiles ({true_count} true, {other_count} false/null)")

    # Fetch true-labeled profiles (randomized)
    cursor.execute("""
        SELECT DISTINCT ON (up.twitter_id)
            up.handle,
            up.name,
            up.bio,
            up.category,
            us.followers,
            ps.label,
            ps.reason
        FROM profile_scores ps
        JOIN user_profiles up ON ps.twitter_id = up.twitter_id
        LEFT JOIN user_stats us ON up.twitter_id = us.twitter_id
        WHERE ps.label = true
          AND up.bio IS NOT NULL AND up.bio != ''
          AND up.name IS NOT NULL AND up.name != ''
        ORDER BY up.twitter_id, RANDOM()
    """)
    true_profiles = cursor.fetchall()
    print(f"Found {len(true_profiles)} true-labeled profiles")

    # Fetch false/null-labeled profiles (randomized)
    cursor.execute("""
        SELECT DISTINCT ON (up.twitter_id)
            up.handle,
            up.name,
            up.bio,
            up.category,
            us.followers,
            ps.label,
            ps.reason
        FROM profile_scores ps
        JOIN user_profiles up ON ps.twitter_id = up.twitter_id
        LEFT JOIN user_stats us ON up.twitter_id = us.twitter_id
        WHERE (ps.label = false OR ps.label IS NULL)
          AND up.bio IS NOT NULL AND up.bio != ''
          AND up.name IS NOT NULL AND up.name != ''
        ORDER BY up.twitter_id, RANDOM()
    """)
    other_profiles = cursor.fetchall()
    print(f"Found {len(other_profiles)} false/null-labeled profiles")

    cursor.close()
    conn.close()

    # Sample profiles to meet target distribution
    if len(true_profiles) < true_count:
        print(f"Warning: Only {len(true_profiles)} true profiles available (need {true_count})")
        true_count = len(true_profiles)
        other_count = total_profiles - true_count

    if len(other_profiles) < other_count:
        print(f"Warning: Only {len(other_profiles)} false/null profiles available (need {other_count})")
        other_count = len(other_profiles)

    # Random sample
    random.shuffle(true_profiles)
    random.shuffle(other_profiles)

    selected_true = true_profiles[:true_count]
    selected_other = other_profiles[:other_count]

    all_profiles = selected_true + selected_other
    random.shuffle(all_profiles)  # Mix them together

    print(f"Selected: {len(selected_true)} true + {len(selected_other)} false/null = {len(all_profiles)} total")

    # Create output directory
    output_dir = Path(__file__).parent.parent / "output"
    output_dir.mkdir(exist_ok=True)

    # Generate output filename
    timestamp = int(time.time())
    output_file = output_dir / f"labeled_profiles_{timestamp}.csv"

    # Write CSV
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["HANDLE", "NAME", "BIO", "CATEGORY", "FOLLOWERS", "LABEL", "REASON"])

        for row in all_profiles:
            handle, name, bio, category, followers, label, reason = row
            # Convert label to string
            if label is True:
                label_str = "true"
            elif label is False:
                label_str = "false"
            else:
                label_str = "null"

            # Clean bio (remove newlines for CSV)
            bio_clean = (bio or "").replace("\n", " ").replace("\r", " ")

            writer.writerow([
                handle,
                name or "",
                bio_clean,
                category or "",
                followers or 0,
                label_str,
                reason or "",
            ])

    print(f"\nExported to: {output_file}")

    # Print distribution summary
    true_final = sum(1 for p in all_profiles if p[5] is True)
    false_final = sum(1 for p in all_profiles if p[5] is False)
    null_final = sum(1 for p in all_profiles if p[5] is None)

    print(f"\nFinal distribution:")
    print(f"  True:  {true_final} ({100*true_final/len(all_profiles):.1f}%)")
    print(f"  False: {false_final} ({100*false_final/len(all_profiles):.1f}%)")
    print(f"  Null:  {null_final} ({100*null_final/len(all_profiles):.1f}%)")


if __name__ == "__main__":
    main()
