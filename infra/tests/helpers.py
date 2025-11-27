"""
Test helper utilities for AWS Lambda, SQS, and CloudWatch operations.
"""

import json
import time
import base64
from datetime import datetime, timedelta, timezone
from typing import Any
from dataclasses import dataclass
from enum import Enum

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box


console = Console()


class LogLevel(Enum):
    """Log level filter for CloudWatch logs."""
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


@dataclass
class LambdaResult:
    """Result from a Lambda invocation."""
    status_code: int
    payload: dict | None
    function_error: str | None
    log_result: str | None
    success: bool

    @property
    def failed(self) -> bool:
        return not self.success


@dataclass
class QueueStats:
    """Statistics for an SQS queue."""
    name: str
    messages_available: int
    messages_in_flight: int
    messages_delayed: int

    @property
    def total(self) -> int:
        return self.messages_available + self.messages_in_flight + self.messages_delayed


# ============================================================================
# Lambda Helpers
# ============================================================================

def invoke_lambda(
    client,
    function_name: str,
    payload: dict | None = None,
    log_type: str = "Tail"
) -> LambdaResult:
    """
    Invoke a Lambda function and return the result.

    Args:
        client: boto3 Lambda client
        function_name: Name of the Lambda function
        payload: JSON payload to send (optional)
        log_type: "Tail" to include logs, "None" to skip

    Returns:
        LambdaResult with status, payload, and logs
    """
    invoke_args = {
        "FunctionName": function_name,
        "LogType": log_type,
    }

    if payload is not None:
        invoke_args["Payload"] = json.dumps(payload).encode()

    response = client.invoke(**invoke_args)

    # Parse response payload
    response_payload = None
    if "Payload" in response:
        raw_payload = response["Payload"].read()
        if raw_payload:
            try:
                response_payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                response_payload = {"raw": raw_payload.decode()}

    # Decode base64 logs if present
    log_result = None
    if log_type == "Tail" and "LogResult" in response:
        log_result = base64.b64decode(response["LogResult"]).decode()

    function_error = response.get("FunctionError")
    status_code = response.get("StatusCode", 0)

    return LambdaResult(
        status_code=status_code,
        payload=response_payload,
        function_error=function_error,
        log_result=log_result,
        success=status_code == 200 and function_error is None,
    )


def invoke_lambda_with_sqs_event(
    client,
    function_name: str,
    message_body: dict
) -> LambdaResult:
    """
    Invoke a Lambda with an SQS-style event payload.

    This simulates how Lambda receives messages from SQS triggers.
    """
    event = {
        "Records": [
            {"body": json.dumps(message_body)}
        ]
    }
    return invoke_lambda(client, function_name, event)


# ============================================================================
# SQS Helpers
# ============================================================================

def get_queue_stats(client, queue_url: str) -> QueueStats:
    """Get statistics for an SQS queue."""
    response = client.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
            "ApproximateNumberOfMessagesDelayed",
        ]
    )

    attrs = response.get("Attributes", {})
    name = queue_url.split("/")[-1]

    return QueueStats(
        name=name,
        messages_available=int(attrs.get("ApproximateNumberOfMessages", 0)),
        messages_in_flight=int(attrs.get("ApproximateNumberOfMessagesNotVisible", 0)),
        messages_delayed=int(attrs.get("ApproximateNumberOfMessagesDelayed", 0)),
    )


def purge_queue(client, queue_url: str) -> None:
    """Purge all messages from an SQS queue."""
    try:
        client.purge_queue(QueueUrl=queue_url)
    except client.exceptions.PurgeQueueInProgress:
        # Queue is already being purged
        pass


def send_message(client, queue_url: str, message: dict) -> str:
    """Send a message to an SQS queue. Returns message ID."""
    response = client.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(message),
    )
    return response["MessageId"]


def wait_for_queue_empty(
    client,
    queue_url: str,
    timeout_seconds: int = 60,
    poll_interval: float = 2.0
) -> bool:
    """
    Wait for a queue to become empty.

    Returns True if queue is empty, False if timeout reached.
    """
    start = time.time()
    while time.time() - start < timeout_seconds:
        stats = get_queue_stats(client, queue_url)
        if stats.total == 0:
            return True
        time.sleep(poll_interval)
    return False


# ============================================================================
# CloudWatch Logs Helpers
# ============================================================================

def get_lambda_logs(
    client,
    function_name: str,
    since_minutes: int = 5,
    level: LogLevel = LogLevel.INFO,
    limit: int = 100
) -> list[dict]:
    """
    Get recent CloudWatch logs for a Lambda function.

    Args:
        client: boto3 CloudWatch Logs client
        function_name: Lambda function name
        since_minutes: How far back to look
        level: Minimum log level to include
        limit: Maximum number of log events

    Returns:
        List of log events with timestamp, level, message, and metadata
    """
    log_group = f"/aws/lambda/{function_name}"
    start_time = int((datetime.now(timezone.utc) - timedelta(minutes=since_minutes)).timestamp() * 1000)

    try:
        response = client.filter_log_events(
            logGroupName=log_group,
            startTime=start_time,
            limit=limit,
        )
    except client.exceptions.ResourceNotFoundException:
        return []

    events = []
    level_priority = {
        LogLevel.DEBUG: 0,
        LogLevel.INFO: 1,
        LogLevel.WARN: 2,
        LogLevel.ERROR: 3,
    }
    min_priority = level_priority[level]

    for event in response.get("events", []):
        message = event.get("message", "")

        # Try to parse JSON structured logs
        parsed = parse_log_message(message)
        if parsed:
            event_level = LogLevel(parsed.get("level", "info"))
            if level_priority.get(event_level, 0) >= min_priority:
                events.append({
                    "timestamp": datetime.fromtimestamp(event["timestamp"] / 1000, timezone.utc),
                    "level": parsed.get("level", "info"),
                    "service": parsed.get("service", "unknown"),
                    "message": parsed.get("message", message),
                    "metadata": {k: v for k, v in parsed.items() if k not in ("level", "service", "message")},
                    "raw": message,
                })
        elif "START RequestId" not in message and "END RequestId" not in message and "REPORT RequestId" not in message:
            # Non-JSON log line (skip Lambda runtime messages)
            events.append({
                "timestamp": datetime.fromtimestamp(event["timestamp"] / 1000, timezone.utc),
                "level": "info",
                "service": "runtime",
                "message": message.strip(),
                "metadata": {},
                "raw": message,
            })

    return events


def parse_log_message(message: str) -> dict | None:
    """Try to parse a JSON log message."""
    # Skip Lambda runtime messages
    if message.startswith("START ") or message.startswith("END ") or message.startswith("REPORT "):
        return None

    # Try to find JSON in the message
    try:
        # Handle Winston format: might have timestamp prefix
        if "\t" in message:
            parts = message.split("\t")
            for part in parts:
                if part.startswith("{"):
                    return json.loads(part)

        if message.strip().startswith("{"):
            return json.loads(message.strip())
    except json.JSONDecodeError:
        pass

    return None


# ============================================================================
# Display Helpers
# ============================================================================

def print_lambda_result(result: LambdaResult, function_name: str) -> None:
    """Print a Lambda invocation result in a nice format."""
    status = "[green]SUCCESS[/green]" if result.success else "[red]FAILED[/red]"
    console.print(Panel(
        f"[bold]{function_name}[/bold]\n"
        f"Status: {status}\n"
        f"HTTP Status: {result.status_code}",
        title="Lambda Invocation",
        box=box.ROUNDED,
    ))

    if result.payload:
        console.print("[bold]Response:[/bold]")
        console.print_json(data=result.payload)

    if result.function_error:
        console.print(f"[red]Error: {result.function_error}[/red]")


def print_queue_stats(stats: list[QueueStats]) -> None:
    """Print queue statistics in a table."""
    table = Table(title="SQS Queue Statistics", box=box.ROUNDED)
    table.add_column("Queue", style="cyan")
    table.add_column("Available", justify="right")
    table.add_column("In Flight", justify="right")
    table.add_column("Delayed", justify="right")
    table.add_column("Total", justify="right", style="bold")

    for s in stats:
        table.add_row(
            s.name,
            str(s.messages_available),
            str(s.messages_in_flight),
            str(s.messages_delayed),
            str(s.total),
        )

    console.print(table)


def print_logs(logs: list[dict], title: str = "CloudWatch Logs") -> None:
    """Print logs in a formatted table."""
    if not logs:
        console.print(f"[dim]No logs found for {title}[/dim]")
        return

    table = Table(title=title, box=box.ROUNDED, show_lines=True)
    table.add_column("Time", style="dim", width=12)
    table.add_column("Level", width=6)
    table.add_column("Service", style="cyan", width=15)
    table.add_column("Message")

    level_styles = {
        "debug": "dim",
        "info": "white",
        "warn": "yellow",
        "error": "red bold",
    }

    for log in logs:
        level = log["level"]
        style = level_styles.get(level, "white")
        time_str = log["timestamp"].strftime("%H:%M:%S")

        message = log["message"]
        if log["metadata"]:
            meta_str = " ".join(f"{k}={v}" for k, v in log["metadata"].items())
            message = f"{message} [dim]({meta_str})[/dim]"

        table.add_row(
            time_str,
            f"[{style}]{level.upper()}[/{style}]",
            log["service"],
            message,
        )

    console.print(table)


def print_db_counts(cursor, title: str = "Database Counts") -> None:
    """Print row counts for main tables."""
    tables = [
        "user_profiles",
        "user_stats",
        "user_keywords",
        "profiles_to_score",
        "profile_scores",
        "xapi_usage_search",
    ]

    table = Table(title=title, box=box.ROUNDED)
    table.add_column("Table", style="cyan")
    table.add_column("Count", justify="right")

    for tbl in tables:
        cursor.execute(f"SELECT COUNT(*) FROM {tbl}")
        count = cursor.fetchone()[0]
        table.add_row(tbl, str(count))

    console.print(table)
