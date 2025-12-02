"""
Profile Scorer Infrastructure - Main Pulumi Program

This file orchestrates the deployment of AWS infrastructure for the Twitter
profile scoring pipeline. The system collects Twitter profiles, computes a
heuristic score (HAS), and queues high-scoring profiles for LLM evaluation.

Architecture Overview:
----------------------
1. VPC with three subnet tiers (public/private/isolated) for security
2. RDS PostgreSQL for persistent storage
3. Four Lambda functions for pipeline stages
4. Two SQS queues for decoupling and retry handling
5. EventBridge for scheduled orchestration

Data Flow:
----------
EventBridge (15 min) → Orchestrator → keyword-engine → SQS → query-twitter-api → DB
                                                              ↓
                                          profiles_to_score → SQS → llm-scorer → DB
"""

import pulumi
import pulumi_aws as aws

from components import (
    Database,
    Ec2Airflow,
    LambdaFunction,
    ProjectBudget,
    ScheduledLambda,
    SqsQueue,
    SqsTriggeredLambda,
    SystemDashboard,
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
# The VPC provides network isolation with three subnet tiers:
# - Public:   Has Internet Gateway, used for NAT Gateway and RDS (dev only)
# - Private:  Has NAT Gateway route, used for Lambdas needing internet access
# - Isolated: No internet route, used for DB-only Lambdas (most secure)
#
# This design follows AWS best practices: external APIs accessed via NAT,
# internal-only resources in isolated subnets with no attack surface.

vpc = Vpc("profile-scorer")

# =============================================================================
# Database - PostgreSQL on RDS
# =============================================================================
# RDS PostgreSQL stores all pipeline data:
# - user_profiles: Collected Twitter profiles with HAS scores
# - user_stats: Raw numeric fields for ML training
# - user_keywords: Many-to-many linking profiles to search keywords
# - xapi_usage_search: API call tracking and pagination state
# - profiles_to_score: Queue of profiles pending LLM evaluation
# - profile_scores: LLM-generated scores and reasoning
#
# Dev Note: Using public subnets for local psql access. In production,
# move to isolated_subnet_ids and remove public accessibility.

db = Database(
    "profile-scorer",
    vpc_id=vpc.vpc.id,
    subnet_ids=vpc.public_subnet_ids,  # Public for dev access; use isolated_subnet_ids for prod
    password=db_password,
    allowed_security_group_ids=[],  # Ingress rules added below after Lambdas are created
)

# =============================================================================
# SQS Queues - Decoupling and Retry Handling
# =============================================================================
# SQS provides reliable message delivery between pipeline stages:
# - Automatic retries with exponential backoff
# - Dead letter queues for failed messages (debugging)
# - Controlled concurrency via Lambda reserved concurrency

# Keywords Queue: Orchestrator → query-twitter-api
# - visibility_timeout: 60s (matches Lambda timeout, prevents duplicate processing)
# - max_receive_count: 3 retries before DLQ (handles transient API failures)
keywords_queue = SqsQueue(
    "keywords",
    visibility_timeout_seconds=60,   # Must be >= Lambda timeout
    message_retention_seconds=86400,  # 1 day retention
    max_receive_count=3,              # 3 attempts before DLQ
)

# NOTE: Scoring queue removed - llm-scorer now pulls work directly from DB
# The profiles_to_score table + LEFT JOIN pattern serves as the queue:
# - Persistence: profiles don't get lost
# - Idempotency: already-scored profiles filtered via profile_scores table
# - No duplicate work: atomic claims via FOR UPDATE SKIP LOCKED

# =============================================================================
# Lambda Functions - Pipeline Stages
# =============================================================================
# Each Lambda has a specific role in the pipeline:
# 1. keyword-engine: Selects keywords for searching (DB analytics)
# 2. query-twitter-api: Fetches profiles from RapidAPI
# 3. llm-scorer: Evaluates profiles with Claude
# 4. orchestrator: Coordinates the pipeline (heartbeat)

# Lambda 1: Keyword Engine
# -------------------------
# Isolated subnet: Only needs DB access, no internet required.
# This is the most secure configuration - no attack surface.
# Currently returns hardcoded keywords; will analyze xapi_usage_search
# to find keywords with highest new profile yield.
keyword_engine_lambda = LambdaFunction(
    "keyword-engine",
    code_path="../lambdas/keyword-engine/dist",
    handler="handler.handler",
    vpc_id=vpc.vpc.id,
    subnet_ids=vpc.isolated_subnet_ids,  # No internet - DB only
    environment={
        "DATABASE_URL": db.connection_string,
        "APP_MODE": "production",
    },
)

# Lambda 2: Query Twitter API
# ----------------------------
# Private subnet: Needs internet via NAT for RapidAPI calls.
# This Lambda does the heavy lifting:
# 1. Fetches profiles from RapidAPI TwitterX
# 2. Computes HAS (Human Authenticity Score) using heuristics
# 3. Stores profiles in user_profiles, user_stats, user_keywords
# 4. Queues high-HAS profiles (>0.65) to profiles_to_score
#
# Timeout: 60s to handle API latency and retries
query_twitter_lambda = LambdaFunction(
    "query-twitter-api",
    code_path="../lambdas/query-twitter-api/dist",
    handler="handler.handler",
    vpc_id=vpc.vpc.id,
    subnet_ids=vpc.private_subnet_ids,  # Internet via NAT for RapidAPI
    timeout=60,  # API calls + DB writes need time
    environment={
        "DATABASE_URL": db.connection_string,
        "TWITTERX_APIKEY": twitterx_apikey,
        "APP_MODE": "production",
    },
)

# Lambda 3: LLM Scorer
# ---------------------
# Private subnet: Needs internet via NAT for LLM API calls.
# Invoked directly by orchestrator (no SQS queue) with model parameter.
# Uses DB-as-queue pattern:
# 1. Claims batch of profiles atomically via FOR UPDATE SKIP LOCKED
# 2. Sends profiles to appropriate LLM (Claude, Gemini)
# 3. Stores scores in profile_scores (prevents re-scoring)
#
# Memory: 512MB for handling larger batches
# Timeout: 120s for LLM response latency
llm_scorer_lambda = LambdaFunction(
    "llm-scorer",
    code_path="../lambdas/llm-scorer/dist",
    handler="handler.handler",
    vpc_id=vpc.vpc.id,
    subnet_ids=vpc.private_subnet_ids,  # Internet via NAT for LLM APIs
    timeout=120,  # LLM calls can be slow
    memory_size=512,  # More memory for batch processing
    environment={
        "DATABASE_URL": db.connection_string,
        "ANTHROPIC_API_KEY": anthropic_apikey,
        "GEMINI_API_KEY": pulumi.Output.secret(require_env("GEMINI_API_KEY")),
        "GROQ_API_KEY": pulumi.Output.secret(require_env("GROQ_API_KEY")),
        "APP_MODE": "production",
    },
)

# Lambda 4: Keyword Stats Updater
# ---------------------------------
# Isolated subnet: Only needs DB access, no internet required.
# Runs daily to recalculate keyword_stats table:
# 1. Gets all keywords from xapi_usage_search
# 2. Calculates profiles_found, avg_human_score, avg_llm_score per keyword
# 3. Updates still_valid based on pagination availability
#
# This provides keyword-engine with a pre-computed stats table for selection.
keyword_stats_updater_lambda = LambdaFunction(
    "keyword-stats-updater",
    code_path="../lambdas/keyword-stats-updater/dist",
    handler="handler.handler",
    vpc_id=vpc.vpc.id,
    subnet_ids=vpc.isolated_subnet_ids,  # No internet - DB only
    timeout=120,  # May take time to process many keywords
    environment={
        "DATABASE_URL": db.connection_string,
        "APP_MODE": "production",
    },
)

# Lambda 5: Orchestrator
# -----------------------
# Private subnet: Needs internet via NAT for AWS API calls (Lambda invoke, SQS).
# This is the pipeline heartbeat, triggered every 15 minutes:
# 1. Invokes keyword-engine to get keyword list
# 2. Sends keywords to keywords-queue (triggers query-twitter-api)
# 3. Invokes llm-scorer directly for each configured model
#
# Why NAT instead of VPC endpoints? Cost - NAT is cheaper for low traffic.
orchestrator_lambda = LambdaFunction(
    "orchestrator",
    code_path="../lambdas/orchestrator/dist",
    handler="handler.handler",
    vpc_id=vpc.vpc.id,
    subnet_ids=vpc.private_subnet_ids,  # Internet via NAT for AWS API calls
    environment={
        "DATABASE_URL": db.connection_string,
        "KEYWORD_ENGINE_ARN": keyword_engine_lambda.function.arn,
        "KEYWORDS_QUEUE_URL": keywords_queue.queue.url,
        "LLM_SCORER_ARN": llm_scorer_lambda.function.arn,
        "APP_MODE": "production",
    },
)

# =============================================================================
# IAM Permissions - Orchestrator Cross-Service Access
# =============================================================================
# The orchestrator needs explicit permissions to invoke other services.
# Following least-privilege principle: only the specific actions needed.

# Permission: Orchestrator → invoke keyword-engine and llm-scorer Lambdas
# Why: Orchestrator calls keyword-engine synchronously and invokes llm-scorer per model
orchestrator_invoke_policy = aws.iam.Policy(
    "orchestrator-invoke-policy",
    policy=pulumi.Output.all(
        keyword_engine_lambda.function.arn,
        llm_scorer_lambda.function.arn
    ).apply(
        lambda arns: f"""{{
            "Version": "2012-10-17",
            "Statement": [
                {{
                    "Effect": "Allow",
                    "Action": "lambda:InvokeFunction",
                    "Resource": ["{arns[0]}", "{arns[1]}"]
                }}
            ]
        }}"""
    ),
)

aws.iam.RolePolicyAttachment(
    "orchestrator-invoke-policy-attachment",
    role=orchestrator_lambda.role.name,
    policy_arn=orchestrator_invoke_policy.arn,
)

# Permission: Orchestrator → send messages to keywords queue
# Why: Orchestrator enqueues keywords for query-twitter-api
orchestrator_sqs_policy = aws.iam.Policy(
    "orchestrator-sqs-policy",
    policy=keywords_queue.queue.arn.apply(
        lambda arn: f"""{{
            "Version": "2012-10-17",
            "Statement": [
                {{
                    "Effect": "Allow",
                    "Action": "sqs:SendMessage",
                    "Resource": "{arn}"
                }}
            ]
        }}"""
    ),
)

aws.iam.RolePolicyAttachment(
    "orchestrator-sqs-policy-attachment",
    role=orchestrator_lambda.role.name,
    policy_arn=orchestrator_sqs_policy.arn,
)

# =============================================================================
# SQS → Lambda Event Source Mappings
# =============================================================================
# These connect SQS queues to Lambda functions:
# - Lambda polls SQS automatically when messages arrive
# - batch_size=1: Process one message at a time for simplicity
# - Failed messages retry based on queue's maxReceiveCount

# Keywords queue → query-twitter-api
# Each message contains a keyword to search for on Twitter
SqsTriggeredLambda(
    "keywords-to-twitter",
    queue=keywords_queue,
    lambda_function=query_twitter_lambda,
    batch_size=1,  # One keyword per invocation for isolation
)

# NOTE: scoring-to-llm SQS trigger removed
# llm-scorer is now invoked directly by orchestrator with model parameter

# =============================================================================
# Database Security Group Rules
# =============================================================================
# Each Lambda gets explicit ingress to PostgreSQL (port 5432).
# This is more secure than a blanket "allow VPC" rule - each
# Lambda is individually authorized for database access.

# Allow keyword-engine → DB (isolated subnet, DB operations only)
aws.ec2.SecurityGroupRule(
    "db-allow-keyword-engine",
    type="ingress",
    from_port=5432,
    to_port=5432,
    protocol="tcp",
    source_security_group_id=keyword_engine_lambda.security_group.id,
    security_group_id=db.security_group.id,
)

# Allow query-twitter-api → DB (stores fetched profiles)
aws.ec2.SecurityGroupRule(
    "db-allow-query-twitter",
    type="ingress",
    from_port=5432,
    to_port=5432,
    protocol="tcp",
    source_security_group_id=query_twitter_lambda.security_group.id,
    security_group_id=db.security_group.id,
)

# Allow llm-scorer → DB (reads profiles_to_score, writes profile_scores)
aws.ec2.SecurityGroupRule(
    "db-allow-llm-scorer",
    type="ingress",
    from_port=5432,
    to_port=5432,
    protocol="tcp",
    source_security_group_id=llm_scorer_lambda.security_group.id,
    security_group_id=db.security_group.id,
)

# Allow orchestrator → DB (checks queue depths, stats)
aws.ec2.SecurityGroupRule(
    "db-allow-orchestrator",
    type="ingress",
    from_port=5432,
    to_port=5432,
    protocol="tcp",
    source_security_group_id=orchestrator_lambda.security_group.id,
    security_group_id=db.security_group.id,
)

# Allow keyword-stats-updater → DB (recalculates keyword statistics)
aws.ec2.SecurityGroupRule(
    "db-allow-keyword-stats-updater",
    type="ingress",
    from_port=5432,
    to_port=5432,
    protocol="tcp",
    source_security_group_id=keyword_stats_updater_lambda.security_group.id,
    security_group_id=db.security_group.id,
)

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
# EventBridge Schedule - Pipeline Heartbeat
# =============================================================================
# The orchestrator runs every 15 minutes to:
# 1. Fetch and queue new keywords
# 2. Trigger scoring jobs for pending profiles
#
# Why 15 minutes? Balance between API costs and data freshness.
# Can be adjusted via schedule_expression without code changes.

ScheduledLambda(
    "orchestrator-schedule",
    lambda_function=orchestrator_lambda,
    schedule_expression="rate(15 minutes)",  # Cron also supported: "cron(0/15 * * * ? *)"
)

# Daily schedule for keyword stats recalculation
# Runs at 4:00 AM UTC daily (11:00 PM EST / 8:00 PM PST)
ScheduledLambda(
    "keyword-stats-updater-schedule",
    lambda_function=keyword_stats_updater_lambda,
    schedule_expression="cron(0 4 * * ? *)",  # 4:00 AM UTC daily
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
# CloudWatch Dashboard - System Overview
# =============================================================================
# Comprehensive dashboard showing metrics for all components:
# - Lambda: Invocations, errors, duration, concurrency
# - RDS: Connections, CPU, storage, IOPS
# - SQS: Queue depth, message age, DLQ
# - NAT Gateway: Traffic, connections
#
# Access via: AWS Console → CloudWatch → Dashboards → profile-scorer

dashboard = SystemDashboard(
    "profile-scorer",
    lambda_names={
        "orchestrator": orchestrator_lambda.function.name,
        "keyword_engine": keyword_engine_lambda.function.name,
        "query_twitter": query_twitter_lambda.function.name,
        "llm_scorer": llm_scorer_lambda.function.name,
        "keyword_stats_updater": keyword_stats_updater_lambda.function.name,
    },
    db_instance_id=db.instance.identifier,
    queue_name=keywords_queue.queue.name,
    dlq_name=keywords_queue.dlq.name,
    nat_gateway_id=vpc.nat_gateway.id,
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
# EC2 Airflow Instance (Migration Target)
# =============================================================================
# This EC2 instance runs Apache Airflow to replace the Lambda-based orchestration.
# During migration, both systems run in parallel:
# - Lambda pipeline: EventBridge → orchestrator → keyword-engine → query-twitter → llm-scorer
# - Airflow pipeline: DAGs on EC2 (profile_scoring, keyword_stats)
#
# Post-migration steps:
# 1. Disable EventBridge schedule for orchestrator
# 2. Enable Airflow DAGs
# 3. Monitor for stability
# 4. Remove Lambda resources (optional, keep for rollback)
#
# SSH key: Must exist in AWS EC2 console. Create via:
#   aws ec2 create-key-pair --key-name profile-scorer-airflow --query 'KeyMaterial' --output text > ~/.ssh/profile-scorer-airflow.pem
#   chmod 600 ~/.ssh/profile-scorer-airflow.pem

# Optional: Only create EC2 if SSH key is configured
ssh_key_name = os.environ.get("AIRFLOW_SSH_KEY_NAME")

airflow_instance = None
if ssh_key_name:
    airflow_instance = Ec2Airflow(
        "profile-scorer-airflow",
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
# Stack Outputs - Infrastructure References
# =============================================================================
# These outputs are used by:
# - Local development (DATABASE_URL for psql/Drizzle)
# - E2E tests (Lambda names for invocation)
# - CI/CD pipelines (ARNs for deployment verification)
#
# Access via: pulumi stack output <key> [--show-secrets]

# Network
pulumi.export("vpc_id", vpc.vpc.id)

# Database
pulumi.export("db_endpoint", db.instance.endpoint)
pulumi.export("db_connection_string", db.connection_string)  # Secret - use --show-secrets

# Lambda ARNs (for IAM policies, monitoring)
pulumi.export("keyword_engine_arn", keyword_engine_lambda.function.arn)
pulumi.export("query_twitter_arn", query_twitter_lambda.function.arn)
pulumi.export("llm_scorer_arn", llm_scorer_lambda.function.arn)
pulumi.export("orchestrator_arn", orchestrator_lambda.function.arn)
pulumi.export("keyword_stats_updater_arn", keyword_stats_updater_lambda.function.arn)

# Lambda names (for CLI invocation, CloudWatch logs)
pulumi.export("keyword_engine_name", keyword_engine_lambda.function.name)
pulumi.export("query_twitter_name", query_twitter_lambda.function.name)
pulumi.export("llm_scorer_name", llm_scorer_lambda.function.name)
pulumi.export("orchestrator_name", orchestrator_lambda.function.name)
pulumi.export("keyword_stats_updater_name", keyword_stats_updater_lambda.function.name)

# Queue URLs (for sending messages, monitoring)
pulumi.export("keywords_queue_url", keywords_queue.queue.url)
pulumi.export("keywords_dlq_url", keywords_queue.dlq.url)

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
    "?chartStyle=STACK&costAggregate=unBlendedCost&endDate=2025-11-30&"
    "excludeForecasting=false&filter=%5B%7B%22dimension%22%3A%7B%22id%22%3A"
    "%22TagKeyValue%22%2C%22displayValue%22%3A%22Tag%22%7D%2C%22operator%22%3A"
    "%22INCLUDES%22%2C%22values%22%3A%5B%7B%22value%22%3A%22Project%24profile-scorer-saas%22%7D%5D%7D%5D&"
    "granularity=Daily&groupBy=%5B%22Service%22%5D&startDate=2025-11-01"
)

# EC2 Airflow (only exported if configured)
if airflow_instance:
    pulumi.export("airflow_instance_id", airflow_instance.instance.id)
    pulumi.export("airflow_public_ip", airflow_instance.eip.public_ip)
    pulumi.export("airflow_ssh_command", airflow_instance.eip.public_ip.apply(
        lambda ip: f"ssh -i ~/.ssh/{ssh_key_name}.pem ec2-user@{ip}"
    ))
    pulumi.export("airflow_url", "https://profile-scorer.admin.ateliertech.xyz")
