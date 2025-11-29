import crypto from "crypto";
import { v4 as uuidv4 } from "uuid";

import { TwitterXapiMetadata, TwitterXapiUser } from "@profile-scorer/db";

import { TwitterXApiError } from "./errors";
import logger from "./logger";
import { sleep } from "./utils";

const RAPIDAPI_HOST = "twitter-x-api.p.rapidapi.com";
const MAX_RETRIES = 10;
const RETRY_DELAY_MS = 10_000;

export async function xapiSearch(
  keyword: string,
  items: number = 20,
  cursor: string | null = null,
  page: number = 0
): Promise<{ users: TwitterXapiUser[]; metadata: TwitterXapiMetadata }> {
  const apiKey = process.env.TWITTERX_APIKEY;
  if (!apiKey) {
    logger.error("API key not configured", { function: "xapiSearch" });
    throw new TwitterXApiError("API_KEY_MISSING", "TWITTERX_APIKEY not set");
  }

  const url = new URL(`https://${RAPIDAPI_HOST}/api/search/people`);
  url.searchParams.set("keyword", keyword);
  url.searchParams.set("count", items.toString());
  if (cursor) url.searchParams.set("cursor", cursor);

  let retries = 0;
  let response: Response | null = null;
  let lastStatus: number | null = null;

  logger.info("Starting xapiSearch", { keyword, items, cursor, page });

  while (retries < MAX_RETRIES) {
    retries++;
    try {
      response = await fetch(url.toString(), {
        method: "GET",
        headers: {
          "x-rapidapi-host": RAPIDAPI_HOST,
          "x-rapidapi-key": apiKey,
        },
      });

      lastStatus = response.status;

      if (response.ok) {
        logger.debug("API request successful", { keyword, retries });
        break;
      }

      // Check for rate limit
      if (response.status === 429) {
        logger.error("Rate limit exceeded", { keyword, retries, status: 429 });
        throw new TwitterXApiError("RATE_LIMITED", "API rate limit exceeded", { keyword, retries });
      }

      logger.warn("API request failed, retrying", {
        keyword,
        retries,
        status: response.status,
        statusText: response.statusText,
      });
    } catch (e: any) {
      // Re-throw TwitterXApiError
      if (e instanceof TwitterXApiError) throw e;

      logger.warn("Network error, retrying", { keyword, retries, error: e.message });
    }

    if (retries < MAX_RETRIES) await sleep(RETRY_DELAY_MS);
  }

  if (!response?.ok) {
    logger.error("xapiSearch failed after max retries", {
      keyword,
      retries: MAX_RETRIES,
      lastStatus,
    });
    throw new TwitterXApiError(
      "MAX_RETRIES_EXCEEDED",
      `xapiSearch failed after ${MAX_RETRIES} attempts`,
      { keyword, lastStatus }
    );
  }

  const json_response: any = await response.json();
  const users: Array<TwitterXapiUser> = json_response.data;

  const idsString = users.map((p) => p.legacy.screen_name).join(",");
  const ids_hash = crypto.createHash("md5").update(idsString).digest("hex").substring(0, 16);

  const metadata: TwitterXapiMetadata = {
    id: uuidv4(),
    ids_hash,
    keyword,
    items,
    retries,
    next_page: json_response.cursor ?? null,
    page,
  };

  logger.info("xapiSearch completed", {
    keyword,
    usersFound: users.length,
    page,
    hasNextPage: !!metadata.next_page,
  });

  return { users, metadata };
}

/**
 * Fetch a single user by username from RapidAPI TwitterX.
 *
 * @param username - Twitter handle (without @)
 * @returns Raw API user object
 * @throws TwitterXApiError with appropriate error code
 */
export async function xapiGetUser(username: string): Promise<TwitterXapiUser> {
  const apiKey = process.env.TWITTERX_APIKEY;
  if (!apiKey) {
    logger.error("API key not configured", { function: "xapiGetUser" });
    throw new TwitterXApiError("API_KEY_MISSING", "TWITTERX_APIKEY not set");
  }

  const url = `https://${RAPIDAPI_HOST}/api/user/detail?username=${username}`;

  let retries = 0;
  let lastStatus: number | null = null;

  logger.info("Starting xapiGetUser", { username });

  while (retries < MAX_RETRIES) {
    retries++;

    try {
      const response = await fetch(url, {
        method: "GET",
        headers: {
          "x-rapidapi-host": RAPIDAPI_HOST,
          "x-rapidapi-key": apiKey,
        },
      });

      lastStatus = response.status;

      // Check for rate limit
      if (response.status === 429) {
        logger.error("Rate limit exceeded", { username, retries, status: 429 });
        throw new TwitterXApiError("RATE_LIMITED", "API rate limit exceeded", {
          username,
          retries,
        });
      }

      if (!response.ok) {
        logger.warn("API request failed, retrying", {
          username,
          retries,
          status: response.status,
          statusText: response.statusText,
        });
        if (retries < MAX_RETRIES) {
          await sleep(RETRY_DELAY_MS);
          continue;
        }
        throw new TwitterXApiError("NETWORK_ERROR", `API request failed: ${response.status}`, {
          username,
          status: response.status,
        });
      }

      const data: any = await response.json();

      // User doesn't exist - empty response
      if (!data || Object.keys(data).length === 0) {
        logger.warn("User not found", { username });
        throw new TwitterXApiError("USER_NOT_FOUND", `User "${username}" not found`, { username });
      }

      // Bottleneck - has user key but empty object
      const user = data.user ?? data;
      if (!user || Object.keys(user).length === 0) {
        logger.warn("API bottleneck, retrying", { username, retries });
        if (retries < MAX_RETRIES) {
          await sleep(RETRY_DELAY_MS);
          continue;
        }
        throw new TwitterXApiError(
          "API_BOTTLENECK",
          `API bottleneck after ${MAX_RETRIES} attempts`,
          { username, retries }
        );
      }

      logger.info("xapiGetUser completed", { username, retries });
      return user.result as TwitterXapiUser;
    } catch (e: any) {
      // Re-throw TwitterXApiError immediately
      if (e instanceof TwitterXApiError) throw e;

      logger.warn("Network error, retrying", { username, retries, error: e.message });
      if (retries >= MAX_RETRIES) {
        throw new TwitterXApiError("NETWORK_ERROR", e.message, { username, retries });
      }
      await sleep(RETRY_DELAY_MS);
    }
  }

  logger.error("xapiGetUser failed after max retries", {
    username,
    retries: MAX_RETRIES,
    lastStatus,
  });
  throw new TwitterXApiError("MAX_RETRIES_EXCEEDED", `Failed after ${MAX_RETRIES} attempts`, {
    username,
    lastStatus,
  });
}
