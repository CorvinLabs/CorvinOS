# The Learning Loop: How Skills Improve

Every Skill in CorvinOS executes, receives feedback, and improves. This document explains the learning loop, feedback types, convergence tracking, and real examples.

---

## The Learning Loop: An S-Curve

```svg
<svg viewBox="0 0 900 600" xmlns="http://www.w3.org/2000/svg">
  <!-- Background -->
  <rect width="900" height="600" fill="#F9FAFB"/>
  
  <!-- Title -->
  <text x="450" y="30" font-size="22" font-weight="bold" text-anchor="middle" fill="#1F2937">
    Learning Loop: Confidence Convergence Over Time (S-Curve)
  </text>
  
  <!-- Axes -->
  <line x1="100" y1="500" x2="800" y2="500" stroke="#1F2937" stroke-width="2"/>
  <line x1="100" y1="500" x2="100" y2="80" stroke="#1F2937" stroke-width="2"/>
  
  <!-- Axis labels -->
  <text x="820" y="505" font-size="12" fill="#1F2937">Weeks</text>
  <text x="70" y="65" font-size="12" fill="#1F2937">Confidence %</text>
  
  <!-- Grid lines -->
  <line x1="100" y1="450" x2="800" y2="450" stroke="#E5E7EB" stroke-width="1" stroke-dasharray="3,3"/>
  <line x1="100" y1="400" x2="800" y2="400" stroke="#E5E7EB" stroke-width="1" stroke-dasharray="3,3"/>
  <line x1="100" y1="350" x2="800" y2="350" stroke="#E5E7EB" stroke-width="1" stroke-dasharray="3,3"/>
  <line x1="100" y1="300" x2="800" y2="300" stroke="#E5E7EB" stroke-width="1" stroke-dasharray="3,3"/>
  <line x1="100" y1="250" x2="800" y2="250" stroke="#E5E7EB" stroke-width="1" stroke-dasharray="3,3"/>
  <line x1="100" y1="200" x2="800" y2="200" stroke="#E5E7EB" stroke-width="1" stroke-dasharray="3,3"/>
  <line x1="100" y1="150" x2="800" y2="150" stroke="#E5E7EB" stroke-width="1" stroke-dasharray="3,3"/>
  
  <!-- Y-axis labels -->
  <text x="85" y="505" font-size="10" text-anchor="end" fill="#6B7280">0%</text>
  <text x="85" y="455" font-size="10" text-anchor="end" fill="#6B7280">20%</text>
  <text x="85" y="405" font-size="10" text-anchor="end" fill="#6B7280">40%</text>
  <text x="85" y="355" font-size="10" text-anchor="end" fill="#6B7280">60%</text>
  <text x="85" y="305" font-size="10" text-anchor="end" fill="#6B7280">80%</text>
  <text x="85" y="155" font-size="10" text-anchor="end" fill="#6B7280">100%</text>
  
  <!-- X-axis labels -->
  <text x="180" y="520" font-size="10" text-anchor="middle" fill="#6B7280">Week 1</text>
  <text x="300" y="520" font-size="10" text-anchor="middle" fill="#6B7280">Week 2</text>
  <text x="420" y="520" font-size="10" text-anchor="middle" fill="#6B7280">Week 3</text>
  <text x="540" y="520" font-size="10" text-anchor="middle" fill="#6B7280">Week 4</text>
  <text x="660" y="520" font-size="10" text-anchor="middle" fill="#6B7280">Week 5</text>
  <text x="780" y="520" font-size="10" text-anchor="middle" fill="#6B7280">Week 6</text>
  
  <!-- S-Curve (learning curve) -->
  <path d="M 180,430 Q 300,380 420,280 T 780,140" 
        fill="none" stroke="#3B82F6" stroke-width="3"/>
  
  <!-- Target line (95%) -->
  <line x1="100" y1="175" x2="800" y2="175" stroke="#10B981" stroke-width="2" stroke-dasharray="5,5"/>
  <text x="810" y="180" font-size="10" fill="#10B981">Target: 95%</text>
  
  <!-- Data points -->
  <circle cx="180" cy="430" r="4" fill="#3B82F6"/>
  <text x="180" y="450" font-size="10" text-anchor="middle" fill="#3B82F6">60%</text>
  
  <circle cx="300" cy="380" r="4" fill="#3B82F6"/>
  <text x="300" y="400" font-size="10" text-anchor="middle" fill="#3B82F6">72%</text>
  
  <circle cx="420" cy="280" r="4" fill="#3B82F6"/>
  <text x="420" y="300" font-size="10" text-anchor="middle" fill="#3B82F6">85%</text>
  
  <circle cx="540" cy="210" r="4" fill="#3B82F6"/>
  <text x="540" y="230" font-size="10" text-anchor="middle" fill="#3B82F6">92%</text>
  
  <circle cx="660" cy="160" r="4" fill="#10B981"/>
  <text x="660" y="140" font-size="10" text-anchor="middle" fill="#10B981">95% ✅</text>
  
  <!-- Annotations -->
  <g>
    <rect x="40" y="90" width="200" height="60" rx="4" fill="#DBEAFE" stroke="#3B82F6" stroke-width="1"/>
    <text x="50" y="105" font-size="10" font-weight="bold" fill="#1E40AF">Phase 1: Slow Start</text>
    <text x="50" y="120" font-size="9" fill="#1E40AF">Week 1-2: Learning basics</text>
    <text x="50" y="133" font-size="9" fill="#1E40AF">from initial feedback</text>
  </g>
  
  <g>
    <rect x="350" y="90" width="200" height="60" rx="4" fill="#FEF3C7" stroke="#F59E0B" stroke-width="1"/>
    <text x="360" y="105" font-size="10" font-weight="bold" fill="#92400E">Phase 2: Rapid Learning</text>
    <text x="360" y="120" font-size="9" fill="#92400E">Week 2-3: Parameter tuning</text>
    <text x="360" y="133" font-size="9" fill="#92400E">large confidence gains</text>
  </g>
  
  <g>
    <rect x="660" y="90" width="200" height="60" rx="4" fill="#DCFCE7" stroke="#10B981" stroke-width="1"/>
    <text x="670" y="105" font-size="10" font-weight="bold" fill="#065F46">Phase 3: Convergence</text>
    <text x="670" y="120" font-size="9" fill="#065F46">Week 4+: Fine-tuning</text>
    <text x="670" y="133" font-size="9" fill="#065F46">minimal improvements</text>
  </g>
</svg>
```

### The Three Phases

1. **Phase 1: Slow Start (Week 1)**
   - Skill loads with default parameters
   - Collects initial feedback (50–100 samples)
   - Confidence: 55–65% (learning basic behavior)

2. **Phase 2: Rapid Learning (Week 2–3)**
   - Optimizer analyzes feedback patterns
   - Adjusts parameters (e.g., threshold from 0.50 → 0.65)
   - Confidence jumps: 72% → 85%
   - Tuning is most aggressive during this phase

3. **Phase 3: Convergence (Week 4+)**
   - Fine-tuning parameters (small adjustments)
   - Confidence approaches target (92–96%)
   - Learning rate slows; optimizer makes minimal changes
   - Skill is production-ready

---

## Feedback Types

### Type 1: Outcome Feedback

"Was the Skill's decision correct?"

- **Yes:** Skill made the right decision → increase confidence
- **No:** Skill was wrong → decrease confidence, adjust parameters
- **Maybe:** Partially correct (e.g., routed to Opus but Haiku would have sufficed) → minor adjustment

**Example:**
```json
{
  "feedback_type": "outcome",
  "skill_id": "os.delegation_router",
  "task_id": "task_xyz",
  "correct": false,
  "actual_route": "sonnet"  // User says: should have routed to Sonnet, not Opus
}
```

### Type 2: Preference Feedback

"I prefer this style going forward."

- **Style:** User prefers a different approach (e.g., "more concise", "more detailed")
- **Skill adjusts:** Configuration for next time reflects preference

**Example:**
```json
{
  "feedback_type": "preference",
  "skill_id": "os.context_adapter",
  "preference": "preserve_more_context",
  "reasoning": "I need to see the full conversation history"
}
```

### Type 3: Confidence Feedback

"How confident are you in this decision?"

- User estimates P(correct decision)
- Used to calibrate learned confidence scores
- Helps identify overconfident or underconfident Skills

**Example:**
```json
{
  "feedback_type": "confidence",
  "skill_id": "os.flow_guard",
  "estimated_confidence": 0.75,
  "actual_confidence_model": 0.92,
  "mismatch": true  // Model thought it was 92% sure, user says 75%
}
```

### Type 4: Metric Feedback

"Here's the actual metric value."

- Latency, error rate, cost, user satisfaction score
- Optimizer uses metrics to tune thresholds
- E.g., "This request took 500ms, but target is 200ms"

**Example:**
```json
{
  "feedback_type": "metric",
  "skill_id": "os.workflow_optimizer",
  "metric_name": "execution_latency_ms",
  "metric_value": 450,
  "target_value": 200,
  "delta": 250
}
```

### Feedback Type Matrix

```svg
<svg viewBox="0 0 900 500" xmlns="http://www.w3.org/2000/svg">
  <!-- Background -->
  <rect width="900" height="500" fill="#F9FAFB"/>
  
  <!-- Title -->
  <text x="450" y="30" font-size="18" font-weight="bold" text-anchor="middle" fill="#1F2937">
    Feedback Types Matrix
  </text>
  
  <!-- Headers -->
  <rect x="50" y="60" width="800" height="40" fill="#F3F4F6" stroke="#D1D5DB" stroke-width="1"/>
  <text x="150" y="90" font-size="11" font-weight="bold" text-anchor="middle" fill="#1F2937">Feedback Type</text>
  <text x="300" y="90" font-size="11" font-weight="bold" text-anchor="middle" fill="#1F2937">Question</text>
  <text x="500" y="90" font-size="11" font-weight="bold" text-anchor="middle" fill="#1F2937">Values</text>
  <text x="700" y="90" font-size="11" font-weight="bold" text-anchor="middle" fill="#1F2937">Impact</text>
  
  <!-- Row 1: Outcome -->
  <rect x="50" y="100" width="800" height="50" fill="#DBEAFE" stroke="#D1D5DB" stroke-width="1"/>
  <text x="150" y="115" font-size="10" font-weight="bold" text-anchor="middle" fill="#1E40AF">Outcome</text>
  <text x="300" y="115" font-size="10" text-anchor="middle" fill="#1E40AF">Was I correct?</text>
  <text x="500" y="115" font-size="10" text-anchor="middle" fill="#1E40AF">yes, no, maybe</text>
  <text x="700" y="115" font-size="10" text-anchor="middle" fill="#1E40AF">Adjust parameters</text>
  
  <!-- Row 2: Preference -->
  <rect x="50" y="150" width="800" height="50" fill="#FEF3C7" stroke="#D1D5DB" stroke-width="1"/>
  <text x="150" y="165" font-size="10" font-weight="bold" text-anchor="middle" fill="#92400E">Preference</text>
  <text x="300" y="165" font-size="10" text-anchor="middle" fill="#92400E">What's your style?</text>
  <text x="500" y="165" font-size="10" text-anchor="middle" fill="#92400E">concise, detailed, formal</text>
  <text x="700" y="165" font-size="10" text-anchor="middle" fill="#92400E">Tune config</text>
  
  <!-- Row 3: Confidence -->
  <rect x="50" y="200" width="800" height="50" fill="#DCFCE7" stroke="#D1D5DB" stroke-width="1"/>
  <text x="150" y="215" font-size="10" font-weight="bold" text-anchor="middle" fill="#065F46">Confidence</text>
  <text x="300" y="215" font-size="10" text-anchor="middle" fill="#065F46">How sure am I?</text>
  <text x="500" y="215" font-size="10" text-anchor="middle" fill="#065F46">0.0–1.0 (probability)</text>
  <text x="700" y="215" font-size="10" text-anchor="middle" fill="#065F46">Calibrate scoring</text>
  
  <!-- Row 4: Metric -->
  <rect x="50" y="250" width="800" height="50" fill="#E0E7FF" stroke="#D1D5DB" stroke-width="1"/>
  <text x="150" y="265" font-size="10" font-weight="bold" text-anchor="middle" fill="#312E81">Metric</text>
  <text x="300" y="265" font-size="10" text-anchor="middle" fill="#312E81">What's the cost?</text>
  <text x="500" y="265" font-size="10" text-anchor="middle" fill="#312E81">latency, cost, errors</text>
  <text x="700" y="265" font-size="10" text-anchor="middle" fill="#312E81">Optimize thresholds</text>
  
  <!-- Legend -->
  <text x="50" y="340" font-size="11" font-weight="bold" fill="#1F2937">⚡ All feedback types are:</text>
  <text x="50" y="360" font-size="10" fill="#6B7280">• Logged immutably (audit trail)</text>
  <text x="50" y="375" font-size="10" fill="#6B7280">• Tenant-scoped (GDPR isolation)</text>
  <text x="50" y="390" font-size="10" fill="#6B7280">• Processed weekly by optimizer</text>
  <text x="50" y="405" font-size="10" fill="#6B7280">• Tracked for convergence</text>
  <text x="50" y="420" font-size="10" fill="#6B7280">• Non-binding (bad feedback just stalls convergence)</text>
</svg>
```

---

## The Optimizer

The **Optimizer** is a weekly process that:
1. Reads all feedback from the past week
2. Analyzes patterns
3. Adjusts Skill configuration
4. Measures convergence
5. Logs all changes (audit trail)

### What the Optimizer Tunes

Different Skills have different tunable parameters:

| Skill | Parameter | Range | Example |
|---|---|---|---|
| **os.delegation_router** | complexity_threshold | 0.0–1.0 | 0.70 → 0.65 |
| **os.context_adapter** | context_window | 5–100 turns | 20 → 25 turns |
| **os.flow_guard** | data_class_threshold | 0.0–1.0 | 0.80 → 0.75 |
| **os.workflow_optimizer** | max_skill_chain_length | 2–10 | 5 → 6 Skills |

### Optimizer Algorithm (Simplified)

```python
def optimize_skill(skill_id: str, feedback: list[dict]) -> dict:
    """
    Analyze feedback and suggest parameter changes.
    
    1. Group feedback by outcome (correct, incorrect, maybe)
    2. Find patterns (e.g., "incorrect when complexity > 0.7")
    3. Compute new parameter value (e.g., increase threshold)
    4. Measure confidence delta (before/after)
    5. Apply change (audit logged)
    """
    
    config = get_config(skill_id)
    
    # Analyze patterns
    correct_count = sum(1 for f in feedback if f["correct"])
    incorrect_count = sum(1 for f in feedback if not f["correct"])
    confidence = correct_count / len(feedback)
    
    # Compute parameter delta
    target_confidence = 0.95
    if confidence < target_confidence:
        # Adjust parameters to improve
        new_value = tune_parameter(config, feedback)
        delta = new_value - config["current_value"]
    else:
        delta = 0  # Already converged
    
    # Log the change (immutable audit event)
    audit_log({
        "event_type": "skill_config_updated",
        "skill_id": skill_id,
        "param": "complexity_threshold",
        "old_value": config["current_value"],
        "new_value": new_value,
        "confidence_before": confidence,
        "feedback_count": len(feedback)
    })
    
    return {"new_value": new_value, "confidence": confidence}
```

---

## Real Examples

### Example 1: os.delegation_router

**What it does:** Route requests to Haiku (simple) or Opus (complex).

**Learning:**

```
Week 1: Uses default threshold (0.5)
  Routed 60% correctly
  User feedback: "Too many complex requests went to Haiku"
  → Threshold too low; lower requests are more complex
  
Week 2: Threshold adjusted to 0.65
  Routed 72% correctly
  Feedback: "Still some complex→Haiku"
  → Lower threshold more
  
Week 3: Threshold 0.60
  Routed 85% correctly
  Feedback: "Better, but some simple→Opus"
  → Threshold slightly high now
  
Week 4: Threshold 0.62 (fine-tune)
  Routed 92% correctly
  Feedback: Mostly correct, tiny improvements
  → Converged at 92%
```

**Audit trail (simplified):**
```json
[
  {"event": "skill_executed", "skill_id": "os.delegation_router", "threshold": 0.50, "confidence": 0.60},
  {"event": "skill_config_updated", "param": "threshold", "old": 0.50, "new": 0.65},
  {"event": "skill_executed", "skill_id": "os.delegation_router", "threshold": 0.65, "confidence": 0.72},
  {"event": "skill_config_updated", "param": "threshold", "old": 0.65, "new": 0.60},
  {"event": "skill_executed", "skill_id": "os.delegation_router", "threshold": 0.60, "confidence": 0.85},
  {"event": "skill_config_updated", "param": "threshold", "old": 0.60, "new": 0.62},
  {"event": "skill_executed", "skill_id": "os.delegation_router", "threshold": 0.62, "confidence": 0.92}
]
```

### Example 2: os.context_adapter

**What it does:** Preserve relevant context across turns.

**Learning:**

```
Week 1: Preserves last 10 messages
  Users rate 65% useful
  Feedback: "Need earlier context"
  → Increase context window
  
Week 2: Preserves last 25 messages
  Users rate 78% useful
  Feedback: "Good, but some very old context is noise"
  → Moderate increase
  
Week 3: Preserves last 20 messages
  Users rate 87% useful
  Feedback: "Better balance"
  → Fine-tune slightly
  
Week 4: Preserves last 22 messages
  Users rate 91% useful
  → Converged
```

---

## Convergence Tracking

### Metrics

For each Skill, track:

| Metric | Target | Status |
|---|---|---|
| **Confidence** | ≥ 95% | Primary goal |
| **Feedback Sample Size** | ≥ 100/week | Sufficient signal |
| **Non-Convergence Duration** | < 2 weeks | Alert if stuck |
| **Parameter Stability** | Changes < 5%/week | Once converged |

### Dashboard

```bash
corvin skill convergence os.delegation_router
# Output:
# ┌─────────────────────────────────────────┐
# │ os.delegation_router                    │
# │ ─────────────────────────────────────── │
# │ Confidence:          92% (target: 95%)  │
# │ Samples (this week): 287                │
# │ Phase:               Convergence        │
# │ Status:              🟢 On track        │
# │ ETA to target:       ~5 days            │
# │ ─────────────────────────────────────── │
# │ Parameter (threshold): 0.62             │
# │ Changes (this week):  -0.02             │
# │ Stability:           Good               │
# └─────────────────────────────────────────┘
```

### Non-Convergence Alerts

If a Skill stalls below 80% confidence for 2+ weeks:

**Alert:** `os.flow_guard` not converging
**Reason:** Feedback contradictory (50% yes, 50% no)
**Action:** Investigate feedback quality or parameter bounds

---

## Learning Events (Audit Trail)

Every feedback event is logged immutably:

```json
{
  "tenant_id": "_default",
  "timestamp": "2026-09-02T14:30:45.123Z",
  "event_type": "learning_event_received",
  "skill_id": "os.delegation_router",
  "feedback_type": "outcome",
  "feedback_value": "no",  // User says: wrong
  "confidence_before": 0.88,
  "hash": "sha256(...)",
  "prev_hash": "sha256(...)"
}
```

Audit all learning events for a Skill:

```bash
corvin audit filter --skill os.delegation_router --event-type learning_event_received | tail -50
```

---

## Best Practices

1. **Provide frequent feedback** — 100+ samples/week per Skill for stable learning
2. **Be honest** — Feedback drives learning; false positives stall convergence
3. **Track convergence** — Monitor confidence scores; alert if stuck
4. **Investigate divergence** — If confidence drops, check feedback quality
5. **Don't tweak manually** — Let the optimizer adjust parameters; manual changes are audit-logged anyway

---

## See Also

- **[Skills System](skills-system.md)** — How Skills work
- **[ACP Vision](acp-vision.md)** — Why Learning is essential to the ACP
- **[Audit Trail](audit-trail.md)** — How all feedback is logged immutably
- **[Deployment Guide](deployment-guide.md)** — Using convergence metrics to deploy safely

---

**Every Skill converges to optimal behavior through feedback. Every step is audited. Convergence is visible, predictable, and verifiable.**
