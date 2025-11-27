import { SQSHandler } from "aws-lambda";
import { and, eq, notExists, sql } from "drizzle-orm";

import { getDb, profileScores, profilesToScore, userProfiles } from "@profile-scorer/db";

interface ScoringMessage {
  model: string;
  batchSize: number;
}

interface ProfileToScore {
  twitterId: string;
  username: string;
  displayName: string | null;
  bio: string | null;
  likelyIs: string | null;
  category: string | null;
}

export const handler: SQSHandler = async (event) => {
  console.log(`[llm-scorer] Processing ${event.Records.length} messages`);

  for (const record of event.Records) {
    const message: ScoringMessage = JSON.parse(record.body);
    const { model, batchSize } = message;

    console.log(`[llm-scorer] Scoring batch for model: ${model}, batchSize: ${batchSize}`);

    try {
      const db = getDb();

      // Fetch profiles not yet scored by this model
      const profiles = await db
        .select({
          twitterId: userProfiles.twitterId,
          username: userProfiles.username,
          displayName: userProfiles.displayName,
          bio: userProfiles.bio,
          likelyIs: userProfiles.likelyIs,
          category: userProfiles.category,
        })
        .from(profilesToScore)
        .innerJoin(userProfiles, eq(profilesToScore.twitterId, userProfiles.twitterId))
        .where(
          notExists(
            db
              .select({ one: sql`1` })
              .from(profileScores)
              .where(
                and(
                  eq(profileScores.twitterId, userProfiles.twitterId),
                  eq(profileScores.scoredBy, model)
                )
              )
          )
        )
        .orderBy(profilesToScore.addedAt)
        .limit(batchSize);

      console.log(`[llm-scorer] Found ${profiles.length} profiles to score`);

      if (profiles.length === 0) {
        console.log("[llm-scorer] No profiles to score for this model");
        continue;
      }

      // TODO: Implement actual LLM scoring
      // For now, generate dummy scores for testing
      const scores = profiles.map((profile) => ({
        twitterId: profile.twitterId,
        username: profile.username,
        score: generateDummyScore(profile),
        reason: `Dummy score for testing - ${profile.likelyIs ?? "Unknown"} profile`,
      }));

      console.log(`[llm-scorer] Generated ${scores.length} scores`);

      // Store scores in database
      for (const scoreData of scores) {
        await db.insert(profileScores).values({
          twitterId: scoreData.twitterId,
          score: scoreData.score.toFixed(2),
          reason: scoreData.reason,
          scoredBy: model,
        });

        console.log(
          `[llm-scorer] Stored score for @${scoreData.username}: ${scoreData.score.toFixed(2)}`
        );
      }

      // Remove scored profiles from queue (only if scored by all models)
      // For now, we don't remove - let profiles accumulate scores from multiple models

      console.log(`[llm-scorer] Completed batch for model: ${model}`);
    } catch (error) {
      console.error(`[llm-scorer] Error processing batch:`, error);
      throw error; // Re-throw to trigger retry/DLQ
    }
  }
};

// Dummy scoring function for testing
function generateDummyScore(profile: ProfileToScore): number {
  let score = 0.5;

  // Boost for human classification
  if (profile.likelyIs === "Human") score += 0.2;
  if (profile.likelyIs === "Creator") score += 0.15;

  // Boost for having a bio
  if (profile.bio && profile.bio.length > 50) score += 0.1;

  // Boost for having a category
  if (profile.category) score += 0.05;

  // Add some randomness for testing
  score += (Math.random() - 0.5) * 0.2;

  return Math.max(0, Math.min(1, score));
}
