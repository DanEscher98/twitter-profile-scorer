/**
 * @profile-scorer/llm-scoring
 *
 * LLM scoring utilities for Twitter profile analysis.
 *
 * Provides helpers for:
 * - Scoring profiles with Anthropic Claude or Google Gemini
 * - Batch scoring with automatic DB persistence
 * - Scoring profiles by keyword
 */

import {
  ProfileToScore,
  getProfilesToScore,
  getProfilesByKeyword,
  countUnscoredByKeyword,
  insertProfileScore,
  getDb,
} from "@profile-scorer/db";
import { createLogger } from "@profile-scorer/utils";
import { scoreWithAnthropic } from "./anthropic";
import { scoreWithGemini } from "./gemini";

export { ScoreResult, SYSTEM_PROMPT, formatProfilesPrompt, parseAndValidateResponse } from "./shared";
export { scoreWithAnthropic } from "./anthropic";
export { scoreWithGemini } from "./gemini";
import { ScoreResult } from "./shared";

const log = createLogger("llm-scoring");

/**
 * LLM wrapper function signature.
 */
export type LlmWrapper = (profiles: ProfileToScore[], model: string) => Promise<ScoreResult[]>;

/**
 * Available LLM models and their wrappers.
 */
export const MODEL_WRAPPERS: Record<string, LlmWrapper> = {
  // Anthropic models
  "claude-haiku-4-5-20251001": scoreWithAnthropic,
  "claude-sonnet-4-20250514": scoreWithAnthropic,
  // Google Gemini models
  "gemini-2.0-flash": scoreWithGemini,
  "gemini-1.5-flash": scoreWithGemini,
};

/**
 * Get the list of available models.
 */
export function getAvailableModels(): string[] {
  return Object.keys(MODEL_WRAPPERS);
}

/**
 * Score an array of profiles with a given model.
 * Does NOT save results to DB - just returns scores.
 *
 * @param profiles - Array of ProfileToScore to evaluate
 * @param model - Model name from MODEL_WRAPPERS
 * @returns Array of ScoreResult (may be fewer than input if LLM errors)
 */
export async function scoreProfiles(
  profiles: ProfileToScore[],
  model: string
): Promise<ScoreResult[]> {
  const wrapper = MODEL_WRAPPERS[model];
  if (!wrapper) {
    const availableModels = getAvailableModels().join(", ");
    throw new Error(`Unknown model: ${model}. Available models: ${availableModels}`);
  }

  if (profiles.length === 0) {
    return [];
  }

  log.info("Scoring profiles", { model, count: profiles.length });
  return await wrapper(profiles, model);
}

/**
 * Result from scoreAndSave operation.
 */
export interface ScoreAndSaveResult {
  model: string;
  scored: number;
  errors: number;
  skipped: number;
  profiles: ScoreResult[];
}

/**
 * Extended score result with profile metadata for CSV export.
 */
export interface ScoredProfileWithMeta {
  username: string;
  bio: string;
  hasScore: number;
  llmScore: number;
  reason: string;
}

/**
 * Result from scoreByKeyword operation with full profile data.
 */
export interface KeywordScoringResult {
  totalScored: number;
  totalErrors: number;
  totalSkipped: number;
  batches: number;
  totalProfiles: number;
  scoredProfiles: ScoredProfileWithMeta[];
}

/**
 * Score profiles and save results to DB.
 * Fetches profiles to score from DB, scores them, and saves results.
 *
 * @param model - Model name from MODEL_WRAPPERS
 * @param batchSize - Maximum profiles to score (default 25)
 * @param threshold - Minimum human_score to consider (default 0.6)
 * @returns ScoreAndSaveResult with counts and scored profiles
 */
export async function scoreAndSaveProfiles(
  model: string,
  batchSize: number = 25,
  threshold: number = 0.6
): Promise<ScoreAndSaveResult> {
  // Initialize DB
  getDb();

  // Get profiles to score for this model
  const profiles = await getProfilesToScore(model, batchSize, threshold);

  log.info("Found profiles to score", { model, count: profiles.length });

  if (profiles.length === 0) {
    return { model, scored: 0, errors: 0, skipped: 0, profiles: [] };
  }

  // Score profiles
  const scores = await scoreProfiles(profiles, model);

  // Save to DB
  let scored = 0;
  let errors = 0;
  let skipped = 0;
  const savedProfiles: ScoreResult[] = [];

  for (const score of scores) {
    try {
      await insertProfileScore(score.twitterId, score.score, score.reason, model);
      scored++;
      savedProfiles.push(score);
      log.debug("Stored score", { twitterId: score.twitterId, score: score.score.toFixed(2) });
    } catch (error: any) {
      if (error.code === "23505") {
        // Unique violation - already scored by this model
        skipped++;
        log.debug("Profile already scored", { twitterId: score.twitterId, model });
      } else {
        errors++;
        log.error("Error storing score", { twitterId: score.twitterId, error: error.message });
      }
    }
  }

  log.info("Scoring completed", { model, scored, errors, skipped });

  return { model, scored, errors, skipped, profiles: savedProfiles };
}

/**
 * Score all pending profiles for a model in batches.
 * Continues until no more profiles to score.
 *
 * @param model - Model name from MODEL_WRAPPERS
 * @param batchSize - Profiles per batch (default 25)
 * @param threshold - Minimum human_score (default 0.6)
 * @param onBatchComplete - Callback after each batch
 * @returns Total counts across all batches
 */
export async function scoreAllPending(
  model: string,
  batchSize: number = 25,
  threshold: number = 0.6,
  onBatchComplete?: (batch: number, result: ScoreAndSaveResult) => void
): Promise<{ totalScored: number; totalErrors: number; totalSkipped: number; batches: number }> {
  let totalScored = 0;
  let totalErrors = 0;
  let totalSkipped = 0;
  let batch = 0;

  while (true) {
    batch++;
    const result = await scoreAndSaveProfiles(model, batchSize, threshold);

    totalScored += result.scored;
    totalErrors += result.errors;
    totalSkipped += result.skipped;

    if (onBatchComplete) {
      onBatchComplete(batch, result);
    }

    // No more profiles to score
    if (result.scored === 0 && result.skipped === 0) {
      break;
    }
  }

  return { totalScored, totalErrors, totalSkipped, batches: batch };
}

/**
 * Score profiles found with a specific keyword.
 * Fetches profiles by keyword, scores them, and saves results.
 *
 * @param keyword - The search keyword to filter profiles by
 * @param model - Model name from MODEL_WRAPPERS
 * @param batchSize - Profiles per batch (default 25)
 * @param onBatchComplete - Callback after each batch
 * @returns Total counts across all batches plus scored profiles with metadata
 */
export async function scoreByKeyword(
  keyword: string,
  model: string,
  batchSize: number = 25,
  onBatchComplete?: (batch: number, result: ScoreAndSaveResult) => void
): Promise<KeywordScoringResult> {
  const wrapper = MODEL_WRAPPERS[model];
  if (!wrapper) {
    const availableModels = getAvailableModels().join(", ");
    throw new Error(`Unknown model: ${model}. Available models: ${availableModels}`);
  }

  // Initialize DB
  getDb();

  // Get total count first
  const totalProfiles = await countUnscoredByKeyword(keyword, model);
  log.info("Starting keyword scoring", { keyword, model, totalProfiles, batchSize });

  let totalScored = 0;
  let totalErrors = 0;
  let totalSkipped = 0;
  let batch = 0;
  const allScoredProfiles: ScoredProfileWithMeta[] = [];

  while (true) {
    batch++;

    // Get profiles for this batch
    const profiles = await getProfilesByKeyword(keyword, model, batchSize);

    if (profiles.length === 0) {
      break;
    }

    // Create lookup map for profile metadata
    const profileMap = new Map(profiles.map((p) => [p.twitterId, p]));

    // Score profiles
    const scores = await scoreProfiles(profiles, model);

    // Save to DB
    let scored = 0;
    let errors = 0;
    let skipped = 0;
    const savedProfiles: ScoreResult[] = [];

    for (const score of scores) {
      try {
        await insertProfileScore(score.twitterId, score.score, score.reason, model);
        scored++;
        savedProfiles.push(score);

        // Collect profile with metadata for CSV export
        const profile = profileMap.get(score.twitterId);
        if (profile) {
          allScoredProfiles.push({
            username: score.username,
            bio: profile.bio,
            hasScore: profile.humanScore,
            llmScore: score.score,
            reason: score.reason,
          });
        }
      } catch (error: any) {
        if (error.code === "23505") {
          skipped++;
        } else {
          errors++;
          log.error("Error storing score", { twitterId: score.twitterId, error: error.message });
        }
      }
    }

    totalScored += scored;
    totalErrors += errors;
    totalSkipped += skipped;

    const result: ScoreAndSaveResult = { model, scored, errors, skipped, profiles: savedProfiles };

    if (onBatchComplete) {
      onBatchComplete(batch, result);
    }

    // No more profiles scored or all already scored
    if (scored === 0 && skipped === 0) {
      break;
    }
  }

  log.info("Keyword scoring completed", { keyword, model, totalScored, totalErrors, totalSkipped, batches: batch });

  return {
    totalScored,
    totalErrors,
    totalSkipped,
    batches: batch,
    totalProfiles,
    scoredProfiles: allScoredProfiles,
  };
}
