---
id: ADR-0385
status: PROPOSED
depends_on: [ADR-0383, ADR-0384]
relates_to: [ADR-0386]
paths:
  - core/learning/task_predictor.py
  - core/console/routes/suggestions.py
  - core/console/web-next/src/components/SuggestionPanel.tsx
docs:
  - docs/v0.6-design/V0.6_IDEAS.md
---

# ADR-0385: Predictive Guidance Engine

## Problem

Operators context-switch constantly. CorvinOS could anticipate the next task and suggest guidance proactively.

## Solution

**ARIMA time-series predictor** for next task type, with operator affinity filtering:

```python
class PredictiveGuidanceEngine:
    """Predict next task type and suggest guidance."""
    
    def predict_next_task(self, operator_id: str) -> List[TaskSuggestion]:
        """Return ranked task suggestions."""
        
        # Load task sequence for this operator
        sequence = load_recent_task_sequence(operator_id, window=30)
        
        # ARIMA(2, 1, 1) prediction
        model = ARIMA(sequence, order=(2, 1, 1))
        predictions = model.predict(steps=1)  # next task only
        
        # Filter by operator affinity
        suggestions = []
        for (task_type, confidence) in predictions:
            if confidence < 0.65:
                break  # too uncertain
            
            affinity = load_affinity(operator_id, task_type)
            if affinity.strength_tier == "weak":
                continue  # skip types operator struggles with
            
            suggestions.append(TaskSuggestion(
                task_type=task_type,
                confidence=confidence,
                affinity_tier=affinity.strength_tier,
                reason=f"Based on your task history, you often work on {task_type} next",
            ))
        
        return suggestions[:2]  # top 2 only
```

## ARIMA Model

**Order:** (2, 1, 1)
- **p=2:** 2 prior task types influence prediction
- **d=1:** Differencing to remove trend
- **q=1:** 1-lag moving average for smoothing

**Hyperparameters (tuned per phase 4):**
- Seasonal order: None (no strong weekly/monthly patterns observed)
- Robust: True (downweight outliers)

## Suggestion Delivery

**Trigger:** After every turn completion

**Display:** Non-intrusive sidebar in console (top-right)

```
┌─────────────────────────────────┐
│ Next up?                        │
├─────────────────────────────────┤
│ 📋 memory optimization          │
│    73% confident based on       │
│    your task history            │
│    Your strength: strong ✓      │
│                                 │
│ 📋 API design                   │
│    54% confident                │
│    Your strength: neutral       │
│                                 │
│ [Dismiss]  [Got it]             │
└─────────────────────────────────┘
```

**Acceptance tracking:**
```python
def record_suggestion_outcome(
    operator_id: str,
    suggestion_id: str,
    accepted: bool,
    task_completed: bool,
) -> None:
    """Track whether operator followed suggestion."""
    
    emit_event(
        "suggestion_outcome",
        operator_id=operator_id,
        suggestion_id=suggestion_id,
        accepted=accepted,
        task_completed=task_completed,
    )
```

## Suppression Rules

- **Don't suggest** if confidence <65%
- **Don't suggest** if operator completed same type last turn (avoid repetition)
- **Don't suggest** weak types (affinity.strength_tier == "weak") to struggling operators
- **Don't suggest** if Settings disabled: `spec.features.operator_modeling_suggestions: false`

## Success Metrics

- **Suggestion acceptance:** >60% (operator starts suggested task within 1 turn)
- **Suggestion quality:** No suggestion ever leads to task failure (confidence interval bias check)
- **Latency:** <50ms p99

## Testing (20+ tests)

- ARIMA model correctness (5)
- Affinity filtering (4)
- Suggestion deduplication (3)
- Acceptance tracking (4)
- E2E suggestion pipeline (4)

---

## GDPR Notes

Predictions use only operator's own task history (Art. 5, 6). No cross-operator data. Suggestions shown locally; optionally stored in local audit trail.

## Rollback

Set `spec.features.operator_modeling_suggestions: false` to hide suggestions (v0.5 behavior).

