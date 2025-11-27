# Human Authenticity Score (HAS) - Hybrid Heuristic

## Overview

This document presents a hybrid approach combining the multi-class classification from Document 1 with the elegant mathematical normalizations from Proposal 1, using **only numeric and boolean fields**.

---

## Analysis of Source Proposals

### Document 1 Strengths
- Multi-class output (Person, Creator, Entity, Bot, Other)
- Comprehensive derived ratios: engagement, media, list credibility
- Separate scoring functions allow nuanced classification

### Document 1 Weaknesses
- Hard indicator functions $\mathbb{1}_{[\cdot]}$ create discontinuous boundaries
- Magic thresholds (e.g., 50 tweets/day, 10k followers) lack smooth degradation

### Proposal 1 Strengths
- Smooth normalizations: $\log$, $\tanh$, exponential decay
- Clamped ratios handle extreme values gracefully
- Multiplicative penalty system for red flags

### Proposal 1 Weaknesses
- Single score loses multi-class information
- Uses `bio_length` and `location` which require string analysis

---

## Hybrid Approach

### Design Principles

1. **Smooth transitions** using continuous functions instead of hard thresholds
2. **Multi-class output** to distinguish Person, Creator, Entity, Bot
3. **Numeric/boolean only** - no keyword or string analysis
4. **Penalty modifiers** for clear red flags

---

## Feature Engineering

### 1. Follower-Following Ratio (Clamped Log)

$$
R_{ff} = \text{clamp}\left( \log_{10}\left(\frac{\text{followers\_count} + 1}{\text{friends\_count} + 1}\right), -2, 3 \right)
$$

**Normalized to [0,1]:**
$$
\hat{R}_{ff} = \frac{R_{ff} + 2}{5}
$$

| $R_{ff}$ | Interpretation |
|----------|----------------|
| $\approx 0$ | Balanced (typical human) |
| $< -1$ | Follows many, few followers (spam pattern) |
| $> 2$ | Celebrity/Entity/Potential bot |

---

### 2. Engagement Ratio

$$
R_{eng} = \min\left(1, \frac{\text{favourites\_count}}{\text{statuses\_count} + 1}\right)
$$

Real users like tweets relative to posting. Bots rarely engage. Values $> 0.5$ strongly indicate human behavior.

---

### 3. Account Age (Exponential Decay)

$$
A_{age} = 1 - e^{-\frac{\text{days\_since\_creation}}{365}}
$$

Smoothly rewards older accounts:
- 1 year → 0.63
- 2 years → 0.86
- 3 years → 0.95

---

### 4. Activity Rate (Normalized)

$$
A_{activity} = \frac{\text{statuses\_count}}{\text{days\_since\_creation} + 1}
$$

**Activity Score** (penalizes extremes):
$$
S_{activity} = \begin{cases}
\frac{A_{activity}}{5} & \text{if } A_{activity} \leq 5 \\
1 - \frac{\min(A_{activity}, 100) - 5}{95} \cdot 0.5 & \text{if } A_{activity} > 5
\end{cases}
$$

Normal: 0.5-10 tweets/day. Suspicious: $>50$/day (bot) or $<0.01$ (dormant).

---

### 5. List Credibility (Tanh Normalized)

$$
R_{list} = \tanh\left(\frac{\text{listed\_count}}{50}\right)
$$

Being added to lists indicates human curation. Uses $\tanh$ for smooth saturation:
- 10 lists → 0.20
- 50 lists → 0.76
- 100+ lists → 0.96

---

### 6. Media Ratio

$$
R_{media} = \min\left(1, \frac{\text{media\_count}}{\text{statuses\_count} + 1}\right)
$$

Proportion of tweets with media. Creators typically $>0.3$.

---

### 7. Profile Customization Score

$$
P_{custom} = \frac{(1 - \text{default\_profile}) + (1 - \text{default\_profile\_image})}{2}
$$

Binary: 0, 0.5, or 1. Penalizes default profiles/images.

---

### 8. Content Safety

$$
P_{safe} = 1 - 0.3 \cdot \text{possibly\_sensitive}
$$

Small penalty for NSFW-flagged accounts.

---

### 9. Verification Bonus

$$
P_{verified} = 0.15 \cdot \text{is\_blue\_verified}
$$

Small bonus for verified accounts (blue check).

---

## Classification Scores

### Sigmoid Helper

$$
\sigma(x) = \frac{1}{1 + e^{-x}}
$$

---

### Bot Score

$$
S_{bot} = \sigma\left( -3 + 3 \cdot S_{hyperactive} + 2 \cdot S_{no\_engage} + 1.5 \cdot S_{unbalanced} + 1.5 \cdot (1 - P_{custom}) + 1 \cdot S_{new} \right)
$$

Where (using smooth approximations):
$$
S_{hyperactive} = \sigma(0.1 \cdot (A_{activity} - 50))
$$
$$
S_{no\_engage} = \sigma(5 \cdot (0.1 - R_{eng}))
$$
$$
S_{unbalanced} = \sigma(5 \cdot (-1.5 - R_{ff}))
$$
$$
S_{new} = \sigma(10 \cdot (0.1 - A_{age}))
$$

---

### Person Score

$$
S_{person} = 0.20 \cdot P_{custom} + 0.25 \cdot R_{eng} + 0.15 \cdot A_{age} + 0.10 \cdot P_{safe} + 0.15 \cdot S_{balanced} + 0.15 \cdot S_{normal\_activity}
$$

Where:
$$
S_{balanced} = 1 - 2 \cdot |\ \hat{R}_{ff} - 0.4\ |
$$
(Peaks when $R_{ff} \approx 0$, i.e., balanced followers/following)

$$
S_{normal\_activity} = \begin{cases}
\frac{A_{activity}}{5} & \text{if } A_{activity} \leq 5 \\
\max(0, 1 - \frac{A_{activity} - 5}{15}) & \text{if } A_{activity} > 5
\end{cases}
$$

---

### Creator Score

$$
S_{creator} = \sigma\left( -2.5 + 1.5 \cdot \sigma(R_{ff} - 1) + 1.2 \cdot R_{media} + 0.8 \cdot R_{list} + 0.5 \cdot P_{verified} + 0.8 \cdot S_{large\_audience} \right)
$$

Where:
$$
S_{large\_audience} = \sigma(0.0003 \cdot (\text{followers\_count} - 10000))
$$

---

### Entity Score

$$
S_{entity} = \sigma\left( -2.5 + 1.2 \cdot \sigma(R_{ff} - 1.7) + 0.8 \cdot (1 - R_{eng}) + 0.6 \cdot R_{media} + 0.5 \cdot P_{verified} + 0.8 \cdot S_{consistent} \right)
$$

Where:
$$
S_{consistent} = 1 - |\ A_{activity} - 3\ | / 10
$$
(Peaks around 3 tweets/day, typical for organizations)

---

## Penalty Modifiers

Apply multiplicative penalties for clear red flags:

$$
\text{penalty} = \prod_{i} (1 - p_i)
$$

| Condition | Penalty $p_i$ |
|-----------|---------------|
| `followers_count < 5` | 0.30 |
| `statuses_count == 0` | 0.50 |
| `days_since_creation < 30` | 0.20 |
| `friends_count > 5000` AND `followers_count < 100` | 0.35 |

---

## Final Classification

### Category Selection

$$
\text{likely\_is} = \arg\max(S_{person}, S_{creator}, S_{entity}, S_{bot})
$$

With threshold rules:
1. **Bot**: $S_{bot} > 0.65$
2. **Entity**: $S_{entity} > 0.55$ AND $S_{bot} < 0.5$
3. **Creator**: $S_{creator} > 0.55$ AND $S_{entity} < 0.5$ AND $S_{bot} < 0.5$
4. **Person**: $S_{person} > 0.45$ AND above fail
5. **Other**: None of the above

Tie-breaking priority: Person > Creator > Entity > Bot > Other

---

### Final Score (Human Authenticity)

$$
\text{HAS} = \begin{cases}
S_{person} \cdot \text{penalty} & \text{if likely\_is} = \text{Person} \\
S_{creator} \cdot \text{penalty} & \text{if likely\_is} = \text{Creator} \\
(1 - S_{entity}) \cdot \text{penalty} & \text{if likely\_is} = \text{Entity} \\
(1 - S_{bot}) \cdot \text{penalty} & \text{if likely\_is} = \text{Bot} \\
0.5 \cdot \text{penalty} & \text{otherwise}
\end{cases}
$$

---

## Score Interpretation

| Score Range | Classification | Recommended Action |
|-------------|----------------|-------------------|
| 0.00 - 0.25 | Likely Bot/Spam | Discard |
| 0.25 - 0.45 | Suspicious | Manual review |
| 0.45 - 0.65 | Uncertain | Include with caution |
| 0.65 - 0.85 | Likely Human | Include |
| 0.85 - 1.00 | Confident Human | High priority |

---

## Rationale Summary

| Signal | Person | Creator | Entity | Bot |
|--------|--------|---------|--------|-----|
| $R_{ff}$ (log) | -0.5 to 1 | 1 to 2 | 1.5+ | < -1.5 |
| $R_{eng}$ | > 0.5 | 0.2 - 1 | < 0.3 | < 0.1 |
| $A_{activity}$ | 0.5 - 10 | 1 - 20 | 1 - 5 | > 50 |
| $P_{custom}$ | High | High | High | Low |
| $A_{age}$ | > 0.63 | Any | > 0.86 | < 0.3 |
| $R_{list}$ | Low-Med | High | High | Very Low |

---

## Advantages of Hybrid Approach

1. **Smooth degradation**: Log, tanh, and sigmoid functions avoid harsh cutoffs
2. **Multi-class output**: Distinguishes Person/Creator/Entity/Bot
3. **Purely numeric**: No string/keyword analysis required
4. **Penalty system**: Catches obvious red flags without affecting edge cases
5. **Interpretable**: Each component has clear meaning
