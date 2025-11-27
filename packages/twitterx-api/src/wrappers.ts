import { TwitterXapiUser, TwitterXapiMetadata, TwitterProfile, upsertUserStats, upsertUserProfile, keywordLastUsages, insertToScore, insertMetadata } from "@profile-scorer/db";
import { computeHAS } from "./compute_has"
import { normalizeString } from "./utils"
import { xapiSearch } from "./fetch";
import logger from "./logger";

export function extractTwitterProfile(user: TwitterXapiUser): TwitterProfile {
  const { score, likely_is } = computeHAS(user);

  const category = user.professional?.category[0]?.name as (string | null)

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

export async function handleTwitterXapiUser(
  user: TwitterXapiUser,
  keyword: string = "@manual",
  searchId?: string):
  Promise<{ profile: TwitterProfile, is_new: number }> {
  const profile = extractTwitterProfile(user);

  logger.debug("Processing user - start", {
    twitterId: profile.twitter_id,
    username: profile.username,
    humanScore: profile.human_score,
    likelyIs: profile.likely_is,
    keyword,
    searchId
  });

  try {
    const is_new = await upsertUserProfile(profile, keyword, searchId);
    logger.debug("Processing user - profile upserted", { twitterId: profile.twitter_id, isNew: is_new });

    await upsertUserStats(user);
    logger.debug("Processing user - stats upserted", { twitterId: profile.twitter_id });

    logger.debug("Processing user - complete", { twitterId: profile.twitter_id, username: profile.username, isNew: is_new });
    return { profile, is_new };
  } catch (e: any) {
    logger.error("Processing user - FAILED", {
      twitterId: profile.twitter_id,
      username: profile.username,
      error: e.message,
      cause: e.cause?.message,
      code: e.code
    });
    throw e;
  }
}

export async function searchUsers(keyword: string): Promise<{
  profiles: TwitterProfile[];
  metadata: TwitterXapiMetadata;
}> {
  const items = 20;

  logger.info("searchUsers starting", { keyword });

  // Check if keyword has been used
  const lastUsages = await keywordLastUsages(keyword)
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
    logger.info("Resuming from previous pagination", { keyword, page, cursor: cursor.substring(0, 20) + "..." });
  }

  const { users, metadata } = await xapiSearch(keyword, items, cursor, page);

  logger.info("Processing fetched users", { keyword, userCount: users.length });

  // Insert metadata FIRST so foreign key constraint is satisfied
  // (user_keywords.search_id references xapi_usage_search.id)
  // We'll update new_profiles count later
  metadata.new_profiles = 0;
  await insertMetadata(metadata);

  const settledResults = await Promise.allSettled(
    users.map((u) => handleTwitterXapiUser(u, keyword, metadata.id))
  );

  const resultProfiles = settledResults.filter(result => result.status === 'fulfilled');
  const failedResults = settledResults.filter(result => result.status === 'rejected') as PromiseRejectedResult[];

  if (failedResults.length > 0) {
    // Log first failure reason for debugging
    const firstError = failedResults[0]!;
    logger.warn("Some users failed processing", {
      keyword,
      failed: failedResults.length,
      total: users.length,
      firstError: firstError.reason?.message || String(firstError.reason),
      firstErrorCause: firstError.reason?.cause?.message
    });
  }

  const newProfiles = resultProfiles.map(result => result.value.is_new).reduce((a, b) => a + b, 0);
  const profiles = resultProfiles.map(result => result.value.profile)

  metadata.new_profiles = newProfiles;

  logger.info("searchUsers completed", {
    keyword,
    totalProfiles: profiles.length,
    newProfiles,
    page: metadata.page
  });

  return { profiles, metadata }
}

interface SearchResult {
  newProfiles: number;
  humanProfiles: number;
}

export async function processKeyword(keyword: string): Promise<SearchResult> {
  logger.info("processKeyword starting", { keyword });

  const { profiles, metadata } = await searchUsers(keyword);

  // Filter human profiles and insert to scoring queue
  const humanProfiles = profiles.filter((p) => p.human_score > 0.65);

  logger.info("Filtering human profiles", {
    keyword,
    total: profiles.length,
    humanCount: humanProfiles.length,
    threshold: 0.65
  });

  await Promise.all(
    humanProfiles.map((p) => insertToScore(p.twitter_id, p.username))
  );

  // Note: metadata was already inserted in searchUsers() to satisfy FK constraint
  // The new_profiles count is set there

  logger.info("processKeyword completed", {
    keyword,
    newProfiles: metadata.new_profiles,
    humanProfiles: humanProfiles.length
  });

  return {
    newProfiles: metadata.new_profiles!,
    humanProfiles: humanProfiles.length,
  };
}
