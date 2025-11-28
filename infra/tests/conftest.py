"""
Pytest configuration and shared fixtures for E2E tests.

Usage:
    uv run pytest tests/e2e/ -v --log-level=INFO
    uv run pytest tests/e2e/ -v --log-level=DEBUG  # More detail
"""

import os
import json
import subprocess
import pytest
from typing import Generator
from dataclasses import dataclass

import boto3
import psycopg2


# ============================================================================
# Configuration
# ============================================================================

@dataclass
class InfraConfig:
    """Infrastructure configuration loaded from Pulumi outputs."""
    region: str
    db_connection_string: str
    orchestrator_name: str
    keyword_engine_name: str
    query_twitter_name: str
    llm_scorer_name: str
    keywords_queue_url: str
    keywords_dlq_url: str


def get_pulumi_output(key: str, show_secrets: bool = False) -> str:
    """Get a Pulumi stack output value."""
    cmd = ["uv", "run", "pulumi", "stack", "output", key]
    if show_secrets:
        cmd.append("--show-secrets")

    result = subprocess.run(
        cmd,
        cwd=os.path.dirname(os.path.dirname(__file__)),
        capture_output=True,
        text=True,
        env={**os.environ, "PULUMI_CONFIG_PASSPHRASE": os.environ.get("PULUMI_CONFIG_PASSPHRASE", "")},
    )

    if result.returncode != 0:
        raise RuntimeError(f"Failed to get Pulumi output '{key}': {result.stderr}")

    return result.stdout.strip()


@pytest.fixture(scope="session")
def infra_config() -> InfraConfig:
    """Load infrastructure configuration from Pulumi outputs."""
    return InfraConfig(
        region="us-east-2",
        db_connection_string=get_pulumi_output("db_connection_string", show_secrets=True),
        orchestrator_name=get_pulumi_output("orchestrator_name"),
        keyword_engine_name=get_pulumi_output("keyword_engine_name"),
        query_twitter_name=get_pulumi_output("query_twitter_name"),
        llm_scorer_name=get_pulumi_output("llm_scorer_name"),
        keywords_queue_url=get_pulumi_output("keywords_queue_url"),
        keywords_dlq_url=get_pulumi_output("keywords_dlq_url"),
    )


# ============================================================================
# AWS Clients
# ============================================================================

@pytest.fixture(scope="session")
def lambda_client(infra_config: InfraConfig):
    """Create AWS Lambda client."""
    return boto3.client("lambda", region_name=infra_config.region)


@pytest.fixture(scope="session")
def sqs_client(infra_config: InfraConfig):
    """Create AWS SQS client."""
    return boto3.client("sqs", region_name=infra_config.region)


@pytest.fixture(scope="session")
def logs_client(infra_config: InfraConfig):
    """Create AWS CloudWatch Logs client."""
    return boto3.client("logs", region_name=infra_config.region)


# ============================================================================
# Database Connection
# ============================================================================

@pytest.fixture(scope="function")
def db_connection(infra_config: InfraConfig) -> Generator[psycopg2.extensions.connection, None, None]:
    """Create a database connection for the test."""
    conn = psycopg2.connect(infra_config.db_connection_string)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="function")
def db_cursor(db_connection) -> Generator[psycopg2.extensions.cursor, None, None]:
    """Create a database cursor for the test."""
    cursor = db_connection.cursor()
    try:
        yield cursor
    finally:
        cursor.close()


# ============================================================================
# Test Markers
# ============================================================================

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "e2e: End-to-end tests requiring deployed infrastructure")
    config.addinivalue_line("markers", "slow: Tests that may take longer to run")
