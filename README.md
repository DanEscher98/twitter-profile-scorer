# Profile Scorer

AWS infrastructure for Twitter profile data pipeline using Pulumi, Lambda, and RDS PostgreSQL.

## Architecture

```
VPC
├── Private Subnets (NAT) → Lambda (external-api-test) → Internet
├── Isolated Subnets     → Lambda (db-health-check)
└── Isolated Subnets     → RDS PostgreSQL (db.t3.micro)
```

## Project Structure

```
├── infra/                 # Pulumi (Python)
├── packages/db/           # Shared Drizzle schemas + client
├── lambdas/
│   ├── db-health-check/   # DB-only lambda (no internet)
│   └── external-api-test/ # DB + internet lambda
└── .github/workflows/     # CI/CD
```

## Setup

```bash
# 1. Install dependencies
yarn install
cd infra && uv sync

# 2. Configure secrets
pulumi config set aws:region us-east-1
pulumi config set --secret db_password "YourPassword123!"
pulumi config set --secret twitterx_apikey "xxx"
pulumi config set --secret anthropic_apikey "xxx"

# 3. Deploy
cd .. && yarn build
cd infra && uv run pulumi up

# 4. Push DB schema
export DATABASE_URL=$(uv run pulumi stack output db_connection_string --show-secrets)
cd .. && yarn push
```

## Commands

| Command                        | Description                    |
| ------------------------------ | ------------------------------ |
| `yarn build`                   | Build all packages and lambdas |
| `yarn generate`                | Generate Drizzle migrations    |
| `yarn push`                    | Push schema to RDS             |
| `cd infra && uv run pulumi up` | Deploy infrastructure          |

## GitHub Actions Secrets

- `PULUMI_ACCESS_TOKEN`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
