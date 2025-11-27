import { InvokeCommand, LambdaClient } from "@aws-sdk/client-lambda";
import { SendMessageCommand, SQSClient } from "@aws-sdk/client-sqs";
import { ScheduledHandler } from "aws-lambda";
import { sql } from "drizzle-orm";

import { getDb, profilesToScore } from "@profile-scorer/db";

const lambda = new LambdaClient({});
const sqs = new SQSClient({});

// Environment variables (set by Pulumi)
const KEYWORD_ENGINE_ARN = process.env.KEYWORD_ENGINE_ARN ?? "";
const KEYWORDS_QUEUE_URL = process.env.KEYWORDS_QUEUE_URL ?? "";
const SCORING_QUEUE_URL = process.env.SCORING_QUEUE_URL ?? "";

interface KeywordEngineResponse {
  keywords: string[];
  stats?: {
    totalSearches: number;
    keywordYields: Record<string, number>;
  };
}

export const handler: ScheduledHandler = async (event) => {
  console.log("[orchestrator] Starting pipeline orchestration");
  console.log("[orchestrator] Event:", JSON.stringify(event));

  const results = {
    keywordsQueued: 0,
    scoringJobsQueued: 0,
    errors: [] as string[],
  };

  // Step 1: Get keywords from keyword-engine
  try {
    console.log("[orchestrator] Invoking keyword-engine...");

    const invokeResponse = await lambda.send(
      new InvokeCommand({
        FunctionName: KEYWORD_ENGINE_ARN,
        InvocationType: "RequestResponse",
        Payload: JSON.stringify({ action: "get_keywords", count: 5 }),
      })
    );

    const payloadStr = invokeResponse.Payload
      ? new TextDecoder().decode(invokeResponse.Payload)
      : "{}";
    const keywordResponse: KeywordEngineResponse = JSON.parse(payloadStr);

    console.log("[orchestrator] Received keywords:", keywordResponse.keywords);

    // Step 2: Queue keywords for query-twitter-api
    for (const keyword of keywordResponse.keywords) {
      try {
        await sqs.send(
          new SendMessageCommand({
            QueueUrl: KEYWORDS_QUEUE_URL,
            MessageBody: JSON.stringify({ keyword, items: 20 }),
          })
        );
        results.keywordsQueued++;
        console.log(`[orchestrator] Queued keyword: ${keyword}`);
      } catch (err) {
        const msg = `Failed to queue keyword ${keyword}: ${err}`;
        console.error(`[orchestrator] ${msg}`);
        results.errors.push(msg);
      }
    }
  } catch (error) {
    const msg = `Failed to invoke keyword-engine: ${error}`;
    console.error(`[orchestrator] ${msg}`);
    results.errors.push(msg);
  }

  // Step 3: Check if there are profiles to score
  try {
    const db = getDb();
    const pendingCount = await db
      .select({ count: sql<number>`count(*)` })
      .from(profilesToScore);

    const count = Number(pendingCount[0]?.count ?? 0);
    console.log(`[orchestrator] Profiles pending scoring: ${count}`);

    if (count > 0) {
      // Queue scoring jobs for each model
      const models = ["claude-haiku-20240307", "gemini-2.0-flash"];

      for (const model of models) {
        try {
          await sqs.send(
            new SendMessageCommand({
              QueueUrl: SCORING_QUEUE_URL,
              MessageBody: JSON.stringify({ model, batchSize: 25 }),
            })
          );
          results.scoringJobsQueued++;
          console.log(`[orchestrator] Queued scoring job for model: ${model}`);
        } catch (err) {
          const msg = `Failed to queue scoring job for ${model}: ${err}`;
          console.error(`[orchestrator] ${msg}`);
          results.errors.push(msg);
        }
      }
    }
  } catch (error) {
    const msg = `Failed to check profiles_to_score: ${error}`;
    console.error(`[orchestrator] ${msg}`);
    results.errors.push(msg);
  }

  console.log("[orchestrator] Completed:", results);

  return results;
};
