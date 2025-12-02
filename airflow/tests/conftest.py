"""Pytest fixtures and configuration."""

from __future__ import annotations

import pytest


@pytest.fixture
def mock_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set up mock environment variables for testing."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("APP_MODE", "development")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
