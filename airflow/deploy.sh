#!/bin/bash
# Deploy Airflow to EC2 instance
#
# Prerequisites:
# 1. EC2 instance deployed via Pulumi with AIRFLOW_SSH_KEY_NAME set
# 2. SSH key available at ~/.ssh/${SSH_KEY}.pem
# 3. EC2 Elastic IP available (get from: pulumi stack output airflow_public_ip)
#
# Usage:
#   ./deploy.sh <elastic-ip> <ssh-key-name>
#   ./deploy.sh 54.123.45.67 profile-scorer-airflow

set -e

# Check arguments
if [ $# -lt 2 ]; then
    echo "Usage: $0 <elastic-ip> <ssh-key-name>"
    echo "Example: $0 54.123.45.67 profile-scorer-airflow"
    exit 1
fi

ELASTIC_IP=$1
SSH_KEY_NAME=$2
SSH_KEY=~/.ssh/${SSH_KEY_NAME}.pem
REMOTE_USER=ec2-user
REMOTE_DIR=/opt/airflow

# Verify SSH key exists
if [ ! -f "$SSH_KEY" ]; then
    echo "Error: SSH key not found at $SSH_KEY"
    exit 1
fi

echo "=== Deploying Airflow to $ELASTIC_IP ==="

# Create directories on remote
echo "Creating directories..."
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ${REMOTE_USER}@${ELASTIC_IP} \
    "sudo mkdir -p ${REMOTE_DIR}/{dags,tasks,packages,logs,certs,audiences} && sudo chown -R ec2-user:ec2-user ${REMOTE_DIR}"

# Sync files to EC2
echo "Syncing project files..."
rsync -avz --progress \
    -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=no" \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude '.pytest_cache' \
    --exclude '*.pyc' \
    --exclude '.git' \
    --exclude 'tests' \
    --exclude 'logs' \
    ./ ${REMOTE_USER}@${ELASTIC_IP}:${REMOTE_DIR}/

# Sync .env file (ensure secrets are available)
if [ -f ".env" ]; then
    echo "Syncing .env file..."
    scp -i "$SSH_KEY" -o StrictHostKeyChecking=no .env ${REMOTE_USER}@${ELASTIC_IP}:${REMOTE_DIR}/.env
fi

# Build and start Docker containers
echo "Building and starting Airflow containers..."
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ${REMOTE_USER}@${ELASTIC_IP} << 'ENDSSH'
cd /opt/airflow

# Build Docker image
echo "Building Docker image..."
docker-compose build

# Stop existing containers
echo "Stopping existing containers..."
docker-compose down || true

# Initialize Airflow (if not already initialized)
echo "Initializing Airflow..."
docker-compose up airflow-init

# Start services
echo "Starting Airflow services..."
docker-compose up -d airflow-webserver airflow-scheduler

echo "Waiting for services to be healthy..."
sleep 30

# Check status
docker-compose ps

echo ""
echo "=== Deployment Complete ==="
echo "Access Airflow at: http://$HOSTNAME:8080"
echo "Default credentials: admin / admin"
ENDSSH

echo ""
echo "=== Deployment Complete ==="
echo "Access Airflow at: http://${ELASTIC_IP}:8080"
echo "For HTTPS setup, see the nginx configuration in ec2_airflow.py"
