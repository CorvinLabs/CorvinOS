# ADR-0299 L16 Integration — Audit Durability + Security Hardening

**Status:** Implementation Complete  
**Date:** 2026-08-12  
**Scope:** AuditDurabilityManager ↔ L16 Security Hardening layer

---

## Overview

L16 Security Hardening requires a load-bearing audit chain to enforce GDPR Art. 30/32 (records of processing) and EU AI Act compliance. ADR-0299 provides the durability guarantees that make this chain trustworthy under crash, corruption, and adversarial scenarios.

This document describes the three coordination points between AuditDurabilityManager and L16 security gates.

---

## Coordination Points

### Point 1: Pre-Audit Gate (Actor Authorization)

**When:** Before recording any audit event, verify actor credentials  
**Who:** L16 security layer (actor context validation)  
**What:** AuditDurabilityManager.record() MUST NOT proceed if actor lacks authorization

```python
# Pseudocode (L16 guards record)

from core.auth import get_current_actor_context
from core.audit import AuditDurabilityManager

def secure_record(manager: AuditDurabilityManager, entry: AuditEntry):
    """Record audit entry only if actor authorized."""
    actor_ctx = get_current_actor_context()
    
    # Gate 1: Actor exists and has permission
    if not actor_ctx.is_authorized_for("audit_write"):
        raise AuthorizationError(f"Actor {actor_ctx.actor_id} not authorized to write audit")
    
    # Gate 2: Tenant exists
    if not validate_tenant_id(actor_ctx.tenant_id):
        raise TenantNotFoundError(f"Tenant {actor_ctx.tenant_id} does not exist")
    
    # Gate 3: Record is not under erasure
    if is_under_erasure(entry.resource):
        raise ComplianceError(f"Resource {entry.resource} is under GDPR Art. 17 erasure")
    
    # All gates passed: record with durability guarantees
    manager.record(entry)
```

**GDPR Binding:** Art. 6(1)(a) (lawfulness of processing), Art. 30 (proof of authorization)

---

### Point 2: Audit-of-Audit (Meta-Logging)

**When:** After durability operation succeeds, log the durability action itself  
**Who:** AuditDurabilityManager (logs crashes, recoveries, repairs)  
**What:** Every durability event (fsync, WAL checkpoint, corruption repair, recovery) is itself audit-logged

```python
# AuditDurabilityManager logs durability events

def _log_recovery_to_audit(self, report: CrashRecoveryReport) -> None:
    """Audit the recovery itself (audit-of-audit).
    
    GDPR Art. 30, 32: Document every data integrity issue.
    """
    entry = AuditEntry(
        event_type="compliance.crash_recovery",  # Compliance event
        actor="system",  # System-initiated
        action="recover_from_crash",
        resource=str(self.log_file),  # The audit log file itself
        result="success" if report.recovery_successful else "partial",
        timestamp=report.recovered_at,
        details={
            "tenant_id": self.tenant_id,
            "records_recovered": report.records_recovered,
            "records_discarded": report.records_discarded,
            "truncation_point": report.truncation_point,
            "errors": report.errors,
        },
    )
    self.chain.record(entry)  # Record the recovery event itself
```

**Chain Structure:**

```
audit.jsonl:
  ...entry_N (user action)
  ...entry_N+1 (corruption detected) → compliance.corruption_detected
  ...entry_N+2 (repair attempted) → compliance.corruption_repaired
  ...entry_N+3 (recovery complete) → compliance.crash_recovery
  ...entry_N+4 (user action)
```

**GDPR Binding:** Art. 30 (records of processing), Art. 32 (integrity verification)

---

### Point 3: Post-Audit Verification (Integrity Check)

**When:** After each audit operation or at boot  
**Who:** L16 security layer (or automated verification)  
**What:** Verify audit chain integrity before allowing operations

```python
# L16 boot tripwire verifies audit chain

from core.audit import AuditDurabilityManager, ChainVerificationError

def verify_audit_chain_on_boot():
    """Boot tripwire: fail-closed if audit chain is corrupt.
    
    GDPR Art. 30, 32: Audit must remain integrity-verified.
    """
    manager = AuditDurabilityManager(AUDIT_LOG_PATH)
    
    try:
        is_valid, message = manager.verify_durability()
        if not is_valid:
            logger.critical(f"Audit chain invalid: {message}")
            # Fail-closed: don't boot
            raise BootError("Audit chain verification failed")
        
        # Chain is valid
        logger.info("Audit chain verified on boot")
        
    except ChainVerificationError as e:
        logger.critical(f"Audit chain verification error: {e}")
        raise BootError("Audit chain is corrupted") from e
```

**Fail-Closed Behavior:**

```
Boot Sequence:
  1. Load AuditDurabilityManager
  2. Run _boot_recovery() (automatic)
  3. Call verify_durability()
     → If INVALID: refuse boot (exit 1)
     → If VALID: continue
```

**GDPR Binding:** Art. 30/32 (load-bearing), compliance self-test requirement

---

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│ User Action (e.g., "/delegate spawn")                       │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
        ┌──────────────────────┐
        │ L16 Gate 1: Authz    │
        │ (Actor authorized?)  │
        └──────┬───────────────┘
               │
               ▼
        ┌──────────────────────┐
        │ L16 Gate 2: Tenant   │
        │ (Tenant exists?)     │
        └──────┬───────────────┘
               │
               ▼
        ┌──────────────────────┐
        │ L16 Gate 3: Erasure  │
        │ (Not under delete?)  │
        └──────┬───────────────┘
               │
               ▼
    ┌────────────────────────────────┐
    │ AuditDurabilityManager.record() │ ← POINT 1 (pre-audit gate)
    │ - WAL BEGIN                    │
    │ - Write audit entry + fsync    │
    │ - WAL COMMIT                   │
    │ - Checkpoint (periodic)        │
    └────────┬───────────────────────┘
             │
             ▼
    ┌────────────────────────────────┐
    │ Audit-of-Audit Logging         │ ← POINT 2 (meta-logging)
    │ - Log corruption repairs       │
    │ - Log crash recovery           │
    │ - Log WAL checkpoints          │
    └────────┬───────────────────────┘
             │
             ▼
    ┌────────────────────────────────┐
    │ L16 Verification               │ ← POINT 3 (integrity check)
    │ - verify_durability()          │
    │ - Daily voice-audit verify     │
    │ - Boot tripwire                │
    └────────┬───────────────────────┘
             │
             ▼
        ┌──────────────────┐
        │ Perform Action   │
        │ (spawn started)  │
        └──────────────────┘
```

---

## Compliance Guarantees

| Regulation | Mechanism | Implementation |
|---|---|---|
| GDPR Art. 5 (Integrity + Confidentiality) | Hash-chained audit + fsync | AuditChain + AuditDurabilityManager |
| GDPR Art. 6 (Lawfulness) | Pre-audit authorization gate | L16 actor context validation |
| GDPR Art. 17 (Erasure) | Resource erasure check | L16 erasure status check |
| GDPR Art. 30 (Records) | Audit-of-audit logging | AuditDurabilityManager._log_recovery_to_audit |
| GDPR Art. 32 (Integrity Verification) | Daily voice-audit verify | L16 boot tripwire + scheduled verification |
| EU AI Act Art. 5 (Risk Mitigation) | House-rules gate before audit record | L44 gate feeds into L16 authorization |

---

## Failure Modes & Recovery

### Scenario: Crash during audit write

```
Timeline:
  T0: Actor authorized ✓
  T1: Entry received by AuditDurabilityManager
  T2: WAL BEGIN written + fsync ✓
  T3: Main entry written + fsync ✓
  T4: [CRASH/SIGKILL] ← Kernel kills process
  T5: WAL COMMIT NOT written ✗
  
Recovery (on boot):
  T6: _boot_recovery() detects uncommitted entry in WAL
  T7: Truncates audit log to last checkpoint
  T8: Records compliance.crash_recovery event
  T9: verify_durability() succeeds
  T10: Boot proceeds
```

**GDPR Impact:** Entry was not completed (actor authorization not watered down). Recovery is conservative: deletes in-flight writes, logs the deletion. Art. 30 compliance maintained.

---

### Scenario: Corruption detected

```
Timeline:
  T0: Periodic corruption check runs
  T1: Hash chain break detected at entry #47
  T2: QueueIntegrityMonitor marks entry #47 as corrupted
  T3: Audit event written: compliance.corruption_detected
  T4: Auto-repair attempted (if enabled)
  T5: Audit event written: compliance.corruption_repaired
  
Chain State:
  ...entry #46 (hash OK)
  ...entry #47 (hash BREAK, marked _corrupted)
  ...entry #48 (hash OK again)
  ...compliance.corruption_detected (recovery event)
  ...compliance.corruption_repaired (repair event)
```

**GDPR Impact:** Corruption is documented in the chain itself. No data loss (marked, not deleted). Art. 30 audit trail is self-documenting.

---

## Configuration

### Feature Flags

```yaml
# tenant.corvin.yaml
spec:
  features:
    audit_durability_enabled: true          # CRITICAL, default OFF
    enable_wal: true                        # Recommended
    enable_crash_recovery: true             # Recommended
    enable_durability_metrics: true         # Optional
    enable_corruption_detection: true       # Recommended
    enable_audit_of_audit: false            # Advanced, off by default
```

### Criticality Messaging

```
CONSOLE UI (Settings → Features):
  🔴 CRITICAL (red badge)
  audit_durability_enabled
  "GDPR Art. 30/32 compliance requires this feature.
   Do not disable unless you have a formal exemption."

CLI (on startup):
  WARNING: audit_durability_enabled is OFF.
  GDPR Art. 30/32 compliance requires this feature.
  Set to True in feature flags before production use.
```

---

## Testing Strategy

**Unit Tests (59 total):**
- 28 durability tests (WAL, atomic writes, fsync, metrics)
- 13 crash recovery tests (SIGKILL, truncation, partial entries)
- 18 chain tests (existing, extended with durability assertions)

**Integration Tests (TBD):**
- L16 authorization gate + AuditDurabilityManager
- Pre-audit + audit-of-audit workflow
- Boot tripwire + crash recovery

**E2E Tests (TBD):**
- Operator: enable flag, observe audit chain verified on startup
- Operator: inject corruption, observe repair logged, chain recovers

---

## Migration Path

**Phase 1:** Deploy with flag default OFF  
**Phase 2:** Internal testing with flag ON (2 weeks)  
**Phase 3:** Beta opt-in with prominent warning  
**Phase 4:** Default ON (deadline set, e.g., 2026-09-01)  
**Phase 5:** Flag removal (after mandatory deadline)

---

## Related Documents

- [ADR-0299 Durability](https://github.com/anthropics/corvin-adr/decisions/0299-audit-durability.md)
- [ADR-0298 Queue Corruption Detection](https://github.com/anthropics/corvin-adr/decisions/0298-queue-corruption-detection.md)
- [Compliance Baseline (GDPR/EU AI Act)](../claude-ref/compliance-baseline.md)
- [L16 Security Hardening](../claude-ref/layer-16-security.md)

---

## Operator Notes

**First-time setup:**
1. Check tenant.corvin.yaml has `audit_durability_enabled: true`
2. Run `corvin audit verify` — should report "Chain verified"
3. Monitor logs for any `compliance.crash_recovery` events
4. Set retention policy in `spec.audit_retention`

**Troubleshooting:**
- `voice-audit verify` fails: Check audit log file permissions (should be 0600)
- Boot tripwire fails: Check ~/.config/corvin-voice/audit_anchor.key exists and has correct permissions
- Corruption detected: Manual repair via `corvin audit repair` (marks, doesn't delete)

**Compliance audit:**
- `grep 'compliance\.' ~/.corvin/audit.jsonl` — shows all compliance events
- Expected: no `compliance.corruption_detected` without corresponding `compliance.corruption_repaired`
- Expected: at most one `compliance.crash_recovery` per crash
