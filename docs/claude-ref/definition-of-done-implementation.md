# Definition-of-Done Verifier Implementation Guide

**Canonical Reference:** [ADR-0721](../../corvin_decisions/decisions/ADR-0721-dod-verifier-skill-architecture.md), [ADR-0722](../../corvin_decisions/decisions/ADR-0722-dod-loss-signal-learning-integration.md), [ADR-0723](../../corvin_decisions/decisions/ADR-0723-dod-implementation-plan.md)

**Last Updated:** 2026-09-26  
**Status:** Production-Ready (Wave 3 sign-off)

---

## Overview

The **Definition-of-Done Verifier** is a learnable Skill 2.0 that evaluates task completion against 5 canonical checks, computes a confidence score (0.0–1.0), and prevents incomplete work from being marked "done." It is wired into the console and bridges to the learning loop (ADR-0314).

**Key Properties:**
- ✅ **Fail-Closed:** No audit trail → task cannot be marked done
- ✅ **Learnable:** Check weights tune via feedback loop (ADR-0314)
- ✅ **Audit-First:** Every verification emitted to ADR-0232 chain
- ✅ **Tenant-Isolated:** GDPR Art. 5, 6 compliance (per-tenant checks + trails)

---

## Phase Breakdown

### Phase 1: Core Skill Architecture (COMPLETE)

**Deliverable:** 5 core DoD checks + scoring logic

| Check | Verifies | Fail-Closed Behavior |
|-------|----------|-----|
| **Reachability** | Symbol has real call site outside tests | No matches → FAIL |
| **Audit Trail** | Event emitted to ADR-0232 chain | No chain record → FAIL |
| **Test Evidence** | Test output file exists + contains PASS | File missing or FAIL in output → FAIL |
| **Docs Sync** | Code changes matched by doc updates | Changed files have no doc counterpart → FAIL |
| **Reproducibility** | Can re-run verification + get same score | Score divergence > tolerance → FAIL |

**Location:**
```
core/skills/os_skills/definition_of_done_verifier/
  ├── skill.py                 # Main Skill class + verification logic
  ├── checks/
  │   ├── reachability.py      # Call-site search (grep-based)
  │   ├── audit_trail.py       # Audit chain validation
  │   ├── test_evidence.py     # Test output parsing
  │   ├── docs_sync.py         # Doc/code correlation
  │   └── reproducibility.py   # Determinism check
  ├── scoring.py               # Score computation (weighted checks)
  └── hardening.py             # Timeout protection, fail-closed gates
```

**Key Definitions:**
- **score:** `(check_1_weight * check_1_result) + ... → [0.0, 1.0]`
- **passed:** `score >= threshold` (threshold = 0.80 for wave 3)
- **weights:** Learnable per check type (ADR-0722 tuning)

### Phase 2: Learning Loop Integration (IN PROGRESS)

**Deliverable:** Weight optimizer + feedback loop

| Component | Purpose | Status |
|-----------|---------|--------|
| **FeedbackEvent** | User input (accurate/inaccurate/not_applicable) | ✅ Defined |
| **WeightOptimizer** | Adjusts check weights via feedback | 🟡 Wired to learning store |
| **LossSignal** | Computes confidence delta | ✅ Implemented |
| **Learning Loop** | Closes: feedback → weights → next verification | 🟡 Pipeline active |

**API (Feedback Loop):**
```python
# Step 1: Run verification
POST /api/dod/verify
  body: { task_id, task_type, commit_range, symbol_name }
  response: { score: 0.85, passed: true, checks: {...}, audit_event_id }

# Step 2: Submit feedback
POST /api/dod/feedback
  body: { task_id, check_name, feedback: "accurate", note: "..." }
  response: { accepted: true, weight_adjustment: -0.02 }

# Step 3: Weights tune automatically
#   (learning optimizer runs daily, updates registry)
```

**Learning Integration (ADR-0314):**
- **EventStore:** `/home/shumway/projects/CorvinOS/core/learning/event_store.py`
- **Event Types:** `dod_verification_executed`, `dod_feedback_received`, `dod_weight_updated`
- **Tenant Scope:** All queries filtered by `tenant_id` (no cross-tenant leakage)

### Phase 3: Hardening (COMPLETE)

**Deliverable:** Timeout protection, fail-closed gates, audit-first architecture

| Mechanism | Behavior | Compliance |
|-----------|----------|-----------|
| **Timeout Gates** | Each check has `timeout_s` limit; timeout → FAIL | Fail-closed, ADR-0232 |
| **Audit-First** | Emit event BEFORE marking task done | GDPR Art. 30 (immutable record) |
| **Tenant Isolation** | No tenant_id → denied (fail-closed) | GDPR Art. 5, 6 |
| **PII Scrubbing** | Hashed inputs/outputs (never raw content) | GDPR Art. 32 |

**Error Handling:**
```python
# Verification execution (fail-closed)
try:
    result = verifier.execute(input_data)  # ADR-0232 audit-first
except AuditFailedError:
    # Audit chain write failed → task cannot be marked done
    raise HTTPException(500, "Verification audit failed")
except TimeoutError:
    # Check timeout → task incomplete (fail-closed)
    return { "passed": False, "reason": "Check timeout" }
```

---

## Installation & Running Locally

### Prerequisites
```bash
# Ensure CorvinOS is installed
cd /home/shumway/projects/CorvinOS
python -m pip install -e .

# Verify audit chain is reachable
ls -la ~/.corvin/tenants/_default/global/forge/audit.jsonl
```

### Running the Skill (Standalone)
```python
from pathlib import Path
from core.skills.os_skills.definition_of_done_verifier.skill_base_wrapper import (
    DoD_VerifierSkillWrapper,
    DoD_VerifierInput,
)

# Initialize verifier
verifier = DoD_VerifierSkillWrapper(
    tenant_id="_default",
    audit_path=Path.home() / ".corvin" / "tenants" / "_default" / "global" / "forge" / "audit.jsonl",
    cwd=Path("/home/shumway/projects/CorvinOS"),
)

# Run verification
input_data = DoD_VerifierInput(
    task_id="task_123",
    task_type="feature",
    symbol_name="my_new_function",
    commit_range="HEAD~5..HEAD",
    commit_msg="feat: add my_new_function",
)

result = verifier.execute(input_data)
print(f"Score: {result.score:.1%}")
print(f"Passed: {result.passed}")
print(f"Checks: {result.checks}")
```

### Running via Console API
```bash
# Start console (if not running)
systemctl --user start corvin-webui

# Run verification (requires authentication)
curl -X POST http://localhost:8765/api/dod/verify \
  -H "Content-Type: application/json" \
  -H "Cookie: session=<session_id>" \
  -d '{
    "task_id": "task_123",
    "task_type": "feature",
    "symbol_name": "my_new_function",
    "commit_range": "HEAD~5..HEAD"
  }'

# Expected response:
# {
#   "task_id": "task_123",
#   "score": 0.85,
#   "passed": true,
#   "checks": { "reachability": true, "audit": true, ... },
#   "audit_event_id": "abc123...",
#   "timestamp": "2026-09-26T..."
# }
```

---

## API Reference

### POST /api/dod/verify

Run a Definition-of-Done verification on a task.

**Request:**
```json
{
  "task_id": "task_123",
  "task_type": "feature",  // or "bugfix", "refactor", "docs"
  "symbol_name": "DoD_VerifierSkillWrapper",  // optional: check reachability
  "commit_range": "HEAD~5..HEAD",  // git range to check
  "commit_msg": "feat(core): add DoD verifier",  // optional
  "project_path": "/path/to/project",  // optional
  "test_path": "/path/to/tests",  // optional
  "test_output_file": "/path/to/test-output.txt"  // optional
}
```

**Response (Success):**
```json
{
  "task_id": "task_123",
  "score": 0.85,
  "passed": true,
  "checks": {
    "reachability": { "passed": true, "evidence": "..." },
    "audit_trail": { "passed": true, "evidence": "..." },
    "test_evidence": { "passed": true, "evidence": "..." },
    "docs_sync": { "passed": true, "evidence": "..." },
    "reproducibility": { "passed": false, "evidence": "Score divergence: 0.02" }
  },
  "weights": {
    "reachability": 0.25,
    "audit_trail": 0.25,
    "test_evidence": 0.20,
    "docs_sync": 0.15,
    "reproducibility": 0.15
  },
  "reason": "Task complete. DoD score: 85.0%",
  "audit_event_id": "dod_verified_abc123...",
  "timestamp": "2026-09-26T..."
}
```

**Response (Failure - Audit):**
```json
{
  "detail": "Verification audit failed. Task cannot be marked done."
}
// HTTP 500
```

### POST /api/dod/feedback

Submit feedback on a DoD verification (used by learning loop).

**Request:**
```json
{
  "task_id": "task_123",
  "check_name": "test_evidence",
  "feedback": "accurate",  // or "inaccurate", "not_applicable"
  "note": "Tests passed but coverage was incomplete"
}
```

**Response:**
```json
{
  "accepted": true,
  "feedback_id": "feedback_task_123_1695753296",
  "weight_adjustment": -0.02  // Predicted adjustment (actual tuning runs daily)
}
```

### GET /api/dod/history/{task_id}

Fetch verification history for a task.

**Request:**
```
GET /api/dod/history/task_123?limit=10
```

**Response:**
```json
{
  "task_id": "task_123",
  "history": [
    {
      "timestamp": "2026-09-26T...",
      "score": 0.85,
      "passed": true,
      "audit_event_id": "..."
    },
    {
      "timestamp": "2026-09-25T...",
      "score": 0.62,
      "passed": false,
      "audit_event_id": "..."
    }
  ],
  "trend": "improving"  // or "stable", "declining"
}
```

### WS /api/dod/stream

Real-time WebSocket stream of DoD verification events (for dashboard).

**Connect:**
```javascript
const ws = new WebSocket("ws://localhost:8765/api/dod/stream");

ws.onmessage = (event) => {
  const { event_type, task_id, score, passed, timestamp } = JSON.parse(event.data);
  // event_type: "verification_started", "check_completed", "verification_done"
  console.log(`Task ${task_id}: score=${score}, passed=${passed}`);
};
```

---

## Operator Runbook

### When to Run Verification

| Scenario | Action |
|----------|--------|
| **Before merging to main** | Run verification on the branch; score must be ≥ 0.80 |
| **End of sprint/week** | Run bulk verification on all tasks; identify low-scorers |
| **After failed deployment** | Verify the deployed code; investigate gap |
| **Learning loop tuning** | Run daily; observe weight shifts in trend dashboard |

### Interpreting Score

| Score Range | Interpretation | Action |
|-------------|-----------------|--------|
| **0.90–1.0** | Excellent | Ready to ship |
| **0.80–0.89** | Good | Ship with review |
| **0.70–0.79** | Fair | Additional work needed |
| **< 0.70** | Poor | Block merge; fix gaps |

### Troubleshooting Low Scores

**Reachability: FAIL**
```
Evidence: Symbol 'my_function' not found in call sites
→ Add a real call site (route handler, CLI command, etc.)
  or provide proof of reachability (URL, endpoint, plugin registry)
```

**Audit Trail: FAIL**
```
Evidence: No audit event in chain
→ Verify audit chain is writable: ls -la ~/.corvin/tenants/_default/global/forge/audit.jsonl
→ Check console logs: journalctl --user -u corvin-webui | grep "AUDIT"
→ Restart audit service if needed: systemctl --user restart corvin-audit-chain
```

**Test Evidence: FAIL**
```
Evidence: Test output file missing or contains FAIL
→ Ensure tests are in --test_path and output in --test_output_file
→ Run: pytest <test_path> -v --tb=short > <test_output_file>
→ Verify output contains "passed" not "failed"
```

**Docs Sync: FAIL**
```
Evidence: Code changed but docs didn't
→ List changed files: git diff HEAD~5..HEAD --name-only
→ For each .py change, ensure corresponding .md update (docs/claude-ref/ or README)
→ Or add file to .docsignore if exemption justified
```

**Reproducibility: FAIL**
```
Evidence: Score diverged by X% on re-run
→ Check for non-deterministic sources: random seeds, timestamps, network calls
→ Run twice in succession: scores should match (±0.02 tolerance)
→ Investigate checks with high variance; may indicate timing issues
```

### Escalation Thresholds

| Condition | Escalation |
|-----------|------------|
| **Score < 0.50** | Contact task owner; investigate root cause |
| **Audit fails** | Page on-call operator; audit chain may be corrupted |
| **>5 tasks score < 0.70** | Weekly review: adjust DoD thresholds or check weights |
| **Learning loop not tuning weights** | Check event store: `ls -la ~/.corvin/tenants/_default/global/learning/` |

---

## Architecture Diagrams

### Verification Flow
```
┌─────────────────────────────────────────────────────────┐
│ POST /api/dod/verify                                    │
│ (Task: task_id, symbol_name, commit_range)             │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
        ┌──────────────────────────────┐
        │  DoD_VerifierSkillWrapper    │
        │  (Tenant-isolated)           │
        └──────────────────────────────┘
                       │
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼             ▼             ▼
    Reachability  AuditTrail   TestEvidence  DocSync  Reproducibility
         │             │             │             │             │
         └─────────────┼─────────────┴─────────────┴─────────────┘
                       ▼
        ┌──────────────────────────────┐
        │  Score Computation           │
        │  (Weighted sum of checks)    │
        └──────────────────────────────┘
                       │
         ┌─────────────┘
         │
         ├─→ Emit to ADR-0232 chain (audit-first)
         │
         ├─→ Log to learning event store
         │
         └─→ Return { score, passed, checks }
```

### Learning Loop
```
┌────────────────────────────────────────────────┐
│ Verification Result                            │
│ (score: 0.85, checks: {...})                  │
└────────────────────┬───────────────────────────┘
                     │
                     ▼
           ┌──────────────────────┐
           │ User Feedback        │
           │ POST /api/dod/feedback
           │ (check: accurate)    │
           └──────────┬───────────┘
                      │
                      ▼
         ┌────────────────────────────┐
         │  Learning Event Store      │
         │  (ADR-0314)                │
         │  - FeedbackEvent           │
         │  - VerificationEvent       │
         │  - WeightUpdateEvent       │
         └────────────┬───────────────┘
                      │
      ┌───────────────┴───────────────┐
      │  (Daily: WeightOptimizer)     │
      │  Tuning loop:                  │
      │  feedback → loss_signal →      │
      │  new_weights → registry        │
      └───────────────┬───────────────┘
                      │
                      ▼
         ┌────────────────────────────┐
         │ Updated Weights (registry) │
         │ Next verification uses:    │
         │ reachability: 0.26         │
         │ audit_trail: 0.24          │
         │ ...                        │
         └────────────────────────────┘
```

---

## Testing

### Unit Tests
```bash
# Test individual checks (no filesystem access)
pytest tests/skills/test_dod_reachability.py -v
pytest tests/skills/test_dod_audit_trail.py -v
pytest tests/skills/test_dod_test_evidence.py -v
pytest tests/skills/test_dod_docs_sync.py -v
pytest tests/skills/test_dod_reproducibility.py -v

# Test scoring logic
pytest tests/skills/test_dod_scoring.py -v
```

### E2E Tests
```bash
# Real verification on CorvinOS codebase
pytest tests/e2e/test_dod_verifier_e2e.py -v

# Console API integration
pytest tests/e2e/test_dod_verifier_api_e2e.py -v

# Audit trail wiring
pytest tests/e2e/test_audit_trail_wired_e2e.py -v
```

### Running All DoD Tests
```bash
pytest tests/ -k "dod" -v --tb=short
```

---

## Compliance & Security

### GDPR Compliance (Art. 5, 6, 30, 32)

| Article | Requirement | Implementation |
|---------|-------------|-----------------|
| **Art. 5** | Accountability (every action audited) | All verifications emitted to ADR-0232 chain |
| **Art. 6** | Lawful basis (explicit purpose) | Verification purpose: task completion validation |
| **Art. 30** | Records of processing (immutable) | Audit chain is hash-linked, append-only |
| **Art. 32** | Security (encryption, integrity) | Fail-closed gates, tenant isolation, no PII |

### EU AI Act Compliance (Art. 50)

| Requirement | Implementation |
|-------------|-----------------|
| **Transparency** | Score + checks + reasoning always returned |
| **Attribution** | Audit event includes `line_of_moral_responsibility` |
| **Auditability** | Full verification trail in audit chain (ADR-0232) |

### Data Classification (L34 Flow Guard)

- **Input:** Commit message, symbol names → **PUBLIC** (no secrets allowed)
- **Output:** Score, checks → **PUBLIC** (operator-facing)
- **Audit Events:** Hashed inputs/outputs → **INTERNAL** (audit chain only)

---

## FAQ

**Q: Can I lower the threshold from 0.80?**  
A: Threshold is in `DoD_VerificationResult.DEFAULT_THRESHOLD` (currently 0.80 for wave 3). Lower thresholds reduce safety; discuss with architecture team before changing.

**Q: What if reachability check can't find a call site?**  
A: Fail-closed (symbol marked unreachable). If the function is genuinely used (e.g., indirectly via reflection), document the exception in commit message: `fix: DoD reachability exemption (see ADR-0721 exception clause)`.

**Q: Does the learning loop run automatically?**  
A: Yes, `WeightOptimizer` runs daily (cron: 3 AM UTC). Manual trigger: `corvin dod learn --tenant=_default`.

**Q: Can I skip DoD verification?**  
A: No. DoD is **non-negotiable** for wave 3 and beyond. Use `[skip-dod-check]` flag in commit message ONLY for security hotfixes (requires on-call approval).

**Q: Is audit trail encrypted?**  
A: No, but hash-chained (ADR-0232). Encryption at-rest is handled by L37 (separate infrastructure component).

---

## Related References

- **ADR-0721:** DoD Verifier Skill Architecture (decision rationale, trade-offs)
- **ADR-0722:** DoD Loss Signal & Learning Integration (optimizer design)
- **ADR-0723:** DoD Implementation Plan (phase breakdown, timeline)
- **ADR-0314:** Learning Infrastructure (feedback event schema)
- **ADR-0232/0233:** Audit Chain (hash-linking, boot tripwire)
- **ADR-0532:** OS-Skills Agentic Control Plane (Skills 2.0 architecture)
- **Skill:** `assistant.definition_of_done_verifier` (marketplace entry)

---

**Last Updated:** 2026-09-26 · **Status:** Production-Ready
