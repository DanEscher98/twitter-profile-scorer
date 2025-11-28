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
  getAllProfilesByKeyword,
  countAllByKeyword,
  insertProfileScore,
  upsertProfileScore,
  getDb,
} from "@profile-scorer/db";
import { createLogger } from "@profile-scorer/utils";
import { scoreWithAnthropic } from "./anthropic";
import { scoreWithGemini } from "./gemini";

export {
  ScoreResult,
  AudienceConfig,
  SYSTEM_PROMPT,
  generateSystemPrompt,
  formatProfilesPrompt,
  parseAndValidateResponse,
} from "./shared";
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
  "claude-opus-4-5-20251101": scoreWithAnthropic,
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
 * Score ALL profiles found with a specific keyword.
 * Fetches all profiles by keyword, scores them in batches of 30, and upserts results.
 *
 * Flow:
 * 1. Get ALL profiles labeled with keyword (not just unscored)
 * 2. Score them in batches of 30
 * 3. Upsert each score (insert or update if twitter_id + model already exists)
 *
 * @param keyword - The search keyword to filter profiles by
 * @param model - Model name from MODEL_WRAPPERS
 * @param onBatchComplete - Callback after each batch
 * @returns Total counts across all batches plus scored profiles with metadata
 */
export async function scoreByKeyword(
  keyword: string,
  model: string,
  onBatchComplete?: (batch: number, result: ScoreAndSaveResult) => void
): Promise<KeywordScoringResult> {
  const BATCH_SIZE = 30; // Fixed batch size of 30

  const wrapper = MODEL_WRAPPERS[model];
  if (!wrapper) {
    const availableModels = getAvailableModels().join(", ");
    throw new Error(`Unknown model: ${model}. Available models: ${availableModels}`);
  }

  // Initialize DB
  getDb();

  // Get total count of ALL profiles for this keyword
  const totalProfiles = await countAllByKeyword(keyword);
  log.info("Starting keyword scoring", { keyword, model, totalProfiles, batchSize: BATCH_SIZE });

  if (totalProfiles === 0) {
    return {
      totalScored: 0,
      totalErrors: 0,
      totalSkipped: 0,
      batches: 0,
      totalProfiles: 0,
      scoredProfiles: [],
    };
  }

  let totalScored = 0;
  let totalErrors = 0;
  let totalUpdated = 0;
  let batch = 0;
  let offset = 0;
  const allScoredProfiles: ScoredProfileWithMeta[] = [];

  while (offset < totalProfiles) {
    batch++;

    // Get ALL profiles for this batch (regardless of scoring status)
    const profiles = await getAllProfilesByKeyword(keyword, BATCH_SIZE, offset);

    if (profiles.length === 0) {
      break;
    }

    // Create lookup map for profile metadata
    const profileMap = new Map(profiles.map((p) => [p.twitterId, p]));

    // Score profiles with LLM
    const scores = await scoreProfiles(profiles, model);

    // Upsert to DB (insert or update)
    let scored = 0;
    let errors = 0;
    let updated = 0;
    const savedProfiles: ScoreResult[] = [];

    for (const score of scores) {
      try {
        const result = await upsertProfileScore(score.twitterId, score.score, score.reason, model);
        if (result === "updated") {
          updated++;
        } else {
          scored++;
        }
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
        errors++;
        log.error("Error upserting score", { twitterId: score.twitterId, error: error.message });
      }
    }

    totalScored += scored;
    totalErrors += errors;
    totalUpdated += updated;
    offset += profiles.length;

    const result: ScoreAndSaveResult = { model, scored, errors, skipped: updated, profiles: savedProfiles };

    if (onBatchComplete) {
      onBatchComplete(batch, result);
    }
  }

  log.info("Keyword scoring completed", {
    keyword,
    model,
    totalScored,
    totalUpdated,
    totalErrors,
    batches: batch,
  });

  return {
    totalScored,
    totalErrors,
    totalSkipped: totalUpdated,
    batches: batch,
    totalProfiles,
    scoredProfiles: allScoredProfiles,
  };
}
