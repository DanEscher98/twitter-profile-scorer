# Development Log

Chronological record of development progress on Profile Scorer.

## Project overview

- Main Goal: Generate a custom audience of Twitter profiles of **qualitative
researchers** who may be potential clients of ThelAI.com. At least 250-500 high
quality profiles.
- Starting Point: a curated dataset of 28 Twitter handles from actual
customers and the description of the product
- The Product: **ThelAI** offers an all-in-one research platform designed to
manage academic and qualitative research studies. Its target audience is:
    - Academic researchers
    - Research teams conducting qualitative studies
    - Organizations needing secure tracking and data collection tools and
    IRB/HIPPA-compliant research infrastructure

---

# 2025-11-26 Wednesday

## Situation and Train of Thoughts

- The chosen API to gather profiles was `twitter-x-api.p.rapidapi.com/api`
that had a `/search/people?keyword=crypto` endpoint.
- We need a pool of `keywords` to perform queries. We also want to continuously
populate the `DB` with new profiles. The endpoint returns 20 profiles, then we
need to paginate to get new profiles from the same keyword. First, we'll
use a hardcoded list of keywords related to `research` as a starting point.
- A desirable component would be a custom fine-tuned LLM to classify profiles
that fit the target audience criteria. But currently the **28 dataset** of
actual customers is way too small to train anything. So first, we'll use
commercial general-purpose LLMs to label each profile.
- We'll focus first on the infrastructure. For this we'll use `Pulumi`,
to have infrastructure-as-code and keep it versionable. We'll deploy first the
`DB`, service to provide search `keywords` and a service to handle the queries.
- For the `DB` we want to not only query, but track the results of each query.
So each profile keeps track from which keyword it was found, and this will
later be a labeling on its own.
- To enhance `Claude` copilot, all the codebase will be in a monorepo. Proper
separation of concerns, so most of it is modular and atomic as possible. Second
priority is that the human developer can understand the whole codebase, avoiding
later spaghettification. 

## What was done

- **Project initialization**: Set up monorepo structure with Yarn workspaces,
`.editorconfig`, `.gitattributes`
- **AWS Infrastructure (Pulumi)**: Created VPC with public/private/isolated
subnets, RDS PostgreSQL, Lambda function components
- **Lambda scaffolding**: Created `keyword-engine` and `query-twitter-api`
lambdas with esbuild configs
- **Database layer**: Set up `@profile-scorer/db` package with Drizzle ORM and
schema definitions
- **SSL configuration**: Added AWS RDS CA certificate bundle for secure
database connections
- **Developer tooling**: Created Justfile with common commands (`deploy`,
`db-push`, `db-studio`)
- **Code quality**: Added Prettier, ESLint, and TypeScript workspace
configurations

## Issues faced

- Package naming inconsistencies between lambdas and workspace dependencies required fixes
- Lambda handlers needed corrections to use proper schema exports (`userProfiles`)
- RDS connections required SSL setup with AWS CA bundle - had to download and configure cert path
- Database access from local dev required moving RDS to public subnets (dev only) and adding security group rules

---

# 2025-11-27 Thursday


## Situation and Train of Thoughts

- The `DB` is getting populated, then proceed with the general-purpose LLM
labeling. To avoid consuming all the LLM budget we'll first filter profiles
with a custom heuristic, `Human Authenticity Score`, that combines several
numerical fields of a given profile (followers, following, media posted, likes,
etc.) to preemptively filter bots and organizations, so we spend LLM filtering
reasonable profiles.
- Focus is still on getting the infrastructure right to gather profiles and
cache most of the data. From `TwitterX` queries, to which LLM model is labeling
what, so later we'll have enough data to debug the system and improve on it. 
- First versions of the prompt to label profiles.

## What was done

- **Full Twitter pipeline**: Implemented complete profile collection flow:
  - `orchestrator` lambda coordinates pipeline (EventBridge 15-min trigger)
  - `keyword-engine` returns randomized keyword pool
  - `query-twitter-api` fetches profiles via RapidAPI, computes HAS scores
  - SQS queue for keyword processing with DLQ
- **HAS scoring package**: Extracted Human Authenticity Score logic to `@profile-scorer/has-scorer`
- **LLM scoring refactor**:
  - Removed SQS queue for scoring (direct Lambda invocation)
  - Added multi-model support (Claude Haiku, Sonnet, Gemini Flash)
  - Probability-based model invocation (100%, 50%, 20%)
- **E2E test suite**: Added comprehensive pytest tests for all lambdas (14 tests)
- **Documentation**: Created detailed Pulumi infrastructure docs, architecture diagrams
- **AWS Resource Group**: Added `profile-scorer-saas` resource group for easy console navigation

## Issues faced

- `new_profiles` count was always 0 in `xapi_usage_search` - fixed counting logic
- Keyword pool was hardcoded - tweaked to be more academic-focused
- LLM scoring via SQS was too complex - simplified to direct invocation pattern

---

# 2025-11-28 Friday


## Situation and Train of Thoughts

- If we decouple the tags, prompts and labeling, this system could evolve
beyond the original purpose (just get a custom audience for `ThelAI`) and
become its own SaaS. User inputs some curated profiles, plus some
metadata (context domain, tags, etc.) and the system returns a list of high
quality profiles: `(curated profiles, metadata) -> custom audience`.
- The specific prompt details of ThelAI audience are now in its own file.
- The latter motivated an enhancement over the `DB`, adding join tables and extra
fields to have more granular tracking of usage.
- I started detecting problems with the LLM labeling; several false positives
were the main concern. This motivated spending time implementing several debugging
scripts to generate plots to understand the distribution of scored profiles.
While the `HAS` heuristic somehow works, it sometimes over-penalizes some valid
profiles, but the reasoning is "it's preferable to have false negatives".

## What was done

- **CloudWatch Dashboard**: Added comprehensive monitoring with Lambda, RDS, SQS, NAT metrics
- **AWS Budget**: Set up $10/month budget with threshold alerts
- **Shared utilities**: Created `@profile-scorer/utils` package (renamed from logger) for CloudWatch-compatible logging
- **LLM scoring package**: Created `@profile-scorer/llm-scoring` for reusable scoring logic
- **Scoring scripts**:
  - `score-all-pending.ts` with parallel batching and blessed TUI
  - `score-by-keyword.ts` for targeted scoring
  - `export-high-scores.ts` for CSV export
- **Audience configuration**: Added `AudienceConfig` and `generateSystemPrompt` for customizable LLM prompts
- **Database enhancements**:
  - Added `keyword_stats` table with semantic tags
  - Made `keyword-engine` database-driven instead of hardcoded
- **API improvements**:
  - Added `TwitterXApiError` class with standardized error codes
  - Added `xapiGetUser` for single user lookups
- **Analysis tools**:
  - `validate-curated-leads.ts` for LLM hallucination detection
  - `export-low-scores.ts` for analyzing rejected profiles
  - `analyze_curated_performance.py` for false negative analysis with visualizations
- **Model upgrade**: Upgraded primary scorer to Claude Haiku 4.5
- **Final score formula**: Documented official equation: `0.2×HAS + 0.8×AVG_LLM`

## Issues faced

- ES modules `__dirname` not available - fixed with `import.meta.url` pattern
- LaTeX math syntax in docs didn't render on GitHub - converted to code blocks
- Mermaid diagrams had syntax errors - fixed formatting
- Merge conflict from parallel work - resolved
- `scoreByKeyword` only scored unscored profiles - refactored to upsert all for re-scoring

---

# 2025-11-29

## Situation and Train of Thoughts

- Even though we have over 9.5k profiles gathered, the test to generate a
filtered audience by combining the mean of LLM scores + the `HAS` heuristic, is
underperforming. A lot of false positives.
- We need to reevaluate the current premises:
    - `@customers` must fit `AudienceConfig.thelai`, and viceversa
    - if all `@customers` score `>=0.7` the system has low false negatives
    - if all `@random` score `<0.4` the system has low false positives
- The problem is, the current dataset has an under representation of `@random`
profiles (bots, organizations, non-related profiles), this may've been
overfitting the scorings. The prompt used maybe has problems too, not
discarding organizations as it should. Plus it underscores some desirable
profiles.
- Current situation: most of `@customers` have a mean score of `0.665`
(desirable 0.75). Their avg. percentile rank is in the top 19.4%. So the
system is mediocre at best. 
- We'll focus on the infrastructure for the custom LLM with SageMaker. We'll
dedicate time to create a curated hand-picked dataset of 500 profiles, labeled
in binary categories (good, bad). It will be divided as follows: 80% for
training, 20% for testing and 30% desired profiles vs 70% bad profiles.
- But first we need to increase the number of bad profiles in our DB. For this
we'll expand the original `keyword` set, adding words to try to find profiles
that have low `HAS` (bots, orgs).
- We'll also improve the prompt, doing several tests over already gathered
profiles. Remove the `likely_is` (generated by `HAS`) field from the prompt
because it may be misguiding the LLM scoring. For this we need batches of
profiles in TOON format, ready to test on `Claude Console`; we'll generate scripts for
this.


## What was done

- **LLM scoring shutdown**: Disabled llm-scorer invocation in orchestrator to
pause API costs
  - Location: `lambdas/orchestrator/src/handler.ts:120`
  - Method: Short-circuit with `&& false` (infrastructure preserved)
- **Code formatting**: Applied Prettier across codebase

## Issues faced

- First deploy only updated `llm-scorer` lambda, not `orchestrator` - had to
rebuild all lambdas before second deploy to pick up orchestrator changes

