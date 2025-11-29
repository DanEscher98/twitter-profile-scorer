# Human Authenticity Score (HAS)

A heuristic scoring system for classifying Twitter/X profiles into types (Human, Creator, Entity, Bot, Other) and assigning confidence scores for ad targeting purposes.

## Design Philosophy

- **Conservative scoring**: Better to reject real humans (false negative) than accept bots/orgs (false positive)
- **Normalized scores**: All type-specific scores output [0,1] for fair comparison
- **Single penalty system**: Applied once at the end, not during scoring
- **Configurable**: All weights, thresholds, and penalties in `config.json`

## Architecture

```
ProfileData → extractFeatures() → DerivedFeatures
                                        ↓
                    ┌───────────────────┼───────────────────┐
                    ↓                   ↓                   ↓
              computeBotScore()   computePersonScore()  computeCreatorScore()
                    ↓                   ↓                   ↓
                  S_bot              S_person            S_creator
                    └───────────────────┼───────────────────┘
                                        ↓
                            Classification Logic
                                        ↓
                              computePenalty()
                                        ↓
                                 Final Score
```

## Feature Extraction

### Follower-Following Ratio

```
R_ff = clamp(log₁₀((followers + 1) / (following + 1)), -2, 3)
R_ff_norm = (R_ff + 2) / 5  ∈ [0, 1]
```

| R_ff Value | Interpretation                             |
| ---------- | ------------------------------------------ |
| < 0        | Follows more than followed (spam/new user) |
| ≈ 0        | Balanced (typical user)                    |
| > 1        | 10x more followers (creator/influencer)    |
| > 2        | 100x more followers (celebrity/org)        |

### Engagement Ratio

```
R_eng = min(1, favorites / (statuses + 1))
```

Measures how much the user likes others' content. High values suggest active human behavior.

**Note**: This is NOT how engaging their content is (we don't have retweet/like data on their tweets).

### List Membership

```
R_list = tanh(listed / 50)
```

Being on lists suggests credibility. `tanh` provides diminishing returns past ~50 lists.

### Media Ratio

```
R_media = min(1, media / (statuses + 1))
```

High media ratio suggests content creator.

### Account Age

```
A_age = 1 - e^(-days / 365)
```

Asymptotically approaches 1:

- ~0.63 at 1 year
- ~0.86 at 2 years
- ~0.95 at 3 years

### Activity Rate

```
A_activity = statuses / (days + 1)
```

Tweets per day average:

- Typical human: 0.5-2 tweets/day
- High activity: 3-5 tweets/day
- Suspicious: >10 tweets/day
- Bot-like: >50 tweets/day

### Profile Customization

```
P_custom = (¬default_profile + ¬default_image) / 2  ∈ {0, 0.5, 1}
```

- 0 = both default (strong spam signal)
- 0.5 = one customized
- 1 = fully customized

### Content Safety

```
P_safe = 1 - 0.3 × possibly_sensitive
```

Slight penalty for sensitive content flag.

## Type-Specific Scoring

### Bot Score

Detects automated accounts using:

- Hyperactive posting (>50 tweets/day)
- Low engagement (doesn't like content)
- Unbalanced ratio (follows >> followers)
- Default profile
- New account

```
S_hyperactive = σ(0.1 × (A_activity - 50))
S_no_engage = σ(5 × (0.1 - R_eng))
S_unbalanced = σ(5 × (-1.5 - R_ff))
S_new = σ(10 × (0.1 - A_age))

S_bot = σ(-3 + 3×S_hyperactive + 2×S_no_engage + 1.5×S_unbalanced + 1.5×(1-P_custom) + S_new)
```

### Creator Score

Detects content creators/influencers:

- Large follower count (>10k)
- High follower ratio (R_ff > 1)
- Media content
- Listed on many lists
- Verified

```
S_large_audience = σ(0.0003 × (followers - 10000))
S_high_ratio = σ(R_ff - 1)

S_creator = σ(-2.5 + 1.5×S_high_ratio + 1.2×R_media + 0.8×R_list + 0.5×P_verified + 0.8×S_large_audience)
```

### Entity Score

Detects organizations/brands:

- Very high follower ratio (R_ff > 1.7)
- Consistent posting (~3 tweets/day)
- Low personal engagement
- Media content
- Verified

```
S_consistent = e^(-((A_activity - 3) / 5)²)  (bell curve)
S_very_high_ratio = σ(R_ff - 1.7)

S_entity = σ(-2.5 + 1.2×S_very_high_ratio + 0.8×(1-R_eng) + 0.6×R_media + 0.5×P_verified + 0.8×S_consistent)
```

### Person Score

Detects regular human users using weighted sum:

```
S_balanced = max(0, 1 - 2×|R_ff_norm - 0.4|)

S_normal_activity = piecewise {
    0.4                           if A_activity < 0.1
    0.4 + 0.6×(A_activity-0.1)/0.4  if 0.1 ≤ A_activity < 0.5
    1.0                           if 0.5 ≤ A_activity ≤ 2
    0.8                           if 2 < A_activity ≤ 4
    0.5                           if 4 < A_activity ≤ 8
    0.2                           if A_activity > 8
}

S_established = min(1, followers / 200)

S_moderate_following = {
    0.5  if following > 5000
    0.8  if following > 2000
    1.0  otherwise
}

S_engaged = min(1, 2 × R_eng)

S_reasonable_volume = {
    0.5  if statuses > 20000
    0.7  if statuses > 10000
    1.0  otherwise
}

S_person = w_custom×P_custom + w_engaged×S_engaged + w_age×A_age + w_safe×P_safe
         + w_balanced×S_balanced + w_activity×S_normal_activity + w_established×S_established
         + w_following×S_moderate_following + w_volume×S_reasonable_volume
```

Default weights (sum to 0.83, leaving room for verification bonus):
| Weight | Value | Description |
|--------|-------|-------------|
| w_custom | 0.10 | Profile customization |
| w_engaged | 0.10 | Engagement behavior |
| w_age | 0.10 | Account age |
| w_safe | 0.05 | Content safety |
| w_balanced | 0.12 | Follower/following balance |
| w_activity | 0.12 | Normal posting frequency |
| w_established | 0.08 | Has established following |
| w_following | 0.08 | Not mass-following |
| w_volume | 0.08 | Reasonable tweet count |

### Verification Bonus

Verified accounts with high base scores (>0.7) receive a bonus of up to 0.08:

```
bonus = P_verified × 0.08 × σ(10 × (baseScore - 0.7))
```

This allows exceptional verified humans to reach 0.95+ while keeping unverified profiles capped at ~0.83.

## Classification Logic

Priority order (highest to lowest):

1. **Bot** - if `S_bot > 0.65`
   - Final score = `1 - S_bot` (inverted: high bot score = low final score)

2. **Entity** - if `S_entity > 0.55` AND `S_bot < 0.5`
   - Final score = `1 - S_entity` (inverted)

3. **Creator** - if `S_creator > 0.55` AND `S_entity < 0.5` AND `S_bot < 0.5`
   - Final score = `S_creator`

4. **Human** - if `S_person > 0.55`
   - Final score = `S_person`

5. **Other** - fallback when no clear classification
   - Uses argmax of all scores
   - If best is Bot/Entity → classify as Other with score 0.5
   - Otherwise use best type and score

## Penalty System

Penalties are multiplicative factors applied to the final score:

```
final_score = raw_score × penalty
```

| Penalty                   | Multiplier | Condition                                    |
| ------------------------- | ---------- | -------------------------------------------- |
| veryFewFollowers          | 0.60       | followers < 10                               |
| fewFollowers              | 0.80       | followers < 50                               |
| zeroStatuses              | 0.40       | statuses = 0                                 |
| veryFewStatuses           | 0.70       | statuses < 10                                |
| veryNewAccount            | 0.60       | days < 30                                    |
| newAccount                | 0.85       | days < 90                                    |
| spamPattern               | 0.50       | following > 5000 AND followers < 100         |
| hyperactive               | 0.65       | A_activity > 20                              |
| highActivity              | 0.85       | A_activity > 10                              |
| highVolumeNoFollowers     | 0.70       | statuses > 30000 AND followers < statuses/10 |
| defaultProfile            | 0.75       | P_custom < 0.5                               |
| lowEngagementHighActivity | 0.70       | R_eng < 0.1 AND A_activity > 5               |

Multiple penalties stack multiplicatively:

```
penalty = Π(penalty_i) for all triggered conditions
```

## Usage

### Basic Usage

```typescript
import { ProfileData, computeHAS } from "@profile-scorer/has-scorer";

const profile: ProfileData = {
  followers: 1500,
  following: 800,
  statuses: 2000,
  favorites: 5000,
  listed: 10,
  media: 200,
  isBlueVerified: false,
  defaultProfile: false,
  defaultProfileImage: false,
  possiblySensitive: false,
  createdAt: "2020-01-15T00:00:00Z",
};

const result = computeHAS(profile);
// { score: 0.72, likelyIs: "Human" }
```

### With Custom Config

```typescript
import { computeHASwithConfig, createConfig, defaultConfig } from "@profile-scorer/has-scorer";

// Modify specific weights
const customConfig = createConfig({
  personWeights: {
    ...defaultConfig.personWeights,
    balanced: 0.2, // Increase importance of follower balance
  },
});

const result = computeHASwithConfig(profile, customConfig);
```

### Debug Mode

```typescript
import { computeDetailedScores, defaultConfig } from "@profile-scorer/has-scorer";

const details = computeDetailedScores(profile, defaultConfig);
console.log(details);
// {
//   features: { R_ff: 0.27, R_ff_norm: 0.45, ... },
//   botScore: 0.05,
//   personScore: 0.72,
//   creatorScore: 0.15,
//   entityScore: 0.08,
//   penalty: 1.0,
//   result: { score: 0.72, likelyIs: "Human" }
// }
```

## Configuration File

All parameters are in `src/config.json`. To tune the system:

1. Copy `config.json` to a new file
2. Modify weights/thresholds
3. Test with `computeHASwithConfig(profile, customConfig)`
4. Use the test script to compare distributions

```bash
# Test with custom config
yarn workspace @profile-scorer/scripts run run js_src/test-has-changes.ts /path/to/custom-config.json
```

## Mathematical Notation Reference

| Symbol       | Definition                  |
| ------------ | --------------------------- |
| σ(x)         | Sigmoid function: 1/(1+e⁻ˣ) |
| tanh(x)      | Hyperbolic tangent          |
| e            | Euler's number ≈ 2.718      |
| log₁₀        | Base-10 logarithm           |
| clamp(x,a,b) | max(a, min(b, x))           |
| ¬            | Logical NOT                 |
| ×            | Multiplication              |
| Σ            | Summation                   |
| Π            | Product                     |
