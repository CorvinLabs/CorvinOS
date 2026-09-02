# The Agentic Control Plane (ACP): Replacing L-Layers with Skills

**The Vision:** Transform CorvinOS from a hardcoded system (36 L-Layers, static behavior) into a **self-learning agentic operating system** where every L-Layer becomes a versioned Skill that improves continuously through feedback.

---

## The Problem with L-Layers

### Old Model: Hardcoded L-Layers

```svg
<svg viewBox="0 0 800 500" xmlns="http://www.w3.org/2000/svg">
  <!-- Background -->
  <rect width="800" height="500" fill="#FEE2E2"/>
  
  <!-- Title -->
  <text x="400" y="30" font-size="20" font-weight="bold" text-anchor="middle" fill="#DC2626">
    ❌ Old Model: Hardcoded L-Layers (Static, No Learning)
  </text>
  
  <!-- Stack of layers -->
  <rect x="100" y="70" width="600" height="50" rx="4" fill="#FECACA" stroke="#DC2626" stroke-width="2"/>
  <text x="400" y="105" font-size="11" font-weight="bold" text-anchor="middle" fill="#7F1D1D">
    L44: House Rules (hardcoded yes/no)
  </text>
  
  <rect x="100" y="130" width="600" height="50" rx="4" fill="#FECACA" stroke="#DC2626" stroke-width="2"/>
  <text x="400" y="165" font-size="11" font-weight="bold" text-anchor="middle" fill="#7F1D1D">
    L5: Routing (hardcoded rules, no learning)
  </text>
  
  <rect x="100" y="190" width="600" height="50" rx="4" fill="#FECACA" stroke="#DC2626" stroke-width="2"/>
  <text x="400" y="225" font-size="11" font-weight="bold" text-anchor="middle" fill="#7F1D1D">
    L34: Data Flow Guard (hardcoded flow matrix)
  </text>
  
  <rect x="100" y="250" width="600" height="50" rx="4" fill="#FECACA" stroke="#DC2626" stroke-width="2"/>
  <text x="400" y="285" font-size="11" font-weight="bold" text-anchor="middle" fill="#7F1D1D">
    L22: Workflow (hardcoded state machine)
  </text>
  
  <!-- Problems -->
  <text x="50" y="350" font-size="12" fill="#7F1D1D" font-weight="bold">Problems:</text>
  <text x="50" y="375" font-size="11" fill="#7F1D1D">🚫 No versioning — change requires full system restart</text>
  <text x="50" y="395" font-size="11" fill="#7F1D1D">🚫 No learning — behavior never improves, always hand-tuned</text>
  <text x="50" y="415" font-size="11" fill="#7F1D1D">🚫 No composition — layers are tightly coupled</text>
  <text x="50" y="435" font-size="11" fill="#7F1D1D">🚫 No feedback loop — no way to measure or optimize</text>
  <text x="50" y="455" font-size="11" fill="#7F1D1D">🚫 Deployment risk — any change affects entire system</text>
</svg>
```

**Issues:**
- **No versioning:** Layer code is global; change one byte and the whole system needs redeployment
- **No learning:** Behavior is hardcoded; even with feedback, no automatic improvement
- **Tight coupling:** Layers depend on each other; hard to test independently
- **High deployment risk:** Any layer change is a full-system rollout
- **No observability:** Hard to trace which layer made which decision

### New Model: Skills-Based ACP

```svg
<svg viewBox="0 0 800 500" xmlns="http://www.w3.org/2000/svg">
  <!-- Background -->
  <rect width="800" height="500" fill="#DCFCE7"/>
  
  <!-- Title -->
  <text x="400" y="30" font-size="20" font-weight="bold" text-anchor="middle" fill="#065F46">
    ✅ New Model: Skills-Based ACP (Versioned, Self-Learning)
  </text>
  
  <!-- Skill boxes -->
  <rect x="100" y="70" width="600" height="50" rx="4" fill="#BBFCE3" stroke="#10B981" stroke-width="2"/>
  <text x="400" y="105" font-size="11" font-weight="bold" text-anchor="middle" fill="#065F46">
    os.house_rules_enforcer v1.2.3 — learns style preferences, converged to 94%
  </text>
  
  <rect x="100" y="130" width="600" height="50" rx="4" fill="#BBFCE3" stroke="#10B981" stroke-width="2"/>
  <text x="400" y="165" font-size="11" font-weight="bold" text-anchor="middle" fill="#065F46">
    os.delegation_router v2.0.1 — learns complexity threshold, converged to 91%
  </text>
  
  <rect x="100" y="190" width="600" height="50" rx="4" fill="#BBFCE3" stroke="#10B981" stroke-width="2"/>
  <text x="400" y="225" font-size="11" font-weight="bold" text-anchor="middle" fill="#065F46">
    os.flow_guard v1.0.0 — learns safe data shapes, converged to 96%
  </text>
  
  <rect x="100" y="250" width="600" height="50" rx="4" fill="#BBFCE3" stroke="#10B981" stroke-width="2"/>
  <text x="400" y="285" font-size="11" font-weight="bold" text-anchor="middle" fill="#065F46">
    os.workflow_optimizer v1.1.0 — learns execution patterns, converged to 88%
  </text>
  
  <!-- Benefits -->
  <text x="50" y="350" font-size="12" fill="#065F46" font-weight="bold">Benefits:</text>
  <text x="50" y="375" font-size="11" fill="#065F46">✅ Versioning — each Skill versioned independently (semver)</text>
  <text x="50" y="395" font-size="11" fill="#065F46">✅ Learning — weekly optimizer tunes parameters, confidence improves</text>
  <text x="50" y="415" font-size="11" fill="#065F46">✅ Composition — Skills call other Skills, decoupled interfaces</text>
  <text x="50" y="435" font-size="11" fill="#065F46">✅ Safe deployment — roll out Skill changes to 10% → 50% → 100%</text>
  <text x="50" y="455" font-size="11" fill="#065F46">✅ Observability — every Skill decision logged, auditable end-to-end</text>
</svg>
```

---

## L-Layer to Skill Transformation

### Mapping: Every L-Layer Becomes a Skill

| L-Layer | Purpose | Becomes Skill | Status |
|---|---|---|---|
| **L5** | Auto-routing | `os.delegation_router` | ✅ Phase 1 |
| **L10** | Context adaptation | `os.context_adapter` | ✅ Phase 1 |
| **L16** | Security hardening | `os.security_orchestrator` | 🚧 Phase 3 |
| **L22** | Workflow orchestration | `os.workflow_optimizer` | 🚧 Phase 2 |
| **L34** | Data flow guard | `os.flow_guard` | 🚧 Phase 3 |
| **L44** | House rules | `os.house_rules_enforcer` | ✅ Phase 1 |
| Meta-Skills | Audit, consent, disclosure | `meta.*` (immutable) | ✅ Always |

### Example: L5 (Routing) Becomes `os.delegation_router`

**Old (L-Layer, hardcoded):**
```python
# L5 routing logic — same for all users, same for all time
if task_complexity > 0.7:
    model = "opus"
else:
    model = "haiku"
```

**New (Skill, self-learning):**
```python
@Skill.register
class DelegationRouter(Skill):
    skill_id = "os.delegation_router"
    version = "2.0.1"
    
    def execute(self, request: dict) -> dict:
        # Read learned parameters
        config = self.get_config()  # e.g., {"threshold": 0.65}
        
        # Route based on current parameters
        complexity = estimate_complexity(request)
        model = "opus" if complexity > config["threshold"] else "haiku"
        
        return {"model": model, "confidence": 0.88}
    
    def get_feedback_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "correct": {"type": "boolean"},
                "actual_model": {"type": "string", "enum": ["haiku", "sonnet", "opus"]}
            }
        }
```

**Optimizer learns:**
- Week 1: 60% correct (threshold too high); lower it
- Week 2: 75% correct (still wrong 25%); lower more
- Week 3: 87% correct; fine-tune threshold
- Week 4: 92% correct; converged

---

## The Four Load-Bearing Boundaries

Skills are NOT just fancy L-Layers. Four hard constraints keep the system safe:

### 1. **Meta-Skills are Immutable, Non-Disableable**

Compliance mechanisms (audit, consent, disclosure, house rules) are **never versioned or disabled**. They run first and cannot be overridden:

```
┌─────────────────────────────────────────┐
│  Meta-Skills Layer (IMMUTABLE)          │
│  • Audit Chain (hash-chained events)    │
│  • Consent Gates (yes/no/maybe)         │
│  • Disclosure Card (AI-nature statement)│
│  • Boot Tripwire (chain integrity check)│
│  • House Rules (unambiguous denials)    │
│  ⚠️  Cannot be disabled, overridden, or │
│     versioned. FAIL-CLOSED.             │
└─────────────────────────────────────────┘
           ↓↓↓ ENFORCES
┌─────────────────────────────────────────┐
│  OS-Skills Layer (VERSIONED)            │
│  • Delegation Router (learns complexity)│
│  • Context Adapter (learns user style)  │
│  • Workflow Optimizer (learns chains)   │
│  • Flow Guard (learns safe shapes)      │
│  ✅ Versioned, learned, deployable      │
│     Can be rolled back instantly        │
└─────────────────────────────────────────┘
```

### 2. **Learning is a Trust Boundary**

The Learning Loop (feedback → optimization) is a separate trust boundary. **Buggy feedback cannot corrupt the Skill; it only affects confidence scoring.**

```
Skill Executes          ← Audit-logged (immutable)
         ↓
User Gives Feedback     ← May be wrong, may be malicious
         ↓
Learning Validates      ← Scrubbed, validated
         ↓
Optimizer Tunes Config  ← Gradually adjusts parameters
         ↓
Confidence Updates      ← Tracks improvement
```

If a user spams feedback incorrectly, the optimizer learns slowly and confidence stalls. The Skill still works; it just doesn't improve.

### 3. **Composition is a DAG (No Cycles)**

Skills call other Skills, but composition must be a **directed acyclic graph**. No cycles allowed:

✅ OK: A → B → C (linear chain)  
✅ OK: A → {B, C} (fan-out)  
❌ BAD: A → B → A (cycle)

The system **validates the DAG on boot** and rejects cyclic dependencies at registration time.

### 4. **Audit is Fail-Closed**

If the audit chain breaks (hash mismatch, missing event), the system **stops immediately**. No fallback, no "audit disabled" mode:

```
Boot tripwire runs:
  1. Connect to audit backend
  2. Fetch last 10 events
  3. Verify hash chain (N → N-1 → N-2 → ...)
  4. If ANY hash mismatch → STOP (exit 1)
  5. If audit unreachable → STOP (exit 1)
  6. Only then proceed to load OS-Skills
```

This ensures **every Skill decision is auditable from the start of the system's life**.

---

## Skill Categories

### OS-Skills (Built-in, Learned)

Core subsystems replacing L-Layers:

- **os.delegation_router** — Route requests to appropriate LLM (learns complexity threshold)
- **os.context_adapter** — Preserve context across turns (learns user style)
- **os.workflow_optimizer** — Chain Skills into workflows (learns execution order)
- **os.security_orchestrator** — Enforce security policies (learns attack patterns)
- **os.flow_guard** — Guard data flows (learns safe data shapes)

All OS-Skills are **versioned, audited, and self-learning**.

### Plugin-Skills (Community, Extensible)

User-contributed Skills for domain-specific logic:

- **memory-plugin** — Vector embeddings, semantic search
- **sql-expert** — SQL optimization
- **nlp-toolkit** — NLP analysis, sentiment

Plugin-Skills can call OS-Skills (dependency declared), but are **not trusted** to affect core behavior.

### User-Skills (Custom, Per-Tenant)

Tenant-specific Skills for organizational workflows:

- **acme.approval_router** — Route approvals to correct manager
- **acme.data_classifier** — Label data for retention

User-Skills are **isolated per tenant** (no cross-tenant access).

---

## The ACP Advantage

### 1. Versioning ✅

Skills are versioned independently. Roll out `os.delegation_router v2.0.1` without touching `os.context_adapter v1.0.0`.

**Deployment:** 10% → 50% → 100% over 7 days.

**Rollback:** Instant, per Skill.

### 2. Learning ✅

Every Skill improves weekly based on feedback. Confidence tracked in real-time.

```
Skill: os.delegation_router
Week 1: confidence = 60% (wrong 40% of the time)
Week 2: confidence = 72% (improved 12%)
Week 3: confidence = 85% (improved 13%)
Week 4: confidence = 92% (converged)
```

### 3. Composition ✅

Skills call other Skills. Dependencies are declared and verified.

```
Router depends on: classify, estimate_complexity, select_engine
                ↓
    Verify all three exist and are enabled
                ↓
    On execution, call them in order
                ↓
    If any fails, entire composition fails (fail-closed)
```

### 4. Observability ✅

Every decision is logged immutably. Audit any task end-to-end:

```bash
corvin audit show-task <task_id>
# Output: full chain of Skill decisions
# - os.delegation_router decided: route to opus
# - os.context_adapter decided: preserve last 10 messages
# - os.workflow_optimizer decided: chain [router, context, executor]
```

### 5. Safety ✅

Meta-Skills (audit, consent, house rules) cannot be disabled. Compliance is **structural, not optional**.

---

## Phase 1–3 Roadmap

### Phase 1 (Weeks 1–4): Foundation ✅ COMPLETE

Deploy first two OS-Skills:
- `os.delegation_router` (learns complexity threshold)
- `os.context_adapter` (learns context window preference)

E2E proof: Real requests flow through Skills → audit events logged → feedback collected.

Tests: 25 E2E + 12 adversarial.

### Phase 2 (Weeks 5–10): Learning Loop 🚧 IN PROGRESS

Wire ADR-0314 feedback into Skills:
- `os.workflow_optimizer` (learns execution chains)
- Dashboard showing confidence scores
- Automated parameter tuning (weekly)

Tests: 40 E2E + 18 adversarial.

### Phase 3 (Weeks 11–24): Scale & Ecosystem 📋 PLANNED

Deploy 2 more OS-Skills:
- `os.security_orchestrator` (learns attack patterns)
- `os.flow_guard` (learns safe data shapes)

Marketplace integration: Skills as discoverable, installable.

Tests: 60 E2E + 25 adversarial.

---

## FAQ

**Q: What about the 36 existing L-Layers? Do they all become Skills?**

A: No. Only subsystems that **benefit from versioning and learning** become Skills. Immutable compliance mechanisms (Meta-Skills) stay out of the Skills system entirely — they run first, enforce constraints, then OS-Skills execute within those constraints.

**Q: Can I disable a Skill?**

A: Yes, except Meta-Skills (audit, consent, disclosure, house rules). Those are immutable and non-disableable.

**Q: What if a Skill learns wrong (bad feedback)?**

A: The Learning boundary isolates buggy feedback. The Skill still works; it just doesn't improve (confidence stalls). You can investigate audit logs to find bad feedback and remove it.

**Q: How long does convergence take?**

A: Typically 2–4 weeks (100–300 feedback samples). Some Skills converge faster (simple decision), others slower (complex reasoning).

**Q: Is Skills 2.0 production-ready?**

A: Phase 1 (foundation) is complete. Phase 2 (learning loop) is in progress. Production use: mid-late September 2026.

---

## See Also

- **[Skills System](skills-system.md)** — What Skills are, how to write them
- **[Learning Loop](learning-loop.md)** — How feedback improves Skills
- **[Audit Trail](audit-trail.md)** — Immutable proof system
- **[Deployment Guide](deployment-guide.md)** — Rolling out Skill changes safely
- **[ADR-0532–0535](https://github.com/CorvinLabs/Corvin-ADR/decisions/)** — ACP Architecture decisions

---

**The ACP Vision: Replace hardcoded layers with versioned, self-learning Skills. Every system subsystem improves continuously. Every decision is auditable. Deployment becomes safe, fast, and reversible.**
