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
import cliProgress from "cli-progress";
import { Table } from "console-table-printer";
// Load environment variables from root .env

import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";

import { countAllByKeyword, getDb } from "@profile-scorer/db";
import {
  AudienceConfig,
  LabelAndSaveResult,
  LabeledProfileWithMeta,
  getAvailableModels,
  labelByKeyword,
} from "@profile-scorer/llm-scoring";

import "./env.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/**
 * Escape a value for CSV (handles commas, quotes, newlines)
 */
function escapeCsvValue(value: string): string {
  if (value.includes(",") || value.includes('"') || value.includes("\n") || value.includes("\r")) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

// Default audience config for CLI usage
const defaultAudienceConfig: AudienceConfig = {
  targetProfile: "qualitative researcher",
  sector: "academia",
  highSignals: [
    "Qualitative methodology keywords: ethnography, grounded theory, discourse analysis",
    "Fields with strong qualitative foundations: sociology, anthropology, social work",
    "Academic research leadership roles: PI, lab director, research scientist",
  ],
  lowSignals: [
    "Clinical-only roles without research designation",
    "Quantitative-primary fields: biostatistics, data science",
    "Organization/company account (not individual)",
  ],
  domainContext: "Target profiles conduct human-subjects research requiring participant recruitment and qualitative data analysis.",
};

/**
 * Convert labeled profiles to CSV string
 */
function toCsv(profiles: LabeledProfileWithMeta[]): string {
  const header = "handle,bio,label,reason";
  const rows = profiles.map((p) => {
    const labelStr = p.label === true ? "true" : p.label === false ? "false" : "null";
    return [
      escapeCsvValue(p.handle),
      escapeCsvValue(p.bio),
      labelStr,
      escapeCsvValue(p.reason),
    ].join(",");
  });
  return [header, ...rows].join("\n");
}

/**
 * Get output filename for CSV
 * Format: unixtimestamp_keyword_model.csv
 */
function getOutputFilename(keyword: string, model: string): string {
  const timestamp = Math.floor(Date.now() / 1000);
  // Sanitize keyword for filename (replace @ and other special chars)
  const safeKeyword = keyword.replace(/[^a-zA-Z0-9-_]/g, "_");
  return `${timestamp}_${safeKeyword}_${model}.csv`;
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

  console.log(`\nLabeling ALL profiles for keyword "${keyword}" with ${model}`);
  console.log(`Batch size: 30 (fixed)\n`);

  // Initialize DB and get total count
  getDb();
  const totalProfiles = await countAllByKeyword(keyword);

  if (totalProfiles === 0) {
    console.log(`No profiles found for keyword "${keyword}".`);
    process.exit(0);
  }

  console.log(`Found ${totalProfiles} profiles to label\n`);

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

  const result = await labelByKeyword(
    keyword,
    model,
    defaultAudienceConfig,
    (batch: number, batchResult: LabelAndSaveResult) => {
      totalProcessed += batchResult.labeled + batchResult.skipped;
      progressBar.update(Math.min(totalProcessed, totalProfiles), { batch });
    }
  );

  progressBar.stop();

  // Display results in table
  console.log(`\n✓ Labeling complete!\n`);

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
    { metric: "New labels", value: result.totalLabeled },
    { metric: "Updated labels", value: result.totalSkipped },
    { metric: "Errors", value: result.totalErrors },
  ]);

  summaryTable.printTable();

  // Write CSV if there are labeled profiles
  if (result.labeledProfiles.length > 0) {
    const outputDir = path.join(__dirname, "..", "output");
    if (!fs.existsSync(outputDir)) {
      fs.mkdirSync(outputDir, { recursive: true });
    }

    const filename = getOutputFilename(keyword, model);
    const outputPath = path.join(outputDir, filename);
    const csv = toCsv(result.labeledProfiles);

    fs.writeFileSync(outputPath, csv, "utf-8");
    console.log(`\n✓ CSV saved to: ${outputPath}`);
    console.log(`  Rows: ${result.labeledProfiles.length}`);
  } else {
    console.log(`\nNo profiles labeled - CSV not created.`);
  }
}

main().catch((err) => {
  console.error("Error:", err.message);
  process.exit(1);
});
