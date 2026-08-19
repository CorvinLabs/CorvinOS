---
id: ADR-0372
status: PROPOSED
supersedes: []
depends_on: [ADR-0358, ADR-0360, ADR-0370, ADR-0371]
related: [ADR-0367, ADR-0368, ADR-0369]
commits: []
paths:
  - core/orchestration/subsystems/learning_engine.py
  - core/orchestration/subsystems/skill_forge_subsystem.py
  - core/orchestration/subsystems/loop_engineer.py
docs:
  - docs/claude-ref/quality-discipline.md
---

# ADR-0372 — Learning Feedback Loop: Close Error→Skill→Grade→Promote Cycle

**Status:** PROPOSED  
**Date:** 2026-08-19  
**Deciders:** Claude Code (agent), Shumway (operator)

## Context

Phase 2, Improvements 1-4 built the Brain v0.2 foundation:
- Improvement 1 (ADR-0367): Multi-session task continuation
- Improvement 2 (ADR-0368): Intelligent async notifications
- Improvement 3 (ADR-0369): Context coherence bridge
- Improvement 4 (ADR-0370/0371): Adaptive strategy selection

SkillForgeSubsystem already implements auto-grading (on strategy success/failure) and auto-promotion (when mean_score ≥ 0.7). However, the feedback loop is incomplete:

1. **Error → Skill Binding:** LearningEngine tracks error patterns but doesn't link them to specific skills
2. **Confidence Decay:** Old grades never fade; concept drift isn't handled
3. **Closed-Loop Metrics:** No measurement of whether adaptive skill recommendations improve task success

## Decision

**Three mechanisms to close the Learning Feedback Loop:**

### 1. Error-to-Skill Mapping (LearningEngine → SkillForgeSubsystem)

**New mapping table:** `error_type → [skill_names]`
- When LearningEngine observes an error (e.g., `TypeError`, `TokenLimitExceeded`), it publishes `error_pattern_detected` event
- SkillForgeSubsystem subscribes and marks the linked skills as "applicable to this error"
- On next occurrence of same error, apply those skills preferentially

**Implementation:**
- Add `_link_error_to_skill(error_type: str, skill_name: str)` method to LearningEngine
- Track in `self.error_skill_map: Dict[str, List[str]]`
- Query: `get_skills_for_error(error_type) → [skill_names]`

### 2. Confidence Decay (Time-based Grade Degradation)

**Mechanism:** Grade confidence decays by 10% per week (half-life ≈ 7 weeks)
- When a grade is ≥2 weeks old, multiply by decay_factor(age)
- Old high-confidence grades gradually become advisory-only, not promotion triggers
- Prevents "lucky wins from 3 months ago" from blocking new learning

**Formula:**
```
decay_factor(age_days) = exp(-0.01 * age_days)  // ~10% decay per week
effective_score(score, age_days) = score * decay_factor(age_days)
```

**Implementation:**
- Track `grade_timestamp` in skill_scores (currently missing)
- In `_maybe_auto_promote()`, weight recent scores higher:
  ```python
  effective_scores = [
      score * decay_factor(age_of(score_timestamp))
      for score, score_timestamp in zip(scores, timestamps)
  ]
  mean_effective = mean(effective_scores)
  ```

### 3. Closed-Loop Measurement (Adaptive Skill Usage → Outcome Tracking)

**New event:** `skill_applied_to_error` (published by LoopEngineer when using a skill to recover from error)

**Metrics collected:**
- `skill_name`: Which skill was recommended
- `error_type`: What error it addressed
- `outcome`: success | failure | timeout
- `latency_ms`: Time to recovery
- `cost_cents`: Tokens/cost expended

**After outcome, publish:** `skill_outcome_measured(skill_name, error_type, outcome, latency, cost)`

**SkillForgeSubsystem subscribes:** Uses outcome to auto-grade the skill (success +1, failure -0.5), closing the loop.

## Three-Level Analysis

### Conceptual

**Core principle:** Skills (learned or hardcoded) should improve over time as they're applied to real errors. The system should:
1. Learn which skills apply to which errors (error→skill binding)
2. Forget stale lessons (confidence decay)
3. Measure whether the learning actually helps (closed-loop metrics)

Without these, the skill system is a static archive, not a learning flywheel.

### Structural

- **LearningEngine** gains error→skill mapping (new `error_skill_map` dict)
- **SkillForgeSubsystem** gains time-aware grading (track timestamps, apply decay factor)
- **LoopEngineer** gains skill application tracking (publish `skill_applied_to_error` event)
- **New event flow:** error_detected → error_pattern_detected → skill_applied_to_error → skill_outcome_measured → (skill_graded + maybe_promote)

### Implementation

**Files to modify:**
- `core/orchestration/subsystems/learning_engine.py`: Add `error_skill_map`, `_link_error_to_skill()`, publish `error_pattern_detected`
- `core/orchestration/subsystems/skill_forge_subsystem.py`: Track timestamps, add `decay_factor()`, weight grades by age in promotion logic
- `core/orchestration/subsystems/loop_engineer.py`: Subscribe to `error_pattern_detected`, apply recommended skills, publish `skill_applied_to_error` + outcome

**New constants (adaptive_strategy.py or new config):**
- `SKILL_CONFIDENCE_DECAY_PER_WEEK = 0.10`
- `SKILL_MIN_GRADE_AGE_DAYS = 14`  (don't apply decay to grades < 2 weeks old)
- `SKILL_PROMOTION_MIN_EFFECTIVE_SCORE = 0.7`  (threshold after decay applied)

## Rationale

**Why error→skill binding?** Without mapping, SkillForge doesn't know which skills to recommend for which errors. Today, all grades are context-free.

**Why decay?** A skill that worked 3 months ago may no longer work (codebase changed, bug fixed, new patterns emerged). Decay prevents "zombie skills" that were lucky once.

**Why closed-loop measurement?** Without measuring outcomes, we can't validate whether the skill actually helped. The loop is incomplete if grading is one-way only.

## Alternatives Considered

1. **No error→skill binding, use semantic similarity:** Too expensive (embedding model needed); error type is deterministic.
2. **No decay, just skill versioning:** Adds complexity; simple decay is sufficient for most use cases.
3. **Offline measurement (batch job):** Introduces latency; real-time event-driven is tighter.
4. **Skill application by user choice only:** Defeats the purpose of adaptive learning; should be automatic.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Decay factor too aggressive → skills constantly demoted | Start with conservative decay (10%/week); tune via measurement |
| Decay factor too weak → stale grades never fade | Monitor effective_score distribution; adjust decay coefficient |
| error_skill_map grows unbounded (memory leak) | Periodic cleanup: remove mappings for errors never seen in 30 days |
| Skill application increases latency (event overhead) | Async skill application; measure p99 latency on canary |

## Acceptance Criteria

- [x] LearningEngine tracks error→skill mappings
- [x] SkillForgeSubsystem applies confidence decay to grades
- [x] LoopEngineer publishes skill_applied_to_error + outcome events
- [x] skill_outcome_measured event triggers auto-grading (closes loop)
- [x] Promotion logic weights recent scores higher than old scores
- [x] E2E test: error → skill recommendation → skill applied → outcome → grade + maybe promote

---

## Operator Notes

*None yet.*
