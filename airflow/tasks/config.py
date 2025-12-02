"""Pipeline configuration and shared types."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class PipelineConfig(BaseModel):
    """Global pipeline configuration."""

    # Keyword sampling
    keyword_count: int = Field(default=5, description="Number of keywords to sample per run")

    # Search settings
    items_per_search: int = Field(default=20, description="Profiles per API search")

    # HAS thresholds
    has_threshold: float = Field(default=0.65, description="Min HAS to queue for LLM scoring")
    llm_threshold: float = Field(default=0.55, description="Min HAS for LLM scoring query")

    # LLM settings
    default_audience: str = Field(
        default="thelai_customers.v3",
        description="Default audience config",
    )
    model_aliases: list[str] = Field(
        default_factory=lambda: ["meta-maverick-17b", "claude-haiku-4.5", "gemini-flash-2.0"],
        description="LLM models to use for scoring",
    )

    # Paths
    audiences_dir: Path = Field(
        default_factory=lambda: Path("/opt/airflow/audiences"),
        description="Directory containing audience configs",
    )


# Global config instance (can be overridden in tests)
DEFAULT_CONFIG = PipelineConfig()


def get_config() -> PipelineConfig:
    """Get the current pipeline configuration."""
    return DEFAULT_CONFIG
