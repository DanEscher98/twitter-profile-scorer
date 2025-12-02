"""
Profile Scorer Infrastructure - Main Pulumi Program

This file orchestrates the deployment of AWS infrastructure for the Twitter
profile scoring pipeline. The pipeline now runs on Apache Airflow (EC2).

Architecture Overview:
----------------------
1. VPC with public subnets for EC2 and RDS
2. RDS PostgreSQL for persistent storage
3. EC2 instance running Apache Airflow 3.x for pipeline orchestration

Data Flow (Airflow):
--------------------
Airflow DAGs → External APIs (Twitter, LLMs) → PostgreSQL

Note: Legacy Lambda infrastructure has been removed. The VPC still has
private/isolated subnets for potential future use, but NAT Gateway has been
removed to save costs.
"""

import pulumi
import pulumi_aws as aws

from components import (
    Database,
    Ec2Airflow,
    ProjectBudget,
    SimpleDashboard,
    Vpc,
)

# =============================================================================
# Configuration (Secrets from environment variables)
# =============================================================================
# Secrets are loaded from environment variables for security:
# - Never committed to git (infra/.env is gitignored)
# - Each developer/deployment uses their own credentials
# - Copy infra/.env.example to infra/.env and fill in your values
#
# For local dev: source infra/.env or use dotenv
# For CI/CD: Set environment variables in your pipeline secrets

import os
from dotenv import load_dotenv

# Load .env file if present (for local development)
load_dotenv()

def require_env(name: str) -> str:
    """Get required environment variable or raise helpful error."""
    value = os.environ.get(name)
    if not value:
        raise ValueError(
            f"Missing required environment variable: {name}\n"
            f"Copy infra/.env.example to infra/.env and set your values."
        )
    return value

config = pulumi.Config()

# Secrets from environment variables (not stored in Pulumi config)
db_password = pulumi.Output.secret(require_env("DB_PASSWORD"))
twitterx_apikey = pulumi.Output.secret(require_env("TWITTERX_APIKEY"))
anthropic_apikey = pulumi.Output.secret(require_env("ANTHROPIC_API_KEY"))

# =============================================================================
# VPC - Network Foundation
# =============================================================================
# The VPC still has the three-tier subnet structure, but NAT Gateway has been
# removed since Lambda functions are no longer used.
#
# Subnet tiers (for reference):
# - Public:   Direct internet access via Internet Gateway (EC2, RDS dev)
# - Private:  No internet (NAT Gateway removed) - unused for now
# - Isolated: No internet - unused for now
#
# Cost savings: No NAT Gateway (~$32/month)

vpc = Vpc("profile-scorer")

# =============================================================================
# Database - PostgreSQL on RDS
# =============================================================================
# RDS PostgreSQL stores all pipeline data:
# - user_profiles: Collected Twitter profiles with HAS scores
# - user_stats: Raw numeric fields for ML training
# - user_keywords: Many-to-many linking profiles to search keywords
# - api_search_usage: API call tracking and pagination state
# - profiles_to_score: Queue of profiles pending LLM evaluation
# - profile_scores: LLM-generated scores and reasoning
# - keyword_stats: Keyword pool with semantic tags and quality metrics
# - keyword_status: Per-platform keyword pagination state
#
# Dev Note: Using public subnets for local psql access. In production,
# move to private subnets and remove public accessibility.

db = Database(
    "profile-scorer",
    vpc_id=vpc.vpc.id,
    subnet_ids=vpc.public_subnet_ids,  # Public for dev access
    password=db_password,
    allowed_security_group_ids=[],  # Ingress rules added below
)

# =============================================================================
# Resource Group - Consolidated View in AWS Console
# =============================================================================
# Creates an AWS Resource Group that shows all profile-scorer resources
# in one place. Access via: AWS Console → Resource Groups → profile-scorer
#
# This is the AWS equivalent of GCloud projects - a single view of all
# related services without affecting billing or permissions.

resource_group = aws.resourcegroups.Group(
    "profile-scorer-resources",
    name="profile-scorer-saas",
    description="All resources for the Profile Scorer Twitter analysis pipeline",
    resource_query={
        "query": """{
            "ResourceTypeFilters": ["AWS::AllSupported"],
            "TagFilters": [
                {
                    "Key": "Project",
                    "Values": ["profile-scorer-saas"]
                }
            ]
        }""",
        "type": "TAG_FILTERS_1_0",
    },
)

# =============================================================================
# EC2 Airflow Instance - Pipeline Orchestration
# =============================================================================
# This EC2 instance runs Apache Airflow 3.x for the profile scoring pipeline.
#
# DAGs:
# - profile_search: Multi-platform profile search (every 15 min)
# - llm_scoring: LLM evaluation of high-HAS profiles (every 15 min)
# - keyword_stats: Daily keyword statistics update (2 AM UTC)
#
# SSH key: Must exist in AWS EC2 console. Create via:
#   aws ec2 create-key-pair --key-name airflow --query 'KeyMaterial' --output text > ~/.ssh/airflow.pem
#   chmod 600 ~/.ssh/airflow.pem

ssh_key_name = os.environ.get("AIRFLOW_SSH_KEY_NAME")

airflow_instance = None
if ssh_key_name:
    airflow_instance = Ec2Airflow(
        "airflow",
        vpc_id=vpc.vpc.id,
        subnet_id=vpc.public_subnet_1.id,  # Public subnet for direct access
        db_security_group_id=db.security_group.id,
        ssh_key_name=ssh_key_name,
        database_url=db.connection_string,
        twitterx_apikey=twitterx_apikey,
        anthropic_api_key=anthropic_apikey,
        gemini_api_key=pulumi.Output.secret(require_env("GEMINI_API_KEY")),
        groq_api_key=pulumi.Output.secret(require_env("GROQ_API_KEY")),
        instance_type="t3.small",  # 2 vCPU, 2GB RAM
    )

# =============================================================================
# Database Security Group Rules
# =============================================================================
# Note: EC2 Airflow → DB rule is created by the Ec2Airflow component

# DEV ONLY: Allow external access for local psql/Drizzle Studio
# WARNING: Remove or restrict this in production!
aws.ec2.SecurityGroupRule(
    "db-allow-external-dev",
    type="ingress",
    from_port=5432,
    to_port=5432,
    protocol="tcp",
    cidr_blocks=["0.0.0.0/0"],  # TODO: Restrict to your IP in production
    security_group_id=db.security_group.id,
)

# =============================================================================
# CloudWatch Dashboard - System Overview (Simplified)
# =============================================================================
# Dashboard showing metrics for EC2 and RDS only:
# - EC2 Airflow: CPU, network, status checks
# - RDS: Connections, CPU, storage, IOPS
#
# Access via: AWS Console → CloudWatch → Dashboards → profile-scorer

dashboard = SimpleDashboard(
    "profile-scorer",
    db_instance_id=db.instance.identifier,
    ec2_instance_id=airflow_instance.instance.id if airflow_instance else None,
    region="us-east-2",
)

# =============================================================================
# Cost Management - Budget & Anomaly Detection
# =============================================================================
# Track project costs and get alerts for unusual spending:
# - AWS Budget: Monthly limit with threshold alerts (50%, 80%, 100%)
# - Cost Anomaly Detection: ML-based alerts for unexpected spikes
#
# Note: Cost allocation tag (Project=profile-scorer-saas) must be activated
# in AWS Billing console for tag-based filtering to work.
# Go to: AWS Console → Billing → Cost allocation tags → Activate "Project"

# Monthly budget with alerts at 50%, 80%, and 100% of limit
# Note: Add notification_emails=["your@email.com"] to receive alerts
budget = ProjectBudget(
    "profile-scorer",
    monthly_limit_usd=10.0,  # $10/month budget
    alert_thresholds=[50, 80, 100],
    project_tag="profile-scorer-saas",
)

# Note: Cost Anomaly Detection monitor already exists in the account
# (Default-Services-Monitor). View at:
# https://console.aws.amazon.com/cost-management/home#/anomaly-detection/monitors

# =============================================================================
# Stack Outputs - Infrastructure References
# =============================================================================
# These outputs are used by:
# - Local development (DATABASE_URL for psql/Drizzle)
# - Airflow configuration
# - CI/CD pipelines (for deployment verification)
#
# Access via: pulumi stack output <key> [--show-secrets]

# Network
pulumi.export("vpc_id", vpc.vpc.id)

# Database
pulumi.export("db_endpoint", db.instance.endpoint)
pulumi.export("db_connection_string", db.connection_string)  # Secret - use --show-secrets

# Resource Group (for consolidated AWS Console view)
pulumi.export("resource_group_arn", resource_group.arn)

# CloudWatch Dashboard
pulumi.export("dashboard_name", dashboard.dashboard.dashboard_name)
pulumi.export("dashboard_url", dashboard.dashboard.dashboard_name.apply(
    lambda name: f"https://us-east-2.console.aws.amazon.com/cloudwatch/home?region=us-east-2#dashboards:name={name}"
))

# Cost Management
pulumi.export("budget_name", budget.budget.name)
pulumi.export("cost_explorer_url",
    "https://us-east-1.console.aws.amazon.com/cost-management/home#/cost-explorer"
    "?chartStyle=STACK&costAggregate=unBlendedCost&endDate=2025-12-31&"
    "excludeForecasting=false&filter=%5B%7B%22dimension%22%3A%7B%22id%22%3A"
    "%22TagKeyValue%22%2C%22displayValue%22%3A%22Tag%22%7D%2C%22operator%22%3A"
    "%22INCLUDES%22%2C%22values%22%3A%5B%7B%22value%22%3A%22Project%24profile-scorer-saas%22%7D%5D%7D%5D&"
    "granularity=Daily&groupBy=%5B%22Service%22%5D&startDate=2025-12-01"
)

# EC2 Airflow
if airflow_instance:
    pulumi.export("airflow_instance_id", airflow_instance.instance.id)
    pulumi.export("airflow_public_ip", airflow_instance.eip.public_ip)
    pulumi.export("airflow_ssh_command", airflow_instance.eip.public_ip.apply(
        lambda ip: f"ssh -i ~/.ssh/{ssh_key_name}.pem ec2-user@{ip}"
    ))
    pulumi.export("airflow_url", "https://profile-scorer.admin.ateliertech.xyz")
