"""Model registry: maps aliases to full model names and providers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel


class Provider(StrEnum):
    """LLM provider identifiers."""

    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    GROQ = "groq"


@dataclass(frozen=True)
class ModelConfig:
    """Configuration for a single LLM model."""

    alias: str
    full_name: str
    provider: Provider
    default_batch_size: int = 25
    probability: float = 1.0  # For probability-based invocation


class ModelRegistry:
    """Registry of available LLM models.

    Maps simplified aliases to full API model names and their providers.
    Use aliases for logging, full names for API calls and DB storage.
    """

    def __init__(self) -> None:
        """Initialize the model registry with all available models."""
        self._models: dict[str, ModelConfig] = {
            # Anthropic models
            "claude-haiku-4.5": ModelConfig(
                alias="claude-haiku-4.5",
                full_name="claude-haiku-4-5-20251001",
                provider=Provider.ANTHROPIC,
                default_batch_size=25,
                probability=0.7,
            ),
            "claude-sonnet-4.5": ModelConfig(
                alias="claude-sonnet-4.5",
                full_name="claude-sonnet-4-5-20250929",
                provider=Provider.ANTHROPIC,
                default_batch_size=15,
                probability=0.3,
            ),
            "claude-opus-4.5": ModelConfig(
                alias="claude-opus-4.5",
                full_name="claude-opus-4-5-20251101",
                provider=Provider.ANTHROPIC,
                default_batch_size=10,
                probability=0.1,
            ),
            # Google Gemini models
            "gemini-flash-2.0": ModelConfig(
                alias="gemini-flash-2.0",
                full_name="gemini-2.0-flash",
                provider=Provider.GOOGLE,
                default_batch_size=15,
                probability=0.4,
            ),
            "gemini-flash-1.5": ModelConfig(
                alias="gemini-flash-1.5",
                full_name="gemini-1.5-flash",
                provider=Provider.GOOGLE,
                default_batch_size=15,
                probability=0.2,
            ),
            # Groq/Meta models
            "meta-maverick-17b": ModelConfig(
                alias="meta-maverick-17b",
                full_name="meta-llama/llama-4-maverick-17b-128e-instruct",
                provider=Provider.GROQ,
                default_batch_size=25,
                probability=0.8,
            ),
        }

    def resolve(self, alias: str) -> ModelConfig:
        """Resolve a model alias to its full configuration.

        Args:
            alias: Simplified model name (e.g., "claude-haiku-4.5").

        Returns:
            ModelConfig with full name and provider.

        Raises:
            ValueError: If alias is not found in registry.
        """
        if alias not in self._models:
            available = ", ".join(sorted(self._models.keys()))
            msg = f"Unknown model alias: {alias}. Available: {available}"
            raise ValueError(msg)

        return self._models[alias]

    def get_chat_model(self, alias: str) -> BaseChatModel:
        """Get LangChain chat model for the given alias.

        Args:
            alias: Model alias to instantiate.

        Returns:
            Configured LangChain chat model.

        Raises:
            ValueError: If alias is unknown or provider not configured.
        """
        config = self.resolve(alias)

        if config.provider == Provider.ANTHROPIC:
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(
                model=config.full_name,
                temperature=0.1,
                max_tokens=2048,
            )

        if config.provider == Provider.GOOGLE:
            from langchain_google_genai import ChatGoogleGenerativeAI

            return ChatGoogleGenerativeAI(
                model=config.full_name,
                temperature=0.1,
                max_output_tokens=2048,
            )

        if config.provider == Provider.GROQ:
            from langchain_groq import ChatGroq

            return ChatGroq(
                model=config.full_name,
                temperature=0.1,
                max_tokens=2048,
            )

        msg = f"Unknown provider: {config.provider}"
        raise ValueError(msg)

    def available_models(self) -> list[str]:
        """Get list of all available model aliases."""
        return sorted(self._models.keys())

    def models_by_provider(self, provider: Provider) -> list[ModelConfig]:
        """Get all models for a specific provider."""
        return [m for m in self._models.values() if m.provider == provider]


@lru_cache(maxsize=1)
def get_registry() -> ModelRegistry:
    """Get cached model registry instance."""
    return ModelRegistry()
