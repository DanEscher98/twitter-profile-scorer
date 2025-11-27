import { Handler } from "aws-lambda";
import { getDb, userProfiles } from "@profile-scorer/db";
import { sql } from "drizzle-orm";

export const handler: Handler = async () => {
  const twitterxApiKey = process.env.TWITTERX_APIKEY;
  const anthropicApiKey = process.env.ANTHROPIC_APIKEY;

  // Check secrets access
  const secretsStatus = {
    twitterx: twitterxApiKey ? "✓ accessible" : "✗ missing",
    anthropic: anthropicApiKey ? "✓ accessible" : "✗ missing",
  };

  console.log("Secrets status:", JSON.stringify(secretsStatus));

  // Test external API (httpbin - simple echo service)
  let externalApiResult: { success: boolean; data?: unknown; error?: string };
  try {
    const response = await fetch("https://httpbin.org/get?test=lambda");
    const data = await response.json();
    externalApiResult = { success: true, data };
    console.log("External API call successful");
  } catch (error) {
    externalApiResult = {
      success: false,
      error: error instanceof Error ? error.message : "Unknown error",
    };
    console.error("External API call failed:", error);
  }

  // Test DB connection
  let dbResult: {
    connected: boolean;
    serverTime?: unknown;
    profileCount?: number;
    error?: string;
  };
  try {
    const db = getDb();
    const result = await db.execute(sql`SELECT NOW() as time`);
    const profileCount = await db
      .select({ count: sql<number>`count(*)` })
      .from(userProfiles);

    dbResult = {
      connected: true,
      serverTime: result.rows[0]?.time,
      profileCount: profileCount[0]?.count ?? 0,
    };
    console.log("DB connection successful");
  } catch (error) {
    dbResult = {
      connected: false,
      error: error instanceof Error ? error.message : "Unknown error",
    };
    console.error("DB connection failed:", error);
  }

  const allHealthy = externalApiResult.success && dbResult.connected;

  return {
    statusCode: allHealthy ? 200 : 500,
    body: JSON.stringify({
      status: allHealthy ? "healthy" : "unhealthy",
      secrets: secretsStatus,
      externalApi: externalApiResult,
      database: dbResult,
    }),
  };
};
