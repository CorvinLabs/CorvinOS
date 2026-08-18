# CorvinOS v0.8 Design Index

**Release:** Offline Mode  
**Timeline:** 6 weeks (2026-11-24)  
**Status:** Design Phase

---

## Quick Navigation

| Document | Purpose | Status |
|---|---|---|
| **[V0.8_IDEAS.md](V0.8_IDEAS.md)** | Vision & 5 core ideas | ✓ Complete |
| **[V0.8_IMPLEMENTATION_PLAN.md](V0.8_IMPLEMENTATION_PLAN.md)** | Detailed impl (5 phases, 160+ tests) | ✓ Complete |
| **[ARCHITECTURE_DIAGRAM.md](ARCHITECTURE_DIAGRAM.md)** | Diagrams: operation queue, CRDT, sync | ✓ Complete |
| **[CRDT_ALGORITHM_SPEC.md](CRDT_ALGORITHM_SPEC.md)** | Formal CRDT merge spec (30+ tests) | ✓ Complete |
| **[REPLAY_DETERMINISM_SPEC.md](REPLAY_DETERMINISM_SPEC.md)** | Replay guarantee + hash verify (30+ tests) | ✓ Complete |

---

## Key Features

### 1. Local LLM Fallback (Llama 2 7B)
- 4-bit quantized, ~4GB cache
- ~90% Claude quality
- Deterministic inference
- <2s latency per turn

### 2. Operation Queue (SQLite)
- Journaled persistence
- Hash-chain integrity
- Deterministic replay
- 100% reliable (zero data loss)

### 3. State Reconciliation (CRDT)
- Last-Write-Wins for scalars
- Union merge for arrays
- Conflict detection
- Automatic resolution

### 4. Graceful Degradation
- Feature availability matrix
- Offline / degraded modes
- Cached data fallback

### 5. Sync Verification
- Hash-chain attestation
- Deterministic replay proof
- Operator notification on conflict

---

## Success Criteria

- [ ] Local LLM: 90%+ valid vs Claude
- [ ] Queue: 100% reliable (no data loss)
- [ ] CRDT: 100% correct merges
- [ ] Offline: <150ms p99 latency
- [ ] Sync: <5 min for 1000-op backlog
- [ ] Conflicts: Zero in normal use
- [ ] Tests: 160+ green

---

## ADRs & Concepts

| Item | Status |
|---|---|
| ADR-0391 (Local LLM integration) | ⏳ Pending |
| ADR-0392 (Operation queue) | ⏳ Pending |
| ADR-0393 (CRDT merge spec) | ⏳ Pending |
| ADR-0394 (Replay determinism) | ⏳ Pending |
| ADR-0395 (Sync protocol) | ⏳ Pending |
| CONCEPT-0027 (Offline strategy) | ⏳ Pending |
| CONCEPT-0028 (CRDT methodology) | ⏳ Pending |
| CONCEPT-0029 (Replay verification) | ⏳ Pending |

---

## Dependency Chain

```
v0.7 (Plugin Ecosystem, complete)
  ↓
v0.8 (Offline Mode) ← YOU ARE HERE
  ├─→ ADR-0391-395
  └─→ CONCEPT-0027-29
      ↓
  v0.9 (Dashboard) — shows offline state
  v1.0 (Production Release)
```

---

**Maintained by:** Claude Code  
**Last Updated:** 2026-08-18
