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
 * - Groq/Meta (0.7): Fast inference, runs ~70% of the time
 * - Haiku (0.6): Cost-effective Claude, runs ~60% of the time
 * - Gemini (0.4): Free tier contrast data, runs ~40% of the time
 *
 * Uses simplified model aliases for logging. The llm-scorer lambda
 * resolves aliases to full model names for API calls and DB storage.
 */
interface ModelConfig {
  model: string; // Simplified alias (e.g., "claude-haiku-4.5")
  probability: number; // 0.0 to 1.0 - chance of running each orchestrator cycle
  batchSize: number; // profiles per invocation
}

const SCORING_MODELS: ModelConfig[] = [
  // Groq/Meta - fast inference, runs 70% of cycles
  { model: "meta-maverick-17b", probability: 0.7, batchSize: 25 },
  // Claude Haiku - cost-effective, runs 60% of cycles
  { model: "claude-haiku-4.5", probability: 0.6, batchSize: 25 },
  // Gemini Flash - free tier contrast, runs 40% of cycles
  { model: "gemini-flash-2.0", probability: 0.4, batchSize: 15 },
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
