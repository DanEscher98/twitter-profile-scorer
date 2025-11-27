"""
SQS Queue Components - Message Queue Infrastructure

This module provides two components for SQS-based messaging:

1. SqsQueue: Queue with automatic DLQ (dead letter queue)
2. SqsTriggeredLambda: Connects queue to Lambda with IAM

Why SQS over EventBridge/SNS?
-----------------------------
SQS is ideal for this use case because:
- Guaranteed delivery: Messages persist until processed
- Retry handling: Automatic retries with configurable count
- Backpressure: Queue depth limits concurrent processing
- Decoupling: Producer/consumer run independently

EventBridge is better for: Event routing, filtering, fan-out
SNS is better for: Pub/sub, push notifications, multiple subscribers

Dead Letter Queue (DLQ):
------------------------
Failed messages go to DLQ after maxReceiveCount attempts:
- Prevents poison messages from blocking the queue
- Enables debugging (inspect failed messages)
- 7-day retention for investigation before expiry

Message Processing Flow:
------------------------
1. Message arrives in queue (invisible for visibility_timeout)
2. Lambda receives message, processes it
3a. Success: Lambda deletes message
3b. Failure: Message becomes visible again after timeout
4. After maxReceiveCount failures → message moves to DLQ

Visibility Timeout:
-------------------
- Must be >= Lambda timeout (otherwise message re-appears during processing)
- Recommendation: visibility_timeout = Lambda timeout + buffer
- Example: 60s Lambda → 60-90s visibility_timeout

Batch Processing:
-----------------
batch_size controls how many messages Lambda receives per invocation:
- batch_size=1: Simple, one message per Lambda (default)
- batch_size=10: More efficient for high-throughput scenarios
- Trade-off: Partial batch failures are complex to handle
"""

import pulumi
import pulumi_aws as aws


class SqsQueue(pulumi.ComponentResource):
    """SQS Queue with automatic Dead Letter Queue for failed messages."""

    def __init__(
        self,
        name: str,
        visibility_timeout_seconds: int = 60,
        message_retention_seconds: int = 86400,  # 1 day
        max_receive_count: int = 3,
        opts: pulumi.ResourceOptions = None,
    ):
        super().__init__("custom:sqs:Queue", name, None, opts)

        # =====================================================================
        # Dead Letter Queue - Catch failed messages
        # =====================================================================
        # Messages that fail max_receive_count times end up here
        # Longer retention (7 days) gives time to investigate failures
        self.dlq = aws.sqs.Queue(
            f"{name}-dlq",
            message_retention_seconds=604800,  # 7 days (maximum)
            tags={"Name": f"{name}-dlq"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # Main Queue - Primary message queue
        # =====================================================================
        self.queue = aws.sqs.Queue(
            f"{name}-queue",

            # How long message is invisible after receive (must be >= Lambda timeout)
            visibility_timeout_seconds=visibility_timeout_seconds,

            # How long messages live in queue (unprocessed messages expire)
            message_retention_seconds=message_retention_seconds,

            # Redrive policy: Send to DLQ after N failures
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
    """Connects an SQS queue to a Lambda with event source mapping.

    This sets up:
    1. IAM policy: Lambda can receive/delete messages from queue
    2. Event source mapping: AWS automatically invokes Lambda when messages arrive

    How Event Source Mapping Works:
    -------------------------------
    - Lambda service polls SQS on your behalf (no charges for polling)
    - When messages arrive, Lambda is invoked with batch of messages
    - On success: Messages are automatically deleted
    - On failure: Messages become visible again after visibility timeout

    Batch Failure Handling:
    -----------------------
    Lambda returns batchItemFailures to indicate which messages failed:
    - Only failed messages are retried
    - Successful messages in the batch are deleted
    - Without this, entire batch retries on any failure
    """

    def __init__(
        self,
        name: str,
        queue: SqsQueue,
        lambda_function: "LambdaFunction",  # Forward reference
        batch_size: int = 1,
        opts: pulumi.ResourceOptions = None,
    ):
        super().__init__("custom:sqs:TriggeredLambda", name, None, opts)

        # =====================================================================
        # IAM Policy - Lambda permissions for SQS
        # =====================================================================
        # Required actions:
        # - ReceiveMessage: Get messages from queue
        # - DeleteMessage: Remove processed messages
        # - GetQueueAttributes: Check queue depth, etc.
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

        # Attach policy to Lambda's execution role
        aws.iam.RolePolicyAttachment(
            f"{name}-sqs-policy-attachment",
            role=lambda_function.role.name,
            policy_arn=sqs_policy.arn,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # Event Source Mapping - Connect SQS to Lambda
        # =====================================================================
        # This tells Lambda service to poll SQS and invoke the function
        self.event_source_mapping = aws.lambda_.EventSourceMapping(
            f"{name}-esm",
            event_source_arn=queue.queue.arn,
            function_name=lambda_function.function.name,
            batch_size=batch_size,  # Messages per Lambda invocation
            # Note: function_response_types=["ReportBatchItemFailures"] for partial failures
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.register_outputs(
            {
                "event_source_mapping_uuid": self.event_source_mapping.uuid,
            }
        )
