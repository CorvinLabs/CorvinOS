# ACP Vision: Skills 2.0 as Unified Control Plane

**Read Time:** 15 minutes | **Audience:** Architects, Backend Engineers | **Diagrams:** DIAGRAM_01 | **ADRs:** ADR-0532–0535

## Core Insight

CorvinOS transforms from **task-runner** → **agentic operating system** by making **Skills 2.0 the unified control plane**. Every subsystem (routing, context, workflow, security, data flow) becomes a **swappable, versioned, self-learning Skill**.

### Why Skills, Not Hardcoded Logic?

| **Hardcoded Logic** | **Skills 2.0** |
|---|---|
| Fixed: routing rules buried in code | Versioned: updated without code changes |
| Silent failures: routing breaks unexpectedly | Audited: every decision logged |
| No learning: same behavior forever | Self-optimizing: learns from feedback |
| Unobservable: no proof of routing choice | Observable: audit trail shows full decision trace |
| Monolithic: can't replace routing without rebuild | Composable: swap routing Skill at runtime |

**Result:** Operator controls AI behavior via Skill config, not code deployment.

---

## Five Phases of L-Layer Replacement

### Phase 1: L5 + L10 (Complete ✅)
- **os.delegation_router** (L5) — Task classification → engine selection
- **os.context_adapter** (L10) — User/task pattern learning → preserve/inject strategy
- **Status:** Production ready, learning loop integrated

### Phase 2: L22 Workflow (In Progress 🔄)
- **os.workflow_optimizer** — Skill DAG composition + execution ordering
- **Dependency:** Learning infrastructure (ADR-0314) must be live
- **Timeline:** Weeks 2–4

### Phase 3: L16 Security (Planned 📋)
- **os.security_orchestrator** — Consent gate + house-rules enforcement
- **Constraint:** Must be fail-closed (no disable flag)
- **Timeline:** Weeks 5–10

### Phase 4: L34 Data Flow (Planned 📋)
- **os.flow_guard** — Input classification, flow validation
- **Constraint:** 4-stage × engine matrix, fail-closed
- **Timeline:** Weeks 11–18

### Phase 5: Meta-Layer (Planned 📋)
- **Skill composition framework** — Dependency DAG, topological validation
- **New:** Skill-to-Skill RPC, multi-Skill workflows
- **Timeline:** Weeks 19–24

---

## What IS a Skill?

A Skill is a **hybrid program** combining:

1. **Deterministic Python** (sync I/O, caching, validation)
   - Runs in-process, returns fast
   - Example: Load tenant config, check deny-list, select engine

2. **Optional LLM** (when uncertain)
   - Called only if needed (e.g., "Which engine for this task?")
   - Result cached (same question → same answer, no redundant LLM calls)
   - Example: `os.delegation_router` uses LLM 10% of time (easy cases are Python-only)

3. **Feedback Loop** (learns from outcomes)
   - User: "Was that routing correct?" → feedback_received event
   - Optimizer: Update router weights (threshold for Claude → Opus shift)
   - Next invocation: Uses tuned config
   - Audit: Every step logged

4. **Audit Trail** (every execution logged, hash-chained)
   - Input, output, latency, confidence score, errors
   - Audit event includes Skill version (rollback-safe)
   - Operator can prove what decision was made when

### Example: os.delegation_router Skill

```python
class DelegationRouterSkill:
    def __init__(self, tenant_id: str, config: SkillConfig):
        self.tenant_id = tenant_id
        self.model = load_weights(config.router_weights)  # Trained from feedback
        self.lom = "os.delegation_router:execute:L237"
    
    def execute(self, task_text: str, user_id: str) -> SkillOutput:
        # 1. DETERMINISTIC: Load config
        task_type = classify_task(task_text)  # Python-only (fast)
        
        # 2. CONDITIONAL LLM: Use model confidence
        confidence = self.model.predict(task_text)
        if confidence < 0.7:  # Uncertain → ask LLM
            engine_recommendation = self.llm.call(
                f"Best engine for: {task_text}?"
            )
            engine = parse_recommendation(engine_recommendation)
        else:  # Confident → use model
            engine = self.model.select_engine(task_text)
        
        # 3. AUDIT: Log decision
        audit.write_event(
            tenant_id=self.tenant_id,
            event_type="skill_executed",
            skill_id="os.delegation_router",
            version="2.1.0",  # Versioned
            input=task_text,
            output=engine,
            lom=self.lom,
            confidence=confidence
        )
        
        return SkillOutput(engine=engine, confidence=confidence)
    
    def on_feedback(self, feedback: FeedbackEvent):
        # 4. LEARNING: Update config
        if feedback.signal == "correct":
            self.model.update_weights(positive_delta)
        optimizer.record_step(
            tenant_id=self.tenant_id,
            skill_id="os.delegation_router",
            weights_before=self.model.weights.copy(),
            weights_after=self.model.weights,
            confidence_before=self.last_confidence,
            confidence_after=new_confidence
        )
```

---

## Skill Contracts (Load-Bearing)

### 1. **Audit-First**
Every `Skill.execute()` must create an audit event BEFORE any state mutation. No silent operations.

### 2. **Immutable Manifest**
`plugin.json` defines:
- `required_checks` — Must pass before Skill runs (audit, consent, house-rules)
- `dependencies` — Other Skills this one depends on (DAG validated)
- `version` — Semver for rollback safety

### 3. **Fail-Closed Gates**
Security Skills (consent, house-rules) default to DENY. Errors → deny, never grant.

### 4. **Learning Isolation**
Skill learns only from feedback on its own decisions. No cross-Skill weight sharing (prevents divergence).

### 5. **Versioning**
Config evolves (weights, thresholds), but logic never breaks. v2.1.0 can read v2.0.x config.

---

## Integration with Learning Loops

Each Skill feeds into the **6D unified loss vector** (ADR-0614):

```
L_routing = accuracy(os.delegation_router)  ← Skill provides decisions
L_context = quality(os.context_adapter)      ← Skill provides preservations
L_exec = latency(L22_workflow)                ← Orchestrator measures SLA
L_conf = convergence(optimizer)               ← All 4 above measured together
L_comply = violations(L34_flow_guard)         ← Compliance Skill detects breaches
L_learn = optimizer_divergence_check()        ← Meta-check on learning itself
```

Backpropagation flows BACKWARD:
```
Optimizer: ∇L_total = w₁·∇L₁ + w₂·∇L₂ + ... + w₆·∇L₆
           ↓
Updates: w₁' = w₁ - η·∇w₁, etc. (gradient descent)
           ↓
Next Invocation: Skills load updated weights
```

---

## Deployment Workflow (Phase 1 Example)

### Day 1: Deploy Skill v2.1.0
```yaml
skill_id: "os.delegation_router"
version: "2.1.0"
boot_layer: "core"
dependencies: []
required_checks: ["audit", "consent", "house-rules"]
config:
  router_weights: [0.34, 0.22, 0.44]  # Claude / Opus / Hermes
  lvm_uncertainty_threshold: 0.70
```

### Day 2–7: Monitor Learning
- **Dashboard:** Shows confidence trend (0.90 → 0.93 → 0.95)
- **Divergence alert:** If variance spikes > threshold, pause learning + alert

### Day 8: Operator Tunes Weights
```bash
corvin skill config os.delegation_router \
  --set router_weights="[0.30, 0.25, 0.45]" \
  --reason="Opus cheaper this week, prefer it slightly"
```
- Change audited immediately (skill_config_updated event)
- Next routing decision uses new weights

### Day 9+: Rollback if Needed
```bash
corvin skill rollback os.delegation_router --to=2.0.5
```
- Instant: Version 2.0.5 code + old weights deployed
- Audit: skill_rolled_back event (reason, timestamp)

---

## Why This Matters

### Operator Control
AI behavior is no longer hardcoded. Operator tunes routing thresholds, context preservation weights, workflow priorities — without touching code.

### Self-Optimization
Skills learn from feedback. Routing confidence improves over time. Operator sees the trend in real-time.

### Provenance
"Why did the system route to Opus for this task?" → `corvin audit trace skill os.delegation_router --task_id=xyz` → Full proof (config at time, decision logic, feedback history).

### Safety
Compliance Skills (consent, house-rules) are **Meta-Skills** (monolithic, non-versioned, locked). Product Skills can never weaken them.

---

## ADR References

- **ADR-0532:** Phase 1–5 roadmap (8–12 weeks, ~2600 LoC)
- **ADR-0533:** Skill manifest schema (plugin.json)
- **ADR-0534:** Feedback integration (outcome sink, learning loop)
- **ADR-0535:** Skill composition + DAG validation

---

**Next:** [Learning Infrastructure: 6D Loss Vector](07_LEARNING_INFRASTRUCTURE.md)

**See Also:** [DIAGRAM_01: ACP Vision (High-Level)](../outputs/DIAGRAM_01_ACP_VISION_HIGH_LEVEL.svg)
