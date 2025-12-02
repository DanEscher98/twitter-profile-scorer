# Profile Scorer - Airflow Pipeline

Apache Airflow 3.x pipeline for Twitter/BlueSky profile scoring.

## Setup

```bash
# Install uv if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync

# Verify installation
uv run ruff check .
uv run mypy .
```

## Project Structure

```
airflow/
├── dags/                    # Airflow DAG definitions
├── tasks/                   # TaskFlow task implementations
├── packages/                # Shared Python packages
│   ├── scorer_db/          # Database (SQLModel + Alembic)
│   ├── scorer_llm/         # LLM scoring (LangChain)
│   ├── scorer_twitter/     # Twitter API client
│   ├── scorer_has/         # HAS algorithm
│   └── scorer_utils/       # Logging, settings
├── alembic/                 # Database migrations
├── tests/                   # Test suite
├── pyproject.toml          # Project config
└── ruff.toml               # Linting config
```

## Development

```bash
# Lint
uv run ruff check .
uv run ruff format .

# Type check
uv run mypy .

# Test
uv run pytest

# Run single DAG (local)
uv run airflow dags test profile_scoring_pipeline
```

## Database Migrations

```bash
# Create new migration
uv run alembic revision -m "description"

# Apply migrations
uv run alembic upgrade head

# Rollback one migration
uv run alembic downgrade -1

# Show migration history
uv run alembic history
```

## Environment Variables

Required in `.env` or environment:

```bash
DATABASE_URL=postgresql://user:pass@host:5432/db?sslmode=require
TWITTERX_APIKEY=your-rapidapi-key
ANTHROPIC_API_KEY=sk-ant-xxx
GEMINI_API_KEY=your-gemini-key
GROQ_API_KEY=gsk_xxx
RDS_CA_PATH=/path/to/aws-rds-global-bundle.pem
```
