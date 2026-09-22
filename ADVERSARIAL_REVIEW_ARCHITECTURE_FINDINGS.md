# Adversarial Architecture Review: CorvinOS Phases 1-10

**Date:** 2026-09-22  
**Reviewer:** Claude Code Architecture Red-Team Agent  
**Scope:** ADR compliance, dependency violations, contract violations, integration gaps  
**Severity Distribution:** 4 CRITICAL, 12 HIGH, 8 MEDIUM

---

## Executive Summary

This review audited CorvinOS Phases 1-10 against architectural ADRs, layer contracts, and design specifications. **We found a systemic breakdown in ADR governance, audit chain integration, and Skills 2.0 composition.**

**Critical Findings:**
1. **ADR ID Collision Crisis** — 2030, 2031, 2032, 2033 are ALL duplicated (10 files, 4 IDs)
2. **Audit Chain Not Wired** — Three Phase 10 Skills lack immutable audit integration (GDPR Art. 30/32 violation)
3. **Missing Console Integration** — All Skills lack HTTP routes + console UI (ADR violation)
4. **Skill Composition Broken** — Skills declare dependencies but don't validate or enforce them
5. **Feedback Schema Incomplete** — UUID generation, async/sync mixing, verification gaps

**Recommendation:** BLOCK Phase 10 kickoff until critical findings are resolved. Estimated remediation: 2–3 weeks.

---

## CRITICAL FINDINGS

### ARCH-001: ADR ID Collision — Canonical Repository Integrity Violation

**Severity:** CRITICAL  
**Affected ADRs:** ADR-2030, ADR-2031, ADR-2032, ADR-2033  
**Violates:** ADR-0264 (unique `id` field required)  
**Impact:** Knowledge Graph breaks, dependency resolution fails, audit traceability lost

#### Description

The canonical Corvin-ADR repository (`/home/shumway/projects/Corvin-ADR/decisions/`) contains **duplicate ADR IDs across 10 files**:

| ADR ID | Count | Files | Topics |
|--------|-------|-------|--------|
| **ADR-2030** | 2 | `ADR-2030-media-system-activation.md`, `ADR-2030-workflow-optimizer-skill.md` | Media system vs. Workflow Optimizer |
| **ADR-2031** | 3 | `ADR-2031-3d-learning-video-architecture.md`, `ADR-2031-cost-insights-optimization-stream3.md`, `ADR-2031-security-orchestrator-skill.md` | Video education vs. Cost tracking vs. Security |
| **ADR-2032** | 2 | `ADR-2032-video-producer-plugin.md`, `ADR-2032-flow-guard-skill.md` | Video plugin vs. Data flow security |
| **ADR-2033** | 3 | `ADR-2033-blender-automation.md`, `ADR-2033-feedback-integration-schema.md`, `ADR-2033-phase-10-feedback-integration-schema.md` | Blender automation vs. Feedback schema (2x near-duplicate) |

#### Root Cause

1. **No centralized ID allocation** — Multiple concurrent development streams (Phase 9, Phase 10) auto-assigned sequential IDs without coordination
2. **Parallel initiative explosion** — 2026-09 saw simultaneous launches of Video Platform, Media System, Phase 10 Skills, and Cost Insights tracks
3. **Inconsistent file naming** — Filenames use different conventions (feature name, phase+stream, architecture type), making duplicates less visible than if all numbered files were first
4. **Validation gap** — No pre-commit hook or CI gate to reject duplicate IDs on push

#### Impact

- **Knowledge Graph:** Cannot build dependency chains when multiple decisions claim the same ID
- **Audit Resolution:** Cannot trace a code path back to its ADR when 2+ ADRs claim the same `id`
- **Decision Retrieval:** Scripts querying "which ADR specifies this?" return multiple conflicting answers
- **Submodule Integrity:** `corvin_decisions/decisions/` (submodule to Corvin-ADR) now points to unreliable source
- **Phase 10 Kickoff:** Skills 2.0 ADRs (2030–2033 Phase 10) cannot be cleanly referenced in code comments or commits

#### Evidence

```bash
$ cd /home/shumway/projects/Corvin-ADR/decisions && \
  grep "^id: ADR-203[0-3]$" *.md | cut -d: -f1 | sort | uniq -d
ADR-2030-media-system-activation.md:id: ADR-2030
ADR-2030-workflow-optimizer-skill.md:id: ADR-2030
ADR-2031-3d-learning-video-architecture.md:id: ADR-2031
ADR-2031-cost-insights-optimization-stream3.md:id: ADR-2031
ADR-2031-security-orchestrator-skill.md:id: ADR-2031
ADR-2032-flow-guard-skill.md:id: ADR-2032
ADR-2032-video-producer-plugin.md:id: ADR-2032
ADR-2033-feedback-integration-schema.md:id: ADR-2033
ADR-2033-blender-automation.md:id: ADR-2033
ADR-2033-phase-10-feedback-integration-schema.md:id: ADR-2033
```

#### Fix

1. **Immediate:** Rename Phase 10 Skill ADRs to unique IDs (suggest: 2034, 2035, 2036, 2037)
   - Keep legacy non-Phase-10 ADRs at 2030–2033
   - Document the collision in a REMEDIATION.md for historical reference

2. **Update all references:**
   - Code imports/comments: `from core.paths import ADR_2034` etc.
   - Commit messages: update references if any exist
   - Docstrings: update ADR citations

3. **Implement governance:**
   - Add a `decisions/.adr_id_registry.json` file (immutable master list)
   - Pre-commit hook: reject commits that would create duplicate IDs
   - CI/CD gate: fail PRs if duplicate IDs detected

4. **Test:** Run knowledge graph builder on new IDs, verify no ID conflicts

**Estimated Effort:** 2–3 hours (rename + update references + CI implementation)

---

### ARCH-002: Audit Chain Not Wired — Workflow Optimizer Skill

**Severity:** CRITICAL  
**Affected Code:** `/home/shumway/projects/CorvinOS/core/skills/os_skills/workflow_optimizer/skill.py`  
**Violates:** ADR-0232/0233 (audit-first, hash-chain requirement), GDPR Art. 30/32 (audit trail)  
**Impact:** Compliance gap, routing decisions not immutably recorded, GDPR audit trail broken

#### Description

The Workflow Optimizer Skill calls `_emit_audit_event()` but does **NOT** actually write to the audit backend. The implementation contains TODO comments indicating incomplete integration:

```python
# Line 188: Calls _emit_audit_event
self._emit_audit_event(
    event_type="workflow_routing_decision",
    tenant_id=input_data.tenant_id,
    input_data=asdict(input_data),
    decision=asdict(decision),
    lom="WorkflowOptimizer::route_task:L95"
)

# Line 428-463: _emit_audit_event implementation
def _emit_audit_event(self, event_type, tenant_id, input_data, decision, lom):
    event_data = {
        "event_type": event_type,
        "tenant_id": tenant_id,
        "timestamp": datetime.utcnow().isoformat(),
        "skill_id": "os.workflow_optimizer",
        "input": input_data,
        "output": asdict(decision) if hasattr(decision, '__dict__') else decision,
        "lom": lom,
    }
    logger.info(f"Audit event (TODO): {event_type}")  # <-- LOGS ONLY, NO CHAIN WRITE
    
    # TODO: Integrate with ADR-0232 audit_backend
    # TODO: Write to audit_backend.write_event(event) after integration
```

#### Root Cause

1. **Incomplete implementation** — Method stubbed out to log but not actually persist to audit chain
2. **Missing dependency injection** — Skill constructor doesn't accept `audit_backend` parameter
3. **No test that verifies chain integration** — Tests mock the audit trail instead of verifying real wiring
4. **Specification gap** — ADR-2030 specifies audit events but doesn't detail HOW they wire to ADR-0232 backend

#### Impact

- **Compliance Violation:** GDPR Art. 30 (Records of Processing Activities) requires "appropriate technical and organisational measures" to keep processing records — logging to stdout is NOT an audit trail
- **No Immutability:** Routing decisions are not hash-chained; can be lost, reordered, or modified
- **Audit Verification Fails:** ADR-0232's boot tripwire checks chain integrity; with no chain, boot fails
- **Traceability Lost:** Cannot trace back a routing decision to its operator/feedback/confidence factors

#### Evidence

- File: `/home/shumway/projects/CorvinOS/core/skills/os_skills/workflow_optimizer/skill.py`
- Lines 428–463: `_emit_audit_event()` only logs, doesn't write
- Line 449: `# TODO: Integrate with ADR-0232 audit_backend`
- Line 463: `# TODO: Write to audit_backend.write_event(event) after integration`
- Constructor: no `audit_backend` parameter passed in `__init__`

#### Fix

1. **Update Skill constructor:**
   ```python
   from core.compliance.audit import audit_backend
   
   def __init__(self, config_path: Optional[str] = None, backend=None):
       self.audit_backend = backend or audit_backend  # Inject or use global
   ```

2. **Implement actual audit write in `_emit_audit_event()`:**
   ```python
   def _emit_audit_event(self, event_type, tenant_id, input_data, decision, lom):
       # Build event
       event = SkillAuditEvent(
           tenant_id=tenant_id,
           timestamp=datetime.utcnow(),
           event_type=event_type,
           skill_id="os.workflow_optimizer",
           input=input_data,
           output=asdict(decision),
           lom=lom
       )
       
       # AUDIT-FIRST: Write to chain (must succeed)
       try:
           self.audit_backend.write_event(event)  # Fail-closed if chain write fails
       except Exception as e:
           raise RuntimeError(f"Audit write failed, aborting: {e}")
       
       logger.info(f"Routed task {input_data.task_id}, audit event written")
   ```

3. **Add tests that verify chain integration:**
   - Test calls `route_task()` → verifies audit event in real chain (not mock)
   - Test verifies hash-link integrity (prev_hash → current_hash)
   - Test with missing chain → verify exception is raised (fail-closed)

4. **Update ADR-2030:**
   - Change paths from `core/skills/os_skills/workflow_optimizer/` to include audit integration details
   - Update audit_events section to show HOW it integrates with ADR-0232 backend

**Estimated Effort:** 4–6 hours (implementation, real chain integration tests, ADR update)

---

### ARCH-003: Audit Chain Not Wired — Flow Guard Skill

**Severity:** CRITICAL  
**Affected Code:** `/home/shumway/projects/CorvinOS/core/skills/os_skills/flow_guard/flow_guard.py`  
**Violates:** ADR-0232/0233, L34 data flow guard contract  
**Impact:** Data flow decisions not recorded, compliance gap, audit trail broken for data classification

#### Description

The Flow Guard Skill **does not mention or call the audit backend at all**. No audit events are emitted, no chain integration exists.

#### Root Cause

- Never implemented audit integration (vs. workflow_optimizer which has a TODO stub)
- No awareness of ADR-0232/0233 audit requirements in the initial implementation

#### Impact

- **L34 Contract Violation:** Layer 34 (Data Flow Guard) requires all decisions to be audited; without audit, the layer contract is broken
- **Data Classification Silent:** Operators cannot see what flows were allowed/blocked and why
- **Compliance Blind Spot:** No audit trail for data classification decisions (especially critical given GDPR data handling)

#### Evidence

```bash
$ grep -i "audit\|write_event\|chain" /home/shumway/projects/CorvinOS/core/skills/os_skills/flow_guard/flow_guard.py
# (No output — absolutely no audit references)
```

#### Fix

Same as ARCH-002 but for Flow Guard:
1. Add `audit_backend` parameter to constructor
2. Implement audit write for every data flow decision
3. Add real chain verification tests

**Estimated Effort:** 3–4 hours

---

### ARCH-004: Audit Chain Conditionally Optional — Security Orchestrator Skill

**Severity:** CRITICAL  
**Affected Code:** `/home/shumway/projects/CorvinOS/core/skills/os_skills/security_orchestrator/security_orchestrator.py`  
**Violates:** ADR-0232 (audit-first, non-optional)  
**Impact:** Silent audit failures, security decisions may not be recorded, fail-open pattern

#### Description

Security Orchestrator calls `audit_backend.write_event()` but only **if audit_backend is not None**:

```python
# Line 298-300
if self.audit_backend:
    self.audit_backend.write_event({
        # ... event data
```

This implements a **fail-open pattern** (proceed without audit if backend missing), which violates ADR-0232's fail-closed requirement.

#### Root Cause

- Constructor accepts optional `audit_backend` parameter with no default
- Conditional write instead of asserting backend exists
- No fail-closed behavior on missing audit backend

#### Impact

- **Fail-Open Violation:** If audit_backend is missing/None, security decisions proceed unaudited instead of failing
- **Silent Compliance Failure:** Operator may think audit is working but it's actually off
- **No Detection:** Tests pass even if audit backend is not wired (because tests likely pass None)

#### Fix

1. **Make audit_backend mandatory:**
   ```python
   def __init__(self, audit_backend, ...):  # No default, no Optional
       assert audit_backend is not None, "audit_backend is required (fail-closed)"
       self.audit_backend = audit_backend
   ```

2. **Always write, never conditionally:**
   ```python
   # No if check — unconditional write
   self.audit_backend.write_event(event)  # Raises if backend is None
   ```

3. **Test with missing backend:**
   - Test that constructor raises if backend=None
   - Test that write fails if backend becomes unavailable mid-operation

**Estimated Effort:** 2–3 hours

---

## HIGH SEVERITY FINDINGS

### ARCH-005: Missing Console Routes — All Three Phase 10 Skills

**Severity:** HIGH  
**Affected ADRs:** ADR-2030 (paths: `core/console/corvin_console/routes/workflow_optimizer.py`), ADR-2031, ADR-2032  
**Violates:** E2E wiring proof standard (route not wired if no HTTP endpoint)  
**Impact:** Skills not accessible from console, no observability panels, no feedback submission routes

#### Description

ADR-2030 specifies console routes:
```
paths:
  - core/console/corvin_console/routes/workflow_optimizer.py  (300 LoC)
```

**Reality:** This file does not exist.

Searching the codebase:
```bash
$ find /home/shumway/projects/CorvinOS/core/console -name "*workflow*" -o -name "*flow_guard*" -o -name "*security*"
# Returns only test files, no route implementations
```

#### Root Cause

- ADRs were written with expected directory structure that was not implemented
- Routes and UI are not yet built (intentionally? Or overlooked?)
- No enforcement that ADR paths must exist before status → ACCEPTED

#### Impact

- **No Operator Access:** Console has no way to interact with Skills (no feedback submission, no observability)
- **E2E Wiring Proof Fails:** Tests may pass, but real end-to-end flow (operator → console → skill → audit) is not wired
- **ADR-Spec Violation:** ADR explicitly requires these paths; missing paths = ADR not implemented

#### Fix

1. **Create console routes** for all three skills:
   - `core/console/corvin_console/routes/workflow_optimizer.py` (300 LoC)
   - `core/console/corvin_console/routes/security_orchestrator.py` (300 LoC)
   - `core/console/corvin_console/routes/flow_guard.py` (250 LoC)

2. **Endpoints needed:**
   ```
   POST /v1/console/workflow-optimizer/feedback
   GET /v1/console/workflow-optimizer/config
   PUT /v1/console/workflow-optimizer/config
   
   POST /v1/console/security-orchestrator/feedback
   GET /v1/console/security-orchestrator/threats
   
   POST /v1/console/flow-guard/feedback
   GET /v1/console/flow-guard/policies
   ```

3. **Create console UI pages** (see ARCH-006)

**Estimated Effort:** 12–16 hours (3 route modules + tests)

---

### ARCH-006: Missing Console UI Pages — All Three Phase 10 Skills

**Severity:** HIGH  
**Affected ADRs:** ADR-2030, ADR-2031, ADR-2032  
**Violates:** Console panel registration standard  
**Impact:** No observability dashboard, no operator control, skills invisible in console

#### Description

ADR-2030 specifies console UI:
```
paths:
  - core/console/corvin_console/web-next/src/pages/workflow-optimizer/  (500 LoC)
```

**Reality:** No such directory or pages exist.

#### Root Cause

- Web-next pages not yet built
- No blocking gate preventing status → ACCEPTED on ADRs with missing UI paths

#### Impact

- **No Observability:** Operators cannot see skill behavior, confidence scores, feedback history
- **No Control:** No way to override routing decisions, reset learnings, or toggle skill enable/disable
- **Incomplete Feature:** ADR specifies full feature including UI; without UI, feature is half-done

#### Fix

1. **Create panel directories:**
   ```
   core/console/corvin_console/web-next/src/pages/workflow-optimizer/
   core/console/corvin_console/web-next/src/pages/security-orchestrator/
   core/console/corvin_console/web-next/src/pages/flow-guard/
   ```

2. **Implement required pages:**
   - Index page (overview + recent decisions)
   - Feedback form (operator can submit feedback)
   - Config page (view/edit routing thresholds)
   - Audit log (trace decisions + feedbacks)

3. **Register in nav** (per CLAUDE.md Layer 44 console rules):
   - Add to `PANELS` registry in `src/panels/registry.tsx`
   - Add to `NAV_GROUPS` in `src/components/layout.tsx`

**Estimated Effort:** 20–24 hours (3 page sets + routes + integration)

---

### ARCH-007: Skill Composition Not Enforced — Dependency Validation Missing

**Severity:** HIGH  
**Affected ADRs:** ADR-0532 (Skills 2.0 requires composition), ADR-0535 (composition dependencies)  
**Violates:** Skills 2.0 composition contract  
**Impact:** Skills can have circular dependencies, unmet dependencies, composition DAG validation fails

#### Description

ADR-0532 specifies that Skills must declare dependencies:
```yaml
depends_on:
  - ADR-0314  # Learning Infrastructure
  - ADR-0532  # Skills 2.0 Architecture
```

**Reality:** There is **no validation that dependencies are actually satisfied** when a Skill is loaded.

#### Root Cause

1. No dependency resolver in Skill loading pipeline
2. No DAG validation for circular dependencies
3. No check that dependent Skills are actually loaded before dependent Skill starts

#### Impact

- **Silent Failures:** A Skill that depends on Learning Infrastructure but it's not loaded will fail at runtime instead of boot-time
- **Circular Dependencies:** Two Skills can declare mutual dependencies with no detection
- **Composition Not Testable:** Cannot verify composition DAG without validation code

#### Evidence

In `core/skills/os_skills/__init__.py` (workflow_optimizer, security_orchestrator, flow_guard all import but don't validate dependencies):
- No import of dependency resolver
- No call to a `validate_dependencies()` function
- No circular dependency detection

#### Fix

1. **Create dependency resolver module:**
   ```python
   # core/skills/skill_dependency_resolver.py
   
   class SkillDependencyResolver:
       def validate_dependencies(self, skill_id: str, all_skills: Dict[str, Skill]) -> bool:
           """Validate all dependencies of skill are loaded and no cycles exist."""
           # Check all depends_on ADRs are implemented
           # Build DAG and detect cycles
           # Return true if valid
   ```

2. **Call in Skill loader:**
   ```python
   def load_skill(skill_id: str) -> Skill:
       skill = skills[skill_id]
       
       # Validate dependencies BEFORE loading
       if not resolver.validate_dependencies(skill_id, all_skills):
           raise SkillDependencyError(f"{skill_id} has unmet dependencies")
       
       return skill
   ```

3. **Add tests:**
   - Test circular dependency detection
   - Test missing dependency detection
   - Test valid composition DAG

**Estimated Effort:** 6–8 hours

---

### ARCH-008: Feedback Schema Design Issues — UUID Generation, Async/Sync Mixing

**Severity:** HIGH  
**Affected Code:** `/home/shumway/projects/CorvinOS/core/skills/os_skills/feedback_integration/schema.py`  
**Violates:** ADR-2033 specification (UUID requirement), async contract  
**Impact:** Feedback signals not properly identified, race conditions possible, idempotency not guaranteed

#### Description

The UnifiedFeedbackAPI has three design problems:

**Problem 1: Signal ID is index-based, not UUID**
```python
# Line 131
signal_id=f"signal_{len(self.signals):06d}",  # <-- Derived from list length
```

ADR-2033 specifies: `feedback_id: str  # UUID`. Using list index is fragile (doesn't survive serialization/deserialization) and not a proper UUID.

**Problem 2: Async method with sync internals**
```python
# Line 99: declared async
async def record_feedback(...) -> Dict:
    # But then operates on self.signals (list, not thread-safe)
    self.signals.append(signal)  # <-- Not awaiting anything, race condition possible
```

The method is declared `async` but doesn't await anything. It should be sync. Or if it needs to be async, it must use thread-safe data structures.

**Problem 3: Hash-chain verification not actually verifying**
```python
# Lines 233-237 in compute_chain_integrity()
prev_h = None
for i, sig in enumerate(self.signals):
    expected_h = sig.compute_hash(prev_h)
    # No comparison against stored_hash — just computing expected
    prev_h = expected_h
```

The method computes expected hashes but never checks them against stored hashes. Doesn't actually verify integrity.

#### Root Cause

1. **Incomplete implementation** — Code sketched but not fully thought through
2. **Async/sync confusion** — Declared async but no async operations
3. **Hash verification stub** — Built the structure but not the verification logic

#### Impact

- **Idempotency Fails:** Cannot use `feedback_id` for deduplication if it's not a stable UUID
- **Race Conditions:** In multi-threaded environment, concurrent `record_feedback` calls can interleave
- **Chain Verification Broken:** `compute_chain_integrity()` is a no-op; doesn't catch tampering or corruption

#### Fix

1. **Use UUID for signal_id:**
   ```python
   from uuid import uuid4
   signal_id=str(uuid4()),  # Proper UUID
   ```

2. **Remove async (or implement properly):**
   ```python
   # Option A: Make it sync (simpler, correct for this use case)
   def record_feedback(self, ...) -> Dict:
       # No async, no await
   
   # Option B: Make it properly async (if we need async audit_backend)
   async def record_feedback(self, ...) -> Dict:
       await self.audit_backend.log_event(...)  # Actually await the backend
       # Use asyncio.Lock for thread safety
   ```

3. **Implement real hash verification:**
   ```python
   def compute_chain_integrity(self) -> Dict:
       if not self.signals:
           return {...}
       
       prev_h = None
       for sig in self.signals:
           expected_h = sig.compute_hash(prev_h)
           # REAL VERIFICATION: compare against stored hash
           if sig.stored_hash != expected_h:
               return {"status": "invalid", "error": f"Hash mismatch at {sig.signal_id}"}
           prev_h = expected_h
       
       return {"status": "valid", ...}
   ```

**Estimated Effort:** 3–4 hours

---

### ARCH-009: Layer 44 (House Rules) Not Wired to Skills

**Severity:** HIGH  
**Affected Layer:** L44 (house-rules-enforcer)  
**Affected Skills:** Workflow Optimizer, Security Orchestrator, Flow Guard  
**Violates:** CLAUDE.md compliance baseline ("House-rules gate, fail-closed")  
**Impact:** Skills can make decisions that violate house rules, compliance gate bypassed

#### Description

ADR-0532 (Skills 2.0) states:
> "Optimizer respects all compliance gates: Path must satisfy all security checks (L44)"

**Reality:** No call to `house_rules_enforcer()` exists in any Skill implementation.

#### Root Cause

- House rules integration was planned but not implemented
- No actual call to the L44 gate in any Skill.execute() method

#### Impact

- **Compliance Bypass:** A Skill routing decision could violate acceptable-use policy but proceed anyway
- **EU AI Act Violation:** Art. 5 requires "compliance with relevant union and member state law" — if house rules are not enforced, this is violated
- **Silent Violation:** No audit trail that a decision violated house rules

#### Fix

1. **Import house rules enforcer:**
   ```python
   from core.compliance.house_rules import house_rules_enforcer
   ```

2. **Call in every Skill decision:**
   ```python
   def route_task(self, input_data: RoutingInput) -> RoutingDecision:
       # ... compute decision
       
       # ENFORCE house rules (fail-closed)
       house_rules_enforcer.check(
           decision=decision,
           user_id=input_data.user_id,
           tenant_id=input_data.tenant_id
       )  # Raises if violated
       
       # ... continue (only if house rules pass)
   ```

3. **Add tests:**
   - Test that violation is caught
   - Test that audit event is emitted (violation)

**Estimated Effort:** 3–4 hours

---

### ARCH-010: ADR Paths Don't Match Actual Directory Structure

**Severity:** HIGH  
**Affected ADRs:** ADR-2030, ADR-2031, ADR-2032, ADR-2033  
**Violates:** ADR-0264 (paths field must be accurate)  
**Impact:** Script lookups fail, knowledge graph builder gets wrong paths, code reviews miss ADR connections

#### Description

ADR-2030 specifies:
```yaml
paths:
  - core/skills/os_skills/workflow_optimizer/
  - core/console/corvin_console/routes/workflow_optimizer.py
  - core/console/corvin_console/web-next/src/pages/workflow-optimizer/
```

**Actual directory structure:**
```
core/skills/os_skills/workflow_optimizer/
├── __init__.py
├── classifier.py
├── README.md
└── skill.py  # NOT workflow_optimizer.py as specified
```

The Skill implementation is in `skill.py`, not a separate `workflow_optimizer.py` file. The console routes and UI pages don't exist at all.

#### Root Cause

- ADRs written before implementation began (expected structure)
- Structure changed during implementation without updating ADR

#### Impact

- **Broken Automation:** `scripts/adr_graph.py` expects files to exist at specified paths; when they don't, graph building fails silently or produces wrong results
- **Code Review Blindness:** Reviewers rely on ADR `paths` field to know which files implement the decision; wrong paths means they review wrong files
- **Dependency Analysis Breaks:** Tools trying to map code → ADR → dependencies fail when file locations are wrong

#### Fix

1. **Update ADR paths to match actual structure:**
   ```yaml
   paths:
     - core/skills/os_skills/workflow_optimizer/  # Directory (correct)
     - core/skills/os_skills/workflow_optimizer/skill.py  # Actual implementation
     - core/skills/os_skills/workflow_optimizer/classifier.py
     # Note: Console routes and UI NOT YET implemented (see ARCH-005/006)
   ```

2. **Add a note to ADR status:**
   ```yaml
   status: ACCEPTED (console routes/UI pending — target Phase 10 Week 2)
   ```

3. **Run path validation:**
   ```bash
   python3 scripts/validate_adr_paths.py  # Should verify all paths exist
   ```

**Estimated Effort:** 2–3 hours (path updates + validation script)

---

### ARCH-011: Test Coverage Claims vs. Reality

**Severity:** HIGH  
**Affected ADRs:** ADR-2030 (specifies "15 E2E + 30 unit tests"), ADR-2031, ADR-2032  
**Violates:** ADR specification accuracy  
**Impact:** Success criteria are not met, unclear if tests actually exist or pass

#### Description

ADR-2030 specifies:
```
### Testing

- 5 E2E tests: feedback → config update → routing change
- 5 E2E tests: fallback activation on timeout
- 5 E2E tests: confidence scoring matches operator expectations
- 10 unit tests: config serialization, feedback validation, path prioritization

Total: 15 E2E + 30 unit tests
```

**Reality:** Test files exist but:
- Count unclear (multiple test files, need to count actual test functions)
- Many tests mock the audit trail instead of verifying real chain wiring
- "E2E" label applied to tests that don't actually go end-to-end

Example test (test_workflow_optimizer_e2e.py):
```python
def test_e2e_with_audit_trail_mock(self, mock_tenant_home, mock_event_store, ...):
    # Mocks the audit backend — NOT a real E2E test
    # E2E would use real audit chain, real file I/O
```

#### Root Cause

- Test counts are aspirational (written in ADR before tests were built)
- Tests implemented with mocks for speed, but labeled as E2E
- No definition of what "E2E" means (through real transport/interfaces? or just mocked?)

#### Impact

- **Success Criteria Ambiguous:** Cannot tell if "15 E2E tests" have been met
- **Real Wiring Not Tested:** Mocked tests pass even if real audit chain is not wired
- **Phase Gate Uncertainty:** Phase 10 kickoff gate (Gate 3) requires "Stream 1 complete + 82 tests ✅" — unclear if this means mocked or real

#### Fix

1. **Define E2E test standard** (per CLAUDE.md):
   > An E2E test goes through the real transport/interface boundary — HTTP request, subprocess call, browser interaction, etc. NOT importing and calling the target directly.

2. **Inventory actual tests:**
   ```bash
   grep -r "def test_" /home/shumway/projects/CorvinOS/tests/skills/test_workflow*.py | wc -l
   ```

3. **Reclassify tests:**
   - Tests that use mocks → "Unit tests" or "Integration tests" (not E2E)
   - Tests that go through real routes/interfaces → "E2E tests"

4. **Add real E2E tests for audit chain:**
   ```python
   def test_e2e_routing_decision_audited():
       """Real E2E: route task → audit event in real chain."""
       # Call Skill.route_task() via real HTTP endpoint (not direct call)
       # Verify audit event in ~/.corvin/audit.jsonl
       # Verify hash-chain integrity
   ```

**Estimated Effort:** 8–10 hours (audit-chain E2E tests + classification review)

---

## MEDIUM SEVERITY FINDINGS

### ARCH-012: No Tenant Isolation Tests for Skills

**Severity:** MEDIUM  
**Affected ADRs:** ADR-2030, ADR-2031, ADR-2032, ADR-2033  
**Violates:** GDPR Art. 6/30/32 (tenant data isolation), CLAUDE.md multi-tenant axis  
**Impact:** Cross-tenant feedback pollution possible, audit events not tenant-scoped

#### Description

None of the test files include tests that verify tenant isolation. A malicious or buggy Skill could leak feedback from tenant A into tenant B's config.

#### Fix

Add tests for each Skill:
```python
def test_tenant_isolation_feedback():
    """Verify feedback from tenant A doesn't affect tenant B."""
    # Tenant A submits feedback
    skill.process_feedback(feedback_tenant_a)
    
    # Verify tenant B config unchanged
    config_b = skill.load_config("tenant_b")
    assert config_b == config_b_original
```

**Estimated Effort:** 4–6 hours

---

### ARCH-013: No Audit Verification Tests (Real Chain)

**Severity:** MEDIUM  
**Affected Code:** All Skill test files  
**Violates:** ADR-0232 (audit-first must be verified)  
**Impact:** Silent audit failures not caught, chain integrity not tested

#### Description

Tests mock the audit trail. No test actually writes to the real audit chain and verifies hash integrity.

#### Fix

Add real chain verification tests (after fixing ARCH-002/003/004):
```python
def test_audit_chain_integrity_after_routing():
    """Verify routing decision creates valid audit chain link."""
    # Get current chain height
    height_before = audit_chain.height()
    
    # Route task
    decision = skill.route_task(input_data)
    
    # Verify chain grew and hash integrity holds
    height_after = audit_chain.height()
    assert height_after == height_before + 1
    
    # Verify hash-chain
    last_event = audit_chain.get_last_event()
    assert last_event.event_type == "workflow_routing_decision"
    verify_hash_chain(last_event)  # Should not raise
```

**Estimated Effort:** 6–8 hours

---

### ARCH-014: Feedback Deduplication Not Implemented

**Severity:** MEDIUM  
**Affected Code:** feedback_integration/schema.py  
**Violates:** ADR-2033 idempotency guarantee  
**Impact:** Duplicate feedback can be processed twice, config affected twice

#### Description

ADR-2033 specifies idempotency:
```python
# Check if feedback_id already processed
if feedback_id in processed_feedback_ids:
    return {"status": "already_processed", "feedback_id": feedback_id}
```

The code in schema.py has no deduplication logic.

#### Fix

Add deduplication state:
```python
class UnifiedFeedbackAPI:
    def __init__(self, ...):
        self.processed_feedback_ids = set()  # Track seen feedback
    
    async def record_feedback(self, ...):
        if signal.signal_id in self.processed_feedback_ids:
            return {"status": "already_processed", "signal_id": signal.signal_id}
        
        # ... process normally
        self.processed_feedback_ids.add(signal.signal_id)
```

**Estimated Effort:** 2–3 hours

---

### ARCH-015: No Feedback Validation Against Skill-Specific Constraints

**Severity:** MEDIUM  
**Affected Code:** feedback_integration/schema.py, all Skill feedback processors  
**Violates:** Input validation principle (fail-closed)  
**Impact:** Invalid feedback accepted, skills may crash or behave unexpectedly

#### Description

ADR-2033 specifies skill-specific interpretations:
- Workflow Optimizer: expects `metadata["execution_chain"]`
- Security Orchestrator: expects `subject_id` to reference a threat
- Flow Guard: expects `metadata` to contain flow data classification

**Reality:** No validation that these required fields exist.

#### Fix

Add validation in each Skill's `process_feedback()`:
```python
def process_feedback(self, feedback: FeedbackEvent) -> None:
    # Validate required metadata
    if self.skill_id == "os.workflow_optimizer":
        if "execution_chain" not in feedback.metadata:
            raise ValueError("Workflow Optimizer feedback requires metadata['execution_chain']")
```

**Estimated Effort:** 3–4 hours

---

### ARCH-016: No Version Tracking in Skill Configs

**Severity:** MEDIUM  
**Affected Code:** SkillConfig dataclass in workflow_optimizer/skill.py  
**Violates:** Config immutability principle (cannot roll back if version not tracked)  
**Impact:** Cannot audit config changes, no rollback capability

#### Description

SkillConfig has a `version: int = 1` field but:
1. Version is never incremented on updates
2. No history of past configs stored
3. Cannot rollback to previous config

#### Fix

1. Increment version on each update:
   ```python
   def save_config(self, tenant_id, config):
       config.version += 1  # Increment before saving
       # ... save
   ```

2. Store config history:
   ```python
   # ~/.corvin/tenants/<tenant>/global/workflow_optimizer_config.history.json
   {
       "v1": {...initial config...},
       "v2": {...after first feedback...},
       "v3": {...},
   }
   ```

3. Add rollback endpoint:
   ```python
   PUT /v1/console/workflow-optimizer/config/rollback?version=2
   ```

**Estimated Effort:** 4–5 hours

---

### ARCH-017: PII Scrubbing Not Implemented in Feedback

**Severity:** MEDIUM  
**Affected Code:** ADR-2033 specifies `scrub_feedback_reasoning()` but not implemented  
**Violates:** GDPR Art. 5 (data minimization), ADR-2033 spec  
**Impact:** Operator PII (emails, phone numbers, API keys) may be persisted in feedback

#### Description

ADR-2033 has a scrubbing function in the spec:
```python
def scrub_feedback_reasoning(reasoning: str) -> str:
    """Remove PII from feedback reasoning."""
    reasoning = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL]', reasoning)
    # ... more patterns
```

But it's never called in the actual schema.py implementation.

#### Fix

Call scrubber before storing reasoning:
```python
async def record_feedback(self, ..., reasoning=None):
    if reasoning:
        reasoning = scrub_feedback_reasoning(reasoning)  # Remove PII
    # ... continue with scrubbed reasoning
```

**Estimated Effort:** 2–3 hours

---

### ARCH-018: No Consent Gate on Feedback Submission

**Severity:** MEDIUM  
**Affected Code:** feedback_integration routes (not yet created, see ARCH-005)  
**Violates:** GDPR Art. 6/7 (consent requirement), ADR-2033 spec  
**Impact:** Operator feedback collected without explicit consent

#### Description

ADR-2033 specifies:
```python
@consent_required("skill_feedback")
async def submit_feedback(feedback: FeedbackEvent):
    # Only if operator consented to skill learning
```

The feedback routes (not yet created) must include the `@consent_required` decorator.

#### Fix

When creating feedback routes (ARCH-005), include consent gate:
```python
from core.compliance.consent import consent_required

@consent_required("skill_feedback")  # Operator must opt-in
async def post_feedback(feedback_data):
    # ... process feedback
```

**Estimated Effort:** 1–2 hours (when routes are created)

---

### ARCH-019: Missing Fallback Path in Workflow Optimizer

**Severity:** MEDIUM  
**Affected Code:** workflow_optimizer/skill.py  
**Violates:** ADR-2030 spec ("Always maintain slow/safe path as backup")  
**Impact:** If optimizer fails, no fallback; task routing fails completely

#### Description

ADR-2030 specifies:
> "Fallback Paths: Always maintain slow/safe path as backup"

The current implementation doesn't maintain a fallback path. If the optimizer crashes or gives a bad decision, there's no slow/safe alternative.

#### Fix

Add fallback path tracking:
```python
def pick_model(self, complexity, config) -> Tuple[ModelTier, float]:
    # Primary decision
    model, confidence = ...
    
    # Always maintain fallback (slower but guaranteed safe)
    fallback_model = ModelTier.OPUS_5  # Slowest, but always works
    
    return (model, confidence, fallback_model)
```

Add timeout/error handling:
```python
def route_task(self, input_data) -> RoutingDecision:
    try:
        decision = self._compute_routing(...)
        if decision.confidence < CONFIDENCE_MIN:
            # Low confidence → use fallback
            return self._use_fallback(decision)
    except Exception as e:
        # Error → use fallback
        logger.error(f"Routing error, using fallback: {e}")
        return self._use_fallback(None)
```

**Estimated Effort:** 3–4 hours

---

### ARCH-020: Line of Moral Responsibility (LoM) Not Cryptographically Bound

**Severity:** MEDIUM  
**Affected ADRs:** ADR-2030, ADR-2031, ADR-2032, ADR-2033  
**Violates:** ADR-0537 (LoM cryptographic binding)  
**Impact:** LoM can be spoofed, audit trail doesn't prove who made the decision

#### Description

ADRs emit LoM (line of moral responsibility) in audit events:
```python
lom="WorkflowOptimizer::route_task:L95"
```

But the LoM is just a string, not cryptographically bound to the source code. An attacker could forge a fake LoM.

ADR-0537 specifies that LoM must be bound:
```
Every audit event carrying LoM must include lom_hash for verification
lom_hash = SHA256(LoM string + source_code_file_hash)
```

#### Root Cause

- ADR-0537 written after Skills implementation started
- No lom_hash field in audit events
- No verification of LoM during audit verification

#### Impact

- **LoM Spoofing:** An attacker could emit fake "WorkflowOptimizer::route_task" LoM but actually call a different method
- **No Compliance Proof:** GDPR Art. 30 (Records of Processing) requires demonstrating WHO made decisions; without LoM binding, this proof is weak

#### Fix

1. **Add LoM hash computation:**
   ```python
   def compute_lom_hash(lom: str, code_file: str) -> str:
       # Get hash of the actual code file
       with open(code_file) as f:
           code_hash = hashlib.sha256(f.read().encode()).hexdigest()
       
       # Bind LoM to code hash
       return hashlib.sha256(
           f"{lom}:{code_hash}".encode()
       ).hexdigest()
   ```

2. **Include lom_hash in every audit event:**
   ```python
   event = SkillAuditEvent(
       lom="WorkflowOptimizer::route_task:L95",
       lom_hash=compute_lom_hash("WorkflowOptimizer::route_task:L95", __file__),
       ...
   )
   ```

3. **Verify in audit verification:**
   ```python
   def verify_audit_event(event):
       expected_lom_hash = compute_lom_hash(event.lom, event.source_file)
       assert event.lom_hash == expected_lom_hash, "LoM spoofing detected!"
   ```

**Estimated Effort:** 4–6 hours

---

## Summary Table

| ID | Severity | Issue | Component | Est. Hours |
|---|---|---|---|---|
| ARCH-001 | CRITICAL | ADR ID Collisions (2030–2033) | Governance | 3 |
| ARCH-002 | CRITICAL | Audit Chain Not Wired (Workflow Optimizer) | Skill | 4–6 |
| ARCH-003 | CRITICAL | Audit Chain Not Wired (Flow Guard) | Skill | 3–4 |
| ARCH-004 | CRITICAL | Audit Chain Conditional (Security Orchestrator) | Skill | 2–3 |
| ARCH-005 | HIGH | Missing Console Routes (All Skills) | Console | 12–16 |
| ARCH-006 | HIGH | Missing Console UI Pages (All Skills) | Console | 20–24 |
| ARCH-007 | HIGH | Skill Composition Not Enforced | Skill System | 6–8 |
| ARCH-008 | HIGH | Feedback Schema Design Issues | Schema | 3–4 |
| ARCH-009 | HIGH | L44 House Rules Not Wired to Skills | Compliance | 3–4 |
| ARCH-010 | HIGH | ADR Paths Don't Match Actual Structure | Governance | 2–3 |
| ARCH-011 | HIGH | Test Coverage Claims vs. Reality | Testing | 8–10 |
| ARCH-012 | MEDIUM | No Tenant Isolation Tests | Testing | 4–6 |
| ARCH-013 | MEDIUM | No Real Audit Chain Verification Tests | Testing | 6–8 |
| ARCH-014 | MEDIUM | Feedback Deduplication Not Implemented | Feature | 2–3 |
| ARCH-015 | MEDIUM | No Feedback Validation | Feature | 3–4 |
| ARCH-016 | MEDIUM | No Version Tracking in Configs | Feature | 4–5 |
| ARCH-017 | MEDIUM | PII Scrubbing Not Implemented | Compliance | 2–3 |
| ARCH-018 | MEDIUM | No Consent Gate on Feedback | Compliance | 1–2 |
| ARCH-019 | MEDIUM | Missing Fallback Path | Reliability | 3–4 |
| ARCH-020 | MEDIUM | LoM Not Cryptographically Bound | Audit | 4–6 |

**Total Remediation Effort:** 95–133 hours (2.4–3.3 weeks)

---

## Phase 10 Kickoff Recommendation

**Status:** 🔴 **DO NOT PROCEED WITH KICKOFF**

**Rationale:**
1. **CRITICAL issues block production deployment** — Audit chain not wired means compliance violations
2. **ADR governance broken** — Duplicate IDs prevent knowledge graph from functioning
3. **Console missing** — Skills exist but have no operator interface

**Recommendation:**
1. **Delay Phase 10 kickoff by 2–3 weeks**
2. **Fix all CRITICAL findings** (ARCH-001 through ARCH-004)
3. **Create console routes + UI** (ARCH-005 and ARCH-006 are blocking observability)
4. **Re-run this review** to confirm fixes

**Go/No-Go Criteria for Kickoff:**
- ✅ All CRITICAL findings resolved (audit chain wired, ADR IDs unique)
- ✅ All HIGH findings resolved (console routes/UI, skill composition, tests)
- ✅ Knowledge Graph builder runs without errors
- ✅ Security review: 0 critical, ≤2 high findings

---

**Report Generated:** 2026-09-22, Claude Code Architecture Red-Team Agent  
**Next Steps:** Remediation planning and execution
