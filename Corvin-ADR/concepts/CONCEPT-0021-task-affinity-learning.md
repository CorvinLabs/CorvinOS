---
kind: concept
id: CONCEPT-0021
status: PROPOSED
supersedes: []
depends_on: [ADR-0383, ADR-0384]
related: []
skills: []
commits: []
paths:
  - core/learning/affinity_model.py
docs:
  - docs/v0.6-design/V0.6_IDEAS.md
---

# CONCEPT-0021: Task Affinity Learning

## The Idea

Some operators excel at authentication bugs but struggle with memory management. **Task affinity** = per-task-type success rate.

```
affinity = {
  "auth": 0.87,       # 87% success on auth tasks
  "memory": 0.62,     # 62% on memory tasks
  "schema": 0.35,     # 35% on schema design
}
```

## Measurement

**Bayesian update on every turn outcome:**

```
success_count_new = success_count + (1 if outcome=="success" else 0)
total = success_count + failure_count

affinity = success_count_new / total
confidence = min(1.0, total / 30)  # saturate at 30 samples
```

## Strength Tiers

- **Strong** (≥75%): Operator is good at this type
- **Neutral** (45-75%): Average performance
- **Weak** (<45%): Operator struggles, needs help

## Use Cases in v0.6+

1. **Suggestion filtering:** Don't suggest weak types to operators who struggle
2. **Guided learning:** Recommend training on weak types
3. **Skill recommendations (v0.7):** Suggest plugins that boost weak areas
4. **Team balancing (v0.8):** Distribute tasks by team member strengths

## Measurement Accuracy

**Validation strategy:** For operators with ≥30 decisions per type:
- Hold out 10 recent decisions (test set)
- Measure affinity on first 20 (training set)
- Compare predicted affinity to actual test-set success rate
- Target MAE < 0.15 (15 percentage points error)

## Operator Visibility

**Console → Learning → My Task Strengths:**

```
┌──────────────────────────────────┐
│ Your Task Strengths              │
├──────────────────────────────────┤
│ Authentication        87% ✓ STRONG│
│ Performance Tuning    76% ✓ STRONG│
│ Memory Management     62% ◐ NEUTRAL│
│ Schema Design         35% ✗ WEAK  │
│ UI Development        48% ◐ NEUTRAL│
│                                   │
│ [Improve Weak Areas]              │
└──────────────────────────────────┘
```

## Operator Notes

(Append-only section)

---

**Next:** ADR-0385 uses this model to filter task suggestions.

