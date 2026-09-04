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

### 2b. `skill_auto_grade` (Internal)

Automatically grade a skill based on strategy outcome (internal use only).

Triggered by Brain's `on_strategy_succeeded`/`on_strategy_failed` event handlers.

```python
handle_request("skill_auto_grade",
    name="classifier-v2",
    score=1.0,  # or -0.5 on strategy failure
    reason="strategy_succeeded"
)
```

**Response:** `{"success": true, "confidence": 0.92}`

**Note:** End-users typically use `skill_grade` instead. This is called internally during event processing.

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

### 6. `get_health` (Internal)

Check subsystem health status.

```python
handle_request("get_health")
```

**Response:**
```json
{
  "status": "healthy",
  "subsystem": "skill_forge",
  "event_queue_size": 42,
  "last_event_time": 1692874523.45
}
```

**Note:** For internal monitoring. Used by Brain to verify subsystem liveness.

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

**Name:** `skill_forge_enabled` (matches Console feature registry)

**Location:** `spec.features.skill_forge_enabled` in `tenant.corvin.yaml`

**Toggle:** Console → Settings → Features → "Skill Forge" (visible when enabled in settings)

---

## Deployment Checklist

- [x] SkillForgeSubsystem wired in Brain (ADR-0360, commit b9f82d81)
- [x] Dependencies installed (numpy, scipy, pandas, scikit-learn)
- [x] Tests validating (96%+ success on `validate_skill_forge_subsystem.py`)
- [x] Metrics endpoint exposed (`get_metrics`)
- [x] Feature flag enabled in tenant config (`spec.features_whitelist`) — 2026-08-26
- [x] API Reference documentation complete (including internal APIs)
- [x] Rollback plan documented (disable feature flag, restart Brain)
- [x] Console Cache Freshness Skill deployed (verifies frontend staleness issues)
- [x] Deployment Guide created (SKILL_FORGE_2_0_DEPLOYMENT.md)

---

## Troubleshooting

**Issue:** `KeyError: 'skill1'` in auto-grading tests

**Cause:** Test setup issue; skills not initialized before grading

**Fix:** Use production flow (create skill first, then grade)

---

**Last Updated:** 2026-08-26  
**Next Review:** Week 5 (ADR-0360 measurement plan)
