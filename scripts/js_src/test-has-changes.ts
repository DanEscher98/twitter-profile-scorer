/**
 * Test HAS heuristic changes by re-scoring profiles from the database.
 * Compares stored (old) scores with newly computed (new) scores.
 *
 * Usage: yarn workspace @profile-scorer/scripts run run js_src/test-has-changes.ts
 */
import { eq, isNotNull } from "drizzle-orm";

import { getDb, userProfiles, userStats } from "@profile-scorer/db";
import {
  HASConfig,
  ProfileData,
  computeHASwithConfig,
  defaultConfig,
} from "@profile-scorer/has-scorer";

const db = getDb();

interface ProfileRow {
  twitterId: string;
  username: string;
  createdAt: string;
  humanScore: string | null;
  likelyIs: string | null;
  // Stats
  followers: number | null;
  following: number | null;
  statuses: number | null;
  favorites: number | null;
  listed: number | null;
  media: number | null;
  blueVerified: boolean | null;
  defaultProfile: boolean | null;
  defaultImage: boolean | null;
  sensitive: boolean | null;
}

type DbRow = Omit<ProfileRow, "media">;

/**
 * Build ProfileData from database row.
 */
function buildProfileData(row: DbRow): ProfileData {
  return {
    followers: row.followers ?? 0,
    following: row.following ?? 0,
    statuses: row.statuses ?? 0,
    favorites: row.favorites ?? 0,
    listed: row.listed ?? 0,
    media: 0, // Not stored in user_stats
    isBlueVerified: row.blueVerified ?? false,
    defaultProfile: row.defaultProfile ?? true,
    defaultProfileImage: row.defaultImage ?? true,
    possiblySensitive: row.sensitive ?? false,
    createdAt: row.createdAt,
  };
}

async function main() {
  // Parse command line args for optional config file
  const args = process.argv.slice(2);
  let config: HASConfig = defaultConfig;

  if (args.length > 0 && args[0]) {
    const fs = await import("fs");
    const configPath = args[0];
    console.log(`Loading config from: ${configPath}`);
    const configJson = fs.readFileSync(configPath, "utf-8");
    config = JSON.parse(configJson) as HASConfig;
  }

  console.log("=== HAS Heuristic Change Test ===\n");
  console.log("Config weights:", JSON.stringify(config.personWeights, null, 2));

  // Fetch profiles with stats
  const rows = await db
    .select({
      twitterId: userProfiles.twitterId,
      username: userProfiles.username,
      createdAt: userProfiles.createdAt,
      humanScore: userProfiles.humanScore,
      likelyIs: userProfiles.likelyIs,
      followers: userStats.followers,
      following: userStats.following,
      statuses: userStats.statuses,
      favorites: userStats.favorites,
      listed: userStats.listed,
      blueVerified: userStats.blueVerified,
      defaultProfile: userStats.defaultProfile,
      defaultImage: userStats.defaultImage,
      sensitive: userStats.sensitive,
    })
    .from(userProfiles)
    .innerJoin(userStats, eq(userProfiles.twitterId, userStats.twitterId))
    .where(isNotNull(userProfiles.humanScore));

  console.log(`\nFound ${rows.length} profiles with stats\n`);

  // Track changes
  let upgrades = 0;
  let downgrades = 0;
  let unchanged = 0;
  let typeChanges = 0;

  const biggestDowngrades: Array<{
    username: string;
    oldScore: number;
    newScore: number;
    oldType: string;
    newType: string;
    diff: number;
  }> = [];

  const oldDistribution: Record<string, number> = {};
  const newDistribution: Record<string, number> = {};

  for (const row of rows) {
    const profileData = buildProfileData(row);
    const { score: newScore, likelyIs: newType } = computeHASwithConfig(profileData, config);
    const oldScore = row.humanScore ? parseFloat(row.humanScore) : 0;
    const oldType = row.likelyIs ?? "Unknown";

    // Score change
    const diff = newScore - oldScore;
    if (Math.abs(diff) < 0.001) {
      unchanged++;
    } else if (diff > 0) {
      upgrades++;
    } else {
      downgrades++;
      biggestDowngrades.push({
        username: row.username,
        oldScore,
        newScore,
        oldType,
        newType,
        diff,
      });
    }

    // Type change
    if (oldType !== newType) {
      typeChanges++;
    }

    // Distribution buckets
    const oldBucket = getBucket(oldScore);
    const newBucket = getBucket(newScore);
    oldDistribution[oldBucket] = (oldDistribution[oldBucket] ?? 0) + 1;
    newDistribution[newBucket] = (newDistribution[newBucket] ?? 0) + 1;
  }

  // Sort and show biggest downgrades
  biggestDowngrades.sort((a, b) => a.diff - b.diff);

  console.log("=== Score Changes ===");
  console.log(`  Upgrades (higher score): ${upgrades}`);
  console.log(`  Downgrades (lower score): ${downgrades}`);
  console.log(`  Unchanged: ${unchanged}`);
  console.log(`  Type classification changes: ${typeChanges}`);

  console.log("\n=== Top 20 Biggest Downgrades ===");
  for (const d of biggestDowngrades.slice(0, 20)) {
    console.log(
      `  @${d.username}: ${d.oldScore.toFixed(3)} → ${d.newScore.toFixed(3)} (${d.diff.toFixed(3)}) [${d.oldType} → ${d.newType}]`
    );
  }

  console.log("\n=== Score Distribution Comparison ===");
  const buckets = ["0.9-1.0", "0.8-0.9", "0.7-0.8", "0.6-0.7", "0.5-0.6", "<0.5"];
  console.log("  Bucket     | Old    | New    | Change");
  console.log("  -----------|--------|--------|-------");
  for (const bucket of buckets) {
    const old = oldDistribution[bucket] ?? 0;
    const newVal = newDistribution[bucket] ?? 0;
    const change = newVal - old;
    const changeStr = change > 0 ? `+${change}` : change.toString();
    console.log(
      `  ${bucket.padEnd(10)} | ${old.toString().padStart(6)} | ${newVal.toString().padStart(6)} | ${changeStr}`
    );
  }

  // Classification distribution
  console.log("\n=== Classification Distribution ===");
  const oldTypes: Record<string, number> = {};
  const newTypes: Record<string, number> = {};
  for (const row of rows) {
    const profileData = buildProfileData(row);
    const { likelyIs: newType } = computeHASwithConfig(profileData, config);
    const oldType = row.likelyIs ?? "Unknown";
    oldTypes[oldType] = (oldTypes[oldType] ?? 0) + 1;
    newTypes[newType] = (newTypes[newType] ?? 0) + 1;
  }

  const allTypes = [...new Set([...Object.keys(oldTypes), ...Object.keys(newTypes)])];
  console.log("  Type       | Old    | New    | Change");
  console.log("  -----------|--------|--------|-------");
  for (const type of allTypes) {
    const old = oldTypes[type] ?? 0;
    const newVal = newTypes[type] ?? 0;
    const change = newVal - old;
    const changeStr = change > 0 ? `+${change}` : change.toString();
    console.log(
      `  ${type.padEnd(10)} | ${old.toString().padStart(6)} | ${newVal.toString().padStart(6)} | ${changeStr}`
    );
  }

  process.exit(0);
}

function getBucket(score: number): string {
  if (score >= 0.9) return "0.9-1.0";
  if (score >= 0.8) return "0.8-0.9";
  if (score >= 0.7) return "0.7-0.8";
  if (score >= 0.6) return "0.6-0.7";
  if (score >= 0.5) return "0.5-0.6";
  return "<0.5";
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
