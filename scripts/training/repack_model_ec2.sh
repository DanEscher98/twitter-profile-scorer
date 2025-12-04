#!/bin/bash
# Repack model.tar.gz on EC2 to fix directory structure for TGI
#
# Usage: ./repack_model_ec2.sh profile-scorer-mistral-20251204-110901
#
# This script runs ON the EC2 instance (fast S3 access within AWS)

set -euo pipefail

MODEL_NAME="${1:-}"
BUCKET="profile-scorer-sagemaker-dev"

if [ -z "$MODEL_NAME" ]; then
    echo "Usage: $0 <model-name>"
    echo "Example: $0 profile-scorer-mistral-20251204-110901"
    exit 1
fi

S3_URI="s3://${BUCKET}/models/${MODEL_NAME}/output/model.tar.gz"
WORK_DIR="/tmp/repack_${MODEL_NAME}"
BACKUP_KEY="models/${MODEL_NAME}/output/model.tar.gz.backup"

echo "============================================================"
echo "Repacking model: ${MODEL_NAME}"
echo "S3 URI: ${S3_URI}"
echo "Work dir: ${WORK_DIR}"
echo "============================================================"

# Create work directory
rm -rf "${WORK_DIR}"
mkdir -p "${WORK_DIR}"
cd "${WORK_DIR}"

# Backup original
echo ""
echo "[1/5] Creating backup..."
aws s3 cp "${S3_URI}" "s3://${BUCKET}/${BACKUP_KEY}" --only-show-errors
echo "Backup created at s3://${BUCKET}/${BACKUP_KEY}"

# Download with progress
echo ""
echo "[2/5] Downloading model.tar.gz..."
aws s3 cp "${S3_URI}" original.tar.gz

# Extract
echo ""
echo "[3/5] Extracting..."
mkdir -p extracted
tar -xzf original.tar.gz -C extracted

# Find model files
echo ""
echo "[4/5] Finding model files..."
if [ -d "extracted/merged" ]; then
    MODEL_DIR="extracted/merged"
    echo "Found model files in: merged/"
elif [ -f "extracted/model.tar.gz" ]; then
    echo "Found nested model.tar.gz, extracting..."
    mkdir -p nested
    tar -xzf extracted/model.tar.gz -C nested
    if [ -d "nested/merged" ]; then
        MODEL_DIR="nested/merged"
    else
        MODEL_DIR="nested"
    fi
else
    MODEL_DIR="extracted"
fi

# Verify config.json exists
if [ ! -f "${MODEL_DIR}/config.json" ]; then
    echo "ERROR: Could not find config.json in ${MODEL_DIR}"
    echo "Contents:"
    ls -la "${MODEL_DIR}/"
    exit 1
fi

echo "Model files:"
ls -lh "${MODEL_DIR}/"

# Create new tarball with files at root
echo ""
echo "[5/5] Creating fixed tarball..."
cd "${MODEL_DIR}"
tar -czvf "${WORK_DIR}/model_fixed.tar.gz" ./*

# Upload
echo ""
echo "Uploading fixed tarball..."
aws s3 cp "${WORK_DIR}/model_fixed.tar.gz" "${S3_URI}"

# Cleanup
echo ""
echo "Cleaning up..."
rm -rf "${WORK_DIR}"

echo ""
echo "============================================================"
echo "SUCCESS! Model tarball has been repacked."
echo "============================================================"
echo ""
echo "You can now deploy with:"
echo "  just deploy-llm ${MODEL_NAME}"
echo ""
echo "If something went wrong, restore backup with:"
 echo "  aws s3 cp s3://${BUCKET}/${BACKUP_KEY} ${S3_URI}"
