# Learning Loop: How Skills Improve Through Feedback

Skills don't stay static. They learn from user feedback and improve their configuration automatically — no code changes, no restarts.

![Learning Loop](docs/assets/learning-loop.svg)

---

## The Improvement Cycle

### Week 1: Skill Deployed
```
Skill: os.vibe_engineering v1.0
Config: {"confidence_threshold": 0.70}
Performance: Many false negatives (accuracy 60%)
```

### User Gives Feedback
```
User: "The routing was too aggressive. Too many high-priority assignments."
Feedback Type: outcome_feedback
Signal: "no" (decision was wrong)
```

### Optimizer Reads Feedback
```
Optimizer: "os.vibe_engineering has 5 'no' feedbacks today"
Optimizer: "Current threshold: 0.70"
Optimizer: "Hypothesis: Lower the threshold to 0.65?"
```

### Config Adjusted
```
Old Config: {"confidence_threshold": 0.70}
New Config: {"confidence_threshold": 0.65}
No code changes. No restart. Just applied.
```

### Week 2: Same Skill, Better
```
Skill: os.vibe_engineering v1.0 (same version!)
Config: {"confidence_threshold": 0.65}
Performance: More accurate (accuracy 87%)
Confidence Score: 0.60 → 0.87
```

---

## Feedback Types (ADR-0314)

![Feedback Types Matrix](docs/assets/feedback-types-matrix.svg)

### 1. Outcome Feedback
```python
Signal: yes/no/other
Meaning: Was the decision correct?

registry.execute("os.delegation_router", input)
# User later rates: "That routing was correct" → yes
# OR "That routing was wrong" → no
```

### 2. Preference Feedback
```python
Signal: "LLM" / "deterministic" / "neither"
Meaning: What style does user prefer for next time?

User preference: "I prefer deterministic algorithms"
→ Optimizer deprioritizes LLM-based Skills
```

### 3. Confidence Score
```python
Signal: 0.0–1.0
Meaning: P(Skill makes correct decision | this input)

P(correct | complexity>10) = 0.92
P(correct | complexity<5) = 0.71
→ Optimizer: adjust threshold
```

### 4. Metric Observed
```python
Signal: latency_ms, error_rate, cost
Meaning: Performance metrics

latency: 450ms (too slow)
error_rate: 0.05 (acceptable)
cost: $0.12 per call
→ Optimizer: prioritize low-latency Skill
```

---

## The Optimizer

The Optimizer is a meta-Skill that:
1. **Reads** all feedback for a Skill (daily)
2. **Analyzes** patterns (confidence score, outcome accuracy)
3. **Proposes** config changes (lower threshold? increase cache size?)
4. **Tests** (A/B split, canary deployment)
5. **Applies** (if metrics improve)

```python
Optimizer Algorithm (pseudocode):

for feedback in daily_feedback(skill_id):
    if feedback.confidence < 0.60:
        # Low confidence: aggressive tuning
        param_delta = calculate_aggressive_delta(feedback)
    elif feedback.confidence > 0.90:
        # High confidence: conservative tuning
        param_delta = calculate_conservative_delta(feedback)
    else:
        # Medium confidence: normal tuning
        param_delta = calculate_normal_delta(feedback)
    
    apply_config_delta(skill_id, param_delta)
    emit_audit_event("SKILL_CONFIG_UPDATED", {
        "skill_id": skill_id,
        "param_delta": param_delta,
        "confidence_before": old_confidence,
        "confidence_after": new_confidence,
    })
```

---

## Convergence Pattern

![Convergence Pattern](docs/assets/convergence-pattern.svg)

Most Skills follow an **S-curve** pattern:

```
Week 1-2: Slow start (exploring parameter space)
Week 3-4: Rapid improvement (found good region)
Week 5+: Plateau (converged to local optimum)

Typical confidence progression:
  Week 1: 0.60
  Week 2: 0.68
  Week 3: 0.79
  Week 4: 0.88
  Week 5: 0.91
  Week 6: 0.92 (plateau reached)
```

---

## Safety Guardrails

### 1. Conservative Updates
```
Max param change per iteration: 10%
Max config change per day: 50%
Prevents wild swings from bad data
```

### 2. Rollback on Regression
```
if new_confidence < old_confidence - 0.05:
    revert_config()
    emit_event("ROLLBACK_ON_REGRESSION")
```

### 3. Feedback Validation
```
Reject feedback with:
- Contradictory signals (high confidence + outcome_feedback="no")
- Stale feedback (> 7 days old)
- Out-of-range metrics (latency < 0)
```

---

## Real-World Example

### Scenario: Routing Skill

**v1.0 Initial Config:**
```python
{
    "complexity_threshold": 0.70,
    "urgency_boost": 1.2,
    "cache_ttl": 300
}
```

**User Feedback Pattern (Week 1):**
```
Request 1: complexity=0.72 → routed to "opus" → User: "Too expensive"
Request 2: complexity=0.69 → routed to "haiku" → User: "Too slow"
Request 3: complexity=0.71 → routed to "opus" → User: "Good choice"
...

Aggregated: 40% negative on high-complexity requests
```

**Optimizer Analysis:**
```
Root cause: Threshold 0.70 too aggressive on boundary cases
Hypothesis: Increase to 0.72 (more conservative)
```

**v1.0 Updated Config (Week 2):**
```python
{
    "complexity_threshold": 0.72,    # ← Changed
    "urgency_boost": 1.2,
    "cache_ttl": 300
}
```

**Result:**
```
Confidence: 0.60 → 0.85
Feedback improved: 40% negative → 5% negative
Same version, better behavior
```

---

## Monitoring & Debugging

### Check Skill Confidence
```bash
corvin skills confidence os.vibe_engineering
# Output:
# Current: 0.87
# Trend: ↑ (improving)
# Iterations: 4
# Converged: yes
```

### View Feedback History
```bash
corvin skills feedback os.vibe_engineering --limit 20
# Output:
# timestamp          | type      | signal | confidence
# 2026-09-02 14:30   | outcome   | no     | 0.60
# 2026-09-02 15:45   | outcome   | yes    | 0.75
# 2026-09-02 16:20   | metric    | latency=450ms | 0.68
```

### Trace Optimization History
```bash
corvin audit trace skill os.vibe_engineering --since 7d
# Shows: SKILL_CONFIG_UPDATED events + deltas applied
```

---

## FAQ

**Q: How often does the optimizer run?**  
A: Daily. Feedback is batched and analyzed once per day (configurable).

**Q: Can the optimizer break things?**  
A: No. Conservative guardrails + rollback on regression prevent harm.

**Q: What if I disagree with the optimizer's change?**  
A: Revert: `corvin skills rollback os.vibe_engineering --to 0.1`. Operator has final say.

**Q: How long until a Skill converges?**  
A: Typical S-curve: 4–6 weeks to 90%+ confidence. Depends on feedback volume.

**Q: Can multiple Skills learn simultaneously?**  
A: Yes! Each Skill's optimizer runs independently. No conflicts.

---

## Next Steps

- **[Audit Trail](audit-trail.md)** — How feedback is logged
- **[Skills API Reference](skills-api-reference.md)** — `get_confidence()` API
- **[Deployment Guide](deployment-guide.md)** — Canary rollout watches confidence metrics
