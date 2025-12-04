"""
SageMaker LLM Component - Fine-tuned LLM Training and Inference

This module creates infrastructure for training and serving a custom
fine-tuned Mistral-7B model on SageMaker:

- S3 bucket for training data and model artifacts
- IAM role for SageMaker execution
- SageMaker Model (references the trained model in S3)
- SageMaker Endpoint Configuration
- SageMaker Endpoint for real-time inference

Training Workflow:
------------------
1. Upload training data: aws s3 cp profile_scorer_train.jsonl s3://<bucket>/training/
2. Run training job: python scripts/training/run_sagemaker_training.py
3. Training outputs model to: s3://<bucket>/models/<job-name>/output/model.tar.gz
4. Update Pulumi config with model path
5. pulumi up -> Creates endpoint pointing to trained model

Inference:
----------
Airflow DAGs call the endpoint via boto3 SageMaker runtime:
  response = sagemaker_runtime.invoke_endpoint(
      EndpointName="profile-scorer-llm",
      ContentType="application/json",
      Body=json.dumps({"instruction": "..."})
  )

Cost Considerations:
--------------------
- Training: ml.g4dn.xlarge spot ($0.15/hr) - ~2-3 hours for 500 samples
- Inference: ml.g5.xlarge on-demand ($1.41/hr) - 24GB VRAM for Mistral-7B, delete when not needed
- S3: Minimal (~$0.02/month for model artifacts)

Note: For production, consider:
- Serverless inference (ml.inf1 with reduced cold start)
- Auto-scaling based on invocation count
- Model registry for versioning
"""

import json

import pulumi
import pulumi_aws as aws


class SageMakerLlm(pulumi.ComponentResource):
    """SageMaker infrastructure for fine-tuned LLM training and inference."""

    def __init__(
        self,
        name: str,
        model_s3_uri: pulumi.Input[str] | None = None,
        instance_type: str = "ml.g5.xlarge",
        enable_endpoint: bool = True,
        opts: pulumi.ResourceOptions = None,
    ):
        """Create SageMaker LLM infrastructure.

        Args:
            name: Resource name prefix.
            model_s3_uri: S3 URI to trained model (e.g., s3://bucket/models/model.tar.gz).
                         If None, only creates bucket and role (for training).
            instance_type: EC2 instance type for inference endpoint.
            enable_endpoint: Whether to create the inference endpoint.
            opts: Pulumi resource options.
        """
        super().__init__("custom:ml:SageMakerLlm", name, None, opts)

        # =====================================================================
        # S3 Bucket - Training Data and Model Artifacts
        # =====================================================================
        self.bucket = aws.s3.Bucket(
            f"{name}-sagemaker-bucket",
            bucket=f"{name}-sagemaker-{pulumi.get_stack()}",
            force_destroy=False,  # Prevent deletion with objects (training data/models)
            tags={
                "Name": f"{name}-sagemaker-bucket",
                "Project": "profile-scorer-saas",
            },
            opts=pulumi.ResourceOptions(
                parent=self,
                protect=True,  # Protect training data and model artifacts
            ),
        )

        # Block public access
        aws.s3.BucketPublicAccessBlock(
            f"{name}-bucket-public-access-block",
            bucket=self.bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # IAM Role - SageMaker Execution
        # =====================================================================
        assume_role_policy = json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "sagemaker.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }],
        })

        self.role = aws.iam.Role(
            f"{name}-sagemaker-role",
            assume_role_policy=assume_role_policy,
            tags={
                "Name": f"{name}-sagemaker-role",
                "Project": "profile-scorer-saas",
            },
            opts=pulumi.ResourceOptions(parent=self),
        )

        # S3 access policy for training data and model artifacts
        s3_policy = self.bucket.arn.apply(lambda arn: json.dumps({
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetObject",
                        "s3:PutObject",
                        "s3:DeleteObject",
                        "s3:ListBucket",
                    ],
                    "Resource": [
                        arn,
                        f"{arn}/*",
                    ],
                },
            ],
        }))

        aws.iam.RolePolicy(
            f"{name}-sagemaker-s3-policy",
            role=self.role.name,
            policy=s3_policy,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Attach SageMaker full access (includes ECR, CloudWatch, etc.)
        aws.iam.RolePolicyAttachment(
            f"{name}-sagemaker-full-access",
            role=self.role.name,
            policy_arn="arn:aws:iam::aws:policy/AmazonSageMakerFullAccess",
            opts=pulumi.ResourceOptions(parent=self),
        )

        # CloudWatch logs policy
        aws.iam.RolePolicyAttachment(
            f"{name}-sagemaker-cloudwatch",
            role=self.role.name,
            policy_arn="arn:aws:iam::aws:policy/CloudWatchLogsFullAccess",
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # SageMaker Model, Endpoint Config, and Endpoint
        # =====================================================================
        # Only create if model_s3_uri is provided
        self.model = None
        self.endpoint_config = None
        self.endpoint = None

        if model_s3_uri and enable_endpoint:
            # Get current region
            region = aws.get_region()

            # Use HuggingFace TGI (Text Generation Inference) container
            # This supports Mistral-7B out of the box
            # Container URI format: {account}.dkr.ecr.{region}.amazonaws.com/huggingface-pytorch-tgi-inference:{version}
            container_image = f"763104351884.dkr.ecr.{region.name}.amazonaws.com/huggingface-pytorch-tgi-inference:2.1.1-tgi1.4.0-gpu-py310-cu121-ubuntu20.04"

            self.model = aws.sagemaker.Model(
                f"{name}-llm-model",
                name=f"{name}-llm-model",
                execution_role_arn=self.role.arn,
                primary_container=aws.sagemaker.ModelPrimaryContainerArgs(
                    image=container_image,
                    model_data_url=model_s3_uri,
                    environment={
                        "HF_MODEL_ID": "/opt/ml/model",
                        "SM_NUM_GPUS": "1",
                        "MAX_INPUT_LENGTH": "1024",
                        "MAX_TOTAL_TOKENS": "2048",
                        "MAX_BATCH_PREFILL_TOKENS": "2048",  # A10G has 24GB VRAM
                    },
                ),
                tags={
                    "Name": f"{name}-llm-model",
                    "Project": "profile-scorer-saas",
                },
                opts=pulumi.ResourceOptions(parent=self),
            )

            self.endpoint_config = aws.sagemaker.EndpointConfiguration(
                f"{name}-llm-config",
                name=f"{name}-llm-config",
                production_variants=[
                    aws.sagemaker.EndpointConfigurationProductionVariantArgs(
                        variant_name="primary",
                        model_name=self.model.name,
                        initial_instance_count=1,
                        instance_type=instance_type,
                        initial_variant_weight=1.0,
                    ),
                ],
                tags={
                    "Name": f"{name}-llm-config",
                    "Project": "profile-scorer-saas",
                },
                opts=pulumi.ResourceOptions(parent=self),
            )

            self.endpoint = aws.sagemaker.Endpoint(
                f"{name}-llm-endpoint",
                name=f"{name}-llm-endpoint",
                endpoint_config_name=self.endpoint_config.name,
                tags={
                    "Name": f"{name}-llm-endpoint",
                    "Project": "profile-scorer-saas",
                },
                opts=pulumi.ResourceOptions(parent=self),
            )

        # =====================================================================
        # Outputs
        # =====================================================================
        outputs = {
            "bucket_name": self.bucket.id,
            "bucket_arn": self.bucket.arn,
            "role_arn": self.role.arn,
            "training_data_uri": self.bucket.id.apply(
                lambda b: f"s3://{b}/training/"
            ),
            "models_uri": self.bucket.id.apply(
                lambda b: f"s3://{b}/models/"
            ),
        }

        if self.endpoint:
            outputs["endpoint_name"] = self.endpoint.name
            outputs["endpoint_arn"] = self.endpoint.arn

        self.register_outputs(outputs)
