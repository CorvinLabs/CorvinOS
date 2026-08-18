---
kind: concept
id: CONCEPT-0022
status: PROPOSED
supersedes: []
depends_on: [ADR-0385]
related: [ADR-0383, ADR-0384]
skills: []
commits: []
paths:
  - core/learning/task_predictor.py
  - core/console/routes/suggestions.py
docs:
  - docs/v0.6-design/V0.6_IDEAS.md
---

# CONCEPT-0022: Predictive Task Suggestion

## The Idea

**What's the operator likely to work on next?** Build an ARIMA(2,1,1) time-series model on operator's task sequence.

## How It Works

**Input:** Operator's last 30 task types: `[auth, auth, perf, memory, schema, memory, ...]`

**ARIMA Model:**
- `p=2`: Last 2 tasks influence next prediction
- `d=1`: Difference to remove trend
- `q=1`: 1-lag moving average smoothing

**Output:** Top-2 predicted task types with confidence scores

```
Predictions:
  1. memory (73% confident) — operator's strength: NEUTRAL
  2. auth (52% confident) — operator's strength: STRONG
```

## Suggestion Display

**Non-intrusive sidebar** (top-right, dismissible):

```
Next up?
━━━━━━━━━━━━━━━━━
📋 memory optimization
   73% confident
   Your strength: NEUTRAL

📋 auth refactor
   52% confident
   Your strength: STRONG ✓

[Dismiss]
```

## Triggering Rules

- Show only if confidence >65%
- Skip if operator completed same type last turn
- Skip weak types (don't suggest struggling areas)
- Show max 2 predictions

## Success Metric

**Suggestion acceptance rate >60%** = operator starts suggested task within 1 turn.

## Privacy

Predictions use **only operator's own task history**, no external data.

## Operator Notes

(Append-only section)

---

**Dependency:** ADR-0384 (Task Affinity) filters out weak-type suggestions.

