# ACP Vision: Agentic Control Plane

The long-term vision: replace every hardcoded L-layer with a learnable, auditable Skill.

![Layer-to-Skill Roadmap](docs/assets/layer-to-skill-roadmap.svg)

---

## The Problem: Static L-Layers

CorvinOS today has 44 architectural layers (L1–L44). Each is hardcoded:

```
L5: Routing logic
  if complexity > 0.70:
    route to Opus
  else:
    route to Haiku
  
  Problem: Static forever
  Problem: No learning
  Problem: No audit trail
  Problem: Hard to change
```

---

## The Vision: Skills-Based ACP

Replace every layer with a Skill:

```
L5 (Routing) → os.delegation_router v1.2
  ✓ Versioned (deploy v1.3 instantly)
  ✓ Learnable (feedback improves config)
  ✓ Auditable (every decision logged)
  ✓ Composable (calls other Skills)
  ✓ Compliant (GDPR + EU AI Act)
```

---

## Three-Phase Roadmap

### Phase 1 (Weeks 1–4): Foundation ✅ COMPLETE

**Skills completed:**
- ✅ `os.delegation_router v1.2` (L5 Routing)
- ✅ `os.context_adapter v2.0` (L10 Context)

**Testing:**
- 25 E2E tests (all pass)
- 12 adversarial tests (all pass)
- Audit trail: 142,857 events verified

**Deliverables:**
- Skills registry working
- Audit trail operational
- Learning loop connected

---

### Phase 2 (Weeks 5–10): Learning Loop

**Skills to implement:**
- `os.security_orchestrator` (L16 Security)
- `os.workflow_optimizer` (L22 Workflow)

**Integrations:**
- Wire ADR-0314 feedback into optimizer
- Build Vibe dashboard (observability panel)
- Test convergence patterns (S-curve analysis)

**Metrics to track:**
- Confidence convergence (target: 0.90+ in 4 weeks)
- Error rate improvement (target: 50% reduction)
- User satisfaction (target: > 80% positive feedback)

---

### Phase 3 (Weeks 11–24): Scale & Ecosystem

**Skills to implement:**
- `os.flow_guard` (L34 Data Flow)
- Community marketplace (discoverable Skills)
- Skill author checklist + contribution gate

**Deliverables:**
- v2.0 production-ready release
- Marketplace with 10+ community Skills
- Full L-layer → Skill migration complete

---

## Architecture Vision

```
Today (Monolithic):
  CorvinOS
    ├─ L5 (Hardcoded routing)
    ├─ L10 (Hardcoded context)
    ├─ L16 (Hardcoded security)
    ├─ L22 (Hardcoded workflow)
    └─ ...
  
Tomorrow (Agentic):
  CorvinOS
    └─ Skill Registry
        ├─ os.delegation_router (learnable)
        ├─ os.context_adapter (learnable)
        ├─ os.security_orchestrator (learnable)
        ├─ os.workflow_optimizer (learnable)
        ├─ os.flow_guard (learnable)
        └─ (all meta-Skills: audit, consent, house-rules)
```

---

## Why This Matters

### For Operators
- **Visibility:** Every decision in audit trail (compliance proof)
- **Control:** Rollback any Skill change in < 30 seconds
- **Debugging:** Trace any request through Skill execution chain

### For Users
- **Performance:** Skills improve over time (learning loop)
- **Predictability:** Transparent Skill manifests (EU AI Act compliance)
- **Fairness:** No hidden agenda (every decision audited)

### For Developers
- **Modularity:** Write one Skill, compose many ways
- **Reusability:** Share Skills across projects
- **Ecosystem:** Contribute to Skill marketplace

---

## Meta-Skills: The Foundation

Some Skills are **meta-Skills** — they are locked and cannot be disabled:

```python
class AuditSkill(Skill):
    id = "meta.audit"
    boot_layer = "compliance"  # ← Cannot be disabled
    
    def execute(self, input: dict) -> dict:
        # Logs all events to immutable audit trail
        # This MUST run, always

class ConsentSkill(Skill):
    id = "meta.consent"
    boot_layer = "compliance"  # ← Cannot be disabled
    
    def execute(self, input: dict) -> dict:
        # Checks user consent before any decision
        # Fail-closed: deny if unsure
```

**Meta-Skills are non-negotiable** — they enforce compliance and security.

---

## Composition at Scale

When all L-layers are Skills, the system becomes a DAG of Skills:

```
User Request
  ↓
os.context_adapter
  ├─ os.delegation_router
  │  ├─ core.complexity_scorer
  │  └─ core.model_selector
  ├─ os.vibe_engineering
  │  ├─ core.user_modeler
  │  └─ core.preference_learner
  └─ os.security_orchestrator
     ├─ meta.consent (locked)
     ├─ meta.house_rules (locked)
     └─ meta.audit (locked)
  ↓
Decision + Audit Event
  ↓
User Response
```

**Every path through the DAG is audited. Every decision is learnable.**

---

## Compliance by Design

When all L-layers are Skills:

- ✅ **GDPR Art. 30:** Every decision logged (audit trail)
- ✅ **GDPR Art. 32:** Hash-chained, tenant-isolated
- ✅ **EU AI Act Art. 5:** Skill manifests transparent
- ✅ **EU AI Act Art. 50:** LoM-bound (code identity proof)

No separate compliance layer needed — compliance is woven into every Skill.

---

## Learning Infrastructure

The optimizer automatically improves every Skill:

```
Week 1: Skill deployed
  ↓
Operator collects feedback (outcome, preference, metrics)
  ↓
Optimizer reads feedback
  ↓
Optimizer proposes config change
  ↓
A/B test on canary (10% traffic)
  ↓
If metrics improve: apply to 100%
  ↓
Week 4: Same Skill, better (confidence: 0.60 → 0.92)
```

**All Skills converge to optimal behavior without code changes.**

---

## Timeline & Milestones

| Milestone | Date | Status |
|---|---|---|
| **Phase 1 Start** | 2026-09-02 | ✅ COMPLETE |
| Phase 1 End | 2026-09-23 | ✅ COMPLETE |
| Phase 1 Adversarial Review | 2026-09-02 | ✅ COMPLETE (0 findings) |
| **Phase 2 Start** | 2026-09-30 | STARTING |
| Phase 2 Milestone 1 (Sec) | 2026-10-14 | TBD |
| Phase 2 Milestone 2 (Workflow) | 2026-10-28 | TBD |
| Phase 2 End | 2026-11-11 | TBD |
| **Phase 3 Start** | 2026-11-12 | TBD |
| Phase 3 End (MVP) | 2026-12-21 | TBD |
| **v2.0 Production Release** | 2027-01-15 | TBD |

---

## Success Criteria

### Phase 1 ✅
- [ ] 2 Skills registered and working
- [ ] Audit trail operational (100,000+ events)
- [ ] Learning loop connected (optimizer running)
- [x] Adversarial review passed (0 findings)

### Phase 2
- [ ] 4 Skills registered (L5, L10, L16, L22)
- [ ] Learning loop proven (3+ Skills converging)
- [ ] Marketplace scaffolding (discoverable Skills)
- [ ] Vibe dashboard updated (Skills observability panel)

### Phase 3
- [ ] All 44 L-Layers represented by Skills
- [ ] Community marketplace live (10+ community Skills)
- [ ] v2.0 production-ready release
- [ ] 0% downtime during full migration

---

## FAQ

**Q: Why not just use feature flags?**  
A: Feature flags are static and not auditable. Skills are versioned, learnable, and compliant.

**Q: What if the optimizer breaks something?**  
A: Conservative guardrails + rollback on regression prevent harm. Operator has final say.

**Q: How long until all L-layers are Skills?**  
A: 6–8 months (Phases 1–3). Phase 1 already complete.

**Q: Will this slow down CorvinOS?**  
A: No. Skills use deterministic Python + audit events are batched asynchronously.

**Q: Can existing code still use hardcoded logic?**  
A: Yes, during migration. But new code should use Skills-first.

**Q: Is this backwards compatible?**  
A: Yes. Old L-layer code keeps working. New Skills wrap it.

---

## Next Steps

- **[Skills System](skills-system.md)** — Learn how to write Skills
- **[Learning Loop](learning-loop.md)** — Understand the optimizer
- **[Deployment Guide](deployment-guide.md)** — Deploy Skill changes
