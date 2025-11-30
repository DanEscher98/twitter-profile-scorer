import { ChatGroq } from "@langchain/groq";

import { ProfileToScore } from "@profile-scorer/db";
import { createLogger } from "@profile-scorer/utils";

import {
  AudienceConfig,
  LabelResult,
  formatProfilesPrompt,
  generateSystemPrompt,
  parseAndValidateResponse,
} from "./shared";

const log = createLogger("groq-wrapper");

/**
 * Check if error is a rate limit error (429).
 */
function isRateLimitError(error: unknown): boolean {
  const err = error as any;
  return err?.status === 429 || err?.code === 429 || err?.message?.includes("429");
}

/**
 * Label profiles using Groq via LangChain.
 *
 * @param profiles - Array of profiles to label
 * @param modelName - Model identifier (e.g., "meta-llama/llama-4-maverick-17b-128e-instruct")
 * @param audienceConfig - Audience configuration for generating system prompt
 * @param apiKey - Optional API key (defaults to GROQ_API_KEY env var)
 * @returns Array of label results (empty array on error)
 */
export async function labelWithGroq(
  profiles: ProfileToScore[],
  modelName: string,
  audienceConfig: AudienceConfig,
  apiKey?: string
): Promise<LabelResult[]> {
  const key = apiKey ?? process.env.GROQ_API_KEY ?? "";
  if (!key) {
    log.error("GROQ_API_KEY environment variable is required");
    return [];
  }

  const systemPrompt = generateSystemPrompt(audienceConfig);
  const userPrompt = formatProfilesPrompt(profiles);

  log.info("Sending profiles to Groq", { model: modelName, profileCount: profiles.length });

  let text: string;
  try {
    const chat = new ChatGroq({
      model: modelName,
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
      log.error("GROQ RATE LIMIT (429)", {
        model: modelName,
        message: err.message,
        action: "WAIT_AND_RETRY",
        profileCount: profiles.length,
      });
      return [];
    }

    log.error("Groq API error", {
      model: modelName,
      status: err.status,
      message: err.message,
      profileCount: profiles.length,
    });
    return [];
  }

  log.info("Received response from Groq", { model: modelName, responseLength: text.length });

  return parseAndValidateResponse(text, profiles);
}
