/**
 * Human Authenticity Score (HAS) Package
 *
 * Exports:
 * - computeHAS: Main function with default config (hardcoded parameters)
 * - computeHASwithConfig: Configurable version for testing/optimization
 * - computeDetailedScores: Debug version with all intermediate values
 * - extractFeatures: Feature extraction function
 * - Types: All type definitions
 * - defaultConfig: Default configuration (can be used as base for modifications)
 */
import defaultConfigJson from "./config.json";
import { computeDetailedScores, computeHASwithConfig, extractFeatures } from "./scorer";
import { HASConfig, HASResult, ProfileData } from "./types";

// Export all types
export * from "./types";

// Export scorer functions
export { computeHASwithConfig, computeDetailedScores, extractFeatures };

/**
 * Default HAS configuration loaded from config.json.
 * Can be used as a base for creating modified configurations.
 */
export const defaultConfig: HASConfig = defaultConfigJson as HASConfig;

/**
 * Compute Human Authenticity Score using default configuration.
 * This is the main entry point for production use.
 *
 * @param profile - Raw profile data with numerical/boolean fields
 * @returns HAS result with score (0-1) and classification
 */
export function computeHAS(profile: ProfileData): HASResult {
  return computeHASwithConfig(profile, defaultConfig);
}

/**
 * Create a modified config by merging overrides with defaults.
 * Useful for parameter optimization experiments.
 *
 * @param overrides - Partial config to merge with defaults
 * @returns Complete config with overrides applied
 */
export function createConfig(overrides: Partial<HASConfig>): HASConfig {
  return {
    personWeights: { ...defaultConfig.personWeights, ...overrides.personWeights },
    activityThresholds: { ...defaultConfig.activityThresholds, ...overrides.activityThresholds },
    penaltyThresholds: { ...defaultConfig.penaltyThresholds, ...overrides.penaltyThresholds },
    classificationThresholds: {
      ...defaultConfig.classificationThresholds,
      ...overrides.classificationThresholds,
    },
    penalties: { ...defaultConfig.penalties, ...overrides.penalties },
  };
}

/**
 * Load config from a JSON string.
 * Useful for loading custom configs from files.
 *
 * @param jsonString - JSON string containing HASConfig
 * @returns Parsed HASConfig
 */
export function loadConfigFromJson(jsonString: string): HASConfig {
  return JSON.parse(jsonString) as HASConfig;
}
