/**
 * Export N random profiles to a TOON file.
 * Usage: yarn workspace @profile-scorer/scripts run tsx js_src/export-random-profiles.ts <N>
 * Output: scripts/output/profilesToScore-<N>_<unixtimestamp>.toon
 */
import { encode as toToon } from "@toon-format/toon";
import { sql } from "drizzle-orm";
import { writeFileSync, mkdirSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

import { getDb, userProfiles, userStats } from "@profile-scorer/db";

const __dirname = dirname(fileURLToPath(import.meta.url));

const db = getDb();

interface ProfileForExport {
  handle: string;
  name: string;
  bio: string;
  category: string | null;
  followers: number;
}

async function getRandomProfiles(n: number): Promise<ProfileForExport[]> {
  const rows = await db
    .select({
      handle: userProfiles.handle,
      name: userProfiles.name,
      bio: userProfiles.bio,
      category: userProfiles.category,
      followers: userStats.followers,
    })
    .from(userProfiles)
    .leftJoin(userStats, sql`${userStats.twitterId} = ${userProfiles.twitterId}`)
    .where(
      sql`${userProfiles.bio} IS NOT NULL AND ${userProfiles.bio} != '' AND ${userProfiles.name} IS NOT NULL AND ${userProfiles.name} != ''`
    )
    .orderBy(sql`RANDOM()`)
    .limit(n);

  return rows.map((row) => ({
    handle: row.handle,
    name: row.name!,
    bio: row.bio!,
    category: row.category,
    followers: row.followers ?? 0,
  }));
}

async function main() {
  const args = process.argv.slice(2);
  const n = parseInt(args[0] ?? "10", 10);

  if (isNaN(n) || n <= 0) {
    console.error("Usage: export-random-profiles <N>");
    console.error("  N must be a positive integer");
    process.exit(1);
  }

  console.log(`Fetching ${n} random profiles...`);

  const profiles = await getRandomProfiles(n);

  if (profiles.length === 0) {
    console.error("No profiles found in database");
    process.exit(1);
  }

  console.log(`Found ${profiles.length} profiles`);

  // Generate TOON content
  const toonContent = toToon(profiles);

  // Generate filename
  const timestamp = Math.floor(Date.now() / 1000);
  const filename = `profilesToScore-${profiles.length}_${timestamp}.toon`;

  // Ensure output directory exists
  const outputDir = join(__dirname, "..", "output");
  mkdirSync(outputDir, { recursive: true });

  const outputPath = join(outputDir, filename);

  // Write file
  writeFileSync(outputPath, toonContent, "utf-8");

  console.log(`\nExported to: ${outputPath}`);
  console.log(`  Profiles: ${profiles.length}`);
  console.log(`  Headers: handle, name, bio, category, followers`);

  process.exit(0);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
