---
id: ADR-0383
status: PROPOSED
depends_on: [ADR-0314, ADR-0359]
relates_to: [ADR-0384, ADR-0385, ADR-0386]
paths:
  - core/learning/operator_fingerprint.py
  - core/learning/affinity_model.py
  - core/learning/preference_inference.py
docs:
  - docs/v0.6-design/V0.6_IDEAS.md
  - docs/claude-ref/layer-28-learning.md
---

# ADR-0383: Operator Fingerprint Data Model

## Problem Statement

CorvinOS has no model of *who* the operator is. Every operator receives identical guidance, despite massive variation in:
- **Risk tolerance:** Some operators are cautious; others are aggressive
- **Speed preference:** Some want thorough analysis; others want quick decisions
- **Communication style:** Some prefer formal documentation; others prefer casual notes
- **Task strengths:** Some excel at authentication bugs; others at memory management
- **Tool trust:** Some trust external APIs; others prefer internal implementations

**Current gap:** The system treats all operators identically, missing opportunities to personalize guidance based on demonstrated behavior.

## Solution Overview

Implement a **4-dimensional operator fingerprint** inferred from audit trail and decision history:

```python
@dataclass(frozen=True)
class OperatorFingerprint:
    """Immutable operator style model."""
    
    # Primary dimensions (0.0 .. 1.0)
    risk_tolerance: float            # cautious (0.0) → aggressive (1.0)
    speed_preference: float          # thorough (0.0) → quick (1.0)
    communication_style: float       # formal (0.0) → casual (1.0)
    
    # Per-task-type affinity
    task_affinities: Dict[str, TaskAffinity]
    
    # Tool trust profile
    tool_trust: Dict[str, float]
    
    # Metadata
    sample_count: int                # decisions used to infer fingerprint
    confidence: float                # [0.0 .. 1.0] based on sample size
    last_updated: datetime
    operator_id: str
    tenant_id: str
```

## Design Details

### 1. Fingerprint Dimensions

#### Risk Tolerance (0.0 .. 1.0)

**Measurement:** Frequency of bold decisions vs. available choices.

```
risk_tolerance = (bold_choices_taken) / (bold_choices_available)
```

**Bold choice definition:** Any option that:
- Has >20% estimated failure risk, AND
- Could produce significant side effects (external API calls, data mutations), AND
- Operator explicitly selected despite availability of safer options

**Example:**
- Operator presented: Option A (safe, slow), Option B (risky, fast)
- Operator chose: Option B three times out of five
- **Risk tolerance:** 0.60 (moderately aggressive)

**Stability:** Recalculated every 10 decisions; exponential moving average (α=0.3) for smoothing

---

#### Speed Preference (0.0 .. 1.0)

**Measurement:** Decision completion time vs. baseline.

```
speed_preference = 1.0 - (avg_decision_time / baseline_decision_time)
```

Where:
- `avg_decision_time` = median time from options-presented to choice-made
- `baseline_decision_time` = 10 seconds (observed mean across all operators)

**Scale:**
- 0.0 = 5× baseline (very thorough, reads all docs)
- 0.5 = 1× baseline (normal)
- 1.0 = 0.2× baseline (very fast, snap decisions)

**Example:**
- Operator typically decides in 3 seconds
- Baseline: 10 seconds
- **Speed preference:** 1.0 - (3/10) = 0.70 (quick, but not snap)

**Clamping:** `min(1.0, max(0.0, speed_preference))` to keep [0.0 .. 1.0]

---

#### Communication Style (0.0 .. 1.0)

**Measurement:** Inferred from operator annotations and feedback.

**Feature extraction from annotations:**
- Sentence length (longer → formal)
- Technical vocabulary (jargon → formal)
- Exclamation marks (informal)
- All-caps (informal)
- Formal phrase patterns ("I recommend...", "Consider..." → formal vs. "yeah ok", "cool" → casual)

**Scoring formula:**
```
formality_score = weighted_sum([
  0.3 × (sentence_length / 20),         # long sentences = formal
  0.2 × (technical_vocab_ratio),        # technical terms = formal
  0.2 × (1 - exclamation_density),      # exclamations = informal
  0.2 × (1 - caps_density),             # caps = informal
  0.1 × (formal_phrase_score),          # phrase analysis
])
communication_style = mean(formality_scores, window=20)  # smooth over last 20 annotations
```

**Example:**
- Annotations: "Please review the authentication logic carefully" → score 0.85
- Annotations: "cool, makes sense" → score 0.15
- **Communication style:** (0.85 + 0.15) / 2 = 0.50 (neutral)

---

### 2. Task Affinity Model

```python
@dataclass(frozen=True)
class TaskAffinity:
    """Per-task-type success model."""
    
    task_type_id: str                # e.g., "auth", "memory", "schema"
    domain_boundary: str             # e.g., "authentication", "memory_mgmt"
    
    # Success metrics
    success_rate: float              # [0.0 .. 1.0]
    sample_count: int                # how many turns of this type
    confidence: float                # [0.0 .. 1.0], function of sample_count
    
    # Trend analysis
    success_trend: float             # slope of success rate over time
    last_practiced: datetime         # when operator last worked on this type
    
    # Categorization
    strength_tier: Literal["strong", "neutral", "weak"]  # Based on success_rate
    
    # Bayesian prior
    prior_strength: float            # [0.0 .. 1.0] based on task difficulty
```

**Measurement:**

```python
def update_task_affinity(
    task_type_id: str,
    outcome: Literal["success", "failure", "unclear"],
) -> TaskAffinity:
    """Bayesian update of affinity on turn outcome."""
    
    affinity = load_affinity(task_type_id)
    
    # Bayesian update
    if outcome == "success":
        success_count += 1
    elif outcome == "failure":
        failure_count += 1
    # else: unclear → no update
    
    # Recalculate metrics
    sample_count = success_count + failure_count
    success_rate = success_count / sample_count if sample_count > 0 else 0.5
    
    # Confidence: saturation at n=30
    confidence = min(1.0, sample_count / 30.0)
    
    # Strength tier
    if success_rate >= 0.75:
        strength_tier = "strong"
    elif success_rate >= 0.45:
        strength_tier = "neutral"
    else:
        strength_tier = "weak"
    
    return TaskAffinity(
        task_type_id=task_type_id,
        domain_boundary=TASK_TYPE_TO_DOMAIN[task_type_id],
        success_rate=success_rate,
        sample_count=sample_count,
        confidence=confidence,
        success_trend=compute_trend(task_type_id),
        last_practiced=now(),
        strength_tier=strength_tier,
        prior_strength=TASK_TYPE_DIFFICULTY_PRIOR[task_type_id],
    )
```

**Strength tier rules:**
- **Strong** (success_rate ≥ 0.75): High likelihood of success on new tasks of this type
- **Neutral** (0.45 ≤ success_rate < 0.75): Average performance, may need guidance
- **Weak** (success_rate < 0.45): Likely to struggle, should prioritize for improvement

---

### 3. Tool Trust Profile

```python
tool_trust = {
    "claude_api": 0.88,              # high trust in Claude API calls
    "external_sql": 0.42,            # low trust in external databases
    "python_exec": 0.95,             # very high trust in local Python
    "bash_exec": 0.72,               # moderate trust in shell commands
    "http_fetch": 0.65,              # moderate trust in HTTP
}
```

**Measurement:** Inferred from choice patterns when multiple tools available for same task.

```python
def infer_tool_trust(tool_name: str) -> float:
    """
    Measure operator's implicit trust via choice frequency.
    
    If operator has 5 opportunities to use tool X vs. tool Y (equivalent results),
    and chooses tool X 4/5 times, tool_trust[X] increases.
    """
    
    total_choices = count_choices_where_tool_available(tool_name)
    times_chosen = count_choices_where_tool_selected(tool_name)
    
    if total_choices == 0:
        return 0.5  # unknown, neutral default
    
    # Bayesian: prior is 0.5 (neutral), update toward observed frequency
    trust = times_chosen / total_choices
    
    # Smooth: exponential moving average toward baseline
    # Prevent one-off skews
    baseline_trust = 0.5
    alpha = 0.1  # slow adaptation
    smoothed = baseline_trust + alpha * (trust - baseline_trust)
    
    return clamp(smoothed, 0.0, 1.0)
```

---

### 4. Sample Count & Confidence

**Minimum threshold for fingerprinting:** 50 decisions per dimension to publish fingerprint.

```python
@property
def is_publishable(self) -> bool:
    """Fingerprint has sufficient data."""
    return (
        self.sample_count >= 50 and
        all(aff.sample_count >= 5 for aff in self.task_affinities.values())
    )
```

**Confidence metric:** Inverse of uncertainty.

```python
def confidence_score(sample_count: int) -> float:
    """
    Confidence as function of sample size.
    
    - 10 samples: confidence 0.33
    - 30 samples: confidence 1.0 (saturate)
    """
    return min(1.0, sample_count / 30.0)
```

---

### 5. Storage & Persistence

**Location:** `~/.corvin/tenants/{tenant_id}/learning/fingerprints/{operator_id}.json`

```json
{
  "operator_id": "op-12345",
  "tenant_id": "_default",
  "fingerprint": {
    "risk_tolerance": 0.62,
    "speed_preference": 0.71,
    "communication_style": 0.50,
    "sample_count": 87,
    "confidence": 0.94,
    "last_updated": "2026-09-05T14:23:00Z"
  },
  "task_affinities": {
    "auth": {
      "task_type_id": "auth",
      "domain_boundary": "authentication",
      "success_rate": 0.87,
      "sample_count": 35,
      "confidence": 0.89,
      "strength_tier": "strong",
      "last_practiced": "2026-09-05T14:23:00Z"
    },
    "memory": {
      "task_type_id": "memory",
      "success_rate": 0.62,
      "sample_count": 12,
      "confidence": 0.40,
      "strength_tier": "neutral",
      "last_practiced": "2026-09-04T10:00:00Z"
    }
  },
  "tool_trust": {
    "claude_api": 0.88,
    "external_sql": 0.42,
    "python_exec": 0.95,
    "bash_exec": 0.72,
    "http_fetch": 0.65
  }
}
```

**Permissions:** 0600 (read/write for operator only)

**Rotation:** Updated every 10 decisions, saved to disk atomically

---

## GDPR Compliance

**Art. 5 (Lawfulness):** Fingerprinting is based on lawful processing of operator's own behavior (their own decisions). No profiling of third parties or sensitive characteristics (race, religion, political views).

**Art. 6 (Legal Basis):** Fingerprinting falls under:
- Art. 6(1)(b) Contract fulfillment (necessary for personalized guidance)
- Art. 6(1)(a) Explicit consent (via bot disclosure + Settings opt-out)

**Art. 30/32 (Records & Security):**
- Fingerprints stored locally only
- Encrypted at rest (AES-256)
- Hash-chained in audit trail (every fingerprint update is an audit event)
- Audit trail signed with operator's GPG key

**Art. 17 (Right to Erasure):**
- Operator can request deletion: triggers purge of fingerprint + all decision history
- Immediate effect, verified with operator

**Data Minimization:** Only collect data necessary for personalized guidance:
- Decision choices (which option selected)
- Decision outcomes (success/failure)
- Operator annotations (reason, ≤200 chars)
- Aggregate metrics (no PII, no prompts/responses)

**Privacy by Design:**
- All inference local (no server transmission)
- Fingerprint never leaves operator's device without explicit consent
- No enrichment from external data sources

---

## Security Considerations

### 1. No Profiling of Sensitive Categories

**Invariant:** Fingerprint must never infer protected characteristics (race, religion, political views, health, etc.).

**Implementation:** Feature set is purely behavioral (decision time, choice patterns, task success). No demographic inference allowed.

**Validation:** `_assert_safe` check on every fingerprint export—raise if any feature could correlate with sensitive category.

---

### 2. Snapshot Isolation

**Fingerprints are operator-specific, tenant-isolated.**

```python
def load_fingerprint(operator_id: str, tenant_id: str) -> OperatorFingerprint:
    """Load only this operator's fingerprint for this tenant."""
    
    # Never cross-tenant, never cross-operator
    assert operator_id is not None and tenant_id is not None
    path = f"~/.corvin/tenants/{tenant_id}/learning/fingerprints/{operator_id}.json"
    return OperatorFingerprint.from_json(read_encrypted(path))
```

---

## Testing Strategy

### Unit Tests (40 tests)
- Fingerprint dimension calculations (10 tests)
  - Risk tolerance edge cases (all-safe, all-bold choices)
  - Speed preference normalization (clamping, smoothing)
  - Communication style feature extraction
- Task affinity updates (15 tests)
  - Bayesian update correctness
  - Confidence saturation at n=30
  - Strength tier classification
  - Trend calculation
- Tool trust inference (10 tests)
  - Trust score with 0/1/many samples
  - Smoothing behavior
  - Equivalence detection
- Serialization (5 tests)
  - JSON round-trip
  - Encryption/decryption
  - Version migration

### Integration Tests (15 tests)
- Fingerprint persistence (5 tests)
  - Save/load from disk
  - Atomic updates
  - Rotation on every 10 decisions
- Decision audit integration (5 tests)
  - Decision choices captured
  - Outcomes recorded
  - Annotations stored
- Privacy checks (5 tests)
  - No PII in serialized fingerprint
  - Encryption verified
  - Audit trail contains fingerprint updates

### E2E Tests (8 tests)
- Real operator makes 50+ decisions → fingerprint published
- Affinity updates on turn outcomes
- Console API returns correct fingerprint
- Operator can view/export fingerprint
- Operator deletion → fingerprint purged

---

## Rollback

If fingerprint model is inaccurate:

1. **Set feature flag to OFF:**
   ```yaml
   spec.features.operator_modeling_fingerprinting: false
   ```

2. **Clear fingerprints:**
   ```bash
   find ~/.corvin/tenants/*/learning/fingerprints -name "*.json" -delete
   ```

3. **Restore v0.5 behavior:** No personalized guidance, system treats all operators identically

4. **Timeline:** <1 minute (instant flag disable, <10 sec cleanup)

---

## Dependencies

- **ADR-0314 (Learning Infrastructure):** Event schema, persistence, emission
- **ADR-0359 (Decision History):** Operator's decision audit trail
- **ADR-0384:** Task Affinity Measurement (extends this model)
- **ADR-0385:** Predictive Guidance Engine (consumes fingerprint)

---

## Future Work

- ADR-0387 (Operator Modeling Phase 2): Preference hierarchy (which dimensions matter most to this operator?)
- ADR-0388: Fairness audit (ensure fingerprinting doesn't discriminate)
- ADR-0389: Multi-agent fingerprints (team operators sharing a fingerprint)

