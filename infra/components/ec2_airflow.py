"""
EC2 Airflow Component - Airflow Server Infrastructure

This module creates an EC2 instance for running Apache Airflow with:
- t3.small instance (2 vCPU, 2GB RAM) - sufficient for LocalExecutor
- Public subnet with Elastic IP for HTTPS access
- Security group allowing SSH, HTTP, HTTPS
- IAM role for CloudWatch logging

Deployment Notes:
-----------------
1. The instance uses Amazon Linux 2023 AMI
2. User data script installs Docker and docker-compose
3. Airflow is deployed via docker-compose (LocalExecutor mode)
4. HTTPS is handled by nginx + certbot (see setup scripts)

SSH Access:
-----------
Use the SSH key specified in ssh_key_name parameter:
  ssh -i ~/.ssh/<key>.pem ec2-user@<elastic-ip>

Post-Deployment:
----------------
1. SSH into instance
2. Run /opt/airflow/setup.sh to initialize Airflow
3. Configure nginx and certbot for HTTPS
4. Access Airflow at https://profile-scorer.admin.ateliertech.xyz
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
        # User Data Script - Instance Initialization
        # =====================================================================
        user_data = pulumi.Output.all(
            database_url,
            twitterx_apikey,
            anthropic_api_key,
            gemini_api_key,
            groq_api_key,
        ).apply(lambda args: f"""#!/bin/bash
set -e

# Update system
yum update -y

# Install Docker
yum install -y docker
systemctl enable docker
systemctl start docker
usermod -aG docker ec2-user

# Install docker-compose
curl -L "https://github.com/docker/compose/releases/download/v2.24.0/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

# Install nginx (for reverse proxy)
yum install -y nginx
systemctl enable nginx

# Install certbot for Let's Encrypt
yum install -y certbot python3-certbot-nginx

# Create Airflow directory structure
mkdir -p /opt/airflow/{{dags,logs,config,certs,audiences}}
chown -R ec2-user:ec2-user /opt/airflow

# Write environment file (secrets)
cat > /opt/airflow/.env << 'ENVEOF'
DATABASE_URL={args[0]}
TWITTERX_APIKEY={args[1]}
ANTHROPIC_API_KEY={args[2]}
GEMINI_API_KEY={args[3]}
GROQ_API_KEY={args[4]}
AIRFLOW_UID=1000
ENVEOF
chmod 600 /opt/airflow/.env

# Create docker-compose.yaml for Airflow
cat > /opt/airflow/docker-compose.yaml << 'COMPOSEEOF'
version: '3.8'

x-airflow-common:
  &airflow-common
  image: apache/airflow:3.0.0-python3.12
  environment:
    &airflow-common-env
    AIRFLOW__CORE__EXECUTOR: LocalExecutor
    AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: sqlite:////opt/airflow/airflow.db
    AIRFLOW__CORE__FERNET_KEY: ''
    AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION: 'false'
    AIRFLOW__CORE__LOAD_EXAMPLES: 'false'
    AIRFLOW__WEBSERVER__SECRET_KEY: 'your-secret-key-change-me'
    AIRFLOW__WEBSERVER__EXPOSE_CONFIG: 'false'
    # Application secrets (passed from host .env)
    DATABASE_URL: ${{DATABASE_URL}}
    TWITTERX_APIKEY: ${{TWITTERX_APIKEY}}
    ANTHROPIC_API_KEY: ${{ANTHROPIC_API_KEY}}
    GEMINI_API_KEY: ${{GEMINI_API_KEY}}
    GROQ_API_KEY: ${{GROQ_API_KEY}}
  volumes:
    - /opt/airflow/dags:/opt/airflow/dags
    - /opt/airflow/logs:/opt/airflow/logs
    - /opt/airflow/config:/opt/airflow/config
    - /opt/airflow/certs:/opt/airflow/certs
    - /opt/airflow/audiences:/opt/airflow/audiences
  user: "${{AIRFLOW_UID:-1000}}:0"

services:
  airflow-init:
    <<: *airflow-common
    command: >
      bash -c "
        airflow db init &&
        airflow users create
          --username admin
          --password admin
          --firstname Admin
          --lastname User
          --role Admin
          --email admin@example.com
      "
    restart: "no"

  airflow-webserver:
    <<: *airflow-common
    command: webserver
    ports:
      - "8080:8080"
    healthcheck:
      test: ["CMD", "curl", "--fail", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 5
    restart: always

  airflow-scheduler:
    <<: *airflow-common
    command: scheduler
    healthcheck:
      test: ["CMD", "airflow", "jobs", "check", "--job-type", "SchedulerJob", "--hostname", "${{HOSTNAME}}"]
      interval: 30s
      timeout: 10s
      retries: 5
    restart: always
COMPOSEEOF

# Create setup script
cat > /opt/airflow/setup.sh << 'SETUPEOF'
#!/bin/bash
set -e
cd /opt/airflow

echo "Starting Airflow initialization..."
docker-compose up airflow-init

echo "Starting Airflow services..."
docker-compose up -d airflow-webserver airflow-scheduler

echo "Airflow is starting. Access at http://localhost:8080"
echo "Default credentials: admin / admin"
echo ""
echo "Next steps:"
echo "1. Configure nginx reverse proxy: /etc/nginx/conf.d/airflow.conf"
echo "2. Run certbot: sudo certbot --nginx -d profile-scorer.admin.ateliertech.xyz"
SETUPEOF
chmod +x /opt/airflow/setup.sh

# Create nginx config template
cat > /etc/nginx/conf.d/airflow.conf << 'NGINXEOF'
server {{
    listen 80;
    server_name profile-scorer.admin.ateliertech.xyz;

    location / {{
        return 301 https://$host$request_uri;
    }}

    location /.well-known/acme-challenge/ {{
        root /var/www/html;
    }}
}}

server {{
    listen 443 ssl http2;
    server_name profile-scorer.admin.ateliertech.xyz;

    # SSL certificates (will be managed by certbot)
    ssl_certificate /etc/letsencrypt/live/profile-scorer.admin.ateliertech.xyz/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/profile-scorer.admin.ateliertech.xyz/privkey.pem;

    # Strong SSL settings
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256;

    # Proxy to Airflow webserver
    location / {{
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support for Airflow UI
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }}
}}
NGINXEOF

echo "EC2 initialization complete. Run /opt/airflow/setup.sh after DNS is configured."
""")

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
                volume_size=20,  # 20 GB for Airflow + Docker images
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
