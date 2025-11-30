/**
 * @profile-scorer/llm-scoring
 *
 * LLM labeling utilities for Twitter profile analysis.
 *
 * Provides helpers for:
 * - Labeling profiles with Anthropic Claude or Google Gemini
 * - Batch labeling with automatic DB persistence
 * - Labeling profiles by keyword
 */
import {
  ProfileToScore,
  countAllByKeyword,
  getAllProfilesByKeyword,
  getDb,
  getProfilesToScore,
  insertProfileLabel,
  upsertProfileLabel,
} from "@profile-scorer/db";
import { createLogger } from "@profile-scorer/utils";

import { labelWithAnthropic } from "./anthropic";
import { labelWithGemini } from "./gemini";
import { labelWithGroq } from "./groq";
import { AudienceConfig, LabelResult } from "./shared";

export {
  LabelResult,
  AudienceConfig,
  generateSystemPrompt,
  formatProfilesPrompt,
  parseAndValidateResponse,
} from "./shared";
export { labelWithAnthropic } from "./anthropic";
export { labelWithGemini } from "./gemini";
export { labelWithGroq } from "./groq";

const log = createLogger("llm-scoring");

/**
 * LLM wrapper function signature.
 */
export type LlmWrapper = (
  profiles: ProfileToScore[],
  model: string,
  audienceConfig: AudienceConfig
) => Promise<LabelResult[]>;

/**
 * Model configuration: maps simplified alias to full model name and wrapper.
 */
export interface ModelConfig {
  alias: string; // Simplified name for logging (e.g., "claude-haiku-4.5")
  fullName: string; // Full API model name (e.g., "claude-haiku-4-5-20251001")
  wrapper: LlmWrapper;
}

/**
 * Model registry: simplified alias -> full config.
 * Use aliases for orchestrator/logging, full names for API calls and DB storage.
 */
export const MODEL_REGISTRY: Record<string, ModelConfig> = {
  // Anthropic models
  "claude-haiku-4.5": {
    alias: "claude-haiku-4.5",
    fullName: "claude-haiku-4-5-20251001",
    wrapper: labelWithAnthropic,
  },
  "claude-sonnet-4.5": {
    alias: "claude-sonnet-4.5",
    fullName: "claude-sonnet-4-20250514",
    wrapper: labelWithAnthropic,
  },
  "claude-opus-4.5": {
    alias: "claude-opus-4.5",
    fullName: "claude-opus-4-5-20251101",
    wrapper: labelWithAnthropic,
  },
  // Google Gemini models
  "gemini-flash-2.0": {
    alias: "gemini-flash-2.0",
    fullName: "gemini-2.0-flash",
    wrapper: labelWithGemini,
  },
  "gemini-flash-1.5": {
    alias: "gemini-flash-1.5",
    fullName: "gemini-1.5-flash",
    wrapper: labelWithGemini,
  },
  // Groq models
  "meta-maverick-17b": {
    alias: "meta-maverick-17b",
    fullName: "meta-llama/llama-4-maverick-17b-128e-instruct",
    wrapper: labelWithGroq,
  },
};

/**
 * Get the list of available model aliases.
 */
export function getAvailableModels(): string[] {
  return Object.keys(MODEL_REGISTRY);
}

/**
 * Resolve a model alias to its full configuration.
 * @throws Error if alias is not found
 */
export function resolveModel(alias: string): ModelConfig {
  const config = MODEL_REGISTRY[alias];
  if (!config) {
    const available = getAvailableModels().join(", ");
    throw new Error(`Unknown model alias: ${alias}. Available: ${available}`);
  }
  return config;
}

/**
 * @deprecated Use MODEL_REGISTRY instead. Kept for backwards compatibility.
 */
export const MODEL_WRAPPERS: Record<string, LlmWrapper> = Object.fromEntries(
  Object.values(MODEL_REGISTRY).map((c) => [c.alias, c.wrapper])
);

/**
 * Label an array of profiles with a given model.
 * Does NOT save results to DB - just returns labels.
 *
 * @param profiles - Array of ProfileToScore to evaluate
 * @param modelAlias - Model alias (e.g., "claude-haiku-4.5")
 * @param audienceConfig - Audience configuration for generating system prompt
 * @returns Array of LabelResult (may be fewer than input if LLM errors)
 */
export async function labelProfiles(
  profiles: ProfileToScore[],
  modelAlias: string,
  audienceConfig: AudienceConfig
): Promise<LabelResult[]> {
  const config = MODEL_REGISTRY[modelAlias];
  if (!config) {
    const availableModels = getAvailableModels().join(", ");
    throw new Error(`Unknown model: ${modelAlias}. Available models: ${availableModels}`);
  }

  if (profiles.length === 0) {
    return [];
  }

  log.info("Labeling profiles", { model: modelAlias, count: profiles.length });
  // Pass the full model name to the wrapper for API calls
  return await config.wrapper(profiles, config.fullName, audienceConfig);
}

/**
 * Result from labelAndSave operation.
 */
export interface LabelAndSaveResult {
  model: string;
  labeled: number;
  errors: number;
  skipped: number;
  profiles: LabelResult[];
}

/**
 * Extended label result with profile metadata for CSV export.
 */
export interface LabeledProfileWithMeta {
  handle: string;
  bio: string;
  label: boolean | null;
  reason: string;
}

/**
 * Result from labelByKeyword operation with full profile data.
 */
export interface KeywordLabelingResult {
  totalLabeled: number;
  totalErrors: number;
  totalSkipped: number;
  batches: number;
  totalProfiles: number;
  labeledProfiles: LabeledProfileWithMeta[];
}

/**
 * Label profiles and save results to DB.
 * Fetches profiles to label from DB, labels them, and saves results.
 *
 * @param model - Model name from MODEL_WRAPPERS
 * @param audienceConfig - Audience configuration for generating system prompt
 * @param batchSize - Maximum profiles to label (default 25)
 * @param threshold - Minimum human_score to consider (default 0.6)
 * @returns LabelAndSaveResult with counts and labeled profiles
 */
export async function labelAndSaveProfiles(
  model: string,
  audienceConfig: AudienceConfig,
  batchSize: number = 25,
  threshold: number = 0.6
): Promise<LabelAndSaveResult> {
  // Initialize DB
  getDb();

  // Get profiles to label for this model
  const profiles = await getProfilesToScore(model, batchSize, threshold);

  log.info("Found profiles to label", { model, count: profiles.length });

  if (profiles.length === 0) {
    return { model, labeled: 0, errors: 0, skipped: 0, profiles: [] };
  }

  // Label profiles
  const labels = await labelProfiles(profiles, model, audienceConfig);

  // Save to DB
  let labeled = 0;
  let errors = 0;
  let skipped = 0;
  const savedProfiles: LabelResult[] = [];

  for (const result of labels) {
    try {
      await insertProfileLabel(result.twitterId, result.label, result.reason, model);
      labeled++;
      savedProfiles.push(result);
      log.debug("Stored label", { twitterId: result.twitterId, label: result.label });
    } catch (error: any) {
      if (error.code === "23505") {
        // Unique violation - already labeled by this model
        skipped++;
        log.debug("Profile already labeled", { twitterId: result.twitterId, model });
      } else {
        errors++;
        log.error("Error storing label", { twitterId: result.twitterId, error: error.message });
      }
    }
  }

  log.info("Labeling completed", { model, labeled, errors, skipped });

  return { model, labeled, errors, skipped, profiles: savedProfiles };
}

/**
 * Label all pending profiles for a model in batches.
 * Continues until no more profiles to label.
 *
 * @param model - Model name from MODEL_WRAPPERS
 * @param audienceConfig - Audience configuration for generating system prompt
 * @param batchSize - Profiles per batch (default 25)
 * @param threshold - Minimum human_score (default 0.6)
 * @param onBatchComplete - Callback after each batch
 * @returns Total counts across all batches
 */
export async function labelAllPending(
  model: string,
  audienceConfig: AudienceConfig,
  batchSize: number = 25,
  threshold: number = 0.6,
  onBatchComplete?: (batch: number, result: LabelAndSaveResult) => void
): Promise<{ totalLabeled: number; totalErrors: number; totalSkipped: number; batches: number }> {
  let totalLabeled = 0;
  let totalErrors = 0;
  let totalSkipped = 0;
  let batch = 0;

  while (true) {
    batch++;
    const result = await labelAndSaveProfiles(model, audienceConfig, batchSize, threshold);

    totalLabeled += result.labeled;
    totalErrors += result.errors;
    totalSkipped += result.skipped;

    if (onBatchComplete) {
      onBatchComplete(batch, result);
    }

    // No more profiles to label
    if (result.labeled === 0 && result.skipped === 0) {
      break;
    }
  }

  return { totalLabeled, totalErrors, totalSkipped, batches: batch };
}

/**
 * Label ALL profiles found with a specific keyword.
 * Fetches all profiles by keyword, labels them in batches of 30, and upserts results.
 *
 * Flow:
 * 1. Get ALL profiles labeled with keyword (not just unlabeled)
 * 2. Label them in batches of 30
 * 3. Upsert each label (insert or update if twitter_id + model already exists)
 *
 * @param keyword - The search keyword to filter profiles by
 * @param model - Model name from MODEL_WRAPPERS
 * @param audienceConfig - Audience configuration for generating system prompt
 * @param onBatchComplete - Callback after each batch
 * @returns Total counts across all batches plus labeled profiles with metadata
 */
export async function labelByKeyword(
  keyword: string,
  model: string,
  audienceConfig: AudienceConfig,
  onBatchComplete?: (batch: number, result: LabelAndSaveResult) => void
): Promise<KeywordLabelingResult> {
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
  log.info("Starting keyword labeling", { keyword, model, totalProfiles, batchSize: BATCH_SIZE });

  if (totalProfiles === 0) {
    return {
      totalLabeled: 0,
      totalErrors: 0,
      totalSkipped: 0,
      batches: 0,
      totalProfiles: 0,
      labeledProfiles: [],
    };
  }

  let totalLabeled = 0;
  let totalErrors = 0;
  let totalUpdated = 0;
  let batch = 0;
  let offset = 0;
  const allLabeledProfiles: LabeledProfileWithMeta[] = [];

  while (offset < totalProfiles) {
    batch++;

    // Get ALL profiles for this batch (regardless of labeling status)
    const profiles = await getAllProfilesByKeyword(keyword, BATCH_SIZE, offset);

    if (profiles.length === 0) {
      break;
    }

    // Create lookup map for profile metadata
    const profileMap = new Map(profiles.map((p) => [p.twitterId, p]));

    // Label profiles with LLM
    const labels = await labelProfiles(profiles, model, audienceConfig);

    // Upsert to DB (insert or update)
    let labeled = 0;
    let errors = 0;
    let updated = 0;
    const savedProfiles: LabelResult[] = [];

    for (const result of labels) {
      try {
        const upsertResult = await upsertProfileLabel(
          result.twitterId,
          result.label,
          result.reason,
          model
        );
        if (upsertResult === "updated") {
          updated++;
        } else {
          labeled++;
        }
        savedProfiles.push(result);

        // Collect profile with metadata for CSV export
        const profile = profileMap.get(result.twitterId);
        if (profile) {
          allLabeledProfiles.push({
            handle: result.handle,
            bio: profile.bio,
            label: result.label,
            reason: result.reason,
          });
        }
      } catch (error: any) {
        errors++;
        log.error("Error upserting label", { twitterId: result.twitterId, error: error.message });
      }
    }

    totalLabeled += labeled;
    totalErrors += errors;
    totalUpdated += updated;
    offset += profiles.length;

    const batchResult: LabelAndSaveResult = {
      model,
      labeled,
      errors,
      skipped: updated,
      profiles: savedProfiles,
    };

    if (onBatchComplete) {
      onBatchComplete(batch, batchResult);
    }
  }

  log.info("Keyword labeling completed", {
    keyword,
    model,
    totalLabeled,
    totalUpdated,
    totalErrors,
    batches: batch,
  });

  return {
    totalLabeled,
    totalErrors,
    totalSkipped: totalUpdated,
    batches: batch,
    totalProfiles,
    labeledProfiles: allLabeledProfiles,
  };
}
