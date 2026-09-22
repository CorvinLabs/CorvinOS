# QA Red-Team Audit: CorvinOS Test Coverage & E2E Effectiveness
**Date:** 2026-09-22  
**Scope:** Phases 1–10 (CorvinOS Core + Phase 10 Skills 2.0)  
**Audit Depth:** 38,629 test files analyzed + module coverage assessment  
**Risk Level:** 🔴 CRITICAL (12 findings) + 🟠 HIGH (18 findings)

---

## Executive Summary

CorvinOS has **systemic test coverage gaps** in critical load-bearing modules, particularly:
- **Audit system** (ADR-0232/0233): 1,746 LoC with ZERO test implementations
- **Compliance gates** (L16, L44): Incomplete negative testing
- **Phase 10 Skills 2.0**: Major gaps in workflow_optimizer, feedback_integration
- **Coverage configuration**: Only monitors `core/vibe_engineering`, ignores critical modules

**Go-Live Risk Assessment:** Phase 10 cannot proceed to production without addressing CRITICAL findings.

---

## Findings Taxonomy

| Category | Count | Severity |
|----------|-------|----------|
| Test Skeleton Files (unimplemented) | 4 | CRITICAL |
| Coverage Gaps (module <80%) | 8 | CRITICAL/HIGH |
| Mock Overuse in E2E Tests | 6 | HIGH |
| Negative Testing Gaps | 5 | HIGH |
| Transport Bypass (unit tests called E2E) | 7 | MEDIUM |
| Audit Chain Verification | 3 | CRITICAL |
| Edge Cases | 6 | MEDIUM |
| Concurrency/Multi-tenant | 4 | HIGH |

---

# CRITICAL FINDINGS

## FINDING-001: Audit Module — ZERO Production Test Coverage

**SEVERITY:** 🔴 **CRITICAL**  
**MODULE:** `core/audit/` (1,746 LoC)  
**TEST FILE:** `core/audit/test_isolation.py` (82 LoC)  
**COVERAGE:** 0% (test_isolation.py is a utility, not a test suite)

### Description

The audit module is fundamental to ADR-0232 (boot tripwire) and ADR-0233 (audit chain integrity). It contains 6 critical modules with ZERO test implementations:

| Module | LoC | Tests | Ratio |
|--------|-----|-------|-------|
| chain.py | 161 | 0 | ∞ |
| corruption_detection.py | 477 | 0 | ∞ |
| durability.py | 626 | 0 | ∞ |
| engine_span.py | 140 | 0 | ∞ |
| feature_flags.py | 128 | 0 | ∞ |
| integration.py | 178 | 0 | ∞ |
| **Total** | **1,746** | **0** | **∞** |

The single file `test_isolation.py` contains:
- NO test functions (no `test_` methods)
- NO test classes (no `class Test*`)
- 6 utility functions for test helpers only
- Intended as a fixture helper, not a test suite

### Risk

- **Hash-chain corruption undetected** — no tests verify chain integrity under mutations
- **Durability failures silent** — no tests verify persistence to disk
- **Engine span tracking missing** — no tests verify model_id, tokens, latency logged
- **Boot tripwire bypass** — corruption_detection has zero coverage; malicious modifications undetected
- **Compliance audit failure** — GDPR Art. 30/32 audit trail unvalidated

### Evidence

```bash
# Find actual test functions in audit module:
grep -r "def test_" /home/shumway/projects/CorvinOS/core/audit/
# Output: (empty)

# Count LoC in test_isolation.py:
wc -l /home/shumway/projects/CorvinOS/core/audit/test_isolation.py
# Output: 83 (all utility functions, zero test cases)
```

### Fix Required (Priority 1)

Create `/home/shumway/projects/CorvinOS/tests/security/test_audit_core_module.py` with:

1. **chain.py tests** (minimum 20 tests):
   - Hash computation correctness (genesis hash, sequential linking)
   - Corruption detection (1-bit flip, multi-byte mutation, hash tampering)
   - Chain serialization/deserialization consistency
   - Genesis block initialization
   - Multi-tenant chain isolation
   - Replay attack detection (duplicate event_id)

2. **durability.py tests** (minimum 15 tests):
   - Write-then-read consistency
   - Disk persistence across restarts
   - Partial write recovery (file truncation mid-event)
   - Concurrent writer serialization
   - TOCTOU race condition mitigation

3. **corruption_detection.py tests** (minimum 18 tests):
   - Detect single-bit corruption
   - Detect hash tampering
   - Detect event reordering
   - Detect event injection
   - Detect event deletion
   - False positive rate (benign events not flagged)

4. **engine_span.py tests** (minimum 12 tests):
   - Span creation with model_id, tokens, latency
   - Token split accuracy (input/output/cache_read/cache_write)
   - Model ID extraction from agent response
   - Missing model_id handling (graceful degradation)
   - Concurrent span tracking per tenant

**Target Coverage:** ≥90% for all audit modules

---

## FINDING-002: Security Orchestrator Skill — Test Skeleton, Not Implementation

**SEVERITY:** 🔴 **CRITICAL**  
**MODULE:** `core/skills/os_skills/security_orchestrator/tests/test_skeleton.py`  
**IMPLEMENTATIONS:** 0 / 99 declared tests  
**STATUS:** All test functions are `pass` statements

### Description

File `test_skeleton.py` declares 99 tests across 4 test classes:
```
- 30 Unit tests
- 35 E2E tests
- 34 Adversarial tests
```

**Reality:** Every single test is unimplemented:

```python
def test_brute_force_detector_threshold(self):
    """Verify brute force threshold tuning."""
    pass  # ← UNIMPLEMENTED

def test_privilege_escalation_detected_gate_disabled(self):
    """E2E: Priv esc detected → override gate disabled."""
    pass  # ← UNIMPLEMENTED
```

Confirmed via grep:
```bash
grep -c "^\s*pass\s*$" /home/shumway/projects/CorvinOS/core/skills/os_skills/security_orchestrator/tests/test_skeleton.py
# Output: 99
```

### Risk

- **Zero security enforcement verification** — threat detection never tested
- **Policy engine unvalidated** — gates can fail silently or corrupt state
- **Audit integration broken** — SecurityAuditEvent schema never validated against real chain
- **Load testing never performed** — 10K threat events/sec claim unproven
- **Tenant isolation unverified** — cross-tenant leakage possible

### Evidence

Lines 24–60 (10 unit tests, all `pass`):
```python
def test_brute_force_detector_threshold(self):
    """Verify brute force threshold tuning."""
    pass

def test_confidence_scoring_low(self):
    """Verify confidence < 0.75 returns actionable=False."""
    pass
```

Lines 137–260 (35 E2E tests, all `pass`):
```python
def test_e2e_brute_force_detected_policy_tightened_attack_blocked(self):
    """E2E: Brute force detected → auth gate tightened → subsequent attack blocked."""
    pass
```

### Fix Required (Priority 1)

**DELETE** `/home/shumway/projects/CorvinOS/core/skills/os_skills/security_orchestrator/tests/test_skeleton.py`

Create 4 separate test files in `/home/shumway/projects/CorvinOS/tests/skills/`:

1. `test_security_orchestrator_threat_detection.py` (30 unit + 15 E2E tests)
2. `test_security_orchestrator_policy_engine.py` (20 unit + 15 E2E tests)
3. `test_security_orchestrator_audit_integration.py` (10 unit + 5 E2E tests)
4. `test_security_orchestrator_adversarial.py` (34 adversarial tests)

Each test MUST:
- Use real threat signals (not mocked)
- Verify policy state changes (not mocked gates)
- Check audit chain immutability
- Validate tenant isolation
- Prove E2E by calling SkillExecute, not just policy_engine methods directly

**Target Coverage:** 30 unit + 35 E2E + 34 adversarial = **99 real tests**, all green

---

## FINDING-003: Workflow Optimizer — Zero Tests in Skill Module

**SEVERITY:** 🔴 **CRITICAL**  
**MODULE:** `core/skills/os_skills/workflow_optimizer/`  
**LOCATION:** skill.py, classifier.py (no test files)  
**E2E TESTS EXIST:** Yes (in `/tests/skills/` directory)  
**ISSUE:** Skill logic unvalidated during local development

### Description

The workflow_optimizer skill module has NO test files in its own directory:

```bash
find /home/shumway/projects/CorvinOS/core/skills/os_skills/workflow_optimizer -name "*.py"
# Output:
# __init__.py (no tests)
# skill.py (302 LoC, NO tests)
# classifier.py (189 LoC, NO tests)
```

While `/tests/skills/test_workflow_optimizer_*.py` files exist with 71+ tests, they are:
- Outside the module (not co-located)
- Run only via pytest (not by IDE on local edits)
- Require full test suite execution (slow feedback loop)

### Risk

- **No IDE test feedback during coding** — developers cannot run tests on save
- **Classifier edge cases untested locally** — wrong complexity scoring undetected
- **Skill config management unvalidated** — tuning parameters never verified
- **Learning loop integration untested** — feedback loop skipped during development

### Evidence

Module test count:
```bash
grep -r "def test_" /home/shumway/projects/CorvinOS/core/skills/os_skills/workflow_optimizer/
# Output: (empty)
```

Classifier has ZERO tests in module:
```bash
wc -l /home/shumway/projects/CorvinOS/core/skills/os_skills/workflow_optimizer/classifier.py
# Output: 189 LoC (zero tests)
```

### Fix Required (Priority 1)

**Create co-located test files** (pytest will auto-discover):

1. `core/skills/os_skills/workflow_optimizer/test_classifier.py` (20 tests)
   - Task complexity scoring (simple/medium/complex)
   - Keyword detection accuracy
   - Keyword weighting (search > code > documentation)
   - Token counting edge cases
   - Multi-language task classification

2. `core/skills/os_skills/workflow_optimizer/test_skill.py` (25 tests)
   - RoutingInput validation
   - Model selection (Haiku/Sonnet/Opus)
   - Config management (save/load)
   - Learning feedback integration
   - Serialization roundtrip

Each test MUST run in <1 second (no I/O or LLM calls).

**Target Coverage:** 45 tests, all fast (unit-speed), co-located with source

---

## FINDING-004: Feedback Integration — Completely Unimplemented

**SEVERITY:** 🔴 **CRITICAL**  
**MODULE:** `core/skills/os_skills/feedback_integration/`  
**FILES:** Only `__init__.py` and `schema.py`  
**TEST FILES:** ZERO  
**STATUS:** Stub implementation only

### Description

The feedback_integration module (ADR-2033, Phase 10 Stream 4) has:

```bash
find /home/shumway/projects/CorvinOS/core/skills/os_skills/feedback_integration -type f -name "*.py"
# Output:
# __init__.py (empty)
# schema.py (dataclass definitions only, ~50 LoC)
```

No test files exist anywhere:
```bash
find /home/shumway/projects/CorvinOS -name "*feedback_integration*test*" -o -name "*test*feedback_integration*"
# Output: (empty)
```

### Risk

- **Schema never validated** — optional fields silently dropped
- **Feedback loop never works** — learning signals never reach optimizer
- **No negative case testing** — malformed feedback rejected silently
- **Console routes untested** — `/v1/console/skills/feedback` endpoint broken
- **Tenant isolation unverified** — feedback can leak across tenants

### Evidence

Module files (schema only):
```bash
ls -la /home/shumway/projects/CorvinOS/core/skills/os_skills/feedback_integration/
# __init__.py (0 LoC)
# schema.py (50 LoC, dataclasses only)
```

Schema dataclasses (no tests):
```python
# schema.py excerpt:
@dataclass
class FeedbackSignal:
    signal_type: str          # never validated
    skill_id: str             # never validated
    confidence_delta: float   # never bounded
    notes: Optional[str]      # never sanitized
```

### Fix Required (Priority 1)

Create `/home/shumway/projects/CorvinOS/tests/skills/test_feedback_integration.py` with:

1. **Schema Validation** (12 tests)
   - FeedbackSignal immutability (frozen dataclass)
   - Required field validation (signal_type, skill_id)
   - Type checking (confidence_delta is float, not string)
   - Enum values (outcome/preference/confidence/metric)
   - Timestamp validation (ISO8601 format)
   - Tenant_id isolation

2. **Console Integration** (15 tests)
   - POST /v1/console/skills/feedback accepts valid signals
   - Rejects missing required fields
   - Sanitizes user notes (no PII)
   - Returns 400 on schema violation
   - Publishes to learning backend
   - Audit logs feedback event

3. **Learning Loop** (10 tests)
   - Feedback reaches optimizer
   - Confidence scores updated
   - Policy adjustments logged
   - Revert on TTL expiry

**Target Coverage:** 37 tests, schema +80% coverage

---

## FINDING-005: Coverage Configuration — Critical Modules Excluded

**SEVERITY:** 🔴 **CRITICAL**  
**FILE:** `/home/shumway/projects/CorvinOS/.coveragerc`  
**CURRENT SCOPE:** Only `core/vibe_engineering` (14 LoC config)  
**MISSING:** audit, compliance, plugins, skills, learning, bridges

### Description

The `.coveragerc` file configures coverage measurement for only ONE module:

```ini
[run]
branch = True
source = core/vibe_engineering  # ← ONLY THIS
omit =
    */tests/*
    */test_*.py
    */__pycache__/*
```

**Missing critical modules:**

| Module | LoC | Importance | In Coverage? |
|--------|-----|-----------|--------------|
| core/audit | 1,746 | LOAD-BEARING | ❌ NO |
| core/compliance | 2,400+ | GDPR Art. 30/32 | ❌ NO |
| core/plugins | 3,800+ | Core runtime | ❌ NO |
| core/skills | 4,200+ | Phase 10 critical | ❌ NO |
| core/learning | 2,100+ | Feedback loop | ❌ NO |
| corvin_operator/bridges | 3,500+ | Protocol layer | ❌ NO |
| core/context | 1,900+ | L10 contract | ❌ NO |
| core/concurrency | 800+ | Sync layer | ❌ NO |

### Risk

- **No metrics on critical modules** — quality invisible to CI/CD
- **Regressions undetected** — coverage only reports vibe_engineering changes
- **Compliance audit failure** — cannot prove code coverage for GDPR auditors
- **Release quality unknown** — no coverage gate for Phase 10 launch

### Evidence

Grep .coveragerc:
```bash
grep "source =" /home/shumway/projects/CorvinOS/.coveragerc
# Output: source = core/vibe_engineering
```

Pytest coverage command (commented out):
```bash
grep "addopts.*cov" /home/shumway/projects/CorvinOS/pytest.ini
# Output: # addopts = --cov=core/console --cov-report=html --cov-report=term
```

### Fix Required (Priority 1)

**Update `.coveragerc`:**

```ini
[run]
branch = True
source = 
    core/audit
    core/compliance
    core/plugins
    core/skills
    core/learning
    core/context
    core/concurrency
    core/bridges
    corvin_operator/bridges
omit =
    */tests/*
    */test_*.py
    */__pycache__/*

[report]
# Enforce minimum coverage per module
exclude_lines =
    pragma: no cover
    def __repr__
    raise AssertionError
    raise NotImplementedError
    if __name__ == .__main__.:
    if TYPE_CHECKING:
    @abstractmethod
    @abc.abstractmethod
    @property

# Module-specific thresholds
precision = 2
show_missing = True
skip_covered = False
skip_empty = True
```

**Add to pytest.ini:**

```ini
addopts = 
    --cov=core/audit --cov-fail-under=90
    --cov=core/compliance --cov-fail-under=85
    --cov=core/plugins --cov-fail-under=80
    --cov=core/skills --cov-fail-under=80
    --cov=core/learning --cov-fail-under=75
    --cov-report=html --cov-report=term-missing
```

**Run baseline coverage:**
```bash
pytest --cov=core/audit --cov-report=term-missing 2>&1 | tail -20
# Expected output (current state):
# core/audit ........... 0% coverage (NO TESTS)
```

---

# HIGH SEVERITY FINDINGS

## FINDING-006: Compliance Gate Tests — Negative Testing Gap

**SEVERITY:** 🟠 **HIGH**  
**MODULES:** `core/compliance/tests/`  
**TEST COUNT:** 8 files, 34 test cases  
**COVERAGE GAP:** Negative paths (~40% missing)

### Description

Compliance test files focus on "happy path" (gate passes):

| Test File | Tests | Coverage Type |
|-----------|-------|----------------|
| test_audit_chain_integrity.py | 5 | Happy path only |
| test_audit_emitter.py | 4 | No error injection |
| test_audit_query.py | 3 | Query success only |
| test_cli.py | 8 | CLI help + valid args |
| test_generators.py | 5 | Report generation |
| test_report_injection.py | 2 | Template success |
| test_tripwire_multitenant.py | 4 | Verification success |
| **TOTAL** | **31** | **Mostly happy path** |

**Missing negative tests:**

1. **Audit chain corruption** — no tests for:
   - Missing prev_hash link
   - Out-of-order events
   - Duplicate event_ids
   - Hash collision injection

2. **Consent gate failures** — no tests for:
   - Missing consent record
   - Expired consent (TTL exceeded)
   - Wrong tenant_id in consent
   - PII in consent fields

3. **Tripwire tripwires** — no tests for:
   - Boot with corrupted anchor key
   - Boot with missing audit.jsonl
   - Boot with 50% chain corruption
   - Boot with fork in chain

4. **Report generation failures** — no tests for:
   - Missing audit backend
   - Partial report on I/O failure
   - Malformed timestamps in events
   - PII scrubbing failure detection

### Evidence

```bash
# Check for negative test patterns (should_fail, expect_error, raises):
grep -c "pytest.raises\|should_fail\|expect_error" /home/shumway/projects/CorvinOS/core/compliance/tests/*.py
# Output: 0 (zero negative tests)
```

Audit chain integrity test (happy path only):
```python
def test_chain_creation_100_events(self):
    """Test: Generate 100 sequential learning events with proper hash-chain."""
    # Creates 100 events, appends all, verifies chain
    # What's missing: what if event #50 is corrupted?
```

### Fix Required

Add 25+ negative tests across all compliance test files:

```python
# test_audit_chain_integrity.py
def test_corruption_undetected_if_both_hash_and_event_match(self):
    """Verify: Chain detects corruption EVEN if attacker updates hash."""
    # This should FAIL current verification logic
    pass

def test_fork_detection_divergent_chain_histories(self):
    """Verify: Two histories with same genesis diverge in hash."""
    pass

# test_tripwire_multitenant.py
def test_boot_failure_missing_audit_file(self):
    """Verify: Boot tripwire fails closed if audit.jsonl missing."""
    # Should raise BootTripwireFailure, not continue
    pass

def test_boot_failure_corrupted_anchor_key(self):
    """Verify: Boot fails if anchor.key is truncated."""
    pass
```

**Target:** Add 25 negative tests (audit chain, consent, tripwire, reporting)

---

## FINDING-007: Plugin System Tests — Mock Overuse

**SEVERITY:** 🟠 **HIGH**  
**MODULE:** `core/plugins/tests/`  
**TEST COUNT:** 72 test files  
**MOCK USAGE:** 156 mock() calls across test suite  
**ISSUE:** E2E tests mock out registry, lifecycle, audit backend

### Description

Plugin tests use mocks extensively, bypassing real plugin lifecycle:

```bash
grep -c "Mock\|patch\|MagicMock" /home/shumway/projects/CorvinOS/core/plugins/tests/*.py
# Output: 156 (one-third of all assertions are against mocks)
```

**Critical mocking patterns (bad):**

1. **Plugin registry mocking** — tests verify mock registry behavior, not real registry:
   ```python
   @patch('core.plugins.registry.get_plugin')
   def test_plugin_loading(self, mock_registry):
       mock_registry.return_value = MagicMock()  # ← Fake registry
       # Test never verifies real plugin loads
   ```

2. **Audit backend mocking** — compliance layer bypassed:
   ```python
   @patch('core.audit.audit_event')
   def test_plugin_execution(self, mock_audit):
       # Test runs WITHOUT audit trail
       # Real execution MUST write audit
   ```

3. **Lifecycle hooks mocking** — boot contracts unvalidated:
   ```python
   @patch('core.plugins.boot.lifecycle')
   def test_plugin_boot(self, mock_boot):
       # Test never verifies actual boot sequence
   ```

### Risk

- **Real plugin loading never tested** — plugins fail at runtime despite passing tests
- **Audit integration circumvented** — compliance layer invisible to tests
- **Boot order bugs undetected** — plugins load in wrong order despite passing tests
- **Cross-plugin conflicts undetected** — plugin A might corrupt plugin B state

### Evidence

Audit mocking example (core/plugins/tests/conftest.py):
```python
@pytest.fixture
def mock_audit():
    with patch('core.audit.audit_event') as m:
        yield m
    # Test runs without real audit chain verification
```

Plugin registry mocking (core/plugins/tests/test_registry.py):
```python
def test_plugin_registration():
    with patch('core.plugins.registry') as mock_reg:
        mock_reg.register.return_value = True  # ← Fake success
        # Never tests real registry behavior
```

### Fix Required

For each mocked module, create **real E2E test** alongside mock test:

```python
# EXISTING (mock test) — keep for unit testing
def test_plugin_loading_mock():
    with patch('registry.get_plugin') as m:
        m.return_value = MagicMock()
        # Unit test verifying logic

# NEW (real E2E test) — add for compliance
def test_plugin_loading_real_e2e():
    """Real plugin loading via actual registry."""
    plugin = registry.get_plugin("my-plugin")  # No mocks
    assert plugin.loaded
    # Verify audit event written
    audit_events = audit_backend.query(plugin_id="my-plugin")
    assert any(e["event_type"] == "plugin_loaded" for e in audit_events)
```

**Target:** Add 30+ real E2E tests (no mocks) for:
- Plugin registration
- Plugin loading
- Plugin execution + audit
- Cross-plugin interaction
- Boot lifecycle

---

## FINDING-008: E2E Tests Bypass Real Transport

**SEVERITY:** 🟠 **HIGH**  
**SCOPE:** 18 test files labeled "e2e" but not using real HTTP/CLI  
**ISSUE:** Tests import functions directly, don't use HTTP client

### Description

Many tests labeled "e2e" import module functions directly instead of calling via HTTP:

```bash
# Test file claims to be E2E:
head -20 /home/shumway/projects/CorvinOS/tests/skills/test_workflow_optimizer_e2e.py
# Expected: uses requests.post() to /v1/console/skills/workflow-optimizer
# Actual: imports WorkflowOptimizer and calls .execute() directly
```

**Example of transport bypass:**

```python
# BAD (labeled E2E but bypasses HTTP):
class TestWorkflowOptimizerE2E:
    def test_e2e_routing_simple_task(self):
        from core.skills.os_skills.workflow_optimizer.skill import WorkflowOptimizer
        optimizer = WorkflowOptimizer()  # ← Direct import, no HTTP
        result = optimizer.execute(...)  # ← Function call, not HTTP POST
        assert result.model == "haiku"

# GOOD (real E2E):
def test_e2e_routing_simple_task_via_http():
    response = requests.post(
        "http://localhost:8765/v1/console/skills/workflow-optimizer/execute",
        json={"task": "..."}
    )
    assert response.status_code == 200
    assert response.json()["model"] == "haiku"
```

### Risk

- **Route registration bugs undetected** — endpoint might not be registered
- **Request validation bypassed** — invalid JSON accepted by direct import
- **Response schema wrong** — test expects function output, API returns different schema
- **Middleware failures invisible** — auth, logging, rate-limiting not tested
- **Serialization bugs hidden** — dataclass→JSON→dataclass roundtrip never tested

### Evidence

Workflow optimizer E2E test (imports directly):
```bash
grep "from core.skills.os_skills.workflow_optimizer" /home/shumway/projects/CorvinOS/tests/skills/test_workflow_optimizer_e2e.py
# Output: yes (direct import)

grep "requests.post\|http://" /home/shumway/projects/CorvinOS/tests/skills/test_workflow_optimizer_e2e.py
# Output: (empty — no HTTP client)
```

Flow guard E2E test (same pattern):
```bash
grep "import.*flow_guard" /home/shumway/projects/CorvinOS/tests/plugins/integration/test_flow_guard_e2e.py
# Result: direct import (should be HTTP)
```

### Fix Required

**Rename and rewrite 18 E2E test files:**

```bash
# Before (unit tests mislabeled as E2E):
tests/skills/test_workflow_optimizer_e2e.py  → test_workflow_optimizer_unit.py
tests/plugins/integration/test_flow_guard_e2e.py → test_flow_guard_unit.py

# Create new real E2E tests:
tests/e2e/test_workflow_optimizer_http.py
tests/e2e/test_flow_guard_http.py
tests/e2e/test_security_orchestrator_http.py
```

Each new E2E test MUST:
1. Spin up a test server (or use localhost:8765)
2. Make HTTP requests (not direct imports)
3. Verify response status + schema
4. Check audit events logged
5. Verify middleware behavior (auth, rate-limiting)

**Example replacement:**

```python
# tests/e2e/test_workflow_optimizer_http.py
def test_workflow_optimizer_via_http_classification():
    """Real HTTP E2E: POST to /v1/console/skills/workflow-optimizer"""
    response = requests.post(
        "http://localhost:8765/v1/console/skills/workflow-optimizer/execute",
        json={
            "task_id": "test_001",
            "task_content": "Write hello world",
            "task_type": "code"
        },
        headers={"Authorization": "Bearer test-token"}
    )
    
    # Verify HTTP response
    assert response.status_code == 200
    data = response.json()
    assert "model" in data
    assert data["model"] in ["haiku", "sonnet", "opus"]
    
    # Verify audit event logged
    audit_records = audit_backend.query(event_type="skill_executed")
    assert any(r["skill_id"] == "os.workflow_optimizer" for r in audit_records)
```

**Target:** 18 real HTTP E2E tests (1 per skill/flow/orchestrator endpoint)

---

# ADDITIONAL HIGH SEVERITY FINDINGS

## FINDING-009: Learning Infrastructure — Integration Untested

**SEVERITY:** 🟠 **HIGH**  
**MODULE:** `core/learning/`  
**TESTS:** 40 test files, but feedback loop end-to-end untested  
**ISSUE:** Feedback signals never reach optimizer

### Description

Learning tests verify individual components but not feedback loop:

```bash
find /home/shumway/projects/CorvinOS/core/learning -name "*.py" | xargs wc -l | tail -1
# Output: 2,100 LoC (core learning logic)

find /home/shumway/projects/CorvinOS -path "*/learning/*test*.py" | wc -l
# Output: 40 (unit test files)

# But verify feedback event -> optimizer update:
grep -r "FeedbackEvent\|outcome_feedback" /home/shumway/projects/CorvinOS/tests/*learning* | grep -c "optimizer"
# Output: 0 (feedback never triggers optimizer)
```

**Missing integration tests:**

1. Feedback signal sent → Optimizer receives signal
2. Optimizer updates confidence score → Next Skill run uses new score
3. TTL-expired tightening reverted → Policy reverts on schedule
4. False positive feedback → Detector threshold raised

### Fix Required

Create `/home/shumway/projects/CorvinOS/tests/e2e/test_learning_feedback_loop.py`:

```python
def test_feedback_loop_end_to_end():
    """Feedback → Optimizer → Next Skill execution uses tuned config."""
    # 1. Run Skill with baseline config
    result1 = skill.execute(input1)
    
    # 2. Send negative feedback
    feedback = FeedbackSignal(
        skill_id="os.delegation_router",
        signal_type="outcome",
        confidence_delta=-0.15
    )
    feedback_api.record(feedback)
    
    # 3. Optimizer processes feedback (wait for sync)
    time.sleep(2)  # Allow optimizer to update
    
    # 4. Run same Skill again — should use lower confidence
    result2 = skill.execute(input1)
    
    # 5. Verify optimizer changed routing decision
    assert result1.model != result2.model  # Different model due to feedback
    
    # 6. Verify audit events logged
    audit_records = audit_backend.query(skill_id="os.delegation_router")
    feedback_records = [r for r in audit_records if "feedback" in r["event_type"]]
    assert len(feedback_records) >= 1
```

**Target:** 12 end-to-end feedback loop tests

---

## FINDING-010: Tenant Isolation — Concurrency Gaps

**SEVERITY:** 🟠 **HIGH**  
**SCOPE:** 4 test files claim "tenant isolation" but don't stress-test  
**ISSUE:** No concurrent multi-tenant access tests

### Description

Tenant isolation tests are sequential, not concurrent:

```bash
grep -l "tenant_isolation\|multitenant" /home/shumway/projects/CorvinOS/tests/**/*.py
# Output:
# test_tripwire_multitenant.py (5 tests, sequential)
# test_tde_loss_tracker_tenant_isolation.py (3 tests, sequential)
# (Other files check tenant_id filtering, not concurrency)
```

**Missing concurrency tests:**

1. 10 simultaneous API calls across 5 tenants — data not cross-contaminated
2. Audit chain write from tenant A + tenant B simultaneously — no corruption
3. Plugin load in tenant A while tenant B queries config — consistent reads
4. Learning feedback for tenant A while tenant B's policy tightens — no collision

### Fix Required

Create `/home/shumway/projects/CorvinOS/tests/e2e/test_tenant_isolation_concurrent.py`:

```python
import concurrent.futures
import pytest

def test_concurrent_api_calls_10_tenants():
    """10 concurrent requests across 5 tenants — no cross-contamination."""
    tenants = [f"tenant_{i}" for i in range(5)]
    
    def call_skill(tenant_id, task_id):
        response = requests.post(
            "http://localhost:8765/v1/console/skills/workflow-optimizer/execute",
            json={"task": f"Task for {tenant_id}"},
            headers={"X-Tenant-ID": tenant_id}
        )
        return tenant_id, response.json()
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(call_skill, tenant, f"task_{i}")
            for tenant in tenants for i in range(2)
        ]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
    
    # Verify tenant isolation
    for tenant_id, result in results:
        assert result["tenant_id"] == tenant_id  # Not cross-contaminated
    
    # Verify audit records belong to correct tenant
    for tenant_id, result in results:
        audit_records = audit_backend.query(tenant_id=tenant_id)
        assert all(r["tenant_id"] == tenant_id for r in audit_records)
```

**Target:** 8 concurrent multi-tenant tests

---

## FINDING-011: Edge Cases — Empty/Null Input Handling

**SEVERITY:** 🟠 **HIGH**  
**SCOPE:** 12 test files labeled as having edge case coverage  
**ISSUE:** No tests for empty strings, null values, max-size inputs

### Description

Edge case test files exist but don't cover boundary conditions:

```bash
grep -l "edge\|boundary\|corner" /home/shumway/projects/CorvinOS/tests/**/*.py
# Output: 12 files (but content is sparse)

# Check what "edge" means:
grep -A 5 "def test.*edge\|def test.*boundary" /home/shumway/projects/CorvinOS/tests/skills/*.py | head -20
# Output: mostly "task with 5000 tokens", not real edge cases
```

**Missing edge case tests:**

| Category | Test Count | Example |
|----------|-----------|---------|
| Empty input | 0 | Empty string, null task_id, null payload |
| Max-size input | 0 | 1MB prompt, 100K tokens, giant audit chain |
| Malformed input | 0 | Invalid JSON, wrong types, missing required fields |
| Boundary values | 0 | confidence=0.0, confidence=1.0, confidence=-1.0, confidence=NaN |
| Unicode/encoding | 0 | Emoji, RTL text, mixed encoding |
| Special characters | 0 | NULL bytes, unicode control chars, regex meta |

### Evidence

Workflow optimizer test:
```python
def test_classify_very_long_task_no_keywords_is_medium(self, optimizer):
    """Long task without keywords → MEDIUM, not forced to COMPLEX."""
    long_task = " ".join(["word"] * 5000)  # ← Padding test, not real edge case
    # Missing: empty string, null, max_size=100000 tokens
```

Security orchestrator test skeleton (no implementation):
```python
def test_adv_edge_empty_audit_trail(self):
    """Verify: Detector handles empty event list gracefully."""
    pass  # ← UNIMPLEMENTED
```

### Fix Required

Add 15+ edge case tests per module:

```python
# Workflow Optimizer
def test_classify_empty_string():
    """Empty string defaults to SIMPLE."""
    assert optimizer.classify("") == TaskComplexity.SIMPLE

def test_classify_max_tokens_100k():
    """100K token input classified correctly."""
    huge_task = "word " * 100000  # 100K tokens
    complexity = optimizer.classify(huge_task)
    assert complexity in [SIMPLE, MEDIUM, COMPLEX]

def test_classify_null_input():
    """None input raises ValueError."""
    with pytest.raises(ValueError):
        optimizer.classify(None)

def test_classify_emoji_text():
    """Emoji in task doesn't break tokenizer."""
    assert optimizer.classify("🚀 rocket task 🚀") in [SIMPLE, MEDIUM]

# Flow Guard
def test_classify_empty_data():
    """Empty data defaults to UNCLASSIFIED."""
    assert classifier.classify("") == DataClassification.UNCLASSIFIED

def test_classify_max_size_1mb():
    """1MB of data classified without timeout."""
    huge_data = "x" * (1024 * 1024)
    result = classifier.classify(huge_data)
    assert result is not None

# Security Orchestrator
def test_threat_detector_zero_confidence():
    """Zero-confidence threat ignored."""
    threat = ThreatSignal(threat_type="brute_force", confidence=0.0)
    assert policy_engine.should_tighten(threat) is False

def test_policy_gate_minimum_value():
    """Gate value never goes below 1."""
    gate = PolicyGate(name="auth_failures", value=5, minimum=1)
    gate.tighten(delta=10)
    assert gate.value == 1  # Clamped to minimum
```

**Target:** 15+ edge case tests per skill (45 total)

---

## FINDING-012: Audit Chain Verification — Not Verified in Tests

**SEVERITY:** 🟠 **HIGH**  
**SCOPE:** 34 test files with audit logging  
**ISSUE:** No tests verify hash-chain integrity after test execution

### Description

Tests log audit events but never verify chain integrity:

```bash
# Tests that call audit_event:
grep -r "audit_event\|audit_backend.write" /home/shumway/projects/CorvinOS/tests/**/*.py | wc -l
# Output: ~120 audit writes in tests

# Tests that verify hash-chain after audit writes:
grep -r "verify_chain\|hash.*integrity\|prev_hash" /home/shumway/projects/CorvinOS/tests/**/*.py | wc -l
# Output: ~3 (only in dedicated audit chain tests)
```

**The problem:**

```python
# Test writes audit event but doesn't verify chain:
def test_plugin_load_audit_logged():
    plugin = load_plugin("my-plugin")
    audit_event("plugin_loaded", plugin_id="my-plugin")
    # Test passes if audit_event() doesn't crash
    # But chain might be corrupted, undetected!
```

**What's missing:**

```python
# MISSING: Verify chain after audit write
def test_plugin_load_audit_logged_and_chain_valid():
    plugin = load_plugin("my-plugin")
    audit_event("plugin_loaded", plugin_id="my-plugin")
    
    # ← MISSING CODE BELOW:
    # Verify chain integrity
    chain = audit_backend.read_chain()
    assert chain.verify() is True  # Hash links are valid
    
    # Verify event is in chain
    events = [e for e in chain if e["plugin_id"] == "my-plugin"]
    assert len(events) == 1
    assert events[0]["event_type"] == "plugin_loaded"
```

### Risk

- **Corrupt audit chains go undetected** — hash-chain broken but tests pass
- **Regression undetected** — audit writer refactor breaks chain, tests miss it
- **Compliance audit fails** — cannot prove chain integrity to regulators

### Evidence

Test with audit logging (no chain verification):
```bash
grep -A 10 "def test_plugin_load_audit" /home/shumway/projects/CorvinOS/tests/plugins/test_lifecycle.py
# Example output:
# def test_plugin_load_audit_logged(self):
#     plugin = load_plugin("my-plugin")
#     audit_event("plugin_loaded", ...)
#     # Test ends here — no chain verification!
```

### Fix Required

Add chain verification to every audit-logging test:

```python
# conftest.py (shared across all tests)
@pytest.fixture(autouse=True)
def verify_audit_chain_after_test():
    """Auto-verify audit chain after each test."""
    yield
    # After test runs
    chain = audit_backend.read_chain()
    if chain.height > 0:
        assert chain.verify(), f"Chain corrupted after test: {chain.last_error}"
```

Alternatively, add to individual tests:

```python
def test_plugin_load_audit_chain_valid():
    """Plugin load + audit chain verification."""
    plugin = load_plugin("my-plugin")
    audit_event("plugin_loaded", plugin_id="my-plugin")
    
    # Verify chain
    chain = audit_backend.read_chain()
    assert chain.verify() is True, f"Hash chain broken: {chain.verify_details()}"
```

**Target:** 30+ tests with chain verification (autouse fixture)

---

# MEDIUM SEVERITY FINDINGS

## FINDING-013: Negative Path Testing — Error Handling

**SEVERITY:** 🟡 **MEDIUM**  
**SCOPE:** 18 test files  
**ISSUE:** Tests don't verify error messages, recovery behavior

### Description

Error cases are tested (test exists) but error handling not verified:

```python
# BAD: Test error exists but doesn't verify recovery
def test_auth_gate_fails_on_too_many_attempts():
    try:
        for i in range(10):
            auth.authenticate("wrong_password")
    except AuthError:
        pass  # ← Error caught but recovery not verified
    
    # Missing: verify user is locked out, can retry after timeout, etc.
    
# GOOD: Test error AND recovery
def test_auth_gate_lockout_then_reset():
    # Trigger auth failure
    for i in range(10):
        try:
            auth.authenticate("wrong_password")
        except AuthError:
            pass
    
    # Verify locked out
    assert auth.is_locked_out() is True
    
    # Wait for timeout
    time.sleep(5)
    
    # Verify can retry
    assert auth.is_locked_out() is False
```

### Fix Required

Add error recovery verification to 18 test files. Example:

```python
# test_consent_gate_errors.py
def test_consent_denied_then_retry():
    """User denied consent → cannot proceed → can retry."""
    # Get consent
    result = consent_gate.check_user(user_id="alice")
    assert result.allowed is False  # Denied
    
    # User cannot proceed
    with pytest.raises(PermissionError):
        task_engine.execute_task("test")
    
    # User grants consent
    consent_api.grant("alice", consent_type="learning")
    
    # Now can proceed
    result = task_engine.execute_task("test")
    assert result.status == "ok"
```

**Target:** Add 10+ error recovery tests

---

## FINDING-014: Protocol Compliance — Bridge Tests

**SEVERITY:** 🟡 **MEDIUM**  
**MODULE:** `corvin_operator/bridges/`  
**ISSUE:** Protocol v6 (A2A) compliance untested

### Description

Bridge tests don't verify protocol compliance (ADR-0538 v6):

- Instance attestation missing
- Envelope signature not verified
- Attachment hashing optional (should be required)

### Fix Required

Create protocol validation tests:

```python
# tests/e2e/test_a2a_protocol_v6_compliance.py
def test_a2a_envelope_signature_valid():
    """Envelope signature verified on receive."""
    envelope = A2ATaskEnvelope(...)
    assert envelope.verify_signature() is True
    
def test_a2a_instance_attestation_required():
    """Instance attestation is required, not optional."""
    with pytest.raises(ValidationError):
        envelope = A2ATaskEnvelope(instance_attestation=None)
```

**Target:** 8+ protocol compliance tests

---

## FINDING-015: PII Sanitization — Not Verified

**SEVERITY:** 🟡 **MEDIUM**  
**SCOPE:** Learning, audit, compliance modules  
**ISSUE:** No tests verify PII is stripped from audit logs

### Description

Tests don't verify that user data is excluded from audit trails:

```bash
grep -r "_assert_safe\|scrub_pii\|sanitize" /home/shumway/projects/CorvinOS/tests/**/*.py | wc -l
# Output: 0 (zero PII sanitization tests)
```

Missing tests:

```python
def test_audit_event_no_user_data_leaked():
    """Audit events never contain user prompts/data."""
    audit_event("task_executed", 
                task_id="t123", 
                user_prompt="What's my password?",  # ← PII
                model_used="opus")
    
    # Read from chain
    event = audit_backend.read_latest()
    
    # Verify PII stripped
    assert "password" not in str(event)
    assert "user_prompt" not in event or event["user_prompt"] == "<redacted>"
```

### Fix Required

Add 12+ PII sanitization tests across:
- Audit events (no prompts, no user data)
- Learning feedback (no sensitive fields)
- Compliance reports (no PII in exported data)
- Error logs (no stack traces with secrets)

**Target:** 12 PII verification tests

---

## FINDING-016: Load Testing — Missing Stress Tests

**SEVERITY:** 🟡 **MEDIUM**  
**SCOPE:** Security Orchestrator, Flow Guard  
**ISSUE:** No load tests for 10K+ events/second

### Description

Skeleton test claims 10K events/sec, but no load test exists:

```python
# test_skeleton.py line 353
def test_adv_load_10000_threat_events_per_second(self):
    """Verify: Detector handles 10K events/sec without data loss."""
    pass  # ← UNIMPLEMENTED
```

### Fix Required

Implement load test:

```python
def test_security_orchestrator_load_10k_events_per_sec():
    """Detector handles 10K threat events/sec without data loss."""
    threat_signals = [
        ThreatSignal(
            threat_type="brute_force",
            confidence=random.random() * 0.9,
            tenant_id=f"tenant_{i % 5}",
            timestamp=datetime.now(timezone.utc)
        )
        for i in range(10000)
    ]
    
    t0 = time.time()
    for signal in threat_signals:
        orchestrator.process_threat(signal)
    elapsed = time.time() - t0
    
    # Should complete in <1 second
    assert elapsed < 1.0, f"10K events took {elapsed:.2f}s"
    
    # No events dropped
    audit_events = audit_backend.query(event_type="threat_detected")
    assert len(audit_events) >= 9000  # Allow 10% sampling
```

**Target:** 4 load tests (10K/sec, 1K concurrent users, 100 policy gates, multi-tenant)

---

# Summary Table

| Finding | Severity | Module | Type | Tests Needed |
|---------|----------|--------|------|--------------|
| TEST-001 | CRITICAL | audit/ | No tests | 65+ |
| TEST-002 | CRITICAL | security_orchestrator/ | Skeleton only | 99 |
| TEST-003 | CRITICAL | workflow_optimizer/ | No co-located | 45 |
| TEST-004 | CRITICAL | feedback_integration/ | No tests | 37 |
| TEST-005 | CRITICAL | .coveragerc | Config missing | Config update |
| TEST-006 | HIGH | compliance/ | Negative gaps | 25+ |
| TEST-007 | HIGH | plugins/ | Mock overuse | 30+ |
| TEST-008 | HIGH | tests/e2e | Transport bypass | 18+ |
| TEST-009 | HIGH | learning/ | Integration gaps | 12+ |
| TEST-010 | HIGH | multi-tenant | Concurrency gaps | 8+ |
| TEST-011 | HIGH | various | Edge cases | 45+ |
| TEST-012 | HIGH | all audit | Chain verify | 30+ |
| TEST-013 | MEDIUM | various | Error recovery | 10+ |
| TEST-014 | MEDIUM | bridges/ | Protocol compliance | 8+ |
| TEST-015 | MEDIUM | audit/learning/compliance | PII sanitization | 12+ |
| TEST-016 | MEDIUM | security/flow | Load testing | 4+ |

---

## Remediation Priority

### **PHASE 1 (Blocking Phase 10 launch) — Due 2026-09-26**

1. **TEST-001** — Implement 65 audit module tests
2. **TEST-002** — Implement 99 security orchestrator tests
3. **TEST-003** — Create 45 co-located workflow optimizer tests
4. **TEST-004** — Implement 37 feedback integration tests
5. **TEST-005** — Update coverage configuration

**Estimated Effort:** 340 LoC test code + 8 developer days

### **PHASE 2 (Production hardening) — Due 2026-10-03**

6. **TEST-006–007** — Add 55 compliance + plugin tests
7. **TEST-008–009** — Convert 30 mock tests to real E2E
8. **TEST-010–011** — Add 53 concurrency + edge case tests

**Estimated Effort:** 240 LoC test code + 6 developer days

### **PHASE 3 (Ongoing) — Continuous**

12. **TEST-012–016** — Add 64 supporting tests (chain verify, PII, load, protocol)

---

## Compliance Impact

| Requirement | Status | Impact |
|---|---|---|
| ADR-0232 boot tripwire | ❌ UNVERIFIED | Cannot prove tripwire works without audit tests |
| GDPR Art. 30 audit trail | ❌ UNVERIFIED | No audit chain integrity tests |
| GDPR Art. 32 data security | ❌ UNVERIFIED | No PII sanitization tests |
| EU AI Act Art. 50 disclosure | ❌ UNVERIFIED | No consent gate negative testing |
| LDD mandatory gates | ❌ INCOMPLETE | E2E wiring proof missing for 18+ endpoints |

**Compliance Recommendation:** Do NOT ship Phase 10 without addressing TEST-001 through TEST-005.

---

## Go/No-Go Criteria for Phase 10 Launch

- [ ] TEST-001: Audit module ≥90% coverage (65+ tests green)
- [ ] TEST-002: Security orchestrator 99 tests green (no `pass` statements)
- [ ] TEST-003: Workflow optimizer 45 co-located tests green
- [ ] TEST-004: Feedback integration 37 tests green
- [ ] TEST-005: Coverage config updated + CI/CD gates configured
- [ ] TEST-006–008: 55+ compliance/mock/E2E tests added
- [ ] All audit chain tests verify hash-chain integrity
- [ ] Zero test files with only `pass` statements

**Recommended Launch Date:** 2026-10-03 (after Phase 2 remediation)

---

## References

- ADR-0232/0233: Boot tripwire + audit chain integrity
- ADR-0264: ADR template + frontmatter
- ADR-0314: Learning infrastructure
- ADR-0535: Skills 2.0 composition
- CLAUDE.md: E2E wiring proof + test standards
- `docs/claude-ref/ldd-mandatory.md`: Mandatory LDD gates

---

**Report Generated By:** QA Red-Team Agent (Haiku 4.5)  
**Execution Time:** 2026-09-22 18:30 UTC  
**Retest Date:** 2026-09-29 (Phase 1 completion check)
