import { Handler } from "aws-lambda";
import { createLogger } from "@profile-scorer/utils";
import {
  getDb,
  getProfilesToScore,
  insertProfileScore,
  ProfileToScore,
} from "@profile-scorer/db";

// Import model wrappers
import { scoreWithAnthropic } from "./wrappers/anthropic";
import { scoreWithGemini } from "./wrappers/gemini";

const log = createLogger("llm-scorer");

/**
 * Event payload for llm-scorer Lambda.
 * Invoked directly by orchestrator (no SQS).
 */
export interface LlmScorerEvent {
  model: string;
  batchSize?: number;
}

/**
 * Response from llm-scorer Lambda.
 */
export interface LlmScorerResponse {
  model: string;
  scored: number;
  errors: number;
  profiles: string[];
}

/**
 * Score result from LLM wrapper.
 */
export interface ScoreResult {
  twitterId: string;
  score: number;
  reason: string;
}

/**
 * LLM wrapper function signature.
 * @param profiles - Profiles to score
 * @param model - Model identifier to use for the API call
 */
export type LlmWrapper = (profiles: ProfileToScore[], model: string) => Promise<ScoreResult[]>;

/**
 * Available LLM models and their wrappers.
 * The wrapper receives the model name to configure the API call.
 */
const MODEL_WRAPPERS: Record<string, LlmWrapper> = {
  // Anthropic models - wrapper uses model name for API call
  "claude-sonnet-4-20250514": scoreWithAnthropic,
  "claude-3-haiku-20240307": scoreWithAnthropic,
  // Google Gemini models
  "gemini-2.0-flash": scoreWithGemini,
  "gemini-1.5-flash": scoreWithGemini,
};

/**
 * LLM Scorer Lambda Handler
 *
 * Invoked directly by orchestrator with model and batchSize parameters.
 * Uses DB-as-queue pattern:
 * 1. Queries profiles_to_score for profiles not yet scored by this model
 * 2. Sends profiles to appropriate LLM wrapper
 * 3. Stores scores in profile_scores table
 *
 * Concurrency safety: Each model is scored independently, and the
 * profile_scores table has a unique constraint on (twitter_id, scored_by).
 * This prevents duplicate scoring even if multiple Lambdas run simultaneously.
 */
export const handler: Handler<LlmScorerEvent, LlmScorerResponse> = async (event) => {
  const { model, batchSize = 25 } = event;

  log.info("Starting scoring", { model, batchSize });

  // Validate model
  const wrapper = MODEL_WRAPPERS[model];
  if (!wrapper) {
    const availableModels = Object.keys(MODEL_WRAPPERS).join(", ");
    throw new Error(`Unknown model: ${model}. Available models: ${availableModels}`);
  }

  // Initialize DB connection
  getDb();

  // Get profiles to score for this model
  const profiles = await getProfilesToScore(model, batchSize);

  log.info("Found profiles to score", { count: profiles.length });

  if (profiles.length === 0) {
    return {
      model,
      scored: 0,
      errors: 0,
      profiles: [],
    };
  }

  // Score profiles using the appropriate wrapper
  let scores: ScoreResult[];
  try {
    scores = await wrapper(profiles, model);
    log.info("Got scores from model", { model, count: scores.length });
  } catch (error) {
    log.error("Error calling model", { model, error });
    throw error;
  }

  // Store scores in database
  let scored = 0;
  let errors = 0;
  const scoredProfiles: string[] = [];

  for (const score of scores) {
    try {
      await insertProfileScore(
        score.twitterId,
        score.score,
        score.reason,
        model
      );
      scored++;
      scoredProfiles.push(score.twitterId);
      log.debug("Stored score", { twitterId: score.twitterId, score: score.score.toFixed(2) });
    } catch (error: any) {
      if (error.code === "23505") {
        // Unique violation - already scored by this model
        log.debug("Profile already scored", { twitterId: score.twitterId, model });
      } else {
        log.error("Error storing score", { twitterId: score.twitterId, error: error.message });
        errors++;
      }
    }
  }

  log.info("Scoring completed", { model, scored, errors });

  return {
    model,
    scored,
    errors,
    profiles: scoredProfiles,
  };
};
