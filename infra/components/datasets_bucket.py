"""
Datasets Bucket Component - S3 Storage for Curated Datasets

This module creates an S3 bucket for storing curated datasets used by the
dashboard for model evaluation (e.g., hand-picked ground truth labels).

Structure:
----------
s3://<bucket>/
  └── curated/
      └── hand_picked-<timestamp>.csv  # Ground truth labels

Usage:
------
- Upload datasets with timestamp suffix for versioning
- Dashboard reads the most recent file from S3 when deployed
- Falls back to local file for development
"""

import pulumi
import pulumi_aws as aws


class DatasetsBucket(pulumi.ComponentResource):
    """S3 bucket for storing curated datasets with public read access."""

    def __init__(
        self,
        name: str,
        opts: pulumi.ResourceOptions = None,
    ):
        super().__init__("custom:storage:DatasetsBucket", name, None, opts)

        # =====================================================================
        # S3 Bucket - Dataset Storage
        # =====================================================================
        self.bucket = aws.s3.Bucket(
            f"{name}-datasets",
            bucket=f"{name}-datasets",
            tags={
                "Name": f"{name}-datasets",
                "Project": "profile-scorer-saas",
            },
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # Bucket Public Access Block - Allow public read
        # =====================================================================
        # We allow public read for simplicity - datasets are not sensitive
        self.public_access_block = aws.s3.BucketPublicAccessBlock(
            f"{name}-datasets-public-access",
            bucket=self.bucket.id,
            block_public_acls=False,
            block_public_policy=False,
            ignore_public_acls=False,
            restrict_public_buckets=False,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # Bucket Policy - Public Read for curated/ prefix
        # =====================================================================
        self.bucket_policy = aws.s3.BucketPolicy(
            f"{name}-datasets-policy",
            bucket=self.bucket.id,
            policy=self.bucket.arn.apply(
                lambda arn: f"""{{
                    "Version": "2012-10-17",
                    "Statement": [
                        {{
                            "Sid": "PublicReadCurated",
                            "Effect": "Allow",
                            "Principal": "*",
                            "Action": "s3:GetObject",
                            "Resource": "{arn}/curated/*"
                        }},
                        {{
                            "Sid": "PublicListCurated",
                            "Effect": "Allow",
                            "Principal": "*",
                            "Action": "s3:ListBucket",
                            "Resource": "{arn}",
                            "Condition": {{
                                "StringLike": {{
                                    "s3:prefix": ["curated/*"]
                                }}
                            }}
                        }}
                    ]
                }}"""
            ),
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[self.public_access_block],
            ),
        )

        # Export the bucket URL for use in applications
        self.bucket_url = self.bucket.bucket.apply(
            lambda b: f"s3://{b}"
        )

        self.curated_url = self.bucket.bucket.apply(
            lambda b: f"https://{b}.s3.amazonaws.com/curated"
        )

        self.register_outputs({
            "bucket_id": self.bucket.id,
            "bucket_url": self.bucket_url,
            "curated_url": self.curated_url,
        })
