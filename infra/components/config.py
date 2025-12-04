"""Centralized configuration for infrastructure components.

This module provides a single source of truth for infrastructure configuration,
preventing duplication and override bugs across components.

Usage:
    from components.config import Config

    # In __main__.py
    config = Config.from_pulumi()

    # Pass to components
    ec2 = Ec2Airflow("airflow", instance_type=config.ec2_instance_type, ...)
"""

from dataclasses import dataclass, field

import pulumi
import pulumi_aws as aws


@dataclass(frozen=True)
class Config:
    """Infrastructure configuration - single source of truth.

    All configurable values should be defined here, not scattered
    across components or hardcoded in __main__.py.
    """

    # Project identification
    project_name: str = "profile-scorer"
    project_tag: str = "profile-scorer-saas"

    # AWS region (detected from Pulumi/AWS provider)
    region: str = field(default_factory=lambda: aws.get_region().name)

    # Network configuration
    vpc_cidr: str = "10.0.0.0/16"
    public_subnet_cidrs: tuple[str, ...] = ("10.0.1.0/24", "10.0.2.0/24")
    private_subnet_cidrs: tuple[str, ...] = ("10.0.10.0/24", "10.0.11.0/24")
    isolated_subnet_cidrs: tuple[str, ...] = ("10.0.20.0/24", "10.0.21.0/24")

    # EC2 Airflow configuration
    ec2_instance_type: str = "t3.medium"
    ec2_volume_size_gb: int = 30

    # Database configuration
    db_instance_class: str = "db.t4g.micro"
    db_allocated_storage_gb: int = 20
    db_name: str = "profilescorer"
    db_username: str = "postgres"
    db_port: int = 5432

    # SageMaker configuration
    sagemaker_inference_instance_type: str = "ml.g5.xlarge"
    sagemaker_training_instance_type: str = "ml.g4dn.12xlarge"

    # Container versions (for reproducibility)
    docker_compose_version: str = "v2.24.0"

    @classmethod
    def from_pulumi(cls) -> "Config":
        """Load config from Pulumi config with defaults.

        Allows overriding defaults via `pulumi config set`.
        Example: pulumi config set profile-scorer:ec2_instance_type t3.large
        """
        pulumi_config = pulumi.Config()

        return cls(
            project_name=pulumi_config.get("project_name") or cls.project_name,
            project_tag=pulumi_config.get("project_tag") or cls.project_tag,
            ec2_instance_type=pulumi_config.get("ec2_instance_type") or cls.ec2_instance_type,
            sagemaker_inference_instance_type=(
                pulumi_config.get("sagemaker_inference_instance_type")
                or cls.sagemaker_inference_instance_type
            ),
        )

    def get_tags(self, name: str) -> dict[str, str]:
        """Generate standard tags for a resource.

        Args:
            name: Resource name

        Returns:
            Dict of tags including Name and Project
        """
        return {
            "Name": name,
            "Project": self.project_tag,
        }
