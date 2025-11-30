import { readFileSync } from "fs";
import { join } from "path";

import { Handler } from "aws-lambda";

import { getDb, getProfilesToScore, insertProfileLabel } from "@profile-scorer/db";
import {
  AudienceConfig,
  LabelResult,
  getAvailableModels,
  labelProfiles,
  resolveModel,
} from "@profile-scorer/llm-scoring";
import { createLogger } from "@profile-scorer/utils";

const log = createLogger("llm-scorer");

/**
 * Event payload for llm-scorer Lambda.
 * Invoked directly by orchestrator (no SQS).
 */
export interface LlmScorerEvent {
  model: string; // Model alias (e.g., "claude-haiku-4.5")
  batchSize?: number;
  audienceConfigPath?: string; // Path to audience config JSON (default: thelai_customers.json)
}

/**
 * Response from llm-scorer Lambda.
 */
export interface LlmScorerResponse {
  model: string; // Returns the alias for logging consistency
  fullName: string; // Full model name used for DB storage
  labeled: number;
  errors: number;
  profiles: string[];
}

/**
 * Result of loading audience config.
 */
interface LoadedAudienceConfig {
  config: AudienceConfig;
  name: string; // Config name without .json extension (e.g., "thelai_customers.v1")
}

/**
 * Load audience config from JSON file.
 * Returns both the config and the resolved config name (for DB storage).
 */
function loadAudienceConfig(configName: string = "thelai_customers.v1"): LoadedAudienceConfig {
  // Try Lambda path first, then local path
  const paths = [
    join("/var/task", "audiences", `${configName}.json`),
    join(__dirname, "audiences", `${configName}.json`),
    join(process.cwd(), "lambdas/llm-scorer/src/audiences", `${configName}.json`),
  ];

  for (const path of paths) {
    try {
      const content = readFileSync(path, "utf-8");
      const config = JSON.parse(content) as AudienceConfig;
      log.info("Loaded audience config", { path, configName, targetProfile: config.targetProfile });
      return { config, name: configName };
    } catch {
      // Try next path
    }
  }

  throw new Error(`Could not load audience config: ${configName}`);
}

/**
 * LLM Scorer Lambda Handler
 *
 * Invoked directly by orchestrator with model alias and batchSize parameters.
 * Uses DB-as-queue pattern:
 * 1. Queries profiles_to_score for profiles not yet labeled by this model
 * 2. Sends profiles to appropriate LLM wrapper (using full model name for API)
 * 3. Stores labels in profile_scores table (using full model name for labeled_by)
 *
 * Concurrency safety: Each model is labeled independently, and the
 * profile_scores table has a unique constraint on (twitter_id, scored_by).
 * This prevents duplicate labeling even if multiple Lambdas run simultaneously.
 */
export const handler: Handler<LlmScorerEvent, LlmScorerResponse> = async (event) => {
  const { model: modelAlias, batchSize = 25, audienceConfigPath = "thelai_customers.v1" } = event;

  log.info("Starting labeling", { model: modelAlias, batchSize, audienceConfigPath });

  // Resolve model alias to full config
  let modelConfig;
  try {
    modelConfig = resolveModel(modelAlias);
  } catch (error) {
    const availableModels = getAvailableModels().join(", ");
    throw new Error(`Unknown model: ${modelAlias}. Available models: ${availableModels}`);
  }

  const { fullName } = modelConfig;

  // Load audience config
  const { config: audienceConfig, name: audienceName } = loadAudienceConfig(audienceConfigPath);

  // Initialize DB connection
  getDb();

  // Get profiles to label for this model (use fullName for DB query)
  const profiles = await getProfilesToScore(fullName, batchSize);

  log.info("Found profiles to label", { count: profiles.length });

  if (profiles.length === 0) {
    return {
      model: modelAlias,
      fullName,
      labeled: 0,
      errors: 0,
      profiles: [],
    };
  }

  // Label profiles using the llm-scoring package (pass alias, it resolves internally)
  let labels: LabelResult[];
  try {
    labels = await labelProfiles(profiles, modelAlias, audienceConfig);
    log.info("Got labels from model", { model: modelAlias, count: labels.length });
  } catch (error) {
    log.error("Error calling model", { model: modelAlias, error });
    throw error;
  }

  // Store labels in database (use fullName for labeled_by column)
  let labeled = 0;
  let errors = 0;
  const labeledProfiles: string[] = [];

  for (const result of labels) {
    try {
      await insertProfileLabel(result.twitterId, result.label, result.reason, fullName, audienceName);
      labeled++;
      labeledProfiles.push(result.twitterId);
      log.debug("Stored label", { twitterId: result.twitterId, label: result.label, audience: audienceName });
    } catch (error: any) {
      if (error.code === "23505") {
        // Unique violation - already labeled by this model
        log.debug("Profile already labeled", { twitterId: result.twitterId, model: modelAlias });
      } else {
        log.error("Error storing label", { twitterId: result.twitterId, error: error.message });
        errors++;
      }
    }
  }

  log.info("Labeling completed", { model: modelAlias, labeled, errors });

  return {
    model: modelAlias,
    fullName,
    labeled,
    errors,
    profiles: labeledProfiles,
  };
};
