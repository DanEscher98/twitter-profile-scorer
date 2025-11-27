import { GoogleGenerativeAI } from "@google/generative-ai";
import { ProfileToScore } from "@profile-scorer/db";
import { ScoreResult } from "../handler";
import { SYSTEM_PROMPT, formatProfilesPrompt, parseAndValidateResponse } from "./shared";

const GEMINI_API_KEY = process.env.GEMINI_API_KEY ?? "";

// Simple logger for wrapper (JSON structured logs)
const log = {
  info: (msg: string, meta?: object) =>
    console.log(JSON.stringify({ level: "info", service: "gemini-wrapper", message: msg, ...meta })),
  warn: (msg: string, meta?: object) =>
    console.warn(JSON.stringify({ level: "warn", service: "gemini-wrapper", message: msg, ...meta })),
  error: (msg: string, meta?: object) =>
    console.error(JSON.stringify({ level: "error", service: "gemini-wrapper", message: msg, ...meta })),
};

/**
 * Score profiles using Google Gemini.
 *
 * @param profiles - Array of profiles to score
 * @param modelName - Model identifier (e.g., "gemini-2.0-flash", "gemini-1.5-flash")
 * @returns Array of score results (empty array on error)
 */
export async function scoreWithGemini(
  profiles: ProfileToScore[],
  modelName: string
): Promise<ScoreResult[]> {
  if (!GEMINI_API_KEY) {
    log.error("GEMINI_API_KEY environment variable is required");
    return [];
  }

  log.info("Sending profiles to Gemini", { model: modelName, profileCount: profiles.length });

  let text: string;
  try {
    const genAI = new GoogleGenerativeAI(GEMINI_API_KEY);
    const model = genAI.getGenerativeModel({
      model: modelName,
      systemInstruction: SYSTEM_PROMPT,
    });

    const prompt = formatProfilesPrompt(profiles);
    const result = await model.generateContent(prompt);
    const response = result.response;
    text = response.text();
  } catch (error: any) {
    // Handle API errors (invalid model name, rate limits, quota exceeded, etc.)
    const errorMessage = error.message || "Unknown error";
    const errorStatus = error.status || error.statusCode || "unknown";

    // Detect quota/billing issues (Google uses 429 for rate limits, 403 for quota)
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
        model: modelName,
        statusCode: errorStatus,
        errorMessage,
        action: "CHECK_BILLING_OR_WAIT",
        profileCount: profiles.length,
      });
    } else {
      log.error("Gemini API error", {
        model: modelName,
        statusCode: errorStatus,
        errorMessage,
        profileCount: profiles.length,
      });
    }

    // Return empty array instead of throwing - allows other models to continue
    return [];
  }

  log.info("Received response from Gemini", { model: modelName, responseLength: text.length });

  // Parse and validate using shared module (handles ```json blocks and Zod validation)
  const validated = parseAndValidateResponse(text, profiles);

  // Map to ScoreResult format
  return validated.map((v) => ({
    twitterId: v.twitterId,
    score: v.score,
    reason: v.reason,
  }));
}
