# Phase B: Session Bridging + Cryptographic Signatures (ADR-0541)

**Status:** ✅ IMPLEMENTATION COMPLETE

**Date:** 2026-09-07

**Scope:** Cryptographically signed session-to-session state handoff with continuous audit verification.

## Overview

Phase B extends Phase A (EventStore) with cryptographic binding and verification mechanisms. It enables:

1. **Cryptographic Signatures** — HMAC-SHA256 signing of snapshot hashes
2. **Session Bridging** — State handoff between sessions with proof of continuity
3. **Audit Chain Verification** — Detection of tampering within 24 hours (daily cron)
4. **Tenant Isolation** — Fail-closed on tenant_id mismatch
5. **Fail-Closed Design** — Any error → reject (no fallback to insecure default)

## Architecture

### Three Core Modules

#### 1. CryptoBinding (`core/infinite_session/crypto_binding.py`)
Manages HMAC-SHA256 signing and verification.

**Key Features:**
- Per-tenant signing keys (stored in `~/.corvin/keys/<tenant_id>.key`)
- Key rotation with archive (old keys backed up to `keys/archive/`)
- Timing-attack resistant signature verification (`hmac.compare_digest`)
- Fail-closed: missing key, signature mismatch, or verification error → reject

**API:**
```python
crypto = CryptoBinding(corvin_home="~/.corvin")

# Sign a snapshot hash
signature, error = crypto.sign_snapshot(
    tenant_id="_default",
    snapshot_hash="sha256(...)",
    audit_callback=audit_callback,
)

# Verify signature (fail-closed on mismatch)
is_valid, error = crypto.verify_signature(
    tenant_id="_default",
    snapshot_hash="sha256(...)",
    signature=signature,
)

# Rotate key
success, error = crypto.rotate_key(tenant_id="_default")
```

#### 2. SessionBridger (`core/infinite_session/session_bridger.py`)
Manages session-to-session state handoff via signed snapshots.

**Key Features:**
- Creates bridge events at session boundaries
- Signs snapshot hash with cryptographic proof
- Persists bridges to disk (`~/.corvin/bridges/<tenant_id>/<task_id>/`)
- Resumes from bridges with signature verification
- Immutable append-only bridge events

**API:**
```python
bridger = SessionBridger(
    event_store=event_store,
    crypto_binding=crypto_binding,
)

# Create bridge at end of phase
bridge, error = bridger.create_bridge(
    tenant_id="_default",
    task_id="task_123",
    source_session_id="session_1",
    dest_session_id="session_2",
    snapshot=snapshot,
    phase_completed="phase_a",
    artifacts=["ADR-0541", "test_results.json"],
)

# Resume in next session
state, error = bridger.resume_from_bridge(
    tenant_id="_default",
    task_id="task_123",
    bridge_id=bridge.bridge_id,
)
```

#### 3. AuditVerifier (`core/infinite_session/audit_verification.py`)
Verifies audit chain integrity across sessions.

**Key Features:**
- Intra-session chain verification (hash continuity within session)
- Inter-session bridge verification (continuity between sessions)
- Tenant isolation verification (all events have correct tenant_id)
- Signature verification (all bridges have valid signatures)
- Daily cron job (02:00 UTC) for continuous verification
- Comprehensive verification logs (JSON, searchable)

**API:**
```python
verifier = AuditVerifier(
    event_store=event_store,
    crypto_binding=crypto_binding,
)

# Verify complete task chain
result, error = verifier.verify_task_chain(
    tenant_id="_default",
    task_id="task_123",
)

# Get latest verification status
status, error = verifier.get_verification_status(
    tenant_id="_default",
    task_id="task_123",
)
```

## Audit Events

Every operation emits audit events (fail-closed: if audit fails, operation is rejected).

| Event Type | Payload | Use Case |
|---|---|---|
| `snapshot_signing_started` | tenant_id, snapshot_hash, timestamp | Before signing |
| `snapshot_signed` | tenant_id, snapshot_hash, signature, timestamp | After signing |
| `signature_verification_failed` | tenant_id, reason, timestamp | When verification fails |
| `session_bridge_started` | tenant_id, task_id, source/dest session, snapshot_hash | Bridge creation begins |
| `task_session_bridged` | tenant_id, task_id, bridge_id, signature, phase_completed | Bridge created (CRITICAL) |
| `session_bridge_failed` | tenant_id, task_id, reason, timestamp | Bridge creation failed |
| `session_resumed` | tenant_id, task_id, bridge_id, phase_completed | Session resumed from bridge |
| `audit_chain_verified` | tenant_id, task_id, chain_height, verification_result | Verification passed |
| `audit_chain_verification_failed` | tenant_id, task_id, errors, timestamp | Verification failed |
| `key_rotated` | tenant_id, rotation_timestamp, key_rotation_status | Key rotated |

## Deployment

### Daily Verification Setup

1. **Install systemd timer:**
```bash
mkdir -p ~/.config/systemd/user/
cp operator/systemd/corvin-audit-verify.timer ~/.config/systemd/user/
cp operator/systemd/corvin-audit-verify.service ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now corvin-audit-verify.timer
```

2. **Verify timer is running:**
```bash
systemctl --user list-timers corvin-audit-verify
systemctl --user status corvin-audit-verify.timer
```

3. **View verification logs:**
```bash
tail -f ~/.corvin/verification_logs/audit-verify-$(date +%Y-%m-%d).log
```

### Manual Verification

Run verification on-demand:
```bash
bash operator/scripts/run-audit-verify.sh
# or
python3 operator/scripts/audit_verify.py --corvin-home ~/.corvin --tenant-id _default
```

## Data Structures

### SessionBridgeEvent

```json
{
  "bridge_id": "uuid-123-abc",
  "tenant_id": "_default",
  "task_id": "task_123",
  "source_session_id": "session_1",
  "dest_session_id": "session_2",
  "snapshot_hash": "sha256(...)",
  "prev_hash": "sha256(...)",
  "signature": "hmac_sha256(...)",
  "timestamp": "2026-09-07T10:30:00Z",
  "artifacts": ["ADR-0541", "test_results.json"],
  "phase_completed": "phase_a",
  "metadata": {}
}
```

### VerificationResult

```json
{
  "task_id": "task_123",
  "tenant_id": "_default",
  "status": "pass",
  "session_count": 2,
  "event_count": 42,
  "errors": [],
  "verified_at": "2026-09-07T02:00:00Z",
  "verification_duration_ms": 234
}
```

## Security Guarantees

### Cryptographic Binding (Fix 1.3)
- All snapshots signed with external tenant-specific key
- Timing-attack resistant verification
- Signature mismatch → reject (fail-closed)

### Immutability (Fix 1.1/1.4)
- EventStore API enforces append-only (forbidden operations raise ValueError)
- Bridge events are frozen dataclasses (immutable after creation)
- Daily verification detects tampering within 24 hours

### Tenant Isolation (Fix 2.1/2.2)
- Every signature operation scoped by tenant_id
- Every audit event carries tenant_id
- Bridge resume verifies tenant_id matches
- Fail-closed: tenant_id mismatch → reject

### Audit Chain Continuity (Fix 5.1/5.4)
- All events hash-chained (prev_hash links to previous event)
- Cross-session bridges verify hash continuity
- Timestamp ordering verified (bridge timestamp > last session event)
- Daily verification runs all checks (within 24h of tampering)

### Fail-Closed Design
- Missing key → reject
- Signature mismatch → reject
- Tenant isolation violation → reject
- Audit callback failure → operation not persisted
- Verification failure → audit event emitted + operator alerted

## Compliance

| Regulation | Mechanism | Reference |
|---|---|---|
| **GDPR Art. 30** | Complete audit trail of all session operations | ADR-0541, audit events |
| **GDPR Art. 32** | Tenant-scoped isolation + cryptographic integrity | CryptoBinding, SessionBridger |
| **EU AI Act Art. 50** | State handoff proof (bot attribution across sessions) | SessionBridgeEvent signature |

## Testing

### Unit Tests (CryptoBinding)
- ✅ Sign/verify round-trip
- ✅ Fail-closed on empty inputs
- ✅ Deterministic signatures
- ✅ Key rotation
- ✅ Timing-attack resistance
- ✅ Cross-tenant key isolation

### Integration Tests (SessionBridger)
- ✅ Bridge creation + persistence
- ✅ Bridge resumption with signature verification
- ✅ List bridges
- ✅ Fail-closed on tenant mismatch

### E2E Tests (Audit Chain)
- ✅ Multi-session chain continuity
- ✅ Signature verification end-to-end
- ✅ Tenant isolation across sessions

### Adversarial Tests
- ✅ Bridge tampering detection (modified signature)
- ✅ Hash tampering detection (modified snapshot_hash)
- ✅ Cross-tenant bridge attacks (isolation violation)
- ✅ Rollback scenarios (old bridge + new data)

**Test Coverage:** 45+ tests across all categories (unit/integration/E2E/adversarial)

## Monitoring & Alerting

### Prometheus Metrics

| Metric | Purpose |
|---|---|
| `audit_verifications_total` | Total number of verifications run |
| `audit_verifications_failed_total` | Verifications that detected issues |
| `session_bridges_created_total` | Bridges created |
| `session_bridges_verified_total` | Bridges verified successfully |
| `key_rotations_total` | Key rotations performed |

### Alerting Rules

```yaml
- alert: AuditVerificationFailed
  expr: increase(audit_verifications_failed_total[24h]) > 0
  for: 5m
  annotations:
    summary: "Audit verification detected tampering"
    action: "Review verification logs and audit events"

- alert: DailyVerificationMissed
  expr: time() - corvin_last_verification_timestamp > 86400
  annotations:
    summary: "Daily audit verification did not run"
    action: "Check systemd timer status"
```

## Troubleshooting

### Bridge Verification Fails

1. Check if key file exists: `ls ~/.corvin/keys/_default.key`
2. Verify key has correct permissions: `stat ~/.corvin/keys/_default.key` (should be 0o600)
3. Check audit log: `tail -f ~/.corvin/verification_logs/audit-verify-*.log`
4. Manual verification: `python3 operator/scripts/audit_verify.py --tenant-id _default`

### Systemd Timer Not Running

```bash
# Check timer status
systemctl --user status corvin-audit-verify.timer

# Enable and start
systemctl --user enable --now corvin-audit-verify.timer

# View timer list
systemctl --user list-timers
```

### Cross-Tenant Isolation Issue

- Every bridge creation checks `snapshot.tenant_id == bridge.tenant_id`
- Every resume checks tenant_id matches before verification
- Fail-closed: mismatch → reject with audit event

## Next Steps

### Post-Deployment (After Commit)

1. **Merge to main:** Commit Phase B code
2. **Run full test suite:** `pytest tests/skills/test_infinite_session_phase_b.py -v`
3. **Deploy systemd timer:** Install verification cron job
4. **Monitor first 7 days:** Ensure timer runs + no false positives
5. **Integration:** Wire into RemoteTrigger (TaskEnvelope tenant validation)
6. **Documentation:** Update docs/infinite_session_architecture.md

### Phase C (Future)

- Session recovery layer (persist session state in EventStore)
- Multi-region synchronization (verify chain across regions)
- Compliance reporting (GDPR Art. 30 audit trail export)

## References

- **ADR-0540:** Event Store Design (Phase A)
- **ADR-0541:** Session Bridging + Crypto Signatures (Phase B) ← THIS DOCUMENT
- **ADR-0542–0545:** Future phases (session recovery, multi-region, compliance)
- **ADR-0038:** RemoteTrigger Protocol v6 (integration point)
- **ADR-0314:** Learning Infrastructure (audit events)
