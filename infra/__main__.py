import pulumi

from components import Database, LambdaFunction, ScheduledLambda, Vpc

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

# Environment variables for lambdas
lambda_env = {
    "DATABASE_URL": db.connection_string,
    "TWITTERX_APIKEY": twitterx_apikey,
    "ANTHROPIC_APIKEY": anthropic_apikey,
}

# Lambda 1: DB Health Check (isolated subnet - no internet)
db_health_lambda = LambdaFunction(
    "keyword-engine",
    code_path="../lambdas/keyword-engine/dist",
    handler="handler.handler",
    vpc_id=vpc.vpc.id,
    subnet_ids=vpc.isolated_subnet_ids,  # No internet access
    environment=lambda_env,
)

# Lambda 2: External API Test (private subnet - has NAT for internet)
external_api_lambda = LambdaFunction(
    "query-twitter-api",
    code_path="../lambdas/query-twitter-api/dist",
    handler="handler.handler",
    vpc_id=vpc.vpc.id,
    subnet_ids=vpc.private_subnet_ids,  # Has internet via NAT
    environment=lambda_env,
)

# Allow lambdas to access DB
import pulumi_aws as aws

aws.ec2.SecurityGroupRule(
    "db-allow-health-lambda",
    type="ingress",
    from_port=5432,
    to_port=5432,
    protocol="tcp",
    source_security_group_id=db_health_lambda.security_group.id,
    security_group_id=db.security_group.id,
)

aws.ec2.SecurityGroupRule(
    "db-allow-external-lambda",
    type="ingress",
    from_port=5432,
    to_port=5432,
    protocol="tcp",
    source_security_group_id=external_api_lambda.security_group.id,
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

# Schedule both lambdas to run every minute
ScheduledLambda(
    "db-health-schedule",
    lambda_function=db_health_lambda,
    schedule_expression="rate(1 minute)",
)

ScheduledLambda(
    "external-api-schedule",
    lambda_function=external_api_lambda,
    schedule_expression="rate(1 minute)",
)

# Exports
pulumi.export("vpc_id", vpc.vpc.id)
pulumi.export("db_endpoint", db.instance.endpoint)
pulumi.export("db_connection_string", db.connection_string)
pulumi.export("db_health_lambda_name", db_health_lambda.function.name)
pulumi.export("external_api_lambda_name", external_api_lambda.function.name)
