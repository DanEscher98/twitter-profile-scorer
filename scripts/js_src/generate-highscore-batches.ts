#!/usr/bin/env tsx
/**
 * Generate batches from highscores CSV for profiles labeled by haiku.
 *
 * 1. Read profiles from output/1764363500-highscores.csv
 * 2. Find which ones have haiku labels in DB profile_scores
 * 3. Exclude profiles already in dataset/batches/*.toon
 * 4. Create batch-XX.toon and haiku-XX.json starting from batch-26
 *
 * Usage: yarn tsx js_src/generate-highscore-batches.ts
 */
import "./env.js";

import { readFileSync, writeFileSync, readdirSync, mkdirSync } from "fs";
import { join } from "path";
import { fileURLToPath } from "url";

import { encode } from "@toon-format/toon";
import { sql } from "drizzle-orm";

import { getDb } from "@profile-scorer/db";

const __filename = fileURLToPath(import.meta.url);
const __dirname = join(__filename, "..");
const OUTPUT_DIR = join(__dirname, "..", "output");
const BATCHES_DIR = join(__dirname, "..", "dataset", "batches");

const INPUT_CSV = join(OUTPUT_DIR, "1764363500-highscores.csv");
const START_BATCH = 26;
const BATCH_SIZE = 30;

interface ProfileData {
  handle: string;
  name: string;
  bio: string;
  category: string | null;
  followers: number;
}

interface HaikuLabel {
  handle: string;
  label: boolean | null;
  reason: string;
}

interface DBHaikuResult {
  handle: string;
  label: boolean | null;
  reason: string | null;
}

/**
 * Parse CSV line handling quoted fields.
 */
function parseCSVLine(line: string): string[] {
  const fields: string[] = [];
  let current = "";
  let inQuotes = false;

  for (let i = 0; i < line.length; i++) {
    const char = line[i];
    if (char === '"') {
      if (inQuotes && line[i + 1] === '"') {
        current += '"';
        i++;
      } else {
        inQuotes = !inQuotes;
      }
    } else if (char === "," && !inQuotes) {
      fields.push(current);
      current = "";
    } else {
      current += char;
    }
  }
  fields.push(current);
  return fields;
}

/**
 * Parse highscores CSV to get usernames.
 */
function parseHighscoresCSV(content: string): string[] {
  const lines = content.trim().split("\n");
  const handles: string[] = [];

  // Skip header
  for (let i = 1; i < lines.length; i++) {
    const fields = parseCSVLine(lines[i]);
    if (fields.length > 0) {
      const handle = fields[0].trim();
      if (handle) handles.push(handle);
    }
  }

  return handles;
}

/**
 * Get handles already in existing batches.
 */
function getExistingBatchHandles(): Set<string> {
  const handles = new Set<string>();
  const batchFiles = readdirSync(BATCHES_DIR).filter(
    (f) => f.startsWith("batch-") && f.endsWith(".toon")
  );

  for (const file of batchFiles) {
    const content = readFileSync(join(BATCHES_DIR, file), "utf-8");
    const lines = content.trim().split("\n");

    // Skip header line
    for (let i = 1; i < lines.length; i++) {
      const line = lines[i].trim();
      if (!line) continue;

      const fields = parseCSVLine(line);
      if (fields.length > 0) {
        handles.add(fields[0].trim().toLowerCase());
      }
    }
  }

  return handles;
}

async function main() {
  console.log("=".repeat(60));
  console.log("Generate Highscore Batches with Haiku Labels");
  console.log("=".repeat(60));

  // 1. Read highscores CSV
  console.log(`\nReading: ${INPUT_CSV}`);
  const csvContent = readFileSync(INPUT_CSV, "utf-8");
  const allHandles = parseHighscoresCSV(csvContent);
  console.log(`  Total handles in CSV: ${allHandles.length}`);

  // 2. Get existing batch handles to exclude
  console.log("\nScanning existing batches...");
  const existingHandles = getExistingBatchHandles();
  console.log(`  Handles in existing batches: ${existingHandles.size}`);

  // Filter out existing
  const newHandles = allHandles.filter(
    (h) => !existingHandles.has(h.toLowerCase())
  );
  console.log(`  New handles to process: ${newHandles.length}`);

  if (newHandles.length === 0) {
    console.log("\nNo new handles to process.");
    process.exit(0);
  }

  // 3. Query DB for haiku labels
  const db = getDb();
  console.log("\nQuerying DB for haiku labels...");

  // Build array for SQL query
  const handlesArray = `{${newHandles.map((h) => `"${h.replace(/"/g, '\\"')}"`).join(",")}}`;

  const haikuResults = await db.execute<DBHaikuResult>(sql`
    SELECT
      up.handle,
      ps.label,
      ps.reason
    FROM profile_scores ps
    JOIN user_profiles up ON ps.twitter_id = up.twitter_id
    WHERE up.handle = ANY(${handlesArray}::text[])
      AND ps.scored_by LIKE '%haiku%'
  `);

  console.log(`  Found ${haikuResults.rows.length} profiles with haiku labels`);

  if (haikuResults.rows.length === 0) {
    console.log("\nNo profiles with haiku labels found.");
    process.exit(0);
  }

  // Create map of haiku labels
  const haikuMap = new Map<string, HaikuLabel>();
  for (const row of haikuResults.rows) {
    haikuMap.set(row.handle.toLowerCase(), {
      handle: row.handle,
      label: row.label,
      reason: row.reason ?? "",
    });
  }

  // Filter to only handles with haiku labels, maintain order from CSV
  const handlesWithHaiku = newHandles.filter((h) =>
    haikuMap.has(h.toLowerCase())
  );
  console.log(`  Handles with haiku labels (ordered): ${handlesWithHaiku.length}`);

  // 4. Get profile data from DB
  console.log("\nFetching profile data...");
  const profileHandlesArray = `{${handlesWithHaiku.map((h) => `"${h.replace(/"/g, '\\"')}"`).join(",")}}`;

  const profileResults = await db.execute<ProfileData>(sql`
    SELECT
      up.handle,
      up.name,
      COALESCE(up.bio, '') as bio,
      up.category,
      COALESCE(us.followers, 0) as followers
    FROM user_profiles up
    LEFT JOIN user_stats us ON up.twitter_id = us.twitter_id
    WHERE up.handle = ANY(${profileHandlesArray}::text[])
  `);

  // Create map of profile data
  const profileMap = new Map<string, ProfileData>();
  for (const row of profileResults.rows) {
    profileMap.set(row.handle.toLowerCase(), {
      handle: row.handle,
      name: row.name,
      bio: row.bio,
      category: row.category,
      followers: Number(row.followers),
    });
  }

  // Build ordered list with both profile and haiku data
  const profiles: { profile: ProfileData; haiku: HaikuLabel }[] = [];
  for (const handle of handlesWithHaiku) {
    const profile = profileMap.get(handle.toLowerCase());
    const haiku = haikuMap.get(handle.toLowerCase());
    if (profile && haiku) {
      profiles.push({ profile, haiku });
    }
  }

  console.log(`  Complete profiles ready: ${profiles.length}`);

  // 5. Create batches
  mkdirSync(BATCHES_DIR, { recursive: true });
  const numBatches = Math.ceil(profiles.length / BATCH_SIZE);
  console.log(`\nCreating ${numBatches} batches starting from batch-${START_BATCH}...`);

  for (let i = 0; i < numBatches; i++) {
    const batchNum = START_BATCH + i;
    const batchNumStr = String(batchNum).padStart(2, "0");
    const start = i * BATCH_SIZE;
    const end = Math.min(start + BATCH_SIZE, profiles.length);
    const batch = profiles.slice(start, end);

    // Create batch-XX.toon
    const toonData = batch.map((b) => b.profile);
    const toonContent = encode(toonData);
    const toonPath = join(BATCHES_DIR, `batch-${batchNumStr}.toon`);
    writeFileSync(toonPath, toonContent, "utf-8");

    // Create haiku-XX.json with matching handles
    const haikuData: HaikuLabel[] = batch.map((b) => ({
      handle: b.profile.handle, // Use exact handle from profile
      label: b.haiku.label,
      reason: b.haiku.reason,
    }));
    const haikuPath = join(BATCHES_DIR, `haiku-${batchNumStr}.json`);
    writeFileSync(haikuPath, JSON.stringify(haikuData, null, 2), "utf-8");

    console.log(`  Created batch-${batchNumStr}.toon and haiku-${batchNumStr}.json (${batch.length} profiles)`);
  }

  console.log("\n" + "=".repeat(60));
  console.log("Summary:");
  console.log(`  Input CSV handles: ${allHandles.length}`);
  console.log(`  Excluded (already in batches): ${existingHandles.size}`);
  console.log(`  With haiku labels: ${profiles.length}`);
  console.log(`  Batches created: ${numBatches} (batch-${START_BATCH} to batch-${START_BATCH + numBatches - 1})`);
  console.log("=".repeat(60));

  process.exit(0);
}

main().catch((err) => {
  console.error("Error:", err);
  process.exit(1);
});
