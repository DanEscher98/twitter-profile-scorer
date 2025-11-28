"""
Billing & Cost Management Components

This module provides cost visibility and alerting for the profile-scorer project:

1. ProjectBudget: AWS Budget with cost/usage tracking and alerts
2. CostAnomalyMonitor: ML-based anomaly detection for unexpected spending

Cost Visibility Strategy:
-------------------------
AWS provides several complementary cost monitoring tools:

- CloudWatch Billing Metrics: Account-wide only, 4-hour delay
- Cost Explorer: Tag/service filtering, daily granularity, manual review
- AWS Budgets: Threshold alerts, forecasting, automated actions
- Cost Anomaly Detection: ML-based unusual spending alerts

For project-level tracking, we use:
1. AWS Budget filtered by Project tag → threshold alerts
2. Cost Anomaly Detection → unexpected spike alerts
3. Cost Explorer → manual deep-dive analysis (console)

Tag Activation:
---------------
Cost allocation tags must be activated in AWS Billing console before
they appear in Cost Explorer and Budgets. The Project tag will take
up to 24 hours to become available after first use.

To activate: AWS Console → Billing → Cost allocation tags → Activate
"""

import pulumi
import pulumi_aws as aws


class ProjectBudget(pulumi.ComponentResource):
    """AWS Budget for project cost tracking with alerts."""

    def __init__(
        self,
        name: str,
        monthly_limit_usd: float,
        alert_thresholds: list[int] = None,  # Percentages, e.g., [50, 80, 100]
        notification_emails: list[str] = None,
        project_tag: str = "profile-scorer-saas",
        opts: pulumi.ResourceOptions = None,
    ):
        """
        Create an AWS Budget for the project.

        Args:
            name: Budget name
            monthly_limit_usd: Monthly spending limit in USD
            alert_thresholds: List of percentages to trigger alerts (default: [50, 80, 100])
            notification_emails: Email addresses for alerts
            project_tag: Value of the Project tag to filter by
        """
        super().__init__("custom:billing:ProjectBudget", name, None, opts)

        if alert_thresholds is None:
            alert_thresholds = [50, 80, 100]

        # Build cost filter for the Project tag
        # Note: Tag must be activated as cost allocation tag to work
        # Format: list of BudgetCostFilterArgs with name and values
        cost_filters = [
            aws.budgets.BudgetCostFilterArgs(
                name="TagKeyValue",
                values=[f"user:Project${project_tag}"],
            )
        ]

        # Create notifications only if email addresses are provided
        # AWS requires at least one subscriber per notification
        notifications = []

        if notification_emails:
            for threshold in alert_thresholds:
                notifications.append(
                    aws.budgets.BudgetNotificationArgs(
                        comparison_operator="GREATER_THAN",
                        threshold=threshold,
                        threshold_type="PERCENTAGE",
                        notification_type="ACTUAL",
                        subscriber_email_addresses=notification_emails,
                    )
                )

        # Create the budget
        self.budget = aws.budgets.Budget(
            f"{name}-budget",
            name=f"{name}-monthly",
            budget_type="COST",
            limit_amount=str(monthly_limit_usd),
            limit_unit="USD",
            time_unit="MONTHLY",
            # Note: cost_filters with tags requires tag activation
            # Until activated, budget tracks all costs
            cost_filters=cost_filters,
            notifications=notifications if notifications else None,
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.register_outputs({
            "budget_name": self.budget.name,
            "budget_id": self.budget.id,
        })


class CostAnomalyMonitor(pulumi.ComponentResource):
    """Cost Anomaly Detection for unexpected spending alerts."""

    def __init__(
        self,
        name: str,
        notification_emails: list[str] = None,
        threshold_usd: float = 10.0,  # Alert when anomaly exceeds this amount
        opts: pulumi.ResourceOptions = None,
    ):
        """
        Create Cost Anomaly Detection monitor.

        Args:
            name: Monitor name
            notification_emails: Email addresses for alerts
            threshold_usd: Dollar threshold for anomaly alerts
        """
        super().__init__("custom:billing:CostAnomalyMonitor", name, None, opts)

        # Create the anomaly monitor
        # Monitor type: DIMENSIONAL tracks by service/linked account
        self.monitor = aws.costexplorer.AnomalyMonitor(
            f"{name}-monitor",
            name=f"{name}-anomaly-monitor",
            monitor_type="DIMENSIONAL",
            monitor_dimension="SERVICE",  # Track anomalies per service
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create SNS topic for notifications if emails provided
        if notification_emails:
            self.sns_topic = aws.sns.Topic(
                f"{name}-anomaly-alerts",
                name=f"{name}-cost-anomaly-alerts",
                opts=pulumi.ResourceOptions(parent=self),
            )

            # Subscribe emails to SNS topic
            for i, email in enumerate(notification_emails):
                aws.sns.TopicSubscription(
                    f"{name}-email-sub-{i}",
                    topic=self.sns_topic.arn,
                    protocol="email",
                    endpoint=email,
                    opts=pulumi.ResourceOptions(parent=self),
                )

            # Create anomaly subscription (connects monitor to SNS)
            self.subscription = aws.costexplorer.AnomalySubscription(
                f"{name}-subscription",
                name=f"{name}-anomaly-subscription",
                frequency="DAILY",  # DAILY or IMMEDIATE
                monitor_arn_lists=[self.monitor.arn],
                subscribers=[
                    aws.costexplorer.AnomalySubscriptionSubscriberArgs(
                        type="SNS",
                        address=self.sns_topic.arn,
                    )
                ],
                # Only alert if anomaly exceeds threshold
                threshold_expression={
                    "dimension": {
                        "key": "ANOMALY_TOTAL_IMPACT_ABSOLUTE",
                        "match_options": ["GREATER_THAN_OR_EQUAL"],
                        "values": [str(threshold_usd)],
                    }
                },
                opts=pulumi.ResourceOptions(parent=self),
            )

        self.register_outputs({
            "monitor_arn": self.monitor.arn,
        })


class ServiceCostBreakdown(pulumi.ComponentResource):
    """
    CloudWatch Dashboard widget showing cost breakdown by service.

    Note: This uses a custom widget with Lambda to fetch Cost Explorer data,
    since CloudWatch doesn't have native service-level cost metrics.

    Alternative: Use AWS Cost Explorer console directly for service breakdown.
    """

    def __init__(
        self,
        name: str,
        services: list[str] = None,
        opts: pulumi.ResourceOptions = None,
    ):
        """
        This is a placeholder for future implementation.

        For now, use Cost Explorer console for service breakdown:
        https://console.aws.amazon.com/cost-management/home#/cost-explorer

        Filter by:
        - Tag: Project = profile-scorer-saas
        - Group by: Service
        """
        super().__init__("custom:billing:ServiceCostBreakdown", name, None, opts)

        if services is None:
            services = [
                "AWS Lambda",
                "Amazon Relational Database Service",
                "EC2 - Other",  # NAT Gateway
                "Amazon Simple Queue Service",
                "Amazon Virtual Private Cloud",
            ]

        # Store for reference
        self.services = services

        self.register_outputs({
            "tracked_services": services,
            "cost_explorer_url": "https://console.aws.amazon.com/cost-management/home#/cost-explorer",
        })
