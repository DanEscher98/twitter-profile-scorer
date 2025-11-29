#!/usr/bin/env tsx
/**
 * Add a new keyword to the keyword_stats pool.
 *
 * Usage:
 *   yarn add-keyword <keyword> [--tags=#tag1,#tag2,...]
 *
 * Example:
 *   yarn add-keyword "data scientist"
 *   yarn add-keyword epidemiologist --tags=#academia,#health
 *   yarn add-keyword "ML engineer" --tags=#industry,#tech
 */
import { getDb, insertKeyword } from "@profile-scorer/db";

async function main() {
  const args = process.argv.slice(2);

  if (args.length === 0 || args.includes("--help") || args.includes("-h")) {
    console.log(`
Add a new keyword to the keyword_stats pool.

Usage:
  yarn add-keyword <keyword> [--tags=#tag1,#tag2,...]

Options:
  --tags=...      Comma-separated semantic tags (e.g., #academia,#health)
  --help, -h      Show this help message

Examples:
  yarn add-keyword "data scientist"
  yarn add-keyword epidemiologist --tags=#academia,#health
  yarn add-keyword "ML engineer" --tags=#industry,#tech

Note:
  If the keyword already exists, its semantic tags will be updated.
`);
    process.exit(0);
  }

  // Find keyword (first non-flag argument)
  const keyword = args.find((a) => !a.startsWith("--"));
  if (!keyword) {
    console.error("Error: Keyword is required");
    process.exit(1);
  }

  // Parse tags
  let semanticTags: string[] = [];
  const tagsArg = args.find((a) => a.startsWith("--tags="));
  if (tagsArg) {
    const tagsValue = tagsArg.split("=")[1];
    if (tagsValue) {
      semanticTags = tagsValue
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean);
    }
  }

  // Initialize DB
  getDb();

  console.log(`\nAdding keyword: "${keyword}"`);
  if (semanticTags.length > 0) {
    console.log(`Semantic tags: ${semanticTags.join(", ")}`);
  }

  try {
    await insertKeyword(keyword, semanticTags);
    console.log(`\n✓ Keyword added successfully!`);
  } catch (error) {
    console.error("\nError adding keyword:", error);
    process.exit(1);
  }
}

main().catch((err) => {
  console.error("Error:", err.message);
  process.exit(1);
});
