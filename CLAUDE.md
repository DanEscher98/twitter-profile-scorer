# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Workflow Rules

**IMPORTANT:** Follow these rules for every task:

1. **Documentation updates**: After any major fix or addition, update the relevant docs in `docs/`, then commit the changes together.

2. **CloudWatch dashboard**: When a Pulumi component is added or removed, update the CloudWatch dashboard in `infra/components/simple_dashboard.py` to include/remove its metrics.

3. **Prefer Justfile commands**: For common tasks, use the battle-tested Justfile commands instead of raw commands. This reduces errors and token usage:
   - `just deploy` - Build + Pulumi up
   - `just db-push` - Push schema with SSL cert
   - `just update-env` - Update .env with DATABASE_URL from Pulumi

## Project Overview

Profile Scorer is an AWS-based Twitter/X profile analysis pipeline. It uses:
- **Pulumi (Python)** for infrastructure
- **Apache Airflow 3.x** on EC2 for pipeline orchestration
- **RDS PostgreSQL** for data storage

The `airflow/` directory is a **git submodule** pointing to a separate repository (`profile-scorer.airflow`) that can be developed and deployed independently.

## Architecture

```
VPC (10.0.0.0/16)
├── Public Subnets (10.0.1-2.0/24)
│   ├── EC2 (t3.medium) - Apache Airflow 3.x with Docker
│   └── RDS PostgreSQL (dev access)
├── Private Subnets (10.0.10-11.0/24) - unused (NAT removed)
└── Isolated Subnets (10.0.20-21.0/24) - unused
```

**Cost Optimization:** NAT Gateway removed (~$32/month savings). All workloads run on EC2 with direct internet access.

## Repository Structure

```
profile-scorer/                    # Parent repo (infrastructure)
├── infra/                         # Pulumi infrastructure (Python)
│   ├── __main__.py               # Main Pulumi program
│   └── components/               # Reusable Pulumi components
├── airflow/                       # Git submodule → profile-scorer.airflow repo
│   ├── dags/                     # Airflow DAGs
│   ├── packages/                 # Python packages (db, scoring, search_profiles)
│   └── .github/workflows/        # Airflow-specific CI/CD
├── packages/                      # Legacy TypeScript packages (deprecated)
├── lambdas/                       # Legacy Lambda functions (deprecated)
└── .github/workflows/            # Infrastructure CI/CD
```

## Airflow Components

**Location:** `airflow/` (separate repo: `profile-scorer.airflow`)

| Package           | Description                                           |
| ----------------- | ----------------------------------------------------- |
| `db`              | SQLModel models, session management                   |
| `search_profiles` | Multi-platform search router (Twitter, Bluesky)       |
| `scoring`         | HAS algorithm + LangChain multi-provider LLM scoring  |
| `utils`           | Logging, settings, base models                        |

**DAGs:**
- `profile_search` - Multi-platform profile search (every 15 min)
- `llm_scoring` - LLM evaluation of high-HAS profiles (every 15 min)
- `keyword_stats` - Daily keyword statistics update (2 AM UTC)

## Quick Start for New Developers

### 1. Airflow Development (Most Common)

```bash
# Clone the airflow repo directly
git clone https://github.com/DanEscher98/profile-scorer.airflow.git
cd profile-scorer.airflow

# Setup local environment
cp .env.example .env
# Edit .env with your credentials

# Run locally with Docker
docker-compose up -d

# Push changes → Auto-deploys to EC2 via GitHub Actions
git add . && git commit -m "Your changes" && git push
```

### 2. Infrastructure Changes (Rare)

```bash
# Clone parent repo with submodule
git clone --recurse-submodules https://github.com/DanEscher98/twitter-profile-scorer.git
cd twitter-profile-scorer

# Setup infrastructure secrets
cp infra/.env.example infra/.env
# Edit infra/.env with AWS credentials and API keys

# Deploy infrastructure
cd infra && uv sync && uv run pulumi up

# If EC2 was recreated, run initial setup
cd ../airflow
./deploy.sh $(cd ../infra && uv run pulumi stack output airflow_public_ip) airflow
```

## Environment Variables

**Infrastructure (`infra/.env`):**
- `DB_PASSWORD` - PostgreSQL password
- `TWITTERX_APIKEY` - RapidAPI key for TwitterX
- `ANTHROPIC_API_KEY` - Claude API key
- `GEMINI_API_KEY` - Google AI API key
- `GROQ_API_KEY` - Groq API key
- `AIRFLOW_SSH_KEY_NAME` - EC2 key pair name

**Airflow (`airflow/.env`):**
- `DATABASE_URL` - PostgreSQL connection string
- `AIRFLOW_ADMIN_USER/PASSWORD` - Airflow web UI credentials
- `AIRFLOW_SECRET_KEY` - Flask secret key
- `AIRFLOW_DOMAIN` - Domain for SSL certificate
- API keys (same as above)

## CI/CD Workflows

| Repo | Workflow | Trigger | Action |
|------|----------|---------|--------|
| `profile-scorer.airflow` | `deploy.yml` | Push to `main` | rsync to EC2, restart containers |
| `twitter-profile-scorer` | `infra-deploy.yml` | Push to `infra/**` | Pulumi up, bootstrap if EC2 recreated |

## Database Schema

Tables in `airflow/packages/db/src/db/models.py`:

- `user_profiles` - Core Twitter user data with HAS score, `platform` column
- `profile_scores` - LLM scoring records (unique per twitter_id + scored_by)
- `user_stats` - Raw numeric fields for ML training
- `api_search_usage` - API call tracking and pagination state
- `profiles_to_score` - Queue of profiles pending LLM evaluation
- `user_keywords` - Many-to-many linking profiles to search keywords
- `keyword_stats` - Keyword pool with semantic tags and quality metrics
- `keyword_status` - Per-platform keyword pagination state

**Migrations:** Alembic (`cd airflow && uv run alembic upgrade head`)

## LLM Scoring System

Models with probability-based invocation:

| Alias              | Full Name                                    | Probability |
| ------------------ | -------------------------------------------- | ----------- |
| `meta-maverick-17b`  | `meta-llama/llama-4-maverick-17b-128e-instruct` | 70%         |
| `claude-haiku-4.5`   | `claude-haiku-4-5-20251001`                    | 60%         |
| `gemini-flash-2.0`   | `gemini-2.0-flash`                             | 40%         |

**Audience configs:** `airflow/dags/audiences/thelai_customers.v*.json`

## Useful Commands

```bash
# SSH to EC2
ssh -i ~/.ssh/airflow.pem ec2-user@$(cd infra && uv run pulumi stack output airflow_public_ip)

# View Airflow logs
ssh -i ~/.ssh/airflow.pem ec2-user@<ip> 'cd /opt/airflow && docker-compose logs -f'

# Database connection
export DATABASE_URL=$(cd infra && uv run pulumi stack output db_connection_string --show-secrets)

# Trigger DAG manually
ssh -i ~/.ssh/airflow.pem ec2-user@<ip> 'cd /opt/airflow && docker-compose exec airflow-scheduler airflow dags trigger profile_search'
```

## Monitoring

- **Airflow UI:** https://profile-scorer.admin.ateliertech.xyz
- **CloudWatch Dashboard:** [profile-scorer](https://us-east-2.console.aws.amazon.com/cloudwatch/home?region=us-east-2#dashboards:name=profile-scorer)
- **AWS Budget:** $50/month limit with alerts at 50%, 80%, 100%
