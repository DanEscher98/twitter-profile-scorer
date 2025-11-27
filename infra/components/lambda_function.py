import pulumi
import pulumi_aws as aws


class LambdaFunction(pulumi.ComponentResource):
    """Lambda function with VPC support"""

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

        # Security group for Lambda
        self.security_group = aws.ec2.SecurityGroup(
            f"{name}-sg",
            vpc_id=vpc_id,
            description=f"Security group for {name} lambda",
            egress=[
                aws.ec2.SecurityGroupEgressArgs(
                    from_port=0,
                    to_port=0,
                    protocol="-1",
                    cidr_blocks=["0.0.0.0/0"],
                )
            ],
            tags={"Name": f"{name}-sg"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # IAM role
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

        # Attach VPC access policy
        aws.iam.RolePolicyAttachment(
            f"{name}-vpc-policy",
            role=self.role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Lambda function
        self.function = aws.lambda_.Function(
            f"{name}-fn",
            runtime="nodejs20.x",
            handler=handler,
            role=self.role.arn,
            code=pulumi.FileArchive(code_path),
            timeout=timeout,
            memory_size=memory_size,
            vpc_config=aws.lambda_.FunctionVpcConfigArgs(
                subnet_ids=subnet_ids,
                security_group_ids=[self.security_group.id],
            ),
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
    """Lambda with EventBridge schedule"""

    def __init__(
        self,
        name: str,
        lambda_function: LambdaFunction,
        schedule_expression: str,  # e.g., "rate(1 minute)"
        opts: pulumi.ResourceOptions = None,
    ):
        super().__init__("custom:lambda:ScheduledLambda", name, None, opts)

        # EventBridge rule
        self.rule = aws.cloudwatch.EventRule(
            f"{name}-rule",
            schedule_expression=schedule_expression,
            tags={"Name": f"{name}-rule"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Target
        self.target = aws.cloudwatch.EventTarget(
            f"{name}-target",
            rule=self.rule.name,
            arn=lambda_function.function.arn,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Permission for EventBridge to invoke Lambda
        aws.lambda_.Permission(
            f"{name}-permission",
            action="lambda:InvokeFunction",
            function=lambda_function.function.name,
            principal="events.amazonaws.com",
            source_arn=self.rule.arn,
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.register_outputs({"rule_arn": self.rule.arn})
