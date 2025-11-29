#!/usr/bin/env tsx
/**
 * Validate Curated Leads CSV
 *
 * Checks an LLM-curated leads CSV against the database to detect:
 * - Usernames that don't exist in DB (hallucinated)
 * - Follower count discrepancies (> 20% difference)
 * - Score discrepancies
 *
 * Outputs a validated CSV with only verified entries.
 *
 * Usage:
 *   LOG_LEVEL=silent yarn workspace @profile-scorer/scripts run tsx js_src/validate-curated-leads.ts <input.csv>
 *
 * Output:
 *   scripts/output/<timestamp>-validated-leads.csv
 *   scripts/output/<timestamp>-validation-report.txt
 */
import blessed from "blessed";
import { eq, inArray } from "drizzle-orm";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";

import { getDb } from "@profile-scorer/db";
import { profileScores, userProfiles, userStats } from "@profile-scorer/db";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Thresholds
const FOLLOWER_DISCREPANCY_THRESHOLD = 0.2; // 20% difference
const SCORE_DISCREPANCY_THRESHOLD = 0.05; // 0.05 absolute difference

interface CuratedLead {
  username: string;
  bio: string;
  followers: number;
  score: number;
  tags: string;
  rationale: string;
}

interface ValidationResult {
  username: string;
  status: "valid" | "not_found" | "follower_mismatch" | "score_mismatch";
  csvFollowers: number;
  dbFollowers: number | null;
  csvScore: number;
  dbScore: number | null;
  followerDiff: number | null;
  scoreDiff: number | null;
  canDm: boolean | null;
  note: string;
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
    title: "Validate Curated Leads",
  });

  // Title
  blessed.box({
    parent: screen,
    top: 0,
    left: "center",
    width: "100%",
    height: 3,
    content: "{center}{bold}Validate Curated Leads{/bold}{/center}",
    tags: true,
    style: { fg: "white", bg: "blue" },
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
      bar: { bg: "green" },
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
    width: "35%",
    height: "50%",
    border: { type: "line" },
    label: " Validation Stats ",
    tags: true,
    style: { border: { fg: "cyan" } },
    content: "Loading...",
  });

  // Log box
  logBox = blessed.box({
    parent: screen,
    top: 6,
    left: "35%",
    width: "65%",
    height: "50%",
    border: { type: "line" },
    label: " Validation Log ",
    tags: true,
    scrollable: true,
    alwaysScroll: true,
    scrollbar: { ch: " ", track: { bg: "cyan" }, style: { inverse: true } },
    style: { border: { fg: "cyan" } },
  });

  // Results box
  blessed.box({
    parent: screen,
    top: "56%",
    left: 0,
    width: "100%",
    height: "44%",
    border: { type: "line" },
    label: " Issues Found ",
    tags: true,
    scrollable: true,
    alwaysScroll: true,
    style: { border: { fg: "yellow" } },
    content: "Waiting for validation...",
  });

  // Quit on q or Ctrl-C
  screen.key(["q", "C-c"], () => {
    screen.destroy();
    process.exit(0);
  });

  screen.render();
}

function log(message: string, type: "info" | "warn" | "error" = "info") {
  const timestamp = new Date().toISOString().slice(11, 19);
  const prefix =
    type === "error" ? "{red-fg}✗{/}" : type === "warn" ? "{yellow-fg}⚠{/}" : "{green-fg}✓{/}";
  logs.push(`[${timestamp}] ${prefix} ${message}`);
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

function updateIssues(issues: ValidationResult[]) {
  const issuesBox = screen.children.find(
    (c) => c.options.label === " Issues Found "
  ) as blessed.Widgets.BoxElement;
  if (!issuesBox) return;

  if (issues.length === 0) {
    issuesBox.setContent("{green-fg}No issues found! All entries validated.{/}");
  } else {
    const lines = issues.map((r) => {
      if (r.status === "not_found") {
        return `{red-fg}NOT FOUND:{/} @${r.username}`;
      } else if (r.status === "follower_mismatch") {
        const pct = r.followerDiff !== null ? (r.followerDiff * 100).toFixed(1) : "?";
        return `{yellow-fg}FOLLOWERS:{/} @${r.username} - CSV: ${r.csvFollowers}, DB: ${r.dbFollowers} (${pct}% diff)`;
      } else if (r.status === "score_mismatch") {
        return `{yellow-fg}SCORE:{/} @${r.username} - CSV: ${r.csvScore.toFixed(4)}, DB: ${r.dbScore?.toFixed(4)} (diff: ${r.scoreDiff?.toFixed(4)})`;
      }
      return `{green-fg}OK:{/} @${r.username}`;
    });
    issuesBox.setContent(lines.join("\n"));
  }
  screen.render();
}

function parseCsv(content: string): CuratedLead[] {
  const lines = content.trim().split("\n");
  const leads: CuratedLead[] = [];

  // Skip header
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i]!;
    if (!line.trim()) continue;

    // Parse CSV with potential quoted fields
    const fields: string[] = [];
    let current = "";
    let inQuotes = false;

    for (let j = 0; j < line.length; j++) {
      const char = line[j]!;
      if (char === '"') {
        if (inQuotes && line[j + 1] === '"') {
          current += '"';
          j++;
        } else {
          inQuotes = !inQuotes;
        }
      } else if (char === "," && !inQuotes) {
        fields.push(current.trim());
        current = "";
      } else {
        current += char;
      }
    }
    fields.push(current.trim());

    if (fields.length >= 5) {
      leads.push({
        username: fields[0]!.trim(),
        bio: fields[1]!.replace(/^"|"$/g, "").trim(),
        followers: parseInt(fields[2]!.trim(), 10) || 0,
        score: parseFloat(fields[3]!.trim()) || 0,
        tags: fields[4]!.trim(),
        rationale: fields[5]?.replace(/^"|"$/g, "").trim() || "",
      });
    }
  }

  return leads;
}

async function validateLeads(leads: CuratedLead[]): Promise<ValidationResult[]> {
  const db = getDb();
  const results: ValidationResult[] = [];

  // Fetch all usernames from DB in one query
  const usernames = leads.map((l) => l.username);
  log(`Fetching ${usernames.length} profiles from database...`);

  const dbProfiles = await db
    .select({
      username: userProfiles.username,
      followers: userStats.followers,
      humanScore: userProfiles.humanScore,
      canDm: userProfiles.canDm,
    })
    .from(userProfiles)
    .leftJoin(userStats, eq(userProfiles.twitterId, userStats.twitterId))
    .where(inArray(userProfiles.username, usernames));

  log(`Found ${dbProfiles.length} matching profiles in DB`);

  // Create lookup map
  const dbMap = new Map<
    string,
    { followers: number | null; humanScore: string | null; canDm: boolean | null }
  >();
  for (const p of dbProfiles) {
    dbMap.set(p.username.toLowerCase(), {
      followers: p.followers,
      humanScore: p.humanScore,
      canDm: p.canDm,
    });
  }

  // Fetch LLM scores for computing final score
  const twitterIds = await db
    .select({
      username: userProfiles.username,
      twitterId: userProfiles.twitterId,
    })
    .from(userProfiles)
    .where(inArray(userProfiles.username, usernames));

  const idMap = new Map<string, string>();
  for (const p of twitterIds) {
    idMap.set(p.username.toLowerCase(), p.twitterId);
  }

  const ids = Array.from(idMap.values());
  const scores =
    ids.length > 0
      ? await db
          .select({
            twitterId: profileScores.twitterId,
            score: profileScores.score,
          })
          .from(profileScores)
          .where(inArray(profileScores.twitterId, ids))
      : [];

  // Compute average LLM score per profile
  const scoreMap = new Map<string, number[]>();
  for (const s of scores) {
    const existing = scoreMap.get(s.twitterId) ?? [];
    existing.push(parseFloat(s.score));
    scoreMap.set(s.twitterId, existing);
  }

  // Validate each lead
  for (let i = 0; i < leads.length; i++) {
    const lead = leads[i]!;
    const dbData = dbMap.get(lead.username.toLowerCase());

    updateProgress(i + 1, leads.length);

    if (!dbData) {
      results.push({
        username: lead.username,
        status: "not_found",
        csvFollowers: lead.followers,
        dbFollowers: null,
        csvScore: lead.score,
        dbScore: null,
        followerDiff: null,
        scoreDiff: null,
        canDm: null,
        note: "Username not found in database - possible hallucination",
      });
      log(`@${lead.username} - NOT FOUND in DB`, "error");
      continue;
    }

    // Check follower discrepancy
    const dbFollowers = dbData.followers ?? 0;
    const followerDiff =
      dbFollowers > 0
        ? Math.abs(lead.followers - dbFollowers) / dbFollowers
        : lead.followers > 0
          ? 1
          : 0;

    // Compute DB final score
    const twitterId = idMap.get(lead.username.toLowerCase());
    const llmScores = twitterId ? (scoreMap.get(twitterId) ?? []) : [];
    const avgLlm =
      llmScores.length > 0 ? llmScores.reduce((a, b) => a + b, 0) / llmScores.length : 0;
    const hasScore = parseFloat(dbData.humanScore ?? "0");
    const dbFinalScore = 0.2 * hasScore + 0.8 * avgLlm;

    const scoreDiff = Math.abs(lead.score - dbFinalScore);

    if (followerDiff > FOLLOWER_DISCREPANCY_THRESHOLD) {
      results.push({
        username: lead.username,
        status: "follower_mismatch",
        csvFollowers: lead.followers,
        dbFollowers,
        csvScore: lead.score,
        dbScore: dbFinalScore,
        followerDiff,
        scoreDiff,
        canDm: dbData.canDm,
        note: `Follower count differs by ${(followerDiff * 100).toFixed(1)}%`,
      });
      log(`@${lead.username} - Follower mismatch: ${lead.followers} vs ${dbFollowers}`, "warn");
    } else if (scoreDiff > SCORE_DISCREPANCY_THRESHOLD) {
      results.push({
        username: lead.username,
        status: "score_mismatch",
        csvFollowers: lead.followers,
        dbFollowers,
        csvScore: lead.score,
        dbScore: dbFinalScore,
        followerDiff,
        scoreDiff,
        canDm: dbData.canDm,
        note: `Score differs by ${scoreDiff.toFixed(4)}`,
      });
      log(
        `@${lead.username} - Score mismatch: ${lead.score} vs ${dbFinalScore.toFixed(4)}`,
        "warn"
      );
    } else {
      results.push({
        username: lead.username,
        status: "valid",
        csvFollowers: lead.followers,
        dbFollowers,
        csvScore: lead.score,
        dbScore: dbFinalScore,
        followerDiff,
        scoreDiff,
        canDm: dbData.canDm,
        note: "Validated successfully",
      });
      log(`@${lead.username} - Valid`);
    }
  }

  return results;
}

function escapeCsvValue(value: string): string {
  if (value.includes(",") || value.includes('"') || value.includes("\n") || value.includes("\r")) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

function generateValidatedCsv(leads: CuratedLead[], results: ValidationResult[]): string {
  // Get valid results and sort by score descending
  const validResults = results
    .filter((r) => r.status === "valid")
    .sort((a, b) => (b.dbScore ?? 0) - (a.dbScore ?? 0));

  const header = "USERNAME,BIO,FOLLOWERS,SCORE,CAN_DM,TAGS,RATIONALE";
  const rows = validResults
    .map((result) => {
      // Find matching lead for bio/tags/rationale
      const lead = leads.find((l) => l.username.toLowerCase() === result.username.toLowerCase());
      if (!lead) return null;

      return [
        escapeCsvValue(result.username),
        escapeCsvValue(lead.bio),
        (result.dbFollowers ?? lead.followers).toString(),
        (result.dbScore ?? lead.score).toFixed(4),
        result.canDm === true ? "true" : result.canDm === false ? "false" : "unknown",
        escapeCsvValue(lead.tags),
        escapeCsvValue(lead.rationale),
      ].join(",");
    })
    .filter((row): row is string => row !== null);

  return [header, ...rows].join("\n");
}

function generateReport(results: ValidationResult[], leads: CuratedLead[]): string {
  const valid = results.filter((r) => r.status === "valid");
  const notFound = results.filter((r) => r.status === "not_found");
  const followerMismatch = results.filter((r) => r.status === "follower_mismatch");
  const scoreMismatch = results.filter((r) => r.status === "score_mismatch");

  let report = `CURATED LEADS VALIDATION REPORT
================================
Generated: ${new Date().toISOString()}

SUMMARY
-------
Total entries: ${leads.length}
Valid: ${valid.length} (${((valid.length / leads.length) * 100).toFixed(1)}%)
Not Found: ${notFound.length}
Follower Mismatch: ${followerMismatch.length}
Score Mismatch: ${scoreMismatch.length}

`;

  if (notFound.length > 0) {
    report += `NOT FOUND (Possible Hallucinations)
------------------------------------
${notFound.map((r) => `- @${r.username}`).join("\n")}

`;
  }

  if (followerMismatch.length > 0) {
    report += `FOLLOWER MISMATCHES (>${FOLLOWER_DISCREPANCY_THRESHOLD * 100}% difference)
--------------------------------------------------
${followerMismatch
  .map(
    (r) =>
      `- @${r.username}: CSV=${r.csvFollowers}, DB=${r.dbFollowers} (${((r.followerDiff ?? 0) * 100).toFixed(1)}% diff)`
  )
  .join("\n")}

`;
  }

  if (scoreMismatch.length > 0) {
    report += `SCORE MISMATCHES (>${SCORE_DISCREPANCY_THRESHOLD} difference)
-----------------------------------------
${scoreMismatch
  .map(
    (r) =>
      `- @${r.username}: CSV=${r.csvScore.toFixed(4)}, DB=${r.dbScore?.toFixed(4)} (diff: ${r.scoreDiff?.toFixed(4)})`
  )
  .join("\n")}

`;
  }

  report += `VALIDATED ENTRIES
-----------------
${valid.map((r) => `- @${r.username}`).join("\n")}
`;

  return report;
}

async function main() {
  const inputPath = process.argv[2] || "output/thelai_curated_leads.csv";
  const fullInputPath = path.isAbsolute(inputPath)
    ? inputPath
    : path.join(__dirname, "..", inputPath);

  if (!fs.existsSync(fullInputPath)) {
    console.error(`Error: File not found: ${fullInputPath}`);
    process.exit(1);
  }

  initTUI();

  const startTime = Date.now();
  let stats = {
    "Input File": path.basename(inputPath),
    "Total Entries": 0,
    Valid: 0,
    "Not Found": 0,
    "Follower Issues": 0,
    "Score Issues": 0,
    Status: "Loading...",
  };
  updateStats(stats);

  try {
    // Initialize DB
    log("Initializing database connection...");
    getDb();

    // Parse CSV
    log(`Reading ${path.basename(inputPath)}...`);
    const content = fs.readFileSync(fullInputPath, "utf-8");
    const leads = parseCsv(content);
    stats["Total Entries"] = leads.length;
    updateStats(stats);

    log(`Parsed ${leads.length} entries from CSV`);

    // Validate
    stats["Status"] = "Validating...";
    updateStats(stats);

    const results = await validateLeads(leads);

    // Calculate stats
    const valid = results.filter((r) => r.status === "valid");
    const notFound = results.filter((r) => r.status === "not_found");
    const followerIssues = results.filter((r) => r.status === "follower_mismatch");
    const scoreIssues = results.filter((r) => r.status === "score_mismatch");

    stats["Valid"] = valid.length;
    stats["Not Found"] = notFound.length;
    stats["Follower Issues"] = followerIssues.length;
    stats["Score Issues"] = scoreIssues.length;
    stats["Status"] = "Complete!";
    updateStats(stats);

    // Show issues
    const issues = results.filter((r) => r.status !== "valid");
    updateIssues(issues);

    // Write outputs
    const timestamp = Math.floor(Date.now() / 1000);
    const outputDir = path.join(__dirname, "..", "output");
    if (!fs.existsSync(outputDir)) {
      fs.mkdirSync(outputDir, { recursive: true });
    }

    // Validated CSV (only valid entries)
    const validatedCsv = generateValidatedCsv(leads, results);
    const csvPath = path.join(outputDir, `${timestamp}-validated-leads.csv`);
    fs.writeFileSync(csvPath, validatedCsv, "utf-8");
    log(`Validated CSV saved: ${path.basename(csvPath)}`);

    // Validation report
    const report = generateReport(results, leads);
    const reportPath = path.join(outputDir, `${timestamp}-validation-report.txt`);
    fs.writeFileSync(reportPath, report, "utf-8");
    log(`Validation report saved: ${path.basename(reportPath)}`);

    log("");
    log(`Elapsed: ${Math.round((Date.now() - startTime) / 1000)}s`);
    log("Press 'q' to exit.");
  } catch (error) {
    log(`Error: ${error instanceof Error ? error.message : String(error)}`, "error");
    stats["Status"] = "Error!";
    updateStats(stats);
  }
}

main().catch((err) => {
  if (screen) screen.destroy();
  console.error("Fatal error:", err.message);
  process.exit(1);
});
