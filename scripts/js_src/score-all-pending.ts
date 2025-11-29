#!/usr/bin/env tsx
// Load environment variables from root .env

import blessed from "blessed";
import { Table } from "console-table-printer";

import { ProfileToScore, getDb, getProfilesToScore, insertProfileScore } from "@profile-scorer/db";
import { getAvailableModels, scoreProfiles } from "@profile-scorer/llm-scoring";

import "./env.js";

/**
 * Score all pending profiles for a given model using parallel batch processing.
 *
 * Features:
 * - Fixed progress bar at top (using blessed)
 * - Scrolling log window below
 * - Parallel batch processing with Promise.allSettled
 *
 * Usage:
 *   yarn score-all <model> [--batch-size=25] [--threshold=0.55] [--concurrency=3]
 */

// Suppress ALL library logs FIRST (before any imports)
process.env.LOG_LEVEL = "silent";

/**
 * Split array into chunks of specified size
 */
function chunkArray<T>(array: T[], size: number): T[][] {
  const chunks: T[][] = [];
  for (let i = 0; i < array.length; i += size) {
    chunks.push(array.slice(i, i + size));
  }
  return chunks;
}

/**
 * Process a single batch: score and save to DB
 */
async function processBatch(
  profiles: ProfileToScore[],
  model: string,
  batchNum: number,
  logFn: (msg: string) => void
): Promise<{ scored: number; errors: number; skipped: number }> {
  if (profiles.length === 0) {
    return { scored: 0, errors: 0, skipped: 0 };
  }

  logFn(`Batch ${batchNum}: Sending ${profiles.length} profiles...`);

  // Score profiles with LLM
  const scores = await scoreProfiles(profiles, model);

  // Save to DB
  let scored = 0;
  let errors = 0;
  let skipped = 0;

  for (const score of scores) {
    try {
      await insertProfileScore(score.twitterId, score.score, score.reason, model);
      scored++;
    } catch (error: any) {
      if (error.code === "23505") {
        skipped++;
      } else {
        errors++;
      }
    }
  }

  logFn(`Batch ${batchNum}: ✓ ${scored} scored, ${skipped} skipped, ${errors} errors`);
  return { scored, errors, skipped };
}

async function main() {
  const args = process.argv.slice(2);

  if (args.length === 0 || args.includes("--help") || args.includes("-h")) {
    console.log(`
Score all pending profiles for a given model (parallel batch processing).

Usage:
  yarn score-all <model> [--batch-size=25] [--threshold=0.55] [--concurrency=3]

Available models:
  ${getAvailableModels().join("\n  ")}

Options:
  --batch-size=N    Profiles per batch (default: 25)
  --threshold=N     Minimum HAS score (default: 0.55)
  --concurrency=N   Parallel batches (default: 3)
  --help, -h        Show this help message

Example:
  yarn score-all claude-haiku-4-5-20251001
  yarn score-all claude-opus-4-5-20251101 --batch-size=10 --concurrency=5
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

  // Parse threshold
  let threshold = 0.55;
  const thresholdArg = args.find((a) => a.startsWith("--threshold="));
  if (thresholdArg) {
    threshold = parseFloat(thresholdArg.split("=")[1] ?? "0.55");
    if (isNaN(threshold) || threshold < 0 || threshold > 1) {
      console.error("Error: Invalid threshold (must be 0-1)");
      process.exit(1);
    }
  }

  // Parse concurrency
  let concurrency = 10;
  const concurrencyArg = args.find((a) => a.startsWith("--concurrency="));
  if (concurrencyArg) {
    concurrency = parseInt(concurrencyArg.split("=")[1] ?? "3", 10);
    if (isNaN(concurrency) || concurrency < 1) {
      console.error("Error: Invalid concurrency");
      process.exit(1);
    }
  }

  // Initialize DB and get ALL profiles to score (up to 10k)
  getDb();
  const allProfiles = await getProfilesToScore(model, 10000, threshold);
  const totalProfiles = allProfiles.length;

  if (totalProfiles === 0) {
    console.log("\nNo profiles to score.\n");
    process.exit(0);
  }

  // Split into batches
  const batches = chunkArray(allProfiles, batchSize);
  const totalBatches = batches.length;

  // Create blessed screen
  const screen = blessed.screen({
    smartCSR: true,
    title: "Profile Scorer",
  });

  // Header box
  const headerBox = blessed.box({
    top: 0,
    left: 0,
    width: "100%",
    height: 10,
    content:
      `
┌─────────────────────────────────────────────────────────────┐
│  Score All Pending Profiles                                 │
├─────────────────────────────────────────────────────────────┤
│  Model:       ${model.padEnd(45)}│
│  Profiles:    ${String(totalProfiles).padEnd(45)}│
│  Batches:     ${totalBatches} (size: ${batchSize}, concurrency: ${concurrency})`.padEnd(59) +
      `│
│  Threshold:   ${String(threshold).padEnd(45)}│
└─────────────────────────────────────────────────────────────┘`,
    style: {
      fg: "white",
    },
  });

  // Progress bar
  const progressBar = blessed.progressbar({
    top: 10,
    left: 0,
    width: "100%",
    height: 3,
    border: { type: "line" },
    style: {
      bar: { bg: "green" },
      border: { fg: "cyan" },
    },
    filled: 0,
    label: " Progress: 0% | 0/" + totalProfiles + " profiles | Batch 0/" + totalBatches + " ",
  });

  // Log window
  const logBox = blessed.log({
    top: 13,
    left: 0,
    width: "100%",
    height: "100%-13",
    border: { type: "line" },
    style: {
      border: { fg: "cyan" },
    },
    label: " Logs ",
    scrollable: true,
    alwaysScroll: true,
    scrollbar: {
      ch: "│",
      style: { bg: "cyan" },
    },
    mouse: true,
  });

  screen.append(headerBox);
  screen.append(progressBar);
  screen.append(logBox);

  // Allow quit with q or Ctrl+C
  screen.key(["q", "C-c"], () => {
    screen.destroy();
    process.exit(0);
  });

  screen.render();

  // Helper to log
  const log = (msg: string) => {
    const timestamp = new Date().toLocaleTimeString();
    logBox.log(`[${timestamp}] ${msg}`);
    screen.render();
  };

  // Helper to update progress
  const updateProgress = (current: number, batch: number) => {
    const pct = Math.floor((current / totalProfiles) * 100);
    progressBar.setProgress(pct);
    progressBar.setLabel(
      ` Progress: ${pct}% | ${current}/${totalProfiles} profiles | Batch ${batch}/${totalBatches} `
    );
    screen.render();
  };

  log(`Starting scoring with ${concurrency} concurrent batches...`);

  let totalScored = 0;
  let totalErrors = 0;
  let totalSkipped = 0;
  let completedBatches = 0;
  let processedProfiles = 0;

  // Process batches in chunks of `concurrency` - TRUE PARALLEL
  const batchChunks = chunkArray(batches, concurrency);

  for (const chunk of batchChunks) {
    // Launch all batches in this chunk in parallel
    const batchStartIdx = completedBatches;
    const promises = chunk.map((batch, i) =>
      processBatch(batch, model, batchStartIdx + i + 1, log)
    );

    // Wait for all to complete (parallel execution)
    const results = await Promise.allSettled(promises);

    // Process results
    for (const [i, result] of results.entries()) {
      completedBatches++;
      const batchProfiles = chunk[i]?.length ?? 0;

      if (result.status === "fulfilled") {
        const { scored, errors, skipped } = result.value;
        totalScored += scored;
        totalErrors += errors;
        totalSkipped += skipped;
      } else {
        totalErrors += batchProfiles;
        log(`Batch ${completedBatches}: ✗ Failed - ${result.reason}`);
      }

      processedProfiles += batchProfiles;
      updateProgress(processedProfiles, completedBatches);
    }
  }

  // Final summary in log
  log("");
  log("═".repeat(50));
  log("✓ SCORING COMPLETE");
  log("═".repeat(50));
  log(`  Total profiles: ${totalProfiles}`);
  log(`  Batches:        ${totalBatches}`);
  log(`  Scored:         ${totalScored}`);
  log(`  Skipped:        ${totalSkipped}`);
  log(`  Errors:         ${totalErrors}`);
  log("");
  log("Press 'q' to exit...");

  // Wait for user to press q
  await new Promise<void>((resolve) => {
    screen.key(["q", "C-c", "enter", "space"], () => {
      resolve();
    });
  });

  // Cleanup and show final table
  screen.destroy();

  console.log("\n✓ Scoring complete!\n");

  const summaryTable = new Table({
    columns: [
      { name: "metric", title: "Metric", alignment: "left" },
      { name: "value", title: "Value", alignment: "right" },
    ],
  });

  summaryTable.addRows([
    { metric: "Model", value: model },
    { metric: "Total profiles", value: totalProfiles },
    { metric: "Batches processed", value: totalBatches },
    { metric: "Concurrency", value: concurrency },
    { metric: "Scored", value: totalScored },
    { metric: "Skipped (already scored)", value: totalSkipped },
    { metric: "Errors", value: totalErrors },
  ]);

  summaryTable.printTable();
}

main().catch((err) => {
  console.error("Error:", err.message);
  process.exit(1);
});
