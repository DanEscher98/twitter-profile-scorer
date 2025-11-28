# System Architecture

## Overview

```mermaid
graph TB
    subgraph Region["AWS us-east-2"]
        subgraph Compute
            OR[orchestrator]
            KE[keyword-engine]
            QT[query-twitter-api]
            LS[llm-scorer]
        end

        subgraph Storage
            RDS[(RDS PostgreSQL)]
        end

        subgraph Messaging
            EB[EventBridge]
            SQS_K[SQS: keywords]
        end
    end

    subgraph External
        RapidAPI[RapidAPI TwitterX]
        Claude[Claude API]
        Gemini[Gemini API]
    end

    EB -->|every 15 min| OR
    OR -->|invoke| KE
    OR -->|send| SQS_K
    OR -->|invoke| LS
    SQS_K -->|trigger| QT
    QT --> RapidAPI
    QT --> RDS
    LS --> Claude
    LS --> Gemini
    LS --> RDS
    KE --> RDS
```

**Resource Group:** [`profile-scorer-saas`](https://console.aws.amazon.com/resource-groups) - View all resources in AWS Console

**Tags:** All resources are tagged with:
- `Project: profile-scorer-saas`
- `Environment: mvp`
- `ManagedBy: pulumi`

## Data Flow

```mermaid
sequenceDiagram
    participant EB as EventBridge
    participant OR as orchestrator
    participant KE as keyword-engine
    participant SQS as SQS: keywords
    participant QT as query-twitter-api
    participant LS as llm-scorer
    participant DB as PostgreSQL

    EB->>OR: Trigger (every 15 min)
    OR->>KE: Get keyword sample
    KE->>DB: Analyze xapi_usage_search
    KE-->>OR: [researcher, phd, pharma, ...]

    loop Each keyword
        OR->>SQS: Enqueue keyword
    end

    SQS->>QT: Trigger lambda
    QT->>DB: Store profiles + HAS scores

    OR->>DB: COUNT profiles_to_score
    alt pendingCount > 0
        OR->>LS: Invoke with model param
        LS->>DB: Fetch batch, store scores
    end
```

## Lambda Functions

| Lambda | Memory | Timeout | Subnet | Trigger |
|--------|--------|---------|--------|---------|
| orchestrator | 256MB | 30s | Private | EventBridge (15 min) |
| keyword-engine | 256MB | 30s | Isolated | Direct invocation |
| query-twitter-api | 256MB | 60s | Private | SQS keywords-queue |
| llm-scorer | 512MB | 120s | Private | Direct invocation |

### 1. Orchestrator

**Location:** `lambdas/orchestrator/`

Pipeline heartbeat that coordinates all other lambdas:
- Invokes `keyword-engine` to get keyword list
- Sends keywords to SQS queue
- Invokes `llm-scorer` directly when profiles need scoring

### 2. Keyword Engine

**Location:** `lambdas/keyword-engine/`

Selects keywords for Twitter search:
- Currently returns hardcoded sample
- (Future) Analyzes `xapi_usage_search` for keyword effectiveness

### 3. Query Twitter API

**Location:** `lambdas/query-twitter-api/`

Fetches and processes Twitter profiles:
- Calls RapidAPI TwitterX
- Computes HAS (Human Authenticity Score)
- Stores to `user_profiles`, `user_stats`, `user_keywords`
- Queues high-HAS profiles (>0.65) to `profiles_to_score`

```mermaid
flowchart LR
    SQS[SQS: keywords] --> QT[query-twitter-api]
    QT --> API[RapidAPI TwitterX]
    API --> QT
    QT --> HAS{HAS > 0.65?}
    HAS -->|Yes| PTS[(profiles_to_score)]
    HAS -->|No| Skip[Skip]
    QT --> DB[(user_profiles)]
```

### 4. LLM Scorer

**Location:** `lambdas/llm-scorer/`

Evaluates profiles with LLMs:
- Fetches batch from `profiles_to_score` using `FOR UPDATE SKIP LOCKED`
- Sends to Claude or Gemini (model specified at invocation)
- Stores scores in `profile_scores`

## Network Architecture

```mermaid
graph TB
    subgraph VPC["VPC (10.0.0.0/16)"]
        subgraph Public["Public Subnets (10.0.1.0/24, 10.0.2.0/24)"]
            NAT[NAT Gateway]
            RDS[(RDS PostgreSQL)]
        end

        subgraph Private["Private Subnets (10.0.10.0/24, 10.0.11.0/24)"]
            QT[query-twitter-api]
            LS[llm-scorer]
            OR[orchestrator]
        end

        subgraph Isolated["Isolated Subnets (10.0.20.0/24, 10.0.21.0/24)"]
            KE[keyword-engine]
        end
    end

    Internet((Internet)) --> NAT
    NAT --> QT
    NAT --> LS
    NAT --> OR
```

| Subnet Type | CIDR | Internet Access | Purpose |
|-------------|------|-----------------|---------|
| Public | 10.0.1-2.0/24 | Direct (IGW) | NAT Gateway, RDS (dev) |
| Private | 10.0.10-11.0/24 | Via NAT | Lambdas needing external APIs |
| Isolated | 10.0.20-21.0/24 | None | DB-only lambdas |

**Note:** RDS is in public subnets for dev access. Move to isolated subnets for production.

## Message Queues

```mermaid
graph LR
    OR[orchestrator] --> KQ[keywords-queue]
    KQ --> QT[query-twitter-api]
    KQ -.->|after 3 failures| DLQ[keywords-dlq]
```

| Queue | Visibility Timeout | Max Retries | DLQ Retention |
|-------|-------------------|-------------|---------------|
| keywords-queue | 60s | 3 | 7 days |

**Note:** The scoring queue was removed. The `llm-scorer` is now invoked directly by the orchestrator. The `profiles_to_score` table serves as a persistent queue with atomic claims via `FOR UPDATE SKIP LOCKED`.

## Rate Limiting

### RapidAPI TwitterX
- **Limit:** 10 req/s, 500K req/month
- **Strategy:** SQS concurrency = 3 (~3 req/s effective)

### LLM APIs
- **Claude Haiku:** $0.25/1M input, $1.25/1M output
- **Gemini Flash:** Free tier
- **Batch size:** 25 profiles per request

## Monitoring & Observability

### CloudWatch Dashboard

**Link:** [profile-scorer](https://us-east-2.console.aws.amazon.com/cloudwatch/home?region=us-east-2#dashboards:name=profile-scorer)

The dashboard provides a unified view of all system components:

| Row | Metrics |
|-----|---------|
| **Pipeline Health** | Lambda invocations (stacked), errors, SQS queue depth |
| **Lambda Performance** | Duration p95, concurrent executions, throttles |
| **Database Health** | RDS connections, CPU utilization, free storage |
| **Database I/O** | Read/write IOPS, read/write latency |
| **Queue Metrics** | Message age, sent/received/deleted, empty receives |
| **Network** | NAT Gateway traffic in/out, connection counts |

### Cost Management

| Resource | Link |
|----------|------|
| **AWS Budget** | [profile-scorer-monthly](https://us-east-1.console.aws.amazon.com/billing/home#/budgets) - $10/month limit |
| **Cost Explorer** | [By Service](https://us-east-1.console.aws.amazon.com/cost-management/home#/cost-explorer) |
| **Anomaly Detection** | [Default-Services-Monitor](https://us-east-1.console.aws.amazon.com/cost-management/home#/anomaly-detection/monitors) |

**Current Cost Breakdown (November 2025):**

| Service | Cost | Notes |
|---------|------|-------|
| EC2 - Other | ~$0.59 | NAT Gateway (largest cost) |
| RDS | ~$0.24 | PostgreSQL db.t4g.micro |
| VPC | ~$0.12 | VPC resources |
| Lambda, SQS, CloudWatch | $0.00 | Free tier |

**To enable tag-based filtering:**
1. AWS Console → Billing → Cost allocation tags
2. Activate `Project` tag
3. Wait 24 hours

**To add email alerts** to the budget, edit `infra/__main__.py`:
```python
budget = ProjectBudget(
    "profile-scorer",
    monthly_limit_usd=10.0,
    notification_emails=["your@email.com"],
)
```

## Pulumi Stack Outputs

```bash
# Database
uv run pulumi stack output db_connection_string --show-secrets

# Lambda ARNs
uv run pulumi stack output orchestrator_arn
uv run pulumi stack output keyword_engine_arn
uv run pulumi stack output query_twitter_arn
uv run pulumi stack output llm_scorer_arn

# Lambda names (for invocation)
uv run pulumi stack output orchestrator_name
uv run pulumi stack output keyword_engine_name
uv run pulumi stack output query_twitter_name
uv run pulumi stack output llm_scorer_name

# Queues
uv run pulumi stack output keywords_queue_url
uv run pulumi stack output keywords_dlq_url

# Monitoring
uv run pulumi stack output dashboard_url
uv run pulumi stack output budget_name

# Resource Group
uv run pulumi stack output resource_group_arn
```

## Testing

```bash
# Invoke orchestrator
aws lambda invoke --function-name $(uv run pulumi stack output orchestrator_name) \
  --payload '{}' /tmp/out.json && cat /tmp/out.json

# Check queue depth
aws sqs get-queue-attributes \
  --queue-url $(uv run pulumi stack output keywords_queue_url) \
  --attribute-names ApproximateNumberOfMessages

# View logs
aws logs tail /aws/lambda/$(uv run pulumi stack output orchestrator_name) --since 5m
```
