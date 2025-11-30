/**
 * Backfill the `audience` column in profile_scores table.
 *
 * This script sets:
 * - `thelai_customers.v0` for all profiles labeled BEFORE Sun Nov 30 00:16:29 2025 -0600
 * - `thelai_customers.v1` for all profiles labeled AFTER that timestamp
 *
 * Usage: yarn tsx js_src/backfill-audience-versions.ts [--dry-run]
 */
import "./env.js";

import { sql } from "drizzle-orm";

import { getDb } from "@profile-scorer/db";

const CUTOFF_TIMESTAMP = "2025-11-30T06:16:29.000Z"; // Nov 30 00:16:29 -0600 converted to UTC

async function main() {
  const isDryRun = process.argv.includes("--dry-run");

  console.log("=".repeat(60));
  console.log("Backfill Audience Versions");
  console.log("=".repeat(60));
  console.log(`Cutoff timestamp: ${CUTOFF_TIMESTAMP}`);
  console.log(`Mode: ${isDryRun ? "DRY RUN" : "LIVE"}`);
  console.log("");

  const db = getDb();

  // Count records before cutoff
  const beforeCount = await db.execute(
    sql`SELECT COUNT(*) as count FROM profile_scores WHERE scored_at < ${CUTOFF_TIMESTAMP}::timestamp`
  );
  const beforeTotal = Number(beforeCount.rows[0]?.count ?? 0);

  // Count records after cutoff
  const afterCount = await db.execute(
    sql`SELECT COUNT(*) as count FROM profile_scores WHERE scored_at >= ${CUTOFF_TIMESTAMP}::timestamp`
  );
  const afterTotal = Number(afterCount.rows[0]?.count ?? 0);

  // Count records with audience already set
  const alreadySet = await db.execute(
    sql`SELECT COUNT(*) as count FROM profile_scores WHERE audience IS NOT NULL`
  );
  const alreadySetTotal = Number(alreadySet.rows[0]?.count ?? 0);

  console.log(`Records before cutoff (v0): ${beforeTotal}`);
  console.log(`Records after cutoff (v1): ${afterTotal}`);
  console.log(`Records with audience already set: ${alreadySetTotal}`);
  console.log("");

  if (isDryRun) {
    console.log("DRY RUN - No changes made");
    console.log("");
    console.log("Would update:");
    console.log(`  - ${beforeTotal} records to 'thelai_customers.v0'`);
    console.log(`  - ${afterTotal} records to 'thelai_customers.v1'`);
    return;
  }

  // Update records before cutoff to v0
  console.log("Updating records before cutoff to thelai_customers.v0...");
  const v0Result = await db.execute(
    sql`UPDATE profile_scores SET audience = 'thelai_customers.v0' WHERE scored_at < ${CUTOFF_TIMESTAMP}::timestamp AND audience IS NULL`
  );
  console.log(`  Updated ${v0Result.rowCount} records`);

  // Update records after cutoff to v1
  console.log("Updating records after cutoff to thelai_customers.v1...");
  const v1Result = await db.execute(
    sql`UPDATE profile_scores SET audience = 'thelai_customers.v1' WHERE scored_at >= ${CUTOFF_TIMESTAMP}::timestamp AND audience IS NULL`
  );
  console.log(`  Updated ${v1Result.rowCount} records`);

  console.log("");
  console.log("Backfill complete!");

  // Verify results
  const verifyCount = await db.execute(
    sql`SELECT audience, COUNT(*) as count FROM profile_scores GROUP BY audience ORDER BY audience`
  );
  console.log("");
  console.log("Final counts by audience:");
  for (const row of verifyCount.rows) {
    console.log(`  ${row.audience ?? "(null)"}: ${row.count}`);
  }
}

main().catch((err) => {
  console.error("Error:", err);
  process.exit(1);
});
