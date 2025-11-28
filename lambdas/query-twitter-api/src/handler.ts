import { SQSHandler, SQSBatchResponse, SQSBatchItemFailure } from "aws-lambda";
import { wrappers } from "@profile-scorer/twitterx-api";
import { createLogger } from "@profile-scorer/utils";

const log = createLogger("query-twitter-api");

interface KeywordMessage {
  keyword: string;
}

export const handler: SQSHandler = async (event): Promise<SQSBatchResponse> => {
  const batchItemFailures: SQSBatchItemFailure[] = [];

  log.info("Lambda invoked", { recordCount: event.Records.length });

  for (const record of event.Records) {
    try {
      const message: KeywordMessage = JSON.parse(record.body);
      const { keyword } = message;

      if (!keyword) {
        log.error("Missing keyword in message", { messageId: record.messageId });
        batchItemFailures.push({ itemIdentifier: record.messageId });
        continue;
      }

      log.info("Processing keyword", { keyword, messageId: record.messageId });

      const result = await wrappers.processKeyword(keyword);

      log.info("Keyword processed successfully", {
        keyword,
        newProfiles: result.newProfiles,
        humanProfiles: result.humanProfiles,
      });
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "Unknown error";
      const errorStack = error instanceof Error ? error.stack : undefined;
      const errorCause = error instanceof Error && 'cause' in error ? (error.cause as Error)?.message : undefined;
      const errorCode = error instanceof Error && 'code' in error ? (error as any).code : undefined;

      // Check if it's a "fully paginated" error - don't retry these
      if (errorMessage.includes("fully paginated")) {
        log.warn("Keyword fully paginated, skipping", { messageId: record.messageId });
        continue;
      }

      log.error("Failed to process record", {
        messageId: record.messageId,
        error: errorMessage,
        cause: errorCause,
        code: errorCode,
        stack: errorStack
      });

      batchItemFailures.push({ itemIdentifier: record.messageId });
    }
  }

  log.info("Lambda completed", {
    processed: event.Records.length,
    failed: batchItemFailures.length
  });

  return { batchItemFailures };
};
