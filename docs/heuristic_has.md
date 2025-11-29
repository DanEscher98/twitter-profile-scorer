# Human Authenticity Score (HAS) - Hybrid Heuristic

## Overview

This document presents a hybrid approach combining multi-class classification with elegant mathematical normalizations, using **only numeric and boolean fields**.

> **Note:** For implementation details and configurable weights, see the `@profile-scorer/has-scorer` package and its [README](../packages/has-scorer/README.md).

---

## Design Principles

1. **Smooth transitions** using continuous functions instead of hard thresholds
2. **Multi-class output** to distinguish Person, Creator, Entity, Bot
3. **Numeric/boolean only** - no keyword or string analysis
4. **Penalty modifiers** for clear red flags
5. **Conservative scoring** - better to reject humans than accept bots

---

## Feature Engineering

### 1. Follower-Following Ratio (Clamped Log)

```
R_ff = clamp(log₁₀((followers + 1) / (following + 1)), -2, 3)
```

**Normalized to [0,1]:**

```
R_ff_norm = (R_ff + 2) / 5
```

| R_ff Value | Interpretation                             |
| ---------- | ------------------------------------------ |
| ≈ 0        | Balanced (typical human)                   |
| < -1       | Follows many, few followers (spam pattern) |
| > 2        | Celebrity/Entity/Potential bot             |

---

### 2. Engagement Ratio

```
R_eng = min(1, favorites / (statuses + 1))
```

Real users like tweets relative to posting. Bots rarely engage. Values > 0.5 strongly indicate human behavior.

---

### 3. Account Age (Exponential Decay)

```
A_age = 1 - e^(-days / 365)
```

Smoothly rewards older accounts:

- 1 year → 0.63
- 2 years → 0.86
- 3 years → 0.95

---

### 4. Activity Rate

```
A_activity = statuses / (days + 1)
```

Normal: 0.5-10 tweets/day. Suspicious: >50/day (bot) or <0.01 (dormant).

---

### 5. List Credibility (Tanh Normalized)

```
R_list = tanh(listed / 50)
```

Being added to lists indicates human curation:

- 10 lists → 0.20
- 50 lists → 0.76
- 100+ lists → 0.96

---

### 6. Media Ratio

```
R_media = min(1, media / (statuses + 1))
```

Proportion of tweets with media. Creators typically > 0.3.

---

### 7. Profile Customization Score

```
P_custom = ((1 - default_profile) + (1 - default_profile_image)) / 2
```

Values: 0, 0.5, or 1. Penalizes default profiles/images.

---

### 8. Content Safety

```
P_safe = 1 - 0.3 × possibly_sensitive
```

Small penalty for NSFW-flagged accounts.

---

### 9. Verification Bonus

```
P_verified = is_blue_verified ? 1 : 0
```

Used in verification bonus calculation for high-scoring profiles.

---

## Classification Scores

### Sigmoid Helper

```
σ(x) = 1 / (1 + e^(-x))
```

---

### Bot Score

```
S_bot = σ(-3 + 3×S_hyperactive + 2×S_no_engage + 1.5×S_unbalanced + 1.5×(1-P_custom) + S_new)
```

Where:

```
S_hyperactive = σ(0.1 × (A_activity - 50))
S_no_engage = σ(5 × (0.1 - R_eng))
S_unbalanced = σ(5 × (-1.5 - R_ff))
S_new = σ(10 × (0.1 - A_age))
```

---

### Person Score

```
S_person = w_custom×P_custom + w_engaged×S_engaged + w_age×S_age + w_safe×P_safe
         + w_balanced×S_balanced + w_activity×S_normal_activity + w_established×S_established
         + w_following×S_moderate_following + w_volume×S_reasonable_volume
```

Where:

```
S_balanced = bellCurve(R_ff_norm, peak=0.4, width=0.25)
           = e^(-((R_ff_norm - 0.4) / 0.25)²)
```

Default weights (sum to ~0.83):

| Weight        | Value | Description                |
| ------------- | ----- | -------------------------- |
| w_custom      | 0.10  | Profile customization      |
| w_engaged     | 0.10  | Engagement behavior        |
| w_age         | 0.10  | Account age                |
| w_safe        | 0.05  | Content safety             |
| w_balanced    | 0.12  | Follower/following balance |
| w_activity    | 0.12  | Normal posting frequency   |
| w_established | 0.08  | Has established following  |
| w_following   | 0.08  | Not mass-following         |
| w_volume      | 0.08  | Reasonable tweet count     |

**Verification Bonus:** Up to +0.08 for verified accounts with base score > 0.7:

```
bonus = P_verified × 0.08 × σ(10 × (baseScore - 0.7))
```

---

### Creator Score

```
S_creator = σ(-2.5 + 1.5×S_high_ratio + 1.2×R_media + 0.8×R_list + 0.5×P_verified + 0.8×S_large_audience)
```

Where:

```
S_high_ratio = σ(R_ff - 1)
S_large_audience = σ(0.0003 × (followers - 10000))
```

---

### Entity Score

```
S_entity = σ(-2.5 + 1.2×S_very_high_ratio + 0.8×(1-R_eng) + 0.6×R_media + 0.5×P_verified + 0.8×S_consistent)
```

Where:

```
S_very_high_ratio = σ(R_ff - 1.7)
S_consistent = e^(-((A_activity - 3) / 5)²)
```

(Peaks around 3 tweets/day, typical for organizations)

---

## Penalty Modifiers

Apply multiplicative penalties for clear red flags:

```
penalty = ∏(penalty_i) for all triggered conditions
```

| Condition                                    | Penalty |
| -------------------------------------------- | ------- |
| followers < 10                               | 0.60    |
| followers < 50                               | 0.80    |
| statuses == 0                                | 0.40    |
| statuses < 10                                | 0.70    |
| days < 30                                    | 0.60    |
| days < 90                                    | 0.85    |
| following > 5000 AND followers < 100         | 0.50    |
| A_activity > 20                              | 0.65    |
| A_activity > 10                              | 0.85    |
| statuses > 30000 AND followers < statuses/10 | 0.70    |
| P_custom < 0.5                               | 0.75    |
| R_eng < 0.1 AND A_activity > 5               | 0.70    |

---

## Final Classification

### Category Selection

Priority order (highest to lowest):

1. **Bot** - if `S_bot > 0.65`
2. **Entity** - if `S_entity > 0.55` AND `S_bot < 0.5`
3. **Creator** - if `S_creator > 0.55` AND `S_entity < 0.5` AND `S_bot < 0.5`
4. **Human** - if `S_person > 0.55`
5. **Other** - fallback when no clear classification

---

### Final Score (Human Authenticity)

```
HAS = rawScore × penalty

where rawScore:
  - Human/Creator: class score (higher = better)
  - Bot/Entity: 1 - class score (inverted: high bot score = low HAS)
  - Other: 0.5
```

---

## Score Interpretation

| Score Range | Classification  | Recommended Action   |
| ----------- | --------------- | -------------------- |
| 0.00 - 0.20 | Likely Bot/Spam | Discard              |
| 0.20 - 0.45 | Suspicious      | Manual review        |
| 0.45 - 0.65 | Uncertain       | Include with caution |
| 0.65 - 0.85 | Likely Human    | Include              |
| 0.85 - 1.00 | Confident Human | High priority        |

**Design Goals:**

- Most humans: 0.60-0.80
- Exceptional humans: 0.85-0.92
- Verified exceptional: Can reach 0.95+
- Blatant bots: < 0.02

---

## Signal Summary

| Signal     | Person    | Creator | Entity | Bot      |
| ---------- | --------- | ------- | ------ | -------- |
| R_ff (log) | -0.5 to 1 | 1 to 2  | 1.5+   | < -1.5   |
| R_eng      | > 0.3     | 0.2 - 1 | < 0.3  | < 0.1    |
| A_activity | 0.5 - 5   | 1 - 20  | 1 - 5  | > 50     |
| P_custom   | High      | High    | High   | Low      |
| A_age      | > 0.63    | Any     | > 0.86 | < 0.3    |
| R_list     | Low-Med   | High    | High   | Very Low |

---

## Advantages

1. **Smooth degradation**: Log, tanh, and sigmoid functions avoid harsh cutoffs
2. **Multi-class output**: Distinguishes Person/Creator/Entity/Bot
3. **Purely numeric**: No string/keyword analysis required
4. **Penalty system**: Catches obvious red flags without affecting edge cases
5. **Conservative**: Weights sum to ~0.83, making high scores rare
6. **Configurable**: All weights in JSON for easy tuning
