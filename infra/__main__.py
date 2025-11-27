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

# Config
config = pulumi.Config()
db_password = config.require_secret("db_password")
twitterx_apikey = config.require_secret("twitterx_apikey")
anthropic_apikey = config.require_secret("anthropic_apikey")

# VPC
vpc = Vpc("profile-scorer")

# Database (using public subnets for dev access - use isolated_subnet_ids for prod)
db = Database(
    "profile-scorer",
    vpc_id=vpc.vpc.id,
    subnet_ids=vpc.public_subnet_ids,  # Public for dev local access; use isolated_subnet_ids for prod
    password=db_password,
    allowed_security_group_ids=[],  # Will be updated after lambdas are created
)

# =============================================================================
# SQS Queues
# =============================================================================

keywords_queue = SqsQueue(
    "keywords",
    visibility_timeout_seconds=60,
    message_retention_seconds=86400,
    max_receive_count=3,
)

scoring_queue = SqsQueue(
    "scoring",
    visibility_timeout_seconds=120,
    message_retention_seconds=86400,
    max_receive_count=2,
)

# =============================================================================
# Lambda Functions
# =============================================================================

# Lambda 1: Keyword Engine (isolated subnet - DB only)
keyword_engine_lambda = LambdaFunction(
    "keyword-engine",
    code_path="../lambdas/keyword-engine/dist",
    handler="handler.handler",
    vpc_id=vpc.vpc.id,
    subnet_ids=vpc.isolated_subnet_ids,
    environment={
        "DATABASE_URL": db.connection_string,
    },
)

# Lambda 2: Query Twitter API (private subnet - internet via NAT)
query_twitter_lambda = LambdaFunction(
    "query-twitter-api",
    code_path="../lambdas/query-twitter-api/dist",
    handler="handler.handler",
    vpc_id=vpc.vpc.id,
    subnet_ids=vpc.private_subnet_ids,
    timeout=60,
    environment={
        "DATABASE_URL": db.connection_string,
        "TWITTERX_APIKEY": twitterx_apikey,
    },
)

# Lambda 3: LLM Scorer (private subnet - internet via NAT)
llm_scorer_lambda = LambdaFunction(
    "llm-scorer",
    code_path="../lambdas/llm-scorer/dist",
    handler="handler.handler",
    vpc_id=vpc.vpc.id,
    subnet_ids=vpc.private_subnet_ids,
    timeout=120,
    memory_size=512,
    environment={
        "DATABASE_URL": db.connection_string,
        "ANTHROPIC_APIKEY": anthropic_apikey,
    },
)

# Lambda 4: Orchestrator (private subnet - needs internet for Lambda/SQS APIs)
orchestrator_lambda = LambdaFunction(
    "orchestrator",
    code_path="../lambdas/orchestrator/dist",
    handler="handler.handler",
    vpc_id=vpc.vpc.id,
    subnet_ids=vpc.private_subnet_ids,  # Needs NAT for AWS API calls
    environment={
        "DATABASE_URL": db.connection_string,
        "KEYWORD_ENGINE_ARN": keyword_engine_lambda.function.arn,
        "KEYWORDS_QUEUE_URL": keywords_queue.queue.url,
        "SCORING_QUEUE_URL": scoring_queue.queue.url,
    },
)

# =============================================================================
# IAM Permissions for Orchestrator
# =============================================================================

# Allow orchestrator to invoke keyword-engine
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

# Allow orchestrator to send messages to SQS queues
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
# SQS -> Lambda Event Source Mappings
# =============================================================================

# Keywords queue triggers query-twitter-api (concurrency controlled by reserved concurrency)
SqsTriggeredLambda(
    "keywords-to-twitter",
    queue=keywords_queue,
    lambda_function=query_twitter_lambda,
    batch_size=1,
)

# Scoring queue triggers llm-scorer
SqsTriggeredLambda(
    "scoring-to-llm",
    queue=scoring_queue,
    lambda_function=llm_scorer_lambda,
    batch_size=1,
)

# =============================================================================
# DB Security Group Rules
# =============================================================================

aws.ec2.SecurityGroupRule(
    "db-allow-keyword-engine",
    type="ingress",
    from_port=5432,
    to_port=5432,
    protocol="tcp",
    source_security_group_id=keyword_engine_lambda.security_group.id,
    security_group_id=db.security_group.id,
)

aws.ec2.SecurityGroupRule(
    "db-allow-query-twitter",
    type="ingress",
    from_port=5432,
    to_port=5432,
    protocol="tcp",
    source_security_group_id=query_twitter_lambda.security_group.id,
    security_group_id=db.security_group.id,
)

aws.ec2.SecurityGroupRule(
    "db-allow-llm-scorer",
    type="ingress",
    from_port=5432,
    to_port=5432,
    protocol="tcp",
    source_security_group_id=llm_scorer_lambda.security_group.id,
    security_group_id=db.security_group.id,
)

aws.ec2.SecurityGroupRule(
    "db-allow-orchestrator",
    type="ingress",
    from_port=5432,
    to_port=5432,
    protocol="tcp",
    source_security_group_id=orchestrator_lambda.security_group.id,
    security_group_id=db.security_group.id,
)

# Allow external access to DB for local development (use with caution in prod!)
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
# Schedule Orchestrator (heartbeat every 15 minutes)
# =============================================================================

ScheduledLambda(
    "orchestrator-schedule",
    lambda_function=orchestrator_lambda,
    schedule_expression="rate(15 minutes)",
)

# =============================================================================
# Exports
# =============================================================================

pulumi.export("vpc_id", vpc.vpc.id)
pulumi.export("db_endpoint", db.instance.endpoint)
pulumi.export("db_connection_string", db.connection_string)

# Lambda ARNs
pulumi.export("keyword_engine_arn", keyword_engine_lambda.function.arn)
pulumi.export("query_twitter_arn", query_twitter_lambda.function.arn)
pulumi.export("llm_scorer_arn", llm_scorer_lambda.function.arn)
pulumi.export("orchestrator_arn", orchestrator_lambda.function.arn)

# Lambda names
pulumi.export("keyword_engine_name", keyword_engine_lambda.function.name)
pulumi.export("query_twitter_name", query_twitter_lambda.function.name)
pulumi.export("llm_scorer_name", llm_scorer_lambda.function.name)
pulumi.export("orchestrator_name", orchestrator_lambda.function.name)

# Queue URLs
pulumi.export("keywords_queue_url", keywords_queue.queue.url)
pulumi.export("scoring_queue_url", scoring_queue.queue.url)
pulumi.export("keywords_dlq_url", keywords_queue.dlq.url)
pulumi.export("scoring_dlq_url", scoring_queue.dlq.url)
