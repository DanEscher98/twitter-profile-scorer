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
    LambdaFunction,
    ScheduledLambda,
    SqsQueue,
    SqsTriggeredLambda,
    Vpc,
)

# =============================================================================
# Configuration (Secrets stored in Pulumi config)
# =============================================================================
# These secrets are set via: pulumi config set --secret <key> <value>
# They're encrypted at rest and never exposed in logs or state files.

config = pulumi.Config()
db_password = config.require_secret("db_password")        # PostgreSQL password
twitterx_apikey = config.require_secret("twitterx_apikey")  # RapidAPI key for TwitterX
anthropic_apikey = config.require_secret("anthropic_apikey")  # Claude API key for LLM scoring

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

# Scoring Queue: Orchestrator → llm-scorer
# - visibility_timeout: 120s (LLM calls can be slow)
# - max_receive_count: 2 (LLM failures are usually not transient)
scoring_queue = SqsQueue(
    "scoring",
    visibility_timeout_seconds=120,  # LLM calls need more time
    message_retention_seconds=86400,
    max_receive_count=2,              # Fewer retries for LLM (expensive)
)

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
    },
)

# Lambda 3: LLM Scorer
# ---------------------
# Private subnet: Needs internet via NAT for Claude API calls.
# Processes batches of 25 profiles:
# 1. Fetches pending profiles from profiles_to_score
# 2. Formats as TOON prompt for Claude
# 3. Parses LLM response and stores in profile_scores
# 4. Removes scored profiles from queue
#
# Memory: 512MB for handling larger batches
# Timeout: 120s for LLM response latency
llm_scorer_lambda = LambdaFunction(
    "llm-scorer",
    code_path="../lambdas/llm-scorer/dist",
    handler="handler.handler",
    vpc_id=vpc.vpc.id,
    subnet_ids=vpc.private_subnet_ids,  # Internet via NAT for Claude API
    timeout=120,  # LLM calls can be slow
    memory_size=512,  # More memory for batch processing
    environment={
        "DATABASE_URL": db.connection_string,
        "ANTHROPIC_APIKEY": anthropic_apikey,
    },
)

# Lambda 4: Orchestrator
# -----------------------
# Private subnet: Needs internet via NAT for AWS API calls (Lambda invoke, SQS).
# This is the pipeline heartbeat, triggered every 15 minutes:
# 1. Invokes keyword-engine to get keyword list
# 2. Sends keywords to keywords-queue (triggers query-twitter-api)
# 3. Checks profiles_to_score count
# 4. Sends scoring jobs to scoring-queue if work exists
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
        "SCORING_QUEUE_URL": scoring_queue.queue.url,
    },
)

# =============================================================================
# IAM Permissions - Orchestrator Cross-Service Access
# =============================================================================
# The orchestrator needs explicit permissions to invoke other services.
# Following least-privilege principle: only the specific actions needed.

# Permission: Orchestrator → invoke keyword-engine Lambda
# Why: Orchestrator calls keyword-engine synchronously to get keyword list
orchestrator_invoke_policy = aws.iam.Policy(
    "orchestrator-invoke-policy",
    policy=keyword_engine_lambda.function.arn.apply(
        lambda arn: f"""{{
            "Version": "2012-10-17",
            "Statement": [
                {{
                    "Effect": "Allow",
                    "Action": "lambda:InvokeFunction",
                    "Resource": "{arn}"
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

# Permission: Orchestrator → send messages to SQS queues
# Why: Orchestrator enqueues keywords and scoring jobs
orchestrator_sqs_policy = aws.iam.Policy(
    "orchestrator-sqs-policy",
    policy=pulumi.Output.all(keywords_queue.queue.arn, scoring_queue.queue.arn).apply(
        lambda arns: f"""{{
            "Version": "2012-10-17",
            "Statement": [
                {{
                    "Effect": "Allow",
                    "Action": "sqs:SendMessage",
                    "Resource": ["{arns[0]}", "{arns[1]}"]
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

# Scoring queue → llm-scorer
# Each message triggers a batch scoring job
SqsTriggeredLambda(
    "scoring-to-llm",
    queue=scoring_queue,
    lambda_function=llm_scorer_lambda,
    batch_size=1,  # One scoring batch per invocation
)

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

# Lambda names (for CLI invocation, CloudWatch logs)
pulumi.export("keyword_engine_name", keyword_engine_lambda.function.name)
pulumi.export("query_twitter_name", query_twitter_lambda.function.name)
pulumi.export("llm_scorer_name", llm_scorer_lambda.function.name)
pulumi.export("orchestrator_name", orchestrator_lambda.function.name)

# Queue URLs (for sending messages, monitoring)
pulumi.export("keywords_queue_url", keywords_queue.queue.url)
pulumi.export("scoring_queue_url", scoring_queue.queue.url)
pulumi.export("keywords_dlq_url", keywords_queue.dlq.url)
pulumi.export("scoring_dlq_url", scoring_queue.dlq.url)
