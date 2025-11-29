import { encode as toToon } from "@toon-format/toon";
import { z } from "zod";

import { ProfileToScore } from "@profile-scorer/db";
import { createLogger } from "@profile-scorer/utils";

const log = createLogger("llm-shared");

/**
 * Score result from LLM wrapper.
 */
export interface ScoreResult {
  twitterId: string;
  username: string;
  score: number;
  reason: string;
}

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
 * Configuration for audience-specific scoring prompts.
 */
export interface AudienceConfig {
  targetProfile: string;
  sector: "academia" | "industry" | "government" | "ngo" | "healthcare" | "custom";
  highSignals: string[];
  lowSignals: string[];
  domainContext: string;
  scoringOverrides?: {
    defaultFloor?: number;
    roleBoosts?: Record<string, number>;
  };
}

/**
 * Generate a system prompt based on audience configuration.
 *
 * @param config - Audience configuration with target profile and signals
 * @returns System prompt string for LLM
 */
export function generateSystemPrompt(config: AudienceConfig): string {
  return `ROLE: You are an expert at evaluating social media profiles to identify ${config.targetProfile}s in ${config.sector.toUpperCase()}.

## Domain Context
${config.domainContext}

## Scoring Signals

HIGH-SIGNAL INDICATORS (increase score):
${config.highSignals.map((s) => `• ${s}`).join("\n")}

LOW-SIGNAL INDICATORS (decrease score or neutral):
${config.lowSignals.map((s) => `• ${s}`).join("\n")}

## Evaluation Process
For each profile:
1. Analyze username, display name, bio, and category
2. Identify HIGH-SIGNAL indicators—these often appear as domain expertise, topics, or affiliations rather than explicit role titles
3. Weight: relevant affiliation + role alignment + domain keywords as strong proxy
4. Determine likelihood of being a ${config.targetProfile}

## Scoring Scale
- 0.0-0.3: Bot, spam, or completely unrelated
- 0.3-0.5: Unlikely (adjacent field or unclear relevance)
- 0.5-0.7: Possible (some signals but not definitive)
- 0.7-0.9: Likely (clear alignment with ${config.targetProfile} profile)
- 0.9-1.0: Definite (explicit match or perfect signal combination)`;
}

/**
 * Default system prompt for TheLAI customers (qualitative researchers).
 * Uses hardcoded config from lambdas/llm-scorer/src/audiences/thelai_customers.json
 */
export const SYSTEM_PROMPT = generateSystemPrompt({
  targetProfile: "qualitative researcher",
  sector: "academia",
  highSignals: [
    "Explicit methodology: qualitative, ethnography, interviews, focus groups, grounded theory, phenomenology, narrative inquiry",
    "Research topics inherently qualitative: lived experience, stigma, identity, community-based participatory research",
    "Roles: PI, lab director, research scientist with population-focused studies",
    "Fields with qualitative traditions: sociology, anthropology, social work, nursing, public health (health equity), education, communication",
    "Participant interaction: 'partnering with communities', studying vulnerable/marginalized populations",
    "IRB-heavy contexts: HIV, mental health, trauma, sexual health, child welfare",
  ],
  lowSignals: [
    "Clinical roles without research (MD focused on patient care)",
    "Quantitative indicators: epidemiologist, biostatistician, data scientist",
    "Industry/corporate focus",
    "Advocacy without research role",
    "Teaching-only focus",
  ],
  domainContext:
    "These researchers need participant management, interview scheduling, transcription, IRB compliance, and secure data storage for sensitive research.",
});

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
    category: p.category,
    likely_is: p.likelyIs,
  }));

  const toonData = toToon(profilesData);

  return `Score the following ${profiles.length} Twitter profiles:

\`\`\`toon
${toonData}
\`\`\`

Respond with a JSON array. Each object must have:
- username: string (the profile's username)
- score: number (0.00 to 1.00)
- reason: string (brief explanation, max 100 chars)

IMPORTANT: Return ONLY the JSON array. No markdown formatting, no code blocks.
Return: [{ "username": string, "reason": string, "score": number }]`;
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
export function parseAndValidateResponse(text: string, profiles: ProfileToScore[]): ScoreResult[] {
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
  const usernameToProfile = new Map(profiles.map((p) => [p.username.toLowerCase(), p]));

  const results: ScoreResult[] = [];

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
