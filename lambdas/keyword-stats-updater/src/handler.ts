import { Handler } from "aws-lambda";

import {
  calculateKeywordStats,
  getAllSearchedKeywords,
  upsertKeywordStats,
} from "@profile-scorer/db";
import { createLogger } from "@profile-scorer/utils";

const log = createLogger("keyword-stats-updater");

export interface KeywordStatsUpdaterEvent {
  action?: "update_all" | "health_check";
}

export interface KeywordStatsUpdaterResponse {
  updated: number;
  keywords: string[];
  errors: string[];
}

export const handler: Handler<KeywordStatsUpdaterEvent, KeywordStatsUpdaterResponse> = async (
  event
) => {
  const action = event?.action ?? "update_all";

  log.info("Handler invoked", { action });

  if (action === "health_check") {
    return { updated: 0, keywords: [], errors: [] };
  }

  const errors: string[] = [];
  const updatedKeywords: string[] = [];

  try {
    // Get all keywords that have been searched
    const keywords = await getAllSearchedKeywords();
    log.info("Found keywords to update", { count: keywords.length });

    // Calculate and upsert stats for each keyword
    for (const keyword of keywords) {
      try {
        const stats = await calculateKeywordStats(keyword);
        await upsertKeywordStats(stats);
        updatedKeywords.push(keyword);

        log.debug("Updated keyword stats", {
          keyword,
          profilesFound: stats.profilesFound,
          avgHumanScore: stats.avgHumanScore.toFixed(3),
          stillValid: stats.stillValid,
        });
      } catch (err: any) {
        log.error("Failed to update keyword stats", { keyword, error: err.message });
        errors.push(`${keyword}: ${err.message}`);
      }
    }

    log.info("Completed keyword stats update", {
      updated: updatedKeywords.length,
      errors: errors.length,
    });

    return {
      updated: updatedKeywords.length,
      keywords: updatedKeywords,
      errors,
    };
  } catch (error: any) {
    log.error("Fatal error in handler", { error: error.message });
    return {
      updated: 0,
      keywords: [],
      errors: [error.message],
    };
  }
};
