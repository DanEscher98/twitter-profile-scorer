# Profile Scorer Documentation

A lookalike audience builder for Twitter ad targeting. Given a curated set of seed profiles (e.g., 50 qualitative researchers), the system discovers and ranks similar profiles for targeted advertising campaigns.

## AWS Console Quick Links

| Resource                   | Link                                                                                                                       |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| **CloudWatch Dashboard**   | [profile-scorer](https://us-east-2.console.aws.amazon.com/cloudwatch/home?region=us-east-2#dashboards:name=profile-scorer) |
| **Resource Group**         | [profile-scorer-saas](https://us-east-2.console.aws.amazon.com/resource-groups/group/profile-scorer-saas)                  |
| **AWS Budgets**            | [profile-scorer-monthly](https://us-east-1.console.aws.amazon.com/billing/home#/budgets)                                   |
| **Cost Explorer**          | [By Service](https://us-east-1.console.aws.amazon.com/cost-management/home#/cost-explorer)                                 |
| **Cost Anomaly Detection** | [Monitors](https://us-east-1.console.aws.amazon.com/cost-management/home#/anomaly-detection/monitors)                      |
| **Airflow UI**             | [profile-scorer.admin.ateliertech.xyz](https://profile-scorer.admin.ateliertech.xyz)                                       |

## Table of Contents

1. [Architecture](./architecture.md) - System design, Airflow DAGs, AWS resources
2. [Human Authenticity Score](./heuristic_has.md) - Heuristic scoring methodology
3. [Data Pipeline](./data_pipeline.md) - Data flow, tables, API usage
4. [LLM Scoring](./llm_scoring.md) - Model-based scoring and final score computation
5. [Airflow Deployment](./airflow-deployment.md) - EC2 setup and deployment workflow
6. [Training Pipeline](./training_pipeline.md) - Custom model fine-tuning (Phase 2)

## Project Goal

**Input:** 50 curated Twitter profiles (actual customers - qualitative researchers)

**Output:** 500+ targeted profiles ranked by similarity/relevance

**Success Metric:** Seed profiles should rank at the top when scored by the system.

## Pipeline Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           PROFILE SCORER PIPELINE                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Apache Airflow 3.x on EC2 (t3.medium)                                      │
│  ─────────────────────────────────────                                      │
│                                                                             │
│  PHASE 1: Collection (profile_search DAG - every 15 min)                    │
│  ────────────────────                                                       │
│  platforms → keyword_engine (×N platforms) → query_profiles (×M keywords)   │
│                                                                             │
│  PHASE 2: Heuristic Filtering                                               │
│  ───────────────────────────────                                            │
│  Raw profiles → HAS (Human Authenticity Score) → filter bots/orgs           │
│  HAS > 0.65 → profiles_to_score queue                                       │
│                                                                             │
│  PHASE 3: LLM Scoring (llm_scoring DAG - every 15 min)                      │
│  ─────────────────────                                                      │
│  Batch 25 profiles → TOON format → Claude/Gemini/Groq → ScoredUser          │
│  Final Score = f(HAS, LLM_score)                                            │
│                                                                             │
│  PHASE 4: Custom Model (Future)                                             │
│  ──────────────────────────────                                             │
│  Generate dataset → Fine-tune Mistral-7B → Deploy GGUF via Ollama           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Quick Reference

### Resource Constraints

| Resource                 | Limit                    | Strategy                          |
| ------------------------ | ------------------------ | --------------------------------- |
| RapidAPI TwitterX        | 500K req/month, 10 req/s | Airflow task concurrency control  |
| Anthropic (Claude Haiku) | $9.90 balance            | Small batches, TOON format        |
| Gemini Flash             | Free tier                | Parallel scoring                  |
| Groq (Llama)             | Free tier                | High probability, fast inference  |

### Key Thresholds

| Parameter            | Value        | Purpose                             |
| -------------------- | ------------ | ----------------------------------- |
| HAS threshold        | 0.65         | Filter bots/orgs before LLM scoring |
| Batch size           | 25 profiles  | TOON format, reduce hallucination   |
| Profiles per keyword | 20 (default) | API stability                       |

### Database Tables

| Table               | Purpose                                |
| ------------------- | -------------------------------------- |
| `user_profiles`     | Core profile data + HAS score          |
| `user_stats`        | Raw numeric fields for HAS validation  |
| `profiles_to_score` | Queue for LLM scoring                  |
| `profile_scores`    | LLM scores per model                   |
| `api_search_usage`  | API usage tracking + pagination cursor |
| `user_keywords`     | Profile-keyword associations           |
| `keyword_stats`     | Keyword pool with semantic tags        |
| `keyword_status`    | Platform-specific keyword pagination   |

## Monitoring & Cost Management

### CloudWatch Dashboard

The [profile-scorer dashboard](https://us-east-2.console.aws.amazon.com/cloudwatch/home?region=us-east-2#dashboards:name=profile-scorer) shows:

- **EC2 metrics**: CPU utilization, network I/O, status checks
- **RDS metrics**: Connections, CPU utilization, storage, IOPS

### Cost Tracking

| Tool                   | Purpose                               |
| ---------------------- | ------------------------------------- |
| AWS Budget             | $50/month limit with threshold alerts |
| Cost Explorer          | Service breakdown, tag filtering      |
| Cost Anomaly Detection | ML-based unusual spending alerts      |

**Estimated Monthly Cost:**

| Service    | Cost    | Notes                                                   |
| ---------- | ------- | ------------------------------------------------------- |
| EC2        | ~$30.00 | t3.medium (4GB RAM - required for PyTorch/transformers) |
| RDS        | ~$13.00 | PostgreSQL db.t4g.micro                                 |
| CloudWatch | ~$0.30  | Basic metrics + status check alarm                      |
| **Total**  | **~$43**| Monthly estimate                                        |

**To enable tag-based cost filtering:**

1. Go to AWS Console → Billing → Cost allocation tags
2. Activate the `Project` tag
3. Wait 24 hours for data to appear

## Current Status

- [x] Infrastructure (VPC, RDS, EC2)
- [x] Database schema (Drizzle ORM + Alembic migrations)
- [x] HAS heuristic implementation (`airflow/packages/scoring`)
- [x] `profile_search` DAG with multi-platform support
- [x] `llm_scoring` DAG (multi-model: Haiku, Gemini, Groq)
- [x] `keyword_stats` DAG
- [x] CloudWatch Dashboard
- [x] AWS Budget and cost monitoring
- [x] GitHub Actions CI/CD for Airflow
- [x] GitHub Actions CI/CD for Infrastructure
- [ ] Seed profile validation
- [ ] Training pipeline (Phase 2)
