import { and, asc, avg, count, desc, eq, gt, isNull, max, min, sql } from "drizzle-orm";

import { createLogger } from "@profile-scorer/utils";

import { getDb } from "./client";
import { TwitterProfile, TwitterUserType, TwitterXapiMetadata, TwitterXapiUser } from "./models";
import {
  keywordStats,
  profileScores,
  profilesToScore,
  userKeywords,
  userProfiles,
  userStats,
  xapiSearchUsage,
} from "./schema";

const db = getDb();
const log = createLogger("db-helpers");

export async function insertToScore(twitterId: string, handle: string): Promise<number> {
  try {
    await db.insert(profilesToScore).values({ twitterId, handle });
    return 1;
  } catch (e: any) {
    if (e.code === "23505") return 0; // already exists
    throw e;
  }
}

/**
 * Check if a profile already exists in the database.
 * Used to count new_profiles before inserting metadata.
 */
export async function profileExists(twitterId: string): Promise<boolean> {
  const result = await db
    .select({ twitterId: userProfiles.twitterId })
    .from(userProfiles)
    .where(eq(userProfiles.twitterId, twitterId))
    .limit(1);
  return result.length > 0;
}

/**
 * Get a profile by handle from the database.
 *
 * @param handle - Twitter handle (case-insensitive)
 * @returns Profile if found, null otherwise
 */
export async function getProfileByHandle(handle: string): Promise<TwitterProfile | null> {
  const result = await db
    .select({
      twitter_id: userProfiles.twitterId,
      handle: userProfiles.handle,
      name: userProfiles.name,
      bio: userProfiles.bio,
      created_at: userProfiles.createdAt,
      follower_count: userProfiles.followerCount,
      can_dm: userProfiles.canDm,
      location: userProfiles.location,
      category: userProfiles.category,
      human_score: userProfiles.humanScore,
      likely_is: userProfiles.likelyIs,
    })
    .from(userProfiles)
    .where(sql`lower(${userProfiles.handle}) = lower(${handle})`)
    .limit(1);

  if (result.length === 0) return null;

  const row = result[0]!;
  return {
    twitter_id: row.twitter_id,
    handle: row.handle,
    name: row.name,
    bio: row.bio,
    created_at: row.created_at ?? "",
    follower_count: row.follower_count,
    can_dm: row.can_dm ?? false,
    location: row.location,
    category: row.category,
    human_score: parseFloat(row.human_score ?? "0"),
    likely_is: (row.likely_is as TwitterUserType) ?? TwitterUserType.Other,
  };
}

/**
 * Upsert a user profile and create keyword association.
 *
 * Database operations:
 * 1. user_profiles - insert new or update existing (appends keyword to got_by_keywords)
 * 2. user_keywords - insert relation with searchId (FK to xapi_usage_search)
 *
 * IMPORTANT: searchId must reference an existing xapi_usage_search record.
 * Call insertMetadata() before calling this function.
 *
 * @returns 1 if new profile inserted, 0 if existing profile updated
 */
export async function upsertUserProfile(
  profile: TwitterProfile,
  keyword: string,
  searchId: string | null = null
): Promise<number> {
  let isNew = 0;

  // Step 1: Insert or update user_profiles
  try {
    await db.insert(userProfiles).values({
      twitterId: profile.twitter_id,
      handle: profile.handle,
      name: profile.name ?? "",
      bio: profile.bio,
      createdAt: profile.created_at,
      followerCount: profile.follower_count,
      location: profile.location,
      canDm: profile.can_dm,
      category: profile.category,
      humanScore: profile.human_score.toString(),
      likelyIs: profile.likely_is,
      gotByKeywords: [keyword],
    });
    isNew = 1;
    log.debug("Inserted new user profile", {
      twitterId: profile.twitter_id,
      handle: profile.handle,
    });
  } catch (e: any) {
    if (e.code === "23505") {
      // Unique violation - update existing profile
      await db
        .update(userProfiles)
        .set({
          updatedAt: sql`now()`,
          gotByKeywords: sql`
            CASE
              WHEN ${keyword} = ANY(got_by_keywords) THEN got_by_keywords
              ELSE array_append(got_by_keywords, ${keyword})
            END
          `,
        })
        .where(eq(userProfiles.twitterId, profile.twitter_id));
      log.debug("Updated existing user profile", { twitterId: profile.twitter_id, keyword });
    } else {
      log.error("Failed to upsert user profile", {
        twitterId: profile.twitter_id,
        error: e.message,
        code: e.code,
      });
      throw e;
    }
  }

  // Step 2: Insert user_keywords (searchId is optional - null for manual fetches)
  try {
    await db
      .insert(userKeywords)
      .values({ twitterId: profile.twitter_id, keyword, searchId })
      .onConflictDoNothing();
    log.debug("Inserted user keyword relation", {
      twitterId: profile.twitter_id,
      keyword,
      searchId,
    });
  } catch (e: any) {
    log.error("Failed to insert user_keywords", {
      twitterId: profile.twitter_id,
      keyword,
      searchId,
      error: e.message,
      code: e.code,
    });
    throw e;
  }

  return isNew;
}

export async function upsertUserStats(user: TwitterXapiUser): Promise<void> {
  const values = {
    twitterId: user.rest_id,
    followers: user.legacy.followers_count,
    following: user.legacy.friends_count,
    statuses: user.legacy.statuses_count,
    favorites: user.legacy.favourites_count,
    listed: user.legacy.listed_count,
    media: user.legacy.media_count,
    verified: user.legacy.verified,
    blueVerified: user.is_blue_verified,
    defaultProfile: user.legacy.default_profile,
    defaultImage: user.legacy.default_profile_image,
    sensitive: user.legacy.possibly_sensitive,
    canDm: user.legacy.can_dm,
  };

  try {
    await db
      .insert(userStats)
      .values(values)
      .onConflictDoUpdate({
        target: userStats.twitterId,
        set: {
          ...values,
          updatedAt: sql`now()`,
        },
      });
    log.debug("Upserted user stats", { twitterId: user.rest_id, followers: values.followers });
  } catch (e: any) {
    log.error("Failed to upsert user stats", {
      twitterId: user.rest_id,
      error: e.message,
      code: e.code,
      cause: e.cause?.message,
    });
    throw e;
  }
}

export async function keywordLastUsages(keyword: string) {
  return await db
    .select()
    .from(xapiSearchUsage)
    .where(eq(xapiSearchUsage.keyword, keyword))
    .orderBy(desc(xapiSearchUsage.page));
}

/**
 * Get the latest search entry for a keyword.
 * Returns the most recent page's data including next_page cursor.
 */
export async function getKeywordLatestPage(keyword: string) {
  const result = await db
    .select({
      page: xapiSearchUsage.page,
      nextPage: xapiSearchUsage.nextPage,
      queryAt: xapiSearchUsage.queryAt,
    })
    .from(xapiSearchUsage)
    .where(eq(xapiSearchUsage.keyword, keyword))
    .orderBy(desc(xapiSearchUsage.page))
    .limit(1);

  return result[0] ?? null;
}

/**
 * Insert search metadata record.
 *
 * IMPORTANT: Must be called BEFORE upsertUserProfile() to satisfy FK constraint.
 * user_keywords.search_id references xapi_usage_search.id.
 *
 * @param metadata - Search metadata with new_profiles already calculated
 */
export async function insertMetadata(metadata: TwitterXapiMetadata) {
  await db.insert(xapiSearchUsage).values({
    id: metadata.id,
    idsHash: metadata.ids_hash!,
    keyword: metadata.keyword,
    items: metadata.items,
    retries: metadata.retries,
    nextPage: metadata.next_page,
    page: metadata.page,
    newProfiles: metadata.new_profiles!,
  });
  log.debug("Inserted search metadata", {
    id: metadata.id,
    keyword: metadata.keyword,
    page: metadata.page,
    newProfiles: metadata.new_profiles,
  });
}

/**
 * Profile data returned by getProfilesToScore.
 * Used as input for LLM labeling.
 */
export interface ProfileToScore {
  twitterId: string;
  handle: string;
  name: string;
  bio: string; // never null - query filters out null bios
  category: string | null;
  followers: number;
}

/**
 * Retrieves profiles that haven't been labeled by the specified model.
 * Uses LEFT JOIN to filter out already-labeled profiles.
 * Filters out profiles with null/empty bio or name (cannot be meaningfully labeled).
 * Returns profiles in random order for diversity in labeling batches.
 *
 * @param model - The LLM model name to check against `profile_scores.scored_by`
 * @param limit - Maximum number of profiles to return (default 25)
 * @param threshold - Minimum human_score to consider (default 0.55)
 * @returns Array of profiles ready for labeling
 */
export async function getProfilesToScore(
  model: string,
  limit: number = 25,
  threshold: number = 0.55
): Promise<ProfileToScore[]> {
  const rows = await db
    .select({
      twitterId: userProfiles.twitterId,
      handle: userProfiles.handle,
      name: userProfiles.name,
      bio: userProfiles.bio,
      category: userProfiles.category,
      followers: userStats.followers,
    })
    .from(userProfiles)
    .innerJoin(profilesToScore, eq(profilesToScore.twitterId, userProfiles.twitterId))
    .leftJoin(userStats, eq(userStats.twitterId, userProfiles.twitterId))
    .leftJoin(
      profileScores,
      and(eq(profileScores.twitterId, userProfiles.twitterId), eq(profileScores.scoredBy, model))
    )
    .where(
      and(
        isNull(profileScores.id),
        gt(userProfiles.humanScore, threshold.toString()),
        sql`${userProfiles.bio} IS NOT NULL AND ${userProfiles.bio} != ''`,
        sql`${userProfiles.name} IS NOT NULL AND ${userProfiles.name} != ''`
      )
    )
    .orderBy(sql`RANDOM()`)
    .limit(limit);

  return rows.map((row) => ({
    twitterId: row.twitterId,
    handle: row.handle,
    name: row.name!, // Safe: filtered in WHERE clause
    bio: row.bio!, // Safe: filtered in WHERE clause
    category: row.category,
    followers: row.followers ?? 0,
  }));
}

/**
 * Inserts a label for a profile.
 *
 * @param twitterId - Twitter ID of the profile
 * @param label - Trivalent label: true=good, false=bad, null=uncertain
 * @param reason - Explanation for the label
 * @param scoredBy - Model name that generated the label
 * @param audience - Audience config name used for scoring (e.g., "thelai_customers.v1")
 */
export async function insertProfileLabel(
  twitterId: string,
  label: boolean | null,
  reason: string,
  scoredBy: string,
  audience?: string
): Promise<void> {
  await db.insert(profileScores).values({
    twitterId,
    label,
    reason,
    scoredBy,
    audience,
  });
  log.debug("Inserted profile label", { twitterId, label, scoredBy, audience });
}

// ============================================================================
// Keyword Stats Helpers
// ============================================================================

export interface KeywordStatsData {
  keyword: string;
  semanticTags: string[];
  profilesFound: number;
  avgHumanScore: number;
  labelRate: number; // Percentage of true labels (0.0-1.0)
  stillValid: boolean;
  pagesSearched: number;
  highQualityCount: number;
  lowQualityCount: number;
  firstSearchAt: string | null;
  lastSearchAt: string | null;
}

/**
 * Calculate stats for a single keyword by aggregating data from related tables.
 */
export async function calculateKeywordStats(keyword: string): Promise<KeywordStatsData> {
  // Get profile counts and HAS score averages from user_keywords + user_profiles
  const profileStats = await db
    .select({
      profilesFound: count(userKeywords.twitterId),
      avgHumanScore: avg(userProfiles.humanScore),
      highQualityCount: sql<number>`count(*) filter (where ${userProfiles.humanScore}::numeric > 0.7)`,
      lowQualityCount: sql<number>`count(*) filter (where ${userProfiles.humanScore}::numeric < 0.4)`,
    })
    .from(userKeywords)
    .innerJoin(userProfiles, eq(userKeywords.twitterId, userProfiles.twitterId))
    .where(eq(userKeywords.keyword, keyword));

  // Get label rate for profiles found with this keyword
  // labelRate = count(true labels) / count(all non-null labels)
  const llmStats = await db
    .select({
      totalLabeled: sql<number>`count(*) filter (where ${profileScores.label} is not null)`,
      trueLabels: sql<number>`count(*) filter (where ${profileScores.label} = true)`,
    })
    .from(userKeywords)
    .innerJoin(profileScores, eq(userKeywords.twitterId, profileScores.twitterId))
    .where(eq(userKeywords.keyword, keyword));

  // Get pagination info from xapi_usage_search
  const searchStats = await db
    .select({
      pagesSearched: max(xapiSearchUsage.page),
      firstSearchAt: min(xapiSearchUsage.queryAt),
      lastSearchAt: max(xapiSearchUsage.queryAt),
    })
    .from(xapiSearchUsage)
    .where(eq(xapiSearchUsage.keyword, keyword));

  // Check if keyword still has pages (latest page has next_page)
  const latestPage = await getKeywordLatestPage(keyword);
  const stillValid = !latestPage || latestPage.nextPage !== null;

  // Get existing semantic tags (if any)
  const existingKeyword = await db
    .select({ semanticTags: keywordStats.semanticTags })
    .from(keywordStats)
    .where(eq(keywordStats.keyword, keyword))
    .limit(1);

  const stats = profileStats[0];
  const llm = llmStats[0];
  const search = searchStats[0];

  // Calculate label rate (percentage of true labels among all non-null labels)
  const totalLabeled = Number(llm?.totalLabeled) || 0;
  const trueLabels = Number(llm?.trueLabels) || 0;
  const labelRate = totalLabeled > 0 ? trueLabels / totalLabeled : 0;

  return {
    keyword,
    semanticTags: existingKeyword[0]?.semanticTags ?? [],
    profilesFound: Number(stats?.profilesFound) || 0,
    avgHumanScore: parseFloat(stats?.avgHumanScore ?? "0") || 0,
    labelRate,
    stillValid,
    pagesSearched: Number(search?.pagesSearched) || 0,
    highQualityCount: Number(stats?.highQualityCount) || 0,
    lowQualityCount: Number(stats?.lowQualityCount) || 0,
    firstSearchAt: search?.firstSearchAt ?? null,
    lastSearchAt: search?.lastSearchAt ?? null,
  };
}

/**
 * Upsert keyword stats record.
 */
export async function upsertKeywordStats(stats: KeywordStatsData): Promise<void> {
  await db
    .insert(keywordStats)
    .values({
      keyword: stats.keyword,
      profilesFound: stats.profilesFound,
      avgHumanScore: stats.avgHumanScore.toFixed(3),
      labelRate: stats.labelRate.toFixed(3),
      stillValid: stats.stillValid,
      pagesSearched: stats.pagesSearched,
      highQualityCount: stats.highQualityCount,
      lowQualityCount: stats.lowQualityCount,
      firstSearchAt: stats.firstSearchAt,
      lastSearchAt: stats.lastSearchAt,
    })
    .onConflictDoUpdate({
      target: keywordStats.keyword,
      set: {
        profilesFound: stats.profilesFound,
        avgHumanScore: stats.avgHumanScore.toFixed(3),
        labelRate: stats.labelRate.toFixed(3),
        stillValid: stats.stillValid,
        pagesSearched: stats.pagesSearched,
        highQualityCount: stats.highQualityCount,
        lowQualityCount: stats.lowQualityCount,
        firstSearchAt: stats.firstSearchAt,
        lastSearchAt: stats.lastSearchAt,
        updatedAt: sql`now()`,
      },
    });
}

/**
 * Get all distinct keywords from xapi_usage_search.
 */
export async function getAllSearchedKeywords(): Promise<string[]> {
  const result = await db
    .selectDistinct({ keyword: xapiSearchUsage.keyword })
    .from(xapiSearchUsage)
    .orderBy(asc(xapiSearchUsage.keyword));

  return result.map((r) => r.keyword);
}

/**
 * Get valid keywords for selection (still_valid = true).
 */
export async function getValidKeywords(): Promise<KeywordStatsData[]> {
  const result = await db
    .select()
    .from(keywordStats)
    .where(eq(keywordStats.stillValid, true))
    .orderBy(desc(keywordStats.avgHumanScore));

  return result.map((r) => ({
    keyword: r.keyword,
    semanticTags: r.semanticTags ?? [],
    profilesFound: r.profilesFound,
    avgHumanScore: parseFloat(r.avgHumanScore ?? "0"),
    labelRate: parseFloat(r.labelRate ?? "0"),
    stillValid: r.stillValid,
    pagesSearched: r.pagesSearched,
    highQualityCount: r.highQualityCount,
    lowQualityCount: r.lowQualityCount,
    firstSearchAt: r.firstSearchAt,
    lastSearchAt: r.lastSearchAt,
  }));
}

/**
 * Insert a new keyword into keyword_stats (for adding new keywords to the pool).
 */
export async function insertKeyword(keyword: string, semanticTags: string[] = []): Promise<void> {
  await db
    .insert(keywordStats)
    .values({
      keyword,
      semanticTags,
      stillValid: true,
    })
    .onConflictDoUpdate({
      target: keywordStats.keyword,
      set: {
        semanticTags,
        updatedAt: sql`now()`,
      },
    });
  log.info("Inserted/updated keyword", { keyword, semanticTags });
}

/**
 * Get profiles by keyword that haven't been labeled by a specific model.
 * Used for bulk labeling profiles found via a particular search keyword.
 * Filters out profiles with null bios.
 *
 * @param keyword - The search keyword to filter by
 * @param model - The LLM model name to check against (filters out already-labeled)
 * @param limit - Maximum number of profiles to return
 * @returns Array of profiles ready for labeling
 */
export async function getProfilesByKeyword(
  keyword: string,
  model: string,
  limit: number = 100
): Promise<ProfileToScore[]> {
  const rows = await db
    .select({
      twitterId: userProfiles.twitterId,
      handle: userProfiles.handle,
      name: userProfiles.name,
      bio: userProfiles.bio,
      category: userProfiles.category,
      followers: userStats.followers,
    })
    .from(userKeywords)
    .innerJoin(userProfiles, eq(userKeywords.twitterId, userProfiles.twitterId))
    .leftJoin(userStats, eq(userStats.twitterId, userProfiles.twitterId))
    .leftJoin(
      profileScores,
      and(eq(profileScores.twitterId, userProfiles.twitterId), eq(profileScores.scoredBy, model))
    )
    .where(
      and(
        eq(userKeywords.keyword, keyword),
        isNull(profileScores.id),
        sql`${userProfiles.bio} IS NOT NULL AND ${userProfiles.bio} != ''`
      )
    )
    .limit(limit);

  return rows.map((row) => ({
    twitterId: row.twitterId,
    handle: row.handle,
    name: row.name ?? "",
    bio: row.bio!,
    category: row.category,
    followers: row.followers ?? 0,
  }));
}

/**
 * Count profiles by keyword that haven't been scored by a specific model.
 *
 * @param keyword - The search keyword to filter by
 * @param model - The LLM model name to check against
 * @returns Count of unscored profiles
 */
export async function countUnscoredByKeyword(keyword: string, model: string): Promise<number> {
  const result = await db
    .select({ count: count() })
    .from(userKeywords)
    .innerJoin(userProfiles, eq(userKeywords.twitterId, userProfiles.twitterId))
    .leftJoin(
      profileScores,
      and(eq(profileScores.twitterId, userProfiles.twitterId), eq(profileScores.scoredBy, model))
    )
    .where(and(eq(userKeywords.keyword, keyword), isNull(profileScores.id)));

  return Number(result[0]?.count ?? 0);
}

/**
 * Get ALL profiles by keyword (regardless of labeling status).
 * Used for bulk labeling all profiles found via a particular search keyword.
 * Filters out profiles with null bios.
 *
 * @param keyword - The search keyword to filter by
 * @param limit - Maximum number of profiles to return
 * @param offset - Number of profiles to skip (for pagination)
 * @returns Array of all profiles for this keyword
 */
export async function getAllProfilesByKeyword(
  keyword: string,
  limit: number = 100,
  offset: number = 0
): Promise<ProfileToScore[]> {
  const rows = await db
    .select({
      twitterId: userProfiles.twitterId,
      handle: userProfiles.handle,
      name: userProfiles.name,
      bio: userProfiles.bio,
      category: userProfiles.category,
      followers: userStats.followers,
    })
    .from(userKeywords)
    .innerJoin(userProfiles, eq(userKeywords.twitterId, userProfiles.twitterId))
    .leftJoin(userStats, eq(userStats.twitterId, userProfiles.twitterId))
    .where(
      and(
        eq(userKeywords.keyword, keyword),
        sql`${userProfiles.bio} IS NOT NULL AND ${userProfiles.bio} != ''`
      )
    )
    .limit(limit)
    .offset(offset);

  return rows.map((row) => ({
    twitterId: row.twitterId,
    handle: row.handle,
    name: row.name ?? "",
    bio: row.bio!,
    category: row.category,
    followers: row.followers ?? 0,
  }));
}

/**
 * Count ALL profiles by keyword (regardless of scoring status).
 *
 * @param keyword - The search keyword to filter by
 * @returns Total count of profiles for this keyword
 */
export async function countAllByKeyword(keyword: string): Promise<number> {
  const result = await db
    .select({ count: count() })
    .from(userKeywords)
    .where(eq(userKeywords.keyword, keyword));

  return Number(result[0]?.count ?? 0);
}

/**
 * Upsert a profile label (insert or update if exists).
 * Uses ON CONFLICT to update existing labels for the same twitter_id + model.
 *
 * @param twitterId - Twitter ID of the profile
 * @param label - Trivalent label: true=good, false=bad, null=uncertain
 * @param reason - Explanation for the label
 * @param scoredBy - Model name that generated the label
 * @param audience - Audience config name used for scoring (e.g., "thelai_customers.v1")
 * @returns 'inserted' if new, 'updated' if existing
 */
export async function upsertProfileLabel(
  twitterId: string,
  label: boolean | null,
  reason: string,
  scoredBy: string,
  audience?: string
): Promise<"inserted" | "updated"> {
  await db
    .insert(profileScores)
    .values({
      twitterId,
      label,
      reason,
      scoredBy,
      audience,
    })
    .onConflictDoUpdate({
      target: [profileScores.twitterId, profileScores.scoredBy],
      set: {
        label,
        reason,
        audience,
        scoredAt: sql`now()`,
      },
    });

  // Drizzle doesn't distinguish insert vs update, so we return 'inserted' for simplicity
  log.debug("Upserted profile label", { twitterId, label, scoredBy, audience });
  return "inserted";
}
