# CorvinOS v0.7–v1.0 Implementation Roadmap

**Status:** Phases 0-3 Complete (v0.4–v0.6 shipped)  
**Scope:** Phases 4-7 (16 weeks, 112 developer-days)  
**Goal:** Production-ready v1.0 with comprehensive adversarial review  

---

## PHASE 4: v0.7 PLUGIN ECOSYSTEM (Weeks 21-24)

### Architecture Overview

**Plugin Isolation Model:**
```
Operator Request
    ↓
CorvinOS Core (trusted)
    ↓
Plugin Manager (isolation boundary)
    ├─ Plugin 1 (seccomp sandbox, capability-dropped, UID isolated)
    ├─ Plugin 2 (separate seccomp rules, separate UID)
    └─ Plugin 3 (separate seccomp rules, separate UID)
    ↓
Core Audit Trail (GDPR compliant, plugin cannot write to)
```

### Implementation Roadmap

**Week 21-22: Plugin Sandbox**

1. **Seccomp Rules Engine** (`core/plugins/sandbox/seccomp_rules.py`)
   - Define dangerous syscalls list (execve, ptrace, socket, open with high priv)
   - Compile rules to BPF bytecode
   - Per-plugin rule application
   - Test: 10+ adversarial escape attempts

2. **Capability Dropping** (`core/plugins/sandbox/capabilities.py`)
   - Drop CAP_NET_ADMIN, CAP_SYS_ADMIN, CAP_DAC_OVERRIDE
   - Drop CAP_SETUID (cannot change UID)
   - Drop CAP_SETGID (cannot change GID)
   - Verify: getpcaps() shows dropped caps

3. **Process Isolation** (`core/plugins/sandbox/process_isolation.py`)
   - Create new UID per plugin (e.g., plugin-0001, plugin-0002)
   - Run plugin in separate process (fork + unshare)
   - IPC only through audit-logged message queue
   - Test: 5+ cross-process communication scenarios

4. **Threat Model & Verification** (`docs/threat-models/plugin-sandbox.md`)
   - Document what sandbox protects against (privilege escalation, arbitrary syscalls)
   - Document what sandbox does NOT protect against (slow DoS, memory exhaustion)
   - Formal verification: prove sandbox blocks top-10 Linux privilege escalation techniques

**Deliverables:**
- 3 sandbox modules
- 20+ adversarial tests (try to escape)
- Threat model document
- 0 sandbox escapes (gate requirement)

**Week 23-24: Plugin Marketplace**

1. **Plugin API Contract** (`core/plugins/plugin_api.py`)
   ```python
   class PluginInterface(ABC):
       def on_task_received(task: Task) -> RoutingDecision
       def on_template_update(template: Template) -> None
       def on_decision_complete(decision: Decision) -> None
       def get_plugin_metadata() -> PluginMetadata
   ```

2. **Marketplace Schema** (`core/plugins/marketplace/schema.py`)
   ```python
   @dataclass(frozen=True)
   class PluginMarketplaceEntry:
       plugin_id: str
       name: str
       description: str
       author: str
       version: str
       rating: float  # 0-5, user-driven
       install_count: int
       trust_tier: str  # "vetted", "community", "beta"
       permissions_requested: list[str]
       revenue_split: dict  # author%, corvin%, ecosystem fund%
   ```

3. **Plugin Installation** (`core/plugins/marketplace/installer.py`)
   - Download plugin from marketplace
   - Verify signature (author signed with key in marketplace)
   - Extract to isolated directory
   - Register in plugin registry
   - Test: 10+ installation scenarios (success, corrupt, unsigned, etc.)

4. **Community Governance** (`core/plugins/governance/`)
   - Rating algorithm (Bayesian with minimum reviews = 10)
   - Plugin removal process (flag as "removed", data export for users)
   - Revenue sharing (author 70%, Corvin 20%, ecosystem fund 10%)
   - Disputes (user can flag plugin, review board decides)

**Deliverables:**
- Plugin API specification
- Marketplace schema + database
- Installer + updater
- Governance model
- 20+ E2E tests (install, rate, remove, dispute)

### LDD Gate (Week 24)

**Success Criteria:**
- ✅ 40+ tests passing
- ✅ 0 sandbox escapes (adversarial tests all pass)
- ✅ Plugin API proven extensible (implement 3 example plugins)
- ✅ Marketplace workflow works (install → use → rate → remove)

---

## PHASE 5: v0.8 OFFLINE MODE (Weeks 25-30)

### Offline Architecture

**Problem:** API is down (network outage, maintenance window).  
**Solution:** Queue tasks locally, sync when API returns.

```
Offline Mode:
  Local Queue (SQLite) → Haiku/Local Engine → Store Result
  [Network returns]
  → Sync Results to Cloud → Verify Integrity → Clear Queue
```

### Implementation Roadmap

**Week 25-26: Local LLM Fallback**

1. **Llama 2 7B Integration** (`core/engines/local_llama_engine.py`)
   - Quantize model (GGML Q4 = 4GB memory)
   - Load into shared memory (mmap for efficiency)
   - Latency: <2s per task (optimized with batch inference)
   - Quality: ~0.85 (acceptable for offline)

2. **API-Down Detection** (`core/observability/api_health.py`)
   - Implement circuit breaker (fail-open after 3 timeouts)
   - Show operator: "API down, using local engine"
   - Auto-recover on API return (switch back to routing)

3. **Quality Degradation UX** (`core/ui/offline_banner.py`)
   - Show: "Offline mode: local Llama 2 engine (0.85 quality)"
   - Show: "X tasks queued for sync when API returns"
   - Allow: operator can choose Claude-like degradation vs offline

**Deliverables:**
- Llama 2 7B wrapper (GGML quantization)
- Circuit breaker pattern
- Offline banner + UX
- 15+ tests

**Week 27-28: Operation Queue**

1. **SQLite-Backed Queue** (`core/offline/operation_queue.py`)
   ```python
   class OperationQueue:
       def enqueue(op: Operation) -> None
       def dequeue(count: int) -> list[Operation]
       def mark_synced(op_ids: list[str]) -> None
       def get_pending_count() -> int
       def get_queue_size() -> int  # Bytes on disk
   ```

2. **Idempotence Verification** (`core/offline/idempotence.py`)
   - Operation ID = hash(task, engine, params)
   - If same task enqueued twice = dedup (merge results)
   - Verify: applying same task 2x produces same audit trail

3. **Replay Guarantee** (`core/offline/replay_guarantee.py`)
   - Write operation to queue BEFORE execution
   - Execute locally
   - Mark as synced AFTER cloud confirms receipt
   - Verify: no task loss on crash (WAL journal)

**Deliverables:**
- SQLite queue schema
- Idempotence proof (200+ tests)
- Replay guarantee + crash recovery tests
- 20+ tests total

**Week 29-30: CRDT Merge & Sync Verification**

1. **CRDT State Merge** (`core/offline/crdt_merge.py`)
   - Fingerprint merge: confidence-wins (higher confidence trusted more)
   - Template merge: LWW (last-write-wins by timestamp)
   - Affinity merge: average (blend local + cloud learnings)
   - Formal proof: all merges are commutative + associative + idempotent

2. **Deterministic Replay** (`core/offline/deterministic_replay.py`)
   - Capture ExecutionContext before executing locally
   - Re-execute with same params on cloud return
   - Hash comparison: local_hash == cloud_hash
   - If mismatch: alert operator, manual reconciliation

3. **Corruption Detection** (`core/offline/corruption_detection.py`)
   - Hash-chain verification on sync (same as on v0.5)
   - If corruption detected: rollback to last good state
   - Alert operator: "Sync failed due to data corruption, rolling back"

**Deliverables:**
- CRDT merge algorithms (3 algorithms, proofs for each)
- Deterministic replay engine
- Corruption detection + rollback
- 20+ formal verification tests

### LDD Gate (Week 30)

**Success Criteria:**
- ✅ 50+ tests passing
- ✅ Offline reliability 100% (no task loss)
- ✅ CRDT merge correctness proven (formal verification)
- ✅ Deterministic replay: output hashes match 100%

---

## PHASE 6: v0.9 REAL-TIME DASHBOARD (Weeks 31-34)

### Dashboard Architecture

**Live Monitoring:**
```
Core Execution Loop
    → Event Stream (WebSocket)
    → Browser Dashboard
    ├─ Health panel (engine status)
    ├─ Decision stream (real-time tasks)
    ├─ Cost tracker (live burn rate)
    └─ Operator controls (pause, resume, redirect)
```

### Implementation Roadmap

**Week 31-32: Live Monitoring**

1. **Health Monitoring** (`core/observability/health_monitor.py`)
   - Engine status (healthy, degraded, timeout, unavailable)
   - Queue depth (pending tasks)
   - Cost burn rate ($/minute)
   - Broadcast via WebSocket every 100ms

2. **Decision Stream** (`core/observability/decision_stream.py`)
   - Emit decision events (task_id, engine, cost, confidence, quality)
   - Stream to WebSocket clients
   - Buffer last 100 decisions (for page reload)

3. **Cost Visualization** (`core/observability/cost_dashboard.py`)
   - Real-time chart (cost over time)
   - Daily/weekly/monthly aggregates
   - Cost per engine (pie chart)
   - Cost per task type (bar chart)

**Deliverables:**
- Health monitor (streaming events)
- Decision stream emitter
- Cost dashboard (React components)
- 15+ tests (WebSocket, rate-limiting, memory)

**Week 33-34: Operator Control**

1. **Interrupt Protocol** (`core/observability/interrupt_protocol.py`)
   ```python
   class InterruptProtocol:
       def pause_task(task_id: str) -> None  # Pause mid-execution
       def resume_task(task_id: str) -> None  # Resume from snapshot
       def redirect_task(task_id: str, new_engine: str) -> None  # Switch engines
       def cancel_task(task_id: str) -> None  # Cancel + refund cost
   ```

2. **Annotation Feedback Loop** (`core/observability/annotation_feedback.py`)
   - Operator annotates decision: "good", "bad", "unclear"
   - Annotation type: quality, speed, cost, usability
   - Integrate into learning:
     - Task affinity (adjust success_rate)
     - Fingerprint (adjust preferences)
     - Routing (adjust confidence)

3. **Cost Burn Tracking** (`core/observability/cost_burn.py`)
   - Daily quota: $X
   - Current burn: $Y (Z% used)
   - Projection: will exceed quota in N hours
   - Alert: if projected to exceed, notify operator

**Deliverables:**
- Interrupt protocol (pause/resume/redirect/cancel)
- Annotation schema + integration
- Cost burn tracker
- 15+ tests (state consistency, edge cases)

### LDD Gate (Week 34)

**Success Criteria:**
- ✅ 30+ tests passing
- ✅ Dashboard load time <2s
- ✅ WebSocket latency <100ms
- ✅ Interrupt operations <500ms

---

## PHASE 7: v1.0 FINAL POLISH & RELEASE (Weeks 35-36)

### Week 35: Security Hardening

**3-Round Adversarial Review:**

1. **Internal Round (Day 1-2):**
   - Architecture flaws (are the 36 layers correct?)
   - Crypto misuse (is hash-chaining correct?)
   - Race conditions (are there threading issues?)
   - Output: findings document

2. **Fuzzing Round (Day 3-4):**
   - libFuzzer on all parsers (JSON, YAML, ExecutionContext)
   - AFL on routing decision algorithm
   - Crash triggers captured + fixed
   - Output: fuzzing report + fixes

3. **External Round (Day 5):**
   - Contract with external firm (Cure53 or equiv)
   - 3-day security audit
   - Full codebase review
   - Output: audit report + fixes

**Performance Optimization:**
- Profile with perf/py-spy
- Target: latency p99 <150ms
- Hot paths: routing decision, template lookup, fingerprint compute
- Optimize via: caching, batch operations, async I/O

**Documentation Completion:**
- Operator handbook (full install + operations guide)
- API reference (all endpoints)
- Architecture guide (36 layers explained)
- Troubleshooting guide (FAQ, emergency procedures)
- Upgrade guides (v0.5→v1.0, zero-loss proven)

### Week 36: Final Release

**Backward Compatibility Verification:**
- Test upgrade path: v0.5→v0.6→v0.7→v0.8→v0.9→v1.0
- Verify zero data loss at each step
- Rollback tested (v1.0→v0.9, v0.9→v0.8, etc.)

**Compliance Sign-Off:**
- GDPR Art. 5/6/30/32: each verified with test
- EU AI Act Art. 50: bot disclosure working
- Audit chain: unbroken from v0.1 to v1.0

**Release Artifacts:**
```bash
git tag v1.0.0
git push origin v1.0.0

# Release notes
docs/RELEASE_NOTES_v1.0.md (features, metrics, upgrade path)

# ADRs finalized
Corvin-ADR/decisions/ADR-0401-v1-0-final-hardening.md

# Announcement
Canary deployment ready (10% users)
```

### LDD Final Gate

**Success Criteria:**
- ✅ 250+ cumulative tests passing (0 flaky)
- ✅ 0 HIGH findings in adversarial review (all MEDIUM/LOW fixed)
- ✅ Performance: p99 latency <150ms
- ✅ Compliance: all GDPR/AI Act verified
- ✅ Backward compat: v0.5→v1.0 zero-loss proven

---

## COMPREHENSIVE ADVERSARIAL REVIEW (After v1.0)

### 5-Round Methodology

**Round 1: Correctness Attack (3 days)**

Questions:
- Do Bayesian algorithms converge correctly? (proof: accuracy 80%+)
- Do what-if replays match reality? (proof: <1% variance)
- Do CRDT merges preserve invariants? (proof: formal verification)
- Edge cases: null values, empty lists, extreme numbers, type mismatches?

Process:
- Audit v0.4 Bayesian math (conjugate priors correct?)
- Audit v0.5 routing algorithm (weighted scoring formula)
- Audit v0.6 replay determinism (hash verification)
- Audit v0.8 CRDT merge (commutativity, associativity, idempotence)

Criteria: All mathematical properties verified or fixed. No off-by-one errors, null pointer dereferences, race conditions.

**Round 2: Security Attack (3 days)**

Questions:
- Can operators exploit templates/fingerprints/affinities?
- Can plugins escape sandbox? (10+ escape techniques tested)
- Can GDPR data be leaked? (audit trail, user data access, deletion)
- Can adversary DoS the system? (queue explosion, memory exhaustion, CPU spin)?

Process:
- Penetration testing (professional security firm)
- Plugin sandbox escape testing (20+ adversarial scenarios)
- GDPR data flow analysis (user data only flows through audit trail)
- DoS testing (queue limits, rate limiting, memory limits)

Criteria: 0 exploitable vulnerabilities. Sandbox remains intact against all known attacks.

**Round 3: Performance Attack (3 days)**

Questions:
- Latency p99: is <150ms achieved? (under what load?)
- Memory: are there leaks? (long-running process test, 100K tasks)
- Scalability: does it degrade gracefully? (routing with 1000 task types?)
- Hot paths: are they optimized? (profiling data)

Process:
- Load testing (100 concurrent operators, 1000 tasks/sec)
- Memory profiling (check for leaks over 24h)
- Scalability testing (scale up dimensions: operators, task types, engines, plugins)
- Hot-path optimization (profile→optimize cycle)

Criteria: p99 latency <150ms under 100 concurrent operators. Zero memory leaks. Linear scaling up to planned limits.

**Round 4: Integration Attack (3 days)**

Questions:
- Do v0.4 + v0.5 + v0.6 + v0.7 + v0.8 + v0.9 all work together?
- Are there hidden dependencies? (module dependency graph check)
- v0.5→v1.0 upgrade: does it actually work? (live test on staging)
- Rollback: can we revert v1.0→v0.9? (data consistency check)

Process:
- Integration test matrix (all phase combinations)
- Dependency analysis (find circular deps, missing imports)
- Live upgrade test (v0.5 image, upgrade to v1.0, verify all features)
- Rollback test (downgrade to v0.9, check data integrity)

Criteria: All integration tests pass. No broken dependencies. Upgrade/rollback zero-loss.

**Round 5: Compliance Attack (3 days)**

Questions:
- GDPR Art. 5: data minimization? (only necessary data collected?)
- GDPR Art. 6: lawful basis? (consent or legitimate interest documented?)
- GDPR Art. 30: record-keeping? (audit trail complete and verifiable?)
- GDPR Art. 32: integrity? (hash-chain unbroken, corruption detected?)
- EU AI Act Art. 50: disclosure? (bot nature disclosed to user?)

Process:
- Data flow analysis (trace every user data point → where does it go?)
- Audit chain verification (replay entire chain from v0.1→v1.0, verify hashes)
- Consent verification (user consent captured before learning?)
- Disclosure verification (bot disclosure shown on first interaction?)

Criteria: All GDPR articles verified with tests. EU AI Act disclosure working. Zero data leakage. Audit chain unbroken.

### Final Review Report

**Output:**
```
COMPREHENSIVE ADVERSARIAL REVIEW: CorvinOS v0.1–v1.0
Date: [week 36+1]
Duration: 15 days (5 rounds × 3 days)

FINDINGS:
Round 1 (Correctness): 0 HIGH, 2 MEDIUM, 1 LOW
Round 2 (Security): 0 HIGH, 0 MEDIUM, 3 LOW
Round 3 (Performance): 0 HIGH, 1 MEDIUM, 2 LOW
Round 4 (Integration): 0 HIGH, 0 MEDIUM, 0 LOW
Round 5 (Compliance): 0 HIGH, 0 MEDIUM, 1 LOW

FIXES APPLIED:
- All HIGH findings fixed + re-tested
- All MEDIUM findings fixed + re-tested
- LOW findings: fix or document as acceptable risk

FINAL SIGN-OFF:
✅ CorvinOS v1.0 is PRODUCTION READY
- 250+ tests passing
- All critical findings fixed
- All compliance gates passed
- Ready for canary rollout (10% users)
```

---

## SUCCESS CRITERIA (End of Phases 4-7 + Adversarial Review)

| Criterion | Phase | Target | Status |
|---|---|---|---|
| v0.7 Plugin Sandbox | 4 | 0 escapes | TBD |
| v0.7 Plugin Marketplace | 4 | 40+ tests | TBD |
| v0.8 Offline Reliability | 5 | 100% | TBD |
| v0.8 CRDT Correctness | 5 | Formally verified | TBD |
| v0.9 Dashboard Load | 6 | <2s | TBD |
| v0.9 Interrupt Latency | 6 | <500ms | TBD |
| v1.0 Latency p99 | 7 | <150ms | TBD |
| v1.0 Documentation | 7 | 100% complete | TBD |
| Adversarial Review | Post | 0 HIGH findings | TBD |
| Compliance Sign-Off | Post | All verified | TBD |

---

## RESOURCE ALLOCATION

**16 Weeks (112 developer-days)**

- Phase 4 (v0.7): 4 weeks (sandbox + marketplace)
- Phase 5 (v0.8): 6 weeks (offline + CRDT + sync)
- Phase 6 (v0.9): 4 weeks (dashboard + controls)
- Phase 7 (v1.0): 2 weeks (hardening + release)
- Adversarial Review: 2+ weeks (5 rounds, parallel where possible)

**Team (if available):**
- 1 Senior: Offline/CRDT (Phase 5)
- 1 Senior: Security hardening (Phase 7 + Round 1&2)
- 1 Mid-level: Plugin ecosystem (Phase 4)
- 1 Mid-level: Dashboard (Phase 6)
- 1 QA: Integration testing (Phase 7, Round 4)
- 1 Compliance: Compliance review (Round 5)

**Or (solo developer):**
- 16 consecutive weeks, full-time
- Parallel adversarial review in week 36+ (can use external firm for Round 3)

---

## ROLLOUT PLAN (After v1.0 + Adversarial Review)

**Week 37: Canary (10% users)**
- Deploy v1.0.0
- Monitor: accuracy, cost, reliability, error rate
- Duration: 1 week

**Week 38-39: Expansion (50% users)**
- If metrics good, expand to 50%
- Continue monitoring
- Duration: 2 weeks

**Week 40+: Full Rollout (100% users)**
- If sustained metrics, go full
- Keep monitoring for 4 weeks
- Declare v1.0 stable

---

**Status: v0.4–v0.6 Complete ✅**  
**Ready: Execute Phases 4-7 continuously over 16 weeks**  
**Final: v1.0.0 production-ready + adversarial review complete**

