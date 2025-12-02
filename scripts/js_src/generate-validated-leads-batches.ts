/**
 * Generate hand_picked batches from validated leads CSV.
 *
 * Reads usernames from the validated leads CSV and creates batch files
 * with profile details fetched from the database.
 *
 * Usage: yarn tsx js_src/generate-validated-leads-batches.ts
 *
 * Input: scripts/output/1764367575-validated-leads.csv
 * Output: scripts/dataset/hand_picked/batch-21.toon through batch-25.toon
 */
import "./env.js";

import { readFileSync, writeFileSync, mkdirSync } from "fs";
import { join } from "path";
import { fileURLToPath } from "url";

import { encode } from "@toon-format/toon";
import { sql } from "drizzle-orm";

import { getDb } from "@profile-scorer/db";

const __filename = fileURLToPath(import.meta.url);
const __dirname = join(__filename, "..");
const OUTPUT_DIR = join(__dirname, "..", "output");
const BATCH_DIR = join(__dirname, "..", "dataset", "hand_picked");

const INPUT_FILE = join(OUTPUT_DIR, "1764367575-validated-leads.csv");
const START_BATCH = 21;
const BATCH_SIZE = 30;
const NUM_BATCHES = 5;

interface ProfileData {
  handle: string;
  name: string;
  bio: string;
  category: string | null;
  followers: number;
}

/**
 * Parse CSV to extract usernames from the first column.
 */
function parseUsernames(csvContent: string): string[] {
  const lines = csvContent.trim().split("\n");
  // Skip header row
  const dataLines = lines.slice(1);

  return dataLines
    .map((line) => {
      // First column is USERNAME - handle comma-separated values
      const firstComma = line.indexOf(",");
      if (firstComma === -1) return line.trim();
      return line.substring(0, firstComma).trim();
    })
    .filter((username) => username.length > 0);
}

async function main() {
  console.log("=".repeat(60));
  console.log("Generate Validated Leads Batches");
  console.log("=".repeat(60));

  // Read and parse CSV
  console.log(`\nReading: ${INPUT_FILE}`);
  const csvContent = readFileSync(INPUT_FILE, "utf-8");
  const usernames = parseUsernames(csvContent);
  console.log(`  Found ${usernames.length} usernames`);

  // Take only what we need for 5 batches of 30
  const targetCount = NUM_BATCHES * BATCH_SIZE;
  const selectedUsernames = usernames.slice(0, targetCount);
  console.log(`  Selected ${selectedUsernames.length} for ${NUM_BATCHES} batches`);

  // Initialize DB
  const db = getDb();

  // Fetch profile data from DB
  console.log("\nFetching profile data from database...");

  // Convert array to PostgreSQL array literal
  const usernamesArray = `{${selectedUsernames.map((u) => `"${u.replace(/"/g, '\\"')}"`).join(",")}}`;

  const profiles = await db.execute<ProfileData>(sql`
    SELECT
      up.handle,
      up.name,
      COALESCE(up.bio, '') as bio,
      up.category,
      COALESCE(us.followers, 0) as followers
    FROM user_profiles up
    LEFT JOIN user_stats us ON up.twitter_id = us.twitter_id
    WHERE up.handle = ANY(${usernamesArray}::text[])
  `);

  console.log(`  Found ${profiles.rows.length} profiles in database`);

  // Create a map for quick lookup
  const profileMap = new Map<string, ProfileData>();
  for (const row of profiles.rows) {
    profileMap.set(row.handle.toLowerCase(), {
      handle: row.handle,
      name: row.name,
      bio: row.bio,
      category: row.category,
      followers: Number(row.followers),
    });
  }

  // Match profiles in order, report missing
  const orderedProfiles: ProfileData[] = [];
  const missing: string[] = [];

  for (const username of selectedUsernames) {
    const profile = profileMap.get(username.toLowerCase());
    if (profile) {
      orderedProfiles.push(profile);
    } else {
      missing.push(username);
    }
  }

  if (missing.length > 0) {
    console.log(`\n  WARNING: ${missing.length} profiles not found in DB:`);
    for (const m of missing.slice(0, 10)) {
      console.log(`    - ${m}`);
    }
    if (missing.length > 10) {
      console.log(`    ... and ${missing.length - 10} more`);
    }
  }

  // Ensure batch directory exists
  mkdirSync(BATCH_DIR, { recursive: true });

  // Generate batch files
  console.log("\nGenerating batch files...");

  for (let i = 0; i < NUM_BATCHES; i++) {
    const batchNum = START_BATCH + i;
    const batchNumStr = String(batchNum).padStart(2, "0");
    const start = i * BATCH_SIZE;
    const end = Math.min(start + BATCH_SIZE, orderedProfiles.length);
    const batch = orderedProfiles.slice(start, end);

    if (batch.length === 0) {
      console.log(`  Skipping batch-${batchNumStr} (no profiles)`);
      continue;
    }

    // Use @toon-format/toon encoder
    const toonContent = encode(batch);

    const batchPath = join(BATCH_DIR, `batch-${batchNumStr}.toon`);
    writeFileSync(batchPath, toonContent, "utf-8");
    console.log(`  Written: batch-${batchNumStr}.toon (${batch.length} profiles)`);
  }

  console.log("\n" + "=".repeat(60));
  console.log("Summary:");
  console.log(`  Input usernames: ${usernames.length}`);
  console.log(`  Profiles found: ${orderedProfiles.length}`);
  console.log(`  Batches created: ${NUM_BATCHES} (batch-${START_BATCH} to batch-${START_BATCH + NUM_BATCHES - 1})`);
  console.log(`  Output directory: ${BATCH_DIR}`);
  console.log("=".repeat(60));

  process.exit(0);
}

main().catch((err) => {
  console.error("Error:", err);
  process.exit(1);
});
