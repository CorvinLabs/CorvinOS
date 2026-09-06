# Architecture Overview: CorvinOS v2.0

**Read Time:** 20 minutes | **Audience:** Architects, Team Leads | **Diagrams:** 2 SVG

## Core Concept

CorvinOS v2.0 is an **Agentic Control Plane (ACP)** — not a task runner, but an operating system where **Skills 2.0 are the unified control plane**, replacing hardcoded logic with versioned, self-learning programs.

```
┌─────────────────────────────────────────────────────────────────┐
│  CorvinOS v2.0: Agentic Operating System                        │
│                                                                  │
│  Every subsystem (routing, context, workflow, security) is      │
│  a Skill — versioned, audited, self-optimizing, replaceable.    │
│                                                                  │
│  Core invariant: AUDIT EVERYTHING. Proof system: hash-chain.    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Four Pillars

### 1. **Skills 2.0: Versioned Programs (Not Static Prompts)**

A Skill is a **hybrid program** that combines:
- **Deterministic Python** (routing logic, caching, validation — fail-fast)
- **Optional LLM** (when uncertain; e.g., `/router classify_request`)
- **Feedback loop** (learn from outcomes; update config)
- **Audit trail** (every execution logged, hash-chained)

**Why?** Static prompts drift; Skills evolve. Skills are composable, versioned, observable.

**Example: `os.delegation_router` (L5)**
```
Input: Task + user_id + context
→ Python: Load tenant config
→ LLM (if uncertain): "Which engine best fits this task?"
→ Python: Select engine (Claude/Opus/Hermes based on cost/quality tradeoff)
→ Audit: skill_executed event (input, output, latency, confidence)
→ Feedback: Operator says "was that correct?" → update router weights
```

**Five Phases of Replacement (ADR-0532 Roadmap):**
| **Phase** | **L-Layer** | **Skill** | **Status** |
|---|---|---|---|
| 1 | L5, L10 | os.delegation_router, os.context_adapter | ✅ Complete |
| 2 | L22 | os.workflow_optimizer | 🔄 In Progress |
| 3 | L16 | os.security_orchestrator | 📋 Planned |
| 4 | L34 | os.flow_guard | 📋 Planned |
| 5 | Meta | os.* Skill composition framework | 📋 Planned |

### 2. **Learning Infrastructure: 6D Loss Vector**

**Problem:** Six independent feedback loops operate causally coupled.
- Routing fails → context degrades
- Context bad → execution slow
- Slow execution → user unhappy
- Low confidence → optimizer diverges
- ...

**Solution (ADR-0614–0616):** **Unified loss function** with backpropagation.

```
L_total = w₁·L_routing + w₂·L_context + w₃·L_exec + w₄·L_conf + w₅·L_comply + w₆·L_learn

where:
  w₁..w₆ ∈ [0,1], Σwᵢ = 1.0
  Gradients backprop to optimizer
  Converges in <2000 samples
```

**Six Feedback Loops:**
1. **Routing Loop** — "Was the agent selection correct?"
2. **Context Loop** — "Did the context help?"
3. **Execution Loop** — "SLA met? No errors?"
4. **Confidence Loop** — "P(correct | input)?"
5. **Compliance Loop** — "PII leaked? Consent violated?"
6. **Learning Loop** — "Did config converge?"

All six are **audited** (every feedback → audit event). Operator sees full proof.

### 3. **Plugin System: Trust Boundaries + Marketplace**

**Architecture:** Five boot layers (compliance → core → bundled → installed → community).

```
┌─ Compliance (Meta-Skills) ──────────────────────┐ TIER 0
│ Audit, Consent, Tripwire, House-Rules (LOCKED)  │ Always-on
├─ Core Plugins ─────────────────────────────────┤ TIER 1
│ Routing, Context, Learning, Workflow            │ Versioned
├─ Bundled Plugins ──────────────────────────────┤ TIER 2
│ Memory, Cache, Telemetry, Integration           │ Disableable (local)
├─ Installed Plugins (from Marketplace) ─────────┤ TIER 3
│ Vetted, downloaded, checksummed                 │ Enable/Disable
├─ Community Plugins (Marketplace) ──────────────┤ TIER 4
│ Community-contributed, sandboxed                │ Subprocess isolation
└─ Custom Plugins (future) ──────────────────────┘ TIER 5
  Operator-built, namespace-gated                  Fail-closed
```

**Marketplace Model (ADR-0511):**
- **Central index** (plugins/index.json) — metadata + checksums
- **Plugin source** (plugins/buildin/<category>/<name>/) — complete src/, tests/, README
- **Vetted registry** — per-category maintainers, review gate
- **Trust boundary** — in-process (compliance/core) vs. subprocess (community)

**Why?** In-process plugins are part of the process. Compliance plugins can never be disabled. Community plugins run sandboxed (30s timeout, IPC via JSON-RPC, kill on error).

### 4. **Audit Chain: Immutable Proof System**

**Core Principle:** Every action → immutable hash-chained event. Operator can prove everything.

**Hash Chain Mechanics:**
```
Event N:   {tenant_id, timestamp, event_type, output, hash: sha256(...abc123)}
           ↓
Event N+1: {tenant_id, timestamp, event_type, output, hash: sha256(...def456), prev_hash: sha256(...abc123)}
           ↓
Event N+2: {tenant_id, timestamp, event_type, output, hash: sha256(...ghi789), prev_hash: sha256(...def456)}
           ↓ [continues...]

INVARIANT: Tampering any event breaks the chain.
```

**What Gets Audited:**
- **Plugins** — plugin_loaded, plugin_executed, plugin_disabled
- **Skills** — skill_executed, skill_config_updated, skill_feedback
- **Security (L16)** — consent_granted, consent_checked, house_rule_denied
- **A2A (L38)** — a2a_task_received, a2a_task_executed, a2a_result_sent
- **Learning (ADR-0314)** — learning_feedback, optimizer_config_updated
- **Audit Itself (L37)** — audit_chain_verified, audit_snapshot, key_rotated

**Tenant Isolation (GDPR Art. 5, 6, 32):**
Every event carries `tenant_id`. No cross-tenant queries. Fail-closed: NULL tenant → rejected.

**Operator Proof Workflow:**
```bash
corvin audit show-task <task_id>          # All events for this task
corvin audit verify-chain --tenant=_default  # Chain integrity check
corvin audit export --format=pdf --since=2026-09-01  # Compliance report
corvin audit trace skill os.router --task=<id>  # Skill decision chain
```

---

## System Topology: Five Layers

### Layer 1: Input Interfaces
- **CLI** — Corvin commands
- **Web Console** — React + real-time updates
- **Voice/Discord** — Bridge (audio → text)
- **A2A** — App-to-App (Task Envelope Protocol, L38)
- **MCP Tools** — Claude API integration

### Layer 2: L5 Routing (Skills-Driven)
- **os.delegation_router** — Which engine? (Claude/Opus/Hermes based on task type)
- **os.context_adapter** — User/task pattern learning
- **Learning loop** — Feedback → router config tuning

### Layer 3: L10 Context Engineering (Hybrid Model)
- **Original Context** — Immutable base
- **Preserved Layers** — Truncation-immune (key facts re-injected each turn)
- **Injected Signals** — Skills + feedback (real content, not pointers)
- **Merged Output** — LLM-ready prompt

**Why Hybrid?** Sessions get truncated (token limit). Important facts must survive truncation. Preserve + re-inject = context is never lost.

### Layer 4: L16/L22 Security + Workflow
- **L16 Compliance Gates** — Consent (deny-by-default), House-Rules (fail-closed)
- **Audit Chain** — Every decision logged (immutable)
- **L22 Workflow (Skills)** — os.workflow_optimizer (DAG-validated skill composition)

### Layer 5: Plugins + Audit
- **Plugin System** — Five boot layers + marketplace
- **Learning Infrastructure** — 6D loss + backprop + operator tuning
- **Audit Chain** — Hash-chained proof (GDPR compliance)

---

## Data Flow Example: A Complete Request

```
1. USER INPUT (Discord voice)
   ↓
2. BRIDGE (audio → text, transcription metadata audit)
   ↓
3. L5 ROUTING (os.delegation_router Skill)
   ├─ Input: task_type="question_answering", user_id="silvio"
   ├─ Decision: route_to="opus" (cost-optimized)
   ├─ Audit: skill_executed event logged (hash-chained)
   ↓
4. L10 CONTEXT (os.context_adapter Skill)
   ├─ Original: full session history (base context)
   ├─ Preserve: [task_id, user_preferences, prior decisions]
   ├─ Inject: [router decision, learning feedback, user model]
   ├─ Merge: compact prompt (LLM-ready, truncation-safe)
   ├─ Audit: context_adapted event
   ↓
5. L16 COMPLIANCE
   ├─ Check consent: user_id has "voice_enabled" → OK
   ├─ Check house-rules: task not in deny-list → OK
   ├─ Audit: consent_checked event
   ↓
6. L22 WORKFLOW (os.workflow_optimizer Skill)
   ├─ Decide: route to Opus engine
   ├─ Check: required_checks in manifest (audit, consent, house-rules)
   ├─ Execute: Skill.execute(context, agent=opus)
   ├─ Audit: skill_executed event (input, output, latency)
   ↓
7. OUTPUT (response to user)
   ├─ Audit: response_generated event
   ↓
8. FEEDBACK LOOP (async, non-blocking)
   ├─ Operator (or auto-metric): "Was that correct?"
   ├─ Audit: skill_feedback event (signal: yes/no/neutral)
   ├─ Optimizer: Update router weights based on feedback
   ├─ Audit: optimizer_step event (delta_w, convergence score)
   ↓
9. AUDIT TRAIL (daily verification)
   ├─ Hash-chain integrity check
   ├─ Audit: audit_chain_verified event
   ├─ Export to audit.jsonl (tenant-scoped, RFC 3161 timestamped)

INVARIANT: Every arrow → immutable audit event (hash-chained)
```

---

## Key Invariants (Load-Bearing)

### 1. **Audit-First**
Every decision, every config change, every feedback → audit event BEFORE any state mutation. No silent operations.

### 2. **Versioning**
Skills are versioned (semver). Config evolves; logic never breaks backward compat. Previous versions always retrievable.

### 3. **Fail-Closed**
Security gates (consent, house-rules, compliance) default to DENY. Errors → deny, never grant.

### 4. **Tenant Isolation**
Every event carries `tenant_id`. No cross-tenant data leakage. Audit reads filtered by tenant.

### 5. **No Silent Optimization**
Learning happens visibly. Operator can see every weight update. Divergence detected & alerted.

---

## Glossary

| **Term** | **Definition** |
|---|---|
| **ACP** | Agentic Control Plane — Skills 2.0 as unified OS subsystem |
| **Skill** | Versioned program (Python + optional LLM + feedback loop + audit trail) |
| **Plugin** | Extension (may be trusted or sandboxed; different trust model than Skills) |
| **Boot Layer** | Five tiers of plugin loading (compliance → core → bundled → installed → community) |
| **L-Layer** | Security/compliance layer (L1–L44). Skills implement L-layer contracts. |
| **Audit Event** | Immutable, hash-chained record of an action (logged to audit.jsonl) |
| **Tenant** | Isolated user/project scope (GDPR Art. 5, 6, 32 isolation boundary) |
| **LoM** | Line of Moral Responsibility — cryptographic attribution (skill_id, version, code location) |
| **Backprop** | Gradient flow from 6D loss backward through decision DAG (learning signal) |

---

## Reading Paths

**I'm a new architect:**
1. This doc (overview)
2. [ACP Vision: Skills 2.0](06_ACP_VISION.md)
3. [Learning Infrastructure](07_LEARNING_INFRASTRUCTURE.md)

**I'm implementing a feature:**
1. This doc (architecture)
2. [Plugin System](08_PLUGIN_SYSTEM.md)
3. [Audit Chain](09_AUDIT_CHAIN.md)
4. Implementation reference (Phase B)

**I'm doing compliance audit:**
1. [Audit Chain](09_AUDIT_CHAIN.md)
2. [Compliance Baseline](10_COMPLIANCE_BASELINE.md)
3. Layer Stack Reference (Phase B)

---

**See Also:**
- [DIAGRAM_01: ACP Vision (High-Level)](../outputs/DIAGRAM_01_ACP_VISION_HIGH_LEVEL.svg)
- [DIAGRAM_05: Data Flow Through System](#) (coming in extended docs)
- ADR-0532–0535 (OS-Skills architecture roadmap)
- CLAUDE.md (maintainer rules)

**Next:** [ACP Vision: Skills 2.0 as Control Plane](06_ACP_VISION.md)
