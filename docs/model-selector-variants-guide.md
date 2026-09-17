# Model Selector Variants B, C, D — Implementation Guide

**Status:** ✅ Implementation Complete  
**Version:** 2.0.0  
**ADRs:** 0641, 0642, 0643, 0644, 0845 (decomposition), 0201 (quota fallback), 0314 (learning)

---

## Overview

Three model selection variants for Autonomous OS L5 (auto-routing) with increasing sophistication:

| Variant | Features | Use Case | ADR |
|---------|----------|----------|-----|
| **B** | Tenant-aware base classification | Simple classification, deterministic routing | 0641, 0642 |
| **C** | Budget-aware + quota fallback | Production with quota limits (ADR-0201) | 0201, 0643 |
| **D** | Learning-integrated + feedback | Continuous optimization via learning loop | 0314, 0377 |

---

## Architecture

### Variant B: Base Classification

**Characteristics:**
- Deterministic feature extraction (token count, complexity markers)
- Task complexity classification: simple/medium/complex
- Tenant-aware overrides from config
- No quota tracking, no learning

**Decision Flow:**
```
Task Input
  ↓
Extract Features (tokens, has_code, has_math, has_reasoning)
  ↓
Classify Complexity (simple/medium/complex)
  ↓
Check Tenant Overrides
  ↓
Select Provider (cost-optimized)
  ↓
Select Model (Haiku/Sonnet/Opus)
  ↓
ModelSelectionDecision (audit trail)
```

**Example:**
```python
from core.skills.os_skills.model_selector_variants import VariantBSelector

selector = VariantBSelector("my_tenant")
decision = selector.classify("Refactor database query", task_type="code_gen")

print(f"Model: {decision.recommended_model}")        # claude-sonnet-5
print(f"Provider: {decision.recommended_provider}")  # anthropic
print(f"Confidence: {decision.confidence}")          # 0.85
```

### Variant C: Budget-Aware with Quota Fallback (ADR-0201)

**Characteristics:**
- Extends Variant B with budget envelope tracking
- Budget ceiling enforcement (ADR-0201):
  - max_loops: 100
  - max_wall_time_seconds: 86400 (24h)
  - max_worker_turns: 5000
  - max_total_workers: 64
- Daily quota tracking per tenant (USD)
- Quota exhaustion → fallback to single Claude Code delegation
- Cost-aware model selection (low quota → cheaper model)

**Decision Flow:**
```
Task Input
  ↓
Check Daily Quota Reset (24h window)
  ↓
Evaluate Quota Status
  ├─ EXHAUSTED → Fallback to Sonnet (single turn)
  ├─ LOW (<25%) → Prefer Haiku (cost optimized)
  ├─ MEDIUM (25-50%) → Use Sonnet
  └─ HIGH (>50%) → Normal Opus/Sonnet/Haiku selection
  ↓
Create Budget Envelope at Ceiling (ADR-0201)
  ↓
ModelSelectionDecision with quota status
```

**Quota Fallback Strategy (ADR-0201):**

When quota exhausted:
1. Downgrade to single Claude Code direct delegation
2. Enforce L44 (Acceptable Use) fail-closed in fallback
3. Never re-open quota fan-out
4. Mark decision with `fallback_applied: true`

**Example:**
```python
from core.skills.os_skills.model_selector_variants import VariantCSelector

selector = VariantCSelector("my_tenant")

# Classification with budget awareness
decision = selector.classify_with_budget("Complex task", cost_limit_usd=5.0)

print(f"Quota Remaining: {selector.current_quota.remaining_budget_percent()}%")
print(f"Budget Envelope: {decision.budget_envelope.max_loops} loops max")
print(f"Model: {decision.recommended_model}")

# Record task cost
selector.deduct_from_quota(2.50)

# Check quota status
quota = selector.get_current_quota()
print(f"Quota Status: {quota.remaining}/${quota.limit} USD")
```

### Variant D: Learning-Integrated Selection

**Characteristics:**
- Extends Variant C with learning loop integration
- Per-model, per-task-type success rate tracking
- Outcome feedback recording (success/failure/cost)
- Bayesian online learning (running average updates)
- Decomposition hints for complex low-confidence tasks
- Cost variance tracking for threshold adaptation

**Decision Flow:**
```
Task Input
  ↓
Get Variant C Decision (with budget)
  ↓
Load Learned Success Rates (model + task_type)
  ↓
Adjust Confidence by Success Rate
  ├─ Complex + low success → Generate Decomposition Hint
  └─ Create Enhanced Decision with Learning Data
  ↓
Return (ModelSelectionDecision, optional_hint)

[Later] Task Outcome Arrives:
  ↓
Record Feedback (model, task_type, success, cost)
  ↓
Update Success Rate (Bayesian: 0.9*old + 0.1*new)
  ↓
Track Cost Variance
  ↓
Persist Learning Data → Next classification uses updated rates
```

**Learned Success Rates (Example):**
```python
{
  "claude-haiku-4-5-20251001": {
    "code_review": 0.96,
    "code_gen": 0.90,
    "analysis": 0.90,
    "documentation": 0.97,
    "default": 0.88
  },
  "claude-sonnet-5": {
    "code_review": 0.99,
    "code_gen": 0.98,
    "analysis": 0.98,
    "default": 0.95
  },
  "claude-opus-5": {
    "code_review": 1.0,
    "code_gen": 0.99,
    "default": 0.98
  }
}
```

**Example:**
```python
from core.skills.os_skills.model_selector_variants import VariantDSelector

selector = VariantDSelector("my_tenant")

# Classification with learning feedback
decision, hint = selector.classify_with_learning(
    "Design microservices architecture",
    task_type="system_design"
)

print(f"Model: {decision.recommended_model}")
print(f"Confidence (with learning): {decision.confidence}")
if hint:
    print(f"Hint: {hint}")

# After task completes, record outcome
selector.record_outcome_feedback(
    model=decision.recommended_model,
    task_type="system_design",
    success=True,
    cost_usd=3.75
)

# Success rates updated for next classification
print(f"Updated rate: {selector.success_rates['claude-opus-5']['system_design']}")
```

---

## Skill Integration

### Using ModelSelectorSkill

Unified interface for all three variants:

```python
from core.skills.os_skills.model_selector_skill_integration import ModelSelectorSkill
from core.skills.os_skills.model_selector_skill_integration import SkillExecutionMode

# Initialize skill (default: Variant C)
skill = ModelSelectorSkill(
    tenant_id="my_tenant",
    variant="variant_c",          # "b", "c", or "d"
    cost_limit_per_task=5.0,
    enable_audit=True,
    enable_learning=True
)

# Execute skill (L5 routing entry point)
decision = skill.execute(
    task_input="Classify and route this task",
    task_type="code_gen",
    mode=SkillExecutionMode.NORMAL
)

# Get recommended model/provider
model = decision.recommended_model           # "claude-sonnet-5"
provider = decision.recommended_provider     # "anthropic"

# Record task cost (Variant C/D only)
if skill.deduct_quota(2.50):
    print("Quota available")
else:
    print("Quota exhausted, fallback applied")

# Get current quota (Variant C/D only)
quota_info = skill.get_current_quota()
if quota_info:
    print(f"Remaining: {quota_info['remaining']}/{quota_info['limit']} USD")

# Record outcome feedback (Variant D only)
skill.record_outcome(
    model=decision.recommended_model,
    task_type="code_gen",
    success=True,
    cost_usd=2.75
)

# Get skill metadata
info = skill.get_skill_info()
print(f"Variant: {info['variant']}")
print(f"Features: {info['features']}")
```

### Skill Manifest Registration

```yaml
id: "os.model_selector"
version: "2.0.0"
tier: "core"
boot_layer: "core"              # Immutable, always active
variant: "variant_c"            # Configurable per tenant
dependencies: []
required_checks:
  - "l44_acceptable_use"        # Fail-closed
  - "l16_consent"               # User consent gate
config:
  tenant_id: "${TENANT_ID}"
  variant: "variant_c"
  fallback_to_delegate: true
  cost_limit_per_task: 5.0
```

### L5 Routing Integration

In `core/routing/l5_auturoute.py`:

```python
from core.skills.os_skills.model_selector_skill_integration import skill_execute_wrapper

# L5 entry point
async def route_task(task: TaskEnvelope) -> EngineAssignment:
    """Auto-route task using model selector skill."""
    
    # Variant-aware model selection
    model, provider = skill_execute_wrapper(
        tenant_id=task.tenant_id,
        task_input=task.prompt,
        variant="variant_c",           # Tenant can override
        task_type=task.classification
    )
    
    # Route to selected model
    return EngineAssignment(
        task_id=task.id,
        model=model,
        provider=provider,
        engine_type="claude"
    )
```

---

## Tenant Isolation

Each tenant gets separate storage for:

### Variant B (Configuration)
- `~/.corvin/tenants/<tenant_id>/global/model_selection_overrides.json`
- Operator-set model overrides per task type

### Variant C (Quota Tracking)
- `~/.corvin/tenants/<tenant_id>/global/quota_tracking.json`
- Daily quota (USD), last reset time, worker count

### Variant D (Learning Data)
- `~/.corvin/tenants/<tenant_id>/global/model_learning.json`
- Success rates per model + task type
- Cost variance history

**File Structure:**
```
~/.corvin/tenants/my_tenant/global/
├── model_selection_overrides.json   # Variant B: {models: {task_type: model}}
├── quota_tracking.json              # Variant C: {daily_quota_remaining, limit, last_reset}
└── model_learning.json              # Variant D: {success_rates, updated}
```

---

## Audit Trail (ADR-0644)

All decisions emit audit events:

```json
{
  "event_type": "skill_executed",
  "tenant_id": "my_tenant",
  "skill_id": "os.model_selector",
  "variant": "variant_c",
  "recommended_model": "claude-sonnet-5",
  "recommended_provider": "anthropic",
  "complexity": "medium",
  "confidence": 0.85,
  "reasoning": "Complexity: medium (confidence 0.85), provider: anthropic, model: claude-sonnet-5",
  "budget_envelope": {
    "max_loops": 100,
    "max_wall_time_seconds": 86400,
    "timeout_seconds": 86400,
    "max_worker_turns": 5000,
    "max_total_workers": 64,
    "max_depth": 4
  },
  "quota_status": "OK (75.5% remaining)",
  "fallback_applied": false,
  "timestamp": "2026-09-17T12:34:56.789Z",
  "lom": "assistant.ModelSelector::execute"
}
```

Learning feedback events (Variant D):
```json
{
  "event_type": "skill_feedback",
  "tenant_id": "my_tenant",
  "skill_id": "os.model_selector",
  "model": "claude-sonnet-5",
  "task_type": "code_gen",
  "success": true,
  "cost_usd": 2.75
}
```

---

## Configuration

### Per-Tenant Settings

Create `~/.corvin/tenants/<tenant_id>/global/model_selection_overrides.json`:

```json
{
  "models": {
    "code_review": "claude-opus-5",
    "code_gen": "claude-sonnet-5",
    "documentation": "claude-haiku-4-5-20251001",
    "default": "claude-sonnet-5"
  }
}
```

### Quota Defaults (Variant C)

- Daily limit: $50.00 USD
- Reset window: 24 hours
- Cost limit per task: $5.00 USD

Customize in `~/.corvin/tenants/<tenant_id>/global/quota_tracking.json`:

```json
{
  "tenant_id": "my_tenant",
  "daily_quota_remaining": 50.0,
  "daily_quota_limit": 100.0,
  "last_reset": "2026-09-17T00:00:00Z",
  "worker_count_current": 0
}
```

---

## Migration Guide

### From Variant A (Legacy) to Variant C

```python
# Before: Simple in-memory config
config = {"simple": "haiku", "medium": "sonnet", "complex": "opus"}

# After: Persistent, quota-aware, audited
from core.skills.os_skills.model_selector_variants import VariantCSelector

selector = VariantCSelector("my_tenant")
decision = selector.classify_with_budget(task)  # Budget-aware
```

### Adopting Variant D (Learning)

```python
# Initialize Variant D selector
selector = VariantDSelector("my_tenant")

# First classification uses defaults
decision, hint = selector.classify_with_learning(task, task_type="code_gen")

# After task completes, record outcome
selector.record_outcome_feedback(
    model=decision.recommended_model,
    task_type="code_gen",
    success=True,
    cost_usd=2.50
)

# Subsequent classifications improve with learned data
# Success rates automatically updated (0.9*old + 0.1*new Bayesian update)
```

---

## Testing

### Validation Script

```bash
python3 scripts/validate_model_selector_variants.py
```

Tests:
- ✅ Variant B: Simple/Medium/Complex classification
- ✅ Variant C: Budget ceiling, quota tracking, fallback
- ✅ Variant D: Learning initialization, outcome feedback, persistence
- ✅ Tenant isolation across all variants
- ✅ Skill integration interface
- ✅ Selector factory
- ✅ Budget envelope validation
- ✅ Quota tracking calculations

### Unit Tests

```bash
python3 -m pytest tests/e2e/test_model_selector_variants_bcd_e2e.py -v
```

---

## Troubleshooting

### Variant C: Quota Exhausted

**Symptom:** Model always falls back to Sonnet, quota showing 0%.

**Solution:**
1. Check quota reset window: `selector.current_quota.quota_reset_needed()`
2. If 24h passed, quota auto-resets on next classification
3. Manual reset: `selector.current_quota.last_reset = datetime.utcnow()`

### Variant D: Learning Not Updating

**Symptom:** Success rates not changing after outcome feedback.

**Checks:**
1. Confirm `enable_learning=True` in skill constructor
2. Verify `record_outcome_feedback()` is called with correct model name
3. Check learning data persistence: `~/.corvin/tenants/<tenant>/global/model_learning.json`

### Tenant Config Not Applied (Variant B)

**Symptom:** Overrides in `model_selection_overrides.json` ignored.

**Solution:**
1. Verify file exists: `~/.corvin/tenants/<tenant_id>/global/model_selection_overrides.json`
2. Check JSON validity: `jq . < model_selection_overrides.json`
3. Reload selector: `selector = VariantBSelector(tenant_id)`

---

## Performance

### Latency
- Variant B: ~2-5ms (feature extraction + classification)
- Variant C: ~5-10ms (+ quota lookup)
- Variant D: ~8-15ms (+ learning data load)

### Storage
- Variant B: ~1KB per tenant (config)
- Variant C: ~500B per tenant (quota tracking, resets daily)
- Variant D: ~5KB per tenant (success rates, cost history)

---

## References

- **ADR-0641:** Model Selection Routing Framework
- **ADR-0642:** Complexity Classification & Provider Selection
- **ADR-0643:** Timeout & Cost Limit Configuration
- **ADR-0644:** Audit Trail Compliance
- **ADR-0201:** Quota Fallback Strategy
- **ADR-0314:** Learning Infrastructure & Feedback Loop
- **ADR-0845:** Prompt-Level Task Decomposition (k=2)

---

## Next Steps

1. **Deploy Variant C** as default for production (budget-safe)
2. **Pilot Variant D** with learning team for optimization research
3. **Monitor quota reset cycles** to tune daily limit per tenant
4. **Collect learning feedback** for 2+ weeks to build success rate baseline
5. **Analyze cost variance** patterns for threshold adaptation
