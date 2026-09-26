# Phase 3 Kickoff — Prepared (Session 4)

**Status:** Ready for Session 4 (fresh token budget)  
**Blocked by:** Token exhaustion end of Session 3  
**Scope:** Hybrid OTEL (1h) + Phase 3 Planning

## Part 1: OTEL Multi-Tenant Instrumentation (1h)

**Scope:**
- Add `tenant_id` to all OTEL spans (ADR-0232 audit context)
- Instrument `corvin_console` routes (ADR-0243 plugin lifecycle)
- Wire into `core/learning/learning_events.py` (telemetry backend)

**Files to modify:**
- `core/console/corvin_console/app.py` (otel middleware)
- `core/console/corvin_console/routes/features_phase2.py` (endpoint spans)
- `core/learning/learning_events.py` (event telemetry)

**Constraint:** Multi-tenant isolation per ADR-0232, ADR-0033 (no tenant leakage)

## Part 2: Phase 3 Kickoff (Planning Doc)

**Goals:**
- Skill Forge v2 (ADR-0836): skill manifest, versioning, canary rollout
- Learning Loop Consolidation: feedback → optimization
- OTEL integration complete (Part 1 + OTEL dashboards)

**Decision Framework:**
- Skill registry: one per tenant (ADR-0243)
- Telemetry: default-ON, opt-out per GDPR (ADR-0232)
- Phase 3 stages: Month 1 (Skills), Month 2 (Learning), Month 3 (OTEL)

**ADR Dependencies:**
- ADR-0836 (Skill Forge v2)
- ADR-0677 (Phase 3 zip packaging)
- ADR-0033 (plugin registry)
- ADR-0243 (boot layers)

---

**Session 4 Estimate:** 3-4h (OTEL 1h + Phase 3 Kickoff 2h + review 0.5-1h)  
**Next:** Merge phase3-kickoff-ready → main when all gates pass
