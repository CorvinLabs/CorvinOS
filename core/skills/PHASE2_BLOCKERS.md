# Phase 2 Architectural Blockers — Deferred from Phase 1

**Status:** Documented for Phase 2 (6-8 weeks)  
**Date:** 2026-09-04  
**Blockers:** 2/12 fixes deferred (Fixes #5, #11)

---

## Fix #5: Subprocess Timeout (HIGH)

**Problem:** Current async model has no signal handler to kill hung Skill processes.  
**Impact:** A Skill that enters infinite loop hangs the registry for 5s+ before timeout returns.  
**Why deferred:** Requires subprocess architecture change (fork/spawn model) — out of scope for Phase 1.

**Workaround (Phase 1):** 
- Document known limitation in Skill authoring guide
- Monitoring: Alert if Skill latency > 4s consistently

**Phase 2 Solution:**
```python
# Pseudo-code: subprocess-based isolation
skill_process = subprocess.Popen([sys.executable, '-m', 'skill_runner', skill_id, input_json])
try:
    output, _ = skill_process.communicate(timeout=timeout_ms/1000.0)
except subprocess.TimeoutExpired:
    skill_process.kill()  # SIGTERM → SIGKILL
    return SkillExecutionResult(status="timeout", ...)
```

**Depends on:** ADR-0550 (subprocess isolation architecture), Phase 2 timeline.

---

## Fix #11: Process-Level Isolation (HIGH)

**Problem:** In-process Skill errors (memory leak, object cycles, monkey-patching) can crash the registry.  
**Impact:** One malicious or buggy Skill can take down the entire system.  
**Why deferred:** Requires fundamental architecture change — separate process per Skill OR sandbox + capability model.

**Workaround (Phase 1):**
- Code review: All Phase 1 Skills are first-party + audited
- Monitoring: Memory leak detection, crash recovery
- Documentation: "Phase 1 is single-process; Phase 2 adds isolation"

**Phase 2 Solution (Option A: Subprocess):**
Each Skill runs in its own subprocess; registry is parent coordinator.

**Phase 2 Solution (Option B: Sandbox):**
Sandbox each Skill (e.g., via `seccomp` on Linux, `pledge` on OpenBSD).

**Depends on:** ADR-0550 (subprocess isolation), ADR-XXXX (sandbox strategy), Phase 2 timeline.

---

## Migration Path (Phase 2)

**Week 1–2:** ADR-0550 finalized, subprocess architecture approved  
**Week 3–4:** Implement subprocess wrapper + tests  
**Week 5–6:** Migrate Phase 1 Skills to subprocess model (backward-compat layer)  
**Week 7–8:** Production rollout + canary testing

**Testing Gate (before production):**
- Hang-test: Subprocess killed cleanly on timeout
- Isolation-test: Crashed Skill doesn't crash registry
- Perf-test: Subprocess overhead < 50ms latency increase

---

## References

- **ADR-0550** (TBD): Subprocess isolation architecture
- **PHASE2_ROADMAP.md** Section 2h: Performance optimization (lazy-load, parallel)
- **Skill Authoring Guide** (Phase 2): "Do not block indefinitely; design Skill cancellation"

---

**Not a production blocker for Phase 1.** Phase 1 uses first-party Skills only; Phase 2 will add isolation before community plugins are added.
