import pulumi
import pulumi_aws as aws


class Database(pulumi.ComponentResource):
    """RDS PostgreSQL instance"""

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

        # Subnet group
        self.subnet_group = aws.rds.SubnetGroup(
            f"{name}-subnet-group",
            subnet_ids=subnet_ids,
            tags={"Name": f"{name}-subnet-group"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Security group
        self.security_group = aws.ec2.SecurityGroup(
            f"{name}-sg",
            vpc_id=vpc_id,
            description="Allow PostgreSQL access",
            tags={"Name": f"{name}-sg"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Allow inbound from specified security groups
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

        # RDS instance
        self.instance = aws.rds.Instance(
            f"{name}-db",
            identifier=f"{name}-postgres",
            engine="postgres",
            engine_version="16",
            auto_minor_version_upgrade=True,
            instance_class="db.t3.micro",
            allocated_storage=20,
            storage_type="gp2",
            db_name="profilescorer",
            username="postgres",
            password=password,
            db_subnet_group_name=self.subnet_group.name,
            vpc_security_group_ids=[self.security_group.id],
            skip_final_snapshot=True,  # For dev; set False in prod
            publicly_accessible=True,  # For dev; set False in prod
            backup_retention_period=0,  # Disable backups for dev
            tags={"Name": f"{name}-postgres"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Connection string (with sslmode=require for RDS)
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
