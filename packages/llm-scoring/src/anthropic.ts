import Anthropic from "@anthropic-ai/sdk";
import { ProfileToScore } from "@profile-scorer/db";
import { createLogger } from "@profile-scorer/utils";
import { ScoreResult, SYSTEM_PROMPT, formatProfilesPrompt, parseAndValidateResponse } from "./shared";

const log = createLogger("anthropic-wrapper");

/**
 * Score profiles using Anthropic Claude.
 *
 * @param profiles - Array of profiles to score
 * @param model - Model identifier (e.g., "claude-sonnet-4-20250514", "claude-haiku-4-5-20251001")
 * @param apiKey - Optional API key (defaults to ANTHROPIC_API_KEY env var)
 * @returns Array of score results (empty array on error)
 */
export async function scoreWithAnthropic(
  profiles: ProfileToScore[],
  model: string,
  apiKey?: string
): Promise<ScoreResult[]> {
  const key = apiKey ?? process.env.ANTHROPIC_API_KEY ?? "";
  if (!key) {
    log.error("ANTHROPIC_API_KEY environment variable is required");
    return [];
  }

  const client = new Anthropic({ apiKey: key });
  const prompt = formatProfilesPrompt(profiles);

  log.info("Sending profiles to Anthropic", { model, profileCount: profiles.length });

  let message;
  try {
    message = await client.messages.create({
      model: model,
      max_tokens: 4096,
      messages: [{ role: "user", content: prompt }],
      system: SYSTEM_PROMPT,
    });
  } catch (error: any) {
    const statusCode = error.status || error.statusCode || "unknown";
    const errorType = error.error?.error?.type || error.type || "unknown";
    const errorMessage = error.error?.error?.message || error.message || "Unknown error";

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
        model, statusCode, errorType, errorMessage,
        action: "PURCHASE_TOKENS_OR_WAIT",
        profileCount: profiles.length,
      });
    } else {
      log.error("Anthropic API error", {
        model, statusCode, errorType, errorMessage,
        profileCount: profiles.length,
      });
    }
    return [];
  }

  const textContent = message.content.find((c) => c.type === "text");
  if (!textContent || textContent.type !== "text") {
    log.error("No text content in Anthropic response", { model });
    return [];
  }

  log.info("Received response from Anthropic", { model, responseLength: textContent.text.length });

  return parseAndValidateResponse(textContent.text, profiles);
}
