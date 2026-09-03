# SPIKE 1 EXECUTION PLAN — Feature Flags → Skills API Rewrite
**Status:** READY FOR AUTONOMOUS EXECUTION | **Owner:** Spike 1 Dev  
**Created:** Sept 2, 2026 | **Autonomous Mode:** Enabled

---

## PHASE 0: BLOCKING WAIT (Sept 2–3 06:00 UTC)

**Purpose:** Monitor for ADR-0544 Amendment answers. Prepare contingency if blockers remain unanswered.

### Monitoring Loop
```
While (Sept 3 10:00 UTC not reached):
  Every 6 hours:
    1. Check for Architecture Lead response to SPIKE_1_ADR_0544_AMENDMENT_REQUEST.md
    2. If answered → go to PHASE 1 (Blocker Analysis)
    3. If escalation triggered → go to PHASE CONTINGENCY
    4. Otherwise: continue monitoring
```

### Contingency Trigger (Sept 3 10:00 UTC)
If ANY blocker remains unanswered:
- **STOP ALL WORK**
- Document which blockers are missing
- Escalate to steering + architecture lead
- Recommend: proceed with Option 2b (Wrapper+Phased) as lowest-risk default

---

## PHASE 1: BLOCKER ANALYSIS (Sept 3 06:00–10:00 UTC)

**Trigger:** Architecture Lead responds to all 4 blockers  
**Output:** Concrete decisions → coded into implementation

### Tasks
1. **Parse Blocker Answers**
   - Read architecture amendment
   - Validate 4 answers are complete and unambiguous
   - If ANY missing/ambiguous → escalate immediately

2. **Translate to Implementation Decisions**
   ```
   Blocker #1 Answer → feature_flags_registry.yaml structure
   Blocker #2 Answer → wrapper vs. big-bang code paths
   Blocker #3 Answer → worker_engine_mode handling
   Blocker #4 Answer → tier management in Skills manifest
   ```

3. **Update Velocity Tracking**
   - Lock in final effort estimates based on blocker decisions
   - Adjust task breakdown if needed
   - Verify total ≤10h

4. **Prepare Code Skeleton**
   - Create branch checkpoint (save current state)
   - Prepare empty Skills manifest structure
   - Outline wrapper API shape (if wrapper chosen)

---

## PHASE 2: SPIKE 1 REWRITE (Sept 3 10:00 → Sept 4 ~20:00 UTC)

**Duration:** ≤10 hours (real-time tracked)  
**Tracking:** Every 30–60 min update SPIKE_1_VELOCITY_TRACKING.md

### Task 1: Skill Manifest Creation (Est. 1–2h)
**Dependencies:** Blocker #1 (flag-to-skill mapping)

```
Deliverables:
  - core/skills/feature_flags_registry.yaml (manifest)
  - Entry for each mapped feature flag
  - Version: 0.1.0-spike1
  - Includes: 60 flags + worker_engine_mode (if Blocker #3 = skill)
  
Testing:
  - Manifest validates against Skills schema
  - All 60 flags present in registry
```

### Task 2: Skills Registry Wrapper API (Est. 1–2h)
**Dependencies:** Blocker #2 (Big Bang vs. Wrapper)

**If Wrapper+Phased (Option 2b):**
```python
# core/skills/feature_flags_legacy_adapter.py
class FeatureFlagLegacyAdapter:
  def is_enabled(flag_id, tenant_id="_default") -> bool:
    # Delegate to Skill, return result
    result = SkillsRegistry.execute(
      "feature_flags",
      {"flag_id": flag_id, "tenant_id": tenant_id}
    )
    return result.get("enabled", False)
  
  def set_enabled(flag_id, enabled, tenant_id="_default") -> bool:
    # Delegate to Skill
    result = SkillsRegistry.execute(
      "feature_flags_admin",
      {"flag_id": flag_id, "enabled": enabled, "tenant_id": tenant_id}
    )
    return result.get("success", False)
```

**If Big Bang (Option 2a):**
```
- Update all 88 call-sites immediately
- Remove legacy adapter
- Direct Skills API calls
- Refactor plan for Phase 1b (separate task)
```

### Task 3: JSON Storage Layer (Est. 1–1.5h)
**Dependencies:** Blocker #1, #2

```
Deliverable:
  - Skill state storage: overlay JSON per tenant
  - Path: {tenant_home}/global/feature_flags_overlay.json
  - Format: { "flags": { "flag_id": bool, ... } }
  - Tenant isolation: every read/write validates tenant_id
  
Implementation:
  - If Wrapper: reuse existing overlay I/O code
  - If Big Bang: migrate to SkillState storage backend
  
Tests:
  - Read/write single flag
  - Read/write multiple flags
  - Cross-tenant isolation (cannot read other tenant's flags)
```

### Task 4: Audit Event Injection (Est. 1–2h)
**Dependencies:** Blocker #2

```
Deliverable:
  - Every Skill.execute() call for feature_flags emits SKILL_EXECUTED event
  - Event structure:
    {
      "event_type": "skill_executed",
      "skill_id": "feature_flags" (or "feature_flags_admin"),
      "tenant_id": "<tenant>",
      "input": {"flag_id": "...", "value": ...},
      "output": {"enabled": true/false, ...},
      "latency_ms": N,
      "lom": "feature_flags_legacy_adapter:is_enabled:L42",  // Line of Moral Responsibility
      "hash": "sha256(...)",
      "prev_hash": "..."
    }
  
Testing:
  - Audit backend captures every event
  - Hash chain verifies (prev_hash matches prior event's hash)
  - Tenant_id is present and correct
  - No PII in payload (flag values only, not user data)
```

### Task 5: Tenant Isolation Validation (Est. 0.5–1h)
**Dependencies:** Task 3 (storage layer)

```
Tests:
  1. is_enabled("flag_x", tenant_id="tenant_a") returns correct value
  2. is_enabled("flag_x", tenant_id="tenant_b") returns DIFFERENT value
  3. set_enabled("flag_x", true, "tenant_a") does NOT affect tenant_b
  4. Audit events are filtered by tenant_id (cannot query cross-tenant)
  5. Concurrent writes to same flag on different tenants don't interfere
  
Validation:
  - GDPR Art. 5, 32 requires strict isolation
  - Failure = cannot ship (compliance gate)
```

### Task 6: Testing Suite (Est. 2–3h)
**Dependencies:** Tasks 1–5

```
Unit Tests:
  - Test every feature_flags.py function with Skills backend
  - Mock Skills API (if needed for unit isolation)
  - Coverage: 100% of public API

Equivalence Tests:
  - OLD: result_old = feature_flags.is_enabled("flag_x")
  - NEW: result_new = SkillsRegistry.execute("feature_flags", {...})
  - ASSERT: result_old == result_new
  - Repeat for all 60 flags + all getter/setter functions
  - CRITICAL GATE: if equivalence fails, cannot proceed

Audit Tests:
  - Call is_enabled() → verify SKILL_EXECUTED event emitted
  - Verify event structure (all required fields present)
  - Verify hash chain integrity
  - Verify tenant_id filtering

E2E Tests:
  - Real Skills system (not mocked)
  - Run on actual feature_flags Skill
  - Verify console/bridges can resolve flags correctly
  - Verify audit trail completeness
```

### Task 7: Documentation & Rollout Plan (Est. 0.5–1h)
**Dependencies:** Blocker #2 (determines rollout scope)

**If Wrapper+Phased:**
```markdown
# Rollout Plan: Wrapper + Phased Migration

## Phase 1b (Weeks 1–10)
- Wrapper transparently delegates to Skills
- Existing 88 call-sites UNCHANGED
- Operator sees no behavior change
- Deprecation notices in code comments

## Phase 2 (Weeks 11+)
- Gradual migration of call-sites to direct Skills API
- Metrics-driven: prioritize high-call-volume sites first
- Wrapper removed once all 88 call-sites migrated

## Migration Checklist
- [ ] Blocker answers integrated
- [ ] Wrapper API tested for equivalence
- [ ] Audit trail verified
- [ ] Phase 1b call-site migration plan
```

**If Big Bang:**
```markdown
# Rollout Plan: Big Bang Refactoring

## Phase 1b (Weeks 1–10)
- All 88 call-sites refactored to Skills API
- Parallel teams (5–8) work on 10–15 files each
- Call-site mapping: [list 88 files]
- Testing: equivalence + audit per file

## Risk Mitigation
- Canary: run on _default tenant first
- Rollback: keep old feature_flags.py in feature/rollback branch
- Metrics: monitor error rate, audit completeness, latency
```

---

## PHASE 3: FINAL VERIFICATION (Sept 4 Morning)

**Gate 1: API Equivalence**
```bash
pytest tests/unit/test_feature_flags_equivalence.py -v
# PASS: Old API == New Skill behavior for all 60 flags
```

**Gate 2: Audit Trail**
```bash
pytest tests/unit/test_feature_flags_audit.py -v
# PASS: Every is_enabled() call produces SKILL_EXECUTED event
# PASS: Hash chain verified (no breaks)
# PASS: Tenant isolation enforced
```

**Gate 3: Tenant Isolation**
```bash
pytest tests/unit/test_feature_flags_tenant_isolation.py -v
# PASS: Cross-tenant queries blocked
# PASS: Concurrent writes don't interfere
```

**Gate 4: Full Test Suite**
```bash
pytest tests/unit/test_feature_flags*.py -v
pytest tests/integration/test_feature_flags_e2e.py -v
# PASS: All tests green
# PASS: No regressions in console/bridges
```

---

## PHASE 4: FINAL REPORT (Sept 4 EOD)

**Deliverable:** `docs/SPIKE_1_FINAL_REPORT_SEPT_4.md`

```markdown
# SPIKE 1 FINAL REPORT — Feature Flags → Skills API Rewrite

## EXECUTIVE SUMMARY
- **Actual Velocity:** X hours (target: ≤10 hours)
- **Status:** ✅ PASS / ⚠️ MARGINAL / ❌ FAIL
- **Quality Gates:** All pass / Some fail / Critical gates failed

## ACTUAL EFFORT BREAKDOWN
- Manifest creation: Xh (estimate: 1–2h)
- Wrapper API: Yh (estimate: 1–2h)
- Storage layer: Zh (estimate: 1–1.5h)
- Audit integration: Ah (estimate: 1–2h)
- Testing: Bh (estimate: 2–3h)
- Documentation: Ch (estimate: 0.5–1h)
- **TOTAL: Xh (estimate: 7.5–13h)**

## KEY FINDINGS
1. [Most surprising/difficult task]
2. [Architecture decision impact]
3. [Audit integration complexity]
4. [Test coverage vs estimate]

## BIG BANG FEASIBILITY ANALYSIS
**Spike 1 (1 file):** ✅ Feasible in ≤10h

**Phase 1b Extrapolation:**
- 1 file = 1550 LOC → took X hours
- 88 files = 137K LOC → extrapolates to Y hours
- With 5 teams → Z weeks

**Recommendation:**
- ✅ Proceed with Phase 1b (Big Bang or Wrapper+Phased per Blocker #2)
- ⚠️ Use team capacity of X, timeline Z
- 🚨 Risk factors: [list]

## NEXT PHASE RECOMMENDATION
- [ ] Proceed with Phase 1b big-bang (88 files)
- [ ] Use wrapper + phased approach (Phase 2 cleanup)
- [ ] Escalate: blockers unresolved (cannot extrapolate)
- [ ] Proceed with measured risk (note contingencies)
```

---

## AUTONOMOUS EXECUTION MODE

**Loop:** Monitor → Execute → Report

```python
# Pseudo-code for autonomous loop
while True:
    status = check_blocker_status()
    
    if status == "ALL_ANSWERED":
        phase = PHASE_1  # Blocker analysis
        execute_phase(phase)
        if phase.success():
            phase = PHASE_2  # Spike 1 rewrite
            execute_phase_real_time(phase)  # Every 30–60 min update velocity
            if phase.success():
                phase = PHASE_3  # Final verification
                execute_phase(phase)
                if phase.success():
                    phase = PHASE_4  # Final report
                    execute_phase(phase)
                    break  # DONE
    elif status == "ESCALATION_DEADLINE":
        trigger_escalation()
        break  # STOP (manual intervention required)
    else:
        sleep(6 hours)  # Continue monitoring
```

---

## CHECKPOINTS & GATES

| Checkpoint | When | Gate | Pass/Fail Action |
|-----------|------|------|---|
| **Blocker Answers** | Sept 3 06:00 UTC | All 4 answered? | Yes: Phase 1 | No: Escalate |
| **Blocker Analysis** | Sept 3 10:00 UTC | Decisions locked? | Yes: Phase 2 | No: Escalate |
| **Spike 1 Complete** | Sept 4 20:00 UTC | Code + tests done? | Yes: Phase 3 | No: Escalate |
| **Verification Pass** | Sept 4 AM | All gates pass? | Yes: Phase 4 | No: Investigate/fix |
| **Final Report** | Sept 4 EOD | Report delivered? | Yes: DONE ✅ | No: Pending |

---

## SUCCESS CRITERIA

✅ **Spike 1 PASS Conditions:**
1. All 7 tasks complete in ≤10h
2. API equivalence verified (old == new)
3. Audit trail complete (all events emitted + hash-chained)
4. Tenant isolation enforced (cross-tenant queries blocked)
5. All tests pass (unit + integration + E2E)
6. No regressions in console/bridges
7. Final report delivered with Phase 1b extrapolation

❌ **Spike 1 FAIL Conditions:**
1. Any blocker unanswered by Sept 3 10:00 UTC
2. Cumulative effort >10h by Sept 4 18:00 UTC
3. API equivalence fails (behavior difference between old/new)
4. Audit trail breaks (events missing or hash-chain breaks)
5. Tenant isolation violated
6. Any critical test fails
7. Regression in console/bridges

---

**Status:** Ready for autonomous execution.  
**Next Trigger:** When blocker answers received (Sept 3 06:00 UTC onwards)  
**Owner:** Spike 1 Dev (autonomous mode enabled)
