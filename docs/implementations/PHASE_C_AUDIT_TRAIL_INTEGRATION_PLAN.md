# Phase C: Audit Trail Integration — Plan

**Status:** 🟡 Planning | **Dependency:** ADR-0232, ADR-0233 (Compliance Hardening) | **Target:** K=3

---

## Scope: Audit Trail ↔ Task Tracking DB

### What is Phase C?

Integrate Task Tracking DB with CorvinOS Audit Trail (L16) — mandatory, hardcoded, hash-chained per ADR-0232.

**Input:** Phase B completed (525 tasks in DB)  
**Output:** Tasks linked to audit events, hash-chain verified, GDPR Art. 30 compliant

### Key Requirements (from ADR-0232)

| Requirement | Details | Status |
|-------------|---------|--------|
| **L16 Audit Trail** | Hash-chained, immutable, fsync'd | Must implement |
| **GDPR Art. 30** | Processing records audited | Compliance target |
| **Immutability** | No updates/deletes to audit events | Hard constraint |
| **Hash Chain** | Each event links to previous | Cryptographic binding |
| **Fail-Closed** | Missing audit → platform shutdown | Tripwire enforcement |

### Phase C Deliverables

1. **Audit Event Schema** — Events for task lifecycle
   - `task_created`, `task_updated`, `task_completed`, `task_deleted`
   - Fields: task_id, event_type, timestamp, actor, delta, hash, prev_hash
   
2. **Audit Writer** — Write task events to audit trail
   - Integrate with `core.task_tracking.store.events` table
   - Hash-chain verification (previous event's hash → current event's prev_hash)
   - Fail-closed: if audit commit fails, task operation rolls back

3. **Audit Reader** — Query task audit history
   - `get_task_audit_trail(task_id)` — full event chain for task
   - `verify_chain(start_hash, end_hash)` — verify chain integrity
   - Tenant-scoped: all queries filtered by tenant_id

4. **Compliance Validation** — K=3 Gates
   - Gate 1: All tasks have ≥1 audit event (created event)
   - Gate 2: Hash-chain integrity verified (no gaps, no tampering)
   - Gate 3: Audit trail matches task state (consistency check)

---

## Architecture (ADR-0232 Constraints)

### Audit Trail Path

```
Task Tracking DB (tasks, items table)
    ↓
    Create audit event (task_created, task_updated, etc.)
    ↓
    Hash-chain link (SHA256(prev_event_hash + event_payload))
    ↓
    Write to core audit chain (core.task_tracking.store.events)
    ↓
    fsync() commit (GDPR Art. 32 — durability)
    ↓
    Task operation completes
```

**Fail-Closed Logic:**
```
IF audit_commit FAILS:
  ROLLBACK task_db_transaction
  SHUTDOWN platform (tripwire)
ELSE:
  COMMIT task_db_transaction
```

### Hash-Chain Schema

```python
@dataclass(frozen=True)
class AuditEvent:
    event_id: str                # UUID
    tenant_id: str               # "_default"
    item_id: str                 # task ID
    event_type: str              # task_created | task_updated | ...
    ts: str                       # ISO 8601
    actor: str                    # "migration-phase-b" | user email
    delta: str                    # JSON: {field: old_value → new_value}
    hash: str                     # SHA256(prev_hash + event_payload)
    prev_hash: str               # Previous event's hash (chain link)
```

---

## K=3 Readiness Gates

### Gate 1: Audit Coverage
```sql
SELECT COUNT(*) FROM items WHERE tenant_id='_default'
  AND id NOT IN (SELECT item_id FROM events WHERE event_type='task_created')
```
**Pass Criteria:** 0 items without creation event (all 525 must have audit trail)

### Gate 2: Hash-Chain Integrity
```python
def verify_chain(start_event, end_event):
    current = start_event
    while current.event_id != end_event.event_id:
        next_event = query(prev_hash=current.hash)
        if not next_event or next_event.prev_hash != current.hash:
            return False  # Chain broken
        current = next_event
    return True
```
**Pass Criteria:** No gaps, no tampering detected

### Gate 3: Consistency Check
```sql
SELECT COUNT(*) FROM items i
  WHERE i.status != (
    SELECT delta->>'status' FROM events e
      WHERE e.item_id=i.id
      ORDER BY e.ts DESC LIMIT 1
  )
```
**Pass Criteria:** 0 mismatches (DB state matches latest audit event)

---

## Implementation Steps (K=3)

### Step 1: Audit Event Generation (Phase C-1)
- [ ] Define AuditEvent dataclass (frozen, immutable)
- [ ] Implement `emit_task_audit_event()` function
- [ ] Integrate with task create/update/delete flows
- [ ] Test: emit event for each task operation

### Step 2: Hash-Chain Link (Phase C-2)
- [ ] Implement `hash_event(prev_hash, event)` → SHA256
- [ ] Implement `link_event_to_chain(event, prev_hash)` → new hash
- [ ] Verify: each event's hash depends on previous
- [ ] Test: chain integrity verification

### Step 3: Audit Trail Write (Phase C-3)
- [ ] Insert AuditEvent into `events` table
- [ ] fsync() on commit (GDPR Art. 32)
- [ ] Fail-closed: if write fails, rollback task operation
- [ ] Test: audit write before task commit

### Step 4: K=3 Validation (Phase C-4)
- [ ] Run all 3 K=3 gates
- [ ] Verify: 525/525 tasks have audit trail
- [ ] Verify: hash-chain integrity (0 gaps)
- [ ] Verify: consistency (DB ↔ audit match)

---

## Timeline & Token Budget

| Phase | Duration | Estimated |
|-------|----------|-----------|
| C-1: Event Generation | 1–1.5h | Low token |
| C-2: Hash-Chain Link | 1–1.5h | Medium token |
| C-3: Audit Write | 0.5–1h | Low token |
| C-4: K=3 Validation | 0.5h | Low token |
| **Total** | **3–5h** | **~2M tokens** |

**Current Token Budget:** ~14.9M / 15M (CRITICAL — only 100k left)

---

## Decision: Phase C in Next Session

**Reason:** Token budget exhausted (14.9M / 15M).  
**Action:** Save Phase C plan, resume with fresh session.

**Next Session Starts With:**
```
/loop-driven-engineering k=3
  Phase C (Audit Trail Integration): 
  - Implement AuditEvent + emit_task_audit_event()
  - Implement hash-chain linking
  - Write + fsync to events table
  - Validate K=3 gates (audit coverage, chain integrity, consistency)
```

---

**Phase C Status:** 🟡 PLANNED, AWAITING NEXT SESSION
