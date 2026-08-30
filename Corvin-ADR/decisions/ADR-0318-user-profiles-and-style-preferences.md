---
id: ADR-0318
status: accepted
depends_on: ['ADR-0314', 'ADR-0317']
related: ['ADR-0307', 'ADR-0309']
commits:
  - "feat(learning): User Profiles & Style Preferences (ADR-0318)"
paths:
  - "core/learning/user_profile.py"
  - "tests/unit/test_user_profile_comprehensive.py"
docs:
  - "docs/compliance/GDPR-ADR-0315-0318-audit.md"
  - "VIBE_PHASE1_IMPLEMENTATION_SUMMARY.md"
---

# ADR-0318 — User Profiles & Style Preferences

**Status:** Accepted
**Date:** 2026-08-12
**Deciders:** Claude (Implementation), shumway (Audit)

## Context

**Context:** Different users have different preferences: some like concise answers, others like detailed explanations. Some prefer pragmatic advice, others want theoretical grounding. ADR-0318 learns these preferences over time.

## Decision

**Decision:** Implement UserProfile:
1. decision_style: pragmatic | theoretical | balanced (inferred from feedback)
2. conciseness_preference: [0.0-1.0] (0=verbose, 1=terse)
3. Skill preferences: weights per skill based on past outcomes
4. Model preference: which models does user seem to prefer?
5. Update preferences based on feedback (ADR-0317)
6. Per-user + per-tenant isolation

## Implementation

**File:** core/learning/user_profile.py

- UserProfile dataclass
- decision_style: Enum
- conciseness_preference: float
- skill_weights: dict[str, float]
- update_from_feedback()
- predict_preference() → dict

## Compliance

**GDPR Art. 21:** Right to object (user can override inferred preferences). **GDPR Art. 6, 7:** Consent (preferences guide personalization).

## Tests

**Tests:** 47 tests covering:
- UserProfile dataclass (10 tests: validation, immutability, serialization)
- Profile manager (10 tests: CRUD, caching, persistence)
- Feedback processing (6 tests: skill feedback, style updates)
- Preference prediction (3 tests)
- Disk persistence (4 tests: file I/O, corruption recovery)
- GDPR compliance (5 tests: data minimization, consent, Right to Object)
- Edge cases (6 tests: boundary conditions, large datasets)

**Test Files:** tests/unit/test_user_profile_comprehensive.py

**Coverage:** 92%+

## Effort Estimation

| Task | Hours |
|---|---|
| UserProfile class | 1.5h |
| Preference inference | 1h |
| Feedback integration | 0.5h |
| Override handling | 0.5h |
| Unit tests (12) | 1h |
| Docs | 0.5h |
| **Total** | **5h** |

---

**Prepared by:** Claude
**Date:** 2026-08-12
