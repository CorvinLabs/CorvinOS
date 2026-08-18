# CorvinOS v0.8 Architecture Diagrams

**Release:** Offline Mode v0.8  
**Status:** Design Phase  
**Purpose:** Visual architecture documentation for offline LLM, operation queue, CRDT merge, and sync verification.

---

## 1. Offline Mode Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                      OPERATOR SESSION                              │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Is Online? ──► YES ──► Use Claude API (native mode)          │   │
│  │                                                               │   │
│  │              ──► NO ──┐                                       │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                         │                                           │
│                         ▼                                           │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ OFFLINE MODE ACTIVATED                                      │   │
│  │                                                              │   │
│  │ ┌──────────────────────┐  ┌──────────────────────────────┐  │   │
│  │ │ Operation Queue      │  │ Local State (Cached)         │  │   │
│  │ │ (SQLite journal)     │  │                              │  │   │
│  │ │                      │  │ • Operator fingerprint       │  │   │
│  │ │ • Task 1 → pending  │  │ • Preferences                │  │   │
│  │ │ • Task 2 → pending  │  │ • Template cache             │  │   │
│  │ │ • Task 3 → pending  │  │ • Plugin configs             │  │   │
│  │ │                      │  │ • Task history (7-day)       │  │   │
│  │ │ Hash-chained        │  │                              │  │   │
│  │ │ Deterministic       │  │ Encrypted at rest (AES-256)  │  │   │
│  │ └──────────────────────┘  └──────────────────────────────┘  │   │
│  │                                                              │   │
│  │ ┌──────────────────────────────────────────────────────────┐ │   │
│  │ │ Local Llama 2 7B (4-bit quantized)                      │ │   │
│  │ │ ├─ Download: 4GB (one-time)                             │ │   │
│  │ │ ├─ Cache: ~/.corvin/models/llama-7b-q4                  │ │   │
│  │ │ ├─ Inference: ~2 sec per turn (90% Claude quality)      │ │   │
│  │ │ ├─ GPU optional (fallback to CPU)                       │ │   │
│  │ │ └─ Deterministic: Same input → Same output              │ │   │
│  │ └──────────────────────────────────────────────────────────┘ │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ FEATURE AVAILABILITY MATRIX                                │   │
│  │                                                              │   │
│  │ Feature              Offline  Online  Quality               │   │
│  │ Chat (local LLM)     ✓        ✓       90%                   │   │
│  │ Suggestions (v0.6)   ✓        ✓       Cached                │   │
│  │ Plugins (v0.7)       ✓        ✓       Sandboxed             │   │
│  │ Streaming            ◐        ✓       No streaming          │   │
│  │ Dashboard (v0.9)     ◐        ✓       Cached metrics only   │   │
│  │ Cloud sync           ✗        ✓       Queued               │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Operation Queue (Journaled Persistence)

```
┌──────────────────────────────────────────────────────────────────┐
│              OPERATION QUEUE (SQLite, Journaled)                 │
│                                                                  │
│  Table: operation_queue                                          │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ Columns:                                                    │ │
│  │  - operation_id (UUID, PRIMARY KEY)                         │ │
│  │  - sequence_num (monotonic, ensures order)                  │ │
│  │  - status (pending, executing, completed, failed)          │ │
│  │  - task_input (JSON: user input, context)                   │ │
│  │  - execution_context_snapshot (JSON: frozen state)          │ │
│  │  - output_hash (SHA256 of expected result)                  │ │
│  │  - prev_operation_hash (hash-chain link)                    │ │
│  │  - current_operation_hash (self-hash)                       │ │
│  │  - created_at (DATETIME: offline timestamp)                 │ │
│  │  - executed_at (DATETIME: when synced online, NULL offline) │ │
│  │  - error_message (if failed)                                │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  Invariants:                                                    │
│  ✓ Every operation links to previous (hash-chain)              │ │
│  ✓ Sequence numbers are monotonic (1, 2, 3, ...)              │ │
│  ✓ Operations are immutable once created (no updates)          │ │
│  ✓ Deterministic replay: same input → same output always      │ │
│                                                                  │
│  Example:                                                       │
│  ┌─────┬────────┬────────────┬──────────────────────────────┐  │
│  │ seq │ op_id  │ status     │ hash (prev → current)        │  │
│  ├─────┼────────┼────────────┼──────────────────────────────┤  │
│  │ 1   │ uuid-1 │ completed  │ null → abc123               │  │
│  │ 2   │ uuid-2 │ completed  │ abc123 → def456             │  │
│  │ 3   │ uuid-3 │ pending    │ def456 → ghi789             │  │
│  │ 4   │ uuid-4 │ pending    │ ghi789 → jkl012             │  │
│  └─────┴────────┴────────────┴──────────────────────────────┘  │
│                                                                  │
│  Journal (Write-Ahead Log):                                     │
│  ├─ Before COMMIT: Write full operation record to WAL           │ │
│  ├─ Ensure disk sync (fsync)                                    │ │
│  ├─ THEN: Execute INSERT into operation_queue                  │ │
│  ├─ Zero data loss: Even crash mid-commit, WAL replays it      │ │
│  └─ Recovery: On restart, WAL is replayed (idempotent)         │ │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. State Reconciliation (CRDT Merge) Data Flow

```
                    OFFLINE STATE                    ONLINE STATE
                    (Local Device)                   (Server)
                    
    ┌──────────────────────────────┐   ┌──────────────────────────────┐
    │ Operator Fingerprint:        │   │ Operator Fingerprint:        │
    │  risk: 0.65                  │   │  risk: 0.70                  │
    │  speed: 0.50                 │   │  speed: 0.50                 │
    │  communication: 0.80         │   │  communication: 0.75         │
    │  affinity:                   │   │  affinity:                   │
    │   - Auth: 0.85               │   │   - Auth: 0.80               │
    │   - Data: 0.60               │   │   - Data: 0.65               │
    │  updated_at: T1              │   │  updated_at: T2 (T2 > T1)   │
    └──────────────────┬───────────┘   └─────────────────┬────────────┘
                       │ CONFLICT DETECTED                │
                       │ Both sides edited "risk"         │
                       │ Offline: T1, value 0.65          │
                       │ Online: T2 > T1, value 0.70      │
                       │                                  │
                       └──────────────────┬───────────────┘
                                          │
                                          ▼
                    ┌──────────────────────────────────────────┐
                    │ MERGE RESOLUTION (Last-Write-Wins)       │
                    │                                          │
                    │ Rule 1: "risk" field (scalar)            │
                    │  - Offline: T1 = 2026-08-17 10:00       │
                    │  - Online:  T2 = 2026-08-17 10:05 ✓     │
                    │  - WINNER: Online (T2 > T1)              │
                    │  - Merged: risk = 0.70                   │
                    │                                          │
                    │ Rule 2: "affinity" field (nested dict)   │
                    │  - Both sides define Auth: 0.85 vs 0.80  │
                    │  - Check timestamps: T1 vs T2            │
                    │  - WINNER: Online (T2 > T1)              │
                    │  - Merged: Auth = 0.80                   │
                    │                                          │
                    │ Rule 3: "enabled_plugins" array          │
                    │  - Offline: [auth-plugin, data-plugin]   │
                    │  - Online:  [auth-plugin, security-plugin]│
                    │  - Conflict: Different arrays            │
                    │  - Strategy: Union (both survive)        │
                    │  - Merged: [auth, data, security]        │
                    │                                          │
                    │ Result:                                  │
                    │ ✓ Final state is deterministic           │
                    │ ✓ All edits preserved (union)            │
                    │ ✓ Conflicts auto-resolved                │
                    └──────────────────────────────────────────┘
                                      │
                                      ▼
                    ┌──────────────────────────────────────────┐
                    │ MERGED STATE                             │
                    │  risk: 0.70 (online)                     │
                    │  speed: 0.50 (both equal, no conflict)   │
                    │  communication: 0.75 (online)            │
                    │  affinity:                               │
                    │   - Auth: 0.80 (online)                  │
                    │   - Data: 0.60 (offline, no online edit) │
                    │   - Security: 0.40 (online only)         │
                    │  enabled_plugins: [auth, data, security] │
                    │  merged_at: T2                           │
                    │  merge_hash: sha256(merged_json)         │
                    └──────────────────────────────────────────┘
```

---

## 4. Deterministic Replay Verification

```
┌─────────────────────────────────────────────────────────────────────┐
│           OPERATION REPLAY WITH HASH VERIFICATION                  │
│                                                                     │
│  PHASE 1: SNAPSHOT & EXECUTION (Offline)                           │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │ 1. Operator: "Create auth template"                        │   │
│  │ 2. Capture ExecutionContext snapshot:                      │   │
│  │    {                                                        │   │
│  │      "operator_id": "op-123",                              │   │
│  │      "session_id": "sess-456",                             │   │
│  │      "fingerprint": {...},                                 │   │
│  │      "available_templates": [...],                         │   │
│  │      "user_input": "Create auth template",                 │   │
│  │      "timestamp": 1692374400,  # Frozen (replay uses this) │   │
│  │      "random_seed": 42,        # Deterministic RNG        │   │
│  │      "rng_state": {...}                                    │   │
│  │    }                                                        │   │
│  │ 3. Execute Brain (Llama 2 7B, local, deterministic)       │   │
│  │ 4. Produce output: "Template: Auth\n..."                  │   │
│  │ 5. Compute output_hash = SHA256(output)                   │   │
│  │ 6. Store in operation_queue:                              │   │
│  │    {                                                        │   │
│  │      "operation_id": uuid,                                 │   │
│  │      "execution_context_snapshot": {...},                 │   │
│  │      "output_hash": "a1b2c3d4...",                        │   │
│  │      "prev_operation_hash": "...",                        │   │
│  │      "status": "completed_offline"                        │   │
│  │    }                                                        │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  PHASE 2: SYNC VERIFICATION (Online Reconnect)                     │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │ 1. Read operation from queue:                              │   │
│  │    - Get execution_context_snapshot                        │   │
│  │    - Get output_hash (recorded offline)                    │   │
│  │                                                             │   │
│  │ 2. REPLAY the operation:                                   │   │
│  │    - Execute Brain again with same snapshot               │   │
│  │    - Use same random_seed (deterministic)                 │   │
│  │    - Frozen timestamp (no current time)                    │   │
│  │    - Produce output: "Template: Auth\n..."                │   │
│  │                                                             │   │
│  │ 3. Compute replayed_hash = SHA256(replayed_output)        │   │
│  │                                                             │   │
│  │ 4. VERIFY:                                                │   │
│  │    if replayed_hash == output_hash {                       │   │
│  │      ✓ MATCH: Offline execution is correct                │   │
│  │      ✓ Update operation status = "verified"               │   │
│  │      ✓ No conflict, no user action needed                  │   │
│  │    } else {                                                │   │
│  │      ✗ MISMATCH: Determinism violated!                    │   │
│  │      ✗ Possible causes:                                    │   │
│  │          - Random seed not stored (non-deterministic)     │   │
│  │          - Model version changed (Llama 7B updated)       │   │
│  │          - Floating-point rounding                         │   │
│  │      ✗ Notify operator with both outputs                  │   │
│  │      ✗ Operator chooses: Keep offline or discard          │   │
│  │      ✗ Update operation status = "manual_review"          │   │
│  │    }                                                        │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  PHASE 3: HASH-CHAIN VALIDATION                                    │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │ Verify entire chain integrity:                             │   │
│  │                                                             │   │
│  │ Op 1: hash(prev=NULL, op_id=uuid-1, ...)                  │   │
│  │       = abc123                                              │   │
│  │                                                             │   │
│  │ Op 2: hash(prev=abc123, op_id=uuid-2, ...)                │   │
│  │       = def456  ✓ (if hash matches stored)                │   │
│  │                                                             │   │
│  │ Op 3: hash(prev=def456, op_id=uuid-3, ...)                │   │
│  │       = ghi789  ✓ (if hash matches stored)                │   │
│  │                                                             │   │
│  │ If ANY hash mismatch: Chain is broken, cannot trust        │   │
│  │ Result: Ask operator to retry online                       │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  SUCCESS: All operations verified, synced to server                │
│  FAILURE: Operator notified, manual review required                │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 5. Graceful Degradation Feature Matrix

```
┌──────────────────────────────────────────────────────────────────┐
│              FEATURE AVAILABILITY BY MODE                         │
│                                                                  │
│  Feature                      Offline  Online  Notes            │
│  ──────────────────────────────────────────────────────────────  │
│  Chat (with local LLM)        ✓        ✓       90% quality      │
│  Suggestions (v0.6 cached)    ✓        ✓       Cached affinity  │
│  Plugins (v0.7 sandboxed)     ✓        ✓       Local only       │
│  Task History (7-day)         ✓        ✓       Cached snapshot  │
│  Settings Changes             ✓        ✓       Queued for sync  │
│  Template Editing             ✓        ✓       Merged on sync   │
│                                                                  │
│  Streaming Output             ◐        ✓       No streaming     │
│  Real-time Metrics (v0.9)     ◐        ✓       Cached metrics   │
│  Plugin Analytics             ◐        ✓       Cached only      │
│                                                                  │
│  Cloud Sync                   ✗        ✓       Queued, pending  │
│  Multi-device Sync            ✗        ✓       After reconnect  │
│  Audit Log Upload             ✗        ✓       Batched on sync  │
│                                                                  │
│  Legend:                                                         │
│  ✓ = Fully available                                            │
│  ◐ = Partially available (degraded quality)                     │
│  ✗ = Not available (requires online)                            │
│                                                                  │
│  Determination: ~150ms to evaluate all features                  │
│  Shows operator UI: "Connected / Offline / Syncing"              │
└──────────────────────────────────────────────────────────────────┘
```

---

## 6. Sync Flow (Reconnect & Merge)

```
OFFLINE STATE                          RECONNECT DETECTED
(Operations Queued)                    (Network up)
        │                                     │
        ├─ Op1: completed                     │
        ├─ Op2: completed                     ▼
        ├─ Op3: pending                       
        ├─ Op4: pending            ┌──────────────────────┐
        └─ Op5: pending            │ Initiate Sync        │
                                   │ ┌──────────────────┐ │
                                   │ │ 1. Sign in/auth  │ │
                                   │ │ 2. Get server    │ │
                                   │ │    state         │ │
                                   │ │ 3. Validate      │ │
                                   │ │    hash-chain    │ │
                                   │ │ 4. Merge local + │ │
                                   │ │    server        │ │
                                   │ │ 5. Replay ops    │ │
                                   │ │ 6. Upload audit  │ │
                                   │ │ 7. Confirm sync  │ │
                                   │ └──────────────────┘ │
                                   └──────────────────────┘
                                            │
                                            ▼
                        ┌───────────────────────────────────┐
                        │ SYNC COMPLETE                     │
                        │ ├─ All operations uploaded        │
                        │ ├─ Local state = server state     │
                        │ ├─ Queue cleared                  │
                        │ ├─ Offline fingerprint synced     │
                        │ └─ Audit trail verified           │
                        └───────────────────────────────────┘
                                            │
                                            ▼
                        ┌───────────────────────────────────┐
                        │ USER NOTIFICATION                 │
                        │ "Sync complete: 5 tasks uploaded  │
                        │  Fingerprint updated"             │
                        └───────────────────────────────────┘
```

---

## References

- **ADRs:** 0391–0395 (offline architecture, CRDT, replay)
- **Concepts:** 0027–0029 (offline methodology)
- **Depends on:** v0.6 (fingerprint), v0.7 (plugins)
- **GDPR:** Art. 5 (accuracy), Art. 32 (security), Art. 17 (erasure)

---

**Maintained by:** Claude Code  
**Last Updated:** 2026-08-18  
**Next Review:** v0.8 Week 2 (Operation queue + CRDT merge)
