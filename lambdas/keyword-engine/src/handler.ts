import { Handler } from "aws-lambda";
import { createLogger } from "@profile-scorer/utils";
import { getValidKeywords } from "@profile-scorer/db";
import { keywordStillHasPages } from "@profile-scorer/twitterx-api";

const log = createLogger("keyword-engine");

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
    // Get valid keywords from keyword_stats table
    const validKeywords = await getValidKeywords();

    if (validKeywords.length === 0) {
      log.warn("No valid keywords found in keyword_stats table");
      return { keywords: [] };
    }

    // Build keyword yields map from the stats
    const keywordYields: Record<string, number> = {};
    for (const kw of validKeywords) {
      keywordYields[kw.keyword] = kw.profilesFound;
    }

    // Shuffle and select keywords with pagination available
    const shuffled = shuffleArray(validKeywords);
    const keywords: string[] = [];
    const discarded: string[] = [];

    for (const kw of shuffled) {
      if (keywords.length >= count) break;

      const hasPages = await keywordStillHasPages(kw.keyword);
      if (hasPages) {
        keywords.push(kw.keyword);
      } else {
        discarded.push(kw.keyword);
      }
    }

    log.info("Selected keywords from pool", {
      selected: keywords,
      discarded: discarded.length > 0 ? discarded : undefined,
      poolSize: validKeywords.length,
    });

    return {
      keywords,
      stats: {
        totalSearches: validKeywords.length,
        keywordYields,
      },
    };
  } catch (error) {
    log.error("Error fetching keywords from pool", { error });
    return { keywords: [] };
  }
};
