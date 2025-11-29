/**
 * Human Authenticity Score (HAS) Scorer
 *
 * Classifies Twitter/X profiles into types (Human, Creator, Entity, Bot, Other)
 * and assigns a confidence score for ad targeting purposes.
 *
 * Design Philosophy:
 * - Conservative scoring: better to reject real humans than accept bots/orgs
 * - All scores normalized to [0,1] for fair comparison
 * - Single penalty system applied once at the end
 * - Clear separation between feature extraction, scoring, and classification
 *
 * @see README.md for detailed equations and reasoning
 */
import { DerivedFeatures, HASConfig, HASResult, ProfileData, UserType } from "./types";

// ============================================================================
// Mathematical Helpers
// ============================================================================

/** Standard sigmoid function: σ(x) = 1 / (1 + e^(-x)) */
function sigmoid(x: number): number {
  return 1 / (1 + Math.exp(-x));
}

/** Clamp value to [min, max] range */
function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

/** Smooth step function using sigmoid: transitions from 0 to 1 around midpoint */
function smoothStep(x: number, midpoint: number, steepness: number): number {
  return sigmoid(steepness * (x - midpoint));
}

/** Inverse smooth step: transitions from 1 to 0 around midpoint */
function invSmoothStep(x: number, midpoint: number, steepness: number): number {
  return 1 - smoothStep(x, midpoint, steepness);
}

/** Bell curve centered at peak with given width: max 1 at peak, decreasing away */
function bellCurve(x: number, peak: number, width: number): number {
  const z = (x - peak) / width;
  return Math.exp(-z * z);
}

/** Days since account creation */
function getDaysSinceCreation(createdAt: string): number {
  const created = new Date(createdAt);
  const now = new Date();
  return Math.max(0, (now.getTime() - created.getTime()) / (1000 * 60 * 60 * 24));
}

/** Round to 4 decimal places */
function roundTo4(value: number): number {
  return Math.round(value * 10000) / 10000;
}

// ============================================================================
// Feature Extraction
// ============================================================================

/**
 * Extract derived features from raw profile data.
 *
 * Features are designed to be:
 * - Normalized to comparable scales where possible
 * - Robust to edge cases (divide by zero, missing data)
 * - Interpretable for debugging
 */
export function extractFeatures(profile: ProfileData): DerivedFeatures {
  const days = getDaysSinceCreation(profile.createdAt);

  // ---- Follower-Following Ratio ----
  // R_ff = log10((followers + 1) / (following + 1))
  // Range: typically -2 to +3
  // Interpretation:
  //   R_ff < 0: follows more than followed (spam pattern or new user)
  //   R_ff ≈ 0: balanced (typical user)
  //   R_ff > 1: more followers than following (creator/influencer)
  //   R_ff > 2: much more followers (celebrity/org)
  const rawRatio = Math.log10((profile.followers + 1) / (profile.following + 1));
  const R_ff = clamp(rawRatio, -2, 3);

  // Normalized to [0,1]: R_ff_norm = (R_ff + 2) / 5
  // 0.0 = very negative ratio (spam)
  // 0.4 = balanced
  // 1.0 = very high ratio (celebrity)
  const R_ff_norm = (R_ff + 2) / 5;

  // ---- Engagement Ratio ----
  // R_eng = favorites / (statuses + 1)
  // Measures: how much this user engages with others' content (liking)
  // High values suggest active human behavior
  // Note: This is NOT how engaging their content is (we don't have that data)
  const R_eng = Math.min(1, profile.favorites / (profile.statuses + 1));

  // ---- List Membership ----
  // R_list = tanh(listed / 50)
  // Being on lists suggests credibility/notability
  // tanh gives diminishing returns past ~50 lists
  const R_list = Math.tanh(profile.listed / 50);

  // ---- Media Ratio ----
  // R_media = media / (statuses + 1)
  // High media ratio suggests content creator
  const R_media = Math.min(1, profile.media / (profile.statuses + 1));

  // ---- Account Age ----
  // A_age = 1 - e^(-days / 365)
  // Asymptotically approaches 1 as account ages
  // ~0.63 at 1 year, ~0.86 at 2 years, ~0.95 at 3 years
  const A_age = 1 - Math.exp(-days / 365);

  // ---- Activity Rate ----
  // A_activity = statuses / (days + 1)
  // Tweets per day on average
  // Typical human: 0.5-3 tweets/day
  // Hyperactive: >10 tweets/day
  const A_activity = profile.statuses / (days + 1);

  // ---- Profile Customization ----
  // P_custom ∈ {0, 0.5, 1}
  // 0 = default profile AND default image (strong spam signal)
  // 0.5 = one customized
  // 1 = both customized
  const P_custom = ((profile.defaultProfile ? 0 : 1) + (profile.defaultProfileImage ? 0 : 1)) / 2;

  // ---- Content Safety ----
  // P_safe = 1 - 0.3 * possibly_sensitive
  // Slight penalty for sensitive content flag
  const P_safe = 1 - 0.3 * (profile.possiblySensitive ? 1 : 0);

  // ---- Verification ----
  // P_verified ∈ {0, 1}
  // Blue checkmark (paid verification)
  const P_verified = profile.isBlueVerified ? 1 : 0;

  return {
    R_ff,
    R_ff_norm,
    R_eng,
    R_list,
    R_media,
    A_age,
    A_activity,
    P_custom,
    P_safe,
    P_verified,
    followers: profile.followers,
    friends: profile.following,
    statuses: profile.statuses,
    days,
  };
}

// ============================================================================
// Type-Specific Scoring Functions
// ============================================================================

/**
 * Bot Score: Probability that this account is automated.
 *
 * Bot indicators:
 * - Hyperactive posting (>50 tweets/day)
 * - Very low engagement ratio (doesn't like content)
 * - Following >> Followers (mass following pattern)
 * - Default profile (no customization)
 * - Very new account
 *
 * S_bot = σ(-3 + 3·S_hyperactive + 2·S_no_engage + 1.5·S_unbalanced + 1.5·(1-P_custom) + S_new)
 */
function computeBotScore(f: DerivedFeatures): number {
  // Hyperactive: >50 tweets/day is extremely suspicious
  const S_hyperactive = smoothStep(f.A_activity, 50, 0.1);

  // No engagement: R_eng < 0.1 suggests automated
  const S_no_engage = invSmoothStep(f.R_eng, 0.1, 5);

  // Unbalanced: follows many more than followed (R_ff < -1.5)
  const S_unbalanced = invSmoothStep(f.R_ff, -1.5, 5);

  // New account: A_age < 0.1 (~36 days)
  const S_new = invSmoothStep(f.A_age, 0.1, 10);

  return sigmoid(
    -3 +
      3 * S_hyperactive +
      2 * S_no_engage +
      1.5 * S_unbalanced +
      1.5 * (1 - f.P_custom) +
      1 * S_new
  );
}

/**
 * Creator Score: Probability that this is a content creator/influencer.
 *
 * Creator indicators:
 * - Large follower count (>10k)
 * - High follower ratio (R_ff > 1, more followers than following)
 * - High media content
 * - Listed on many lists
 * - Verified
 *
 * S_creator = σ(-2.5 + 1.5·S_high_ratio + 1.2·R_media + 0.8·R_list + 0.5·P_verified + 0.8·S_large_audience)
 */
function computeCreatorScore(f: DerivedFeatures): number {
  // Large audience: >10k followers
  const S_large_audience = smoothStep(f.followers, 10000, 0.0003);

  // High ratio: R_ff > 1 (10x more followers than following)
  const S_high_ratio = smoothStep(f.R_ff, 1, 1);

  return sigmoid(
    -2.5 +
      1.5 * S_high_ratio +
      1.2 * f.R_media +
      0.8 * f.R_list +
      0.5 * f.P_verified +
      0.8 * S_large_audience
  );
}

/**
 * Entity Score: Probability that this is an organization/brand account.
 *
 * Entity indicators:
 * - Very high follower ratio (R_ff > 1.7)
 * - Consistent posting (~3 tweets/day)
 * - Low personal engagement (orgs don't "like" as much)
 * - Media content
 * - Verified
 *
 * S_entity = σ(-2.5 + 1.2·S_high_ratio + 0.8·(1-R_eng) + 0.6·R_media + 0.5·P_verified + 0.8·S_consistent)
 */
function computeEntityScore(f: DerivedFeatures): number {
  // Consistent posting: peaks at ~3 tweets/day
  const S_consistent = bellCurve(f.A_activity, 3, 5);

  // Very high ratio: R_ff > 1.7 (50x more followers)
  const S_very_high_ratio = smoothStep(f.R_ff, 1.7, 1);

  return sigmoid(
    -2.5 +
      1.2 * S_very_high_ratio +
      0.8 * (1 - f.R_eng) +
      0.6 * f.R_media +
      0.5 * f.P_verified +
      0.8 * S_consistent
  );
}

/**
 * Person Score: Probability that this is a regular human user.
 *
 * Human indicators:
 * - Balanced follower/following ratio (R_ff ≈ 0)
 * - Moderate activity (0.5-2 tweets/day)
 * - Some engagement (likes others' content)
 * - Customized profile
 * - Established account (some age, some followers)
 * - Reasonable tweet volume (<10k lifetime)
 *
 * Design goals:
 * - Most humans score 0.60-0.80
 * - Exceptional humans score 0.85-0.92
 * - Score 0.95+ requires verified + perfect signals (very rare)
 * - Uses sqrt/log curves so signals don't max out easily
 *
 * S_person = Σ(wi · Si) where weights sum to ~0.90
 * Verification bonus can add up to 0.08 for exceptional profiles
 */
function computePersonScore(f: DerivedFeatures, config: HASConfig): number {
  const w = config.personWeights;
  const t = config.activityThresholds;

  // ---- Signal Functions ----
  // Use sqrt/log curves to make maxing out harder

  // Balanced ratio: bell curve peaks when R_ff ≈ 0
  // Most humans will score 0.6-0.9 here, not 1.0
  const S_balanced = bellCurve(f.R_ff_norm, 0.4, 0.25);

  // Normal activity: piecewise with softer transitions
  // 0-0.1 tweets/day: 0.5 (lurkers are okay but not ideal)
  // 0.1-0.5: linear increase to 0.9
  // 0.5-2: 0.9-1.0 (optimal range)
  // 2-4: 0.7-0.8 (slightly high)
  // 4-8: 0.4-0.5 (suspicious)
  // >8: 0.2 (very suspicious)
  let S_normal_activity: number;
  if (f.A_activity < t.veryLow) {
    S_normal_activity = 0.5;
  } else if (f.A_activity < t.low) {
    S_normal_activity = 0.5 + (0.4 * (f.A_activity - t.veryLow)) / (t.low - t.veryLow);
  } else if (f.A_activity <= t.optimalMax) {
    // Peak in optimal range, slightly below 1.0
    S_normal_activity = 0.9 + 0.1 * bellCurve(f.A_activity, 1.0, 0.5);
  } else if (f.A_activity <= t.highMax) {
    S_normal_activity = 0.75;
  } else if (f.A_activity <= t.veryHighMax) {
    S_normal_activity = 0.45;
  } else {
    S_normal_activity = 0.2;
  }

  // Established following: logarithmic curve, harder to max
  // 50 followers: ~0.56, 200: ~0.76, 500: ~0.89, 1000: ~0.96
  const S_established = Math.min(1, Math.log10(f.followers + 1) / 3);

  // Moderate following: smooth penalty curve
  // Penalize gradually above 1000, heavily above 3000
  let S_moderate_following: number;
  if (f.friends <= 500) {
    S_moderate_following = 1.0;
  } else if (f.friends <= 1500) {
    S_moderate_following = 1.0 - (0.15 * (f.friends - 500)) / 1000;
  } else if (f.friends <= 3000) {
    S_moderate_following = 0.85 - (0.25 * (f.friends - 1500)) / 1500;
  } else {
    S_moderate_following = Math.max(0.3, 0.6 - (0.1 * (f.friends - 3000)) / 2000);
  }

  // Engagement: sqrt curve, harder to max
  // R_eng=0.3: ~0.77, R_eng=0.5: ~0.89, R_eng=1.0: 1.0
  const S_engaged = Math.min(1, Math.sqrt(f.R_eng * 2));

  // Reasonable volume: smooth decay
  // <5k: 1.0, 5k-10k: gradual decay to 0.8, 10k-20k: decay to 0.5
  let S_reasonable_volume: number;
  if (f.statuses <= 5000) {
    S_reasonable_volume = 1.0;
  } else if (f.statuses <= 10000) {
    S_reasonable_volume = 1.0 - (0.2 * (f.statuses - 5000)) / 5000;
  } else if (f.statuses <= 20000) {
    S_reasonable_volume = 0.8 - (0.3 * (f.statuses - 10000)) / 10000;
  } else {
    S_reasonable_volume = Math.max(0.3, 0.5 - (0.1 * (f.statuses - 20000)) / 10000);
  }

  // Account age: slower saturation
  // 1 year: ~0.63, 2 years: ~0.73, 3 years: ~0.80, 5 years: ~0.87
  const S_age = 1 - Math.exp(-f.days / 500);

  // ---- Weighted Sum ----
  // Weights sum to ~0.90, leaving room for verification bonus
  const baseScore =
    w.custom * f.P_custom +
    w.engaged * S_engaged +
    w.age * S_age +
    w.safe * f.P_safe +
    w.balanced * S_balanced +
    w.normalActivity * S_normal_activity +
    w.established * S_established +
    w.moderateFollowing * S_moderate_following +
    w.reasonableVolume * S_reasonable_volume;

  // Verification bonus: adds up to 0.08 for verified accounts
  // Only applies if base score is already high (>0.7)
  // This allows exceptional verified humans to reach 0.95+
  const verificationBonus = f.P_verified * 0.08 * smoothStep(baseScore, 0.7, 10);

  return Math.min(1, baseScore + verificationBonus);
}

// ============================================================================
// Global Penalty Function
// ============================================================================

/**
 * Compute penalty multiplier for suspicious patterns.
 *
 * Penalties are applied AFTER classification to the final score.
 * They represent strong red flags that should reduce confidence
 * regardless of the detected type.
 *
 * Penalty is multiplicative: final_score = raw_score × penalty
 * penalty ∈ (0, 1] where 1 = no penalty
 */
function computePenalty(f: DerivedFeatures, config: HASConfig): number {
  const t = config.penaltyThresholds;
  const p = config.penalties;
  let penalty = 1.0;

  // ---- Account Credibility ----

  // Very few followers: likely inactive or spam
  if (f.followers < t.veryFewFollowers) {
    penalty *= p.veryFewFollowers;
  } else if (f.followers < t.fewFollowers) {
    penalty *= p.fewFollowers;
  }

  // Zero or very few tweets: can't verify authenticity
  if (f.statuses === 0) {
    penalty *= p.zeroStatuses;
  } else if (f.statuses < t.veryFewStatuses) {
    penalty *= p.veryFewStatuses;
  }

  // Very new account: high spam risk
  if (f.days < t.veryNewDays) {
    penalty *= p.veryNewAccount;
  } else if (f.days < t.newDays) {
    penalty *= p.newAccount;
  }

  // ---- Spam Patterns ----

  // Classic spam: follows many, few followers back
  if (f.friends > t.massFollowing && f.followers < 100) {
    penalty *= p.spamPattern;
  }

  // Hyperactive posting
  if (f.A_activity > t.hyperactiveTweets) {
    penalty *= p.hyperactive;
  } else if (f.A_activity > t.highActivityTweets) {
    penalty *= p.highActivity;
  }

  // High volume without proportional audience
  // If you have 30k tweets but only 3k followers, suspicious
  if (f.statuses > t.hugeStatuses && f.followers < f.statuses / 10) {
    penalty *= p.highVolumeNoFollowers;
  }

  // ---- Profile Signals ----

  // Default profile image: strong spam signal
  if (f.P_custom < 0.5) {
    penalty *= p.defaultProfile;
  }

  // Low engagement + high activity: bot pattern
  if (f.R_eng < t.lowEngagementRate && f.A_activity > t.highActivityForLowEngagement) {
    penalty *= p.lowEngagementHighActivity;
  }

  return penalty;
}

// ============================================================================
// Main Classification Function
// ============================================================================

/**
 * Compute Human Authenticity Score with configurable parameters.
 *
 * Classification Priority (highest to lowest):
 * 1. Bot - if S_bot > threshold (dangerous, reject first)
 * 2. Entity - if S_entity > threshold AND not likely bot
 * 3. Creator - if S_creator > threshold AND not entity/bot
 * 4. Human - if S_person > threshold
 * 5. Other - fallback when no clear classification
 *
 * Final score = rawScore × penalty
 * - For Human/Creator: rawScore = class score (higher = more confident)
 * - For Bot/Entity: rawScore = 1 - class score (lower score = worse for ads)
 *
 * @param profile - Raw profile data
 * @param config - HAS configuration with all weights and thresholds
 * @returns HAS result with score and classification
 */
export function computeHASwithConfig(profile: ProfileData, config: HASConfig): HASResult {
  const features = extractFeatures(profile);
  const ct = config.classificationThresholds;

  // Compute type-specific scores
  const S_bot = computeBotScore(features);
  const S_person = computePersonScore(features, config);
  const S_creator = computeCreatorScore(features);
  const S_entity = computeEntityScore(features);

  // Global penalty (applied to final score)
  const penalty = computePenalty(features, config);

  // ---- Classification Logic ----
  let likelyIs: UserType;
  let rawScore: number;

  // Priority 1: Bot detection (most important to filter)
  if (S_bot > ct.bot) {
    likelyIs = UserType.Bot;
    // Invert: high bot score = low final score (bad for ads)
    rawScore = 1 - S_bot;
  }
  // Priority 2: Entity detection (orgs are okay but not ideal)
  else if (S_entity > ct.entity && S_bot < 0.5) {
    likelyIs = UserType.Entity;
    // Invert: high entity score = lower final score
    rawScore = 1 - S_entity;
  }
  // Priority 3: Creator detection (influencers are good targets)
  else if (S_creator > ct.creator && S_entity < 0.5 && S_bot < 0.5) {
    likelyIs = UserType.Creator;
    rawScore = S_creator;
  }
  // Priority 4: Human detection (primary target)
  else if (S_person > ct.human) {
    likelyIs = UserType.Human;
    rawScore = S_person;
  }
  // Fallback: Unclear classification
  else {
    // Find the highest scoring type
    const scores = [
      { type: UserType.Human, score: S_person, invert: false },
      { type: UserType.Creator, score: S_creator, invert: false },
      { type: UserType.Entity, score: S_entity, invert: true },
      { type: UserType.Bot, score: S_bot, invert: true },
    ];
    scores.sort((a, b) => b.score - a.score);

    const best = scores[0]!;
    // If best match is Bot or Entity, classify as Other (uncertain)
    if (best.type === UserType.Bot || best.type === UserType.Entity) {
      likelyIs = UserType.Other;
      rawScore = 0.5; // Neutral score for uncertain
    } else {
      likelyIs = best.type;
      rawScore = best.score;
    }
  }

  // Apply penalty and clamp to [0, 1]
  const finalScore = roundTo4(clamp(rawScore * penalty, 0, 1));

  return {
    score: finalScore,
    likelyIs,
  };
}

/**
 * Compute all intermediate scores for debugging/analysis.
 * Useful for understanding why a profile received a particular classification.
 */
export function computeDetailedScores(
  profile: ProfileData,
  config: HASConfig
): {
  features: DerivedFeatures;
  botScore: number;
  personScore: number;
  creatorScore: number;
  entityScore: number;
  penalty: number;
  result: HASResult;
} {
  const features = extractFeatures(profile);
  const botScore = computeBotScore(features);
  const personScore = computePersonScore(features, config);
  const creatorScore = computeCreatorScore(features);
  const entityScore = computeEntityScore(features);
  const penalty = computePenalty(features, config);
  const result = computeHASwithConfig(profile, config);

  return {
    features,
    botScore,
    personScore,
    creatorScore,
    entityScore,
    penalty,
    result,
  };
}
