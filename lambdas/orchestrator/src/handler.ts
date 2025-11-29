import { InvokeCommand, LambdaClient } from "@aws-sdk/client-lambda";
import { SQSClient, SendMessageCommand } from "@aws-sdk/client-sqs";
import { ScheduledHandler } from "aws-lambda";
import { sql } from "drizzle-orm";

import { getDb, profilesToScore } from "@profile-scorer/db";
import { createLogger } from "@profile-scorer/utils";

const log = createLogger("orchestrator");
const lambda = new LambdaClient({});
const sqs = new SQSClient({});

// Environment variables (set by Pulumi)
const KEYWORD_ENGINE_ARN = process.env.KEYWORD_ENGINE_ARN ?? "";
const KEYWORDS_QUEUE_URL = process.env.KEYWORDS_QUEUE_URL ?? "";
const LLM_SCORER_ARN = process.env.LLM_SCORER_ARN ?? "";

/**
 * Model configuration with priority and probability.
 *
 * Priority Strategy:
 * - Haiku (1.0): Always runs, cost-effective for bulk scoring
 * - Sonnet (0.5): Higher quality but more expensive, runs ~50% of the time
 * - Gemini (0.2): Free tier contrast data, runs ~20% of the time
 *
 * This balances cost vs quality while ensuring all profiles get scored
 * by at least one model (Haiku) and building comparison data over time.
 */
interface ModelConfig {
  model: string;
  probability: number; // 0.0 to 1.0 - chance of running each orchestrator cycle
  batchSize: number; // profiles per invocation
}

const SCORING_MODELS: ModelConfig[] = [
  // Primary scorer - always runs, fast and cost-effective
  { model: "claude-haiku-4-5-20251001", probability: 1.0, batchSize: 25 },
  // Premium scorer - runs 50% of cycles for quality comparison
  { model: "claude-sonnet-4-20250514", probability: 0.5, batchSize: 10 },
  // Free tier contrast - runs 20% of cycles
  { model: "gemini-2.0-flash", probability: 0.2, batchSize: 15 },
];

interface KeywordEngineResponse {
  keywords: string[];
  stats?: {
    totalSearches: number;
    keywordYields: Record<string, number>;
  };
}

interface LlmScorerResponse {
  model: string;
  scored: number;
  errors: number;
  profiles: string[];
}

export const handler: ScheduledHandler = async (event) => {
  log.info("Starting pipeline orchestration");
  log.debug("Event received", { event });

  const results = {
    keywordsQueued: 0,
    scoringResults: [] as { model: string; scored: number; errors: number; skipped?: boolean }[],
    errors: [] as string[],
  };

  // Step 1: Get keywords from keyword-engine
  try {
    log.info("Invoking keyword-engine");

    const invokeResponse = await lambda.send(
      new InvokeCommand({
        FunctionName: KEYWORD_ENGINE_ARN,
        InvocationType: "RequestResponse",
        Payload: JSON.stringify({ action: "get_keywords", count: 5 }),
      })
    );

    const payloadStr = invokeResponse.Payload
      ? new TextDecoder().decode(invokeResponse.Payload)
      : "{}";
    const keywordResponse: KeywordEngineResponse = JSON.parse(payloadStr);

    log.info("Received keywords", { keywords: keywordResponse.keywords });

    // Step 2: Queue keywords for query-twitter-api
    for (const keyword of keywordResponse.keywords) {
      try {
        await sqs.send(
          new SendMessageCommand({
            QueueUrl: KEYWORDS_QUEUE_URL,
            MessageBody: JSON.stringify({ keyword, items: 20 }),
          })
        );
        results.keywordsQueued++;
        log.debug("Queued keyword", { keyword });
      } catch (err) {
        const msg = `Failed to queue keyword ${keyword}: ${err}`;
        log.error("Failed to queue keyword", { keyword, error: err });
        results.errors.push(msg);
      }
    }
  } catch (error) {
    const msg = `Failed to invoke keyword-engine: ${error}`;
    log.error("Failed to invoke keyword-engine", { error });
    results.errors.push(msg);
  }

  // Step 3: Check if there are profiles to score
  try {
    const db = getDb();
    const pendingCount = await db.select({ count: sql<number>`count(*)` }).from(profilesToScore);

    const count = Number(pendingCount[0]?.count ?? 0);
    log.info("Profiles pending scoring", { count });

    if (count > 0 && false) {
      // DISABLED: LLM scoring temporarily shut down
      // Step 4: Invoke llm-scorer for each model based on probability
      // Each invocation is independent - models don't interfere with each other
      // because profile_scores tracks (twitter_id, scored_by) uniquely
      for (const config of SCORING_MODELS) {
        const roll = Math.random();
        const shouldRun = roll < config.probability;

        log.debug("Model probability check", {
          model: config.model,
          probability: config.probability,
          roll: roll.toFixed(2),
          shouldRun,
        });

        if (!shouldRun) {
          results.scoringResults.push({
            model: config.model,
            scored: 0,
            errors: 0,
            skipped: true,
          });
          continue;
        }

        try {
          log.info("Invoking llm-scorer", { model: config.model, batchSize: config.batchSize });

          const scorerResponse = await lambda.send(
            new InvokeCommand({
              FunctionName: LLM_SCORER_ARN,
              InvocationType: "RequestResponse",
              Payload: JSON.stringify({
                model: config.model,
                batchSize: config.batchSize,
              }),
            })
          );

          const responseStr = scorerResponse.Payload
            ? new TextDecoder().decode(scorerResponse.Payload)
            : "{}";

          // Check for Lambda error
          if (scorerResponse.FunctionError) {
            const errorPayload = JSON.parse(responseStr);
            const msg = `llm-scorer error for ${config.model}: ${errorPayload.errorMessage || "Unknown error"}`;
            log.error("llm-scorer Lambda error", {
              model: config.model,
              error: errorPayload.errorMessage,
            });
            results.errors.push(msg);
            results.scoringResults.push({ model: config.model, scored: 0, errors: 1 });
            continue;
          }

          const scorerResult: LlmScorerResponse = JSON.parse(responseStr);
          results.scoringResults.push({
            model: config.model,
            scored: scorerResult.scored,
            errors: scorerResult.errors,
          });

          log.info("llm-scorer completed", {
            model: config.model,
            scored: scorerResult.scored,
            errors: scorerResult.errors,
          });
        } catch (err) {
          const msg = `Failed to invoke llm-scorer for ${config.model}: ${err}`;
          log.error("Failed to invoke llm-scorer", { model: config.model, error: err });
          results.errors.push(msg);
          results.scoringResults.push({ model: config.model, scored: 0, errors: 1 });
        }
      }
    } else {
      log.info("No profiles to score, skipping llm-scorer");
    }
  } catch (error) {
    const msg = `Failed to check profiles_to_score: ${error}`;
    log.error("Failed to check profiles_to_score", { error });
    results.errors.push(msg);
  }

  log.info("Pipeline orchestration completed", results);

  return results;
};
