"""Database session management with SSL support."""

from __future__ import annotations

import re
import ssl
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import create_engine
from sqlmodel import Session

from scorer_utils import get_logger
from scorer_utils.settings import get_settings

if TYPE_CHECKING:
    from sqlalchemy import Engine

log = get_logger("scorer_db.client")


def _find_rds_certificate() -> Path | None:
    """Find RDS CA certificate from known locations.

    Search order:
    1. RDS_CA_PATH environment variable
    2. /opt/airflow/certs/ (EC2 deployment)
    3. /var/task/ (Lambda, for backwards compat)
    4. Current directory
    """
    settings = get_settings()

    # 1. Explicit path from settings
    if settings.rds_ca_path and settings.rds_ca_path.exists():
        return settings.rds_ca_path

    # 2. Known locations
    cert_name = "aws-rds-global-bundle.pem"
    search_paths = [
        Path("/opt/airflow/certs") / cert_name,
        Path("/var/task") / cert_name,
        Path.cwd() / "certs" / cert_name,
        Path.cwd() / cert_name,
    ]

    for path in search_paths:
        if path.exists():
            log.debug("found_rds_certificate", path=str(path))
            return path

    log.warning("rds_certificate_not_found", searched=len(search_paths))
    return None


def _create_ssl_context() -> ssl.SSLContext | None:
    """Create SSL context for RDS connection."""
    cert_path = _find_rds_certificate()
    if cert_path is None:
        return None

    ssl_context = ssl.create_default_context(cafile=str(cert_path))
    ssl_context.verify_mode = ssl.CERT_REQUIRED
    return ssl_context


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Get cached SQLAlchemy engine with SSL configuration."""
    settings = get_settings()
    database_url = settings.database_url.get_secret_value()

    # Remove sslmode from URL if present (we handle SSL explicitly)
    if "sslmode=" in database_url:
        database_url = re.sub(r"\?sslmode=[^&]+&?", "?", database_url)
        database_url = re.sub(r"&sslmode=[^&]+", "", database_url)
        database_url = database_url.rstrip("?&")

    # Build connect_args with SSL
    connect_args: dict[str, ssl.SSLContext] = {}
    ssl_context = _create_ssl_context()
    if ssl_context:
        connect_args["ssl"] = ssl_context
        log.info("database_ssl_enabled")
    else:
        log.warning("database_ssl_disabled")

    engine = create_engine(
        database_url,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        pool_pre_ping=True,
        connect_args=connect_args,
    )

    return engine


@contextmanager
def get_session() -> Iterator[Session]:
    """Get database session with automatic cleanup.

    Usage:
        with get_session() as session:
            profile = session.get(UserProfile, twitter_id)
    """
    engine = get_engine()
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
