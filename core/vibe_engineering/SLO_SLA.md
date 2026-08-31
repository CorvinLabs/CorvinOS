# Vibe Engineering v0.2-rc1 SLO/SLA

## Service Level Objectives (SLOs)

### Availability & Reliability

| Objective | Target | Measurement | Owner |
|-----------|--------|-------------|-------|
| **Checkpoint Success Rate** | 99.9% | successful saves / total save attempts | CheckpointManager |
| **Recovery Success Rate** | 99.5% | successful recoveries / total recovery attempts | RecoveryEngine |
| **Trigger Detection Accuracy** | 100% | triggers correctly identified / total checks | SessionLifecycleManager |
| **Graceful Degradation** | 100% | tasks continue when filesystem fails | CheckpointFallback |

### Performance

| Objective | Target | Measurement | Owner |
|-----------|--------|-------------|-------|
| **Trigger Evaluation Latency** | <5ms | p95 latency of `evaluate_triggers()` | SessionLifecycleManager |
| **Checkpoint Serialization** | <10ms | p95 latency of `serialize()` | CheckpointManager |
| **Context Reduction** | 91% compression | (original_tokens - reduced_tokens) / original_tokens | ContextReducer |
| **Recovery Latency** | <100ms | p95 latency of `recover_from_checkpoint()` | RecoveryEngine |
| **Concurrent Write Latency** | <50ms | p95 latency with 4 parallel writers | CheckpointManager |

### Capacity

| Objective | Target | Measurement | Owner |
|-----------|--------|-------------|-------|
| **Checkpoint File Size** | <500KB/task | avg checkpoint JSON size | CheckpointManager |
| **Memory Fallback Capacity** | 10 checkpoints | max in-memory checkpoint limit | CheckpointFallback |
| **Context Reduction Overhead** | <10% CPU | CPU% during reduce operation | ContextReducer |

---

## Service Level Agreements (SLAs)

**These are commitments we make to users.**

### Tier 1: Critical Path (Canary)

| SLA | Commitment | Remedy |
|-----|-----------|--------|
| **Checkpoint Persistence** | If a task triggers split, checkpoint MUST persist to disk (or memory fallback) | Automatic fallback to memory; no task interruption |
| **Recovery on Resume** | If a task resumes from checkpoint, it MUST recover to the EXACT state it left | Idempotency guarantee; manual recovery available |
| **No Data Loss** | Checkpoint contains all essential state for task continuation | Dropped sections tracked; recovery possible even if incomplete |

### Tier 2: Operational (Staging)

| SLA | Commitment | Remedy |
|-----|----------|--------|
| **Monitoring** | Metrics emitted every checkpoint cycle | Manual query available via CLI |
| **Error Alerting** | Recovery failures alert oncall | Dashboard + Slack channel |
| **Logging** | Structured JSON logs for all operations | Direct API query if parsing needed |

### Tier 3: Best-Effort (Future)

| SLA | Commitment | Remedy |
|-----|-----------|--------|
| **Performance Targets** | Maintain <5ms triggers, <100ms recovery | Optimize after measurement (Phase 3) |
| **Encryption at Rest** | Checkpoints encrypted on disk (Phase 2) | Manual decryption for incident recovery |

---

## Failure Modes & Mitigations

### FM1: Filesystem Full

**Detection:** `persistence_failures` counter increases

**Mitigation:** Automatic fallback to memory; task continues

**Monitoring:** Alert when `mode=degraded` for >5 min

**Recovery:** Operator frees disk space; fallback retries automatically

### FM2: Corrupted Checkpoint

**Detection:** `recovery_failure` when loading JSON

**Mitigation:** Operator can manually delete file; task restarts from prior checkpoint

**Monitoring:** Alert `recovery_failure_rate > 1%`

**Recovery:** Use fallback checkpoint or restart task

### FM3: Concurrent Write Collision

**Detection:** File locking prevents overwrites; writes serialize

**Mitigation:** Exponential backoff + retry; newer write wins

**Monitoring:** No user-visible impact (handled transparently)

### FM4: Memory Exhaustion (In-Memory Fallback)

**Detection:** `memory_checkpoints` counter reaches limit (10)

**Mitigation:** Oldest checkpoints evicted; most recent kept

**Monitoring:** Alert if memory checkpoint count > 5 for extended time

---

## Downtime Budget

With 99.9% uptime SLO:

- **Per Month:** 43 minutes of unplanned downtime acceptable
- **Per Year:** 8.76 hours of unplanned downtime acceptable

**Current Status:** No incidents logged (Phase 1 canary hasn't started yet)

---

## Escalation Path

| Alert | Page Oncall? | Response Time | Action |
|-------|--|---|---------|
| `checkpoint_success_rate < 99.9%` | Yes | 15 min | Investigate filesystem/permissions |
| `recovery_failure_rate > 1%` | Yes | 30 min | Review checkpoint corruption, logs |
| `persistence_unhealthy (degraded)` | No (non-critical) | 1 hour | Check disk space, clear old checkpoints |
| `memory_checkpoints > 8` | No | Monitor | Operator action not urgent |

## Review Cadence

- **Weekly:** Metrics dashboard review (checkpoint success, recovery success)
- **Monthly:** SLO compliance report (are we meeting targets?)
- **Quarterly:** Capacity planning review (checkpoint file sizes, context reduction %)

## Document History

| Date | Status | Version |
|------|--------|---------|
| 2026-08-24 | Proposed | v0.2-rc1 |
| 2026-09-01 (Est.) | Accepted | v0.2-ga |
| 2026-10-01 (Est.) | Phase 2 Update | v0.3-rc1 |
