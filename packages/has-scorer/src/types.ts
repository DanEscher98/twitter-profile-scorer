/**
 * Human Authenticity Score (HAS) Types
 *
 * This module defines all types used by the HAS scoring system.
 * Types are independent of any external API - just raw numerical/boolean values.
 */

/**
 * Raw profile data needed for HAS computation.
 * These are the minimal fields required - no API-specific types.
 */
export interface ProfileData {
  // Counts
  followers: number;
  following: number;
  statuses: number;
  favorites: number;
  listed: number;
  media: number;

  // Booleans
  isBlueVerified: boolean;
  defaultProfile: boolean;
  defaultProfileImage: boolean;
  possiblySensitive: boolean;

  // Account age
  createdAt: string; // ISO date string
}

/**
 * Derived features computed from raw profile data.
 * These are the intermediate values used in score calculations.
 */
export interface DerivedFeatures {
  // Ratios
  R_ff: number;           // Follower-following ratio (log, clamped)
  R_ff_norm: number;      // Normalized to [0,1]
  R_eng: number;          // Engagement ratio
  R_list: number;         // List credibility (tanh)
  R_media: number;        // Media ratio

  // Account maturity
  A_age: number;          // Account age score (exp decay)
  A_activity: number;     // Tweets per day

  // Profile signals
  P_custom: number;       // Profile customization
  P_safe: number;         // Content safety
  P_verified: number;     // Verification bonus

  // Raw values for thresholds
  followers: number;
  friends: number;
  statuses: number;
  days: number;
}

/**
 * Classification type for Twitter users.
 */
export enum UserType {
  Human = "Human",
  Creator = "Creator",
  Entity = "Entity",
  Other = "Other",
  Bot = "Bot",
}

/**
 * Result of HAS computation.
 */
export interface HASResult {
  score: number;
  likelyIs: UserType;
}

/**
 * Weights for the Person score calculation.
 */
export interface PersonScoreWeights {
  custom: number;           // Profile customization
  engaged: number;          // Engagement
  age: number;              // Account age
  safe: number;             // Content safety
  balanced: number;         // Follower/following balance
  normalActivity: number;   // Normal posting frequency
  established: number;      // Has established following
  moderateFollowing: number; // Not following too many
  reasonableVolume: number; // Reasonable tweet count
}

/**
 * Thresholds for activity scoring.
 */
export interface ActivityThresholds {
  veryLow: number;          // Below this = low activity score
  low: number;              // Below this = slightly reduced
  optimalMax: number;       // Sweet spot upper bound
  highMax: number;          // Above this = suspicious
  veryHighMax: number;      // Above this = very suspicious
}

/**
 * Penalty thresholds for various red flags.
 */
export interface PenaltyThresholds {
  // Follower thresholds
  veryFewFollowers: number;
  fewFollowers: number;

  // Status thresholds
  veryFewStatuses: number;
  fewStatuses: number;

  // Account age (days)
  veryNewDays: number;
  newDays: number;

  // Activity rate
  hyperactiveTweets: number;
  highActivityTweets: number;

  // Following patterns
  massFollowing: number;
  highFollowing: number;

  // High volume patterns
  hugeStatuses: number;
  veryHighStatuses: number;

  // Engagement patterns
  lowEngagementRate: number;
  highActivityForLowEngagement: number;
}

/**
 * Classification thresholds.
 */
export interface ClassificationThresholds {
  bot: number;
  entity: number;
  creator: number;
  human: number;
}

/**
 * Complete configuration for HAS computation.
 */
export interface HASConfig {
  personWeights: PersonScoreWeights;
  activityThresholds: ActivityThresholds;
  penaltyThresholds: PenaltyThresholds;
  classificationThresholds: ClassificationThresholds;

  // Penalty multipliers (applied to final score)
  // All values in (0, 1] where 1 = no penalty
  penalties: {
    // Account credibility
    veryFewFollowers: number;    // <10 followers
    fewFollowers: number;        // <50 followers
    zeroStatuses: number;        // 0 tweets
    veryFewStatuses: number;     // <10 tweets
    veryNewAccount: number;      // <30 days old
    newAccount: number;          // <90 days old

    // Spam patterns
    spamPattern: number;         // follows many, few followers
    hyperactive: number;         // >20 tweets/day
    highActivity: number;        // >10 tweets/day
    highVolumeNoFollowers: number; // >30k tweets, <3k followers

    // Profile signals
    defaultProfile: number;      // default avatar/banner
    lowEngagementHighActivity: number; // posts a lot, never likes
  };
}
