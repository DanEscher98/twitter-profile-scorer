#!/usr/bin/env tsx
/**
 * Score all pending profiles for a given model.
 *
 * Usage:
 *   yarn score-all <model> [--batch-size=25]
 *
 * Example:
 *   yarn score-all claude-haiku-4-5-20251001
 *   yarn score-all gemini-2.0-flash --batch-size=15
 */

import cliProgress from "cli-progress";
import {
  scoreAllPending,
  getAvailableModels,
  ScoreAndSaveResult,
} from "@profile-scorer/llm-scoring";
import { getDb, getProfilesToScore } from "@profile-scorer/db";

async function main() {
  const args = process.argv.slice(2);

  if (args.length === 0 || args.includes("--help") || args.includes("-h")) {
    console.log(`
Score all pending profiles for a given model.

Usage:
  yarn score-all <model> [--batch-size=25]

Available models:
  ${getAvailableModels().join("\n  ")}

Options:
  --batch-size=N    Profiles per batch (default: 25)
  --help, -h        Show this help message

Example:
  yarn score-all claude-haiku-4-5-20251001
  yarn score-all gemini-2.0-flash --batch-size=15
`);
    process.exit(0);
  }

  const model = args[0];
  if (!model) {
    console.error("Error: Model is required");
    process.exit(1);
  }

  const availableModels = getAvailableModels();
  if (!availableModels.includes(model)) {
    console.error(`Error: Unknown model "${model}"`);
    console.error(`Available models: ${availableModels.join(", ")}`);
    process.exit(1);
  }

  // Parse batch size
  let batchSize = 25;
  const batchArg = args.find((a) => a.startsWith("--batch-size="));
  if (batchArg) {
    batchSize = parseInt(batchArg.split("=")[1] ?? "25", 10);
    if (isNaN(batchSize) || batchSize < 1) {
      console.error("Error: Invalid batch size");
      process.exit(1);
    }
  }

  console.log(`\nScoring all pending profiles with ${model}`);
  console.log(`Batch size: ${batchSize}\n`);

  // Initialize DB and get initial count
  getDb();
  const initialProfiles = await getProfilesToScore(model, 1000);
  const totalEstimate = initialProfiles.length;

  if (totalEstimate === 0) {
    console.log("No profiles to score.");
    process.exit(0);
  }

  console.log(`Found approximately ${totalEstimate} profiles to score\n`);

  // Progress bar
  const progressBar = new cliProgress.SingleBar(
    {
      format: "Progress |{bar}| {percentage}% | {value}/{total} | Batch {batch} | ETA: {eta}s",
      hideCursor: true,
    },
    cliProgress.Presets.shades_classic
  );

  progressBar.start(totalEstimate, 0, { batch: 0 });

  let totalProcessed = 0;

  const result = await scoreAllPending(
    model,
    batchSize,
    0.6,
    (batch: number, batchResult: ScoreAndSaveResult) => {
      totalProcessed += batchResult.scored + batchResult.skipped;
      progressBar.update(Math.min(totalProcessed, totalEstimate), { batch });
    }
  );

  progressBar.stop();

  console.log(`\n✓ Scoring complete!`);
  console.log(`  Model: ${model}`);
  console.log(`  Batches: ${result.batches}`);
  console.log(`  Scored: ${result.totalScored}`);
  console.log(`  Skipped (already scored): ${result.totalSkipped}`);
  console.log(`  Errors: ${result.totalErrors}`);
}

main().catch((err) => {
  console.error("Error:", err.message);
  process.exit(1);
});
