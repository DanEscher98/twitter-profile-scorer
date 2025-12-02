"""
Simple CloudWatch Dashboard Component - EC2 and RDS Only

This module provides a CloudWatch Dashboard showing metrics for:
- EC2 Airflow: CPU, network, status checks
- RDS PostgreSQL: Connections, CPU, storage, IOPS

Dashboard Layout:
-----------------
Row 1: EC2 Airflow (CPU, Network, Status)
Row 2: Database Health (Connections, CPU, Storage)
Row 3: Database I/O (IOPS, Latency)
"""

import json
import pulumi
import pulumi_aws as aws


class SimpleDashboard(pulumi.ComponentResource):
    """CloudWatch Dashboard for EC2 and RDS monitoring."""

    def __init__(
        self,
        name: str,
        db_instance_id: pulumi.Input[str],
        ec2_instance_id: pulumi.Input[str] = None,
        region: str = "us-east-2",
        opts: pulumi.ResourceOptions = None,
    ):
        """
        Create a simplified CloudWatch Dashboard.

        Args:
            name: Dashboard name
            db_instance_id: RDS instance identifier
            ec2_instance_id: EC2 Airflow instance ID (optional)
            region: AWS region
        """
        super().__init__("custom:cloudwatch:SimpleDashboard", name, None, opts)

        # Combine all inputs and build dashboard
        dashboard_body = pulumi.Output.all(
            db_id=db_instance_id,
            ec2_id=ec2_instance_id or "",
        ).apply(
            lambda args: self._build_dashboard(
                db_id=args["db_id"],
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
        db_id: str,
        ec2_id: str,
        region: str,
    ) -> str:
        """Build complete dashboard JSON."""
        widgets = []
        y = 0

        # Colors
        COLORS = {
            "primary": "#1f77b4",     # Blue
            "secondary": "#ff7f0e",   # Orange
            "success": "#2ca02c",     # Green
            "danger": "#d62728",      # Red
            "warning": "#ffbb78",     # Light orange
            "info": "#17becf",        # Cyan
            "purple": "#9467bd",      # Purple
        }

        # =================================================================
        # ROW 1: EC2 Airflow Metrics (height=6)
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
                         {"label": "CPU %", "color": COLORS["secondary"]}],
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
                         {"label": "In", "color": COLORS["primary"]}],
                        ["AWS/EC2", "NetworkOut", "InstanceId", ec2_id,
                         {"label": "Out", "color": COLORS["secondary"]}],
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
                         {"label": "Failed Checks", "color": COLORS["danger"]}],
                        ["AWS/EC2", "StatusCheckFailed_Instance", "InstanceId", ec2_id,
                         {"label": "Instance Failed", "color": COLORS["secondary"]}],
                        ["AWS/EC2", "StatusCheckFailed_System", "InstanceId", ec2_id,
                         {"label": "System Failed", "color": COLORS["purple"]}],
                    ],
                    "view": "timeSeries",
                    "period": 300,
                    "stat": "Maximum",
                },
            })
            y += 6
        else:
            # Placeholder if no EC2
            widgets.append({
                "type": "text",
                "x": 0, "y": y, "width": 24, "height": 2,
                "properties": {
                    "markdown": "### EC2 Airflow: Not configured (set AIRFLOW_SSH_KEY_NAME to enable)",
                },
            })
            y += 2

        # =================================================================
        # ROW 2: Database Health (height=6)
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
                     {"color": COLORS["info"]}],
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
                     {"color": COLORS["info"]}],
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
                     {"color": COLORS["info"]}],
                ],
                "view": "timeSeries",
                "period": 300,
                "stat": "Average",
                "yAxis": {"left": {"label": "Bytes"}},
            },
        })
        y += 6

        # =================================================================
        # ROW 3: Database I/O (height=6)
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
                     {"label": "Read IOPS", "color": COLORS["success"]}],
                    ["AWS/RDS", "WriteIOPS", "DBInstanceIdentifier", db_id,
                     {"label": "Write IOPS", "color": COLORS["danger"]}],
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
                     {"label": "Read", "color": COLORS["success"]}],
                    ["AWS/RDS", "WriteLatency", "DBInstanceIdentifier", db_id,
                     {"label": "Write", "color": COLORS["danger"]}],
                ],
                "view": "timeSeries",
                "period": 60,
                "stat": "Average",
            },
        })

        return json.dumps({"widgets": widgets})
