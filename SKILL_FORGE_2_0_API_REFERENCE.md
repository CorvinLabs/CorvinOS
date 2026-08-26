# Skill Forge 2.0 API Reference

**Status:** Production (v0.2-rc1)  
**Architecture:** ADR-0360 (Skill Forge Subsystem Integration)

## Overview

Skill Forge 2.0 is a subsystem for autonomous skill creation, grading, and promotion within CorvinOS Brain v0.2.

## Subsystem Registration

**Location:** `core/orchestration/brain.py::run_task()`

Skills are registered automatically after ExecutionContext initialization:

```python
if not self._subsystems_initialized:
    try:
        execution_context = self._context_initializer.get_execution_context()
        if execution_context:
            await self._register_skill_forge_subsystem(execution_context)
            self._subsystems_initialized = True
```

## Request Types

### 1. `skill_create`

Create a new skill.

```python
handle_request("skill_create", 
    name="classifier-v2",
    body_md="# Skill Body\n...",
    description="Classifies tasks",
    skill_type="learned-experience",
    scope="session"
)
```

**Response:** `{"skill_record": {...}, "success": true}`

**Metrics:** `skill_create_latency_p95_ms` (observable via `get_metrics`)

---

### 2. `skill_grade`

Manually grade a skill.

```python
handle_request("skill_grade",
    name="classifier-v2",
    run_id="run_123",
    score=0.85,
    notes="Good classification accuracy"
)
```

**Response:** `{"success": true, "grade": {...}}`

---

### 3. `skill_promote`

Promote a skill to a higher scope (session → project → user).

```python
handle_request("skill_promote",
    name="classifier-v2",
    from_scope="session",
    to_scope="project"
)
```

**Response:** `{"success": true, "promoted_to": "project"}`

---

### 4. `list_skills`

List all skills matching criteria.

```python
handle_request("list_skills",
    scope="session",
    skill_type="learned-experience"
)
```

**Response:** `{"skills": [...], "count": 5}`

---

### 5. `get_metrics`

Retrieve performance metrics.

```python
handle_request("get_metrics")
```

**Response:**
```json
{
  "skill_create_count": 5,
  "skill_create_latency_p95_ms": 142.3,
  "skill_grade_count": 12,
  "skill_grade_latency_p95_ms": 8.5,
  "skill_promote_count": 2,
  "auto_grade_count": 15,
  "auto_grade_failures": 0
}
```

---

## Event Types (Published)

- `skill_created` — A skill was created
- `skill_graded` — A skill was graded
- `skill_promoted` — A skill was promoted
- `strategy_succeeded` / `strategy_failed` — Auto-grading trigger (ADR-0360)

---

## Auto-Grading Algorithm

**Trigger:** Strategy success/failure events

**Scoring:**
```
score = success(+1.0) OR failure(-0.5)
confidence = t_distribution_cdf(score, sem)
```

**Auto-Promotion:** `mean_score > 0.7 AND uses >= 5 AND confidence > 0.6`

**Reference:** ADR-0360, Section "Auto-Grading Algorithm"

---

## Feature Flag

**Default:** OFF (ship-dark)

**Location:** `spec.features.skill_forge_v2_enabled` in `tenant.corvin.yaml`

**Toggle:** Console → Settings → Features → "Skill Forge v2"

---

## Deployment Checklist

- [ ] SkillForgeSubsystem wired in Brain (ADR-0360, commit b9f82d81)
- [ ] Dependencies installed (numpy, scipy, pandas, scikit-learn)
- [ ] Tests validating (96%+ success on `validate_skill_forge_subsystem.py`)
- [ ] Metrics endpoint exposed (`get_metrics`)
- [ ] Feature flag enabled in settings (or default-OFF for canary)
- [ ] Rollback plan documented (disable feature flag, restart Brain)

---

## Troubleshooting

**Issue:** `KeyError: 'skill1'` in auto-grading tests

**Cause:** Test setup issue; skills not initialized before grading

**Fix:** Use production flow (create skill first, then grade)

---

**Last Updated:** 2026-08-26  
**Next Review:** Week 5 (ADR-0360 measurement plan)
