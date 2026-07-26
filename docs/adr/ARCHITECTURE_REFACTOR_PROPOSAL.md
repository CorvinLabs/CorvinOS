# CorvinOS Architectural Refactor: "Compartmentalized Ship" Architecture
## Converting Core Features to Pluggable Modules

**Date:** 2026-07-26  
**Status:** Strategic Analysis (Ready for Team Discussion)  
**Scope:** CorvinOS v0.13+ (12-18 month roadmap)  
**Author:** Claude Code

---

## Executive Summary

**Vision:** Transform CorvinOS from a monolithic system into a compartmentalized architecture where each major feature operates as an independent, toggleable module — like watertight compartments on a ship.

### Current Problem
```
Monolithic CorvinOS
├─ L44 House Rules
├─ L37 Signing
├─ L34 Flow Guard
├─ L28 Conversation Recall
├─ L25 Compute Engine
├─ L23 Speech-to-Text
├─ L22 Engine Routing
└─ ... (44 layers, tightly coupled)

Problem: If one layer fails → entire system fails
Solution: Extract layers as plugins with circuit breakers
```

### Proposed Solution
```
CorvinOS Core (800 LOC)
├─ Session/Auth
├─ HTTP Router
├─ Plugin Registry
├─ Audit Foundation
└─ Config Loader
     ↓
Plugin System (already built!)
├─ Tier A: Forge, LLM, Bridges (always-on)
├─ Tier B: TDE, Recall, Compliance (optional)
└─ Tier C: Community (sandboxed)
```

**Immediate Benefits:**
- 🟢 Each feature can be enabled/disabled independently
- 🟢 Per-feature audit logging
- 🟢 Multi-tenant feature sets
- 🟢 Graceful degradation (disable TDE → fall back to native)
- 🟢 Ecosystem-ready (community plugins)

---

## The Five Critical Features to Compartmentalize

### 1. Audit & Compliance (L16 + L37)
**Current:** Baked into every operation, no fallback  
**Risk:** GDPR audit chain corruption = security incident

**Proposed Plugin:**
```python
class AuditPlugin(Plugin):
    async def log_event(self, event: AuditEvent) -> None:
        """Log to audit trail (file/DB/syslog)."""
    
    async def verify_chain(self) -> bool:
        """Verify hash-chain integrity."""
    
    async def on_disable(self):
        """Gracefully queue events if audit subsystem slow."""
```

**Why First:** Audit failures cascade through entire system; needs circuit breaker.

---

### 2. User Management & Auth (L18-21)
**Current:** Core auth, no bypass  
**Flexibility:** Should support OIDC, LDAP, local, guest modes

**Proposed Plugin:**
```python
class UserManagementPlugin(Plugin):
    async def authenticate(self, creds: Credentials) -> User:
        """Verify identity via configured provider."""
    
    async def enforce_quota(self, user_id: str) -> None:
        """Check per-user resource limits."""
    
    async def on_config_change(self, new_auth_provider: str):
        """Switch auth backends (OIDC → LDAP)."""
```

**Why Second:** Enables true multi-tenancy (different auth per tenant).

---

### 3. Compute Engine (L25)
**Current:** Singleton, hardcoded, no alternative  
**Flexibility:** Should support local workers, remote, Kubernetes

**Proposed Plugin:**
```python
class ComputeEnginePlugin(Plugin):
    async def submit_task(self, task: ComputeTask) -> TaskResult:
        """Run compute task."""
    
    async def get_metrics(self) -> ComputeMetrics:
        """Resource usage (CPU, memory)."""
    
    async def on_disable(self):
        """Degrade to minimal in-process compute."""
```

**Why Critical:** Enables deployment flexibility (lightweight vs. feature-rich).

---

### 4. Engine Routing & Delegation (L22 + L34)
**Current:** TDE/ACS always active  
**Flexibility:** Should support native-only, TDE, ACS policies per tenant

**Proposed Plugin:**
```python
class EngineRouterPlugin(Plugin):
    async def route_task(self, task: Task) -> EngineTarget:
        """Decide: native | TDE | ACS."""
    
    async def on_disable(self):
        """Fall back to native execution."""
```

**Why Valuable:** Simpler deployments can run lightweight (native-only).

---

### 5. Speech-to-Text (L23)
**Current:** Bridge-only, not modular  
**Flexibility:** Should support Whisper, Cloud APIs, optional disable

**Proposed Plugin:**
```python
class SpeechToTextPlugin(Plugin):
    async def transcribe(self, audio: bytes) -> str:
        """Audio → text."""
    
    async def on_disable(self):
        """Bridge: voice-mode → text-only."""
```

**Why Graceful:** Voice deployments degrade to text-only when STT unavailable.

---

## Refactoring Phases: 12-18 Month Roadmap

### Phase 0: Foundation ✅
- Plugin System v1 built (Phases 1-2b)
- 56/56 tests passing
- Ready to compartmentalize

### Phase 1: Critical Path (3-4 months)
**Engineers:** 3-4  
**Goal:** Stabilize audit + auth

1. **Extract Audit Plugin** (4 weeks)
   - Move L16 audit logic
   - Circuit breaker + queue
   - Per-tenant audit chains

2. **Extract User Management** (3 weeks)
   - Move L18-21 auth
   - Multi-auth support
   - Formalize guest mode

3. **Integration + E2E** (2 weeks)
   - Ensure both work together
   - GDPR compliance validation

**Deliverable:** Audit and Auth as first-class plugins

### Phase 2: Compute & Routing (3-4 months)
**Engineers:** 2-3  
**Goal:** Enable deployment flexibility

1. **Compute Engine Plugin** (3 weeks)
2. **Router Plugin** (3 weeks)
3. **Load testing + resilience** (2 weeks)

**Deliverable:** Can run native-only OR TDE/ACS

### Phase 3: Graceful Degradation (2-3 months)
**Engineers:** 2  
**Goal:** Optional features fail gracefully

1. **Speech-to-Text Plugin** (2 weeks)
2. **Conversation Recall Plugin** (2 weeks)

**Deliverable:** STT/Recall disable without crashing

### Phase 4: Marketplace Unification (2-3 months)
**Engineers:** 2-3  
**Goal:** All features discoverable

1. **Migrate built-in skills** to marketplace (Code Review, etc.)
2. **Auto-generate settings UI** from schema
3. **Feature discovery + version management**

**Deliverable:** Console shows all features with toggles

---

## Unified Logging Architecture

### Current Problem
```
Operation
   ↓
Layer 44 → Layer 25 → Layer 23 → ... → audit.jsonl
               ↓ (error)
        system crash (no fallback)
```

### Proposed Solution
```
Operation
   ↓
┌──────────────────────────────────────┐
│   Circuit Breaker (Plugin System)    │
├──────────────────────────────────────┤
│ Try: call AuditPlugin.log_event()    │
│ Catch: queue event, continue core    │
│ Fallback: in-memory buffer           │
└──────────────────────────────────────┘
           ↓ (both success + failure)
    ┌──────┴────────┬─────────┐
    ↓               ↓         ↓
 [audit.jsonl]  [audit.db] [syslog]
   (local)     (Postgres)  (external)
```

**Benefits:**
- ✅ Core continues even if audit slow
- ✅ Events queued + replayed when audit recovers
- ✅ Per-tenant audit chains (true multi-tenancy)
- ✅ Swappable backends (file/DB/external)

---

## Multi-Tenant Feature Sets

### Example: Two Tenants, Different Configs

**Tenant A: Enterprise (Full-Featured)**
```yaml
plugins:
  audit-compliance:
    enabled: true
    config: 
      retention_days: 2555  # 7 years (legal)
      backend: postgres
  
  user-management:
    enabled: true
    config:
      auth_provider: ldap
      guest_mode: false
  
  engine-router:
    enabled: true
    config:
      policy: tde  # Use TDE
  
  speech-to-text:
    enabled: true
    config:
      engine: whisper
```

**Tenant B: Lightweight (Minimal)**
```yaml
plugins:
  audit-compliance:
    enabled: false  # Audit off for speed
  
  user-management:
    enabled: true
    config:
      auth_provider: local
      guest_mode: true
  
  engine-router:
    enabled: false  # Native-only, no TDE
  
  speech-to-text:
    enabled: false  # Text-only mode
```

---

## Success Criteria (12-18 Months)

### Core Stability
- [ ] CorvinOS runs with any plugin subset
- [ ] No "required plugin" hidden in code
- [ ] Clear error when user requests disabled feature

### Audit Trail
- [ ] Every operation logged (even failures)
- [ ] Hash-chain verifiable per-tenant
- [ ] GDPR audit retention automated

### Graceful Degradation
- [ ] TDE disabled → native execution works
- [ ] STT disabled → text mode works
- [ ] Recall disabled → no memory features
- [ ] Router disabled → native-only works

### Multi-Tenancy
- [ ] Per-tenant feature sets
- [ ] Per-tenant audit chains
- [ ] No feature leakage between tenants

### Documentation
- [ ] Migration guide for each feature
- [ ] Settings schema (JSON Schema) for UI
- [ ] E2E tests with/without each plugin

---

## Effort & Timeline Summary

| Phase | Work | Engineers | Duration | Risk |
|-------|------|-----------|----------|------|
| 0 | Foundation | — | ✅ Done | Low |
| 1 | Audit + Auth | 3-4 | 3-4 mo | **HIGH** |
| 2 | Compute + Router | 2-3 | 3-4 mo | HIGH |
| 3 | STT + Recall | 2 | 2-3 mo | MEDIUM |
| 4 | Marketplace | 2-3 | 2-3 mo | LOW |
| **TOTAL** | Compartmentalization | 12-15 | **12-18 mo** | **HIGH** |

**Resource:** 50-100 engineer-weeks, ~3-5 engineers full-time

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Audit plugin crash | CRITICAL | Circuit breaker + in-memory queue + fallback to stderr |
| Auth bypass | CRITICAL | Auth always required (can never be disabled) |
| Regression in disabled path | HIGH | E2E tests: every feature on + every feature off |
| Performance overhead | MEDIUM | Benchmark every plugin boundary (target <5% latency) |
| Operator confusion | MEDIUM | CLI tool: `corvin features list` (shows enabled/disabled) |
| Async audit queue bottleneck | MEDIUM | Async queue size monitoring + circuit breaker |

---

## Next Steps (Immediate)

### Week 1: Team Alignment
- [ ] Discuss this proposal in engineering standup
- [ ] Stack-rank plugins (audit vs. auth vs. compute?)
- [ ] Get security review (audit + auth are critical)

### Week 2: Spike (Phase 1 Design)
- [ ] Extract Audit Plugin interface (4 days, 2 engineers)
- [ ] Prototype circuit breaker (2 days, 1 engineer)
- [ ] Design User Management plugin (2 days, 1 engineer)

### Week 3-4: Phase 1 Implementation
- [ ] Begin Audit plugin migration
- [ ] Begin User Management plugin migration
- [ ] E2E testing starts

---

## Why This Matters

**Current:** CorvinOS is a ship with no compartments. One leak → entire ship sinks.

**Proposed:** CorvinOS is a ship with watertight compartments. One compartment floods → others stay dry.

This architecture enables:
- **Reliability:** Graceful degradation instead of cascading failures
- **Flexibility:** Operators choose which features to enable
- **Multi-Tenancy:** Different tenants, different feature sets
- **Ecosystem:** Community can build plugins without core changes
- **Observability:** Per-feature audit logging + metrics
- **Testability:** Plugins tested in isolation + with other plugins

---

## Recommendation

**Start Phase 1 with Audit + Auth plugins** because:
1. Audit is prerequisite for everything (GDPR audit trail)
2. Auth enables multi-tenancy
3. Both are critical paths (deserve full attention)
4. Success here validates the entire refactoring strategy

**Timeline:** Start immediately (4-person team, 3-4 months for Phase 1).

---

**This refactoring is ambitious but achievable. The payoff: a self-healing, self-describing, independently-scalable platform.** ⚓
