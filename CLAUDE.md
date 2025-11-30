# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Workflow Rules

**IMPORTANT:** Follow these rules for every task:

1. **Documentation updates**: After any major fix or addition, update the relevant docs in `docs/`, then commit the changes together.

2. **CloudWatch dashboard**: When a Pulumi component is added or removed, update the CloudWatch dashboard in `infra/components/dashboard.py` to include/remove its metrics.

3. **Prefer Justfile commands**: For common tasks, use the battle-tested Justfile commands instead of raw commands. This reduces errors and token usage:
   - `just deploy` - Build + Pulumi up
   - `just test` - Run E2E tests
   - `just test-debug` - Run E2E tests with DEBUG logging
   - `just db-push` - Push schema with SSL cert
   - `just update-env` - Update .env with DATABASE_URL from Pulumi

## Project Overview

Profile Scorer is an AWS-based Twitter profile analysis pipeline. It uses Pulumi (Python) for infrastructure, Node.js Lambda functions for processing, and PostgreSQL with Drizzle ORM for data storage. The codebase is a monorepo managed by Yarn workspaces.

## Architecture

```
VPC (10.0.0.0/16)
├── Public Subnets → NAT Gateway → Internet
├── Private Subnets → Lambda: orchestrator, query-twitter-api, llm-scorer (internet via NAT)
└── Isolated Subnets → RDS PostgreSQL + Lambda: keyword-engine (DB-only)
```

**Key components:**

- `packages/db/` - Shared database layer (Drizzle ORM, TypeScript)
- `packages/twitterx-api/` - Twitter API wrapper with HAS scoring (RapidAPI client, Winston logging)
- `lambdas/orchestrator/` - Pipeline coordinator (EventBridge triggered every 15 min)
- `lambdas/keyword-engine/` - Keyword selection lambda (isolated subnet, ~80 academic research keywords)
- `lambdas/query-twitter-api/` - Profile fetching lambda (private subnet with NAT)
- `lambdas/llm-scorer/` - Multi-model LLM scoring lambda (private subnet with NAT)
- `infra/` - Pulumi infrastructure (Python, uv package manager)

## Build Commands

```bash
# Install dependencies
yarn install                    # Node.js packages
cd infra && uv sync            # Python/Pulumi packages

# Build
yarn build                      # Build all packages and lambdas
yarn build:lambdas             # Build only lambda functions

# Database
yarn generate                   # Generate Drizzle migrations
yarn push                       # Apply schema to RDS (requires DATABASE_URL)

# Infrastructure
yarn deploy                     # Deploy with pulumi up --yes
cd infra && uv run pulumi up   # Interactive deploy

# Testing
just test                       # Run E2E tests
just test-debug                 # Run E2E tests with DEBUG logging
```

## Environment Variables

Required for database operations:

```bash
export DATABASE_URL=$(cd infra && uv run pulumi stack output db_connection_string --show-secrets)
```

Pulumi secrets (set via `pulumi config set --secret`):

- `db_password` - PostgreSQL password
- `twitterx_apikey` - RapidAPI key for TwitterX
- `anthropic_apikey` - Claude API key for LLM scoring
- `gemini_apikey` - Google AI API key for Gemini scoring
- `groq_apikey` - Groq API key for Meta/Llama models

## Tech Stack

- **Node.js**: Yarn 4.12.0, TypeScript 5.x, esbuild for bundling
- **Python**: uv package manager, Pulumi 3.x/4.x
- **Database**: PostgreSQL 16.3, Drizzle ORM
- **Lambda Runtime**: Node.js 20.x, 256MB memory, 30s timeout
- **LLM SDKs**: LangChain providers (@langchain/anthropic, @langchain/google-genai, @langchain/groq)
- **Validation**: Zod for LLM response validation
- **Serialization**: TOON format for LLM input

## Database Schema

Tables defined in `packages/db/src/schema.ts`:

- `user_profiles` - Core Twitter user data with HAS (Human Authenticity Score)
- `profile_scores` - LLM scoring records (unique per twitter_id + scored_by model)
- `user_stats` - Raw numeric fields for ML training
- `xapi_usage_search` - API call tracking and pagination state
- `profiles_to_score` - Queue of profiles pending LLM evaluation (HAS > 0.65)
- `user_keywords` - Many-to-many linking profiles to search keywords
- `keyword_stats` - Keyword pool with semantic tags and quality metrics

## LLM Scoring System

The `llm-scorer` lambda supports multiple models with probability-based invocation. Models use simplified aliases for logging, with full names stored in DB.

| Alias              | Full Name                                    | Probability | Batch Size |
| ------------------ | -------------------------------------------- | ----------- | ---------- |
| `meta-maverick-17b`  | `meta-llama/llama-4-maverick-17b-128e-instruct` | 0.7 (70%)   | 25         |
| `claude-haiku-4.5`   | `claude-haiku-4-5-20251001`                    | 0.6 (60%)   | 25         |
| `gemini-flash-2.0`   | `gemini-2.0-flash`                             | 0.4 (40%)   | 15         |

**Additional models available:**
- `claude-sonnet-4.5` → `claude-sonnet-4-20250514`
- `claude-opus-4.5` → `claude-opus-4-5-20251101`
- `gemini-flash-1.5` → `gemini-1.5-flash`

**Architecture:**

- Orchestrator invokes llm-scorer with model alias (e.g., `claude-haiku-4.5`)
- `packages/llm-scoring` resolves alias to full model name via `MODEL_REGISTRY`
- DB `labeled_by` column stores full model name for precise tracking
- Unique constraint on `(twitter_id, scored_by)` prevents duplicate scoring
- Each model scores independently - profiles accumulate labels from multiple models

**Input/Output:**

- Input: Profiles serialized in TOON format
- Output: JSON array validated with Zod schema `{ handle, label, reason }[]`
- Trivalent labeling: `true` (match), `false` (no match), `null` (uncertain)

**Error Handling:**

- Quota/rate limit errors logged with `action: "PURCHASE_TOKENS_OR_WAIT"`
- Returns empty array on error (allows other models to continue)
- Invalid model aliases rejected with available models list

## Deployment Workflow

```bash
yarn build                                          # 1. Build packages (includes RDS CA cert copy)
export DATABASE_URL=$(cd infra && uv run pulumi stack output db_connection_string --show-secrets)
yarn push                                           # 2. Push schema
cd infra && uv run pulumi up --yes                 # 3. Deploy infra
```

Or use the Justfile:

```bash
just deploy                                         # Build + Pulumi up
just db-push                                        # Push schema with SSL cert
```

## SSL/TLS Configuration

AWS RDS requires SSL for connections. The `packages/db/` client automatically loads the RDS CA certificate bundle:

- Certificate location: `certs/aws-rds-global-bundle.pem`
- Each Lambda bundles this cert in its dist folder (via esbuild config)
- The client searches `/var/task/` at runtime (Lambda extraction path)

## Pipeline Flow

```
EventBridge (15 min) → orchestrator
                           │
                           ├─→ keyword-engine (get randomized keywords)
                           │         │
                           │         ↓
                           │   SQS:keywords-queue → query-twitter-api
                           │                              │
                           │                              ↓
                           │                    DB: user_profiles, user_stats, user_keywords
                           │                              │
                           │                              ↓
                           │                    profiles_to_score (HAS > 0.65)
                           │
                           └─→ llm-scorer (per model, probability-based)
                                     │
                                     ↓
                               DB: profile_scores
```

## Testing

E2E tests located in `infra/tests/e2e/`:

```bash
just test                    # Run all tests
just test-debug              # Run with DEBUG logging
```

Test coverage:

- `test_keyword_engine.py` - Keyword retrieval and randomization
- `test_query_twitter_api.py` - Profile fetching and HAS scoring
- `test_orchestrator.py` - Pipeline coordination
- `test_llm_scorer.py` - LLM scoring with multiple models
- `test_database.py` - Data integrity and FK constraints
