---
name: os.delegation_router
version: "1.0.0"
description: Route tasks to native OS, ACS, or TDE
---

# OS-Skill: Delegation Router v1.0

You are the delegation router skill. Your job: decide where to route a task.

## Phase 0 — Intake

Validate input against input_schema:
- task_shape: one of [small_code, big_data, prose, structured]
- context_size: integer 0-2000000
- tenant_id: alphanumeric + underscore/dash

If any field missing or invalid type, report error and exit.

## Phase 3 — Plan + Decision

**Heuristic (MVP):**
- If task_shape == "big_data": route to ACS (cost-efficient for large data)
- Else: route to native (lower latency)

Set confidence based on certainty:
- big_data heuristic: confidence 0.75 (data efficiency is reliable)
- native fallback: confidence 0.80 (native is always an option)

Reasoning: brief one-sentence explanation

## Phases 1, 2, 4-9

Skipped in MVP (no context loading, no clarification, no full logic flow).

## Phase 10 — Output

Return JSON:
```json
{
  "decision": "acs" or "native" or "tde",
  "confidence": float 0.0-1.0,
  "reasoning": "string explanation"
}
```

Validate against output_schema. If invalid, error.

## Phase 11 — Learning

Skipped in MVP (done in Phase 2).

---

## No Feedback Loop in MVP

Feedback ingestion and optimization added in Phase 2.
