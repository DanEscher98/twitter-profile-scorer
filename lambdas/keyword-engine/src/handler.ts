import { Handler } from "aws-lambda";
import { sql } from "drizzle-orm";
import { createLogger } from "@profile-scorer/utils";
import { getDb, xapiSearchUsage } from "@profile-scorer/db";

const log = createLogger("keyword-engine");

/**
 * Seed keywords for finding qualitative researchers in academia.
 *
 * Categories:
 * - Academic titles and roles
 * - Research methodology terms
 * - Academic disciplines (social sciences, humanities, health)
 * - Institutional affiliations
 * - Research output indicators
 */
const SEED_KEYWORDS = [
  // Academic titles and credentials
  "professor",
  "phd",
  "postdoc",
  "lecturer",
  "academic",
  "faculty",
  "tenure",
  "emeritus",

  // Research roles
  "researcher",
  "scientist",
  "scholar",
  "principal investigator",
  "research fellow",
  "doctoral candidate",
  "research associate",

  // Social sciences
  "sociologist",
  "anthropologist",
  "psychologist",
  "political scientist",
  "economist",
  "geographer",
  "demographer",

  // Health and medical research
  "epidemiologist",
  "public health researcher",
  "health services research",
  "clinical researcher",
  "bioethicist",
  "medical anthropology",
  "health policy",
  "psychiatry",
  "neuroscience",
  "immunologist",
  "oncologist",

  // Industry/applied research
  "pharma",
  "biotech researcher",
  "UX researcher",
  "market researcher",
  "policy analyst",

  // Research indicators
  "peer reviewed",
  "published author",
  "grant funded",
  "NIH funded",
  "NSF funded",
  "h-index",
];

/**
 * Fisher-Yates shuffle algorithm for randomizing array.
 */
function shuffleArray<T>(array: T[]): T[] {
  const shuffled = [...array];
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }
  return shuffled;
}

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

  log.info("Handler invoked", { action, count });

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

    // Shuffle and sample keywords (future: rank by yield and prioritize high-performing)
    const shuffled = shuffleArray(SEED_KEYWORDS);
    const keywords = shuffled.slice(0, count);

    log.info("Returning randomized keywords", { count: keywords.length, poolSize: SEED_KEYWORDS.length, keywords });

    return {
      keywords,
      stats: {
        totalSearches: keywordStats.length,
        keywordYields,
      },
    };
  } catch (error) {
    log.error("Error fetching keyword stats", { error });

    // Fallback to randomized seed keywords on error
    const shuffled = shuffleArray(SEED_KEYWORDS);
    return {
      keywords: shuffled.slice(0, count),
    };
  }
};
