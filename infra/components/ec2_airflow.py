"""
EC2 Airflow Component - Airflow Server Infrastructure

This module creates an EC2 instance for running Apache Airflow with:
- t3.small instance (2 vCPU, 2GB RAM) - sufficient for LocalExecutor
- Public subnet with Elastic IP for HTTPS access
- Security group allowing SSH, HTTP, HTTPS
- IAM role for CloudWatch logging

Deployment Workflow:
--------------------
1. `pulumi up` - Creates EC2 with Docker, nginx, certbot installed
2. `./deploy.sh <elastic-ip> <ssh-key>` - Deploys application code and starts Airflow

This two-step approach allows:
- Credential rotation without Pulumi changes
- Domain configuration flexibility (set in .env, not Pulumi)
- Secure credential management (not stored in Pulumi state)

SSH Access:
-----------
Use the SSH key specified in ssh_key_name parameter:
  ssh -i ~/.ssh/<key>.pem ec2-user@<elastic-ip>

Prerequisites before deploy.sh:
-------------------------------
1. Configure DNS A record pointing to the Elastic IP
2. Copy .env.example to .env and configure all values
3. Generate secure AIRFLOW_SECRET_KEY and AIRFLOW_ADMIN_PASSWORD
"""

import pulumi
import pulumi_aws as aws


class Ec2Airflow(pulumi.ComponentResource):
    """EC2 instance for Airflow with security group and Elastic IP."""

    def __init__(
        self,
        name: str,
        vpc_id: pulumi.Input[str],
        subnet_id: pulumi.Input[str],
        db_security_group_id: pulumi.Input[str],
        ssh_key_name: str,
        database_url: pulumi.Input[str],
        twitterx_apikey: pulumi.Input[str],
        anthropic_api_key: pulumi.Input[str],
        gemini_api_key: pulumi.Input[str],
        groq_api_key: pulumi.Input[str],
        instance_type: str = "t3.small",
        opts: pulumi.ResourceOptions = None,
    ):
        """Create EC2 Airflow infrastructure.

        Args:
            name: Resource name prefix.
            vpc_id: VPC ID for security group.
            subnet_id: Public subnet ID for the instance.
            db_security_group_id: RDS security group ID (for DB access rule).
            ssh_key_name: EC2 key pair name for SSH access.
            database_url: PostgreSQL connection string (secret).
            twitterx_apikey: RapidAPI key for Twitter API.
            anthropic_api_key: Anthropic API key for Claude.
            gemini_api_key: Google AI API key for Gemini.
            groq_api_key: Groq API key for Meta/Llama.
            instance_type: EC2 instance type (default: t3.small).
            opts: Pulumi resource options.
        """
        super().__init__("custom:compute:Ec2Airflow", name, None, opts)

        # =====================================================================
        # Security Group - Airflow Access
        # =====================================================================
        self.security_group = aws.ec2.SecurityGroup(
            f"{name}-airflow-sg",
            vpc_id=vpc_id,
            description="Security group for Airflow EC2 instance",
            ingress=[
                # SSH access (restrict to your IP in production)
                aws.ec2.SecurityGroupIngressArgs(
                    protocol="tcp",
                    from_port=22,
                    to_port=22,
                    cidr_blocks=["0.0.0.0/0"],  # TODO: Restrict in production
                    description="SSH access",
                ),
                # HTTP (for certbot ACME challenge and redirect to HTTPS)
                aws.ec2.SecurityGroupIngressArgs(
                    protocol="tcp",
                    from_port=80,
                    to_port=80,
                    cidr_blocks=["0.0.0.0/0"],
                    description="HTTP for ACME challenge",
                ),
                # HTTPS (Airflow web UI)
                aws.ec2.SecurityGroupIngressArgs(
                    protocol="tcp",
                    from_port=443,
                    to_port=443,
                    cidr_blocks=["0.0.0.0/0"],
                    description="HTTPS for Airflow UI",
                ),
            ],
            egress=[
                # Allow all outbound traffic (for API calls, package downloads)
                aws.ec2.SecurityGroupEgressArgs(
                    protocol="-1",
                    from_port=0,
                    to_port=0,
                    cidr_blocks=["0.0.0.0/0"],
                    description="All outbound traffic",
                ),
            ],
            tags={"Name": f"{name}-airflow-sg"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # Database Access Rule
        # =====================================================================
        # Allow Airflow EC2 to connect to RDS PostgreSQL
        self.db_ingress_rule = aws.ec2.SecurityGroupRule(
            f"{name}-airflow-db-access",
            type="ingress",
            from_port=5432,
            to_port=5432,
            protocol="tcp",
            source_security_group_id=self.security_group.id,
            security_group_id=db_security_group_id,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # IAM Role - CloudWatch Logs & SSM
        # =====================================================================
        assume_role_policy = """{
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "ec2.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }]
        }"""

        self.role = aws.iam.Role(
            f"{name}-airflow-role",
            assume_role_policy=assume_role_policy,
            tags={"Name": f"{name}-airflow-role"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Attach CloudWatch Logs policy
        aws.iam.RolePolicyAttachment(
            f"{name}-airflow-cloudwatch",
            role=self.role.name,
            policy_arn="arn:aws:iam::aws:policy/CloudWatchLogsFullAccess",
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Attach SSM policy for Session Manager (alternative to SSH)
        aws.iam.RolePolicyAttachment(
            f"{name}-airflow-ssm",
            role=self.role.name,
            policy_arn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Instance profile (required to attach role to EC2)
        self.instance_profile = aws.iam.InstanceProfile(
            f"{name}-airflow-profile",
            role=self.role.name,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # User Data Script - Instance Initialization (Prerequisites Only)
        # =====================================================================
        # Note: This only installs prerequisites. Application deployment is done
        # via deploy.sh which syncs code, configures nginx/certbot, and starts
        # Airflow with proper credentials.
        #
        # Deployment workflow:
        #   1. pulumi up           -> Creates EC2 with Docker/nginx/certbot
        #   2. ./deploy.sh <ip> <key> -> Deploys application code and starts Airflow
        #
        # The secrets are NOT written to the instance here - they are synced
        # by deploy.sh from the local .env file, which allows:
        #   - Credential rotation without Pulumi changes
        #   - Domain configuration flexibility
        #   - Secure credential management (not in Pulumi state)
        user_data = """#!/bin/bash
set -e

# Update system
dnf update -y

# Install Docker
dnf install -y docker
systemctl enable docker
systemctl start docker
usermod -aG docker ec2-user

# Install docker-compose
curl -L "https://github.com/docker/compose/releases/download/v2.24.0/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

# Install nginx (for reverse proxy)
dnf install -y nginx
systemctl enable nginx

# Install certbot for Let's Encrypt
dnf install -y certbot python3-certbot-nginx

# Install envsubst (for nginx template processing) and rsync (for deployments)
dnf install -y gettext rsync

# Create Airflow directory structure
mkdir -p /opt/airflow/{dags,tasks,packages,logs,certs,audiences,nginx}
mkdir -p /var/www/certbot
chown -R ec2-user:ec2-user /opt/airflow /var/www/certbot

echo "EC2 initialization complete."
echo "Deploy application with: ./deploy.sh <elastic-ip> <ssh-key-name>"
"""
        # Note: We no longer use pulumi.Output.all() since we don't need secrets
        # in user_data. Secrets are synced via deploy.sh from local .env file.

        # =====================================================================
        # AMI - Amazon Linux 2023
        # =====================================================================
        ami = aws.ec2.get_ami(
            most_recent=True,
            owners=["amazon"],
            filters=[
                aws.ec2.GetAmiFilterArgs(
                    name="name",
                    values=["al2023-ami-*-x86_64"],
                ),
                aws.ec2.GetAmiFilterArgs(
                    name="virtualization-type",
                    values=["hvm"],
                ),
            ],
        )

        # =====================================================================
        # EC2 Instance
        # =====================================================================
        self.instance = aws.ec2.Instance(
            f"{name}-airflow",
            ami=ami.id,
            instance_type=instance_type,
            key_name=ssh_key_name,
            subnet_id=subnet_id,
            vpc_security_group_ids=[self.security_group.id],
            iam_instance_profile=self.instance_profile.name,
            user_data=user_data,
            root_block_device=aws.ec2.InstanceRootBlockDeviceArgs(
                volume_size=30,  # 30 GB for Airflow + Docker images (AMI minimum)
                volume_type="gp3",
                delete_on_termination=True,
            ),
            tags={
                "Name": f"{name}-airflow",
                "Project": "profile-scorer-saas",
            },
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # Elastic IP - Static public IP for DNS
        # =====================================================================
        self.eip = aws.ec2.Eip(
            f"{name}-airflow-eip",
            instance=self.instance.id,
            domain="vpc",
            tags={"Name": f"{name}-airflow-eip"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # Outputs
        # =====================================================================
        self.register_outputs({
            "instance_id": self.instance.id,
            "public_ip": self.eip.public_ip,
            "security_group_id": self.security_group.id,
        })
