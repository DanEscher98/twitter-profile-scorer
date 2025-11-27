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
