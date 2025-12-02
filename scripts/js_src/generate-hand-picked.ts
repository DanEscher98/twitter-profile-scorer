/**
 * Generate hand_picked.toon with 600 random profiles for manual labeling.
 *
 * Creates:
 * - scripts/dataset/hand_picked.toon - Master list with (handle, label, reason) for manual labeling
 * - scripts/dataset/hand_picked/batch-XX.toon - 20 batch files with profile details (handle, name, bio, category, followers)
 *
 * Excludes profiles with keyword "@customers".
 * Profiles are sorted by handle (ascending).
 *
 * Usage: yarn tsx js_src/generate-hand-picked.ts
 */
import "./env.js";

import { writeFileSync, mkdirSync } from "fs";
import { join } from "path";
import { fileURLToPath } from "url";

import { sql } from "drizzle-orm";

import { getDb } from "@profile-scorer/db";

const __filename = fileURLToPath(import.meta.url);
const __dirname = join(__filename, "..");
const DATASET_DIR = join(__dirname, "..", "dataset");
const BATCH_DIR = join(DATASET_DIR, "hand_picked");

const TOTAL_PROFILES = 600;
const BATCH_SIZE = 30;

interface ProfileData {
  handle: string;
  name: string;
  bio: string;
  category: string | null;
  followers: number;
}

async function main() {
  console.log("=".repeat(60));
  console.log("Generate Hand-Picked Dataset");
  console.log("=".repeat(60));

  const db = getDb();

  // Query 600 random unique profiles from profile_scores
  // Exclude profiles with keyword "@customers"
  // Join with user_profiles and user_stats for details
  console.log("\nFetching 600 random profiles (excluding @customers)...");

  const profiles = await db.execute<ProfileData>(sql`
    SELECT DISTINCT ON (up.handle)
      up.handle,
      up.name,
      COALESCE(up.bio, '') as bio,
      up.category,
      COALESCE(us.followers, 0) as followers
    FROM profile_scores ps
    JOIN user_profiles up ON ps.twitter_id = up.twitter_id
    LEFT JOIN user_stats us ON up.twitter_id = us.twitter_id
    WHERE NOT EXISTS (
      SELECT 1 FROM user_keywords uk
      WHERE uk.twitter_id = up.twitter_id
      AND uk.keyword = '@customers'
    )
    AND up.bio IS NOT NULL
    AND up.bio != ''
    ORDER BY up.handle, RANDOM()
    LIMIT ${TOTAL_PROFILES}
  `);

  // Convert to array and shuffle, then sort by handle
  const profileList: ProfileData[] = profiles.rows.map((row) => ({
    handle: row.handle,
    name: row.name,
    bio: row.bio,
    category: row.category,
    followers: Number(row.followers),
  }));

  // Shuffle first to get random selection
  for (let i = profileList.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [profileList[i], profileList[j]] = [profileList[j], profileList[i]];
  }

  // Take only TOTAL_PROFILES if we got more
  const selected = profileList.slice(0, TOTAL_PROFILES);

  // Sort by handle ascending
  selected.sort((a, b) => a.handle.toLowerCase().localeCompare(b.handle.toLowerCase()));

  console.log(`  Found ${selected.length} profiles`);

  if (selected.length < TOTAL_PROFILES) {
    console.warn(`  WARNING: Only found ${selected.length} profiles, expected ${TOTAL_PROFILES}`);
  }

  // Create directories
  mkdirSync(DATASET_DIR, { recursive: true });
  mkdirSync(BATCH_DIR, { recursive: true });

  // Generate hand_picked.toon - master list for manual labeling
  console.log("\nGenerating hand_picked.toon...");
  const masterLines: string[] = [`hand_picked[${selected.length}]{handle,label,reason}:`];
  for (const p of selected) {
    // Escape handle if it contains special characters
    const handle = p.handle.includes(",") || p.handle.includes('"')
      ? `"${p.handle.replace(/"/g, '""')}"`
      : p.handle;
    masterLines.push(`  ${handle}, null, ""`);
  }
  const masterPath = join(DATASET_DIR, "hand_picked.toon");
  writeFileSync(masterPath, masterLines.join("\n") + "\n", "utf-8");
  console.log(`  Written: ${masterPath}`);

  // Generate batch files
  console.log("\nGenerating batch files...");
  const numBatches = Math.ceil(selected.length / BATCH_SIZE);

  for (let i = 0; i < numBatches; i++) {
    const batchNum = String(i + 1).padStart(2, "0");
    const start = i * BATCH_SIZE;
    const end = Math.min(start + BATCH_SIZE, selected.length);
    const batch = selected.slice(start, end);

    const batchLines: string[] = [`[${batch.length}]{handle,name,bio,category,followers}:`];
    for (const p of batch) {
      // Escape fields that might contain special characters
      const handle = escapeField(p.handle);
      const name = escapeField(p.name);
      const bio = escapeField(p.bio);
      const category = p.category ? escapeField(p.category) : "null";
      batchLines.push(`  ${handle},${name},${bio},${category},${p.followers}`);
    }

    const batchPath = join(BATCH_DIR, `batch-${batchNum}.toon`);
    writeFileSync(batchPath, batchLines.join("\n") + "\n", "utf-8");
    console.log(`  Written: batch-${batchNum}.toon (${batch.length} profiles)`);
  }

  console.log("\n" + "=".repeat(60));
  console.log("Summary:");
  console.log(`  Total profiles: ${selected.length}`);
  console.log(`  Master file: ${masterPath}`);
  console.log(`  Batch files: ${numBatches} files in ${BATCH_DIR}`);
  console.log("=".repeat(60));
}

/**
 * Escape a field for TOON format.
 * Wraps in quotes if contains comma, quote, or newline.
 */
function escapeField(value: string): string {
  // Replace newlines with spaces
  const cleaned = value.replace(/[\n\r]+/g, " ").trim();

  // If contains comma, quote, or special chars, wrap in quotes
  if (cleaned.includes(",") || cleaned.includes('"') || cleaned.includes("\t")) {
    return `"${cleaned.replace(/"/g, '""')}"`;
  }
  return cleaned;
}

main().catch((err) => {
  console.error("Error:", err);
  process.exit(1);
});
