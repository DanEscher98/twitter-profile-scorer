"""Tests for LLM scoring module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from scoring.llm import AudienceConfig, ModelConfig
from scoring.llm.registry import Provider, get_registry


class TestModelRegistry:
    """Tests for the model registry."""

    def test_resolve_valid_alias(self) -> None:
        """Test resolving a valid model alias."""
        registry = get_registry()
        config = registry.resolve("claude-haiku-4.5")

        assert config.alias == "claude-haiku-4.5"
        assert config.provider == Provider.ANTHROPIC
        assert config.full_name == "claude-haiku-4-5-20251001"

    def test_resolve_invalid_alias(self) -> None:
        """Test resolving an invalid model alias."""
        registry = get_registry()

        with pytest.raises(ValueError, match="Unknown model alias"):
            registry.resolve("invalid-model")

    def test_available_models(self) -> None:
        """Test getting available models list."""
        registry = get_registry()
        models = registry.available_models()

        assert "claude-haiku-4.5" in models
        assert "gemini-flash-2.0" in models
        assert "meta-maverick-17b" in models

    def test_models_by_provider(self) -> None:
        """Test filtering models by provider."""
        registry = get_registry()

        anthropic_models = registry.models_by_provider(Provider.ANTHROPIC)
        assert len(anthropic_models) >= 1
        assert all(m.provider == Provider.ANTHROPIC for m in anthropic_models)

        google_models = registry.models_by_provider(Provider.GOOGLE)
        assert len(google_models) >= 1
        assert all(m.provider == Provider.GOOGLE for m in google_models)


class TestAudienceConfig:
    """Tests for audience configuration."""

    def test_validate_audience_config(self, sample_audience_config: dict) -> None:
        """Test validating a valid audience config."""
        config = AudienceConfig.model_validate(sample_audience_config)

        assert config.sector == "academia"
        assert "PhD" in config.high_signals
        assert "Crypto" in config.low_signals

    def test_invalid_sector(self, sample_audience_config: dict) -> None:
        """Test validation with invalid sector."""
        from pydantic import ValidationError

        sample_audience_config["sector"] = "invalid"

        with pytest.raises(ValidationError):
            AudienceConfig.model_validate(sample_audience_config)


class TestLabelBatch:
    """Tests for label_batch function."""

    @patch("scoring.llm.labeler.get_registry")
    def test_empty_profiles(self, mock_registry: MagicMock) -> None:
        """Test labeling with empty profiles list."""
        from scoring.llm.labeler import label_batch

        mock_model_config = MagicMock()
        mock_model_config.alias = "test-model"

        result = label_batch([], mock_model_config, MagicMock())

        assert result.results == []
        assert result.metadata.input_tokens == 0
        assert result.metadata.output_tokens == 0
        assert result.metadata.call_cost == 0.0
        mock_registry.assert_not_called()

    @patch("scoring.llm.labeler.get_registry")
    def test_label_batch_success(
        self,
        mock_registry: MagicMock,
        sample_audience_config: dict,
    ) -> None:
        """Test successful label batch."""
        from scoring.llm.labeler import label_batch
        from scoring.llm.types import ProfileToLabel

        # Setup mock with proper usage_metadata
        mock_response = MagicMock()
        mock_response.content = '[{"handle": "testuser", "label": true, "reason": "Matches target profile"}]'
        mock_response.usage_metadata = {"input_tokens": 100, "output_tokens": 50}

        mock_chat_model = MagicMock()
        mock_chat_model.invoke.return_value = mock_response
        mock_registry.return_value.get_chat_model.return_value = mock_chat_model

        profiles = [
            ProfileToLabel(
                twitter_id="123",
                handle="testuser",
                name="Test User",
                bio="PhD researcher",
                category=None,
                followers=1000,
                likely_is="Human",
            )
        ]
        model_config = ModelConfig(
            alias="test-model",
            full_name="test-model-full",
            provider=Provider.ANTHROPIC,
        )
        audience_config = AudienceConfig.model_validate(sample_audience_config)

        result = label_batch(profiles, model_config, audience_config)

        assert len(result.results) == 1
        assert result.results[0].twitter_id == "123"
        assert result.results[0].label is True
        assert result.metadata.input_tokens > 0
        assert result.metadata.output_tokens > 0
        assert result.metadata.call_cost >= 0

    @patch("scoring.llm.labeler.get_registry")
    def test_label_batch_invalid_json(
        self,
        mock_registry: MagicMock,
        sample_audience_config: dict,
    ) -> None:
        """Test label batch with invalid JSON response."""
        from scoring.llm.labeler import label_batch
        from scoring.llm.types import ProfileToLabel

        # Setup mock with proper usage_metadata
        mock_response = MagicMock()
        mock_response.content = "not valid json"
        mock_response.usage_metadata = {"input_tokens": 100, "output_tokens": 10}

        mock_chat_model = MagicMock()
        mock_chat_model.invoke.return_value = mock_response
        mock_registry.return_value.get_chat_model.return_value = mock_chat_model

        profiles = [
            ProfileToLabel(
                twitter_id="123",
                handle="testuser",
                name="Test User",
                bio="PhD researcher",
                category=None,
                followers=1000,
                likely_is="Human",
            )
        ]
        model_config = ModelConfig(
            alias="test-model",
            full_name="test-model-full",
            provider=Provider.ANTHROPIC,
        )
        audience_config = AudienceConfig.model_validate(sample_audience_config)

        result = label_batch(profiles, model_config, audience_config)

        assert result.results == []
