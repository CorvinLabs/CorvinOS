# Engine Control is Core (Tier-1)
## Model Selection, Provider Routing, Engine-to-Request Binding

**Date:** 2026-07-26  
**Decision:** Engine control (L22 engine layer, ADR-0181 provider model) belongs in Tier-1 Core.

---

## What is Engine Control?

**Core decisions made per request:**
1. Which engine does this go to? (Claude? Hermes? Custom?)
2. Which model in that engine? (Haiku, Sonnet, Opus?)
3. What routing policy applies? (TDE? Native? ACS?)
4. What cost/token budget? (Per-user? Per-tenant? Per-request?)

**Current code:**
- `core/engine_layer.py` — Engine registry + model selection
- `core/delegation/provider_model.py` — ADR-0181 (provider + model split)
- `core/delegation/routing.py` — Route by policy

---

## Why Tier-1 Core (Not Plugin)

### Problem: If Engine Control Were Plugin-able
```python
# User disables engine-control plugin
# → system doesn't know which engine to use
# → every request fails
# → fork gets it for free
# → can't charge for "smart model selection"
```

### Solution: Engine Control is Tier-1
- **Required:** Every request needs engine selection
- **Strategic:** Hermes vs Claude selection, cost optimization, quality vs speed tradeoffs
- **IP:** Custom provider routing, per-user model selection
- **Not replaceable:** But extensible via hooks

---

## Engine Control Architecture

### Core (Tier-1, ~800 LOC)
```python
# core/engine_layer.py

class EngineRegistry:
    """Central engine registry (hardcoded, required)."""
    
    def __init__(self):
        self.engines = {
            "native": ClaudeEngine(),         # Tier-1, default
            "hermes": HermesEngine(),         # Tier-1, fallback
            "tde": TDERouter(),               # Tier-1, smart
            "acs": ACSManager(),              # Tier-1, parallel
        }
    
    def get_engine_for_request(
        self,
        request: Request,
        policy: RoutingPolicy = None
    ) -> Engine:
        """
        Core logic: given request + policy, select engine.
        
        Called by: every request path
        Never bypassed: routing logic always runs
        """
        # Apply routing policy (default: native → TDE → ACS)
        policy = policy or self.default_policy
        
        engine = policy.select(request, self.engines)
        
        return engine or self.engines["native"]  # Fallback


class ProviderModelRegistry:
    """Map (provider, model) → executable (ADR-0181)."""
    
    def __init__(self):
        self.bindings = {
            ("claude", "haiku"): ClaudeHaikuExecutor(),
            ("claude", "sonnet"): ClaudeSonnetExecutor(),
            ("claude", "opus"): ClaudeOpusExecutor(),
            ("hermes", "base"): HermesBaseExecutor(),
        }
    
    def get_executor(self, provider: str, model: str) -> Executor:
        """Execute request on specific provider + model."""
        key = (provider, model)
        if key not in self.bindings:
            raise ValueError(f"Unknown provider/model: {key}")
        return self.bindings[key]


class RoutingPolicy:
    """Policy for selecting engine per request (ADR-0181, L22)."""
    
    def __init__(self):
        self.hook_manager = HookManager()
        self._register_default_hooks()
    
    def _register_default_hooks(self):
        """Built-in routing (immutable)."""
        self.hook_manager.register(
            "model_selection",
            self._select_model_default
        )
    
    def select(
        self,
        request: Request,
        engines: dict[str, Engine]
    ) -> Engine:
        """
        Select engine for this request.
        
        Default policy:
          1. Check budget + tokens → select model
          2. Call model_selection hook (extensible)
          3. Route to appropriate engine
          4. Fallback to native if unavailable
        """
        # Budget-aware model selection (immutable core logic)
        cost_estimate = self._estimate_cost(request)
        tokens_available = request.user_quota.remaining_tokens
        
        if cost_estimate.tokens > tokens_available:
            model = "haiku"  # Cheap model
        else:
            model = "sonnet"  # Default
        
        # Hook: custom model selection (extensible)
        override_model = self.hook_manager.call(
            "model_selection",
            request=request,
            default_model=model
        )
        if override_model:
            model = override_model
        
        # Route to engine
        if self._is_tde_eligible(request):
            return engines["tde"]
        elif self._is_acs_eligible(request):
            return engines["acs"]
        else:
            return engines["native"]
    
    def _is_tde_eligible(self, request: Request) -> bool:
        """Should this request use TDE (smart routing)?"""
        return (
            request.model != "opus"  # Opus always native
            and not request.is_time_sensitive
            and request.user_tier >= "professional"  # Not free
        )
    
    def _is_acs_eligible(self, request: Request) -> bool:
        """Should this request use ACS (parallel workers)?"""
        return (
            request.is_big_data_shaped
            and request.user_tier >= "enterprise"
        )
    
    def register_hook(self, hook_name: str, handler: Callable) -> Result:
        """
        Extensible hook: custom model selection.
        
        Default: budget-aware + tier-aware
        Can override: user-based, geo-based, cost-based, SLA-based
        """
        ALLOWED_HOOKS = {
            "model_selection",      # Override model choice
            "engine_selection",     # Override engine choice (after model)
        }
        
        if hook_name not in ALLOWED_HOOKS:
            return Result(ok=False, message=f"Hook {hook_name} not allowed")
        
        self.hook_manager.register(hook_name, handler)
        return Result(ok=True, message=f"Hook {hook_name} registered")
```

---

## Extension Points (Tier-1)

### Hook: Custom Model Selection
```python
policy.register_hook(
    "model_selection",
    my_model_selector_fn
)

def my_model_selector_fn(request: Request, default_model: str) -> str:
    """
    Override model selection per request.
    
    Default: budget-aware (Opus expensive, Haiku cheap)
    Custom: quality-aware (always Opus for creative, Haiku for simple)
    """
    if request.is_creative_task:
        return "opus"  # Quality over cost
    
    if request.is_brainstorm:
        return "sonnet"  # Balance
    
    return "haiku"  # Default to cheap
```

### Hook: Custom Engine Selection
```python
policy.register_hook(
    "engine_selection",
    my_engine_selector_fn
)

def my_engine_selector_fn(request: Request, engines: dict) -> Engine:
    """
    Override engine selection after model choice.
    
    Default: TDE for non-opus, ACS for big-data
    Custom: geo-aware (EU users → EU engine), SLA-aware, etc.
    """
    if request.user_region == "eu" and "eu-engine" in engines:
        return engines["eu-engine"]
    
    if request.requires_low_latency:
        return engines["native"]  # Direct, no routing overhead
    
    return engines["tde"]  # Default smart routing
```

---

## What's NOT Extensible in Engine Control

```python
# ❌ Can't replace engine registry
registry.replace_engine("native", MyCustomEngine)
# → No such method (engines are hardcoded by tiers)

# ❌ Can't disable model selection (routing logic)
policy.disable_model_selection()
# → No such method (would break every request)

# ❌ Can't bypass routing policy
request.force_engine = "hermes"
execute(request)
# → Ignored; RoutingPolicy.select() always runs

# ✅ CAN extend with hooks
policy.register_hook("model_selection", my_fn)
policy.register_hook("engine_selection", my_fn)
```

---

## Admin Control: Engine Policy

### What Admin Can Do
```bash
# View current routing policy
corvinctl engine policy get
# Returns:
# {
#   "default_model": "sonnet",
#   "tde_eligible": {"model != opus", "not time_sensitive", "user_tier >= professional"},
#   "acs_eligible": {"is_big_data_shaped", "user_tier >= enterprise"},
#   "fallback": "native"
# }

# Update routing policy
corvinctl engine policy set --default-model opus
# Now all requests start with Opus (expensive, high quality)

# Register custom model selector
corvinctl engine hook register model_selection my-plugin/1.0.0

# View active hooks
corvinctl engine hooks list
```

### What Admin CANNOT Do
```bash
# ❌ Disable engine selection entirely
corvinctl engine disable selection
# Error: Cannot disable engine control (core infrastructure)

# ❌ Bypass routing policy for a user
corvinctl engine force-direct-mode alice@company.com
# Error: RoutingPolicy is immutable

# ❌ Remove native/hermes engines
corvinctl engine uninstall native
# Error: Cannot remove tier-1 engines
```

---

## Admin Dashboard: Engine Control Tab

```
ENGINE CONTROL & ROUTING

Status: ✅ All engines healthy

┌─ Default Routing Policy
│  ├─ Model Selection: budget-aware (Opus if budget allows, else Sonnet/Haiku)
│  ├─ TDE Eligible: (model != opus) AND (not time_sensitive) AND (tier >= pro)
│  ├─ ACS Eligible: (is_big_data) AND (tier >= enterprise)
│  └─ Fallback: native
│
├─ Active Hooks
│  ├─ model_selection: custom-routing/1.0.0 (custom cost model)
│  └─ engine_selection: geo-routing/1.0.0 (region-aware routing)
│
└─ Engines
   ├─ native (Claude) ✅ healthy, 100% available
   ├─ hermes (Fallback) ✅ healthy, 95% available
   ├─ tde (Smart Routing) ✅ healthy, 1,234 delegations/min
   └─ acs (Parallel) ✅ healthy, 50 active workers

[Edit Policy] [Register Hook] [View Metrics]
```

---

## Why Engine Control is Tier-1

| Aspect | Why Core |
|--------|----------|
| **Load-bearing** | Every request needs engine selection → core path |
| **Strategic IP** | Model selection algorithms = competitive advantage |
| **Not replaceable** | Fork gets basic version (can't monetize model selection) |
| **Extensible** | But admin/plugins can customize via hooks |
| **Compliance-free** | No GDPR requirement, but essential infrastructure |

---

## Updated Tier-1 Components

```
Tier 1: Core Infrastructure (6-7 KB total)

Execution:
  ├─ Engine Registry + ProviderModelRegistry (800 LOC)
  ├─ Routing Policy + hook system (600 LOC)
  ├─ Model selection logic (400 LOC)

Orchestration:
  ├─ A2A Orchestration (600 LOC)
  ├─ TDE Routing (600 LOC)
  ├─ ACS Manager (800 LOC)
  ├─ Compute Worker (700 LOC)
  ├─ Delegation Router (500 LOC)
  ├─ Workflow Engine (600 LOC)

Data:
  ├─ Conversation Recall (600 LOC)

Admin:
  └─ Control Plane (500 LOC)
```

---

## Summary

**Engine Control (L22, ADR-0181) is Tier-1 Core because:**
- Every request goes through it (core path)
- It's strategic IP (model selection, cost optimization)
- It's not replaceable (forks expect this)
- But it's extensible via hooks (admin can customize)

**Admin can:**
- View + update routing policy
- Register custom model/engine selectors
- See engine health + metrics

**Admin cannot:**
- Disable engine selection (would break routing)
- Replace engine registry (no fork logic)
- Bypass routing policy (always runs)

**Extensions:**
- `model_selection` hook: custom Haiku/Sonnet/Opus logic per request
- `engine_selection` hook: custom engine choice (geo, cost, SLA, etc.)

This completes the Tier-1 Core: **Compliance + Agentic Compute + Engine Control = 6-7 KB immutable, 50+ extensible.**

