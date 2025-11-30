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

## What was done then

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

## What was done then

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

- Despite having 9.5k profiles gathered and 12.4k combined scores on DB, the
final “LLM mean score + HAS heuristic” filter is underperforming, producing too
many false positives.
- We need to revisit key assumptions:
  - `@customers` should align with `AudienceConfig.thelai` and vice versa.
  - If all `@customers` score `>= 0.7`, false negatives stay low.
  - If all `@random` score `< 0.4`, false positives stay low.
- The dataset is skewed: we lack enough `@random` profiles (bots, orgs,
unrelated accounts). This likely caused overfitting. The prompt also fails to
consistently discard organizations and sometimes underrates good profiles.
- Current performance: `@customers` average score is `0.665` (target ~0.75),
with a mean percentile of 19.4%. Overall, the model is mediocre.
- Next step: focus on the SageMaker-based custom model. Build a curated,
hand-labeled dataset of 500 profiles (binary: good/bad), split 80% train / 20%
test, with a 30/70 good-to-bad ratio.
- Before that, increase the number of bad profiles in the DB. Expand the
`keyword` set to capture low-HAS accounts (bots, orgs).
- The scoring system requested in the system prompt (0.00-1.00) has proven to
be un reliable. Changing to a trivalent system (`true`, `false`, `null`)


- Improve the prompt through controlled tests on existing profiles. Remove the
`likely_is` field from prompts—it may bias LLM scoring. Prepare batches in TOON
format for benchmarking in Claude Console and generate scripts for batch
creation.

```TypeScript
// new LLM input
interface ParamsScoreProfile {
  handle: string;
  name: string | null;
  bio: string | null; // a null bio always must result in a `score: null`
  category: string | null;
  followers: number;  // added for context, how big of an account it is
}

// Zod schema for a single score result from LLM.
const ScoreItemSchema = z.object({
  username: z.string(),
  reason: z.string(),
  label: z.boolean().nullable()
});
```

- Original approach: treat this as a ranking problem—first gather profiles,
then cheaply filter junk with HAS, then apply an initial LLM-based label. Keep
numerical data (e.g., followers) out of the LLM prompt to avoid skewing its
output. Provide the LLM with batches of 25 TOON profiles to limit drift.
Combine LLM scores (text-based) with HAS (numeric/structural) for a more stable
final score. That was the initial design hypothesis.


## What was done then

- **LLM scoring shutdown**: Disabled llm-scorer invocation in orchestrator to
pause API costs
  - Location: `lambdas/orchestrator/src/handler.ts:120`
  - Method: Short-circuit with `&& false` (infrastructure preserved)
- **Code formatting**: Applied Prettier across codebase

## Issues faced

- First deploy only updated `llm-scorer` lambda, not `orchestrator` - had to
rebuild all lambdas before second deploy to pick up orchestrator changes

