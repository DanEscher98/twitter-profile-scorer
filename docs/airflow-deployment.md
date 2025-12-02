# Airflow EC2 Deployment Guide

This guide covers deploying and configuring the Airflow EC2 instance for the profile scoring pipeline.

## Prerequisites

1. AWS account with appropriate permissions
2. SSH key pair created in AWS EC2
3. GoDaddy domain access for `ateliertech.xyz`
4. Pulumi CLI installed and configured

## Step 1: Create SSH Key Pair

```bash
# Create key pair in AWS
aws ec2 create-key-pair \
  --key-name profile-scorer-airflow \
  --query 'KeyMaterial' \
  --output text > ~/.ssh/profile-scorer-airflow.pem

chmod 600 ~/.ssh/profile-scorer-airflow.pem
```

## Step 2: Deploy EC2 Instance

```bash
# Set environment variables
export AIRFLOW_SSH_KEY_NAME=profile-scorer-airflow

# Deploy infrastructure
cd infra
uv run pulumi up

# Get the Elastic IP
pulumi stack output airflow_public_ip
```

Note the Elastic IP address - you'll need it for DNS configuration.

## Step 3: Configure DNS (GoDaddy)

1. Log in to [GoDaddy DNS Management](https://dcc.godaddy.com/manage/ateliertech.xyz/dns)
2. Add an A record:
   - **Type:** A
   - **Name:** profile-scorer.admin
   - **Value:** `<elastic-ip-from-step-2>`
   - **TTL:** 600 (10 minutes)
3. Wait for DNS propagation (5-10 minutes)

Verify DNS:
```bash
dig profile-scorer.admin.ateliertech.xyz
```

## Step 4: SSH into Instance

```bash
# Get SSH command from Pulumi
pulumi stack output airflow_ssh_command

# Or manually:
ssh -i ~/.ssh/profile-scorer-airflow.pem ec2-user@<elastic-ip>
```

## Step 5: Initialize Airflow

On the EC2 instance:

```bash
# Run setup script
sudo /opt/airflow/setup.sh

# This will:
# 1. Initialize Airflow database (SQLite)
# 2. Create admin user (admin/admin)
# 3. Start webserver and scheduler containers

# Verify containers are running
docker ps
```

## Step 6: Configure HTTPS with Certbot

```bash
# Comment out the HTTPS server block temporarily (certbot needs port 80)
sudo nano /etc/nginx/conf.d/airflow.conf
# Comment lines 12-38 (the 443 server block)

# Start nginx
sudo systemctl start nginx

# Run certbot
sudo certbot --nginx -d profile-scorer.admin.ateliertech.xyz

# Follow prompts:
# - Enter email for renewal notices
# - Agree to terms
# - Choose redirect HTTP to HTTPS (option 2)

# Certbot will modify nginx config automatically
# Verify:
sudo nginx -t
sudo systemctl reload nginx
```

## Step 7: Access Airflow

Open in browser: https://profile-scorer.admin.ateliertech.xyz

Default credentials:
- **Username:** admin
- **Password:** admin

**IMPORTANT:** Change the admin password immediately after first login!

## Step 8: Deploy DAGs

The DAGs are located in `airflow/dags/`. To deploy:

```bash
# From local machine - copy DAGs to EC2
scp -i ~/.ssh/profile-scorer-airflow.pem \
  airflow/dags/*.py \
  ec2-user@<elastic-ip>:/opt/airflow/dags/

# Copy audience configs
scp -i ~/.ssh/profile-scorer-airflow.pem \
  airflow/dags/audiences/*.json \
  ec2-user@<elastic-ip>:/opt/airflow/audiences/

# Copy RDS certificate
scp -i ~/.ssh/profile-scorer-airflow.pem \
  certs/aws-rds-global-bundle.pem \
  ec2-user@<elastic-ip>:/opt/airflow/certs/
```

The scheduler will automatically detect new DAGs within 30 seconds.

## Step 9: Install Python Dependencies

SSH into the instance and install dependencies in the Airflow container:

```bash
# Enter the webserver container
docker exec -it profile-scorer-airflow-airflow-webserver-1 bash

# Install dependencies
pip install \
  sqlmodel \
  pydantic \
  httpx \
  structlog \
  langchain-anthropic \
  langchain-google-genai \
  langchain-groq
```

For a permanent solution, create a custom Dockerfile:

```dockerfile
FROM apache/airflow:3.0.0-python3.12

USER airflow
RUN pip install --no-cache-dir \
  sqlmodel \
  pydantic \
  httpx \
  structlog \
  langchain-anthropic \
  langchain-google-genai \
  langchain-groq
```

## SSL Certificate Renewal

Certbot automatically renews certificates. Verify the renewal timer:

```bash
sudo systemctl status certbot-renew.timer
```

Manual renewal (if needed):
```bash
sudo certbot renew --dry-run
```

## Troubleshooting

### Check Airflow Logs
```bash
docker logs profile-scorer-airflow-airflow-webserver-1
docker logs profile-scorer-airflow-airflow-scheduler-1
```

### Restart Services
```bash
cd /opt/airflow
docker-compose restart
```

### Check Database Connection
```bash
docker exec -it profile-scorer-airflow-airflow-webserver-1 python -c "
from scorer_db import get_session
with get_session() as s:
    print('DB connection OK')
"
```

### View nginx Logs
```bash
sudo tail -f /var/log/nginx/error.log
sudo tail -f /var/log/nginx/access.log
```

## Security Considerations

1. **Change default passwords:**
   - Airflow admin password
   - AIRFLOW__WEBSERVER__SECRET_KEY in docker-compose.yaml

2. **Restrict SSH access:**
   - Edit `/etc/nginx/conf.d/airflow.conf` security group to your IP only

3. **Enable MFA:**
   - Consider enabling Airflow's RBAC with LDAP/OAuth

4. **Backup:**
   - Airflow metadata: `/opt/airflow/airflow.db`
   - DAGs: `/opt/airflow/dags/`
   - Configs: `/opt/airflow/.env`

## Migration Checklist

After deploying Airflow and verifying DAGs work correctly:

- [ ] Airflow UI accessible at https://profile-scorer.admin.ateliertech.xyz
- [ ] `profile_scoring` DAG runs successfully
- [ ] `keyword_stats_update` DAG runs successfully
- [ ] Database records are being created/updated
- [ ] Disable Lambda orchestrator EventBridge schedule
- [ ] Monitor for 24-48 hours
- [ ] Optional: Remove Lambda resources after stable operation
