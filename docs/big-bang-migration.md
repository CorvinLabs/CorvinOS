# Big Bang Migration: From Feature Flags to Skills

How we replaced 4,900 lines of feature flag code with Skills (ADR-0544).

![Feature Flags vs Skills](docs/assets/feature-flags-vs-skills.svg)

---

## The Problem: Feature Flags

Before Skills, CorvinOS used hardcoded feature flags:

```python
# config.json
{
  "routing_strategy": "opus",     # Set at deploy time only
  "context_ttl": 3600,            # Static
  "features": {
    "new_ui": false,              # Permanent feature gate
    "experimental_routing": false
  }
}

# app.py
if CONFIG.routing_strategy == "opus":
    engine = "claude-opus-5"
else:
    engine = "claude-haiku-4-5"

# Problems:
# ❌ Static (no optimization)
# ❌ Opaque (why this path?)
# ❌ Not auditable (no proof)
# ❌ Not versionized (can't rollback)
# ❌ Not learnable (no feedback loop)
# ❌ GDPR risk (no audit trail)
```

---

## The Solution: Skills

After migration, CorvinOS uses versioned Skills:

```python
class DelegationRouterSkill(Skill):
    id = "os.delegation_router"
    version = "1.2"
    config = {"complexity_threshold": 0.70}
    
    def execute(self, input: dict) -> dict:
        if input["complexity"] > self.config["complexity_threshold"]:
            return {"engine": "claude-opus-5"}
        else:
            return {"engine": "claude-haiku-4-5"}

# registry.execute("os.delegation_router", {"complexity": 10})
# Result: {"engine": "claude-opus-5"}

# Benefits:
# ✅ Dynamic (deploy instantly)
# ✅ Transparent (audit trail)
# ✅ Auditable (every decision logged)
# ✅ Versionized (instant rollback)
# ✅ Learnable (feedback improves config)
# ✅ GDPR compliant (hash-chained audit)
```

---

## Migration Strategy

### Phase 1: Identify Feature Flags

```bash
# Find all feature flags in codebase
grep -rE "CONFIG\.|FLAGS\.|feature_" core/ | wc -l
# Output: 4,900 lines

# Categorize by layer:
# L5 Routing: 340 lines → os.delegation_router Skill
# L10 Context: 280 lines → os.context_adapter Skill
# L16 Security: 520 lines → os.security_orchestrator Skill (TBD)
# ...
```

---

### Phase 2: Implement Skill (e.g., os.delegation_router)

```python
# BEFORE: Feature flag
if CONFIG.routing_strategy == "opus":
    engine = "claude-opus-5"
elif CONFIG.routing_strategy == "haiku":
    engine = "claude-haiku-4-5"

# AFTER: Skill
class DelegationRouterSkill(Skill):
    id = "os.delegation_router"
    version = "1.0"
    config = {
        "complexity_threshold": 0.70,
        "strategy": "deterministic"
    }
    
    def execute(self, input: dict) -> dict:
        threshold = self.config["complexity_threshold"]
        if input.get("complexity", 0) > threshold:
            return {"engine": "claude-opus-5"}
        else:
            return {"engine": "claude-haiku-4-5"}
```

---

### Phase 3: Replace Call Sites

```python
# BEFORE: Direct config access
if CONFIG.routing_strategy == "opus":
    engine = "claude-opus-5"
else:
    engine = "claude-haiku-4-5"

# AFTER: Skill execution
result = registry.execute("os.delegation_router", {"complexity": input_complexity})
engine = result["engine"]
```

**Tool-assisted rewrite:** Script to find all call sites and replace.

---

### Phase 4: Testing & Validation

```bash
# E2E test: Skill is called
pytest tests/test_delegation_router_e2e.py -v

# Audit verification: Events are logged
corvin audit verify-chain --tenant=_default

# Behavioral equivalence: Output matches old system
pytest tests/test_behavioral_equivalence.py -v

# All tests must pass before merging
```

---

## Timeline & Scope

```
Phase 1 (Weeks 1-4): Foundation
  ├─ os.delegation_router (L5, 340 LOC)
  └─ os.context_adapter (L10, 280 LOC)
  → 4,900 LOC left

Phase 2 (Weeks 5-10): Learning Loop
  ├─ os.security_orchestrator (L16, 520 LOC)
  └─ os.workflow_optimizer (L22, 450 LOC)
  → 3,950 LOC left

Phase 3 (Weeks 11-24): Scale
  ├─ os.flow_guard (L34, 380 LOC)
  └─ Remaining L-layers (L1-L4, L6-L9, L11-L15, ...)
  → 0 LOC left (complete migration)
```

---

## Code Examples

### Old System (Feature Flags)

```python
# config.yaml
routing:
  strategy: opus
  threshold: 0.70

# app.py
def route_request(input):
    if config.routing.strategy == "opus":
        if input.complexity > config.routing.threshold:
            return "opus"
    return "haiku"

# Limitations:
# - Hard to change (redeploy)
# - No versioning (can't rollback)
# - Not auditable (no proof)
# - Not learnable (static forever)
```

### New System (Skills)

```python
# registry.execute() call
def route_request(input):
    result = skill_registry.execute(
        "os.delegation_router",
        {"complexity": input.complexity}
    )
    return result["engine"]

# Advantages:
# - Easy to change (deploy new version)
# - Versionized (instant rollback)
# - Auditable (events logged)
# - Learnable (optimizer improves config)
```

---

## Verification Checklist

For each migrated feature flag → Skill:

- [ ] Skill registered in registry
- [ ] Skill.execute() produces same output as old code (unit test)
- [ ] E2E test: Skill is called end-to-end (no test imports directly)
- [ ] Audit trail: SKILL_EXECUTED event logged (verify with corvin audit)
- [ ] All call sites replaced (grep for old flag name)
- [ ] Rollback tested (deploy v1.0 → v1.1 → rollback to v1.0)
- [ ] Configuration matches (old config → new Skill config)
- [ ] Dependencies declared (list all Skills this Skill calls)
- [ ] Compliance check passed (GDPR + EU AI Act)
- [ ] Merged to main with ADR reference

---

## Real-World Migration (Example)

### Step 1: Identify Flags

```bash
grep -r "CONFIG.routing" core/routing/
# Found:
# core/routing/router.py:42 - routing strategy
# core/routing/router.py:87 - complexity threshold
# core/routing/cache.py:120 - caching logic
```

### Step 2: Write Skill

```python
# core/skills/os_delegation_router.py
class DelegationRouterSkill(Skill):
    id = "os.delegation_router"
    version = "1.0"
    config = {"complexity_threshold": 0.70}
    
    def execute(self, input: dict) -> dict:
        # Replaces core/routing/router.py lines 40-95
        threshold = self.config["complexity_threshold"]
        return {
            "engine": "opus" if input["complexity"] > threshold else "haiku"
        }

registry.register(DelegationRouterSkill())
```

### Step 3: Replace Call Sites

```python
# BEFORE (core/routing/router.py:42)
def route_task(input):
    if CONFIG.routing.strategy == "opus":
        if input.complexity > CONFIG.routing.threshold:
            return "opus"
    return "haiku"

# AFTER (core/routing/router.py:42)
def route_task(input):
    result = skill_registry.execute("os.delegation_router", input)
    return result["engine"]
```

### Step 4: Test

```bash
pytest tests/test_routing_e2e.py::test_delegation_router_called
# Output: PASSED

corvin audit show-task <task_id>
# Verifies: SKILL_EXECUTED event for os.delegation_router
```

### Step 5: Commit

```bash
git add core/skills/os_delegation_router.py
git add core/routing/router.py
git commit -m "feat: Migrate routing L-layer to os.delegation_router Skill

Replaces 340 LOC of feature flags with versioned Skill.
- DelegationRouterSkill implements routing logic (core/routing/router.py)
- All call sites updated to use skill_registry.execute()
- E2E tested: skill is called end-to-end
- Audit verified: events logged correctly
- Behavioral equivalence confirmed

ADR-0544 documents the strategy."
```

---

## Success Metrics

### Phase 1 Complete ✅

- [x] 620 LOC migrated (os.delegation_router + os.context_adapter)
- [x] 0 regressions (all E2E tests pass)
- [x] 0 audit chain violations (daily verification passes)
- [x] Adversarial review: 0 findings

### Phase 2 (In Progress)

- [ ] 1,450 LOC migrated (target: Nov 11)
- [ ] Learning loop proven (3+ Skills converging)
- [ ] < 1% latency regression

### Phase 3 (Planned)

- [ ] 4,900 LOC migrated (target: Dec 21)
- [ ] v2.0 production release (target: Jan 15)
- [ ] 0% downtime during migration

---

## FAQ

**Q: Do we need to rewrite all 4,900 lines at once?**  
A: No. Migrate L-layer by L-layer (Phase 1–3, ~8 weeks).

**Q: What if a feature flag is used in multiple places?**  
A: One Skill replaces all uses. Register once, use everywhere.

**Q: Can we mix Skills and feature flags?**  
A: Yes, during migration. But eventually phase out flags (hard deadline: v2.0).

**Q: Will this break existing code?**  
A: No. Skill API is backwards-compatible. Feature flags still work until migration.

**Q: How do we handle configuration?**  
A: Old config → Skill config. Operator can tune via Optimizer or manual override.

---

## Next Steps

- **[Skills System](skills-system.md)** — Learn to write Skills
- **[ACP Vision](acp-vision.md)** — See the full L-layer migration roadmap
- **[Deployment Guide](deployment-guide.md)** — Deploy Skill changes
