# Profile Scorer Documentation

A lookalike audience builder for Twitter ad targeting. Given a curated set of seed profiles (e.g., 50 qualitative researchers), the system discovers and ranks similar profiles for targeted advertising campaigns.

## AWS Console Quick Links

| Resource | Link |
|----------|------|
| **CloudWatch Dashboard** | [profile-scorer](https://us-east-2.console.aws.amazon.com/cloudwatch/home?region=us-east-2#dashboards:name=profile-scorer) |
| **Resource Group** | [profile-scorer-saas](https://us-east-2.console.aws.amazon.com/resource-groups/group/profile-scorer-saas) |
| **AWS Budgets** | [profile-scorer-monthly](https://us-east-1.console.aws.amazon.com/billing/home#/budgets) |
| **Cost Explorer** | [By Service](https://us-east-1.console.aws.amazon.com/cost-management/home#/cost-explorer) |
| **Cost Anomaly Detection** | [Monitors](https://us-east-1.console.aws.amazon.com/cost-management/home#/anomaly-detection/monitors) |

## Table of Contents

1. [Architecture](./architecture.md) - System design, Lambda pipeline, AWS resources
2. [Human Authenticity Score](./heuristic_has.md) - Heuristic scoring methodology
3. [Data Pipeline](./data_pipeline.md) - Data flow, tables, API usage
4. [LLM Scoring](./llm_scoring.md) - Model-based scoring and final score computation
5. [Training Pipeline](./training_pipeline.md) - Custom model fine-tuning (Phase 2)

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
│  PHASE 1: Collection                                                        │
│  ────────────────────                                                       │
│  orchestrator → keyword-engine → query-twitter-api (×N parallel)            │
│                                                                             │
│  PHASE 2: Heuristic Filtering                                               │
│  ───────────────────────────────                                            │
│  Raw profiles → HAS (Human Authenticity Score) → filter bots/orgs           │
│  HAS > 0.65 → profiles_to_score queue                                       │
│                                                                             │
│  PHASE 3: LLM Scoring                                                       │
│  ─────────────────────                                                      │
│  Batch 25 profiles → TOON format → Claude/Gemini → ScoredUser               │
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

| Resource | Limit | Strategy  |
|----------|-------|----------|
| RapidAPI TwitterX | 500K req/month, 10 req/s | SQS concurrency control (3) |
| Anthropic (Claude Haiku) | $9.90 balance | Small batches, TOON format |
| Gemini Flash | Free tier | Parallel scoring |

### Key Thresholds

| Parameter | Value | Purpose  |
|-----------|-------|---------|
| HAS threshold | 0.65 | Filter bots/orgs before LLM scoring |
| Batch size | 25 profiles | TOON format, reduce hallucination |
| Profiles per keyword | 20 (default) | API stability |

### Database Tables

| Table | Purpose |
|-------|---------|
| `user_profiles` | Core profile data + HAS score |
| `user_stats` | Raw numeric fields for HAS validation |
| `profiles_to_score` | Queue for LLM scoring |
| `profile_scores` | LLM scores per model |
| `xapi_usage_search` | API usage tracking + pagination cursor |
| `user_keywords` | Profile-keyword associations |
| `keyword_stats` | Keyword pool with semantic tags |

## Utility Scripts

Scripts for data analysis and profile management in `scripts/`:

### TypeScript Scripts (`scripts/js_src/`)

```bash
# Upload curated usernames for scoring
yarn workspace @profile-scorer/scripts run tsx js_src/upload_curated_users.ts data/curated_usernames.txt

# Export high-scoring profiles to CSV (TUI interface)
LOG_LEVEL=silent yarn workspace @profile-scorer/scripts run tsx js_src/export-high-scores.ts

# Score all pending profiles with TUI progress
yarn workspace @profile-scorer/scripts run tsx js_src/score-all-pending.ts

# Add new keyword to the pool
yarn workspace @profile-scorer/scripts run tsx js_src/add-keyword.ts "new keyword" --tags=#academia,#research
```

### Python Scripts (`scripts/py_src/`)

```bash
cd scripts

# Analyze scores across all models (generates visualization)
uv run py_src/analyze_profile_scores.py

# Analyze scores for a specific model
uv run py_src/analyze_model_scores.py claude-haiku-4-5-20251001

# Plot HAS score distribution
uv run py_src/plot_has_distribution.py
```

Output files are saved to `scripts/output/` with unix timestamps.

## Monitoring & Cost Management

### CloudWatch Dashboard

The [profile-scorer dashboard](https://us-east-2.console.aws.amazon.com/cloudwatch/home?region=us-east-2#dashboards:name=profile-scorer) shows:

- **Lambda metrics**: Invocations, errors, duration (p95), concurrent executions
- **RDS metrics**: Connections, CPU utilization, storage, IOPS
- **SQS metrics**: Queue depth, message age, DLQ depth
- **NAT Gateway**: Traffic in/out, connection counts

### Cost Tracking

| Tool | Purpose |
|------|---------|
| AWS Budget | $10/month limit with threshold alerts |
| Cost Explorer | Service breakdown, tag filtering |
| Cost Anomaly Detection | ML-based unusual spending alerts |

**To enable tag-based cost filtering:**
1. Go to AWS Console → Billing → Cost allocation tags
2. Activate the `Project` tag
3. Wait 24 hours for data to appear

## Current Status

- [x] Infrastructure (VPC, RDS, Lambda functions)
- [x] Database schema (Drizzle ORM)
- [x] HAS heuristic implementation (`@profile-scorer/has-scorer`)
- [x] `keyword-engine` Lambda
- [x] `query-twitter-api` Lambda with HAS scoring
- [x] `orchestrator` Lambda (15-min schedule)
- [x] `llm-scorer` Lambda (multi-model: Haiku, Sonnet, Gemini)
- [x] CloudWatch Dashboard
- [x] AWS Budget and cost monitoring
- [x] E2E test suite (14 tests)
- [x] Standardized API error handling (`TwitterXApiError`)
- [x] Utility scripts for analysis and export
- [ ] Seed profile validation
- [ ] Training pipeline (Phase 2)
