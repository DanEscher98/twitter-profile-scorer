/**
 * Export profiles by keyword(s) to TOON format.
 *
 * Usage:
 *   yarn tsx js_src/export-keyword-profiles.ts keyword1 keyword2 ...
 *   yarn tsx js_src/export-keyword-profiles.ts "@customers" "qualitative research"
 *
 * Output: scripts/output/{keyword}-{timestamp}.toon
 */
import "./env.js";

import { writeFileSync, mkdirSync } from "fs";
import { join } from "path";
import { fileURLToPath } from "url";

import { encode } from "@toon-format/toon";

import { getDb, getAllProfilesByKeyword } from "@profile-scorer/db";

const __filename = fileURLToPath(import.meta.url);
const __dirname = join(__filename, "..");
const OUTPUT_DIR = join(__dirname, "..", "output");

interface ProfileRow {
  handle: string;
  name: string;
  bio: string;
  category: string | null;
  followers: number;
}

/**
 * Sanitize keyword for use in filename.
 * Removes special characters, replaces spaces with underscores.
 */
function sanitizeKeyword(keyword: string): string {
  return keyword
    .replace(/^@/, "") // Remove leading @
    .replace(/[^a-zA-Z0-9_-]/g, "_") // Replace special chars
    .replace(/_+/g, "_") // Collapse multiple underscores
    .toLowerCase();
}

/**
 * Export profiles for a single keyword to TOON format.
 */
async function exportKeyword(keyword: string): Promise<string> {
  console.log(`\nFetching profiles for keyword: "${keyword}"`);

  // Fetch all profiles for this keyword (no limit)
  const profiles = await getAllProfilesByKeyword(keyword, 10000, 0);

  if (profiles.length === 0) {
    console.log(`  No profiles found for "${keyword}"`);
    return "";
  }

  console.log(`  Found ${profiles.length} profiles`);

  // Transform to output format
  const data: ProfileRow[] = profiles.map((p) => ({
    handle: p.handle,
    name: p.name,
    bio: p.bio,
    category: p.category,
    followers: p.followers,
  }));

  // Sort by followers descending
  data.sort((a, b) => b.followers - a.followers);

  // Encode to TOON format
  const toon = encode(data);

  // Generate filename: keyword-timestamp.toon
  const timestamp = Math.floor(Date.now() / 1000);
  const sanitized = sanitizeKeyword(keyword);
  const filename = `${sanitized}-${timestamp}.toon`;
  const filepath = join(OUTPUT_DIR, filename);

  // Ensure output directory exists
  mkdirSync(OUTPUT_DIR, { recursive: true });

  // Write file
  writeFileSync(filepath, toon, "utf-8");
  console.log(`  Exported to: ${filepath}`);

  return filepath;
}

async function main() {
  const keywords = process.argv.slice(2);

  if (keywords.length === 0) {
    console.error("Usage: yarn tsx js_src/export-keyword-profiles.ts keyword1 [keyword2 ...]");
    console.error('Example: yarn tsx js_src/export-keyword-profiles.ts "@customers" "qualitative research"');
    process.exit(1);
  }

  console.log("=".repeat(60));
  console.log("Export Keyword Profiles to TOON");
  console.log("=".repeat(60));
  console.log(`Keywords: ${keywords.join(", ")}`);

  // Initialize DB connection
  getDb();

  const exported: string[] = [];

  for (const keyword of keywords) {
    const filepath = await exportKeyword(keyword);
    if (filepath) {
      exported.push(filepath);
    }
  }

  console.log("\n" + "=".repeat(60));
  console.log(`Exported ${exported.length}/${keywords.length} keywords`);
  if (exported.length > 0) {
    console.log("Files:");
    for (const f of exported) {
      console.log(`  - ${f}`);
    }
  }
}

main().catch((err) => {
  console.error("Error:", err);
  process.exit(1);
});
