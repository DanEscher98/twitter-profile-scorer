"""
CloudWatch Dashboard Component - Full System Overview

This module provides a CloudWatch Dashboard showing metrics for ALL resources
in the Profile Scorer pipeline:

- Lambda Functions: Invocations, errors, duration, concurrency
- RDS PostgreSQL: Connections, CPU, storage, IOPS
- SQS Queues: Messages visible, in-flight, age, DLQ depth
- NAT Gateway: Bytes transferred, packets, errors
- EC2 Airflow: CPU, network, disk (when configured)

Dashboard Layout:
-----------------
Row 1: Pipeline Health (invocations, errors, queue depth)
Row 2: Lambda Performance (duration p95, concurrent executions)
Row 3: Database Health (connections, CPU, storage)
Row 4: Database I/O (read/write IOPS, latency)
Row 5: Queue Metrics (messages, age, DLQ)
Row 6: Network (NAT Gateway traffic)
Row 7: EC2 Airflow (CPU, network, disk) - optional
"""

import json
import pulumi
import pulumi_aws as aws


class SystemDashboard(pulumi.ComponentResource):
    """CloudWatch Dashboard for full system monitoring."""

    def __init__(
        self,
        name: str,
        lambda_names: dict[str, pulumi.Input[str]],
        db_instance_id: pulumi.Input[str],
        queue_name: pulumi.Input[str],
        dlq_name: pulumi.Input[str],
        nat_gateway_id: pulumi.Input[str] = None,
        ec2_instance_id: pulumi.Input[str] = None,
        region: str = "us-east-2",
        opts: pulumi.ResourceOptions = None,
    ):
        """
        Create a comprehensive CloudWatch Dashboard.

        Args:
            name: Dashboard name
            lambda_names: Dict of display_name -> function_name
            db_instance_id: RDS instance identifier
            queue_name: Main SQS queue name
            dlq_name: Dead letter queue name
            nat_gateway_id: NAT Gateway ID (optional)
            ec2_instance_id: EC2 Airflow instance ID (optional)
            region: AWS region
        """
        super().__init__("custom:cloudwatch:SystemDashboard", name, None, opts)

        # Combine all inputs and build dashboard
        dashboard_body = pulumi.Output.all(
            lambda_names=lambda_names,
            db_id=db_instance_id,
            queue=queue_name,
            dlq=dlq_name,
            nat_id=nat_gateway_id or "",
            ec2_id=ec2_instance_id or "",
        ).apply(
            lambda args: self._build_dashboard(
                lambda_names=args["lambda_names"],
                db_id=args["db_id"],
                queue_name=args["queue"],
                dlq_name=args["dlq"],
                nat_id=args["nat_id"],
                ec2_id=args["ec2_id"],
                region=region,
            )
        )

        self.dashboard = aws.cloudwatch.Dashboard(
            f"{name}-dashboard",
            dashboard_name=name,
            dashboard_body=dashboard_body,
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.register_outputs({
            "dashboard_name": self.dashboard.dashboard_name,
            "dashboard_arn": self.dashboard.dashboard_arn,
        })

    def _build_dashboard(
        self,
        lambda_names: dict[str, str],
        db_id: str,
        queue_name: str,
        dlq_name: str,
        nat_id: str,
        ec2_id: str,
        region: str,
    ) -> str:
        """Build complete dashboard JSON."""
        widgets = []
        y = 0

        # Colors
        COLORS = {
            "orchestrator": "#2ca02c",      # Green
            "keyword_engine": "#1f77b4",    # Blue
            "query_twitter": "#ff7f0e",     # Orange
            "llm_scorer": "#9467bd",        # Purple
            "error": "#d62728",             # Red
            "warning": "#ffbb78",           # Light orange
            "database": "#17becf",          # Cyan
            "queue": "#bcbd22",             # Yellow-green
        }

        # =================================================================
        # ROW 1: Pipeline Health Overview (height=6)
        # =================================================================
        # Total Invocations (stacked by function)
        widgets.append({
            "type": "metric",
            "x": 0, "y": y, "width": 8, "height": 6,
            "properties": {
                "title": "Lambda Invocations",
                "region": region,
                "metrics": [
                    ["AWS/Lambda", "Invocations", "FunctionName", fn,
                     {"label": name.replace("_", "-"), "color": COLORS.get(name, "#333")}]
                    for name, fn in lambda_names.items()
                ],
                "view": "timeSeries",
                "stacked": True,
                "period": 300,
                "stat": "Sum",
            },
        })

        # Total Errors
        widgets.append({
            "type": "metric",
            "x": 8, "y": y, "width": 8, "height": 6,
            "properties": {
                "title": "Lambda Errors",
                "region": region,
                "metrics": [
                    ["AWS/Lambda", "Errors", "FunctionName", fn,
                     {"label": name.replace("_", "-"), "color": COLORS["error"]}]
                    for name, fn in lambda_names.items()
                ],
                "view": "timeSeries",
                "stacked": True,
                "period": 300,
                "stat": "Sum",
            },
        })

        # SQS Queue Depth
        widgets.append({
            "type": "metric",
            "x": 16, "y": y, "width": 8, "height": 6,
            "properties": {
                "title": "SQS Queue Depth",
                "region": region,
                "metrics": [
                    ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", queue_name,
                     {"label": "Visible", "color": COLORS["queue"]}],
                    ["AWS/SQS", "ApproximateNumberOfMessagesNotVisible", "QueueName", queue_name,
                     {"label": "In Flight", "color": COLORS["warning"]}],
                    ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", dlq_name,
                     {"label": "DLQ", "color": COLORS["error"]}],
                ],
                "view": "timeSeries",
                "stacked": False,
                "period": 60,
                "stat": "Average",
            },
        })
        y += 6

        # =================================================================
        # ROW 2: Lambda Performance (height=6)
        # =================================================================
        # Duration p95
        widgets.append({
            "type": "metric",
            "x": 0, "y": y, "width": 8, "height": 6,
            "properties": {
                "title": "Lambda Duration (p95)",
                "region": region,
                "metrics": [
                    ["AWS/Lambda", "Duration", "FunctionName", fn,
                     {"label": name.replace("_", "-"), "color": COLORS.get(name, "#333")}]
                    for name, fn in lambda_names.items()
                ],
                "view": "timeSeries",
                "stacked": False,
                "period": 300,
                "stat": "p95",
                "yAxis": {"left": {"label": "ms"}},
            },
        })

        # Concurrent Executions
        widgets.append({
            "type": "metric",
            "x": 8, "y": y, "width": 8, "height": 6,
            "properties": {
                "title": "Concurrent Executions",
                "region": region,
                "metrics": [
                    ["AWS/Lambda", "ConcurrentExecutions", "FunctionName", fn,
                     {"label": name.replace("_", "-"), "color": COLORS.get(name, "#333")}]
                    for name, fn in lambda_names.items()
                ],
                "view": "timeSeries",
                "stacked": True,
                "period": 60,
                "stat": "Maximum",
            },
        })

        # Throttles
        widgets.append({
            "type": "metric",
            "x": 16, "y": y, "width": 8, "height": 6,
            "properties": {
                "title": "Lambda Throttles",
                "region": region,
                "metrics": [
                    ["AWS/Lambda", "Throttles", "FunctionName", fn,
                     {"label": name.replace("_", "-"), "color": COLORS["warning"]}]
                    for name, fn in lambda_names.items()
                ],
                "view": "timeSeries",
                "stacked": True,
                "period": 300,
                "stat": "Sum",
            },
        })
        y += 6

        # =================================================================
        # ROW 3: Database Health (height=6)
        # =================================================================
        # Database Connections
        widgets.append({
            "type": "metric",
            "x": 0, "y": y, "width": 8, "height": 6,
            "properties": {
                "title": "RDS Connections",
                "region": region,
                "metrics": [
                    ["AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", db_id,
                     {"color": COLORS["database"]}],
                ],
                "view": "timeSeries",
                "period": 60,
                "stat": "Average",
            },
        })

        # CPU Utilization
        widgets.append({
            "type": "metric",
            "x": 8, "y": y, "width": 8, "height": 6,
            "properties": {
                "title": "RDS CPU Utilization",
                "region": region,
                "metrics": [
                    ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", db_id,
                     {"color": COLORS["database"]}],
                ],
                "view": "timeSeries",
                "period": 60,
                "stat": "Average",
                "yAxis": {"left": {"min": 0, "max": 100, "label": "%"}},
            },
        })

        # Free Storage Space
        widgets.append({
            "type": "metric",
            "x": 16, "y": y, "width": 8, "height": 6,
            "properties": {
                "title": "RDS Free Storage (GB)",
                "region": region,
                "metrics": [
                    ["AWS/RDS", "FreeStorageSpace", "DBInstanceIdentifier", db_id,
                     {"color": COLORS["database"]}],
                ],
                "view": "timeSeries",
                "period": 300,
                "stat": "Average",
                "yAxis": {"left": {"label": "Bytes"}},
            },
        })
        y += 6

        # =================================================================
        # ROW 4: Database I/O (height=6)
        # =================================================================
        # Read/Write IOPS
        widgets.append({
            "type": "metric",
            "x": 0, "y": y, "width": 12, "height": 6,
            "properties": {
                "title": "RDS IOPS",
                "region": region,
                "metrics": [
                    ["AWS/RDS", "ReadIOPS", "DBInstanceIdentifier", db_id,
                     {"label": "Read IOPS", "color": "#2ca02c"}],
                    ["AWS/RDS", "WriteIOPS", "DBInstanceIdentifier", db_id,
                     {"label": "Write IOPS", "color": "#d62728"}],
                ],
                "view": "timeSeries",
                "period": 60,
                "stat": "Average",
            },
        })

        # Read/Write Latency
        widgets.append({
            "type": "metric",
            "x": 12, "y": y, "width": 12, "height": 6,
            "properties": {
                "title": "RDS Latency (ms)",
                "region": region,
                "metrics": [
                    ["AWS/RDS", "ReadLatency", "DBInstanceIdentifier", db_id,
                     {"label": "Read", "color": "#2ca02c"}],
                    ["AWS/RDS", "WriteLatency", "DBInstanceIdentifier", db_id,
                     {"label": "Write", "color": "#d62728"}],
                ],
                "view": "timeSeries",
                "period": 60,
                "stat": "Average",
            },
        })
        y += 6

        # =================================================================
        # ROW 5: Queue Metrics (height=6)
        # =================================================================
        # Message Age
        widgets.append({
            "type": "metric",
            "x": 0, "y": y, "width": 8, "height": 6,
            "properties": {
                "title": "SQS Message Age (oldest)",
                "region": region,
                "metrics": [
                    ["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", queue_name,
                     {"label": "Keywords Queue", "color": COLORS["queue"]}],
                    ["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", dlq_name,
                     {"label": "DLQ", "color": COLORS["error"]}],
                ],
                "view": "timeSeries",
                "period": 60,
                "stat": "Maximum",
                "yAxis": {"left": {"label": "seconds"}},
            },
        })

        # Messages Sent/Received
        widgets.append({
            "type": "metric",
            "x": 8, "y": y, "width": 8, "height": 6,
            "properties": {
                "title": "SQS Messages Sent/Received",
                "region": region,
                "metrics": [
                    ["AWS/SQS", "NumberOfMessagesSent", "QueueName", queue_name,
                     {"label": "Sent", "color": "#2ca02c"}],
                    ["AWS/SQS", "NumberOfMessagesReceived", "QueueName", queue_name,
                     {"label": "Received", "color": "#1f77b4"}],
                    ["AWS/SQS", "NumberOfMessagesDeleted", "QueueName", queue_name,
                     {"label": "Deleted", "color": "#9467bd"}],
                ],
                "view": "timeSeries",
                "period": 300,
                "stat": "Sum",
            },
        })

        # Empty Receives (indicates polling efficiency)
        widgets.append({
            "type": "metric",
            "x": 16, "y": y, "width": 8, "height": 6,
            "properties": {
                "title": "SQS Empty Receives",
                "region": region,
                "metrics": [
                    ["AWS/SQS", "NumberOfEmptyReceives", "QueueName", queue_name,
                     {"label": "Empty Polls", "color": COLORS["warning"]}],
                ],
                "view": "timeSeries",
                "period": 300,
                "stat": "Sum",
            },
        })
        y += 6

        # =================================================================
        # ROW 6: Network / NAT Gateway (height=6)
        # =================================================================
        if nat_id:
            # NAT Gateway Bytes
            widgets.append({
                "type": "metric",
                "x": 0, "y": y, "width": 12, "height": 6,
                "properties": {
                    "title": "NAT Gateway Traffic",
                    "region": region,
                    "metrics": [
                        ["AWS/NATGateway", "BytesOutToDestination", "NatGatewayId", nat_id,
                         {"label": "Bytes Out", "color": "#ff7f0e"}],
                        ["AWS/NATGateway", "BytesInFromDestination", "NatGatewayId", nat_id,
                         {"label": "Bytes In", "color": "#1f77b4"}],
                    ],
                    "view": "timeSeries",
                    "period": 300,
                    "stat": "Sum",
                    "yAxis": {"left": {"label": "Bytes"}},
                },
            })

            # NAT Gateway Connections
            widgets.append({
                "type": "metric",
                "x": 12, "y": y, "width": 12, "height": 6,
                "properties": {
                    "title": "NAT Gateway Connections",
                    "region": region,
                    "metrics": [
                        ["AWS/NATGateway", "ActiveConnectionCount", "NatGatewayId", nat_id,
                         {"label": "Active", "color": "#2ca02c"}],
                        ["AWS/NATGateway", "ConnectionAttemptCount", "NatGatewayId", nat_id,
                         {"label": "Attempts", "color": "#1f77b4"}],
                        ["AWS/NATGateway", "ConnectionEstablishedCount", "NatGatewayId", nat_id,
                         {"label": "Established", "color": "#9467bd"}],
                    ],
                    "view": "timeSeries",
                    "period": 300,
                    "stat": "Sum",
                },
            })
        else:
            # Placeholder text widget if no NAT Gateway
            widgets.append({
                "type": "text",
                "x": 0, "y": y, "width": 24, "height": 2,
                "properties": {
                    "markdown": "### Network metrics: NAT Gateway ID not provided",
                },
            })
        y += 6

        # =================================================================
        # ROW 7: EC2 Airflow Metrics (height=6) - Optional
        # =================================================================
        if ec2_id:
            # EC2 CPU Utilization
            widgets.append({
                "type": "metric",
                "x": 0, "y": y, "width": 8, "height": 6,
                "properties": {
                    "title": "Airflow EC2 CPU",
                    "region": region,
                    "metrics": [
                        ["AWS/EC2", "CPUUtilization", "InstanceId", ec2_id,
                         {"label": "CPU %", "color": "#ff7f0e"}],
                    ],
                    "view": "timeSeries",
                    "period": 60,
                    "stat": "Average",
                    "yAxis": {"left": {"min": 0, "max": 100, "label": "%"}},
                },
            })

            # EC2 Network
            widgets.append({
                "type": "metric",
                "x": 8, "y": y, "width": 8, "height": 6,
                "properties": {
                    "title": "Airflow EC2 Network",
                    "region": region,
                    "metrics": [
                        ["AWS/EC2", "NetworkIn", "InstanceId", ec2_id,
                         {"label": "In", "color": "#1f77b4"}],
                        ["AWS/EC2", "NetworkOut", "InstanceId", ec2_id,
                         {"label": "Out", "color": "#ff7f0e"}],
                    ],
                    "view": "timeSeries",
                    "period": 300,
                    "stat": "Sum",
                    "yAxis": {"left": {"label": "Bytes"}},
                },
            })

            # EC2 Status Checks
            widgets.append({
                "type": "metric",
                "x": 16, "y": y, "width": 8, "height": 6,
                "properties": {
                    "title": "Airflow EC2 Status",
                    "region": region,
                    "metrics": [
                        ["AWS/EC2", "StatusCheckFailed", "InstanceId", ec2_id,
                         {"label": "Failed Checks", "color": "#d62728"}],
                        ["AWS/EC2", "StatusCheckFailed_Instance", "InstanceId", ec2_id,
                         {"label": "Instance Failed", "color": "#ff7f0e"}],
                        ["AWS/EC2", "StatusCheckFailed_System", "InstanceId", ec2_id,
                         {"label": "System Failed", "color": "#9467bd"}],
                    ],
                    "view": "timeSeries",
                    "period": 300,
                    "stat": "Maximum",
                },
            })

        return json.dumps({"widgets": widgets})
