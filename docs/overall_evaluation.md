# Overall Pipeline Evaluation

This document provides a comprehensive analysis of the Profile Scorer data pipeline, identifying structural flaws and recommendations for improvement.

**Last Updated**: 2025-12-03

---

## Current Pipeline Architecture

```
Profile Search DAG (every 10 min)
├── keyword_engine → Get keywords per platform (Twitter, Bluesky)
├── query_profiles → Fetch from TwitterX/Bluesky APIs
├── update_profiles → Store user_profiles + user_stats
├── compute_scores → Calculate activity_score, cos_sim, llm_worth
└── users_by_target → Mark profiles worth LLM evaluation

LLM Scoring DAG (every 10 min, offset by 5)
├── fetch profiles where llm_worth=True
├── score with 3 LLMs (probabilistic invocation)
└── store labels (true/false/null)
```

### Scoring Components

| Component | Purpose | Key Signal |
|-----------|---------|------------|
| `activity_score` | Is this a real human? | 8 numerical features (followers, activity, etc.) |
| `bio_quality` | Is the bio readable? | Latin text ratio |
| `cos_sim` | Does bio match target audience? | Embedding similarity to centroid |
| `llm_worth` | Worth sending to LLM? | Logistic regression on above 3 |

---

## Current Performance (2025-12-03)

After retraining with `--balanced --metric f2`:

```
Model: llm_worth v02
Threshold: 0.33
Weights: cos_sim=6.09, activity_score=2.98, bio_quality=0.17

Curated Recall:     92.9% (26/28)
Hand-picked Recall: 94.3% (367/389)

llm_worth=True:  6,371 (56.8%)
llm_worth=False: 4,840 (43.2%)
Total profiles:  11,211
```

**False Negatives (Curated)**: `@BhavanaBRaoMD`, `@ProfAbigailYork`

---

## Known Flaws (Documented)

These issues are already documented in `weights_methodology.md`:

1. **Small evaluation set**: Only 28 curated customers for TP validation
2. **Label noise in TN class**: `label=FALSE` mixes bots, entities, and off-topic humans
3. **Selection bias**: Hand-picked profiles may not represent production distribution
4. **Platform imbalance**: Dataset predominantly Twitter, Bluesky underrepresented
5. **Single centroid assumption**: Target audience may have multiple clusters (wet lab vs computational researchers)

---

## Additional Identified Flaws

### 1. Circular Dependency in Training Data

The `llm_worth` classifier is trained on profiles gathered via keyword search. The keywords were chosen to find "qualitative researchers." This creates selection bias:

- Profiles in DB are pre-filtered by keyword relevance
- The classifier learns to discriminate within this already-filtered pool
- In production, the same keywords feed the same pipeline

**Risk**: The model hasn't seen truly random profiles (spam, crypto, political accounts) that might share surface-level features with researchers.

**Evidence**: All training/test profiles came from academic-focused keyword searches.

---

### 2. cos_sim Dominance Problem

From trained weights:

```
cos_sim:        +6.09  (dominant signal)
activity_score: +2.98  (moderate)
bio_quality:    +0.17  (near zero - effectively ignored)
```

The classifier is essentially `cos_sim > threshold` with activity_score as a minor adjustment.

**Fragility cases**:
- Spam account with academic keywords in bio → high cos_sim → llm_worth=True (false positive)
- Legitimate researcher with casual bio ("just a curious human") → low cos_sim → llm_worth=False (false negative)

**Evidence**: 22 false negatives in hand-picked profiles (like `@QuantPsychiatry`, `@acagamic`) likely have bios that don't match academic keyword patterns in the centroid.

---

### 3. Embedding Model Mismatch

`all-MiniLM-L6-v2` is trained on general English text, not academic/professional bios. Academic bios have specific patterns:

- Abbreviated titles: "Asst Prof", "PI", "PhD student"
- Institution codes: "@Stanford", "@MIT"
- Hashtags as identity: "#AcademicTwitter", "#phdlife"
- Field-specific jargon: "mixed methods", "IRB", "CBPR"

A general-purpose embedding may not capture semantic similarity between:
- "Qualitative researcher studying health disparities"
- "PI, mixed methods, community health @UCLA"

These are semantically equivalent for our use case but may have low cosine similarity in general embedding space.

---

### 4. LLM Scoring Queue Starvation

From `llm_scoring.py`:

```python
MODEL_CONFIGS = [
    {"alias": "meta-maverick-17b", "probability": 0.7, "batch_size": 25},
    {"alias": "claude-haiku-4.5", "probability": 0.6, "batch_size": 25},
    {"alias": "gemini-flash-2.0", "probability": 0.4, "batch_size": 15},
]
```

With `llm_worth=True` rate at 56.8%, queue may grow faster than processing:

| Metric | Rate |
|--------|------|
| New profiles per 10 min | ~200 (10 keywords × 20 profiles) |
| Marked llm_worth=True | ~114 (56.8% rate) |
| LLM processing capacity | ~45 scores per 10 min |

**Calculation**: 3 LLMs × 25 batch × 0.6 avg probability ≈ 45 scores/run

**Risk**: Queue backlog grows; LLM scores become stale as profiles evolve.

---

### 5. Keyword Pool Exhaustion

The keyword pool is finite. As keywords get exhausted (all pages fetched), the system relies on tangential keywords. Profile quality degrades over time:

- **Early**: Direct keywords ("qualitative research", "grounded theory") yield high-fit profiles
- **Later**: Indirect keywords ("health equity", "community") bring edge cases

From DEVLOG (2025-11-29):
> "Current performance: @customers average score is 0.665 (target ~0.75), with a mean percentile of 19.4%"

This was before recall improvements, but the underlying keyword exhaustion problem persists.

---

### 6. No Negative Feedback Loop

When LLM labels a profile as `false`, nothing happens upstream. The `llm_worth` classifier doesn't learn from:

- Profiles marked `llm_worth=True` that LLM labeled `false` (wasted API cost)
- Profiles marked `llm_worth=False` that match curated customers (missed leads)

The DEVLOG mentions "Active learning" as a long-term goal but it's not implemented.

**Current state**: Classifier is static after deployment; only retrained manually.

---

### 7. Bluesky Data Quality Unknown

From analysis results:

```
Platform breakdown:
  twitter: 10,045 profiles (55.4% llm_worth=True)
  bluesky:  1,166 profiles (69.5% llm_worth=True)
```

Bluesky shows higher `llm_worth=True` rate, but:

- All 28 curated customers are Twitter accounts
- The centroid was computed from Twitter bios only
- Bluesky bio conventions may differ (shorter, different style)

**Risk**: Higher rate may indicate model miscalibration for Bluesky, not better profiles.

---

### 8. Threshold vs Probability Mismatch

The model outputs probabilities, but returns boolean:

```python
prob = 1 / (1 + np.exp(-z))
return bool(prob >= config.threshold)
```

At threshold 0.33:
- Profile with P=0.34 treated same as P=0.99
- Information lost; no prioritization possible

A tiered approach would enable:
- Prioritizing high-confidence profiles for LLM
- Human review of edge cases (0.30-0.40 range)
- Better queue management

---

## Recommendations

### High Priority (High Impact, Moderate Effort)

| Issue | Fix | Expected Outcome |
|-------|-----|------------------|
| cos_sim dominance | Add negative examples from unrelated domains (crypto, sports, marketing) to training | More robust discrimination |
| No feedback loop | Log LLM results → retrain llm_worth quarterly | Continuous improvement |
| Threshold brittleness | Store probability in `users_by_target`, add confidence tiers | Better queue prioritization |

### Medium Priority (Moderate Impact)

| Issue | Fix | Expected Outcome |
|-------|-----|------------------|
| Queue growth | Add rate limiting or priority scoring for LLM queue | Prevent backlog |
| Keyword exhaustion | Implement keyword refresh from LLM-labeled positives | Sustainable growth |
| Platform imbalance | Gather Bluesky-specific curated profiles for validation | Calibrated cross-platform |

### Low Priority (Lower Impact or High Effort)

| Issue | Fix | Expected Outcome |
|-------|-----|------------------|
| Embedding model | Fine-tune on academic bios or switch to `e5-small` | Better semantic matching |
| Multi-cluster audience | K-means on positive bios, score against nearest centroid | Handle audience heterogeneity |
| Circular training data | Sample truly random profiles for TN class | More generalizable model |

---

## Structural Limitations

These are fundamental constraints that require significant architectural changes:

1. **Training data homogeneity**: All profiles from keyword-filtered pool. True generalization requires external data sources.

2. **cos_sim is doing most of the work**: The 3-feature logistic regression effectively reduces to a single-feature threshold. Consider gradient boosting or neural approach for feature interactions.

3. **No active learning infrastructure**: The pipeline lacks mechanisms to feed LLM results back into training. Requires schema changes and scheduled retraining.

4. **Single audience model**: Assumes target audience is unimodal. Multi-centroid or clustering approaches require significant refactoring.

---

## Conclusion

The pipeline is functional for initial audience building with 94%+ recall on known positives. The primary risks are:

1. **Short-term**: LLM queue backlog as llm_worth=True rate increased to 56.8%
2. **Medium-term**: Keyword pool exhaustion degrading profile quality
3. **Long-term**: Classifier drift without feedback loop

The recall improvement (from ~75% to ~94%) validates the F2 + balanced approach. Remaining false negatives (`@BhavanaBRaoMD`, `@ProfAbigailYork`) likely have bios that don't match the centroid's academic keyword patterns—a limitation of the embedding model rather than the classifier.

---

## Short-Term Improvement Plan

A focused 2-week sprint to address the most critical issues before they compound.

### Week 1: Data Quality & Feedback Infrastructure

#### Task 1.1: Store LLM Worth Probability (1 day)

**Goal**: Preserve probability scores for future prioritization and analysis.

**Changes**:
1. Add `llm_worth_prob` column to `users_by_target` table (DECIMAL 0-1)
2. Modify `compute_llm_worth()` to return `(bool, float)` tuple
3. Update `profile_search.py:compute_scores()` to store probability
4. Backfill existing records with re-computed probabilities

**Files**:
- `packages/db/src/db/models.py` - Add column
- `packages/scoring/src/scoring/api.py` - Return probability
- `dags/profile_search.py` - Store probability
- `scripts/backfill_users_by_target.py` - Update for probability

#### Task 1.2: Create LLM Feedback Export (1 day)

**Goal**: Enable periodic retraining by exporting LLM scoring results.

**Script**: `scripts/training/export_llm_feedback.py`

```python
# Export profiles where:
# - llm_worth=True (we sent to LLM)
# - profile_scores.label is not null (LLM responded)
# Output: CSV with (handle, activity_score, bio_quality, cos_sim, llm_label)
```

**Use case**: After 1000+ LLM scores, regenerate training dataset with real labels.

#### Task 1.3: Add Out-of-Domain Negatives (2 days)

**Goal**: Reduce cos_sim dominance by training on diverse negatives.

**Steps**:
1. Create keyword list for non-target domains: `["crypto trader", "sports fan", "marketing guru", "influencer", "forex"]`
2. Run one-time data collection with these keywords (500 profiles)
3. Manually label sample as `FALSE` (quick spot-check, not exhaustive)
4. Add to training set as guaranteed negatives
5. Retrain `llm_worth` with expanded TN class

**Expected outcome**: Model learns that high cos_sim alone isn't sufficient; activity patterns matter more.

### Week 2: Queue Management & Monitoring

#### Task 2.1: Priority Queue for LLM Scoring (2 days)

**Goal**: Process high-confidence profiles first, prevent backlog.

**Changes**:
1. Add `priority` column to `profiles_to_score` or use `llm_worth_prob`
2. Modify `fetch_profiles_to_score()` to order by probability DESC
3. Add queue depth monitoring (log warning if > 500 pending)

**Logic**:
```python
# High priority: prob >= 0.7 (process first)
# Medium priority: 0.5 <= prob < 0.7
# Low priority: 0.33 <= prob < 0.5 (process when queue is short)
```

#### Task 2.2: LLM Queue Dashboard Metrics (1 day)

**Goal**: Visibility into queue health.

**Metrics to add**:
- `llm_queue_depth`: Count of pending profiles
- `llm_scores_per_hour`: Rate of LLM processing
- `llm_worth_rate_7d`: Rolling average of llm_worth=True rate

**Location**: CloudWatch dashboard or Airflow metrics.

#### Task 2.3: Keyword Health Check (1 day)

**Goal**: Identify exhausted keywords before they dominate the pool.

**Script**: `scripts/analysis/keyword_health.py`

```python
# For each keyword, compute:
# - Pages fetched
# - Profiles found
# - llm_worth=True rate
# - Last 7-day yield (new profiles per query)
# Flag keywords with yield < 2 profiles/query as "exhausted"
```

**Output**: Report showing keyword pool health, recommendations for new keywords.

### Validation Checkpoints

| Milestone | Metric | Target |
|-----------|--------|--------|
| End of Week 1 | Probability stored for new profiles | 100% coverage |
| End of Week 1 | Out-of-domain negatives collected | 500 profiles |
| End of Week 2 | Queue depth monitored | Alert if > 500 |
| End of Week 2 | Keyword health report | Automated weekly |

### Success Criteria

After 2 weeks:
1. **Data**: Probability scores stored, enabling future prioritization
2. **Feedback**: Export script ready for quarterly retraining
3. **Robustness**: Model retrained with out-of-domain negatives
4. **Visibility**: Queue and keyword health monitored
5. **Sustainability**: High-priority profiles processed first

### Stretch Goals (If Time Permits)

- [ ] Implement confidence tiers in LLM queue (high/medium/low)
- [ ] Add Bluesky-specific validation set (5-10 curated handles)
- [ ] Create automated monthly retraining pipeline
- [ ] Add keyword suggestion from LLM-labeled positives

---

## Related Documentation

- [Weights Training Methodology](./weights_methodology.md) - How components were trained
- [HAS Heuristic](./heuristic.md) - Activity score feature definitions
- [DEVLOG](./DEVLOG.md) - Chronological development history
