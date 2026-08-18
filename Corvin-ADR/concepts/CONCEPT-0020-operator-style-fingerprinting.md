---
kind: concept
id: CONCEPT-0020
status: PROPOSED
supersedes: []
depends_on: [ADR-0383, ADR-0384]
related: [ADR-0385, ADR-0386]
skills: []
commits: []
paths:
  - core/learning/operator_fingerprint.py
  - core/learning/affinity_model.py
docs:
  - docs/v0.6-design/V0.6_IDEAS.md
---

# CONCEPT-0020: Operator Style Fingerprinting

## The Problem: No Operator Context

CorvinOS treats every operator identically. But operators are different:
- **Alice:** Risk-averse, wants thorough analysis, formal tone
- **Bob:** Aggressive, wants quick decisions, casual tone
- **Charlie:** Novice at auth, expert at performance tuning

**Cost of not knowing:** Generic guidance misses the mark. Alice gets suggestions that are too risky; Bob gets suggestions that are too slow. Neither gets help in their weak areas.

## The Idea: Operator Model from Behavior

**Fingerprinting** = inferring operator style from their decision history.

A **fingerprint** is a 4-dimensional model:

```
fingerprint = {
  risk_tolerance: 0.62,      # 62% → moderately aggressive
  speed_preference: 0.71,    # 71% → prefers quick decisions
  communication_style: 0.50, # 50% → neutral (formal/casual)
  task_affinities: {
    "auth": 0.87,     # 87% success → strong
    "memory": 0.62,   # 62% success → neutral
    "schema": 0.35,   # 35% success → weak
  }
}
```

## How It Works

### Step 1: Collect Decision Data

Every time an operator makes a choice, capture:
- **The options** presented
- **The chosen option**
- **The outcome** (success / failure)
- **Operator notes** ("why I chose this")

### Step 2: Infer Dimensions

From this data, compute:

**Risk tolerance:**
```
risk_tolerance = (times_chose_bold_option) / (bold_options_available)
```

If operator chose risky options 6 out of 10 times: risk_tolerance = 0.60.

**Speed preference:**
```
speed_preference = 1.0 - (avg_decision_time / baseline_time)
```

If operator decides in 3 seconds, baseline is 10 seconds:
speed_preference = 1.0 - (3/10) = 0.70 (quick).

**Communication style:**
```
Infer from operator annotations:
- Long sentences, technical jargon → formal (0.8)
- Short messages, casual language → casual (0.2)
- Average → neutral (0.5)
```

**Task affinities:**
```
For each task type (auth, memory, performance, etc.):
  affinity = (success_count) / (success_count + failure_count)
```

### Step 3: Publish Fingerprint

After ≥50 decisions, fingerprint is published and available for:
- Personalized guidance
- Skill recommendations
- What-If counterfactual analysis

## Measurement: Key Equations

### Dimension Formulas

**Risk Tolerance:**
```
risk_tolerance = (bold_choices_taken) / (bold_choices_available)

bold_choice = option with >20% failure risk AND significant side effects
```

**Speed Preference:**
```
speed_preference = 1.0 - (median_decision_time / 10_seconds)
Clamp to [0.0, 1.0]
```

**Communication Style:**
```
formality_score = weighted_average([
  0.3 × (sentence_length / 20),       # longer = more formal
  0.2 × (technical_vocab_ratio),      # more jargon = more formal
  0.2 × (1 - exclamation_ratio),      # more ! = more casual
  0.2 × (1 - caps_ratio),             # more CAPS = more casual
  0.1 × (formal_phrase_score),        # "I recommend" = formal
])
Smooth over last 20 annotations with moving average.
```

**Task Affinity:**
```
affinity = (success_count) / (success_count + failure_count)
confidence = min(1.0, sample_count / 30)

strength_tier = {
  if affinity >= 0.75: "strong",
  elif affinity >= 0.45: "neutral",
  else: "weak"
}
```

## Stability & Convergence

### When is a Fingerprint Reliable?

**Minimum sample size:** 50 decisions total

**Dimension stability:** Fingerprint is stable when standard deviation across 5 consecutive 10-decision windows is <0.10.

**Example:** If risk_tolerance estimates across 5 windows are [0.60, 0.62, 0.59, 0.61, 0.60], σ = 0.01 < 0.10 ✓ (stable).

### Confidence Saturation

```
confidence(n_samples) = min(1.0, n_samples / 30)

n=10  → confidence 0.33 (uncertain)
n=30  → confidence 1.0  (saturated, no more gain)
n=100 → confidence 1.0  (no additional confidence from more data)
```

**Rationale:** 30 samples = ~95% CI for Bernoulli outcomes at p=0.5. Beyond that, law of large numbers gives diminishing returns.

## Stability Testing: Real Example

**Operator "Alice"** (100 decisions collected):

**Window 1 (decisions 1-10):**
- Risk: 0.30 (chose risky 3/10)
- Speed: 0.80 (decided in 2s avg)
- Communication: 0.65 (formal notes)
- Affinity.auth: 0.40 (4/10 success)

**Window 2 (decisions 11-20):**
- Risk: 0.32 (chose risky 3/10)
- Speed: 0.82
- Communication: 0.68
- Affinity.auth: 0.45 (5/11 success)

**Window 3 (decisions 21-30):**
- Risk: 0.28
- Speed: 0.79
- Communication: 0.62
- Affinity.auth: 0.48 (6/12 success)

**... (Windows 4-5 similar)**

**σ(risk estimates) = [0.30, 0.32, 0.28, 0.29, 0.31] → σ = 0.015 < 0.10** ✓

**Conclusion:** Alice's fingerprint is stable after ~50 decisions.

## Privacy by Design

### What's Collected?

- Decision choices (which option)
- Decision outcomes (did it work)
- Operator annotations (why I chose this)
- Aggregate metrics (risk_tolerance, affinity percentages)

### What's NOT Collected?

- Prompt content
- Response content
- Conversation history
- User data from turns
- Demographic information
- External data (never enriched from web/APIs)

### Storage

- **Location:** Operator's local disk (~/.corvin/tenants/{tenant_id}/...)
- **Encryption:** AES-256 at rest
- **Transmission:** Never sent to server without explicit consent
- **Retention:** 365 days (auto-purged after 1 year if inactive)
- **Permissions:** 0600 (read/write for operator only)

## Failure Modes & Mitigations

| Failure Mode | Risk | Mitigation |
|---|---|---|
| **Fingerprint inaccurate** | Medium | Calibration phase (Ph4), benchmarks before release |
| **Overfitting to recent decisions** | Low | Exponential moving average (α=0.3) smooths noise |
| **PII leakage in exports** | Low | `_assert_safe` validation on serialization |
| **Model atrophies if operator disables** | Low | Graceful degradation, can re-enable anytime |
| **Cross-operator contamination** | Low | Tenant + operator scoping enforced everywhere |

## Operator Controls

**Settings → Learning → Operator Modeling:**

```
[x] Infer my operator fingerprint
    Helps personalize guidance and suggestions.
    Privacy: Computed locally, never leaves your device.

[x] Show me my fingerprint metrics
    See your risk tolerance, speed preference, task strengths.

[ ] Enable predictive task suggestions
    CorvinOS suggests next task based on history.
    Improves with more decisions (>50 needed).

[ ] Enable What-If counterfactual replay
    Explore decisions: "What if I chose option B?"

[!] Delete all fingerprint data
    Removes your operator model & decision history.
    Action is irreversible.
```

## Operator Notes

(Append-only section for operator feedback on this concept)

---

## Concept Outcome Criteria

**Success = Fingerprint stable within ±0.1 after 50 decisions per dimension.**

Measured during Phase 2 (v0.6 Week 3-4) with ≥5 operators on beta.

