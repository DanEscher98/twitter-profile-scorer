import { TwitterXapiUser, TwitterXapiMetadata } from "@profile-scorer/db";
import { sleep } from "./utils"
import crypto from 'crypto';
import { v4 as uuidv4 } from 'uuid';
import logger from "./logger";

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
  if (!apiKey) throw new Error("TWITTERX_APIKEY not set");

  const url = new URL(`https://${RAPIDAPI_HOST}/api/search/people`);
  url.searchParams.set("keyword", keyword);
  url.searchParams.set("count", items.toString());
  if (cursor) url.searchParams.set("cursor", cursor);

  let retries = 0;
  let response: Response | null = null;

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

      if (response.ok) {
        logger.debug("API request successful", { keyword, retries });
        break;
      }
      logger.warn("API request failed, retrying", {
        keyword,
        retries,
        status: response.status,
        statusText: response.statusText
      });
    } catch (e: any) {
      logger.warn("Network error, retrying", { keyword, retries, error: e.message });
    }

    if (retries < MAX_RETRIES) await sleep(RETRY_DELAY_MS);
  }

  if (!response?.ok) {
    logger.error("xapiSearch failed after max retries", { keyword, retries: MAX_RETRIES });
    throw new Error(`xapiSearch failed after ${MAX_RETRIES} attempts`);
  }

  const json_response: any = await response.json();
  const users: Array<TwitterXapiUser> = json_response.data;

  const idsString = users.map((p) => p.legacy.screen_name).join(',');
  const ids_hash = crypto
    .createHash('md5')
    .update(idsString)
    .digest('hex')
    .substring(0, 16);

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
    hasNextPage: !!metadata.next_page
  });

  return { users, metadata };
}
