/**
 * Upload Curated Users Script
 *
 * Processes a list of curated Twitter usernames from a file,
 * fetches their profiles via getUser, and queues them for LLM scoring.
 *
 * Usage:
 *   yarn workspace @profile-scorer/scripts run tsx js_src/upload_curated_users.ts <handlers.txt>
 *
 * Example:
 *   yarn workspace @profile-scorer/scripts run tsx js_src/upload_curated_users.ts ../data/curated_usernames.txt
 */
import cliProgress from "cli-progress";
import { Table } from "console-table-printer";
import { readFileSync } from "fs";

import { getDb, insertToScore } from "@profile-scorer/db";
import { TwitterXApiError, wrappers } from "@profile-scorer/twitterx-api";

const { getUser } = wrappers;

interface ResultRow {
  handle: string;
  human_score: number;
  likely_is: string;
  followers: number | null;
  from_api: boolean;
  status: string;
}

async function processHandler(handle: string): Promise<ResultRow> {
  try {
    const { profile, fromApi } = await getUser(handle, { keyword: "@customers" });

    // Queue for LLM scoring (ignore duplicates)
    try {
      await insertToScore(profile.twitter_id, profile.handle);
    } catch {
      // Ignore - profile may already be queued
    }

    return {
      handle,
      human_score: profile.human_score,
      likely_is: profile.likely_is,
      followers: profile.follower_count,
      from_api: fromApi,
      status: "success",
    };
  } catch (e: unknown) {
    if (e instanceof TwitterXApiError) {
      return {
        handle,
        human_score: 0,
        likely_is: "-",
        followers: null,
        from_api: false,
        status: e.errorCode,
      };
    }
    return {
      handle,
      human_score: 0,
      likely_is: "-",
      followers: null,
      from_api: false,
      status: "error",
    };
  }
}

function printTable(rows: ResultRow[]) {
  const table = new Table({
    columns: [
      { name: "handle", alignment: "left" },
      { name: "human_score", alignment: "right" },
      { name: "likely_is", alignment: "left" },
      { name: "followers", alignment: "right" },
      { name: "source", alignment: "left" },
      { name: "status", alignment: "left" },
    ],
  });

  for (const row of rows) {
    table.addRow(
      {
        handle: row.handle,
        human_score: row.human_score.toFixed(2),
        likely_is: row.likely_is,
        followers: row.followers?.toString() ?? "-",
        source: row.from_api ? "API" : "cache",
        status: row.status,
      },
      { color: row.status === "success" ? "green" : "red" }
    );
  }

  table.printTable();
}

async function main() {
  // Default to curated_usernames.txt if no path provided
  const filepath = process.argv[2] || "data/curated_usernames.txt";

  // Initialize DB connection
  console.log("Initializing database connection...");
  getDb();
  console.log("Database connected.\n");

  const content = readFileSync(filepath, "utf-8");
  const handles = content
    .split("\n")
    .map((h) => h.trim())
    .filter((h) => h && !h.startsWith("#"));

  console.log(`Processing ${handles.length} handlers...\n`);

  // Progress bar
  const progressBar = new cliProgress.SingleBar(
    {
      format: "Progress |{bar}| {percentage}% | {value}/{total} | {handle}",
      hideCursor: true,
    },
    cliProgress.Presets.shades_classic
  );

  progressBar.start(handles.length, 0, { handle: "" });

  // Process sequentially to avoid rate limiting
  const results: ResultRow[] = [];
  for (let i = 0; i < handles.length; i++) {
    const handle = handles[i]!;
    progressBar.update(i, { handle });
    const result = await processHandler(handle);
    results.push(result);
    progressBar.update(i + 1, { handle });
  }

  progressBar.stop();

  console.log("\n");
  printTable(results);

  const successful = results.filter((r) => r.status === "success").length;
  const fromApi = results.filter((r) => r.from_api).length;
  const humans = results.filter((r) => r.human_score > 0.55).length;
  console.log(
    `\nTotal: ${results.length} | Success: ${successful} | From API: ${fromApi} | Humans (HAS>0.55): ${humans}`
  );
}

main().catch(console.error);
