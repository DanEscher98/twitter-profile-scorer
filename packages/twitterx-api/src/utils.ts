import { getKeywordLatestPage } from "@profile-scorer/db";

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function normalizeString(input: string): string {
  return input
    .replace(/\\/g, "") // remove backslashes
    .replace(/["']/g, "") // remove quotes
    .replace(/,/g, "") // remove commas
    .replace(/[\x00-\x1F\x7F]/g, "") // remove control/escape chars
    .trim();
}

/**
 * Check if a keyword still has pagination available.
 * Returns true if:
 * - Keyword has never been searched (no entries in xapi_usage_search)
 * - Latest page has a next_page cursor
 */
export async function keywordStillHasPages(keyword: string): Promise<boolean> {
  const latestPage = await getKeywordLatestPage(keyword);

  // Never searched - has pages
  if (!latestPage) return true;

  // Has next_page cursor - still has pages
  return latestPage.nextPage !== null;
}
