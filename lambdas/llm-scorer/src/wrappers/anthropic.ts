import Anthropic from "@anthropic-ai/sdk";
import { ProfileToScore } from "@profile-scorer/db";
import { ScoreResult } from "../handler";
import { SYSTEM_PROMPT, formatProfilesPrompt, parseAndValidateResponse } from "./shared";

const ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY ?? "";

// Simple logger for wrapper (JSON structured logs)
const log = {
  info: (msg: string, meta?: object) =>
    console.log(JSON.stringify({ level: "info", service: "anthropic-wrapper", message: msg, ...meta })),
  warn: (msg: string, meta?: object) =>
    console.warn(JSON.stringify({ level: "warn", service: "anthropic-wrapper", message: msg, ...meta })),
  error: (msg: string, meta?: object) =>
    console.error(JSON.stringify({ level: "error", service: "anthropic-wrapper", message: msg, ...meta })),
};

/**
 * Score profiles using Anthropic Claude.
 *
 * @param profiles - Array of profiles to score
 * @param model - Model identifier (e.g., "claude-sonnet-4-20250514", "claude-3-haiku-20240307")
 * @returns Array of score results (empty array on error)
 */
export async function scoreWithAnthropic(
  profiles: ProfileToScore[],
  model: string
): Promise<ScoreResult[]> {
  if (!ANTHROPIC_API_KEY) {
    log.error("ANTHROPIC_API_KEY environment variable is required");
    return [];
  }

  const client = new Anthropic({
    apiKey: ANTHROPIC_API_KEY,
  });

  const prompt = formatProfilesPrompt(profiles);

  log.info("Sending profiles to Anthropic", { model, profileCount: profiles.length });

  let message;
  try {
    message = await client.messages.create({
      model: model,
      max_tokens: 4096,
      messages: [
        {
          role: "user",
          content: prompt,
        },
      ],
      system: SYSTEM_PROMPT,
    });
  } catch (error: any) {
    // Handle API errors (invalid model name, rate limits, quota, etc.)
    const statusCode = error.status || error.statusCode || "unknown";
    const errorType = error.error?.error?.type || error.type || "unknown";
    const errorMessage = error.error?.error?.message || error.message || "Unknown error";

    // Detect quota/billing issues
    const isQuotaError =
      statusCode === 429 ||
      errorType === "rate_limit_error" ||
      errorType === "insufficient_quota" ||
      errorType === "billing_hard_limit_reached" ||
      errorMessage.toLowerCase().includes("quota") ||
      errorMessage.toLowerCase().includes("rate limit") ||
      errorMessage.toLowerCase().includes("billing") ||
      errorMessage.toLowerCase().includes("credit");

    if (isQuotaError) {
      log.error("ANTHROPIC QUOTA/RATE LIMIT - Purchase more credits or wait", {
        model,
        statusCode,
        errorType,
        errorMessage,
        action: "PURCHASE_TOKENS_OR_WAIT",
        profileCount: profiles.length,
      });
    } else {
      log.error("Anthropic API error", {
        model,
        statusCode,
        errorType,
        errorMessage,
        profileCount: profiles.length,
      });
    }

    // Return empty array instead of throwing - allows other models to continue
    return [];
  }

  // Extract text content from response
  const textContent = message.content.find((c) => c.type === "text");
  if (!textContent || textContent.type !== "text") {
    log.error("No text content in Anthropic response", { model });
    return [];
  }

  log.info("Received response from Anthropic", { model, responseLength: textContent.text.length });

  // Parse and validate using shared module (handles ```json blocks and Zod validation)
  const validated = parseAndValidateResponse(textContent.text, profiles);

  // Map to ScoreResult format
  return validated.map((v) => ({
    twitterId: v.twitterId,
    score: v.score,
    reason: v.reason,
  }));
}
