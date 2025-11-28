/**
 * Human Authenticity Score (HAS) - Twitter API Adapter
 *
 * This module provides an adapter to use the has-scorer package with Twitter API data.
 * It converts TwitterXapiUser to ProfileData and re-exports the scoring functions.
 */

import {
  computeHAS as computeHASBase,
  computeHASwithConfig,
  computeDetailedScores,
  extractFeatures as extractFeaturesBase,
  ProfileData,
  HASResult,
  HASConfig,
  UserType,
  DerivedFeatures,
  defaultConfig,
  createConfig,
} from "@profile-scorer/has-scorer";
import { TwitterXapiUser, UserScore, TwitterUserType } from "@profile-scorer/db";

// ============================================================================
// Type Conversion
// ============================================================================

/**
 * Convert TwitterXapiUser to ProfileData for HAS scoring.
 */
export function toProfileData(user: TwitterXapiUser): ProfileData {
  const legacy = user.legacy;
  return {
    followers: legacy.followers_count,
    following: legacy.friends_count,
    statuses: legacy.statuses_count,
    favorites: legacy.favourites_count,
    listed: legacy.listed_count,
    media: legacy.media_count,
    isBlueVerified: user.is_blue_verified,
    defaultProfile: legacy.default_profile,
    defaultProfileImage: legacy.default_profile_image,
    possiblySensitive: legacy.possibly_sensitive,
    createdAt: legacy.created_at,
  };
}

/**
 * Convert UserType (from has-scorer) to TwitterUserType (from db).
 */
function toTwitterUserType(userType: UserType): TwitterUserType {
  switch (userType) {
    case UserType.Human:
      return TwitterUserType.Human;
    case UserType.Creator:
      return TwitterUserType.Creator;
    case UserType.Entity:
      return TwitterUserType.Entity;
    case UserType.Bot:
      return TwitterUserType.Bot;
    case UserType.Other:
    default:
      return TwitterUserType.Other;
  }
}

/**
 * Convert HASResult to UserScore (for compatibility with existing code).
 */
function toUserScore(result: HASResult): UserScore {
  return {
    score: result.score,
    likely_is: toTwitterUserType(result.likelyIs),
  };
}

// ============================================================================
// Main API
// ============================================================================

/**
 * Compute HAS score for a Twitter API user.
 * This is the main function used by the twitterx-api pipeline.
 *
 * @param user - TwitterXapiUser from the Twitter API
 * @returns UserScore with score and classification
 */
export function computeHAS(user: TwitterXapiUser): UserScore {
  const profileData = toProfileData(user);
  const result = computeHASBase(profileData);
  return toUserScore(result);
}

/**
 * Compute HAS score with a custom configuration.
 * Useful for parameter optimization experiments.
 *
 * @param user - TwitterXapiUser from the Twitter API
 * @param config - Custom HAS configuration
 * @returns UserScore with score and classification
 */
export function computeHASCustom(user: TwitterXapiUser, config: HASConfig): UserScore {
  const profileData = toProfileData(user);
  const result = computeHASwithConfig(profileData, config);
  return toUserScore(result);
}

/**
 * Extract derived features for analysis.
 *
 * @param user - TwitterXapiUser from the Twitter API
 * @returns DerivedFeatures with all intermediate values
 */
export function extractFeatures(user: TwitterXapiUser): DerivedFeatures {
  const profileData = toProfileData(user);
  return extractFeaturesBase(profileData);
}

/**
 * Get detailed scoring breakdown for debugging.
 *
 * @param user - TwitterXapiUser from the Twitter API
 * @param config - Optional custom configuration (defaults to default)
 * @returns All intermediate scores and final result
 */
export function getDetailedScores(user: TwitterXapiUser, config: HASConfig = defaultConfig) {
  const profileData = toProfileData(user);
  return computeDetailedScores(profileData, config);
}

// Re-export useful items from has-scorer
export {
  ProfileData,
  HASConfig,
  HASResult,
  UserType,
  DerivedFeatures,
  defaultConfig,
  createConfig,
};
