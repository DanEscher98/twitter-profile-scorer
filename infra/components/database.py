"""
Database Component - RDS PostgreSQL

This module creates an RDS PostgreSQL instance for persistent storage.

Why RDS over Aurora Serverless?
-------------------------------
- Cost: db.t3.micro is cheaper for dev/low traffic (~$15/month vs $70+)
- Simplicity: No cold start latency concerns
- Predictability: Fixed pricing, no surprise bills from ACU usage

Configuration Choices:
----------------------
- PostgreSQL 16: Latest LTS with best JSON and array support
- db.t3.micro: Burstable instance, 1GB RAM, sufficient for development
- gp2 storage: General purpose SSD, 20GB baseline
- Single AZ: Cost savings for dev (enable multi-AZ for production)

Security Configuration:
-----------------------
- Security group controls ingress at network level
- SSL required via connection string (?sslmode=require)
- Lambda bundles RDS CA cert for certificate verification
- Password stored as Pulumi secret (encrypted)

Dev vs Production Notes:
------------------------
Dev (current):
  - publicly_accessible=True: Allows psql from local machine
  - skip_final_snapshot=True: Fast teardown
  - backup_retention_period=0: No backups (cost savings)

Production (recommended changes):
  - publicly_accessible=False: Only VPC access
  - skip_final_snapshot=False: Prevent accidental data loss
  - backup_retention_period=7: Daily backups, 7-day retention
  - multi_az=True: Automatic failover
  - Restrict security group to specific IPs
"""

import pulumi
import pulumi_aws as aws


class Database(pulumi.ComponentResource):
    """RDS PostgreSQL instance with security group and connection string."""

    def __init__(
        self,
        name: str,
        vpc_id: pulumi.Input[str],
        subnet_ids: list[pulumi.Input[str]],
        password: pulumi.Input[str],
        allowed_security_group_ids: list[pulumi.Input[str]] = None,
        opts: pulumi.ResourceOptions = None,
    ):
        super().__init__("custom:database:Postgres", name, None, opts)

        # =====================================================================
        # Subnet Group - Multi-AZ placement
        # =====================================================================
        # RDS requires subnets in 2+ AZs even for single-AZ deployments
        # This allows easy upgrade to Multi-AZ later
        self.subnet_group = aws.rds.SubnetGroup(
            f"{name}-subnet-group",
            subnet_ids=subnet_ids,
            tags={"Name": f"{name}-subnet-group"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # Security Group - Network access control
        # =====================================================================
        # Controls which resources can connect to PostgreSQL (port 5432)
        # Ingress rules added separately to avoid circular dependencies
        self.security_group = aws.ec2.SecurityGroup(
            f"{name}-sg",
            vpc_id=vpc_id,
            description="Allow PostgreSQL access",
            tags={"Name": f"{name}-sg"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Add ingress rules for explicitly allowed security groups
        # (Typically Lambda security groups added in __main__.py)
        if allowed_security_group_ids:
            for i, sg_id in enumerate(allowed_security_group_ids):
                aws.ec2.SecurityGroupRule(
                    f"{name}-ingress-{i}",
                    type="ingress",
                    from_port=5432,
                    to_port=5432,
                    protocol="tcp",
                    source_security_group_id=sg_id,
                    security_group_id=self.security_group.id,
                    opts=pulumi.ResourceOptions(parent=self),
                )

        # =====================================================================
        # RDS Instance - PostgreSQL database
        # =====================================================================
        self.instance = aws.rds.Instance(
            f"{name}-db",
            identifier=f"{name}-postgres",

            # Engine configuration
            engine="postgres",
            engine_version="16",                    # Latest LTS version
            auto_minor_version_upgrade=True,        # Auto-apply security patches

            # Instance sizing
            instance_class="db.t3.micro",           # Smallest burstable (1GB RAM)
            allocated_storage=20,                   # 20GB minimum for gp2
            storage_type="gp2",                     # General purpose SSD

            # Database configuration
            db_name="profilescorer",                # Initial database name
            username="postgres",                    # Master username
            password=password,                      # From Pulumi secret

            # Network configuration
            db_subnet_group_name=self.subnet_group.name,
            vpc_security_group_ids=[self.security_group.id],
            publicly_accessible=True,               # DEV ONLY - set False for prod

            # Backup and maintenance (dev settings)
            skip_final_snapshot=True,               # DEV ONLY - set False for prod
            backup_retention_period=0,              # DEV ONLY - set 7+ for prod

            tags={"Name": f"{name}-postgres"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # Connection String - For application use
        # =====================================================================
        # Format: postgresql://user:pass@host:port/database?sslmode=require
        # sslmode=require ensures encrypted connections (RDS enforces this)
        # Lambda uses RDS CA bundle for certificate verification
        self.connection_string = pulumi.Output.all(
            self.instance.endpoint, password
        ).apply(lambda args: f"postgresql://postgres:{args[1]}@{args[0]}/profilescorer?sslmode=require")

        self.register_outputs(
            {
                "endpoint": self.instance.endpoint,
                "connection_string": self.connection_string,
                "security_group_id": self.security_group.id,
            }
        )
