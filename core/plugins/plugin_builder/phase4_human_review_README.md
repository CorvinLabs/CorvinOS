# Phase 4: Human Review — Complete Implementation

**Status:** Production-Ready ✅

Comprehensive implementation of Phase 4 (Human Review) for generated test suites, including GitHub workflow integration, tiered approval system, audit trail logging, and rubber-stamp detection.

## Components

### 1. GitHub Workflow (`github_workflow.py`)
Posts generated tests as PR comments and manages GitHub integrations.

**Key Classes:**
- `TestSuite` — Metadata container for generated tests
- `PRComment` — GitHub PR comment with tiered approval buttons
- `GitHubClient` — GitHub API client (post comments, fetch PR info, post reviews)
- `WorkflowOrchestrator` — Orchestrates submission workflow
- `ApprovalTier` enum — Tier 1/2/3 classification
- `ApprovalStatus` enum — Workflow status tracking

**Example Usage:**
```python
from github_workflow import (
    TestSuite, ApprovalTier, GitHubConfig, WorkflowOrchestrator
)
from pathlib import Path

# Create test suite metadata
suite = TestSuite(
    plugin_id="my-plugin",
    test_path=Path("/tests/test_my_plugin.py"),
    test_count=5,
    tier=ApprovalTier.TIER_1,
    coverage_percent=85.5,
    generated_at="2026-08-29T10:00:00Z",
)

# Submit for review
config = GitHubConfig(
    token="ghp_...",
    owner="corvin",
    repo="corvinOS",
)
orchestrator = WorkflowOrchestrator(config)
pr_comment = orchestrator.submit_for_review(
    test_suite=suite,
    pr_number=42,
    reviewer_tier=ApprovalTier.TIER_1,
)
```

### 2. Approval Logic (`approval_logic.py`)
Rules engine implementing tiered approval decisions.

**Key Classes:**
- `ApprovalRulesEngine` — Implements Tier 1/2/3 rules
- `ReviewDecision` — Immutable decision record
- `ReviewerRole` enum — Junior/Senior/TechLead/Maintainer
- `DecisionReason` enum — Reason codes for decisions

**Tier Rules:**
- **Tier 1 (Simple Unit Tests):** Auto-approve if coverage ≥ 80% and no risky patterns
- **Tier 2 (Integration Tests):** Require senior engineer review, coverage ≥ 75%
- **Tier 3 (E2E Tests):** Require 2 approvers (at least 1 tech lead), coverage ≥ 85%, risk assessment

**Example Usage:**
```python
from approval_logic import ApprovalRulesEngine, ReviewerRole

engine = ApprovalRulesEngine()

# Evaluate Tier 1
decision = engine.evaluate_tier_1(
    coverage_percent=85.0,
    test_count=5,
    has_risky_patterns=False,
)
print(f"Decision: {decision.status} — {decision.reason}")

# Evaluate Tier 2 with reviewer
decision = engine.evaluate_tier_2(
    coverage_percent=75.0,
    test_count=7,
    reviewer="alice@corvin.io",
    reviewer_role=ReviewerRole.SENIOR_ENGINEER,
)

# Rubber-stamp detection
decisions = [d1, d2, d3]  # List of decisions
result = engine.check_rubber_stamp(decisions, time_window_seconds=300)
if result["is_rubber_stamp"]:
    print(f"⚠️ Suspicious approval pattern (confidence: {result['confidence']:.2f})")
```

### 3. Audit Trail (`audit_trail.py`)
Hash-chained JSONL audit log for compliance.

**Key Classes:**
- `AuditTrail` — Append-only hash-chained log
- `AuditEvent` — Immutable audit event

**Features:**
- SHA256 hash-chain linking (GDPR Art. 30, 32)
- Tamper detection via `verify_chain()`
- Compliance export with optional date filtering
- Automatic event type routing

**Example Usage:**
```python
from audit_trail import AuditTrail
from pathlib import Path

trail = AuditTrail(Path("./.corvin/audit/phase4.jsonl"))

# Log submission
trail.log_submission(
    plugin_id="my-plugin",
    pr_number=42,
    actor="alice@example.com",
    test_count=5,
    coverage_percent=85.0,
    tier="tier_1",
)

# Log decision
trail.log_decision(
    plugin_id="my-plugin",
    pr_number=42,
    actor="bob@example.com",
    status="approved",
    reason="auto_approved",
)

# Verify integrity
result = trail.verify_chain()
if not result["valid"]:
    print(f"❌ Chain broken at line {result['broken_at']}")

# Export for compliance
events = trail.export_for_compliance(start_date="2026-08-01", end_date="2026-08-31")
```

## Test Coverage

**35 Unit + Integration + E2E Tests:**
- `TestTestSuite` (2 tests) — Metadata serialization
- `TestPRComment` (3 tests) — Comment rendering
- `TestGitHubConfig` (3 tests) — Configuration validation
- `TestGitHubClient` (2 tests) — API client with mocks
- `TestReviewDecision` (3 tests) — Decision records
- `TestApprovalRulesEngine` (14 tests) — Rules evaluation + rubber-stamp detection
- `TestAuditTrail` (5 tests) — Hash-chain, verification, export
- `TestWorkflowOrchestrator` (1 test) — Integration test
- `TestPhase4E2EWithMockGitHub` (2 tests) — E2E workflows

**Run tests:**
```bash
cd /home/shumway/projects/CorvinOS
source .venv/bin/activate
python -m pytest core/plugins/plugin_builder/tests/test_phase4_human_review.py -v
```

**All 35 tests pass ✅**

## Compliance & Security

### GDPR Compliance (ADR-0262 Extended)
- ✅ Hash-chained audit trail (Art. 30, 32)
- ✅ Immutable event records
- ✅ Tamper detection via chain verification
- ✅ Compliance export with date filtering
- ✅ No PII in audit payloads (usernames only, no email in core fields)

### Rubber-Stamp Detection (Sampling Audit)
Detects suspicious approval patterns:
- Multiple approvals within short time window
- Confidence score (0.0–1.0) based on time delta
- Logged as separate audit event

### Security Principles
- Fail-closed approval rules (default: escalate on missing info)
- Immutable decision records
- No env-var kill-flags or bypass switches
- GitHub token stored securely via config parameter

## Tier Requirements Summary

| Aspect | Tier 1 | Tier 2 | Tier 3 |
|--------|--------|--------|--------|
| **Test Type** | Unit | Integration | E2E |
| **Min Coverage** | 80% | 75% | 85% |
| **Auto-Approve** | Yes (if coverage high) | No | No |
| **Min Reviewers** | 0 | 1 | 2 |
| **Min Reviewer Role** | — | Senior Engineer | Tech Lead |
| **Risk Assessment** | Optional | Optional | Required |

## Future Enhancements

### Phase 4.4 — GitHub App Integration
- Interactive approval buttons via GitHub App webhooks
- Real-time comment updates with approval status
- `/approve`, `/request-review`, `/reject` slash commands

### Phase 4.5 — Dashboard Integration
- Web UI for approval queue
- Metrics: approval time, auto-approve rate, escalation rate
- Compliance reporting dashboard

### Phase 4.6 — Advanced Sampling
- Multi-modal rubber-stamp detection
- Bayesian approval time modeling
- Anomaly detection for reviewer patterns

## Files Generated

```
core/plugins/plugin_builder/
  ├── github_workflow.py           (305 lines)
  ├── approval_logic.py            (298 lines)
  ├── audit_trail.py               (331 lines)
  └── tests/
      └── test_phase4_human_review.py  (769 lines, 35 tests)
```

## Integration Points

1. **Plugin Builder** — After test generation (generators/e2e_tests.py)
2. **CI/CD Pipeline** — On PR creation, auto-submit for review
3. **Compliance Reporting** — Export audit trail for GDPR/SOC2 audits
4. **Dashboard** — Surface approval metrics and decisions

## Configuration

Example `.corvin/tenants/_default/phase4.yaml`:
```yaml
# Phase 4 Human Review Configuration
phase4:
  github:
    token: "${GITHUB_TOKEN}"
    owner: "corvin"
    repo: "corvinOS"
  
  approval:
    tier_1:
      auto_approve: true
      min_coverage: 80.0
    tier_2:
      min_reviewer_role: "senior"
      min_coverage: 75.0
    tier_3:
      min_reviewers: 2
      min_reviewer_role: "tech_lead"
      requires_risk_assessment: true
      min_coverage: 85.0
  
  audit_trail:
    path: ".corvin/audit/phase4.jsonl"
    retention_days: 365
  
  rubber_stamp:
    enabled: true
    time_window_seconds: 300
    alert_on_confidence_above: 0.7
```

## References

- ADR-0262: Extended — Test Generation & Human Review
- GDPR Art. 30 (Records of Processing), Art. 32 (Security)
- `docs/claude-ref/quality-discipline.md` — E2E Wiring Proof standard
- `docs/implementation/PLUGIN_SYSTEM_ACTIVATION_PLAN.md` — Deployment phases

---

**Implementation Date:** 2026-08-29  
**Version:** 4.0  
**Status:** ✅ Production-Ready — All 35 tests passing
