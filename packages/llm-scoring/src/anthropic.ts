import { ChatAnthropic } from "@langchain/anthropic";

import { ProfileToScore } from "@profile-scorer/db";
import { createLogger } from "@profile-scorer/utils";

import {
  AudienceConfig,
  LabelResult,
  formatProfilesPrompt,
  generateSystemPrompt,
  parseAndValidateResponse,
} from "./shared";

const log = createLogger("anthropic-wrapper");

/**
 * Check if error is a rate limit error (429).
 */
function isRateLimitError(error: unknown): boolean {
  const err = error as any;
  return err?.status === 429 || err?.code === 429 || err?.message?.includes("429");
}

/**
 * Label profiles using Anthropic Claude via LangChain.
 *
 * @param profiles - Array of profiles to label
 * @param model - Model identifier (e.g., "claude-sonnet-4-20250514", "claude-haiku-4-5-20251001")
 * @param audienceConfig - Audience configuration for generating system prompt
 * @param apiKey - Optional API key (defaults to ANTHROPIC_API_KEY env var)
 * @returns Array of label results (empty array on error)
 */
export async function labelWithAnthropic(
  profiles: ProfileToScore[],
  model: string,
  audienceConfig: AudienceConfig,
  apiKey?: string
): Promise<LabelResult[]> {
  const key = apiKey ?? process.env.ANTHROPIC_API_KEY ?? "";
  if (!key) {
    log.error("ANTHROPIC_API_KEY environment variable is required");
    return [];
  }

  const systemPrompt = generateSystemPrompt(audienceConfig);
  const userPrompt = formatProfilesPrompt(profiles);

  log.info("Sending profiles to Anthropic", { model, profileCount: profiles.length });

  let text: string;
  try {
    const chat = new ChatAnthropic({
      model,
      apiKey: key,
      maxTokens: 4096,
    });

    const response = await chat.invoke([
      { role: "system", content: systemPrompt },
      { role: "user", content: userPrompt },
    ]);

    text = typeof response.content === "string" ? response.content : JSON.stringify(response.content);
  } catch (error: unknown) {
    const err = error as any;

    if (isRateLimitError(error)) {
      log.error("ANTHROPIC RATE LIMIT (429)", {
        model,
        message: err.message,
        action: "WAIT_AND_RETRY",
        profileCount: profiles.length,
      });
      return [];
    }

    log.error("Anthropic API error", {
      model,
      status: err.status,
      message: err.message,
      profileCount: profiles.length,
    });
    return [];
  }

  log.info("Received response from Anthropic", { model, responseLength: text.length });

  return parseAndValidateResponse(text, profiles);
}
