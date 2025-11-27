/**
 * Human Authenticity Score (HAS) - Hybrid Heuristic
 *
 * Classifies Twitter/X users into categories using only numeric and boolean fields.
 * Combines multi-class classification with smooth mathematical normalizations.
 */

import { TwitterXapiUser, UserScore, TwitterUserType } from "@profile-scorer/db";

// ============================================================================
// Helper Functions
// ============================================================================

function sigmoid(x: number): number {
  return 1 / (1 + Math.exp(-x));
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function getDaysSinceCreation(createdAt: string): number {
  const created = new Date(createdAt);
  const now = new Date();
  return Math.max(0, (now.getTime() - created.getTime()) / (1000 * 60 * 60 * 24));
}

function roundTo4(value: number): number {
  return Math.round(value * 10000) / 10000;
}

// ============================================================================
// Feature Extraction
// ============================================================================

interface DerivedFeatures {
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

function extractFeatures(user: TwitterXapiUser): DerivedFeatures {
  const legacy = user.legacy;
  const days = getDaysSinceCreation(legacy.created_at);

  // Follower-Following Ratio (clamped log)
  const rawRatio = Math.log10((legacy.followers_count + 1) / (legacy.friends_count + 1));
  const R_ff = clamp(rawRatio, -2, 3);
  const R_ff_norm = (R_ff + 2) / 5;

  // Engagement Ratio
  const R_eng = Math.min(1, legacy.favourites_count / (legacy.statuses_count + 1));

  // List Credibility (tanh normalized)
  const R_list = Math.tanh(legacy.listed_count / 50);

  // Media Ratio
  const R_media = Math.min(1, legacy.media_count / (legacy.statuses_count + 1));

  // Account Age (exponential decay)
  const A_age = 1 - Math.exp(-days / 365);

  // Activity Rate
  const A_activity = legacy.statuses_count / (days + 1);

  // Profile Customization
  const P_custom = ((legacy.default_profile ? 0 : 1) + (legacy.default_profile_image ? 0 : 1)) / 2;

  // Content Safety
  const P_safe = 1 - 0.3 * (legacy.possibly_sensitive ? 1 : 0);

  // Verification Bonus
  const P_verified = 0.15 * (user.is_blue_verified ? 1 : 0);

  return {
    R_ff, R_ff_norm, R_eng, R_list, R_media,
    A_age, A_activity,
    P_custom, P_safe, P_verified,
    followers: legacy.followers_count,
    friends: legacy.friends_count,
    statuses: legacy.statuses_count,
    days
  };
}

// ============================================================================
// Classification Scores
// ============================================================================

function computeBotScore(f: DerivedFeatures): number {
  // Smooth indicators using sigmoid
  const S_hyperactive = sigmoid(0.1 * (f.A_activity - 50));
  const S_no_engage = sigmoid(5 * (0.1 - f.R_eng));
  const S_unbalanced = sigmoid(5 * (-1.5 - f.R_ff));
  const S_new = sigmoid(10 * (0.1 - f.A_age));

  return sigmoid(
    -3 +
    3 * S_hyperactive +
    2 * S_no_engage +
    1.5 * S_unbalanced +
    1.5 * (1 - f.P_custom) +
    1 * S_new
  );
}

function computePersonScore(f: DerivedFeatures): number {
  // S_balanced: peaks when R_ff ≈ 0 (balanced followers/following)
  const S_balanced = Math.max(0, 1 - 2 * Math.abs(f.R_ff_norm - 0.4));

  // S_normal_activity: peaks at moderate activity (1-5 tweets/day)
  let S_normal_activity: number;
  if (f.A_activity <= 5) {
    S_normal_activity = f.A_activity / 5;
  } else {
    S_normal_activity = Math.max(0, 1 - (f.A_activity - 5) / 15);
  }

  return (
    0.20 * f.P_custom +
    0.25 * f.R_eng +
    0.15 * f.A_age +
    0.10 * f.P_safe +
    0.15 * S_balanced +
    0.15 * S_normal_activity
  );
}

function computeCreatorScore(f: DerivedFeatures): number {
  // Large audience indicator
  const S_large_audience = sigmoid(0.0003 * (f.followers - 10000));
  // High follower ratio indicator
  const S_high_ratio = sigmoid(f.R_ff - 1);

  return sigmoid(
    -2.5 +
    1.5 * S_high_ratio +
    1.2 * f.R_media +
    0.8 * f.R_list +
    0.5 * f.P_verified / 0.15 + // Normalize P_verified back
    0.8 * S_large_audience
  );
}

function computeEntityScore(f: DerivedFeatures): number {
  // Consistent posting indicator (peaks around 3 tweets/day)
  const S_consistent = Math.max(0, 1 - Math.abs(f.A_activity - 3) / 10);
  // Very high follower ratio
  const S_very_high_ratio = sigmoid(f.R_ff - 1.7);

  return sigmoid(
    -2.5 +
    1.2 * S_very_high_ratio +
    0.8 * (1 - f.R_eng) +
    0.6 * f.R_media +
    0.5 * f.P_verified / 0.15 +
    0.8 * S_consistent
  );
}

// ============================================================================
// Penalty Calculation
// ============================================================================

function computePenalty(f: DerivedFeatures): number {
  let penalty = 1.0;

  // Very few followers
  if (f.followers < 5) {
    penalty *= 0.70;
  }

  // Zero tweets
  if (f.statuses === 0) {
    penalty *= 0.50;
  }

  // Very new account (< 30 days)
  if (f.days < 30) {
    penalty *= 0.80;
  }

  // Classic spam pattern: follows many, few followers
  if (f.friends > 5000 && f.followers < 100) {
    penalty *= 0.65;
  }

  return penalty;
}

// ============================================================================
// Main Classification Function
// ============================================================================

function computeHAS(user: TwitterXapiUser): UserScore {
  const features = extractFeatures(user);

  // Compute all scores
  const S_bot = computeBotScore(features);
  const S_person = computePersonScore(features);
  const S_creator = computeCreatorScore(features);
  const S_entity = computeEntityScore(features);
  const penalty = computePenalty(features);

  // Determine classification based on thresholds
  let likely_is: TwitterUserType;
  let rawScore: number;

  if (S_bot > 0.65) {
    likely_is = TwitterUserType.Bot;
    rawScore = 1 - S_bot;
  } else if (S_entity > 0.55 && S_bot < 0.5) {
    likely_is = TwitterUserType.Entity;
    rawScore = 1 - S_entity;
  } else if (S_creator > 0.55 && S_entity < 0.5 && S_bot < 0.5) {
    likely_is = TwitterUserType.Creator;
    rawScore = S_creator;
  } else if (S_person > 0.45) {
    likely_is = TwitterUserType.Human;
    rawScore = S_person;
  } else {
    // Use argmax for Other cases
    const scores = [
      { type: TwitterUserType.Human, score: S_person },
      { type: TwitterUserType.Creator, score: S_creator },
      { type: TwitterUserType.Entity, score: S_entity },
      { type: TwitterUserType.Bot, score: S_bot }
    ];
    scores.sort((a, b) => b.score - a.score);

    const best = scores[0]!;
    if (best.type === TwitterUserType.Bot || best.type === TwitterUserType.Entity) {
      likely_is = TwitterUserType.Other;
      rawScore = 0.5;
    } else {
      likely_is = best.type;
      rawScore = best.score;
    }
  }

  // Apply penalty and round
  const finalScore = roundTo4(clamp(rawScore * penalty, 0, 1));

  return {
    score: finalScore,
    likely_is
  };
}

// ============================================================================
// Export
// ============================================================================

export {
  computeHAS,
  extractFeatures,
  DerivedFeatures
};
