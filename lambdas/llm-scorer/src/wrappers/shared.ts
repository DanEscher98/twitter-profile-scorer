import { z } from "zod";
import { encode as toToon } from "@toon-format/toon";
import { ProfileToScore } from "@profile-scorer/db";
import { createLogger } from "@profile-scorer/utils";

const log = createLogger("llm-shared");

/**
 * Zod schema for a single score result from LLM.
 */
const ScoreItemSchema = z.object({
  username: z.string(),
  reason: z.string(),
  score: z.number().min(0).max(1),
});

/**
 * Zod schema for the full LLM response array.
 */
const ScoreResponseSchema = z.array(ScoreItemSchema);

/**
 * Type inferred from the Zod schema.
 */
export type ParsedScoreItem = z.infer<typeof ScoreItemSchema>;

/**
 * System prompt for scoring Twitter profiles.
 * Instructs the model to evaluate research relevance.
 */
export const SYSTEM_PROMPT = `You are an expert at evaluating Twitter profiles to identify qualitative researchers in academia.

For each profile, you will:
1. Analyze the username, display name, bio, and inferred category
2. Determine how likely they are to be a qualitative researcher
3. Assign a score from 0.0 to 1.0 where:
   - 0.0-0.3: Not a researcher (bot, brand, random person)
   - 0.3-0.5: Unlikely researcher (may work in adjacent field)
   - 0.5-0.7: Possible researcher (some signals but unclear)
   - 0.7-0.9: Likely researcher (clear academic/research signals)
   - 0.9-1.0: Definite researcher (explicit qualitative research mention)

You MUST respond with valid JSON only. No markdown, no code blocks, no additional text.
Return a JSON array with objects containing: username, reason, score.`;

/**
 * Format profiles into a TOON prompt for the LLM.
 *
 * @param profiles - Array of profiles to score
 * @returns Formatted prompt string with TOON representation
 */
export function formatProfilesPrompt(profiles: ProfileToScore[]): string {
  // Transform to TOON-friendly format
  const profilesData = profiles.map((p) => ({
    username: p.username,
    display_name: p.displayName,
    bio: p.bio,
    likely_is: p.likelyIs,
    category: p.category,
  }));

  const toonData = toToon(profilesData);

  return `Score the following ${profiles.length} Twitter profiles for research relevance.

${toonData}

Respond with a JSON array. Each object must have:
- username: string (the profile's username)
- score: number (0.0 to 1.0)
- reason: string (brief explanation, max 100 chars)

Example response format:
[{"username": "researcher_jane", "score": 0.85, "reason": "PhD candidate in sociology, mentions ethnography"}, {"username": "marketing_co", "score": 0.2, "reason": "Marketing account, no research signals"}]

IMPORTANT: Return ONLY the JSON array. No markdown formatting, no code blocks.`;
}

/**
 * Extract JSON from LLM response, handling markdown code blocks.
 *
 * @param text - Raw LLM response text
 * @returns Cleaned JSON string
 */
function extractJson(text: string): string {
  let jsonStr = text.trim();

  // Handle ```json ... ``` blocks (common with Haiku)
  if (jsonStr.startsWith("```json")) {
    jsonStr = jsonStr.slice(7);
  } else if (jsonStr.startsWith("```")) {
    jsonStr = jsonStr.slice(3);
  }

  if (jsonStr.endsWith("```")) {
    jsonStr = jsonStr.slice(0, -3);
  }

  return jsonStr.trim();
}

/**
 * Parse and validate LLM response using Zod schema.
 *
 * @param text - Raw LLM response text
 * @param profiles - Original profiles for mapping back twitterId
 * @returns Array of validated score results with twitterId
 */
export function parseAndValidateResponse(
  text: string,
  profiles: ProfileToScore[]
): { twitterId: string; username: string; score: number; reason: string }[] {
  // Extract JSON from potential markdown blocks
  const jsonStr = extractJson(text);

  // Parse JSON
  let parsed: unknown;
  try {
    parsed = JSON.parse(jsonStr);
  } catch (error: any) {
    log.error("Failed to parse JSON from LLM response", {
      error: error.message,
      responsePreview: text.slice(0, 300),
    });
    return [];
  }

  // Validate with Zod
  const validation = ScoreResponseSchema.safeParse(parsed);
  if (!validation.success) {
    log.error("Zod validation failed for LLM response", {
      errors: validation.error.errors.map((e) => ({
        path: e.path.join("."),
        message: e.message,
      })),
      responsePreview: text.slice(0, 300),
    });
    return [];
  }

  // Map usernames back to twitterIds
  const usernameToProfile = new Map(
    profiles.map((p) => [p.username.toLowerCase(), p])
  );

  const results: { twitterId: string; username: string; score: number; reason: string }[] = [];

  for (const item of validation.data) {
    const profile = usernameToProfile.get(item.username.toLowerCase());
    if (profile) {
      results.push({
        twitterId: profile.twitterId,
        username: item.username,
        score: Math.max(0, Math.min(1, item.score)),
        reason: item.reason.slice(0, 500),
      });
    } else {
      log.warn("Username from LLM response not found in profiles", {
        username: item.username,
      });
    }
  }

  log.info("Parsed and validated LLM response", {
    inputCount: profiles.length,
    outputCount: results.length,
    missingCount: profiles.length - results.length,
  });

  return results;
}
