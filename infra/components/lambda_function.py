"""
Lambda Function Components - Serverless Compute

This module provides two components for Lambda-based compute:

1. LambdaFunction: Base Lambda with VPC support
2. ScheduledLambda: EventBridge-triggered Lambda (cron/rate)

Why Lambda over ECS/Fargate?
----------------------------
- Cost: Pay only when code runs (vs always-on containers)
- Simplicity: No container orchestration complexity
- Scaling: Automatic scaling to 1000+ concurrent invocations
- Integration: Native SQS triggers, CloudWatch logs

VPC Configuration:
------------------
Lambda in VPC is required because:
- RDS is in VPC (private networking)
- Security groups control DB access
- Network isolation for security

Trade-off: VPC Lambdas have ~1s cold start overhead due to ENI allocation.
Mitigation: Use provisioned concurrency for latency-sensitive functions.

Security Group Design:
----------------------
Each Lambda gets its own security group:
- Egress: Allow all (0.0.0.0/0) for internet/API access
- Ingress: None (Lambdas don't accept inbound connections)

This allows fine-grained DB access control: only Lambdas with
explicit ingress rules in the DB security group can connect.

IAM Role Design:
----------------
Each Lambda gets its own IAM role with:
- Trust policy: Lambda service can assume the role
- Managed policy: AWSLambdaVPCAccessExecutionRole (ENI management)
- Additional policies added as needed (SQS, Lambda invoke, etc.)

Runtime Configuration:
----------------------
- Node.js 20.x: LTS version with best performance
- 256MB memory: Default, sufficient for most operations
- 30s timeout: Default, adjust per function needs
"""

import pulumi
import pulumi_aws as aws


class LambdaFunction(pulumi.ComponentResource):
    """Lambda function with VPC support and IAM role."""

    def __init__(
        self,
        name: str,
        code_path: str,
        handler: str,
        vpc_id: pulumi.Input[str],
        subnet_ids: list[pulumi.Input[str]],
        environment: dict[str, pulumi.Input[str]] = None,
        timeout: int = 30,
        memory_size: int = 256,
        opts: pulumi.ResourceOptions = None,
    ):
        super().__init__("custom:lambda:Function", name, None, opts)

        # =====================================================================
        # Security Group - Lambda network identity
        # =====================================================================
        # Used for:
        # 1. DB ingress rules (allow this Lambda to connect)
        # 2. Egress to internet (API calls) or VPC resources (DB)
        self.security_group = aws.ec2.SecurityGroup(
            f"{name}-sg",
            vpc_id=vpc_id,
            description=f"Security group for {name} lambda",
            egress=[
                # Allow all outbound traffic (internet, VPC, AWS APIs)
                aws.ec2.SecurityGroupEgressArgs(
                    from_port=0,
                    to_port=0,
                    protocol="-1",  # All protocols
                    cidr_blocks=["0.0.0.0/0"],
                )
            ],
            # No ingress rules - Lambdas don't accept inbound connections
            tags={"Name": f"{name}-sg"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # IAM Role - Lambda execution permissions
        # =====================================================================
        # Trust policy: Who can assume this role (Lambda service)
        assume_role_policy = aws.iam.get_policy_document(
            statements=[
                aws.iam.GetPolicyDocumentStatementArgs(
                    actions=["sts:AssumeRole"],
                    principals=[
                        aws.iam.GetPolicyDocumentStatementPrincipalArgs(
                            type="Service",
                            identifiers=["lambda.amazonaws.com"],
                        )
                    ],
                )
            ]
        )

        self.role = aws.iam.Role(
            f"{name}-role",
            assume_role_policy=assume_role_policy.json,
            tags={"Name": f"{name}-role"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # VPC access policy: Required for ENI creation/deletion in VPC
        # Includes: ec2:CreateNetworkInterface, ec2:DeleteNetworkInterface,
        # ec2:DescribeNetworkInterfaces, logs:CreateLogGroup, etc.
        aws.iam.RolePolicyAttachment(
            f"{name}-vpc-policy",
            role=self.role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # Lambda Function - The actual compute resource
        # =====================================================================
        self.function = aws.lambda_.Function(
            f"{name}-fn",

            # Runtime configuration
            runtime="nodejs20.x",  # Latest LTS with best cold start performance
            handler=handler,        # Module.function to invoke (e.g., handler.handler)

            # IAM role for execution permissions
            role=self.role.arn,

            # Code package - directory is zipped automatically
            code=pulumi.FileArchive(code_path),

            # Resource limits
            timeout=timeout,        # Max execution time in seconds
            memory_size=memory_size,  # Also determines CPU allocation (1769MB = 1 vCPU)

            # VPC configuration for private network access
            vpc_config=aws.lambda_.FunctionVpcConfigArgs(
                subnet_ids=subnet_ids,
                security_group_ids=[self.security_group.id],
            ),

            # Environment variables (e.g., DATABASE_URL, API keys)
            environment=aws.lambda_.FunctionEnvironmentArgs(variables=environment)
            if environment
            else None,

            tags={"Name": name},
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.register_outputs(
            {
                "function_name": self.function.name,
                "function_arn": self.function.arn,
                "security_group_id": self.security_group.id,
            }
        )


class ScheduledLambda(pulumi.ComponentResource):
    """EventBridge rule that triggers a Lambda on a schedule.

    Schedule Expressions:
    ---------------------
    Rate: "rate(1 minute)", "rate(5 minutes)", "rate(1 hour)", "rate(1 day)"
    Cron: "cron(0 12 * * ? *)" = noon UTC daily

    Use cases:
    - Periodic data collection (orchestrator every 15 min)
    - Cleanup jobs (delete old records daily)
    - Health checks (ping services every minute)

    Why EventBridge over CloudWatch Events?
    ---------------------------------------
    EventBridge is the successor to CloudWatch Events with:
    - Better event filtering
    - Schema registry
    - Third-party integrations
    (For schedules, they're functionally identical)
    """

    def __init__(
        self,
        name: str,
        lambda_function: LambdaFunction,
        schedule_expression: str,  # e.g., "rate(15 minutes)"
        opts: pulumi.ResourceOptions = None,
    ):
        super().__init__("custom:lambda:ScheduledLambda", name, None, opts)

        # =====================================================================
        # EventBridge Rule - Defines the schedule
        # =====================================================================
        self.rule = aws.cloudwatch.EventRule(
            f"{name}-rule",
            schedule_expression=schedule_expression,
            tags={"Name": f"{name}-rule"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # Event Target - What to invoke when rule fires
        # =====================================================================
        self.target = aws.cloudwatch.EventTarget(
            f"{name}-target",
            rule=self.rule.name,
            arn=lambda_function.function.arn,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # Lambda Permission - Allow EventBridge to invoke
        # =====================================================================
        # Without this, EventBridge can't call the Lambda (resource-based policy)
        aws.lambda_.Permission(
            f"{name}-permission",
            action="lambda:InvokeFunction",
            function=lambda_function.function.name,
            principal="events.amazonaws.com",
            source_arn=self.rule.arn,  # Only this specific rule can invoke
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.register_outputs({"rule_arn": self.rule.arn})
