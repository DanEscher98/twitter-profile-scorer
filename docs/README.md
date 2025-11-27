# Profile Scorer Documentation

A lookalike audience builder for Twitter ad targeting. Given a curated set of seed profiles (e.g., 50 qualitative researchers), the system discovers and ranks similar profiles for targeted advertising campaigns.

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
│  HAS > 0.55 → profiles_to_score queue                                       │
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
| Gemini Flash             | Free tier | Parallel scoring |

### Key Thresholds

| Parameter | Value | Purpose  |
|-----------|-------|---------|
| HAS threshold | 0.55 | Filter bots/orgs before LLM scoring |
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

## Current Status

- [x] Infrastructure (VPC, RDS, Lambda shells)
- [x] Database schema
- [x] HAS heuristic implementation (C+ quality, needs tuning)
- [ ] `query-twitter-api` actual implementation
- [ ] `orchestrator` Lambda
- [ ] `llm-scorer` Lambda
- [ ] Training pipeline (Phase 2)
