#!/usr/bin/env python3
"""Test LLM labeling directly without database storage.

Usage:
    # From airflow directory, with .env sourced:
    source ../infra/.env && uv run python scripts/test_llm_direct.py

    # Or specify model:
    source ../infra/.env && uv run python scripts/test_llm_direct.py --model gemini-flash-2.0
"""

from __future__ import annotations

import argparse
import os
import sys

# Ensure we can import from packages
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    """Run direct LLM test."""
    parser = argparse.ArgumentParser(description="Test LLM labeling directly")
    parser.add_argument(
        "--model",
        default="gemini-flash-2.0",
        help="Model alias to test (default: gemini-flash-2.0)",
    )
    args = parser.parse_args()

    # Set required env vars for settings
    os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@localhost:5432/unused")
    os.environ.setdefault("APP_MODE", "development")
    os.environ.setdefault("TWITTERX_APIKEY", "unused")

    # Check API keys
    gemini_key = os.environ.get("GEMINI_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    groq_key = os.environ.get("GROQ_API_KEY")

    print("=== API Key Status ===")
    print(f"GEMINI_API_KEY: {'✓ Set' if gemini_key else '✗ Missing'}")
    print(f"ANTHROPIC_API_KEY: {'✓ Set' if anthropic_key else '✗ Missing'}")
    print(f"GROQ_API_KEY: {'✓ Set' if groq_key else '✗ Missing'}")
    print()

    # Import after env setup
    from scoring.llm import AudienceConfig, label_batch
    from scoring.llm.registry import get_registry
    from scoring.llm.types import ProfileToLabel

    # Mock profiles for testing
    mock_profiles = [
        ProfileToLabel(
            twitter_id="1001",
            handle="dr_jane_researcher",
            name="Dr. Jane Smith",
            bio="Associate Professor of Molecular Biology at Stanford. PhD MIT. Studying gene regulation and CRISPR applications. Nature/Science publications.",
            category="Academic",
            followers=12500,
            likely_is="Human",
        ),
        ProfileToLabel(
            twitter_id="1002",
            handle="crypto_moon_guy",
            name="🚀 CryptoMoonShot 🌙",
            bio="10x your portfolio! NFT drops daily. DM for alpha. NFA DYOR 🔥",
            category="Crypto",
            followers=45000,
            likely_is="Bot",
        ),
        ProfileToLabel(
            twitter_id="1003",
            handle="biotech_founder",
            name="Sarah Chen",
            bio="CEO @GenomeTech | Ex-Genentech | Building the future of personalized medicine | Stanford MBA | Hiring!",
            category="Entrepreneur",
            followers=8200,
            likely_is="Human",
        ),
        ProfileToLabel(
            twitter_id="1004",
            handle="phd_student_bio",
            name="Alex Martinez",
            bio="PhD candidate @UCBerkeley studying computational biology. Python enthusiast. Coffee addict ☕",
            category=None,
            followers=890,
            likely_is="Human",
        ),
        ProfileToLabel(
            twitter_id="1005",
            handle="sales_guru_pro",
            name="Sales Mastery Coach",
            bio="Helped 10,000+ close deals | DM 'SALES' for free training | Founder @SalesAcademy",
            category="Marketing",
            followers=32000,
            likely_is="Human",
        ),
    ]

    # Mock audience config for life sciences
    audience_config = AudienceConfig(
        target_profile="Academic researchers and scientists in life sciences, biotechnology, and related fields",
        sector="academia",
        high_signals=[
            "PhD",
            "Professor",
            "Researcher",
            "Scientist",
            "University",
            "Lab",
            "Publications",
            "Nature",
            "Science",
            "Cell",
            "Postdoc",
        ],
        low_signals=[
            "Crypto",
            "NFT",
            "Marketing",
            "Sales",
            "Coach",
            "DM for",
            "10x",
            "moon",
        ],
        null_signals=["Student", "Intern", "Aspiring"],
        domain_context="Life sciences academic and research community, including biology, biotechnology, genomics, and pharmaceutical research.",
        notes="Focus on established researchers with clear academic credentials",
        config_name="test_life_sciences.v1",
    )

    # Resolve model
    registry = get_registry()
    try:
        model_config = registry.resolve(args.model)
    except ValueError as e:
        print(f"Error: {e}")
        print(f"Available models: {', '.join(registry.available_models())}")
        sys.exit(1)

    print(f"=== Testing Model: {model_config.alias} ===")
    print(f"Full name: {model_config.full_name}")
    print(f"Provider: {model_config.provider.value}")
    print(f"Profiles to classify: {len(mock_profiles)}")
    print()

    # Call the labeler
    print("Calling LLM...")
    result = label_batch(mock_profiles, model_config, audience_config)

    print()
    print("=== Results ===")
    print(f"Model: {result.model}")
    print(f"Input tokens: {result.metadata.input_tokens}")
    print(f"Output tokens: {result.metadata.output_tokens}")
    print(f"Cost: ${result.metadata.call_cost:.6f}")
    print()

    if result.results:
        print("Labels:")
        for label_result in result.results:
            # Find the profile
            profile = next((p for p in mock_profiles if p.twitter_id == label_result.twitter_id), None)
            handle = profile.handle if profile else "unknown"
            label_str = "✓ TRUE" if label_result.label is True else ("✗ FALSE" if label_result.label is False else "? NULL")
            print(f"  @{handle}: {label_str}")
            print(f"    Reason: {label_result.reason}")
    else:
        print("No results returned (possible error)")

    print()
    print("=== Raw Response Object ===")
    print(result)


if __name__ == "__main__":
    main()
