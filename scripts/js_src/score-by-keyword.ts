#!/usr/bin/env tsx
/**
 * Score ALL profiles found with a specific keyword.
 *
 * Flow:
 * 1. Get ALL profiles labeled with keyword (not just unscored)
 * 2. Score them in batches of 30 using the specified LLM model
 * 3. Upsert each score (insert or update if twitter_id + model already exists)
 * 4. Export results to CSV
 *
 * Usage:
 *   yarn score-keyword <keyword> <model>
 *
 * Example:
 *   yarn score-keyword epidemiologist claude-haiku-4-5-20251001
 *   yarn score-keyword "@customers" claude-sonnet-4-20250514
 *
 * Output:
 *   Creates a CSV file in scripts/output/<keyword>-<model>-<YYYYMMDD>.csv
 *   with columns: username, bio, has_score, llm_score, reason
 */

import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";
import cliProgress from "cli-progress";
import { Table } from "console-table-printer";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

import {
  scoreByKeyword,
  getAvailableModels,
  ScoreAndSaveResult,
  ScoredProfileWithMeta,
} from "@profile-scorer/llm-scoring";
import { getDb, countAllByKeyword } from "@profile-scorer/db";

/**
 * Escape a value for CSV (handles commas, quotes, newlines)
 */
function escapeCsvValue(value: string): string {
  if (value.includes(",") || value.includes('"') || value.includes("\n") || value.includes("\r")) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

/**
 * Convert scored profiles to CSV string
 */
function toCsv(profiles: ScoredProfileWithMeta[]): string {
  const header = "username,bio,has_score,llm_score,reason";
  const rows = profiles.map((p) => {
    return [
      escapeCsvValue(p.username),
      escapeCsvValue(p.bio),
      p.hasScore.toFixed(3),
      p.llmScore.toFixed(3),
      escapeCsvValue(p.reason),
    ].join(",");
  });
  return [header, ...rows].join("\n");
}

/**
 * Get output filename for CSV
 */
function getOutputFilename(keyword: string, model: string): string {
  const timestamp = Math.floor(Date.now() / 1000);
  // Sanitize keyword for filename (replace @ and other special chars)
  const safeKeyword = keyword.replace(/[^a-zA-Z0-9-_]/g, "_");
  return `${safeKeyword}-${model}-${timestamp}.csv`;
}

async function main() {
  const args = process.argv.slice(2);

  if (args.length < 2 || args.includes("--help") || args.includes("-h")) {
    console.log(`
Score ALL profiles found with a specific keyword.

Flow:
  1. Get ALL profiles labeled with keyword
  2. Score them in batches of 30 using the LLM model
  3. Upsert each score (insert or update if exists)
  4. Export results to CSV

Usage:
  yarn score-keyword <keyword> <model>

Available models:
  ${getAvailableModels().join("\n  ")}

Options:
  --help, -h        Show this help message

Output:
  Creates a CSV file in scripts/output/<keyword>-<model>-<YYYYMMDD>.csv
  with columns: username, bio, has_score, llm_score, reason

Example:
  yarn score-keyword epidemiologist claude-haiku-4-5-20251001
  yarn score-keyword "@customers" claude-sonnet-4-20250514
`);
    process.exit(args.includes("--help") || args.includes("-h") ? 0 : 1);
  }

  const keyword = args[0];
  const model = args[1];

  if (!keyword || !model) {
    console.error("Error: Both keyword and model are required");
    process.exit(1);
  }

  const availableModels = getAvailableModels();
  if (!availableModels.includes(model)) {
    console.error(`Error: Unknown model "${model}"`);
    console.error(`Available models: ${availableModels.join(", ")}`);
    process.exit(1);
  }

  console.log(`\nScoring ALL profiles for keyword "${keyword}" with ${model}`);
  console.log(`Batch size: 30 (fixed)\n`);

  // Initialize DB and get total count
  getDb();
  const totalProfiles = await countAllByKeyword(keyword);

  if (totalProfiles === 0) {
    console.log(`No profiles found for keyword "${keyword}".`);
    process.exit(0);
  }

  console.log(`Found ${totalProfiles} profiles to score\n`);

  // Progress bar
  const progressBar = new cliProgress.SingleBar(
    {
      format: "Progress |{bar}| {percentage}% | {value}/{total} | Batch {batch} | ETA: {eta}s",
      hideCursor: true,
    },
    cliProgress.Presets.shades_classic
  );

  progressBar.start(totalProfiles, 0, { batch: 0 });

  let totalProcessed = 0;

  const result = await scoreByKeyword(
    keyword,
    model,
    (batch: number, batchResult: ScoreAndSaveResult) => {
      totalProcessed += batchResult.scored + batchResult.skipped;
      progressBar.update(Math.min(totalProcessed, totalProfiles), { batch });
    }
  );

  progressBar.stop();

  // Display results in table
  console.log(`\n✓ Scoring complete!\n`);

  const summaryTable = new Table({
    columns: [
      { name: "metric", title: "Metric", alignment: "left" },
      { name: "value", title: "Value", alignment: "right" },
    ],
  });

  summaryTable.addRows([
    { metric: "Keyword", value: keyword },
    { metric: "Model", value: model },
    { metric: "Total profiles", value: result.totalProfiles },
    { metric: "Batches processed", value: result.batches },
    { metric: "New scores", value: result.totalScored },
    { metric: "Updated scores", value: result.totalSkipped },
    { metric: "Errors", value: result.totalErrors },
  ]);

  summaryTable.printTable();

  // Write CSV if there are scored profiles
  if (result.scoredProfiles.length > 0) {
    const outputDir = path.join(__dirname, "..", "output");
    if (!fs.existsSync(outputDir)) {
      fs.mkdirSync(outputDir, { recursive: true });
    }

    const filename = getOutputFilename(keyword, model);
    const outputPath = path.join(outputDir, filename);
    const csv = toCsv(result.scoredProfiles);

    fs.writeFileSync(outputPath, csv, "utf-8");
    console.log(`\n✓ CSV saved to: ${outputPath}`);
    console.log(`  Rows: ${result.scoredProfiles.length}`);
  } else {
    console.log(`\nNo profiles scored - CSV not created.`);
  }
}

main().catch((err) => {
  console.error("Error:", err.message);
  process.exit(1);
});
