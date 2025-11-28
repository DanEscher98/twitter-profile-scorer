import { GoogleGenerativeAI } from "@google/generative-ai";
import { ProfileToScore } from "@profile-scorer/db";
import { createLogger } from "@profile-scorer/utils";
import { ScoreResult, SYSTEM_PROMPT, formatProfilesPrompt, parseAndValidateResponse } from "./shared";

const log = createLogger("gemini-wrapper");

/**
 * Score profiles using Google Gemini.
 *
 * @param profiles - Array of profiles to score
 * @param modelName - Model identifier (e.g., "gemini-2.0-flash", "gemini-1.5-flash")
 * @param apiKey - Optional API key (defaults to GEMINI_API_KEY env var)
 * @returns Array of score results (empty array on error)
 */
export async function scoreWithGemini(
  profiles: ProfileToScore[],
  modelName: string,
  apiKey?: string
): Promise<ScoreResult[]> {
  const key = apiKey ?? process.env.GEMINI_API_KEY ?? "";
  if (!key) {
    log.error("GEMINI_API_KEY environment variable is required");
    return [];
  }

  log.info("Sending profiles to Gemini", { model: modelName, profileCount: profiles.length });

  let text: string;
  try {
    const genAI = new GoogleGenerativeAI(key);
    const model = genAI.getGenerativeModel({
      model: modelName,
      systemInstruction: SYSTEM_PROMPT,
    });

    const prompt = formatProfilesPrompt(profiles);
    const result = await model.generateContent(prompt);
    const response = result.response;
    text = response.text();
  } catch (error: any) {
    const errorMessage = error.message || "Unknown error";
    const errorStatus = error.status || error.statusCode || "unknown";

    const isQuotaError =
      errorStatus === 429 ||
      errorStatus === 403 ||
      errorMessage.toLowerCase().includes("quota") ||
      errorMessage.toLowerCase().includes("rate limit") ||
      errorMessage.toLowerCase().includes("resource exhausted") ||
      errorMessage.toLowerCase().includes("billing") ||
      errorMessage.toLowerCase().includes("exceeded");

    if (isQuotaError) {
      log.error("GEMINI QUOTA/RATE LIMIT - Check billing or wait", {
        model: modelName, statusCode: errorStatus, errorMessage,
        action: "CHECK_BILLING_OR_WAIT",
        profileCount: profiles.length,
      });
    } else {
      log.error("Gemini API error", {
        model: modelName, statusCode: errorStatus, errorMessage,
        profileCount: profiles.length,
      });
    }
    return [];
  }

  log.info("Received response from Gemini", { model: modelName, responseLength: text.length });

  return parseAndValidateResponse(text, profiles);
}
