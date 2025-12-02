import { encode as toToon } from "@toon-format/toon";
import { z } from "zod";

import { ProfileToScore } from "@profile-scorer/db";
import { createLogger } from "@profile-scorer/utils";

const log = createLogger("llm-shared");

/**
 * Label result from LLM wrapper.
 * Trivalent system: true=good, false=bad, null=uncertain
 */
export interface LabelResult {
  twitterId: string;
  handle: string;
  label: boolean | null;
  reason: string;
}

/**
 * Zod schema for a single label result from LLM.
 */
const LabelItemSchema = z.object({
  handle: z.string(),
  reason: z.string(),
  label: z.boolean().nullable(),
});

/**
 * Zod schema for the full LLM response array.
 */
const LabelResponseSchema = z.array(LabelItemSchema);

/**
 * Configuration for audience-specific labeling prompts.
 */
export interface AudienceConfig {
  targetProfile: string;
  sector: "academia" | "industry" | "government" | "ngo" | "healthcare" | "custom";
  highSignals: string[];
  lowSignals: string[];
  domainContext: string;
}

/**
 * Generate a system prompt based on audience configuration
 * Uses trivalent labeling: true (match), false (no match), null (uncertain)
 *
 * @param config - Audience configuration with target profile and signals
 * @returns System prompt string for LLM
 */
export function generateSystemPrompt(config: AudienceConfig): string {
  return `ROLE: You are an expert at identifying individual ${config.targetProfile}s in ${config.sector.toUpperCase()}.

## Domain Context
${config.domainContext}

## Classification Signals

POSITIVE INDICATORS (suggests target match):
${config.highSignals.map((s) => `• ${s}`).join("\n")}

NEGATIVE INDICATORS (suggests non-match):
${config.lowSignals.map((s) => `• ${s}`).join("\n")}

## Evaluation Process
For each profile:
1. First: Is this an individual person? (Reject orgs, brands, podcasts, journals, bots)
2. Then: Does this individual match ${config.targetProfile} criteria?
IMPORTANT: When in doubt, use null. False positives are worse than uncertain labels.

## Label Definitions
- true: Individual who clearly matches ${config.targetProfile} criteria
- false: Organization/brand/bot, empty/vague bio, or individual clearly outside target
- null: Individual with some signals but ambiguous fit or just adjacent field

## Output Interface
Respond with a JSON array. Each object must have:
- handle: string (profile's handle)
- label: boolean|null (is a "${config.targetProfile}"?)
- reason: string (brief explanation, max 15 words)

IMPORTANT: Return ONLY the JSON array. No markdown formatting, no code blocks.
Return: [{ "handle": string, "label": boolean|null, "reason": string }]`;
}

/**
 * Format profiles into a TOON prompt for the LLM.
 *
 * @param profiles - Array of profiles to label
 * @returns Formatted prompt string with TOON representation
 */
export function formatProfilesPrompt(profiles: ProfileToScore[]): string {
  // Transform to TOON-friendly format
  const profilesData = profiles.map((p) => ({
    handle: p.handle,
    name: p.name,
    bio: p.bio,
    category: p.category,
    followers: p.followers,
  }));

  const toonData = toToon(profilesData);

  return `Label the following ${profiles.length} Twitter profiles:

\`\`\`toon
${toonData}
\`\`\``;
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
 * @returns Array of validated label results with twitterId
 */
export function parseAndValidateResponse(text: string, profiles: ProfileToScore[]): LabelResult[] {
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
  const validation = LabelResponseSchema.safeParse(parsed);
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

  // Map handles back to twitterIds
  const handleToProfile = new Map(profiles.map((p) => [p.handle.toLowerCase(), p]));

  const results: LabelResult[] = [];

  for (const item of validation.data) {
    const profile = handleToProfile.get(item.handle.toLowerCase());
    if (profile) {
      results.push({
        twitterId: profile.twitterId,
        handle: item.handle,
        label: item.label,
        reason: item.reason.slice(0, 500),
      });
    } else {
      log.warn("Handle from LLM response not found in profiles", {
        handle: item.handle,
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
