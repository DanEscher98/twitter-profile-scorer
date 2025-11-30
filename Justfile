# Profile Scorer Development Commands

# Load environment variables from .env
set dotenv-load

# Default recipe
default:
    @just --list

# Database connection test
db-connect:
    psql "$DATABASE_URL"

# Quick DB connection test (just checks if connection works)
db-test:
    psql "$DATABASE_URL" -c "SELECT NOW() as connected_at, current_database() as db_name;"

# Push Drizzle schema to database
db-push:
    cd packages/db && NODE_EXTRA_CA_CERTS=../../certs/aws-rds-global-bundle.pem yarn push

# Generate Drizzle migrations
db-generate:
    cd packages/db && NODE_EXTRA_CA_CERTS=../../certs/aws-rds-global-bundle.pem yarn generate

# Launch Drizzle Studio (database GUI) - kills previous instance if running
db-studio:
    #!/usr/bin/env bash
    # Kill any existing drizzle-kit studio process on port 4983
    lsof -ti:4983 | xargs -r kill -9 2>/dev/null || true
    cd packages/db && NODE_EXTRA_CA_CERTS=../../certs/aws-rds-global-bundle.pem npx drizzle-kit studio &
    echo "Drizzle Studio starting at https://local.drizzle.studio"

# Build all packages
build:
    yarn build

# Build only lambdas
build-lambdas:
    yarn build:lambdas

# Deploy infrastructure
deploy:
    cd infra && PULUMI_CONFIG_PASSPHRASE="$PULUMI_CONFIG_PASSPHRASE" uv run pulumi up --yes

# Preview infrastructure changes
preview:
    cd infra && PULUMI_CONFIG_PASSPHRASE="$PULUMI_CONFIG_PASSPHRASE" uv run pulumi preview

# Get database connection string from Pulumi
db-url:
    @cd infra && PULUMI_CONFIG_PASSPHRASE="$PULUMI_CONFIG_PASSPHRASE" uv run pulumi stack output db_connection_string --show-secrets

# Update .env with current DATABASE_URL from Pulumi
update-env:
    #!/usr/bin/env bash
    DB_URL=$(cd infra && PULUMI_CONFIG_PASSPHRASE="$PULUMI_CONFIG_PASSPHRASE" uv run pulumi stack output db_connection_string --show-secrets)
    sed -i "s|DATABASE_URL=.*|DATABASE_URL=$DB_URL|" .env
    echo "Updated DATABASE_URL in .env"

# Install all dependencies
install:
    yarn install
    cd infra && uv sync

# Download AWS RDS CA certificate bundle
download-rds-cert:
    mkdir -p certs
    curl -o certs/aws-rds-global-bundle.pem https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
    @echo "Downloaded AWS RDS CA certificate to certs/aws-rds-global-bundle.pem"

# Full setup: install, build, download cert, and push schema
setup: install download-rds-cert build
    @echo "Setup complete! Run 'just db-push' after deploying infrastructure."

# ============================================================================
# E2E Testing
# ============================================================================

# Install test dependencies
test-install:
    cd infra && uv sync --extra test

# Run all E2E tests with INFO level logging
test: test-install
    cd infra && PULUMI_CONFIG_PASSPHRASE="$PULUMI_CONFIG_PASSPHRASE" uv run pytest tests/e2e/ -v --log-level=INFO

# Run E2E tests with DEBUG level logging (verbose)
test-debug: test-install
    cd infra && PULUMI_CONFIG_PASSPHRASE="$PULUMI_CONFIG_PASSPHRASE" uv run pytest tests/e2e/ -v --log-level=DEBUG

# Run E2E tests with WARN level logging (quiet)
test-quiet: test-install
    cd infra && PULUMI_CONFIG_PASSPHRASE="$PULUMI_CONFIG_PASSPHRASE" uv run pytest tests/e2e/ -v --log-level=WARN

# Run E2E tests with ERROR level logging (minimal)
test-errors: test-install
    cd infra && PULUMI_CONFIG_PASSPHRASE="$PULUMI_CONFIG_PASSPHRASE" uv run pytest tests/e2e/ -v --log-level=ERROR

# Run specific test file (e.g., just test-file test_orchestrator)
test-file file: test-install
    cd infra && PULUMI_CONFIG_PASSPHRASE="$PULUMI_CONFIG_PASSPHRASE" uv run pytest tests/e2e/{{file}}.py -v --log-level=INFO

# Run tests matching a pattern (e.g., just test-match "orchestrator")
test-match pattern: test-install
    cd infra && PULUMI_CONFIG_PASSPHRASE="$PULUMI_CONFIG_PASSPHRASE" uv run pytest tests/e2e/ -v -k "{{pattern}}" --log-level=INFO

# Run only fast tests (excludes slow integration tests)
test-fast: test-install
    cd infra && PULUMI_CONFIG_PASSPHRASE="$PULUMI_CONFIG_PASSPHRASE" uv run pytest tests/e2e/ -v -m "not slow" --log-level=INFO

# Run slow integration tests only
test-slow: test-install
    cd infra && PULUMI_CONFIG_PASSPHRASE="$PULUMI_CONFIG_PASSPHRASE" uv run pytest tests/e2e/ -v -m "slow" --log-level=INFO

# Run database integrity tests only
test-db: test-install
    cd infra && PULUMI_CONFIG_PASSPHRASE="$PULUMI_CONFIG_PASSPHRASE" uv run pytest tests/e2e/test_database.py -v --log-level=INFO

# Run query-twitter-api tests
test-twitter: test-install
    cd infra && PULUMI_CONFIG_PASSPHRASE="$PULUMI_CONFIG_PASSPHRASE" uv run pytest tests/e2e/test_query_twitter_api.py -v --log-level=INFO

# Run orchestrator tests
test-orchestrator: test-install
    cd infra && PULUMI_CONFIG_PASSPHRASE="$PULUMI_CONFIG_PASSPHRASE" uv run pytest tests/e2e/test_orchestrator.py -v --log-level=INFO

# Run llm-scorer tests
test-scorer: test-install
    cd infra && PULUMI_CONFIG_PASSPHRASE="$PULUMI_CONFIG_PASSPHRASE" uv run pytest tests/e2e/test_llm_scorer.py -v --log-level=INFO

# ============================================================================
# Scripts
# ============================================================================

# Run a TypeScript script (e.g., just ts-script js_src/inspect-profiles.ts)
ts-script path:
    NODE_EXTRA_CA_CERTS=certs/aws-rds-global-bundle.pem yarn workspace @profile-scorer/scripts run run {{path}}

# Run a Python script (e.g., just py-script py_src/plot_has_distribution.py)
py-script path:
    cd scripts && uv run {{path}}

# Run a SQL migration file (e.g., just db-migrate packages/db/migrations/populate_semantic_tags.sql)
db-migrate path:
    psql "$DATABASE_URL" -f {{path}}

# ============================================================================
# LLM Scoring Scripts
# ============================================================================

# Score all pending profiles with a specific model (parallel batches)
# Usage: just score-all <model> [batch-size] [threshold] [concurrency]
# Example: just score-all claude-haiku-4-5-20251001
# Example: just score-all claude-opus-4-5-20251101 10 0.55 5
score-all model batch_size="25" threshold="0.55" concurrency="10":
    yarn workspace @profile-scorer/scripts score-all {{model}} --batch-size={{batch_size}} --threshold={{threshold}} --concurrency={{concurrency}}

# Score profiles by keyword with a specific model
# Usage: just score-keyword <keyword> <model>
# Example: just score-keyword epidemiologist claude-haiku-4-5-20251001
score-keyword keyword model:
    yarn workspace @profile-scorer/scripts score-keyword {{keyword}} {{model}}

# Generate system prompt from audience config JSON
# Usage: just get-systemprompt <config-path>
# Example: just get-systemprompt scripts/data/thelai_customers.json
get-systemprompt path:
    yarn workspace @profile-scorer/scripts run tsx js_src/get-system-prompt.ts {{path}}
