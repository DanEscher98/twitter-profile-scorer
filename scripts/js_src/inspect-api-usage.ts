/**
 * Inspect Twitter API usage statistics
 * Usage: yarn workspace @profile-scorer/scripts run run js_src/inspect-api-usage.ts
 */

import { getDb, xapiSearchUsage } from "@profile-scorer/db";
import { desc, count, sum } from "drizzle-orm";

const db = getDb();

async function main() {
  console.log("=== Twitter API Usage Statistics ===\n");

  // Total API calls
  const totalResult = await db.select({ total: count() }).from(xapiSearchUsage);
  console.log(`Total API searches: ${totalResult[0]?.total ?? 0}`);

  // Total profiles fetched (items * searches approximation)
  const itemsResult = await db
    .select({ totalItems: sum(xapiSearchUsage.items) })
    .from(xapiSearchUsage);
  console.log(`Total items requested: ${itemsResult[0]?.totalItems ?? 0}`);

  // New profiles discovered
  const newResult = await db
    .select({ newProfiles: sum(xapiSearchUsage.newProfiles) })
    .from(xapiSearchUsage);
  console.log(`New profiles discovered: ${newResult[0]?.newProfiles ?? 0}`);

  // Usage by keyword
  console.log("\n=== API Usage by Keyword ===");
  const keywordUsage = await db
    .select({
      keyword: xapiSearchUsage.keyword,
      searches: count(),
      totalItems: sum(xapiSearchUsage.items),
      newProfiles: sum(xapiSearchUsage.newProfiles),
    })
    .from(xapiSearchUsage)
    .groupBy(xapiSearchUsage.keyword)
    .orderBy(desc(sum(xapiSearchUsage.newProfiles)))
    .limit(20);

  console.log("Keyword".padEnd(25) + "Searches".padEnd(10) + "Items".padEnd(10) + "New");
  console.log("-".repeat(55));
  for (const row of keywordUsage) {
    console.log(
      `${row.keyword.padEnd(25)}${String(row.searches).padEnd(10)}${String(row.totalItems ?? 0).padEnd(10)}${row.newProfiles ?? 0}`
    );
  }

  // Recent searches
  console.log("\n=== Recent Searches (last 10) ===");
  const recentSearches = await db
    .select({
      keyword: xapiSearchUsage.keyword,
      items: xapiSearchUsage.items,
      newProfiles: xapiSearchUsage.newProfiles,
      queryAt: xapiSearchUsage.queryAt,
    })
    .from(xapiSearchUsage)
    .orderBy(desc(xapiSearchUsage.queryAt))
    .limit(10);

  for (const search of recentSearches) {
    const date = search.queryAt ?? "N/A";
    console.log(
      `[${date}] "${search.keyword}" - ${search.items} items (${search.newProfiles} new)`
    );
  }

  process.exit(0);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
