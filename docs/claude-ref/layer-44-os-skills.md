# Layer 44 — OS-Skills: Agentic Control Plane (ADR-0532)

> Load when: designing or implementing OS-Skills, modifying skill lifecycle, or understanding skill-based orchestration.

**Status:** PHASE 1 WIRED (2026-09-26)
- ✅ L5 (Delegation Routing): `os.delegation_router` — shadow mode active
- ✅ L10 (Context Adaptation): `os.context_adapter` — shadow mode active, audited
- 🟡 L22 (Workflow Optimization): not wired — a draft `core/skills/os_skills/workflow_optimizer/skill.py` exists but has no production caller as of 2026-09-27 (adversarial review)
- 🟡 L16 (Security Orchestration): not yet built
- 🟡 L34 (Data Flow Guard): not yet built

---

## Overview

**OS-Skills** transform CorvinOS from a static task-runner into a **self-optimizing agentic OS**. Each OS-Skill is a composable, learnable program that owns a critical OS-level decision:

| Skill | Layer | Purpose | Status |
|---|---|---|---|
| `os.delegation_router` | L5 | Route tasks: native, ACS, or TDE | ✅ Wired (shadow) |
| `os.context_adapter` | L10 | Inject context per agent type | ✅ Wired (shadow) |
| `os.workflow_optimizer` | L22 | Parallelize vs. serialize multi-worker tasks | Not wired (draft module, no production caller as of 2026-09-27) |
| `os.security_orchestrator` | L16 | Detect threat patterns, suggest policies | Not built |
| `os.flow_guard` | L34 | Learn safe data flow shapes | Not built |

**Core Property:** Every OS-Skill:
- Runs **12-phase execution** (intake → learning → delivery) with resumable checkpoints
- Emits **immutable audit events** (hash-chained, GDPR Art. 30/32)
- Learns from **feedback loops** (ADR-0314 integration)
- Composes with **other skills** (DAG validation, no cycles)
- Versioned & **hot-swappable** (semantic versioning, in-flight freeze)

---

## Why OS-Skills? (The Problem)

**Today's hardcoded approach:**
- Delegation decisions are baked into `delegation_policy.py` — change = PR + deploy
- Context injection is one-size-fits-all — no tenant/task adaptation
- Orchestration shapes are fixed — no data-driven parallelism experiments
- No feedback loop — decisions never improve over time
- Operator can't experiment — testing a heuristic takes a week-long PR cycle

**Impact Metrics:**
- Delegation latency: 50/50 split (should be 75/25 with learning)
- Context bloat: 30% wasted tokens on context agents don't need
- Orchestration suboptimal: fixed shapes, no adaptation to worker availability
- Experimentation impossible: hardcoded, no hot-swap

**OS-Skills solution:**
- ✅ Installable & versioned (like plugins, for OS orchestration)
- ✅ Observable (full audit trail, every decision explained)
- ✅ Learnable (feedback loops → optimization → score-tracking)
- ✅ Hot-swappable (v1.2 → v1.3 in 100ms, no deploy)
- ✅ Composable (skills call other skills, DAG-validated)
- ✅ Safe (phase timeouts, deterministic validation, GDPR-compliant)

---

## Architecture (5 Layers)

OS-Skills ship as **5-layer stacks**, each layer serving a purpose:

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: Registry & Lifecycle                               │
│  - skill_manager.py: discover, install, activate            │
│  - registry.yaml: manifest index                            │
│  - skill_validator.py: schema + DAG validation              │
└─────────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────────┐
│ Layer 2: Manifest & Metadata                                │
│  - manifest.yaml: triggers, schemas, learning signals       │
│  - Dependencies declared (ADR-0533)                         │
│  - Boot layer & origin: compliance, bundled, installed      │
└─────────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────────┐
│ Layer 3: Prompt-Logic (12 Phases)                          │
│  - SKILL.md: intake → dialektical → delivery               │
│  - Input validation → context loading → decision logic      │
│  - Fallback at each phase (resilient execution)            │
└─────────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────────┐
│ Layer 4: Deterministic Validation                           │
│  - scripts/validate_*.py: schema checks, PII filtering      │
│  - Anomaly detection, fail-closed on suspicious signals     │
│  - Immutable input/output logging                           │
└─────────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────────┐
│ Layer 5: Learning Loop & Optimization                       │
│  - ADR-0314 integration: feedback ingestion                 │
│  - Score-tracking: per-tenant, per-epoch                    │
│  - Optimizer: 1 param change/iteration, convergence @ MDE<5%│
└─────────────────────────────────────────────────────────────┘
```

**Each layer is:**
- **Observable:** Full audit trail (ADR-0232/0233 hash-chained)
- **Deterministic:** Python scripts validate before LLM layer
- **Tenant-scoped:** No cross-tenant data leakage (GDPR Art. 32)
- **Resumable:** State committed before each phase transition

---

## Skill Format (Directory Structure)

Every OS-Skill ships as a versioned directory:

```
~/.corvin/skills/os_<name>_v<version>/
├── manifest.yaml                    # Metadata, triggers, schemas
├── SKILL.md                         # 12 phases (intake→delivery)
├── references/
│   ├── background-context.md        # Knowledge for LLM
│   └── examples.md                  # Real-world examples
├── scripts/
│   ├── validate_input.py            # Deterministic input checks
│   ├── validate_output.py           # Deterministic output checks
│   ├── detect_anomalies.py          # PII/security checks
│   └── optimize_config.py           # Learning loop integration
├── tests/
│   ├── test_e2e_skill.py            # End-to-end execution
│   ├── test_dag_composition.py      # Dependency resolution
│   └── test_feedback_loop.py        # Learning integration
└── runs/                            # Per-tenant runtime state
    ├── run_2026_09_01_xyz/          # Immutable execution record
    │   ├── input.json               # Skill input
    │   ├── context_snapshot.json    # Context at time of execution
    │   ├── decision_record.md       # LLM reasoning
    │   ├── output.json              # Skill output
    │   └── audit_event.jsonl        # Hash-chained audit record
    ├── run_2026_09_02_abc/
    │   └── ... (same structure)
    ├── feedback_log.jsonl           # Append-only feedback events
    ├── grading_stats.json           # Score tracking + convergence
    └── config_history.jsonl         # Optimizer param changes
```

---

## Execution Lifecycle (12 Phases)

Every OS-Skill invocation follows a **12-phase model** with resumable checkpoints:

| Phase | Timeout | Purpose | Fallback |
|-------|---------|---------|----------|
| **0: Intake** | 100ms | Validate input schema | Hard-reject + audit |
| **1: Load Context** | 500ms | Fetch history, tenant config | Empty context, proceed |
| **2: Clarify** | 2s | Resolve ambiguities in input | Use defaults |
| **3: Dialectical** | 3s | Surface design choices (why this decision?) | Fallback heuristic |
| **4: Planning** | 2s | Build decision tree | Minimal strategy |
| **5: Execution** | 5s | Core decision logic (deterministic or LLM) | Emergency default |
| **6: Reasoning** | 3s | Generate explanation (audit trail) | "Decision made" |
| **7: Feedback Capture** | 500ms | Persist events to learning system | Enqueue for batch |
| **8: Validation** | 1s | Anomaly detection, PII filter (fail-closed) | Log anomaly, proceed |
| **9: Output Schema** | 200ms | Validate output against schema | Return last-good-output |
| **10: Delivery** | 200ms | Return decision to caller | Return fallback |
| **11: Grading (Async)** | N/A | Score decision (async, after invocation) | Retry loop (eventual) |

**Key Property:** Every phase **commits state before calling next** → resumable on crash.

```python
# Example: os.delegation_router execution flow
async def execute_skill(input: Dict) -> Dict:
    state = SkillExecutionState()
    
    # Phase 0: Intake
    state.phase = 0
    state.commit()  # Save checkpoint
    validate_input_schema(input)
    
    # Phase 1: Load Context
    state.phase = 1
    state.commit()
    history = load_tenant_history(input['tenant_id'])
    
    # Phase 3: Dialectical (optional, debug mode)
    state.phase = 3
    state.commit()
    if debug_mode:
        reasoning = await lm_dialectical(input, history)
    
    # Phase 5: Execution
    state.phase = 5
    state.commit()
    decision = classify_task_and_route(input, history)  # Deterministic + LLM
    
    # Phase 7: Feedback Capture
    state.phase = 7
    state.commit()
    emit_skill_executed_event(decision, latency=...)  # ADR-0314
    
    # Phase 9: Output Schema
    state.phase = 9
    state.commit()
    validate_output_schema(decision)
    
    return decision
```

---

## Learning Integration (ADR-0314)

OS-Skills learn via a closed loop:

```
Execution → Audit Event → Feedback → Optimizer → Config Update
```

**Step 1: Emit Execution Event**
```python
# Phase 7 (Feedback Capture)
audit_backend.write_event({
    'event_type': 'skill_executed',
    'skill_id': 'os.delegation_router',
    'version': '1.2.3',
    'input': {'task_shape': 'small_code', 'context_size': 500},
    'output': {'decision': 'native', 'confidence': 0.92, 'reasoning': '...'},
    'latency_ms': 42,
    'tenant_id': 'tenant_123',
    'timestamp': '2026-09-01T12:34:56Z',
})
```

**Step 2: Operator Provides Feedback**
```python
# User rates the decision (thumbs up/down or explicit score)
feedback_backend.write_event({
    'event_type': 'skill_feedback',
    'skill_id': 'os.delegation_router',
    'run_id': 'run_2026_09_01_xyz',
    'feedback_type': 'outcome_feedback',  # or: preference, confidence, metric
    'signal': 'correct',  # yes/no/other
    'confidence': 0.95,
    'tenant_id': 'tenant_123',
})
```

**Step 3: Optimizer Learns**
```python
# Optimizer reads execution + feedback
# Computes delta: param_before=[0.85], param_after=[0.87]
# Reason: "Raised confidence threshold: 2 false positives in window"
audit_backend.write_event({
    'event_type': 'skill_config_updated',
    'skill_id': 'os.delegation_router',
    'version': '1.2.3',
    'param_name': 'confidence_threshold',
    'param_before': 0.85,
    'param_after': 0.87,
    'reason': 'false_positive_reduction',
    'confidence_improvement': 0.03,
})
```

**Step 4: Next Invocation Uses Updated Config**
```python
# Skill loads updated config from runs/grading_stats.json
# Next decision uses threshold=0.87 instead of 0.85
```

**Tenant Isolation:**
- Each tenant has its own `feedback_log.jsonl` + `grading_stats.json`
- No cross-tenant learning
- GDPR Art. 5 (data minimization) compliant

---

## Versioning & Hot-Swap (ADR-0533)

OS-Skills use **Semantic Versioning** with **in-flight-freeze semantics**:

**Semantic Versioning:** `MAJOR.MINOR.PATCH`
- `MAJOR`: Breaking change (output schema changes)
- `MINOR`: New feature (new input field, new decision option)
- `PATCH`: Bug fix (no schema changes)

**In-Flight Freeze:**
- A run that starts with `v1.2` finishes with `v1.2`, even if `v1.3` is installed
- Prevents mid-flight version changes from corrupting state
- New runs use the latest version

**Hot-Swap (100ms):**
```bash
# Operator installs new skill version
corvin skills install os.delegation_router@1.3.0

# Current runs finish with v1.2
# New runs start with v1.3
# No deploy, no restart, no downtime
```

**Canary Deployment (Optional):**
```yaml
# manifest.yaml
canary:
  enabled: true
  traffic_percent: 10             # Route 10% traffic to v1.3
  duration_days: 7
  success_criteria:
    score_improvement_percent: 2  # Must beat v1.2 by 2%
    error_rate_max_percent: 1
  auto_rollback_on_failure: true
```

---

## Composition & Dependencies (ADR-0535)

OS-Skills can call other OS-Skills. Dependencies are **DAG-validated** at install time.

**Dependency Declaration** (in manifest.yaml):
```yaml
depends_on:
  - name: os.delegation_router
    version: ">=1.0.0"
    required: true                # Fail if unavailable
    call_pattern: per_worker      # once, per_worker, on_demand
    call_budget_ms: 50            # Time budget per call
    timeout_handling: fail_parent  # or: degrade_gracefully
    
  - name: os.context_adapter
    version: ">=0.9.0"
    required: false               # Continue if unavailable
    call_budget_ms: 100
    timeout_handling: degrade_gracefully
```

**Semantics:**
- `required: true` → missing dep = installation fails or execution fails
- `required: false` → missing dep = skill continues with degraded capability
- `timeout_handling: fail_parent` → dep timeout → parent times out
- `timeout_handling: degrade_gracefully` → dep timeout → parent uses fallback

**DAG Validation** (at install time):
```python
from core.skills.skill_dag_loader import SkillDAGLoader

loader = SkillDAGLoader(registry)
report = loader.validate_skill_dependencies("os.workflow_optimizer")

if not report.is_valid:
    for blocker in report.blockers:
        print(f"❌ {blocker}")  # "Dependency X not found", "Cycle detected", etc.
    sys.exit(1)
```

**Checks performed:**
1. **Existence**: All dependencies exist in registry
2. **Version Constraints**: Installed versions satisfy declared constraints
3. **Cycle Detection** (DFS): No circular dependencies allowed
4. **Timeout Budget**: Total call time ≤ skill timeout

**Example: Composition in Action**

`os.workflow_optimizer` calls `os.delegation_router` for each worker:

```python
# In SKILL.md (Phase 5: Execution)
async def execute_workflow_optimization(input: Dict) -> Dict:
    workers = input['worker_list']  # [worker_1, worker_2, ...]
    decisions = []
    
    for worker in workers:
        # Call dependent skill
        router_input = {
            'task_shape': worker['task_type'],
            'context_size': worker['context_size'],
            'tenant_id': input['tenant_id'],
        }
        
        # Skill composition: os.workflow_optimizer → os.delegation_router
        router_decision = await call_skill(
            'os.delegation_router',
            input=router_input,
            timeout_ms=50,  # From manifest depends_on.call_budget_ms
        )
        
        decisions.append(router_decision)
    
    # Aggregate decisions
    return {
        'decisions': decisions,
        'parallelism': compute_parallelism(decisions),
    }
```

**Feedback Isolation:**
- Each skill's execution is graded independently
- If `os.orchestration` calls `os.delegation_router`, feedback for each is separate
- No cascade of blame (if outcome is bad, don't auto-penalize the caller)

---

## Audit Trail Integration (ADR-0232/0233)

Every OS-Skill decision is **immutable, hash-chained, and auditable**.

**Audit events per skill (boot, invocation, optimization):**

| Event | Timing | Purpose |
|-------|--------|---------|
| `skill.migrated` | Boot-time | A prompt skill (`prompt.<name>`) entered the registry / changed hash (ADR-2175) |
| `skill_executed` | Phases 0–10 | Input, output, latency, reasoning, lom |
| `skill_config_updated` | Phase 11 (async) | Optimizer param changes, reason |

**Example audit trail** (for one `os.delegation_router` invocation):

```jsonl
{"event_type":"skill.migrated","skill_id":"prompt.adr_gate","skill_version":"0.0.0+1a2b3c4d","source":"bundle","action":"registered","content_hash":"1a2b3c4d5e6f7a8b","timestamp":"2026-10-10T12:00:00Z","tenant_id":"tenant_123","hash":"sha256(abc...)","prev_hash":"sha256(xyz...)"}
{"event_type":"skill_executed","skill_id":"os.delegation_router","run_id":"run_2026_09_01_xyz","input":{"task_shape":"small_code","context_size":500},"output":{"decision":"native","confidence":0.92,"reasoning":"Task < 5K tokens, native execution faster"},"latency_ms":42,"lom":"os_skills/delegation_router.py:L237","tenant_id":"tenant_123","timestamp":"2026-09-01T12:34:56Z","hash":"sha256(def...)","prev_hash":"sha256(abc...)"}
{"event_type":"skill_feedback","skill_id":"os.delegation_router","run_id":"run_2026_09_01_xyz","signal":"correct","feedback_source":"operator_ui","timestamp":"2026-09-01T12:35:10Z","tenant_id":"tenant_123","hash":"sha256(ghi...)","prev_hash":"sha256(def...)"}
{"event_type":"skill_config_updated","skill_id":"os.delegation_router","param_name":"confidence_threshold","param_before":0.85,"param_after":0.87,"reason":"false_positive_reduction","confidence_delta":0.03,"timestamp":"2026-09-01T12:36:00Z","tenant_id":"tenant_123","hash":"sha256(jkl...)","prev_hash":"sha256(ghi...)"}
```

**Immutability Guarantees:**
- Every event is hash-chained to the previous event
- No modification of past events (append-only)
- Hash-chain verification runs at boot (ADR-0232 tripwire)
- Operator can inspect full audit trail: `corvin audit show-skill <skill_id>`

---

## Feedback Validation (ADR-0534, built 2026-10-10)

OS-Skills learn from feedback, but **feedback must be validated** to prevent poisoning.
The gate is `core/learning/feedback_gate.admit()`, run inside `EventStore.write_event` for
every `FEEDBACK` learning event (and by `POST /feedback/skill`, which moves a live config
without storing one). Fail-closed; every decision is chained; a refusal answers HTTP 422
`{error, reason, audit_ref}`.

| Layer | Rule | Chained event |
|---|---|---|
| 1 Throttle (`feedback_throttle.py`) | ≤ 5 decisions / 60 s per subject, counted over a persisted content-free ledger; **no override secret** | `learning.feedback_throttled` |
| 2 Reality (`reality_check.py`) | the skill ran (a `skill_executed` learning event of this tenant) within 24 h before the signal; an optional `claimed_output_hash` must match a run's output; `tool:<id>` is unverifiable | `learning.feedback_rejected`, `learning.feedback_reality_mismatch` |
| 3 Trust (`adversarial_detector.py`) | `trust_weight = max(0.1, 1 − rejected/total)` over the last 20 decisions, stamped into `signal.trust_weight`, applied by every consumer; < 0.3 alerts | `learning.adversarial_feedback_detected` |

Accepted → `learning.feedback_accepted`. Full reference: [learning-loop.md](learning-loop.md)
§ Feedback trust gate.

---

## Prompt skills are registry skills (ADR-2175, T-0103/T-0104/T-0105 built 2026-10-10)

Every SKILL.md of the shipped bundle and every SkillForge skill visible to the tenant is
registered by `skills.boot.boot_skills` as **`prompt.<name>`** (`core/skills/prompt_skill_adapter.py`,
one generic `PromptSkill`):

* **Version = content hash** (`0.0.0+<sha8>`); a changed hash re-registers the skill.
* **`skill.migrated`** is chained once per new (id, hash): ids, source label, 16 hex of the
  hash, action (`registered`/`updated`) — never the body.
* `execute()` answers `{"decision": "inject", "mode": <scope>}`; tier `installed`, `learn=False`
  (one run per injected skill per turn would flood the learning store; every run is still audited).
* `skill.executed` records now carry `skill_version` for every skill.
* **Injection goes through the registry (T-0104).** `skill_inject.collect_active_skills` — the one
  choke point for the bridge and the delegate engines — hands its selection to
  `prompt_skill_adapter.gate_injection`, which syncs the selected skills (so a skill forged after boot
  is registered on its first turn) and runs `registry.execute("prompt.<name>")` for each:
  * registry-disabled → **withheld**, except the core quality skills (`adr_gate`, `e2e-wiring-proof`,
    `concept_gate`), whose off switch stays `quality_layers.py`;
  * timeout / error → **injected** (fail-open on injection, never on audit — the failure is chained);
  * **per-turn budget 250 ms** (`DEFAULT_BUDGET_MS`): a call starts only if ≥ 100 ms
    (`PER_CALL_TIMEOUT_MS`) remain, so gating can never overrun it, and a budget-starved skill is passed
    un-decided instead of counted as a failure (three failures would auto-disable it). Measured with the
    real chain writer: 8 skills p50 12 ms, p95 20 ms, max 21 ms.
  * one **`skill.injection.gated`** record per turn: candidates / allowed / withheld / ungated / errors /
    elapsed_ms / budget_ms / budget_exceeded.
  * No audited registry in the process (`booted_registry()` is None — never lazily created) or a turn
    tenant ≠ boot tenant → selection unchanged, nothing gated.
* **Versions are kept and pinnable (T-0105, ADR-0533).** `core/skills/prompt_skill_versions.py`:
  every body the registry is about to execute is recorded in a per-tenant catalog
  (`<tenant>/skills/prompt_versions/catalog.jsonl` — name, seq, sha256, first_seen; content-free) with a
  content-addressed copy (`bodies/<sha256>.md`, written once, **re-hashed on read** — an altered copy is
  never served). Version = `0.0.<seq>+<sha8>`: the patch number orders the edits, the build part names
  the content (`version_manager.SemanticVersion` accepts and ignores `+build` for precedence).
  Pins: `spec.skills."prompt.<name>".version` in `tenants/<tid>/global/tenant.corvin.yaml`, resolved by
  `version_manager.VersionResolver` (its first production caller). A pin to an older version injects
  that stored body; a pin the catalog cannot serve (unknown, or the stored copy altered) **withholds**
  the skill — never falls through to "latest", which would re-install what the operator rolled away
  from. `skill.executed.skill_version` names the version actually served; the per-turn record counts
  `pinned` / `pin_unavailable`. No canaries (single-operator installs roll out 100 %). The registry
  holds the RESOLVED version per id; the catalog holds every version.

Proof: `tests/skills/test_prompt_skill_adapter_e2e.py`, `tests/e2e/test_prompt_skill_injection_gate_bridge_e2e.py` (through `adapter.process_one`, argv dump, verified chain).

---

## E2E Wiring Proof

Every OS-Skill must prove it is **called end-to-end** (not just unit-tested):

```bash
# 1. Run the skill through its real entry points (bridge adapter.process_one and
#    console stream_turn; both land with ADR-2092 and replace the retired
#    tests/skills/test_delegation_router_wiring_e2e.py)
pytest tests/e2e/test_os_skills_l5_l10_wiring.py \
       tests/e2e/test_l5_routing_loop_bridge_e2e.py \
       core/console/tests/test_l5_routing_loop_console_e2e.py -v

# 2. Verify skill was called — the ONE per-tenant chain is
#    <corvin_home>/tenants/<tid>/global/forge/audit.jsonl (tenant_audit_chain()),
#    not ~/.corvin/audit.jsonl
CHAIN="$CORVIN_HOME/tenants/_default/global/forge/audit.jsonl"
grep "skill_executed.*delegation_router" "$CHAIN" | tail -1

# 3. Verify feedback loop works
grep "skill_feedback\|skill_config_updated" "$CHAIN" \
  | grep "delegation_router" | tail -3

# 4. Commit (only if audit trail shows execution)
git add ... && git commit -m "feat(os-skills): add delegation_router [audit-verified]"
```

**Must NOT do:**
- ❌ Unit test skill without E2E proof
- ❌ Call skill in only one code path (needs at least 2 independent call sites)
- ❌ Skip audit verification
- ❌ Declare skill "done" without feedback-loop E2E test

---

## Must NOT do (Absolute Rules)

- **Don't hardcode** OS-level orchestration outside Skills
- **Don't skip Dialectical Reasoning** for skill changes (surface assumptions, confirm tradeoffs)
- **Don't merge without E2E proof** (unit tests ≠ called; prove real execution + audit event)
- **Don't audit-bypass** (every skill action must log, no silent learning)
- **Don't weaken feedback loop** (no opt-out, no stale-feedback bypass)
- **Don't let Skill disable compliance** (audit chain, consent, house-rules are meta-Skills)
- **Don't leak PII into skill manifests** (manifests are public)
- **Don't add OS-level Skill without ADR** (one ADR per new L-layer Skill)
- **Don't fork SkillForge architecture** (one skill_forge registry, not multiple)

---

## References

→ **ADR-0532:** OS-Skills Architecture (full design, roadmap, integration points)  
→ **ADR-0533:** OS-Skill Manifest Schema & Versioning Strategy  
→ **ADR-0534:** Learning Loop Trust-Boundary: Feedback Validation + Reality-Check  
→ **ADR-0535:** OS-Skill Composition & Dependency Resolution  
→ **ADR-0314:** Learning Infrastructure (feedback ingestion, optimizer loop)  
→ **ADR-0232/0233:** Audit Chain Boot Tripwire & Integrity  
→ **skill-manifest-ref.md:** Manifest schema reference with examples  
→ **skill-composition-dag.md:** DAG validation details  

See `/home/shumway/projects/Corvin-Knowledge/decisions/` for full ADR documents.
