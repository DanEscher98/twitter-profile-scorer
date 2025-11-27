import { Handler } from "aws-lambda";
import { sql } from "drizzle-orm";

import { getDb, xapiSearchUsage } from "@profile-scorer/db";

// Seed keywords for qualitative researchers
const SEED_KEYWORDS = ["researcher", "phd", "psychiatry", "neuroscience", "pharma"];

export interface KeywordEngineEvent {
  action?: "get_keywords" | "health_check";
  count?: number;
}

export interface KeywordEngineResponse {
  keywords: string[];
  stats?: {
    totalSearches: number;
    keywordYields: Record<string, number>;
  };
}

export const handler: Handler<KeywordEngineEvent, KeywordEngineResponse> = async (event) => {
  const action = event?.action ?? "get_keywords";
  const count = event?.count ?? 5;

  console.log(`[keyword-engine] Action: ${action}, Count: ${count}`);

  if (action === "health_check") {
    return {
      keywords: [],
      stats: { totalSearches: 0, keywordYields: {} },
    };
  }

  try {
    const db = getDb();

    // Get keyword performance stats from xapi_usage_search
    const keywordStats = await db
      .select({
        keyword: xapiSearchUsage.keyword,
        totalSearches: sql<number>`count(*)`,
        totalNewProfiles: sql<number>`sum(${xapiSearchUsage.newProfiles})`,
      })
      .from(xapiSearchUsage)
      .groupBy(xapiSearchUsage.keyword);

    const keywordYields: Record<string, number> = {};
    for (const stat of keywordStats) {
      keywordYields[stat.keyword] = Number(stat.totalNewProfiles) || 0;
    }

    // For now, return seed keywords (future: rank by yield)
    const keywords = SEED_KEYWORDS.slice(0, count);

    console.log(`[keyword-engine] Returning ${keywords.length} keywords:`, keywords);

    return {
      keywords,
      stats: {
        totalSearches: keywordStats.length,
        keywordYields,
      },
    };
  } catch (error) {
    console.error("[keyword-engine] Error:", error);

    // Fallback to seed keywords on error
    return {
      keywords: SEED_KEYWORDS.slice(0, count),
    };
  }
};
