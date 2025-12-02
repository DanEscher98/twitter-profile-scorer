"""Structured logging with Airflow integration.

In production (Airflow):
- Routes logs through Python's standard logging module
- Airflow automatically captures and stores task logs
- JSON format for structured log aggregation

In development (localhost):
- Uses structlog's colorized console output
- Easier to read during local development
"""

from __future__ import annotations

import logging
import sys
from functools import lru_cache
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from structlog.typing import FilteringBoundLogger


def _get_airflow_processors() -> list[structlog.types.Processor]:
    """Get processors for Airflow/production environment.

    Routes structlog output through Python's logging module,
    which Airflow captures for task logs.
    """
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ]


def _get_dev_processors() -> list[structlog.types.Processor]:
    """Get processors for development environment.

    Uses colorized console output for local debugging.
    """
    return [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.dev.ConsoleRenderer(colors=True),
    ]


def _configure_stdlib_logging(log_level: int) -> None:
    """Configure Python's standard logging for Airflow integration."""
    # Create formatter that structlog will use
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
        ],
    )

    # Configure root logger handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    # Only add handler if not already configured (Airflow may have its own)
    if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
        root_logger.addHandler(handler)
    root_logger.setLevel(log_level)


@lru_cache(maxsize=1)
def _configure_logging(*, is_production: bool, is_silent: bool) -> None:
    """Configure structlog for the application.

    Args:
        is_production: Use Airflow-compatible logging (routes through stdlib).
        is_silent: Suppress all logging output.
    """
    min_level = logging.CRITICAL if is_silent else logging.INFO

    if is_production:
        # Production: Route through Python logging for Airflow capture
        _configure_stdlib_logging(min_level)

        structlog.configure(
            processors=_get_airflow_processors(),
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )
    else:
        # Development: Direct console output with colors
        structlog.configure(
            processors=_get_dev_processors(),
            wrapper_class=structlog.make_filtering_bound_logger(
                logging.DEBUG if not is_silent else min_level,
            ),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )


def get_logger(name: str) -> FilteringBoundLogger:
    """Get a logger for a specific module/service.

    In production (Airflow):
        Logs are captured by Airflow's task logging system.

    In development:
        Logs are printed to console with colors.

    Args:
        name: Logger name (typically module name like "scoring.llm.labeler").

    Returns:
        Configured structlog logger with service context.

    Example:
        >>> log = get_logger("scoring.llm.labeler")
        >>> log.info("labeling_batch", model="claude-haiku-4.5", profiles=25)
    """
    from utils.settings import get_settings

    settings = get_settings()
    _configure_logging(is_production=settings.is_production, is_silent=settings.is_silent)

    return structlog.get_logger(service=name)
