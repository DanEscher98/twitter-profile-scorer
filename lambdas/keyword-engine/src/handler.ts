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

  // Test DB connection
  try {
    const db = getDb();
    const result = await db.execute(sql`SELECT NOW() as time`);
    const profileCount = await db
      .select({ count: sql<number>`count(*)` })
      .from(userProfiles);

    console.log("DB connection successful");

    return {
      statusCode: 200,
      body: JSON.stringify({
        status: "healthy",
        secrets: secretsStatus,
        database: {
          connected: true,
          serverTime: result.rows[0]?.time,
          profileCount: profileCount[0]?.count ?? 0,
        },
      }),
    };
  } catch (error) {
    console.error("DB connection failed:", error);

    return {
      statusCode: 500,
      body: JSON.stringify({
        status: "unhealthy",
        secrets: secretsStatus,
        database: {
          connected: false,
          error: error instanceof Error ? error.message : "Unknown error",
        },
      }),
    };
  }
};
