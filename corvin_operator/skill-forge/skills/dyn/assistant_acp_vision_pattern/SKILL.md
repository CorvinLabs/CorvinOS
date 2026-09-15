---
name: assistant_acp_vision_pattern
description: L-Layer to Skill transformation pattern: when a monolithic subsystem should become a versioned, audited, self-learning Skill (ACP Vision)
---

# ACP Vision Pattern — When an L-Layer Becomes a Skill

**Quick Decision:** Is your subsystem making a decision? Is it tunable? Is it security-critical? Is it learnable?

```
Decision? + Tunable? + ¬Security-Critical? + Learnable?
   ↓           ↓                ↓                  ↓
  YES    +    YES     +        NO        +       YES       = **SKILL**
```

## The 7-Step Transformation: L-Layer → Skill

### Step 1: Identify Subsystem Boundary
Define: Input, Output, Immutable State. Map today's hardcoded logic.

### Step 2: Decouple Deterministic + LLM Paths
```
input → Python (local decisions) → [opt. LLM] → Python (learned params) → output + audit_event
```

### Step 3: Design Skill Manifest + Immutable Event Schema
- Manifest: `skill_id`, `version` (semver), `config_params`, `required_checks` (compliance gates)
- Event: frozen dataclass with `tenant_id`, `skill_id`, `input_hash`, `output_hash`, `lom` (line-of-moral-responsibility), `prev_hash` (chain)

### Step 4: Wire into Audit Chain (ADR-0299)
Every `Skill.execute()` → `audit_backend.write_event()`. Hash-chain to prior event.

### Step 5: Add Feedback Loop (ADR-0314)
Operator gives feedback → Optimizer tunes params (deterministic, fail-closed) → next invocation uses tuned config.

### Step 6: Compose Multi-Skill Workflows (if needed)
Declare dependencies in manifest. Topological load order. DAG-validated at boot (no circular deps).

### Step 7: Compliance Meta-Skill Layer (Immutable)
Audit, Consent, Tripwire, House-Rules = Meta-Skills (monolithic, never versioned, never disabled).

## Load-Bearing Invariants (All Five Required)

1. **Audit-First:** Every decision + feedback + config change → immutable event (tenant-scoped, hash-chained)
2. **Versioning:** Config evolves; logic never breaks backward compat (semver)
3. **No Silent Optimization:** Learning events are audited; operator can inspect delta
4. **Compliance Immune:** Meta-Skills run independently; no Product Skill can disable them
5. **Compositional + Observable:** DAG-validated dependencies; dashboard shows confidence, feedback health, convergence

## When To Use / When NOT

| Scenario | Use? | Why |
|---|---|---|
| New L-layer decision subsystem | ✅ | Make it Skill from day 1 |
| Existing rigid L-layer | ✅ | Transform incrementally (7 steps) |
| One-off setup logic | ❌ | Use script/hook, not Skill |
| Security-critical mechanism | ⚠️ | Meta-Skill (monolithic, locked) |
| &lt;3ms hot-path SLA | ⚠️ | Profile first; 1–2ms Skill overhead |

## The Boundary Map

| L-Layer | Subsystem | Today | ACP Vision |
|---|---|---|---|
| L5 | Routing | Hardcoded | **Skill** ✅ |
| L10 | Path-Gate | Config | **Skill** ✅ |
| L16 | Consent/Audit | Hardcoded | **Meta-Skill** (locked) |
| L22 | Workflow | Stateless | **Skill Orchestrator** ✅ |
| L34 | Data-Flow Guard | Validator | **Hybrid** (validator monolithic, policy learnable) |
| L44 | House-Rules | Config | **Meta-Skill** (locked) |

## Why This Pattern Works

**Evidence from CorvinOS History:**

1. **Phase 2 Feature Flags Failed** (behavioral ≠ structural)
2. **Phase 3 Plugin-Security Took 5 Iterations** (in-process guards fail; Tripwire succeeds)
3. **Audit Chain + Learning Loop Converge** (external verification + feedback = self-optimizing)
4. **ADR Chain:** ADR-0299 (Audit) → ADR-0314 (Learning) → ADR-0532 (Skills) — Load-bearing prerequisites

## The Four Load-Bearing Boundaries (Dialect Synthesis)

1. **Meta-Skills are Monolithic:** Audit, Consent, Tripwire, House-Rules never versioned
2. **Learning Loop Needs Trust-Boundary:** Feedback validated against observable reality (malice detection)
3. **DAG + Dynamic Circle Detection:** Static DAG at boot; runtime monitoring for learning-event cycles
4. **Audit-Failure is Fail-Closed:** Audit-Backend must initialize before Skill loads (boot order: Tripwire → Audit-Verify → Skill-Load)

## One-Liner Decision Rule

**"Does this subsystem decide? Tune itself without code changes? And is it not security-critical?"** → **Skill.**

---

**Related:** CONCEPT-0016 (full documentation), ADR-0299/0314/0532/0534/0535/0537 (implementation ADRs), docs/claude-ref/layer-summary.md (all L-layers)
