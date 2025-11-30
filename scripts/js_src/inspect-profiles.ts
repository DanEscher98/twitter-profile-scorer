/**
 * Inspect user profiles in the database
 * Usage: yarn workspace @profile-scorer/scripts run run js_src/inspect-profiles.ts
 */
import { avg, count, desc, sql } from "drizzle-orm";

import { getDb, profilesToScore, userProfiles, userStats } from "@profile-scorer/db";

const db = getDb();

async function main() {
  console.log("=== Profile Scorer DB Inspection ===\n");

  // Count total profiles
  const totalResult = await db.select({ total: count() }).from(userProfiles);
  console.log(`Total profiles: ${totalResult[0]?.total ?? 0}`);

  // Count profiles pending scoring
  const pendingResult = await db.select({ pending: count() }).from(profilesToScore);
  console.log(`Profiles pending scoring: ${pendingResult[0]?.pending ?? 0}`);

  // Average human score
  const avgResult = await db.select({ avgScore: avg(userProfiles.humanScore) }).from(userProfiles);
  const avgScore = avgResult[0]?.avgScore;
  console.log(`Average human score: ${avgScore ? Number(avgScore).toFixed(3) : "N/A"}`);

  // Top 10 profiles by human score
  console.log("\n=== Top 10 Profiles by Human Score ===");
  const topProfiles = await db
    .select({
      handle: userProfiles.handle,
      name: userProfiles.name,
      humanScore: userProfiles.humanScore,
      followerCount: userProfiles.followerCount,
    })
    .from(userProfiles)
    .orderBy(desc(userProfiles.humanScore))
    .limit(10);

  for (const profile of topProfiles) {
    const score = profile.humanScore ? Number(profile.humanScore).toFixed(3) : "N/A";
    console.log(
      `@${profile.handle} (${profile.name}) - Score: ${score} | Followers: ${profile.followerCount}`
    );
  }

  // Human score distribution
  console.log("\n=== Human Score Distribution ===");
  const distribution = await db
    .select({
      bucket: sql<string>`
        CASE
          WHEN ${userProfiles.humanScore}::numeric >= 0.9 THEN '0.9-1.0'
          WHEN ${userProfiles.humanScore}::numeric >= 0.8 THEN '0.8-0.9'
          WHEN ${userProfiles.humanScore}::numeric >= 0.7 THEN '0.7-0.8'
          WHEN ${userProfiles.humanScore}::numeric >= 0.6 THEN '0.6-0.7'
          WHEN ${userProfiles.humanScore}::numeric >= 0.5 THEN '0.5-0.6'
          ELSE '<0.5'
        END
      `.as("bucket"),
      count: count(),
    })
    .from(userProfiles)
    .where(sql`${userProfiles.humanScore} IS NOT NULL`)
    .groupBy(sql`bucket`)
    .orderBy(sql`bucket`);

  for (const row of distribution) {
    console.log(`  ${row.bucket}: ${row.count}`);
  }

  // User stats summary
  console.log("\n=== User Stats Summary ===");
  const statsResult = await db
    .select({
      totalWithStats: count(),
      avgFollowers: avg(userStats.followers),
      avgFollowing: avg(userStats.following),
      avgStatuses: avg(userStats.statuses),
    })
    .from(userStats);

  const stats = statsResult[0];
  if (stats) {
    console.log(`Profiles with stats: ${stats.totalWithStats}`);
    console.log(
      `Avg followers: ${stats.avgFollowers ? Math.round(Number(stats.avgFollowers)) : "N/A"}`
    );
    console.log(
      `Avg following: ${stats.avgFollowing ? Math.round(Number(stats.avgFollowing)) : "N/A"}`
    );
    console.log(
      `Avg statuses: ${stats.avgStatuses ? Math.round(Number(stats.avgStatuses)) : "N/A"}`
    );
  }

  process.exit(0);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
