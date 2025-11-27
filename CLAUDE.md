# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
- `lambdas/keyword-engine/` - Keyword selection lambda (isolated subnet)
- `lambdas/query-twitter-api/` - Profile fetching lambda (private subnet with NAT)
- `lambdas/llm-scorer/` - LLM scoring lambda (private subnet with NAT)
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
```

## Environment Variables

Required for database operations:

```bash
export DATABASE_URL=$(cd infra && uv run pulumi stack output db_connection_string --show-secrets)
```

Pulumi secrets (set via `pulumi config set --secret`):

- `db_password`
- `twitterx_apikey`
- `anthropic_apikey`

## Tech Stack

- **Node.js**: Yarn 4.12.0, TypeScript 5.x, esbuild for bundling
- **Python**: uv package manager, Pulumi 3.x/4.x
- **Database**: PostgreSQL 16.3, Drizzle ORM
- **Lambda Runtime**: Node.js 20.x, 256MB memory, 30s timeout

## Database Schema

Tables defined in `packages/db/src/schema.ts`:

- `user_profiles` - Core Twitter user data
- `profile_scores` - Scoring records
- `user_stats` - Aggregated statistics
- `xapi_search_usage` - API call tracking
- `profiles_to_score` - Processing queue
- `user_keywords` - Keywords for profiles

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
EventBridge (15 min) → orchestrator → keyword-engine → SQS:keywords-queue
                                                              ↓
                                                      query-twitter-api (×3 concurrent)
                                                              ↓
                                                      DB: user_profiles, user_stats, user_keywords
                                                              ↓
                                                      profiles_to_score (HAS > 0.65)
                                                              ↓
                                                      SQS:scoring-queue → llm-scorer
```
