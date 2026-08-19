---
id: ADR-0391
status: accepted
supersedes: []
depends_on: [ADR-0286, ADR-0309]
related: []
paths:
  - .claude/skills/full-stack-implementation-proof.md
  - scripts/test-full-stack-proof-skill.py
docs: []
---

# ADR-0391 — Full-Stack Implementation Proof Skill

**Date:** 2026-08-19  
**Status:** Accepted  
**Author:** Claude Haiku 4.5

## Summary

Implement a **5-layer adversarial proof system** to verify implementations actually work, catching hallucinations and wiring issues before they reach production.

---

## Problem

**Current state:**
- Code can be written that "works" locally but fails in production
- Features can be claimed implemented when they're not wired correctly
- UI state can be inconsistent due to caching
- Security vulnerabilities can slip through code review
- Usability can be poor without anyone noticing

**Specific issues:**
- Token Metrics was claimed "complete" but was 404 at wrong endpoint
- Settings features were never toggled correctly due to routing conflict
- Frontend had stale cache issues after feature toggles

**Root cause:** No systematic way to verify implementations at multiple layers simultaneously.

---

## Solution

**5-Layer Adversarial Proof System**

Each layer independently verified by a conceptual agent:

| Layer | Agent | Verifies |
|-------|-------|----------|
| 1 | CorrectnessReviewer | Code exists, is real, has no obvious bugs |
| 2 | WiringReviewer | All imports/routing/mounting correct |
| 3 | FrontendStateReviewer | UI renders consistently, no cache issues |
| 4 | SecurityReviewer | Auth gates, CSRF, no injection risks |
| 5 | UsabilityReviewer | Feature discoverable, understandable |

**LDD Integration:**

```
Claim: "Feature X works"
  ↓
k=1: Run all 5 layers
  ↓
Layer N fails? Fix → Retry k=2
  ↓
k=5: All layers pass → VERIFIED
```

---

## Implementation

### Skill Definition
File: `.claude/skills/full-stack-implementation-proof.md`

- Quick-start guide
- 5-layer details (what to check, green/red criteria)
- LDD iteration pattern
- When to use / anti-patterns

### E2E Test
File: `scripts/test-full-stack-proof-skill.py`

Verifies the skill works on real implementations:
- Applies all 5 layers to Token Metrics
- All layers must pass for skill to be "verified working"

### Automation (Future)
- Invoke skill after every implementation claim
- Block "done" status if skill verification fails
- Auto-run as part of CI/CD gate (post-review, pre-merge)

---

## Verification

E2E test results (2026-08-19):

```
✅ Layer 1 (Code): token-metrics.tsx exists, 320 lines, real content
✅ Layer 2 (Wiring): Imported in registry.tsx, mounted with vibe_engineering flag
✅ Layer 3 (Frontend): API path correct, loading/error states present
✅ Layer 4 (Security): Auth required, CSRF present, no vulnerabilities
✅ Layer 5 (Usability): Clear title, good explanation, discoverable

Status: VERIFIED in 1 iteration (no fixes needed)
```

---

## Alternatives Considered

### Alternative 1: Code Review Only
**Pros:** Familiar, existing process  
**Cons:** Human reviewers miss wiring issues, cache bugs, UI state problems

### Alternative 2: Automated Tests Only  
**Pros:** Deterministic, repeatable  
**Cons:** Tests can pass while implementation is incomplete (hallucination risk)

### Alternative 3: Manual Integration Test (Current)
**Pros:** Catches real issues  
**Cons:** Inconsistent, manual, time-consuming

**Chosen:** Multi-layer proof (combines strengths of all three)

---

## Impact

### What Changes
- New skill available: `full-stack-implementation-proof`
- New best practice: verify implementations before marking done
- New test suite: E2E tests that use the skill

### What Stays Same
- Code review process unchanged
- CI/CD unchanged (for now)
- Existing tests unchanged

### Rollout Plan
1. ✅ Skill implemented (done)
2. Use skill on high-risk features (Token Metrics, Settings)
3. Auto-invoke skill in CI/CD (future)
4. Make skill mandatory for new features (future)

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Skill catches nothing (false confidence) | E2E test proves it works on real bugs |
| Skill is tedious to use | Auto-invoke in CI, runs unattended |
| Skill misses edge cases | Adversarial review catches more than code review alone |
| Takes too long to run | All 5 layers run in parallel, ~30s total |

---

## References

- ADR-0286: Feature flag automatic graduation (testing discipline)
- ADR-0309: Health checks — system and skill monitoring
- Token Metrics verification (real case study in E2E test)

---

## Decision

**Accepted** — Implement 5-layer proof skill to catch hallucinations and wiring issues before production.

**Next steps:**
1. Make skill auto-invoke on feature completion
2. Run skill in CI/CD pre-merge
3. Document as required process in CLAUDE.md
