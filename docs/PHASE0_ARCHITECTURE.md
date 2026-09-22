# Phase 0 Architecture: Foundation & Bootstrap

**Status:** 🟢 **FOUNDATION COMPLETE** (based on Phase 9 delivery)  
**Date:** 2026-09-22  
**Purpose:** Define the immutable foundation layers required for Phase 1 launch  
**Scope:** Auth, Audit, Learning, Skills, Compliance — NO new features

---

## Executive Summary

Phase 0 is a **foundation lockdown**. Every subsystem that Phase 1 depends on (Intent Router, Control Plane, Audit Chain, Learning Loop, Skills 2.0) must be stable, tested, and monitored.

**Foundation Principle:** Phase 1 features are built ON TOP of Phase 0's immutable layers. If Phase 0 breaks, Phase 1 stops.

---

## Foundation Layers (IMMUTABLE)

### Layer 1: Audit Chain (LOAD-BEARING)

**Location:** `core/compliance/audit/`  
**Current Status:** ✅ PRODUCTION (deployed 2026-09-03, verified daily)  
**SLA:** 99.99% uptime, zero data loss, hash-chain verified

**What it does:**
- Every action → immutable audit event (append-only, hash-chained)
- Events: plugins loaded, skills executed, consent checked, data flow guarded
- Tenant isolation: all reads/writes filtered by `tenant_id`
- Hash-chain verification: boot tripwire (ADR-0232) verifies chain before any subsystem runs

**Why it's IMMUTABLE:**
- GDPR Art. 30, 32 (legal requirement)
- EU AI Act Art. 50 (disclosure + audit trail)
- CorvinOS identity (proof system, not logging)

**Monitoring:**
```bash
# Daily (automated)
corvin audit verify-chain --tenant=_default
# Output: ✅ Chain height 142857, all hashes verified, 0 gaps

# Weekly (operator)
corvin audit report --since=7d --export=pdf
# Output: compliance-report-weekly.pdf
```

**Failure mode:** If audit chain breaks (hash mismatch), boot tripwire refuses start. Manual recovery required (contact ops).

---

### Layer 2: Compliance Gates (FAIL-CLOSED)

**Location:** `core/compliance/gates/`  
**Current Status:** ✅ PRODUCTION (all 6 gates active)  
**SLA:** 100% coverage (no bypass), fail-closed always

**Gates:**
1. **Bot Disclosure (L18)** — First use per user: operator sees "This is Claude AI" card + consent prompt
2. **Consent Gate (L16)** — User opt-out: `/pass` stops processing, audit logs refusal
3. **House Rules (L44)** — Acceptable-use filter: blocks harmful requests, always on, no off switch
4. **Data Flow Guard (L34)** — Classification + blocking: data classified as PII/secret → blocked, audited
5. **Path Gate (L10)** — Filesystem protection: write operations gated, fail-closed
6. **Voice Audit (L23)** — Speech-to-text logging: metadata-only (no transcript), GDPR-compliant

**Why it's IMMUTABLE:**
- Each gate is fail-closed: default is DENY
- No gate has an off switch or bypass
- Each violation is audited + chain-verified

**Monitoring:**
```bash
# Automated checks (hourly)
corvin gates health-check --all
# Output: ✅ All 6 gates active and fail-closed

# On violation
grep "gate_denied\|gate_violated" ~/.corvin/audit.jsonl | jq -c .
# Output: event stream for compliance review
```

**Failure mode:** If any gate fails or goes bypass-open, entire system denies. Manual lockdown required.

---

### Layer 3: Learning Infrastructure (ADR-0314)

**Location:** `core/learning/`  
**Current Status:** ✅ PRODUCTION (event schema + persistence deployed)  
**SLA:** 99.9% uptime, events persisted within 100ms, analytics available within 1h

**What it does:**
- Captures learning signals: feedback, outcomes, preferences, metrics
- Persists to append-only EventStore (date-partitioned JSON)
- Tenant-scoped isolation: all reads/writes filter by `tenant_id`
- Integration point for Skills 2.0 optimization loop

**Event Types:**
- `confidence_feedback` — User rates Skill decision (yes/no/unclear)
- `outcome_feedback` — Task outcome (success/failure)
- `preference_feedback` — User style preference (LLM/deterministic/neither)
- `metric_observed` — Latency, error rate, cost
- `optimizer_config_updated` — Skill params changed after learning

**Why it's IMMUTABLE:**
- Events are frozen dataclasses (no mutation)
- Hash-chained (audit-first: core chain write + EventStore disk record)
- Failure-safe: if core chain doesn't commit, no disk record

**Monitoring:**
```bash
# Learning loop health (hourly)
corvin learning status --tenant=_default
# Output: 
#   Events today: 1,247
#   Optimizer converged: 3/5 skills
#   Feedback latency p99: 42ms

# Optimizer convergence (weekly dashboard)
# Output: confidence scores trending up for routers, context_adapters
```

**Failure mode:** If events stop persisting, learning loop stalls. Historical data preserved; system recovers on restart.

---

### Layer 4: Skills 2.0 (Control Plane)

**Location:** `core/skills/os_skills/`  
**Current Status:** ✅ WIRED (delegation_router shadow mode, context_adapter registered but not called)  
**SLA:** 99.95% uptime, <100ms p99 latency, zero silent failures

**What it does:**
- Autonomous decision-making: route requests, adapt context, guard data flows
- Learns via feedback loop (ADR-0314)
- Versioned, deployable, composable
- Audit-attributed (every decision logged with LoM = line of moral responsibility)

**Skills in Phase 0:**
1. **os.delegation_router** — Route to Haiku/Sonnet/Opus based on task complexity
   - Status: WIRED in shadow mode (bundled engine still stands)
   - Audit event: `skill_executed` (input, output, latency, lom)
   - Learning: feedback on correct routing from Phase 1 operators

2. **os.context_adapter** — Adapt context to user/task patterns
   - Status: REGISTERED but NOT WIRED (no production call site)
   - Audit event: None (not executing)
   - Learning: Disabled pending wiring in Phase 1

**Why it's IMMUTABLE:**
- Skill versioning (semantic versioning, immutable releases)
- Execution is audited (every decision chain-verified)
- No silent learning (all optimizer steps logged)

**Monitoring:**
```bash
# Skill execution health (real-time)
corvin skills status
# Output:
#   os.delegation_router: ✅ WIRED (shadow), 1,247 calls/day, confidence 0.87
#   os.context_adapter:   🟡 REGISTERED (not wired), 0 calls

# Skill convergence (daily)
corvin skills convergence --skill=os.delegation_router
# Output: confidence trending 0.83 → 0.87 (↑4% week-over-week)
```

**Failure mode:** If Skill crashes, exception caught + logged, fallback triggered. Audit trail preserved.

---

### Layer 5: Plugin System (Boot Layers)

**Location:** `core/plugins/`  
**Current Status:** ✅ PRODUCTION (5 boot layers, 6 core plugins active)  
**SLA:** 99.9% uptime, atomic load/unload, zero plugin crash → system crash

**Boot Layers** (immutable load order):
1. **compliance** — Audit chain, consent, house rules (non-disableable)
2. **core** — Learning, Skills, Context (rarely disabled)
3. **bundled** — Video Producer, KG Connector, etc. (default enabled)
4. **installed** — User-installed plugins (opt-in, enabled by default)
5. **community** — Marketplace plugins (beta, explicit enable required)

**Why it's IMMUTABLE:**
- Compliance layer cannot be disabled (no kill-switch)
- Boot order is topologically sorted (dependency resolution)
- Plugin failure is isolated (subprocess boundary, ADR-0241)

**Monitoring:**
```bash
# Plugin health (every 5 min)
corvin plugins status
# Output:
#   compliance:  ✅ LOADED (bootstrap layer, non-disableable)
#   core:        ✅ LOADED (3 subsystems, 0 errors)
#   bundled:     ✅ LOADED (6 plugins, CPU <2%)

# Plugin audit trail (daily)
grep "plugin_loaded\|plugin_error\|plugin_disabled" ~/.corvin/audit.jsonl | jq -c .
```

**Failure mode:** Plugin crashes → subprocess restart + audit event + alert. System continues.

---

### Layer 6: Multi-Tenant Isolation (ADR-0007)

**Location:** `~/.corvin/tenants/` + `core/paths/tenant.py`  
**Current Status:** ✅ PRODUCTION (5-scope model, verified 2026-09-07)  
**SLA:** 100% isolation, zero cross-tenant data leakage

**Isolation Points:**
- **Audit chain:** `tenants/<tid>/global/forge/audit.jsonl` (one per tenant)
- **Learning events:** Filtered by `tenant_id` on every read/write
- **Console routes:** Auth token carries `session.tenant_id`, all queries filter on it
- **Plugin state:** Tenant-scoped configurations + data

**Why it's IMMUTABLE:**
- GDPR Art. 5 (integrity, confidentiality)
- Every query explicitly filters by tenant_id (no implicit default)
- Boot tripwire verifies tenant-scoped chain before subsystems load

**Monitoring:**
```bash
# Tenant isolation verification (daily)
corvin tenant verify-isolation --all-tenants
# Output: ✅ 3 tenants, 0 cross-tenant reads, 0 dangling refs

# Audit trail per tenant (audit log)
corvin audit export --tenant=_default --since=24h | wc -l
```

**Failure mode:** If cross-tenant leak detected, system enters lockdown. Manual audit required.

---

## Bootstrap Sequence (Immutable Order)

Every CorvinOS boot follows this sequence. **If any step fails, boot stops at that step.**

### Stage 1: Pre-Boot Verification (30s, <5MB checks)
```bash
# 1a. Audit chain integrity check (boot tripwire, ADR-0232)
#     - Read latest 10 events from audit.jsonl
#     - Verify hash chain: event[i].prev_hash == event[i-1].hash
#     - Fail-closed: if chain broken, refuse boot with error code 1
# SLA: <100ms

# 1b. Configuration parsing
#     - Load tenant.corvin.yaml (mode 0o600 required)
#     - Validate schema (TenantSpec, extra="allow" with AWP envelope)
#     - Fail-closed: if malformed, refuse boot with error code 2
# SLA: <50ms

# 1c. Secrets verification
#     - Check encryption key exists (~/.corvin/secrets/keyring.json, mode 0o600)
#     - Test decryption (symmetric: AES-256-GCM)
#     - Fail-closed: if key missing/corrupted, refuse boot with error code 3
# SLA: <100ms

Stage 1 Complete: ✅ Chain verified, config loaded, secrets available
Elapsed: 250ms (SLA: <1s)
```

### Stage 2: Foundation Layers (1.5s, core subsystems)
```bash
# 2a. Audit chain writer initialization
#     - Open audit.jsonl (append-only mode)
#     - Verify write permissions (must be 0o600)
#     - Emit boot_started event
# SLA: <100ms

# 2b. Compliance gates initialization (all 6 gates, fail-closed)
#     - Initialize bot disclosure state (per-user consent tracking)
#     - Initialize consent gate (GDPR Art. 6, 7)
#     - Initialize house rules enforcer (always on)
#     - Initialize data flow guard (classifier loaded)
#     - Initialize path gate (FS permission check)
#     - Initialize voice audit logger (metadata-only)
# SLA: <300ms

# 2c. Learning infrastructure initialization
#     - Open EventStore (append-only, date-partitioned)
#     - Verify persistence layer (can write + read events)
#     - Load optimizer state (if exists; start empty otherwise)
# SLA: <200ms

Stage 2 Complete: ✅ Audit chain ready, all gates active, learning online
Elapsed: 600ms (SLA: <2s)
```

### Stage 3: Plugin System (3s, all boot layers)
```bash
# 3a. Bootstrap compliance layer (non-disableable)
#     - Load compliance plugins (audit backend, consent, house rules)
#     - Verify plugins execute without error
#     - Fail-closed: any plugin error → boot refusal
# SLA: <500ms

# 3b. Bootstrap core layer (rarely disabled)
#     - Load core plugins (Skills, Learning, Context, KG)
#     - Initialize SkillSystemIntegration
#     - Verify Skills can execute
# SLA: <500ms

# 3c. Bootstrap bundled layer (default enabled)
#     - Load bundled plugins (Video Producer, KG Connector, etc.)
#     - Initialize plugin registry
#     - Emit plugins_loaded event (audit)
# SLA: <500ms

# 3d. Bootstrap installed + community layers (if enabled)
#     - Load user-installed + marketplace plugins
#     - Emit plugins_loaded event per plugin (audit)
# SLA: <1s

Stage 3 Complete: ✅ All plugins loaded, dependency graph verified
Elapsed: 2.5s (SLA: <5s)
```

### Stage 4: Subsystem Activation (2s, engines + routes)
```bash
# 4a. Gateway initialization
#     - Mount all routes (/v1/console/*, /v1/api/*, /v1/voice/*)
#     - Verify routes are callable (smoke test per endpoint)
#     - Emit gateway_ready event
# SLA: <500ms

# 4b. Console initialization
#     - Mount static assets (/console/)
#     - Verify SPA bundle loads (no 404)
#     - Emit console_ready event
# SLA: <300ms

# 4c. Worker engines initialization (ACS, A2A)
#     - Initialize delegation policy
#     - Initialize worker dispatcher
#     - Verify engines connectable (not reachable yet, just connectable)
# SLA: <500ms

# 4d. Daemons + watchers
#     - Start task completion registry sync (if enabled)
#     - Start console auto-reload watcher (if enabled)
#     - Start learning optimizer loop (background)
#     - Emit daemons_ready event
# SLA: <700ms

Stage 4 Complete: ✅ All routes live, console online, engines ready
Elapsed: 2s (SLA: <5s)
```

### Stage 5: Health Checks & Readiness Probes (1s, external-facing)
```bash
# 5a. Liveness check (is the process responding?)
#     - HTTP HEAD /health/live → 200 OK
#     - Response time <100ms
# SLA: <100ms

# 5b. Readiness check (can I serve traffic?)
#     - HTTP GET /health/ready → 200 OK + JSON status
#     - Checks: audit chain ✅, gates ✅, plugins ✅, routes ✅
# SLA: <200ms

# 5c. Startup check (did boot succeed?)
#     - Verify no ERROR logs in boot sequence
#     - Verify all stages completed (SLA met)
#     - Emit boot_complete event (audit)
# SLA: <100ms

Stage 5 Complete: ✅ Boot successful, system online
Elapsed: 400ms (SLA: <1s)

Total Boot Time: 250ms + 600ms + 2.5s + 2s + 400ms = 5.75s (SLA: <10s)
```

---

## Health Checks & Monitoring

### Real-Time Monitoring (continuous)

**Liveness Probe** (every 5s, external monitoring)
```bash
curl -s http://127.0.0.1:8765/health/live
# Expected: 200 OK (if server is responding)
# Failure: Any non-200 → alert ops
```

**Readiness Probe** (every 30s, load balancer)
```bash
curl -s http://127.0.0.1:8765/health/ready | jq .
# Expected: 
# {
#   "ready": true,
#   "checks": {
#     "audit_chain": "✅",
#     "compliance_gates": "✅",
#     "learning": "✅",
#     "plugins": "✅",
#     "routes": "✅"
#   }
# }
# Failure: Any check not ✅ → remove from load balancer, alert ops
```

### Daily Audits (automated, 03:00 UTC)

**Audit Chain Verification**
```bash
corvin audit verify-chain --tenant=_default
# Output: ✅ Chain height 142857, all hashes verified, 0 gaps
# Failure: ❌ Chain broken at event 12345 (hash mismatch) → email ops, escalate
```

**Compliance Gates Health Check**
```bash
corvin gates health-check --all
# Output: ✅ All 6 gates active and fail-closed
# Failure: 🔴 House rules gate offline → email ops, page on-call
```

**Learning Loop Status**
```bash
corvin learning status --tenant=_default
# Output:
#   Events today: 1,247
#   Optimizer converged: 3/5 skills
#   Feedback latency p99: 42ms
# Failure: No events in 1h → check EventStore, investigate silence
```

**Plugin Health**
```bash
corvin plugins status --verbose
# Output per plugin: status, uptime, restart count, error rate
# Failure: Plugin down >5min → auto-restart, audit event, alert
```

### Weekly Reviews (manual, Friday 14:00 UTC)

1. **Performance Analysis**
   - Latency p99 (should be <500ms)
   - Throughput (requests/sec)
   - Error rate (<0.1%)

2. **Learning Convergence**
   - Which Skills are converging (confidence trending up)?
   - Which are stuck (confidence flat)?
   - Any surprising feedback patterns?

3. **Audit Trail Summary**
   - Total events this week
   - Any denied requests (gates triggered)?
   - Any plugin errors or restarts?

4. **Capacity Planning**
   - Storage growth (audit.jsonl, EventStore)
   - Memory usage (plugins, learning state)
   - CPU usage (optimizer, skill execution)

---

## Disaster Recovery

### Failure Modes & Responses

| Failure | Detection | Automatic Response | Manual Intervention |
|---|---|---|---|
| **Audit chain broken** | Boot tripwire (pre-boot) | Refuse boot (code 1) | Restore from backup or rebuild chain |
| **Compliance gate fails** | Readiness probe | Remove from LB | Investigate gate, restart if needed |
| **Learning EventStore corrupted** | Daily audit | Preserve historical, start empty | Investigate corruption, restore if backup exists |
| **Plugin crash** | Watchdog (every 10s) | Restart plugin (auto) | If restart loop, manually disable + investigate |
| **Tenant cross-leak** | Daily isolation check | Enter lockdown | Manual audit, restore from backup, rotate keys |
| **Secrets compromised** | Manual security review | Rotate immediately | Notify all tenants, revoke old tokens |

### Backup & Restore

**What to backup** (immutable only):
- `~/.corvin/audit.jsonl` (append-only, hash-chained)
- `~/.corvin/tenants/*/global/forge/audit.jsonl` (per-tenant audit trails)
- `core/learning/event_store/` (date-partitioned events, immutable)
- Encryption keyring (separate vault, high security)

**What NOT to backup** (ephemeral):
- Learning optimizer state (can rebuild from events)
- Plugin caches (can rebuild on startup)
- Temporary files (safe to lose)

**Backup Schedule:**
- Hourly (full): All foundation files → S3/GCS (daily retention)
- Daily (snapshot): Audit chain snapshot → Vault (30-day retention)
- Weekly (test): Restore from backup to staging, verify integrity

**Restore Procedure:**
1. Stop all subsystems (graceful shutdown)
2. Restore backup files to `~/.corvin/`
3. Verify audit chain integrity (boot tripwire)
4. Boot system normally (health checks)
5. Run compliance gates verification (all 6 must pass)

---

## Scaling Strategy (Phase 1+)

### Horizontal Scaling (stateless components)

**Can scale independently:**
- Gateway routes (`/v1/console/*`, `/v1/api/*`) — stateless, LB-friendly
- Skill execution (`os.delegation_router`, etc.) — stateless if no local state
- Plugin routes (bundled/installed plugins) — if plugin is stateless

**Strategy:** Behind a load balancer, multiple instances behind single audit chain (master-only writes).

### Vertical Scaling (stateful components)

**Cannot scale (single node):**
- Audit chain writer (must be serial, single master)
- Learning EventStore (single writer, multiple readers)
- Plugin registry (single source of truth)
- Secrets keyring (single encryption master)

**Strategy:** Increase machine resources (CPU, RAM, disk). Plan capacity per audit event rate.

### Capacity Planning

**Audit Event Rate** (Phase 1 baseline):
- Routing decisions: ~1,000/day per operator
- Plugin events: ~100/day
- Compliance events: ~50/day
- Learning feedback: ~500/day
Total: ~1,650 events/day per tenant

**Audit Chain Growth:**
- Event size: ~500 bytes (average)
- Daily growth: 1,650 * 500 = 825 KB/day
- Monthly growth: ~24 MB/month
- Yearly growth: ~300 MB/year
- Retention: 5 years (5 GB per tenant)

**Learning EventStore Growth:**
- Event size: ~300 bytes (average)
- Daily growth: ~500 events * 300 = 150 KB/day
- Monthly growth: ~4.5 MB/month
- Yearly growth: ~55 MB/year

**Total Phase 1 Storage (single tenant, 1 year):**
- Audit: ~300 MB
- Learning: ~55 MB
- Total: ~355 MB (very small)

**CPU/Memory** (Phase 1 baseline, single operator):
- CPU: <5% (audit writing, gate checking, skill execution)
- RAM: ~500 MB (plugins, learning state, caches)
- Disk I/O: <10 MB/s (audit writes, event store)

**Machine Spec** (minimum for Phase 1):
- CPU: 4 cores (comfortable headroom)
- RAM: 2 GB (headroom for plugins)
- Disk: 50 GB (5-year retention)
- Network: 1 Gbps (overkill for this throughput)

---

## Phase 0 Readiness Checklist

Before Phase 1 launch, verify:

- [ ] Audit chain verified + backup tested
- [ ] All 6 compliance gates active + fail-closed
- [ ] Learning infrastructure persisting events + optimizer running
- [ ] Skills 2.0 wired (delegation_router shadow mode, audited)
- [ ] Plugin system booting all 5 layers without error
- [ ] Multi-tenant isolation verified (zero cross-tenant leaks)
- [ ] Health checks passing (liveness + readiness)
- [ ] Monitoring in place (daily audits, weekly reviews)
- [ ] Disaster recovery plan tested (backup + restore working)
- [ ] Scaling strategy documented (horizontal/vertical capacity)
- [ ] Team trained on runbook + escalation procedures
- [ ] Phase 1 dependencies documented (ADRs + implementation plan)

---

**Next:** See PHASE0_PRODUCTION_RUNBOOK.md (daily operations) and PHASE1_ROADMAP.md (Phase 1 features).
