#!/usr/bin/env python3
"""SageMaker LLM training and deployment CLI.

This script provides a unified interface for training and deploying
custom LLM models on SageMaker.

Commands:
    train   - Upload training data and start SageMaker training job
    deploy  - Deploy a trained model to SageMaker endpoint
    status  - Check training job status
    list    - List available models in S3
    delete  - Delete SageMaker endpoint (to save costs)
    info    - Show LLM infrastructure status
    toggle  - Toggle endpoint on/off

Usage:
    # Train a new model
    just train-llm training_data.jsonl

    # Deploy latest model
    just deploy-llm

    # Deploy specific model
    just deploy-llm profile-scorer-mistral-20241204-143000

    # Check training status
    just llm-status

    # Toggle endpoint on/off
    just llm-toggle on
    just llm-toggle off

    # Delete endpoint when not in use
    just llm-delete
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


def get_project_root() -> Path:
    """Get the project root directory."""
    # This script is in scripts/training/sagemaker_cli.py
    return Path(__file__).parent.parent.parent


def get_config() -> dict:
    """Get SageMaker configuration from environment or Pulumi."""
    bucket = os.environ.get("SAGEMAKER_BUCKET")
    role_arn = os.environ.get("SAGEMAKER_ROLE_ARN")
    region = os.environ.get("AWS_REGION", "us-east-2")

    if not bucket or not role_arn:
        # Try to get from Pulumi
        import subprocess

        try:
            infra_dir = get_project_root() / "infra"
            result = subprocess.run(
                ["uv", "run", "pulumi", "stack", "output", "--json"],
                cwd=infra_dir,
                capture_output=True,
                text=True,
                check=True,
            )
            outputs = json.loads(result.stdout)
            bucket = bucket or outputs.get("sagemaker_bucket")
            role_arn = role_arn or outputs.get("sagemaker_role_arn")
        except Exception as e:
            print(f"Warning: Could not get Pulumi outputs: {e}")

    if not bucket or not role_arn:
        print("Error: Missing SageMaker configuration.")
        print("Either set SAGEMAKER_BUCKET and SAGEMAKER_ROLE_ARN environment variables,")
        print("or ensure SageMaker is enabled in Pulumi (just llm-setup)")
        sys.exit(1)

    return {
        "bucket": bucket,
        "role_arn": role_arn,
        "region": region,
        "endpoint_name": "profile-scorer-profile-scorer-endpoint",
    }


def upload_training_data(data_path: str, config: dict) -> str:
    """Upload training data to S3.

    Args:
        data_path: Path to JSONL training data file.
        config: SageMaker configuration.

    Returns:
        S3 URI of uploaded data.
    """
    s3 = boto3.client("s3", region_name=config["region"])
    bucket = config["bucket"]

    # If it's a CSV, convert to JSONL first
    if data_path.endswith(".csv"):
        print(f"Converting {data_path} to JSONL...")
        # Import from airflow submodule
        sys.path.insert(0, str(get_project_root() / "airflow" / "scripts" / "training"))
        from convert_csv_to_jsonl import convert_csv_to_jsonl

        jsonl_path = data_path.replace(".csv", "_train.jsonl")
        convert_csv_to_jsonl(data_path, jsonl_path.replace("_train.jsonl", ".jsonl"))
        data_path = jsonl_path

    file_name = Path(data_path).name
    s3_key = f"training/{file_name}"

    print(f"Uploading {data_path} to s3://{bucket}/{s3_key}...")
    s3.upload_file(data_path, bucket, s3_key)
    print(f"Uploaded to s3://{bucket}/{s3_key}")

    return f"s3://{bucket}/{s3_key}"


def get_training_script() -> str:
    """Generate the training script to run inside SageMaker."""
    return '''#!/usr/bin/env python3
"""SageMaker training script for Mistral-7B fine-tuning with QLoRA.

Logs are written to stdout and captured by CloudWatch at:
/aws/sagemaker/TrainingJobs/<job-name>
"""

import json
import logging
import os
import subprocess
import sys
import tarfile
from pathlib import Path

# Configure logging to stdout (CloudWatch captures this)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# Upgrade tokenizers to fix Mistral tokenizer compatibility (must be <0.19 for transformers 4.36)
logger.info("Upgrading tokenizers library for Mistral compatibility...")
subprocess.run([sys.executable, "-m", "pip", "install", "tokenizers>=0.15.0,<0.19.0"], check=True)
logger.info("Tokenizers upgraded successfully")


def main():
    logger.info("=" * 60)
    logger.info("Starting SageMaker training job")
    logger.info("=" * 60)

    # SageMaker paths
    model_dir = os.environ.get("SM_MODEL_DIR", "/opt/ml/model")
    train_dir = os.environ.get("SM_CHANNEL_TRAINING", "/opt/ml/input/data/training")

    logger.info(f"Model output dir: {model_dir}")
    logger.info(f"Training data dir: {train_dir}")

    # List directory contents for debugging
    logger.info(f"Contents of {train_dir}:")
    for f in Path(train_dir).iterdir():
        logger.info(f"  - {f.name} ({f.stat().st_size} bytes)")

    # Find training file
    train_files = list(Path(train_dir).glob("*.jsonl"))
    if not train_files:
        logger.error(f"No .jsonl files found in {train_dir}")
        raise FileNotFoundError(f"No .jsonl files found in {train_dir}")
    train_file = str(train_files[0])
    logger.info(f"Training file: {train_file}")

    # Import ML libraries (after logging setup)
    logger.info("Importing ML libraries...")
    import torch
    from datasets import load_dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        TrainingArguments,
    )
    from trl import SFTTrainer

    logger.info(f"PyTorch version: {torch.__version__}")
    logger.info(f"CUDA available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        logger.info(f"CUDA device count: {torch.cuda.device_count()}")
        for i in range(torch.cuda.device_count()):
            logger.info(f"  GPU {i}: {torch.cuda.get_device_name(i)}")

    # Load dataset
    logger.info("Loading dataset...")
    dataset = load_dataset("json", data_files=train_file, split="train")
    logger.info(f"Loaded {len(dataset)} training examples")
    logger.info(f"Dataset columns: {dataset.column_names}")

    # Configure 4-bit quantization
    logger.info("Configuring 4-bit quantization...")
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )

    # Load model and tokenizer
    model_name = "mistralai/Mistral-7B-Instruct-v0.2"
    logger.info(f"Loading tokenizer: {model_name}")

    # Use use_fast=False to avoid tokenizer compatibility issues
    tokenizer = AutoTokenizer.from_pretrained(model_name, use_fast=False)
    tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"
    logger.info("Tokenizer loaded successfully")

    logger.info(f"Loading model: {model_name}")
    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=True,
    )
    logger.info("Model loaded, preparing for k-bit training...")
    model = prepare_model_for_kbit_training(model)

    # Configure LoRA
    logger.info("Configuring LoRA adapters...")
    lora_config = LoraConfig(
        r=16,
        lora_alpha=32,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # Format dataset for Mistral Instruct
    logger.info("Formatting dataset for Mistral Instruct format...")
    def formatting_function(examples):
        texts = []
        for instruction, output in zip(examples["instruction"], examples["output"]):
            text = f"<s>[INST] {instruction} [/INST] {output}</s>"
            texts.append(text)
        return {"text": texts}

    dataset = dataset.map(formatting_function, batched=True, remove_columns=dataset.column_names)
    logger.info(f"Dataset formatted, {len(dataset)} examples ready")

    # Training arguments
    logger.info("Setting up training arguments...")
    training_args = TrainingArguments(
        output_dir="/opt/ml/checkpoints",
        per_device_train_batch_size=2,
        gradient_accumulation_steps=4,
        warmup_steps=10,
        num_train_epochs=3,
        learning_rate=2e-4,
        fp16=True,
        logging_steps=10,
        save_strategy="epoch",
        save_total_limit=2,
        optim="paged_adamw_8bit",
        seed=42,
        report_to="none",  # Disable wandb/tensorboard
    )

    # Train
    logger.info("Creating SFTTrainer...")
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=2048,
        args=training_args,
    )

    logger.info("=" * 60)
    logger.info("Starting training...")
    logger.info("=" * 60)
    trainer.train()
    logger.info("=" * 60)
    logger.info("Training complete!")
    logger.info("=" * 60)

    # Merge LoRA adapters with base model
    logger.info("Merging LoRA adapters with base model...")
    merged_model = model.merge_and_unload()

    # Save merged model
    merged_path = Path(model_dir) / "merged"
    merged_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"Saving merged model to: {merged_path}")
    merged_model.save_pretrained(str(merged_path))
    tokenizer.save_pretrained(str(merged_path))
    logger.info("Merged model saved successfully")

    # Create model.tar.gz for SageMaker deployment
    tar_path = Path(model_dir) / "model.tar.gz"
    logger.info(f"Creating model archive: {tar_path}")
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(merged_path, arcname=".")
    logger.info(f"Model archive created: {tar_path}")


if __name__ == "__main__":
    main()
'''


def start_training(config: dict, wait: bool = False) -> str:
    """Start a SageMaker training job.

    Args:
        config: SageMaker configuration.
        wait: Whether to wait for training to complete.

    Returns:
        Training job name.
    """
    sagemaker = boto3.client("sagemaker", region_name=config["region"])
    s3 = boto3.client("s3", region_name=config["region"])

    # Generate job name with timestamp
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    job_name = f"profile-scorer-mistral-{timestamp}"

    # Upload training script as tarball (required by SageMaker)
    import io
    import tarfile

    script_content = get_training_script()
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
        script_bytes = script_content.encode("utf-8")
        tarinfo = tarfile.TarInfo(name="train.py")
        tarinfo.size = len(script_bytes)
        tar.addfile(tarinfo, io.BytesIO(script_bytes))
    tar_buffer.seek(0)

    s3.put_object(
        Bucket=config["bucket"],
        Key="training/sourcedir.tar.gz",
        Body=tar_buffer.getvalue(),
    )
    print(f"Uploaded training script to s3://{config['bucket']}/training/sourcedir.tar.gz")

    # HuggingFace PyTorch training container
    image_uri = f"763104351884.dkr.ecr.{config['region']}.amazonaws.com/huggingface-pytorch-training:2.1.0-transformers4.36.0-gpu-py310-cu121-ubuntu20.04"

    # Training job configuration
    training_params = {
        "TrainingJobName": job_name,
        "RoleArn": config["role_arn"],
        "AlgorithmSpecification": {
            "TrainingImage": image_uri,
            "TrainingInputMode": "File",
        },
        "HyperParameters": {
            "sagemaker_program": "train.py",
            "sagemaker_submit_directory": f"s3://{config['bucket']}/training/sourcedir.tar.gz",
        },
        "InputDataConfig": [
            {
                "ChannelName": "training",
                "DataSource": {
                    "S3DataSource": {
                        "S3DataType": "S3Prefix",
                        "S3Uri": f"s3://{config['bucket']}/training/",
                        "S3DataDistributionType": "FullyReplicated",
                    }
                },
                "ContentType": "application/jsonlines",
            }
        ],
        "OutputDataConfig": {
            "S3OutputPath": f"s3://{config['bucket']}/models/",
        },
        "ResourceConfig": {
            "InstanceType": "ml.g4dn.12xlarge",  # 4x T4 GPUs, 48GB VRAM
            "InstanceCount": 1,
            "VolumeSizeInGB": 100,
        },
        "StoppingCondition": {
            "MaxRuntimeInSeconds": 7200,
        },
        # Note: Spot training disabled - requires quota request
        # "EnableManagedSpotTraining": True,
        # "MaxWaitTimeInSeconds": 10800,
    }

    response = sagemaker.create_training_job(**training_params)
    print(f"\nTraining job started: {job_name}")
    print(f"ARN: {response['TrainingJobArn']}")
    print(f"\nMonitor at: https://{config['region']}.console.aws.amazon.com/sagemaker/home?region={config['region']}#/jobs/{job_name}")

    if wait:
        wait_for_training(job_name, config)

    return job_name


def wait_for_training(job_name: str, config: dict) -> dict:
    """Wait for training job to complete.

    Args:
        job_name: SageMaker training job name.
        config: SageMaker configuration.

    Returns:
        Final training job description.
    """
    sagemaker = boto3.client("sagemaker", region_name=config["region"])

    print(f"\nWaiting for training job: {job_name}")

    while True:
        response = sagemaker.describe_training_job(TrainingJobName=job_name)
        status = response["TrainingJobStatus"]
        secondary_status = response.get("SecondaryStatus", "")

        print(f"[{datetime.now().strftime('%H:%M:%S')}] Status: {status} - {secondary_status}")

        if status in ["Completed", "Failed", "Stopped"]:
            break

        time.sleep(60)

    if status == "Completed":
        model_artifacts = response.get("ModelArtifacts", {}).get("S3ModelArtifacts", "")
        print(f"\n{'='*60}")
        print("TRAINING COMPLETED!")
        print(f"{'='*60}")
        print(f"Model artifacts: {model_artifacts}")
        print(f"\nTo deploy, run:")
        print(f"  just deploy-llm {job_name}")
        print(f"{'='*60}")
    else:
        failure_reason = response.get("FailureReason", "Unknown")
        print(f"\nTraining {status}: {failure_reason}")

    return response


def get_training_status(config: dict, job_name: str | None = None) -> None:
    """Check training job status.

    Args:
        config: SageMaker configuration.
        job_name: Specific job name, or None for latest.
    """
    sagemaker = boto3.client("sagemaker", region_name=config["region"])

    if not job_name:
        # Get latest training job
        response = sagemaker.list_training_jobs(
            NameContains="profile-scorer",
            SortBy="CreationTime",
            SortOrder="Descending",
            MaxResults=1,
        )
        if not response["TrainingJobSummaries"]:
            print("No training jobs found.")
            return
        job_name = response["TrainingJobSummaries"][0]["TrainingJobName"]

    response = sagemaker.describe_training_job(TrainingJobName=job_name)
    status = response["TrainingJobStatus"]
    secondary_status = response.get("SecondaryStatus", "")

    print(f"Job: {job_name}")
    print(f"Status: {status} - {secondary_status}")

    if status == "Completed":
        model_artifacts = response.get("ModelArtifacts", {}).get("S3ModelArtifacts", "")
        print(f"Model: {model_artifacts}")
    elif status == "Failed":
        print(f"Failure: {response.get('FailureReason', 'Unknown')}")


def list_models(config: dict) -> list[dict]:
    """List available trained models in S3.

    Args:
        config: SageMaker configuration.

    Returns:
        List of model info dicts.
    """
    s3 = boto3.client("s3", region_name=config["region"])

    response = s3.list_objects_v2(
        Bucket=config["bucket"],
        Prefix="models/",
        Delimiter="/",
    )

    models = []
    for prefix in response.get("CommonPrefixes", []):
        model_name = prefix["Prefix"].rstrip("/").split("/")[-1]

        # Check if model.tar.gz exists
        try:
            s3.head_object(
                Bucket=config["bucket"],
                Key=f"models/{model_name}/output/model.tar.gz",
            )
            models.append({
                "name": model_name,
                "s3_uri": f"s3://{config['bucket']}/models/{model_name}/output/model.tar.gz",
            })
        except ClientError:
            pass

    if not models:
        print("No trained models found.")
        print(f"Check s3://{config['bucket']}/models/")
    else:
        print("Available models:")
        for m in models:
            print(f"  - {m['name']}")
            print(f"    {m['s3_uri']}")

    return models


def deploy_model(config: dict, model_name: str | None = None) -> None:
    """Deploy a trained model to SageMaker endpoint.

    This uses Pulumi to deploy the endpoint properly.

    Args:
        config: SageMaker configuration.
        model_name: Model name (training job name) or None for latest.
    """
    import subprocess

    if not model_name:
        # Get latest completed training job
        sagemaker = boto3.client("sagemaker", region_name=config["region"])
        response = sagemaker.list_training_jobs(
            NameContains="profile-scorer",
            StatusEquals="Completed",
            SortBy="CreationTime",
            SortOrder="Descending",
            MaxResults=1,
        )
        if not response["TrainingJobSummaries"]:
            print("No completed training jobs found.")
            return
        model_name = response["TrainingJobSummaries"][0]["TrainingJobName"]

    model_s3_uri = f"s3://{config['bucket']}/models/{model_name}/output/model.tar.gz"

    # Verify model exists
    s3 = boto3.client("s3", region_name=config["region"])
    try:
        s3.head_object(
            Bucket=config["bucket"],
            Key=f"models/{model_name}/output/model.tar.gz",
        )
    except ClientError:
        print(f"Error: Model not found at {model_s3_uri}")
        print("Run 'just llm-list' to see available models.")
        return

    print(f"Deploying model: {model_name}")
    print(f"S3 URI: {model_s3_uri}")

    # Deploy via Pulumi
    infra_dir = get_project_root() / "infra"
    env = os.environ.copy()
    env["ENABLE_SAGEMAKER"] = "true"
    env["SAGEMAKER_MODEL_S3_URI"] = model_s3_uri

    print("\nRunning Pulumi to deploy endpoint...")
    result = subprocess.run(
        ["uv", "run", "pulumi", "up", "--yes"],
        cwd=infra_dir,
        env=env,
    )

    if result.returncode == 0:
        print(f"\n{'='*60}")
        print("DEPLOYMENT COMPLETE!")
        print(f"{'='*60}")
        print(f"Model: {model_name}")
        print(f"Endpoint: {config['endpoint_name']}")
        print(f"\nThe model is now accessible from Airflow using:")
        print('  model_alias="profile-scorer-v1"')
        print(f"\nTo turn off endpoint (save costs):")
        print("  just llm-toggle off")
        print(f"{'='*60}")
    else:
        print("\nDeployment failed. Check Pulumi output above.")


def delete_endpoint(config: dict) -> None:
    """Delete SageMaker endpoint to save costs.

    Args:
        config: SageMaker configuration.
    """
    sagemaker = boto3.client("sagemaker", region_name=config["region"])
    endpoint_name = config["endpoint_name"]

    try:
        # Check if endpoint exists
        sagemaker.describe_endpoint(EndpointName=endpoint_name)
    except ClientError as e:
        if "Could not find endpoint" in str(e):
            print(f"Endpoint {endpoint_name} does not exist.")
            return
        raise

    print(f"Deleting endpoint: {endpoint_name}")
    print("This will stop billing for the endpoint (~$0.52/hr).")

    sagemaker.delete_endpoint(EndpointName=endpoint_name)
    print("Endpoint deletion initiated.")

    # Also delete endpoint config and model
    try:
        sagemaker.delete_endpoint_config(
            EndpointConfigName="profile-scorer-profile-scorer-config"
        )
        print("Deleted endpoint configuration.")
    except ClientError:
        pass

    try:
        sagemaker.delete_model(ModelName="profile-scorer-profile-scorer-model")
        print("Deleted model.")
    except ClientError:
        pass

    print("\nEndpoint deleted. To redeploy, run:")
    print("  just llm-toggle on")


def get_endpoint_status(config: dict) -> dict | None:
    """Get current endpoint status.

    Args:
        config: SageMaker configuration.

    Returns:
        Endpoint info dict or None if not exists.
    """
    sagemaker = boto3.client("sagemaker", region_name=config["region"])
    endpoint_name = config["endpoint_name"]

    try:
        response = sagemaker.describe_endpoint(EndpointName=endpoint_name)
        return {
            "name": endpoint_name,
            "status": response["EndpointStatus"],
            "creation_time": response.get("CreationTime"),
            "last_modified": response.get("LastModifiedTime"),
        }
    except ClientError as e:
        if "Could not find endpoint" in str(e):
            return None
        raise


def show_info(config: dict) -> None:
    """Show current LLM infrastructure status.

    Args:
        config: SageMaker configuration.
    """
    print("=" * 60)
    print("SageMaker LLM Status")
    print("=" * 60)

    # Check endpoint
    endpoint_info = get_endpoint_status(config)
    if endpoint_info:
        print(f"\nEndpoint: {endpoint_info['name']}")
        print(f"  Status: {endpoint_info['status']}")
        if endpoint_info["status"] == "InService":
            print("  Cost: ~$0.52/hr (running)")
            print("  Airflow alias: profile-scorer-v1")
        print(f"  Last modified: {endpoint_info['last_modified']}")
    else:
        print("\nEndpoint: NOT DEPLOYED")
        print("  Cost: $0 (no endpoint)")
        print("  Run 'just llm-toggle on' to deploy")

    # Check for available models
    print("\n" + "-" * 40)
    models = list_models(config)
    if models:
        print(f"\nLatest model ready for deployment: {models[0]['name']}")

    # Check for running training jobs
    sagemaker = boto3.client("sagemaker", region_name=config["region"])
    response = sagemaker.list_training_jobs(
        NameContains="profile-scorer",
        StatusEquals="InProgress",
        MaxResults=1,
    )
    if response["TrainingJobSummaries"]:
        job = response["TrainingJobSummaries"][0]
        print(f"\nTraining in progress: {job['TrainingJobName']}")

    print("\n" + "=" * 60)


def toggle_endpoint(config: dict, action: str | None = None) -> None:
    """Toggle endpoint on/off or show status.

    Args:
        config: SageMaker configuration.
        action: "on", "off", or None (show status and prompt).
    """
    endpoint_info = get_endpoint_status(config)
    is_running = endpoint_info and endpoint_info["status"] == "InService"

    if action is None:
        # Show status and options
        if is_running:
            print("Endpoint is ON (running)")
            print("Cost: ~$0.52/hr")
            print("\nTo turn OFF: just llm-toggle off")
        else:
            print("Endpoint is OFF")
            print("Cost: $0")
            print("\nTo turn ON: just llm-toggle on")
        return

    if action.lower() == "on":
        if is_running:
            print("Endpoint is already running.")
            return
        print("Turning ON endpoint...")
        deploy_model(config, None)

    elif action.lower() == "off":
        if not is_running:
            print("Endpoint is already off.")
            return
        print("Turning OFF endpoint...")
        delete_endpoint(config)

    else:
        print(f"Unknown action: {action}")
        print("Use 'on' or 'off'")


def main():
    parser = argparse.ArgumentParser(
        description="SageMaker LLM training and deployment CLI"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Train command
    train_parser = subparsers.add_parser("train", help="Start training job")
    train_parser.add_argument(
        "data",
        nargs="?",
        help="Path to training data (JSONL or CSV)",
    )
    train_parser.add_argument(
        "--wait",
        action="store_true",
        help="Wait for training to complete",
    )

    # Deploy command
    deploy_parser = subparsers.add_parser("deploy", help="Deploy model to endpoint")
    deploy_parser.add_argument(
        "model",
        nargs="?",
        help="Model name (training job name), or latest if not specified",
    )

    # Status command
    status_parser = subparsers.add_parser("status", help="Check training status")
    status_parser.add_argument(
        "job",
        nargs="?",
        help="Job name, or latest if not specified",
    )

    # List command
    subparsers.add_parser("list", help="List available models")

    # Delete command
    subparsers.add_parser("delete", help="Delete endpoint (save costs)")

    # Info command
    subparsers.add_parser("info", help="Show LLM infrastructure status")

    # Toggle command
    toggle_parser = subparsers.add_parser("toggle", help="Toggle endpoint on/off")
    toggle_parser.add_argument(
        "action",
        nargs="?",
        choices=["on", "off"],
        help="'on' to start endpoint, 'off' to stop (saves costs)",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    config = get_config()

    if args.command == "train":
        if args.data:
            upload_training_data(args.data, config)
        start_training(config, wait=args.wait)

    elif args.command == "deploy":
        deploy_model(config, args.model)

    elif args.command == "status":
        get_training_status(config, args.job)

    elif args.command == "list":
        list_models(config)

    elif args.command == "delete":
        delete_endpoint(config)

    elif args.command == "info":
        show_info(config)

    elif args.command == "toggle":
        toggle_endpoint(config, args.action)


if __name__ == "__main__":
    main()
