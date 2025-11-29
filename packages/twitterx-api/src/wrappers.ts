/**
 * Twitter API Wrappers
 *
 * This module provides high-level functions for fetching and processing Twitter profiles.
 * The main entry point is `processKeyword()` which orchestrates the full pipeline.
 *
 * Data Flow:
 * 1. Fetch users from RapidAPI (xapiSearch)
 * 2. Extract profiles and count new vs existing (read-only DB queries)
 * 3. Insert search metadata with accurate new_profiles count
 * 4. Insert user data: profiles → keywords (with searchId) → stats
 * 5. Queue high-HAS profiles for LLM scoring
 *
 * This order ensures:
 * - new_profiles count is accurate (counted before any inserts)
 * - FK constraint satisfied (metadata inserted before user_keywords)
 * - All records are immutable once inserted (no updates needed)
 */
import {
  TwitterProfile,
  TwitterXapiMetadata,
  TwitterXapiUser,
  getDb,
  getProfileByUsername,
  insertMetadata,
  insertToScore,
  keywordLastUsages,
  profileExists,
  upsertUserProfile,
  upsertUserStats,
  userKeywords,
} from "@profile-scorer/db";

import { computeHAS } from "./compute_has";
import { xapiGetUser, xapiSearch } from "./fetch";
import logger from "./logger";
import { normalizeString } from "./utils";

// ============================================================================
// Constants
// ============================================================================

/** HAS threshold for queuing profiles to LLM scoring */
const HAS_THRESHOLD = 0.65;

// ============================================================================
// Profile Extraction
// ============================================================================

/**
 * Extract a TwitterProfile from raw API response.
 * Computes HAS score and normalizes string fields.
 */
export function extractTwitterProfile(user: TwitterXapiUser): TwitterProfile {
  const { score, likely_is } = computeHAS(user);
  const category = user.professional?.category[0]?.name as string | null;

  return {
    twitter_id: user.rest_id,
    username: user.legacy.screen_name,
    display_name: user.legacy.name ?? null,
    bio: user.legacy.description ? normalizeString(user.legacy.description) : null,
    created_at: user.legacy.created_at,
    follower_count: user.legacy.followers_count ?? null,
    can_dm: user.legacy.can_dm ?? false,
    location: user.legacy.location ? normalizeString(user.legacy.location) : null,
    category: category ? normalizeString(category) : null,
    human_score: score,
    likely_is,
  };
}

// ============================================================================
// Single User Processing
// ============================================================================

/**
 * Process a single Twitter user: save profile, keyword relation, and stats.
 *
 * Database operations (in order):
 * 1. user_profiles - insert or update profile data
 * 2. user_keywords - insert relation with searchId (FK to xapi_usage_search)
 * 3. user_stats - upsert raw stats for future ML training
 *
 * IMPORTANT: searchId must reference an existing xapi_usage_search record.
 * Call insertMetadata() before calling this function.
 *
 * @param user - Raw user data from API
 * @param keyword - Search keyword that found this user
 * @param searchId - UUID of xapi_usage_search record (must exist for FK)
 * @returns Profile (is_new is not returned - use profileExists() before calling)
 */
export async function handleTwitterXapiUser(
  user: TwitterXapiUser,
  keyword: string,
  searchId: string | null = null
): Promise<TwitterProfile> {
  const profile = extractTwitterProfile(user);

  logger.debug("Processing user", {
    twitterId: profile.twitter_id,
    username: profile.username,
    humanScore: profile.human_score,
  });

  // Step 1 & 2: Insert/update profile + create keyword relation
  await upsertUserProfile(profile, keyword, searchId);

  // Step 3: Save raw stats
  await upsertUserStats(user);

  return profile;
}

// ============================================================================
// Batch User Processing
// ============================================================================

interface ProcessUsersResult {
  profiles: TwitterProfile[];
  failedCount: number;
}

/**
 * Process a batch of users in parallel.
 * Failures are logged but don't stop other users from being processed.
 *
 * IMPORTANT: searchId must reference an existing xapi_usage_search record.
 * Call insertMetadata() before calling this function.
 */
async function processUsers(
  users: TwitterXapiUser[],
  keyword: string,
  searchId: string
): Promise<ProcessUsersResult> {
  const results = await Promise.allSettled(
    users.map((u) => handleTwitterXapiUser(u, keyword, searchId))
  );

  const fulfilled = results.filter(
    (r): r is PromiseFulfilledResult<TwitterProfile> => r.status === "fulfilled"
  );
  const rejected = results.filter((r): r is PromiseRejectedResult => r.status === "rejected");

  if (rejected.length > 0) {
    const firstError = rejected[0]!;
    logger.warn("Some users failed processing", {
      keyword,
      failed: rejected.length,
      total: users.length,
      firstError: firstError.reason?.message || String(firstError.reason),
    });
  }

  return {
    profiles: fulfilled.map((r) => r.value),
    failedCount: rejected.length,
  };
}

// ============================================================================
// Keyword Search
// ============================================================================

interface SearchUsersResult {
  profiles: TwitterProfile[];
  newProfiles: number;
  metadata: TwitterXapiMetadata;
}

/**
 * Search for users by keyword and save to database.
 *
 * Operation order (critical for FK constraints and accurate counts):
 * 1. Check pagination state (keywordLastUsages)
 * 2. Fetch users from API (xapiSearch)
 * 3. Extract profiles and count new vs existing (read-only queries)
 * 4. Insert search metadata with accurate new_profiles count
 *    → Creates xapi_usage_search record (required for FK)
 * 5. Insert user data (handleTwitterXapiUser)
 *    → user_profiles, user_keywords (with searchId), user_stats
 *
 * This order ensures:
 * - new_profiles is accurate (counted before inserts)
 * - FK constraint satisfied (metadata exists before user_keywords)
 * - All records immutable once inserted
 *
 * @param keyword - Search term
 * @returns Processed profiles and metadata
 */
export async function searchUsers(keyword: string): Promise<SearchUsersResult> {
  logger.info("searchUsers starting", { keyword });

  // Step 1: Check pagination state
  const lastUsages = await keywordLastUsages(keyword);
  let cursor: string | null = null;
  let page = 0;

  if (lastUsages.length > 0) {
    const last = lastUsages[0]!;
    if (last.nextPage === null) {
      logger.warn("Keyword fully paginated", { keyword, lastPage: last.page });
      throw new Error(`Keyword "${keyword}" fully paginated. No more pages.`);
    }
    cursor = last.nextPage;
    page = last.page + 1;
    logger.info("Resuming pagination", { keyword, page });
  }

  // Step 2: Fetch users from API
  const { users, metadata } = await xapiSearch(keyword, 20, cursor, page);
  logger.info("Fetched users from API", { keyword, count: users.length });

  // Step 3: Extract profiles and count new ones (read-only)
  const extractedProfiles = users.map(extractTwitterProfile);
  const existenceChecks = await Promise.all(
    extractedProfiles.map((p) => profileExists(p.twitter_id))
  );
  const newCount = existenceChecks.filter((exists) => !exists).length;
  logger.debug("Counted new profiles", { keyword, total: users.length, newCount });

  // Step 4: Insert metadata FIRST (required for user_keywords FK)
  metadata.new_profiles = newCount;
  await insertMetadata(metadata);
  logger.debug("Inserted metadata", { id: metadata.id, newProfiles: newCount });

  // Step 5: Process all users (inserts with searchId)
  const { profiles, failedCount } = await processUsers(users, keyword, metadata.id);

  logger.info("searchUsers completed", {
    keyword,
    totalProfiles: profiles.length,
    newProfiles: newCount,
    failed: failedCount,
    page: metadata.page,
  });

  return { profiles, newProfiles: newCount, metadata };
}

// ============================================================================
// Main Entry Point
// ============================================================================

interface ProcessKeywordResult {
  newProfiles: number;
  humanProfiles: number;
}

/**
 * Main entry point: fetch profiles by keyword, filter by HAS, queue for scoring.
 *
 * This is the function called by the query-twitter-api Lambda.
 *
 * @param keyword - Search term to query
 * @returns Count of new profiles and profiles queued for LLM scoring
 */
export async function processKeyword(keyword: string): Promise<ProcessKeywordResult> {
  logger.info("processKeyword starting", { keyword });

  // Fetch and save profiles
  const { profiles, newProfiles } = await searchUsers(keyword);

  // Filter high-HAS profiles for LLM scoring
  const humanProfiles = profiles.filter((p) => p.human_score > HAS_THRESHOLD);

  logger.info("Filtering for LLM scoring", {
    keyword,
    total: profiles.length,
    aboveThreshold: humanProfiles.length,
    threshold: HAS_THRESHOLD,
  });

  // Queue for LLM scoring
  await Promise.all(humanProfiles.map((p) => insertToScore(p.twitter_id, p.username)));

  logger.info("processKeyword completed", {
    keyword,
    newProfiles,
    humanProfiles: humanProfiles.length,
  });

  return { newProfiles, humanProfiles: humanProfiles.length };
}

// ============================================================================
// Single User Retrieval
// ============================================================================

interface GetUserOptions {
  /** Force API fetch even if user exists in DB (default: false) */
  update?: boolean;
  /** Keyword to associate with user if saved to DB (default: "@manual") */
  keyword?: string;
}

interface GetUserResult {
  profile: TwitterProfile;
  /** Whether the profile was fetched from API (true) or DB cache (false) */
  fromApi: boolean;
}

/**
 * Get a user profile by username.
 *
 * By default, checks the database first and returns cached profile if found.
 * Use `update: true` to force a fresh fetch from the API (recomputes HAS).
 *
 * @param username - Twitter handle (without @)
 * @param options - Optional settings
 * @param options.update - Force API fetch even if cached (default: false)
 * @param options.keyword - Keyword to associate if saved to DB (default: "@manual")
 * @returns Profile and source indicator
 * @throws TwitterXApiError for API errors (USER_NOT_FOUND, RATE_LIMITED, etc.)
 *
 * @example
 * // Get from cache or API
 * const { profile, fromApi } = await getUser("elonmusk");
 *
 * // Force fresh fetch (recomputes HAS)
 * const { profile } = await getUser("elonmusk", { update: true });
 *
 * // With custom keyword
 * const { profile } = await getUser("elonmusk", { keyword: "@seed_profile" });
 */
export async function getUser(
  username: string,
  options: GetUserOptions = {}
): Promise<GetUserResult> {
  const { update = false, keyword = "@manual" } = options;

  logger.info("getUser starting", { username, update, keyword });

  // Check DB cache first (unless update is forced)
  if (!update) {
    const cached = await getProfileByUsername(username);
    if (cached) {
      // Create keyword association even for cached profiles
      const db = getDb();
      try {
        await db
          .insert(userKeywords)
          .values({ twitterId: cached.twitter_id, keyword, searchId: null })
          .onConflictDoNothing();
        logger.debug("Created keyword association for cached profile", {
          username,
          keyword,
        });
      } catch (e: any) {
        logger.warn("Failed to create keyword association for cached profile", {
          username,
          keyword,
          error: e.message,
        });
      }

      logger.info("getUser returning cached profile", {
        username,
        twitterId: cached.twitter_id,
        humanScore: cached.human_score,
      });
      return { profile: cached, fromApi: false };
    }
    logger.debug("Profile not in cache, fetching from API", { username });
  }

  // Fetch from API, compute HAS, and save to DB
  const rawUser = await xapiGetUser(username);
  const profile = await handleTwitterXapiUser(rawUser, keyword);

  logger.info("getUser completed from API", {
    username,
    twitterId: profile.twitter_id,
    humanScore: profile.human_score,
    likelyIs: profile.likely_is,
    keyword,
  });

  return { profile, fromApi: true };
}
