"""Tests for tasks configuration."""

from __future__ import annotations

from pathlib import Path

from tasks.config import PipelineConfig, get_config


class TestPipelineConfig:
    """Tests for pipeline configuration."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        config = PipelineConfig()

        assert config.keyword_count == 5
        assert config.items_per_search == 20
        assert config.has_threshold == 0.65
        assert config.llm_threshold == 0.55
        assert config.default_audience == "thelai_customers.v3"

    def test_custom_values(self) -> None:
        """Test custom configuration values."""
        config = PipelineConfig(
            keyword_count=10,
            items_per_search=50,
            has_threshold=0.70,
        )

        assert config.keyword_count == 10
        assert config.items_per_search == 50
        assert config.has_threshold == 0.70

    def test_model_aliases_default(self) -> None:
        """Test default model aliases."""
        config = PipelineConfig()

        assert "meta-maverick-17b" in config.model_aliases
        assert "claude-haiku-4.5" in config.model_aliases
        assert "gemini-flash-2.0" in config.model_aliases

    def test_audiences_dir_default(self) -> None:
        """Test default audiences directory."""
        config = PipelineConfig()

        assert config.audiences_dir == Path("/opt/airflow/audiences")

    def test_get_config(self) -> None:
        """Test get_config returns valid configuration."""
        config = get_config()

        assert isinstance(config, PipelineConfig)
        assert config.keyword_count > 0
