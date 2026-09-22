# Flow Guard Skill (ADR-2032)

**Phase 10 Stream 3 · 12-Week Production Delivery**

Flow Guard is an advanced Skill that learns safe data flows and dynamically allows/blocks data paths based on classification and learned outcomes.

## Overview

### Problem

Today's data flow policies are **static and hardcoded**. They cannot adapt to new data types or improve from experience. This means:
- New PII types are a manual operator decision
- Safe flows still require approval if they're not pre-approved
- Data leaks cannot retroactively update policy

### Solution

Flow Guard makes policies **dynamic and learned**:

1. **Classify** incoming data (PII/sensitive/public)
2. **Check** policy (allow/deny/uncertain)
3. **Decide** (auto-allow high-confidence flows, block unknown data, ask for uncertain flows)
4. **Learn** (update policy from outcomes — successes increase allow confidence, leaks increase deny confidence)

### Fail-Closed Design

- Unknown data → UNCERTAIN (requires approval)
- Credentials → DENY (never allow, confidence=1.0)
- Missing consent → UNCERTAIN (requires approval)
- Any error → DENY (fail-closed)

## Architecture

### Modules

| Module | Purpose | LoC |
|---|---|---|
| `data_classifier.py` | Detect PII, credentials, URLs | 200 |
| `flow_policy.py` | State machine: allow/deny/uncertain policies | 300 |
| `flow_guard.py` | Main orchestrator + audit integration | 400 |
| `test_flow_guard_week1.py` | Unit tests (28 tests) | 600+ |

### Data Model

```python
# Data flow classification
ClassificationResult = {
    "data_class": "personal_email",        # From classifier
    "confidence": 0.95,                    # 0.0–1.0
    "reasoning": "Gmail domain detected"
}

# Policy decision
FlowEvaluation = {
    "data_class": "personal_email",
    "destination_engine": "anthropic/claude-opus-5",
    "decision": "allow" | "deny" | "uncertain",
    "policy_confidence": 0.88,
    "reasoning": "...",
    "lom": "flow_guard.FlowGuard.evaluate_flow:L155"  # Audit trail
}

# Learning from outcomes
FlowOutcome = {
    "data_class": "personal_email",
    "destination_engine": "anthropic/claude-opus-5",
    "result": "success" | "pii_leak_detected" | "error",
    "reasoning": "..."
}
```

## Quick Start

### Basic Usage

```python
from core.skills.os_skills.flow_guard import FlowGuard, FlowDecision

# Create guard for tenant
guard = FlowGuard(tenant_id="my_tenant", confidence_threshold=0.7)

# Evaluate a flow
eval = guard.evaluate_flow(
    data="user@example.com",
    destination_engine="anthropic/claude-opus-5",
    user_consent={"personal_email": True}  # User gave consent
)

if eval.decision == FlowDecision.ALLOW:
    print("Flow allowed ✓")
elif eval.decision == FlowDecision.UNCERTAIN:
    print("Flow uncertain — requires operator approval")
else:  # DENY
    print(f"Flow blocked: {eval.block_reason}")

# Record outcome (for learning)
guard.record_outcome(
    data_class="personal_email",
    destination_engine="anthropic/claude-opus-5",
    result="success",  # or "pii_leak_detected" or "error"
)
```

### Data Classification

```python
from core.skills.os_skills.flow_guard import DataClassifier, DataClassification

classifier = DataClassifier()

# Classify single item
result = classifier.classify("AKIA1234567890ABCDEF")
print(result.data_class)  # DataClassification.CREDENTIALS
print(result.confidence)  # 0.95

# Classify multiple items
results = classifier.classify_bulk([
    "user@gmail.com",
    "555-123-4567",
    "https://github.com/repo"
])
```

### Policy Management

```python
from core.skills.os_skills.flow_guard import FlowGuard, PolicyRule, FlowDecision

guard = FlowGuard(tenant_id="my_tenant")

# Add custom policy rule (e.g., operator override)
rule = PolicyRule(
    data_class="business_email",
    destination_engine="anthropic/*",  # Wildcard
    decision=FlowDecision.ALLOW,
    confidence=0.95
)
guard.add_policy_rule(rule)

# Export policy for backup
policy_json = guard.export_policy()

# Import policy
guard.import_policy(policy_json)
```

## Classification Taxonomy

### High-Risk (Always Blocked)
- `CREDENTIALS` — API keys, passwords, private keys
- `ENCRYPTION_KEY` — Master keys, private keys
- `FINANCIAL_ACCOUNT` — Bank account numbers, credit cards
- `HEALTH_RECORD` — Medical data, diagnoses

### Medium-Risk (Require Consent + Audit)
- `PERSONAL_EMAIL` — Personal email addresses
- `PHONE_NUMBER` — Phone numbers
- `SSN` — Social Security Numbers
- `HOME_ADDRESS` — Physical addresses
- `FINANCIAL_DATA` — Transaction history, balances
- `BIOMETRIC_DATA` — Fingerprints, face scans

### Low-Risk (Generally Safe)
- `BUSINESS_EMAIL` — Company email addresses
- `GENERIC_ID` — User IDs, order IDs (non-sensitive)
- `PUBLIC_URL` — Public websites (GitHub, Wikipedia, etc.)
- `METADATA` — Timestamps, counts, categories

### Fallback
- `UNKNOWN` — Ambiguous or unclassified data

## Pattern Detection

Flow Guard uses multiple detectors working in sequence:

| Detector | Patterns | Confidence |
|---|---|---|
| **CredentialsDetector** | AWS key, API token, private key, password | 0.70–0.99 |
| **EmailDetector** | Personal (gmail/yahoo) vs. business | 0.60–0.95 |
| **PhoneNumberDetector** | US format, international | 0.80–0.85 |
| **SSNDetector** | XXX-XX-XXXX format | 0.95 |
| **URLDetector** | Public (GitHub, Wikipedia) vs. internal | 0.30–0.95 |

## Compliance

### Audit Trail (ADR-0232/0233)

Every flow decision is logged to the audit trail with:
- `event_type`: `data_flow_decision`
- `skill_id`: `os.flow_guard`
- `data_class`: The classified data type
- `destination_engine`: Where data flows
- `decision`: ALLOW / DENY / UNCERTAIN
- `confidence`: Policy confidence (0.0–1.0)
- `reasoning`: Human-readable explanation
- `lom`: Line-of-Moral-Responsibility (for attribution)

### Tenant Isolation (GDPR Art. 5, 6, 32)

- Each tenant has its own policy (FlowPolicyManager)
- No cross-tenant data leakage
- All queries filtered by tenant_id

### House-Rules (CLAUDE.md)

- ✅ Credentials always blocked (confidence=1.0, never weakened)
- ✅ PII flows respect consent gates (require explicit approval)
- ✅ Unknown data defaults to UNCERTAIN (fail-closed)

## Testing

### Unit Tests (28 tests)

```bash
cd /home/shumway/projects/CorvinOS
python3 -m pytest core/skills/os_skills/flow_guard/test_flow_guard_week1.py -v
```

**Test Classes:**
- `TestCredentialsDetector` (4 tests)
- `TestEmailDetector` (4 tests)
- `TestPhoneNumberDetector` (4 tests)
- `TestSSNDetector` (2 tests)
- `TestURLDetector` (3 tests)
- `TestDataClassifier` (6 tests)
- `TestFlowPolicy` (10 tests)
- `TestFlowGuard` (7 tests)

### Manual Validation

```python
# Run sanity checks
python3 << 'EOF'
from core.skills.os_skills.flow_guard import DataClassifier, FlowGuard, FlowDecision

# Test credentials blocking
classifier = DataClassifier()
cred = classifier.classify("AKIA1234567890ABCDEF")
assert cred.data_class.value == "credentials"

# Test flow guard
guard = FlowGuard(tenant_id="test")
eval = guard.evaluate_flow("AKIA1234567890ABCDEF", "anthropic/claude-opus-5")
assert eval.decision == FlowDecision.DENY

print("✅ Sanity checks passed")
EOF
```

## Timeline

| Week | Phase | Scope | Status |
|---|---|---|---|
| 1-2 | Data Classification | 900 LoC + 28 tests | ✅ COMPLETE |
| 2-4 | Policy Engine | 450 LoC + 30 tests | 🔄 IN PROGRESS |
| 4-6 | Learning Integration | 350 LoC + 25 E2E | ⏳ NEXT |
| 6-8 | UI + Audit Trail | 300 LoC + 18 UI tests | ⏳ NEXT |
| 8-10 | Hardening + Load Test | 250 LoC + 22 adversarial | ⏳ NEXT |
| 10-12 | Production Integration | 100 LoC | ⏳ NEXT |

## Dependencies

- **ADR-0232/0233** — Audit chain + hash verification
- **ADR-0314** — Learning Infrastructure (outcome sink)
- **ADR-2030** — Workflow Optimizer (parallel Stream 1)
- **ADR-2033** — Feedback Integration Schema (Week 2-4)

## References

- **ADR-2032** — Flow Guard Skill architecture (Corvin-ADR repo)
- **ADR-0532** — Skills 2.0 architecture
- **L34** — Data Flow Guard layer
- **GDPR Art. 5, 6, 32** — Data processing principles

## Next Steps

- [ ] Week 2: Feedback integration (ADR-2033)
- [ ] Week 3: Console routes (/flow/policy, /flow/feedback)
- [ ] Week 4: Learning loop validation (confidence scoring)
- [ ] Week 6: UI dashboard + Grafana metrics
- [ ] Week 10: Gate 4 review (90 tests, security audit)
- [ ] Week 12: Production release

---

**Status:** Week 1-2 COMPLETE ✅  
**Next Update:** Week 2 EOD Friday (2026-09-29)
