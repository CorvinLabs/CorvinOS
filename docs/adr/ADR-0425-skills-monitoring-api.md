---
id: ADR-0425
status: ACCEPTED
depends_on:
  - ADR-0422
  - ADR-0423
  - ADR-0424
relates_to:
  - ADR-0420
  - ADR-0421
paths:
  - core/console/corvin_console/routes/skills_monitoring.py
  - ops/launcher/corvin/skills_cli.py
docs:
  - docs/claude-ref/skill-system-v2-monitoring.md
---

# ADR-0425: Skills Monitoring API (Phase 8 k=2)

**Problem:**
Operators need visibility into Skill System hardening state (cache stats, circuit-breaker, rate-limiter) via both REST API and CLI. The hardening layer (ADR-0422) exists but has no observability surface.

**Solution:**
Two new public surfaces expose hardening metrics:
1. **REST API** (FastAPI routes under `/api/skills/`): cache-stats, circuit-breaker state, rate-limiter quota, health, cache-clear
2. **CLI** (Click commands): `corvin skills {cache-stats,cache-clear,health,circuit-breaker}`

Both routes delegate to existing hardening instances (no new state). Monitoring adds **zero** to the core security model — it only surfaces what the hardening layer already tracks.

**Architecture:**
- `SkillServiceHardening` instances are shared singletons (lazy-initialized)
- Routes dispatch to `SkillDependencyResolver.stats()` and hardening.health_status()
- All endpoints auth-gated (FastAPI Depends + CLI implicit localhost-only)
- Recommendations engine flags threshold violations (hit-rate <70%, circuit-breaker OPEN, etc.)

**Invariants:**
- Monitoring NEVER modifies hardening state except explicit `cache/clear` (admin operation)
- All metrics are point-in-time snapshots; no alerting/escalation logic
- Tenant-scoped queries (resolver-level, not cross-tenant)

**Trade-offs:**
- Lazy singleton pattern means first request to a route creates hardening instance; subsequent routes reuse it
- No persistent metric history (stateless); operators must scrape periodically if trending needed
- Circuit-breaker state is global, not per-skill (simplified fail-fast model; per-skill circuit breaker is deferred to v0.3)

**Alternatives Considered:**
1. Hardening metrics via a separate monitoring service — rejected: adds deployment complexity, eventual consistency risk
2. Metrics as part of resolver.resolve() calls — rejected: observer effect (metrics query itself consumes rate-limit tokens)
3. Disabled by default — rejected: observability is security; monitoring should always be available to an authenticated operator

**See Also:**
- Phase 8 k=1: `hardening.py` (rate-limiter, circuit-breaker)
- Phase 8 k=3: E2E integration tests (resolver + hardening end-to-end)
