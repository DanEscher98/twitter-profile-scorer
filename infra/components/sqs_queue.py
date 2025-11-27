import pulumi
import pulumi_aws as aws


class SqsQueue(pulumi.ComponentResource):
    """SQS Queue with optional DLQ"""

    def __init__(
        self,
        name: str,
        visibility_timeout_seconds: int = 60,
        message_retention_seconds: int = 86400,  # 1 day
        max_receive_count: int = 3,
        opts: pulumi.ResourceOptions = None,
    ):
        super().__init__("custom:sqs:Queue", name, None, opts)

        # Dead letter queue
        self.dlq = aws.sqs.Queue(
            f"{name}-dlq",
            message_retention_seconds=604800,  # 7 days
            tags={"Name": f"{name}-dlq"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Main queue
        self.queue = aws.sqs.Queue(
            f"{name}-queue",
            visibility_timeout_seconds=visibility_timeout_seconds,
            message_retention_seconds=message_retention_seconds,
            redrive_policy=self.dlq.arn.apply(
                lambda arn: f'{{"deadLetterTargetArn":"{arn}","maxReceiveCount":{max_receive_count}}}'
            ),
            tags={"Name": f"{name}-queue"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.register_outputs(
            {
                "queue_url": self.queue.url,
                "queue_arn": self.queue.arn,
                "dlq_url": self.dlq.url,
                "dlq_arn": self.dlq.arn,
            }
        )


class SqsTriggeredLambda(pulumi.ComponentResource):
    """Connects an SQS queue to a Lambda with event source mapping"""

    def __init__(
        self,
        name: str,
        queue: SqsQueue,
        lambda_function: "LambdaFunction",  # Forward reference
        batch_size: int = 1,
        opts: pulumi.ResourceOptions = None,
    ):
        super().__init__("custom:sqs:TriggeredLambda", name, None, opts)

        # IAM policy for Lambda to read from SQS
        sqs_policy = aws.iam.Policy(
            f"{name}-sqs-policy",
            policy=pulumi.Output.all(queue.queue.arn, queue.dlq.arn).apply(
                lambda arns: f"""{{
                    "Version": "2012-10-17",
                    "Statement": [
                        {{
                            "Effect": "Allow",
                            "Action": [
                                "sqs:ReceiveMessage",
                                "sqs:DeleteMessage",
                                "sqs:GetQueueAttributes"
                            ],
                            "Resource": ["{arns[0]}", "{arns[1]}"]
                        }}
                    ]
                }}"""
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        aws.iam.RolePolicyAttachment(
            f"{name}-sqs-policy-attachment",
            role=lambda_function.role.name,
            policy_arn=sqs_policy.arn,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Event source mapping
        self.event_source_mapping = aws.lambda_.EventSourceMapping(
            f"{name}-esm",
            event_source_arn=queue.queue.arn,
            function_name=lambda_function.function.name,
            batch_size=batch_size,
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.register_outputs(
            {
                "event_source_mapping_uuid": self.event_source_mapping.uuid,
            }
        )
