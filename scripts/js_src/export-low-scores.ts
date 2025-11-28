#!/usr/bin/env tsx
/**
 * Export low-scoring profiles to CSV with blessed TUI.
 *
 * Gathers ALL profiles with FINAL_SCORE < 0.5 for analysis of
 * what the pipeline is correctly rejecting.
 *
 * Final Score Equation:
 *   - With LLM scores: FINAL_SCORE = 0.2 * HAS + 0.8 * AVG_LLM
 *   - Without LLM scores: FINAL_SCORE = HAS (use HAS directly, don't penalize)
 *
 * This avoids over-penalizing profiles that haven't been LLM-scored yet
 * by NOT multiplying HAS by 0.2 when there's no LLM score to contribute.
 *
 * Usage:
 *   LOG_LEVEL=silent yarn workspace @profile-scorer/scripts run tsx js_src/export-low-scores.ts
 *
 * Output:
 *   scripts/output/<timestamp>-lowscores.csv
 */

import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";
import blessed from "blessed";
import { getDb } from "@profile-scorer/db";
import { eq } from "drizzle-orm";
import {
  userProfiles,
  profileScores,
  userKeywords,
  userStats,
} from "@profile-scorer/db";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Score thresholds
const HAS_WEIGHT = 0.2;
const LLM_WEIGHT = 0.8;
const MAX_FINAL_SCORE = 0.5;

interface ProfileData {
  twitterId: string;
  username: string;
  bio: string;
  followers: number;
  hasScore: number;
  likelyIs: string;
}

interface ProfileWithScores extends ProfileData {
  llmScores: number[];
  avgLlmScore: number | null;
  finalScore: number;
  keywords: string[];
  hasLlmScores: boolean;
}

// TUI state
let screen: blessed.Widgets.Screen;
let progressBar: blessed.Widgets.ProgressBarElement;
let logBox: blessed.Widgets.BoxElement;
let statsBox: blessed.Widgets.BoxElement;
let logs: string[] = [];

function initTUI() {
  screen = blessed.screen({
    smartCSR: true,
    title: "Low Scores Export",
  });

  // Title
  blessed.box({
    parent: screen,
    top: 0,
    left: "center",
    width: "100%",
    height: 3,
    content: "{center}{bold}Low Scores Export (<0.5){/bold}{/center}",
    tags: true,
    style: { fg: "white", bg: "red" },
  });

  // Progress bar
  progressBar = blessed.progressbar({
    parent: screen,
    top: 3,
    left: 0,
    width: "100%",
    height: 3,
    border: { type: "line" },
    style: {
      fg: "white",
      bg: "default",
      bar: { bg: "yellow" },
      border: { fg: "cyan" },
    },
    ch: "█",
    filled: 0,
    label: " Progress: 0% ",
  });

  // Stats box
  statsBox = blessed.box({
    parent: screen,
    top: 6,
    left: 0,
    width: "30%",
    height: "50%",
    border: { type: "line" },
    label: " Statistics ",
    tags: true,
    style: { border: { fg: "cyan" } },
    content: "Loading...",
  });

  // Log box
  logBox = blessed.box({
    parent: screen,
    top: 6,
    left: "30%",
    width: "70%",
    height: "50%",
    border: { type: "line" },
    label: " Progress Log ",
    tags: true,
    scrollable: true,
    alwaysScroll: true,
    scrollbar: { ch: " ", track: { bg: "cyan" }, style: { inverse: true } },
    style: { border: { fg: "cyan" } },
  });

  // Output box
  blessed.box({
    parent: screen,
    top: "56%",
    left: 0,
    width: "100%",
    height: "44%",
    border: { type: "line" },
    label: " Output Preview (Lowest Scores) ",
    tags: true,
    scrollable: true,
    alwaysScroll: true,
    style: { border: { fg: "red" } },
    content: "Waiting for results...",
  });

  // Quit on q or Ctrl-C
  screen.key(["q", "C-c"], () => {
    screen.destroy();
    process.exit(0);
  });

  screen.render();
}

function log(message: string) {
  const timestamp = new Date().toISOString().slice(11, 19);
  logs.push(`[${timestamp}] ${message}`);
  if (logs.length > 100) logs.shift();
  logBox.setContent(logs.join("\n"));
  logBox.setScrollPerc(100);
  screen.render();
}

function updateProgress(current: number, total: number) {
  const pct = Math.round((current / total) * 100);
  progressBar.setProgress(pct);
  progressBar.setLabel(` Progress: ${pct}% (${current}/${total}) `);
  screen.render();
}

function updateStats(stats: Record<string, string | number>) {
  const content = Object.entries(stats)
    .map(([k, v]) => `{bold}${k}:{/bold} ${v}`)
    .join("\n");
  statsBox.setContent(content);
  screen.render();
}

function updateOutput(profiles: ProfileWithScores[]) {
  const outputBox = screen.children.find(
    (c) => c.options.label === " Output Preview (Lowest Scores) "
  ) as blessed.Widgets.BoxElement;
  if (!outputBox) return;

  const preview = profiles.slice(0, 15).map((p, i) => {
    const score = p.finalScore.toFixed(3);
    const llmInfo = p.hasLlmScores ? `LLM:${p.avgLlmScore?.toFixed(2)}` : "no-LLM";
    const bio = (p.bio || "No bio").slice(0, 35).replace(/\n/g, " ");
    return `${i + 1}. @${p.username} (${score}) [${llmInfo}] - ${bio}...`;
  });

  outputBox.setContent(
    `Bottom ${Math.min(15, profiles.length)} of ${profiles.length} low-scoring profiles:\n\n${preview.join("\n")}`
  );
  screen.render();
}

async function fetchAllProfiles(): Promise<ProfileData[]> {
  const db = getDb();

  log("Fetching all profiles...");

  const profiles = await db
    .select({
      twitterId: userProfiles.twitterId,
      username: userProfiles.username,
      bio: userProfiles.bio,
      hasScore: userProfiles.humanScore,
      likelyIs: userProfiles.likelyIs,
      followers: userStats.followers,
    })
    .from(userProfiles)
    .leftJoin(userStats, eq(userProfiles.twitterId, userStats.twitterId));

  log(`Found ${profiles.length} total profiles`);

  return profiles.map((p) => ({
    twitterId: p.twitterId,
    username: p.username,
    bio: p.bio ?? "",
    followers: p.followers ?? 0,
    hasScore: parseFloat(p.hasScore ?? "0"),
    likelyIs: p.likelyIs ?? "Unknown",
  }));
}

function processProfile(
  profile: ProfileData,
  allScores: Map<string, number[]>,
  allKeywords: Map<string, string[]>
): ProfileWithScores {
  const llmScores = allScores.get(profile.twitterId) ?? [];
  const hasLlmScores = llmScores.length > 0;

  // Calculate average LLM score if available
  const avgLlmScore = hasLlmScores
    ? llmScores.reduce((a, b) => a + b, 0) / llmScores.length
    : null;

  // Calculate final score
  // If LLM scores exist: 0.2 * HAS + 0.8 * AVG_LLM
  // If no LLM scores: just use HAS (profiles that haven't been LLM-scored yet)
  const finalScore = hasLlmScores
    ? HAS_WEIGHT * profile.hasScore + LLM_WEIGHT * avgLlmScore!
    : profile.hasScore;

  // Get keywords
  const keywords = allKeywords.get(profile.twitterId) ?? [];

  return {
    ...profile,
    llmScores,
    avgLlmScore,
    finalScore,
    keywords,
    hasLlmScores,
  };
}

async function fetchAllScores(): Promise<Map<string, number[]>> {
  const db = getDb();
  log("Fetching all LLM scores...");

  const scores = await db
    .select({
      twitterId: profileScores.twitterId,
      score: profileScores.score,
    })
    .from(profileScores);

  log(`Fetched ${scores.length} total LLM scores`);

  const scoreMap = new Map<string, number[]>();
  for (const s of scores) {
    const existing = scoreMap.get(s.twitterId) ?? [];
    existing.push(parseFloat(s.score));
    scoreMap.set(s.twitterId, existing);
  }

  return scoreMap;
}

async function fetchAllKeywords(): Promise<Map<string, string[]>> {
  const db = getDb();
  log("Fetching all keywords...");

  const keywords = await db
    .select({
      twitterId: userKeywords.twitterId,
      keyword: userKeywords.keyword,
    })
    .from(userKeywords);

  log(`Fetched ${keywords.length} keyword associations`);

  const keywordMap = new Map<string, string[]>();
  for (const k of keywords) {
    const existing = keywordMap.get(k.twitterId) ?? [];
    if (!existing.includes(k.keyword)) {
      existing.push(k.keyword);
    }
    keywordMap.set(k.twitterId, existing);
  }

  return keywordMap;
}

function escapeCsvValue(value: string): string {
  if (
    value.includes(",") ||
    value.includes('"') ||
    value.includes("\n") ||
    value.includes("\r")
  ) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

function toCsv(profiles: ProfileWithScores[]): string {
  const header = "USERNAME,BIO,FOLLOWERS,FINAL_SCORE,HAS_SCORE,AVG_LLM,HAS_LLM,IS_LIKELY,TAGS";
  const rows = profiles.map((p) => {
    return [
      escapeCsvValue(p.username),
      escapeCsvValue(p.bio.replace(/\n/g, " ").slice(0, 500)),
      p.followers.toString(),
      p.finalScore.toFixed(4),
      p.hasScore.toFixed(4),
      p.avgLlmScore !== null ? p.avgLlmScore.toFixed(4) : "",
      p.hasLlmScores ? "true" : "false",
      escapeCsvValue(p.likelyIs),
      escapeCsvValue(p.keywords.join("; ")),
    ].join(",");
  });
  return [header, ...rows].join("\n");
}

async function main() {
  initTUI();

  const startTime = Date.now();
  let stats = {
    "Total Profiles": 0,
    "With LLM Scores": 0,
    "Low Score (<0.5)": 0,
    "Exported": 0,
    "Avg Low Score": "-",
    "Processing": "...",
    "Elapsed": "0s",
  };
  updateStats(stats);

  try {
    // Initialize DB
    log("Initializing database connection...");
    getDb();

    // Fetch all data in parallel
    log("Fetching data from database...");
    const [profiles, allScores, allKeywords] = await Promise.all([
      fetchAllProfiles(),
      fetchAllScores(),
      fetchAllKeywords(),
    ]);

    stats["Total Profiles"] = profiles.length;
    stats["With LLM Scores"] = allScores.size;
    updateStats(stats);

    // Process profiles concurrently using Promise.allSettled
    log(`Processing ${profiles.length} profiles...`);
    stats["Processing"] = "Running...";
    updateStats(stats);

    const BATCH_SIZE = 100;
    const allProcessed: ProfileWithScores[] = [];
    let processed = 0;

    for (let i = 0; i < profiles.length; i += BATCH_SIZE) {
      const batch = profiles.slice(i, i + BATCH_SIZE);

      const results = await Promise.allSettled(
        batch.map((profile) =>
          Promise.resolve(processProfile(profile, allScores, allKeywords))
        )
      );

      for (const result of results) {
        if (result.status === "fulfilled") {
          allProcessed.push(result.value);
        }
      }

      processed += batch.length;
      updateProgress(processed, profiles.length);

      stats["Elapsed"] = `${Math.round((Date.now() - startTime) / 1000)}s`;
      updateStats(stats);
    }

    // Filter for low scores (< 0.5)
    const lowScoreProfiles = allProcessed.filter(
      (p) => p.finalScore < MAX_FINAL_SCORE
    );

    stats["Low Score (<0.5)"] = lowScoreProfiles.length;
    updateStats(stats);

    log(`Found ${lowScoreProfiles.length} profiles with score < ${MAX_FINAL_SCORE}`);

    // Sort by final score ascending (lowest first) - export ALL low scores
    lowScoreProfiles.sort((a, b) => a.finalScore - b.finalScore);

    stats["Exported"] = lowScoreProfiles.length;

    // Calculate stats
    const avgScore =
      lowScoreProfiles.length > 0
        ? lowScoreProfiles.reduce((sum, p) => sum + p.finalScore, 0) /
          lowScoreProfiles.length
        : 0;

    const withLlm = lowScoreProfiles.filter((p) => p.hasLlmScores).length;

    stats["Avg Low Score"] = avgScore.toFixed(3);
    stats["Processing"] = "Complete!";
    updateStats(stats);

    log(`Exporting ${lowScoreProfiles.length} low-scoring profiles`);
    log(`${withLlm} have LLM scores, ${lowScoreProfiles.length - withLlm} HAS-only`);
    updateOutput(lowScoreProfiles);

    // Write CSV
    if (lowScoreProfiles.length > 0) {
      const timestamp = Math.floor(Date.now() / 1000);
      const outputDir = path.join(__dirname, "..", "output");
      if (!fs.existsSync(outputDir)) {
        fs.mkdirSync(outputDir, { recursive: true });
      }

      const filename = `${timestamp}-lowscores.csv`;
      const outputPath = path.join(outputDir, filename);
      const csv = toCsv(lowScoreProfiles);

      fs.writeFileSync(outputPath, csv, "utf-8");
      log(`CSV saved to: ${outputPath}`);
      log(`Total rows: ${lowScoreProfiles.length}`);
    } else {
      log("No low-scoring profiles found. CSV not created.");
    }

    log("");
    log("Press 'q' to exit.");
  } catch (error) {
    log(`Error: ${error instanceof Error ? error.message : String(error)}`);
    stats["Processing"] = "Error!";
    updateStats(stats);
  }
}

main().catch((err) => {
  if (screen) screen.destroy();
  console.error("Fatal error:", err.message);
  process.exit(1);
});
