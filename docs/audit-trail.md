# Audit Trail: Complete Proof of Work

Every Skill decision is logged immutably with cryptographic proof. The audit trail is the system's memory and compliance foundation.

![Audit Event Structure](docs/assets/audit-event-structure.svg)

---

## What Gets Audited

| Subsystem | Event Type | Payload |
|---|---|---|
| **Skills** | `SKILL_EXECUTED` | input, output, latency, lom, hash |
| **Feedback** | `SKILL_FEEDBACK_RECEIVED` | feedback_type, signal |
| **Config** | `SKILL_CONFIG_UPDATED` | param_delta, confidence_before/after |
| **Compliance** | `CONSENT_GRANTED`, `CONSENT_CHECKED` | user_id, consent_type |
| **Security** | `HOUSE_RULE_DENIED` | reason, rule_id |
| **Audit Chain** | `AUDIT_CHAIN_VERIFIED` | chain_height, verification_result |

---

## Audit Event Structure

```json
{
  "event_type": "SKILL_EXECUTED",
  "skill_id": "os.delegation_router",
  "skill_version": "1.2",
  "input": {
    "complexity": 10,
    "task_type": "analysis"
  },
  "output": {
    "engine": "claude-opus-5"
  },
  "timestamp": "2026-09-02T12:34:56.789Z",
  "tenant_id": "_default",
  "lom": "os_delegation_router.py:156",
  "lom_hash": "sha256(source_code_identity)",
  "hash": "sha256(this_event)",
  "prev_hash": "sha256(previous_event)"
}
```

**Key Fields:**

- **tenant_id:** Tenant isolation (GDPR Art. 6)
- **timestamp:** Absolute ordering (GDPR Art. 30)
- **hash/prev_hash:** Immutability proof (GDPR Art. 32)
- **lom (Line of Moral Responsibility):** Cryptographically bound to source code
- **lom_hash:** Prevents LoM spoofing (code identity proof)

---

## Hash-Chain Integrity

```
Event 1:
  {input, output, ...}
  hash = sha256(event_1) = "abc123"

Event 2:
  {input, output, prev_hash="abc123", ...}
  hash = sha256(event_2) = "def456"

Event 3:
  {input, output, prev_hash="def456", ...}
  hash = sha256(event_3) = "ghi789"

Integrity Check:
  sha256(event_2_content + "abc123") == "def456" ✓
  sha256(event_3_content + "def456") == "ghi789" ✓
  → Chain is unbroken, no tampering
```

**No event can be altered without breaking all subsequent hashes.**

---

## Tenant Isolation (GDPR Art. 5, 6)

Every audit query is filtered by tenant_id:

```python
# CORRECT: Tenant-scoped query
events = audit_backend.query(
    tenant_id="_default",
    skill_id="os.vibe_engineering",
    limit=10
)

# WRONG: No tenant filtering → REJECTED
events = audit_backend.query(
    skill_id="os.vibe_engineering",
    limit=10
)
# Error: tenant_id required

# WRONG: Cross-tenant read → DENIED
events = audit_backend.query(
    tenant_id="other_tenant",
    skill_id="os.vibe_engineering"
)
# Error: Access denied (tenant mismatch)
```

**Fail-closed:** Null or missing tenant_id → denied immediately.

---

## Compliance Integration

### GDPR Art. 30 (Record of Processing)

✅ Covered: Every decision (Skill execution, feedback, config change) is logged with:
- Who: skill_id + version
- What: input + output
- When: timestamp (UTC)
- Why: lom (line of code)

### GDPR Art. 32 (Confidentiality + Integrity)

✅ Covered: Events are:
- Hash-chained (tamper-proof)
- Tenant-isolated (no leakage)
- Audit-backend append-only (immutable)
- Daily verified (integrity checks run hourly)

### GDPR Art. 5 (Data Minimization)

✅ Covered: Audit events carry only:
- Metadata (skill_id, version, timestamp)
- Technical proof (hash, lom, tenant_id)
- NO personal data (no names, emails, user content)

### EU AI Act Art. 50 (Transparency)

✅ Covered: Every Skill decision is:
- Attributed (skill_id + version in audit)
- Timestamped (when it happened)
- LoM-bound (which code did it)
- Reversible (logs can be inspected for refusal)

---

## Daily Verification

The system verifies the entire audit chain daily:

```bash
corvin audit verify-chain --tenant=_default

Output:
  Chain height: 142,857 events
  Verification: PASSED ✓
  Checked links: 142,856 hash pairs
  Gaps found: 0
  Last event: 2026-09-02 23:59:59 UTC
  Verification took: 3.2 seconds
```

**Automatically detects:**
- Missing events (gaps in chain)
- Tampered hashes (hash mismatch)
- Tenant isolation violations (cross-tenant leakage)

---

## LoM Binding (Line of Moral Responsibility)

Each event includes a cryptographic binding to the exact source code that caused the decision:

```python
# In os_delegation_router.py, line 156:
if input["complexity"] > self.config["threshold"]:
    return {"engine": "claude-opus-5"}

# Audit event includes:
{
  "lom": "os_delegation_router.py:156",
  "lom_hash": "sha256(source_code_at_that_location)",
  ...
}

# Later, to verify:
# 1. Get source code from git repo at that commit
# 2. Extract line 156
# 3. Compute sha256
# 4. Compare with lom_hash
# Result: ✓ Code identity proven (no spoofing)
```

**Prevents:** Someone replacing the source code with a different decision and claiming the LoM.

---

## Operator Debugging

### View All Decisions for a Task

```bash
corvin audit show-task <task_id>

Output:
  Task: analysis_2026-09-02_001
  Events in chain:
    1. SKILL_EXECUTED: os.delegation_router v1.2 (input: complexity=10, output: engine=opus)
    2. SKILL_EXECUTED: os.vibe_engineering v0.3 (input: ..., output: ...)
    3. SKILL_FEEDBACK_RECEIVED: outcome_feedback="yes"
    4. SKILL_CONFIG_UPDATED: threshold 0.70 → 0.68
```

### Trace a Specific Skill

```bash
corvin audit trace skill os.delegation_router --task=<task_id>

Output:
  Skill: os.delegation_router
  Executions in this task: 3
  Event 1: input={...}, output={engine: opus}, latency=42ms, lom_hash=sha256(...)
  Event 2: input={...}, output={engine: opus}, latency=38ms, lom_hash=sha256(...)
  Event 3: input={...}, output={engine: haiku}, latency=15ms, lom_hash=sha256(...)
```

### Export Compliance Report

```bash
corvin audit export \
  --tenant=_default \
  --format=pdf \
  --since=2026-09-01 \
  --until=2026-09-30 \
  --events=skill_executed,consent_granted,house_rule_denied

Output: compliance-report-2026-09.pdf
  - Covers: All decisions, consent checks, denials for Sept 1–30
  - Signatures: RFC 3161 TSA timestamp (proof of when report was created)
  - Redacted: No sensitive data (aggregated metrics only)
```

---

## FAQ

**Q: Is the audit trail encrypted?**  
A: At-rest encryption optional (ADR-0537). Hash-chain is the security guarantee, not encryption.

**Q: How long is the audit trail kept?**  
A: Default: 90 days. Configurable per GDPR Art. 17 (erasure requests). Archived after retention.

**Q: Can I delete an audit event?**  
A: No. Audit is append-only. Deletion breaks hash-chain. Only erasure (GDPR Art. 17) is supported.

**Q: What if I want to prove a Skill decision to a regulator?**  
A: Export compliance report + show hash-chain verification. Regulator can recompute hashes to verify authenticity.

**Q: Can the audit trail itself be tampered with?**  
A: No. Hash-chain breaks immediately. Daily verification detects any tampering. Boot tripwire fails if chain is broken.

---

## Next Steps

- **[Deployment Guide](deployment-guide.md)** — Monitor audit events during rollout
- **[ACP Vision](acp-vision.md)** — How all Skill decisions fit into one proof system
- **[Skills API Reference](skills-api-reference.md)** — Audit query API
