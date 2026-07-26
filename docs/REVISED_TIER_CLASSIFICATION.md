# Revised Tier Classification: Agentic Compute Goes to Core

**Date:** 2026-07-26  
**Critical Refinement:** ACS, Compute Worker, Delegation, Workflows are Core, not plugins.

---

## The Insight

**Before:** "Everything non-compliance is a plugin"  
**After:** "Agentic compute is load-bearing. It's Tier-1 Core."

**Why:**
- Without ACS (Autonomous Compute Shell), CorvinOS can't parallelize work
- Without Compute Worker orchestration, TDE and ACS can't operate
- Without Delegation Router, requests can't reach the right engine
- Without Workflows, enterprise automation is impossible

These aren't optional. They're the nervous system of the platform.

---

## Revised 4-Tier Architecture

### Tier 0: Mandatory Compliance (2.4 KB) — Hardcoded, Immutable
```
Audit Trail Writer (L16)         ✅ GDPR Art. 30, 32
Consent Gate (L18)               ✅ GDPR Art. 6, 7
Flow Guard (L34)                 ✅ PII detection, fail-closed
House Rules (L44)                ✅ EU AI Act Art. 5, 50
Erasure Orchestrator (L36)       ✅ GDPR Art. 17
Plugin Registry                  ✅ Bootstrap system
Session + Auth Middleware        ✅ Identity + consent per request
```

**Admin:** View only. Immutable by law.  
**Replaceability:** 0% — These are regulatory requirements.  
**Extensions:** None. Hardcoded.

---

### Tier 1: Core Infrastructure (5-6 KB) — Required, Strategic IP, Extensible

#### Instance Coordination
```
A2A Orchestration (L38)                    Instance-to-instance tasks
  ├─ Ed25519 attestation (immutable)
  ├─ Task envelope protocol
  └─ Extension hooks: routing, attestation.custom_verify, pre/post_send
```

#### Data & Routing
```
Conversation Recall (L28)                  User data persistence
  ├─ Encryption (immutable)
  ├─ Retention policy (immutable)
  └─ Extension hooks: storage_backend, pre/post storage
  
TDE Routing Engine (L22)                   Smart delegation
  ├─ Token accounting (immutable)
  ├─ Budget enforcement (immutable, fail-closed)
  └─ Extension hooks: cost_model, router_strategy
```

#### Agentic Compute (CORE — DO NOT PLUGIN)
```
ACS Manager (Autonomous Compute Shell)     Worker orchestration
  ├─ Task queue + load balancing
  ├─ Worker lifecycle management
  ├─ Timeout + resource limits
  └─ Extension hooks: worker_selector, task_prioritizer

Compute Worker (L25)                       Per-worker execution sandbox
  ├─ Isolated context per worker
  ├─ Token measurement per request
  ├─ Fallback to native if worker unavailable
  └─ Extension hooks: pre_exec, post_exec

Delegation Router (ADR-0190 M1-M8)         Route by policy
  ├─ Policy-driven engine selection
  ├─ Native → TDE → ACS escalation
  ├─ Budget-aware routing
  └─ Extension hooks: route_selection_policy
  
Workflow Engine (ADR-0190 M1-M8)           DAG execution
  ├─ code/merge/route/ask_human nodes
  ├─ Resumable workflows
  ├─ Parallel fan-out
  └─ Extension hooks: workflow_gate (for ACS delegation)
```

#### Control Plane
```
Admin Control Plane                        Dashboard + API
  ├─ Plugin management
  ├─ License checking
  ├─ Metrics + observability
  └─ Extension hooks: admin_dashboard_widgets
```

**Why Tier-1 (not plugins):**
- ACS/Compute are the execution engines. Without them, delegation fails.
- Delegation Router is how requests reach the right place.
- Workflows are enterprise automation infrastructure.
- Forks get these features by definition (can't monetize compute control).

**But:** Tier-1 has 50+ extension points (custom routing, worker selection, priorities).

**Admin:** Can configure + extend via hooks, but CANNOT disable.  
**Replaceability:** 0% (core infrastructure) — but 100% extensible.  
**Extensions:** Custom routing policies, worker selectors, task prioritizers, workflow gates.

---

### Tier 2: Standard Edition (3-4 KB) — Pre-installed, Optional, Free
```
Forge (L6)                      Tool generation
  ├─ Sandbox management
  ├─ Code execution
  └─ Extension hooks: sandbox_provider, execution_policy

SkillForge (L7)                 Skill generation
  ├─ Markdown parsing
  ├─ Prompt injection checks
  └─ Extension hooks: skill_parser, safety_checks

Bridges (L23)                   User-facing integration
  ├─ Discord, Slack, Telegram, WhatsApp
  ├─ Adapter protocol
  └─ Extension hooks: bridge_middleware, message_transform

Structured Logging             Observability
  ├─ CorvinLogger interface
  ├─ Correlation IDs
  ├─ NerveFiber integration
  └─ Extension hooks: log_filter, log_enrichment

Basic Monitoring              Health checks
  ├─ Plugin health polling
  ├─ Basic Prometheus metrics
  └─ Extension hooks: health_check_custom
```

**Why Tier-2:**
- These are differentiators (Forge/SkillForge make CorvinOS unique).
- But optional (could implement Corvin without them).
- Pre-installed in standard edition (90%+ users won't disable).

**Admin:** Can disable, re-enable, configure, extend.  
**Replaceability:** 100% (can disable Forge and still have compute).  
**Extensions:** Sandbox providers, parsers, filters, enrichers.

---

### Tier 3: Premium Plugins (Licensed)
```
Advanced STT                    Cloud speech-to-text (pay-per-minute)
Advanced Classification         ML-based data classification ($X/month)
Custom Auth                     OKTA, SAML, LDAP ($X,000s)
Audit Backends                  Postgres, Splunk, Datadog ($X/month)
Custom Routing Strategies       Regional, cost-optimized ($X/month)
Advanced Monitoring             Predictive alerts, ML-based ($X/month)
```

**Admin:** Must have license key.  
**Replaceability:** 100% (bypass if disabled or unlicensed).  
**Extensions:** Per-plugin hooks.

---

## Tier Mapping: Which Layers Go Where?

| Layer | Name | Tier | Notes |
|-------|------|------|-------|
| L16 | Audit Trail | **Tier-0** | GDPR requirement |
| L18 | Consent Gate | **Tier-0** | GDPR requirement |
| L22 | TDE Routing | **Tier-1** | Core delegation |
| L23 | STT | **Tier-2** (basic) / **Tier-3** (premium) | Basic metadata only |
| L25 | Compute Worker | **Tier-1** | Load-bearing |
| L28 | Recall | **Tier-1** | User data, strategic |
| L29-30 | Delegation | **Tier-1** | Route by policy |
| L34 | Flow Guard | **Tier-0** | GDPR data classification |
| L36 | Erasure | **Tier-0** | GDPR Art. 17 |
| L38 | A2A | **Tier-1** | Instance coordination |
| L44 | House Rules | **Tier-0** | EU AI Act requirement |
| L6 | Forge | **Tier-2** | Differentiator |
| L7 | SkillForge | **Tier-2** | Differentiator |
| ADR-0190 Workflows | Workflow Engine | **Tier-1** | Enterprise automation |
| ADR-0190 ACS | Autonomous Compute Shell | **Tier-1** | Worker orchestration |
| Bridges | Discord/Slack/Telegram | **Tier-2** | Distribution |
| Logging | Structured logging | **Tier-2** | Observability |

---

## Size Estimates (Revised)

```
Tier 0: 2.4 KB
  ├─ Audit writer         (400 LOC)
  ├─ Consent gate         (300 LOC)
  ├─ Flow guard           (200 LOC)
  ├─ House rules          (200 LOC)
  ├─ Erasure              (300 LOC)
  ├─ Plugin registry      (500 LOC)
  └─ Session + middleware (300 LOC)
  
Tier 1: 5-6 KB (STRATEGIC IP)
  ├─ A2A orchestration    (600 LOC)
  ├─ TDE routing          (600 LOC)
  ├─ Recall + backends    (600 LOC)
  ├─ ACS manager          (800 LOC)
  ├─ Compute worker       (700 LOC)
  ├─ Delegation router    (500 LOC)
  ├─ Workflow engine      (600 LOC)
  └─ Admin control plane  (500 LOC)
  
Tier 2: 3-4 KB (DIFFERENTIATORS)
  ├─ Forge                (500 LOC)
  ├─ SkillForge           (400 LOC)
  ├─ Bridges              (1,000 LOC)
  ├─ Logging              (400 LOC)
  └─ Monitoring           (300 LOC)
  
Tier 3: 2-3 KB each (PREMIUM)
  ├─ Advanced STT         (300 LOC)
  ├─ ML classification    (500 LOC)
  ├─ Auth backends        (400 LOC)
  ├─ Audit backends       (400 LOC)
  └─ Monitoring extras    (300 LOC)

TOTAL CORE: 2.4 + 5-6 KB = ~8 KB mandatory
           (compliance + orchestration + compute)
           
TOTAL STANDARD: 8 KB + 3-4 KB = ~11 KB
               (core + differentiators)
```

---

## Business Model Alignment (Revised)

### Open-Source CorvinOS
- **Tier 0 + 1 + 2:** All unlocked (no license checks)
- **Tier 3:** Not installable (no license endpoint)
- **User gets:** Full orchestration, delegation, workflows, tools, skills
- **Community builds on:** ACS, Compute, Delegation, Workflows (extensible)

### Managed SaaS
- **Tier 0 + 1 + 2:** Bundled in subscription ($X/user/month)
- **Tier 3:** Licensed per-feature ($X/month for STT, $Y/month for OKTA, etc.)
- **Revenue from:**
  - Per-user subscription (Tier 1 orchestration)
  - Premium features (Tier 3)
  - Enterprise support

### Enterprise License
- **Tier 0 + 1 + 2:** Licensed (not free)
- **Tier 3:** Licensed per-feature
- **Custom A2A routing/TDE strategies:** Built-in extensions, no extra cost
- **Revenue from:**
  - Enterprise license (Tier 1 + 2 bundle)
  - Premium features (Tier 3)
  - Custom implementation services

---

## Why Agentic Compute is Core (Not Plugin)

### Problem: If ACS/Compute are plugins...
- User disables ACS → system falls back to native → delegation breaks
- User uninstalls ACS → can't delegate at all
- Fork gets ACS for free → loses revenue from "managed orchestration"
- Can't charge for compute services

### Solution: ACS/Compute are Tier-1 Core
- Disabled only if something goes catastrophically wrong
- Fallback path is degradation (native-only), not absence
- Forks get orchestration (expected), but Managed charges for reliability/monitoring
- Extension points (custom worker selectors, routing policies) let enterprises customize

### Parallel: Why A2A is Core (Not Plugin)
- If A2A is plugin, user disables it → instances can't coordinate
- If A2A is plugin, fork gets it for free → lose revenue from "managed instances"
- Solution: A2A is Tier-1, but with extension hooks for custom routing/attestation

**ACS/Compute follow the same pattern.**

---

## Updated Implementation Roadmap

### Phase 1, Weeks 1-2: Core Extraction (Tier 0 + 1)
```
Tier 0: Extract compliance plugins
  ├─ Audit writer
  ├─ Consent gate
  ├─ Flow guard
  ├─ House rules
  ├─ Erasure
  └─ Plugin registry

Tier 1: Extract infrastructure (DO NOT make plugins)
  ├─ A2A orchestration (move to tier-1, add hooks)
  ├─ TDE routing (move to tier-1, add hooks)
  ├─ Recall (move to tier-1, add hooks)
  ├─ ACS manager (move to tier-1, keep as core)
  ├─ Compute worker (move to tier-1, keep as core)
  ├─ Delegation router (move to tier-1, keep as core)
  ├─ Workflow engine (move to tier-1, keep as core)
  └─ Admin control plane
```

**Key:** ACS/Compute/Workflows don't become plugins. They stay in `core/orchestration/`, just with clear interfaces + extension points.

---

## Extension Points for Tier-1 Agentic Compute

### ACS Manager
```python
acs.register_hook("worker_selector", my_worker_selector_fn)
# Hook: Choose which worker gets the task
# Default: Round-robin, can override for affinity/cost

acs.register_hook("task_prioritizer", my_priority_fn)
# Hook: Sort task queue (default: FIFO)
# Can override for SLA-based, cost-based, user-based priority
```

### Compute Worker
```python
worker.register_hook("pre_exec", my_setup_fn)
worker.register_hook("post_exec", my_teardown_fn)
# Can run custom setup/teardown logic
# Default: none
```

### Delegation Router
```python
router.register_hook("route_selection_policy", my_policy_fn)
# Hook: Given request + available engines, choose route
# Default: use configured policy (native → TDE → ACS)
# Can override for custom logic (geo, cost, latency, etc.)
```

### Workflow Engine
```python
workflow.register_hook("workflow_gate", my_gate_fn)
# Hook: Before delegating workflow to ACS, apply custom checks
# Default: Budget-based gate
# Can override for SLA, priority, etc.
```

---

## What's NOT Extensible in Tier-1

```python
# ❌ Can't replace ACS entirely (core infrastructure)
acs.replace_implementation(my_acs)

# ❌ Can't disable Compute Worker (load-bearing)
disable_compute_worker()

# ❌ Can't turn off Delegation Router (routing logic)
disable_delegation()

# ❌ Can't disable Workflows (enterprise automation)
disable_workflow_engine()

# ✅ CAN extend with hooks
acs.register_hook("worker_selector", ...)
router.register_hook("route_selection_policy", ...)
```

---

## Admin Control Implications

**Admin can:**
- ✅ View ACS/Compute/Delegation status
- ✅ Configure routing policies
- ✅ Register custom worker selectors
- ✅ Register custom workflow gates
- ✅ See which Tier-1 features are active

**Admin CANNOT:**
- ❌ Disable ACS (core infrastructure)
- ❌ Disable Compute Worker (core infrastructure)
- ❌ Disable Delegation (core infrastructure)
- ❌ Disable Workflows (core infrastructure)
- ❌ Replace with fork (IP-protected)

System prevents all of these at registry level.

---

## Summary: Revised Tiers

| Tier | Size | What | Disableable | Extensible | Why |
|------|------|------|------------|-----------|-----|
| **0** | 2.4 KB | Compliance | ❌ Never | ❌ No | GDPR/EU AI Act requirements |
| **1** | 5-6 KB | **Agentic Compute** (ACS, Compute, Delegation, Workflows) + A2A + TDE + Recall | ❌ Never | ✅ 50+ hooks | Load-bearing orchestration |
| **2** | 3-4 KB | Forge, SkillForge, Bridges, Logging | ✅ Yes (optional) | ✅ Lots | Differentiators |
| **3** | 2-3 KB each | STT, Classification, Auth, etc. | ✅ Yes (if licensed) | ✅ Per-plugin | Premium features |

**Core is now 8 KB = 2.4 KB (compliance) + 5-6 KB (agentic compute infrastructure).**

