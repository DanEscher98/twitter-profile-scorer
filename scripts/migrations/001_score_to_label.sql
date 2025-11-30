-- Migration: Convert numeric scores to trivalent labels
--
-- This migration:
-- 1. Renames username -> handle, display_name -> name in user_profiles
-- 2. Renames username -> handle in profiles_to_score
-- 3. Renames avg_llm_score -> label_rate in keyword_stats
-- 4. Adds label column to profile_scores
-- 5. Converts existing scores to labels: true (>0.7), false (<0.4), null (0.4-0.7)
-- 6. Drops the old score column
--
-- Run with: psql $DATABASE_URL -f scripts/migrations/001_score_to_label.sql

BEGIN;

-- Step 1: Rename columns in user_profiles
ALTER TABLE user_profiles RENAME COLUMN username TO handle;
ALTER TABLE user_profiles RENAME COLUMN display_name TO name;

-- Update the unique index
DROP INDEX IF EXISTS uq_username;
CREATE UNIQUE INDEX uq_handle ON user_profiles(handle);

-- Step 2: Rename column in profiles_to_score
ALTER TABLE profiles_to_score RENAME COLUMN username TO handle;

-- Step 3: Rename column in keyword_stats
ALTER TABLE keyword_stats RENAME COLUMN avg_llm_score TO label_rate;

-- Step 4: Add label column to profile_scores
ALTER TABLE profile_scores ADD COLUMN label BOOLEAN;

-- Step 5: Convert existing scores to labels
-- true: score >= 0.7 (likely match)
-- false: score < 0.4 (not a match)
-- null: 0.4 <= score < 0.7 (uncertain)
UPDATE profile_scores
SET label = CASE
    WHEN score::numeric >= 0.7 THEN true
    WHEN score::numeric < 0.4 THEN false
    ELSE null
END;

-- Log the conversion stats
DO $$
DECLARE
    total_count INTEGER;
    true_count INTEGER;
    false_count INTEGER;
    null_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO total_count FROM profile_scores;
    SELECT COUNT(*) INTO true_count FROM profile_scores WHERE label = true;
    SELECT COUNT(*) INTO false_count FROM profile_scores WHERE label = false;
    SELECT COUNT(*) INTO null_count FROM profile_scores WHERE label IS NULL;

    RAISE NOTICE 'Migration complete:';
    RAISE NOTICE '  Total scores: %', total_count;
    RAISE NOTICE '  Converted to true (>=0.7): %', true_count;
    RAISE NOTICE '  Converted to false (<0.4): %', false_count;
    RAISE NOTICE '  Converted to null (0.4-0.7): %', null_count;
END $$;

-- Step 6: Drop the old score column
ALTER TABLE profile_scores DROP COLUMN score;

COMMIT;
