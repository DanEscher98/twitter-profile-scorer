"""Structured logging with structlog."""

from __future__ import annotations

import logging
import sys
from functools import lru_cache
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from structlog.typing import FilteringBoundLogger


@lru_cache(maxsize=1)
def _configure_logging(*, is_production: bool, is_silent: bool) -> None:
    """Configure structlog for the application.

    Args:
        is_production: Use JSON output for production, colorized for dev.
        is_silent: Suppress all logging output.
    """
    # Set log level
    min_level = logging.CRITICAL if is_silent else logging.INFO

    # Shared processors for all environments
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if is_production:
        # JSON logging for production/Airflow
        structlog.configure(
            processors=[
                *shared_processors,
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(min_level),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
            cache_logger_on_first_use=True,
        )
    else:
        # Colorized logging for development
        structlog.configure(
            processors=[
                *shared_processors,
                structlog.dev.ConsoleRenderer(colors=True),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(
                logging.DEBUG if not is_silent else min_level,
            ),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )


def get_logger(name: str) -> FilteringBoundLogger:
    """Get a logger for a specific module/service.

    Args:
        name: Logger name (typically module name like "scorer_twitter.client").

    Returns:
        Configured structlog logger with service context.

    Example:
        >>> log = get_logger("scorer_twitter.client")
        >>> log.info("fetching profiles", keyword="researcher", count=20)
    """
    # Lazy configuration on first logger access
    from scorer_utils.settings import get_settings

    settings = get_settings()
    _configure_logging(is_production=settings.is_production, is_silent=settings.is_silent)

    return structlog.get_logger(service=name)
