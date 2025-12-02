"""Application settings with Pydantic Settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Environment
    app_mode: Literal["development", "production"] = Field(
        default="development",
        alias="APP_MODE",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "silent"] = Field(
        default="INFO",
        alias="LOG_LEVEL",
    )

    # Database - Optional for testing, required in production
    database_url: SecretStr | None = Field(default=None, alias="DATABASE_URL")
    rds_ca_path: Path | None = Field(default=None, alias="RDS_CA_PATH")

    # API Keys
    twitterx_apikey: SecretStr | None = Field(default=None, alias="TWITTERX_APIKEY")
    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    gemini_api_key: SecretStr | None = Field(default=None, alias="GEMINI_API_KEY")
    groq_api_key: SecretStr | None = Field(default=None, alias="GROQ_API_KEY")

    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return self.app_mode == "production"

    @property
    def is_silent(self) -> bool:
        """Check if logging should be suppressed."""
        return self.log_level == "silent"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()  # type: ignore[call-arg]
