import { getDb } from "./client";
import { userProfiles, userKeywords, userStats, profilesToScore, xapiSearchUsage } from "./schema";
import { TwitterProfile, TwitterXapiUser, TwitterXapiMetadata } from "./models"
import { sql, eq, desc } from "drizzle-orm";

const db = getDb()

// Simple logger for helpers (no external deps)
const log = {
  debug: (msg: string, meta?: object) => console.log(JSON.stringify({ level: "debug", service: "db-helpers", message: msg, ...meta })),
  info: (msg: string, meta?: object) => console.log(JSON.stringify({ level: "info", service: "db-helpers", message: msg, ...meta })),
  warn: (msg: string, meta?: object) => console.warn(JSON.stringify({ level: "warn", service: "db-helpers", message: msg, ...meta })),
  error: (msg: string, meta?: object) => console.error(JSON.stringify({ level: "error", service: "db-helpers", message: msg, ...meta })),
};

export async function insertToScore(twitterId: string, username: string): Promise<number> {
  try {
    await db.insert(profilesToScore).values({ twitterId, username });
    return 1;
  } catch (e: any) {
    if (e.code === '23505') return 0; // already exists
    throw e;
  }
}

export async function upsertUserProfile(
  profile: TwitterProfile,
  keyword: string,
  searchId?: string
): Promise<number> {
  let isNew = 0;

  try {
    await db.insert(userProfiles).values({
      twitterId: profile.twitter_id,
      username: profile.username,
      displayName: profile.display_name ?? '',
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
    log.debug("Inserted new user profile", { twitterId: profile.twitter_id, username: profile.username });
  } catch (e: any) {
    if (e.code === '23505') {
      // unique violation - update existing profile
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
      log.debug("Updated existing user profile", { twitterId: profile.twitter_id, username: profile.username, keyword });
    } else {
      log.error("Failed to upsert user profile", { twitterId: profile.twitter_id, error: e.message, code: e.code });
      throw e;
    }
  }

  // Always insert user_keywords (for both new and existing profiles)
  try {
    await db
      .insert(userKeywords)
      .values({ twitterId: profile.twitter_id, keyword, searchId })
      .onConflictDoNothing();
    log.debug("Inserted user keyword relation", { twitterId: profile.twitter_id, keyword, searchId });
  } catch (e: any) {
    log.error("Failed to insert user_keywords", { twitterId: profile.twitter_id, keyword, searchId, error: e.message, code: e.code });
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
    log.error("Failed to upsert user stats", { twitterId: user.rest_id, error: e.message, code: e.code, cause: e.cause?.message });
    throw e;
  }
}

export async function keywordLastUsages(keyword: string) {
  return await db
    .select()
    .from(xapiSearchUsage)
    .where(eq(xapiSearchUsage.keyword, keyword))
    .orderBy(desc(xapiSearchUsage.page))
}

export async function insertMetadata(metadata: TwitterXapiMetadata) {
  await db
    .insert(xapiSearchUsage)
    .values({
      id: metadata.id, // Use the pre-generated UUID so FK references work
      idsHash: metadata.ids_hash!,
      keyword: metadata.keyword,
      items: metadata.items,
      retries: metadata.retries,
      nextPage: metadata.next_page,
      page: metadata.page,
      newProfiles: metadata.new_profiles!,
    });
}
