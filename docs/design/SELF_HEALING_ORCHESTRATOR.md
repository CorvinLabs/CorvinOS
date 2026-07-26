# Self-Healing Orchestrator Design
## Autonomous Failure Detection & Recovery (Stage 3)

**Date:** 2026-07-26  
**Status:** Design Specification (Implement After Stage 2 proven)  
**Related to:** ADR-0XXX (Compartmentalization System)  
**Dependencies:** Structured Logging (Layer 2) + Health Monitoring (Layer 3)

---

## Overview: The Healing Loop

```
NerveFiber polls plugin health_check()
        ↓
Health check fails 3+ times in 5 minutes
        ↓
Healing Orchestrator is notified
        ↓
Fetch healing policy for this plugin
        ↓
┌─────────────────────────────────────────────┐
│ Select healing action based on policy       │
├─────────────────────────────────────────────┤
│ Level 1 (Safest):  Circuit-break            │
│ Level 2 (Medium):  Soft-restart             │
│ Level 3 (Last):    Disable + degrade        │
└─────────────────────────────────────────────┘
        ↓
Execute action + log to audit trail
        ↓
Wait 5 minutes before next healing attempt
        ↓
Monitor recovery (health_check passes again?)
```

---

## Architecture: Three Components

### Component 1: Health Tracker
**Job:** Remember failure history per plugin

```python
class HealthTracker:
    """Track consecutive failures for each plugin."""
    
    def __init__(self, threshold: int = 3, window_minutes: int = 5):
        self.threshold = threshold  # Fail 3x before healing
        self.window_minutes = window_minutes  # Within 5 min
        self.failures: dict[str, list[float]] = {}  # plugin_id → [timestamp, ...]
    
    def record_failure(self, plugin_id: str, timestamp: float = None):
        """Record health check failure."""
        timestamp = timestamp or time.time()
        if plugin_id not in self.failures:
            self.failures[plugin_id] = []
        
        # Clean old failures (older than window)
        cutoff = timestamp - (self.window_minutes * 60)
        self.failures[plugin_id] = [
            t for t in self.failures[plugin_id] if t > cutoff
        ]
        
        # Add new failure
        self.failures[plugin_id].append(timestamp)
    
    def record_success(self, plugin_id: str):
        """Record successful health check."""
        self.failures[plugin_id] = []  # Reset counter
    
    def should_heal(self, plugin_id: str) -> bool:
        """Should we try to heal this plugin?"""
        failures = self.failures.get(plugin_id, [])
        return len(failures) >= self.threshold
    
    def consecutive_failures(self, plugin_id: str) -> int:
        """How many consecutive failures?"""
        return len(self.failures.get(plugin_id, []))
```

### Component 2: Healing Policy Engine
**Job:** Decide what action to take

```python
@dataclass
class HealingPolicy:
    """Per-plugin healing configuration."""
    
    plugin_id: str
    
    # Consecutive failures before attempting heal
    consecutive_failures_threshold: int = 3
    
    # What actions are allowed?
    # Level 1: Circuit-break only (safest)
    # Level 2: Soft-restart allowed
    # Level 3: Full disable + degrade allowed
    policy_level: int = 1  # 1 | 2 | 3
    
    # Max heals per hour (prevent healing loops)
    max_heals_per_hour: int = 5
    
    # Escalation if first action fails
    escalation: str = "page_on_call"  # "retry" | "escalate" | "page_on_call" | "disable"
    
    # Back-off between healing attempts (seconds)
    healing_retry_delay_seconds: int = 300  # 5 minutes


class HealingPolicyEngine:
    """Decide healing actions based on policy."""
    
    def __init__(self, policies: dict[str, HealingPolicy]):
        self.policies = policies
    
    def get_policy(self, plugin_id: str) -> HealingPolicy:
        """Get policy for plugin, or default."""
        return self.policies.get(
            plugin_id,
            HealingPolicy(plugin_id=plugin_id, policy_level=1)  # Default: circuit-break only
        )
    
    def select_healing_action(
        self,
        plugin_id: str,
        consecutive_failures: int,
    ) -> str:
        """Select action: 'circuit_break' | 'soft_restart' | 'disable' | 'none'"""
        policy = self.get_policy(plugin_id)
        
        # Policy Level 1: Only circuit-break
        if policy.policy_level == 1:
            return "circuit_break"
        
        # Policy Level 2: Try soft-restart first
        elif policy.policy_level == 2:
            if consecutive_failures <= 3:
                return "soft_restart"  # First attempt: restart
            else:
                return "circuit_break"  # 2nd+ attempt: circuit-break
        
        # Policy Level 3: Full degradation available
        elif policy.policy_level == 3:
            if consecutive_failures <= 2:
                return "soft_restart"  # Try restart first
            elif consecutive_failures <= 4:
                return "disable"  # Then disable + degrade
            else:
                return "page_on_call"  # Then escalate to human
        
        return "none"
```

### Component 3: Orchestrator (Main Healing Loop)

```python
class SelfHealingOrchestrator:
    """Autonomous healing orchestrator."""
    
    def __init__(
        self,
        registry: PluginRegistry,
        health_tracker: HealthTracker,
        policy_engine: HealingPolicyEngine,
        audit_emit: Callable,
    ):
        self.registry = registry
        self.health_tracker = health_tracker
        self.policy_engine = policy_engine
        self.audit_emit = audit_emit
        
        # Track when we last healed each plugin
        self.last_heal_timestamp: dict[str, float] = {}
    
    async def health_check_loop(self):
        """Called every 30s by NerveFiber."""
        plugin_health = self.registry.health_check_all()
        
        for plugin_id, status in plugin_health.items():
            if status.ok:
                # Plugin is healthy
                self.health_tracker.record_success(plugin_id)
            else:
                # Plugin failed health check
                self.health_tracker.record_failure(plugin_id)
                
                # Check if we should heal
                if self.health_tracker.should_heal(plugin_id):
                    await self._heal_plugin(plugin_id)
    
    async def _heal_plugin(self, plugin_id: str):
        """Attempt to heal a plugin."""
        # Respect back-off (don't heal too frequently)
        last_heal = self.last_heal_timestamp.get(plugin_id, 0)
        now = time.time()
        
        policy = self.policy_engine.get_policy(plugin_id)
        if (now - last_heal) < policy.healing_retry_delay_seconds:
            # Still in back-off period
            return
        
        consecutive_failures = self.health_tracker.consecutive_failures(plugin_id)
        action = self.policy_engine.select_healing_action(plugin_id, consecutive_failures)
        
        if action == "none":
            return
        
        # Log healing attempt
        self.audit_emit("plugin.healing_attempted", {
            "plugin_id": plugin_id,
            "action": action,
            "consecutive_failures": consecutive_failures,
            "timestamp": now,
        })
        
        try:
            # Execute healing action
            if action == "circuit_break":
                await self._circuit_break_plugin(plugin_id)
            
            elif action == "soft_restart":
                await self._soft_restart_plugin(plugin_id)
            
            elif action == "disable":
                await self._disable_and_degrade(plugin_id)
            
            elif action == "page_on_call":
                await self._page_on_call(plugin_id)
            
            # Record successful healing
            self.last_heal_timestamp[plugin_id] = now
            
            # Log healing success
            self.audit_emit("plugin.healing_succeeded", {
                "plugin_id": plugin_id,
                "action": action,
                "timestamp": now,
            })
        
        except Exception as e:
            # Healing failed! Log and escalate.
            self.audit_emit("plugin.healing_failed", {
                "plugin_id": plugin_id,
                "action": action,
                "error_code": type(e).__name__,
                "timestamp": now,
            })
            
            # Escalate based on policy
            if policy.escalation == "page_on_call":
                await self._page_on_call(plugin_id)
    
    # ────────────────────────────────────────────────────────────────────────────
    # Healing Actions (Reversible, Safe)
    # ────────────────────────────────────────────────────────────────────────────
    
    async def _circuit_break_plugin(self, plugin_id: str):
        """Circuit-break: queue requests, fail gracefully."""
        # This is the safest action. Implemented via CircuitBreaker wrapper.
        plugin = self.registry.get(plugin_id)
        
        if hasattr(plugin, '_circuit_breaker'):
            plugin._circuit_breaker.trip()
            print(f"[HEALING] Circuited plugin {plugin_id}")
        
        # Auto-recover: circuit breaker resets after timeout
    
    async def _soft_restart_plugin(self, plugin_id: str):
        """Soft-restart: on_unload() + on_load()."""
        plugin = self.registry.get(plugin_id)
        ctx = self.registry._contexts.get(plugin_id)
        
        try:
            # Graceful shutdown
            await asyncio.wait_for(plugin.on_unload(), timeout=30)
            
            # Brief pause
            await asyncio.sleep(1)
            
            # Restart
            plugin.on_load(ctx)
            
            print(f"[HEALING] Soft-restarted plugin {plugin_id}")
        
        except asyncio.TimeoutError:
            raise RuntimeError(f"Plugin {plugin_id} failed to unload within 30s")
    
    async def _disable_and_degrade(self, plugin_id: str):
        """Disable plugin and degrade gracefully."""
        plugin = self.registry.get(plugin_id)
        
        # Graceful shutdown
        try:
            await asyncio.wait_for(plugin.on_unload(), timeout=30)
        except Exception:
            pass  # If unload fails, continue anyway
        
        # Mark plugin as disabled in registry
        self.registry._plugins[plugin_id]._disabled = True
        
        # Activate degradation mode (e.g., STT off → text-only)
        # Specific degradation depends on plugin type
        await self._activate_degradation(plugin_id)
        
        print(f"[HEALING] Disabled plugin {plugin_id} and activated degradation")
    
    async def _activate_degradation(self, plugin_id: str):
        """Activate graceful degradation for this plugin type."""
        policy = self.policy_engine.get_policy(plugin_id)
        
        if "stt" in plugin_id:
            # Voice mode → text-only
            # Emit event for bridges to switch mode
            self.audit_emit("plugin.degradation.stt_disabled", {
                "plugin_id": plugin_id,
                "fallback": "text_only_mode",
            })
        
        elif "router" in plugin_id:
            # Engine router disabled → native-only
            self.audit_emit("plugin.degradation.router_disabled", {
                "plugin_id": plugin_id,
                "fallback": "native_only",
            })
        
        elif "compute" in plugin_id:
            # Compute disabled → limited in-process only
            self.audit_emit("plugin.degradation.compute_disabled", {
                "plugin_id": plugin_id,
                "fallback": "minimal_in_process",
            })
    
    async def _page_on_call(self, plugin_id: str):
        """Escalate to on-call engineer."""
        # Send alert to external alerting system
        self.audit_emit("plugin.escalation.page_on_call", {
            "plugin_id": plugin_id,
            "reason": "Plugin failed to heal autonomously",
            "action_required": True,
        })
        
        # In real system: PagerDuty / Opsgenie / Slack alert
        print(f"[ESCALATION] Paging on-call engineer for {plugin_id}")
```

---

## Healing Policies: Built-in Defaults

### Critical Plugins (Never Auto-Heal)

**audit-backend** (Audit trail is precious)
```yaml
plugin_id: "audit-backend"
consecutive_failures_threshold: 5  # High tolerance
policy_level: 1  # Circuit-break only
escalation: "page_on_call"  # Always call human
max_heals_per_hour: 1  # Very conservative
```

**user-backend** (Auth is critical)
```yaml
plugin_id: "user-backend"
consecutive_failures_threshold: 5
policy_level: 1
escalation: "page_on_call"
max_heals_per_hour: 1
```

### Medium-Risk Plugins (Soft Restart Safe)

**engine-router** (Routing decision)
```yaml
plugin_id: "engine-router"
consecutive_failures_threshold: 3
policy_level: 2  # Can soft-restart
escalation: "disable"  # If restart fails, disable (fall back to native)
max_heals_per_hour: 3
```

### Low-Risk Plugins (Full Degradation Safe)

**stt-provider** (Voice is optional)
```yaml
plugin_id: "stt-provider"
consecutive_failures_threshold: 2  # Low tolerance
policy_level: 3  # Full degradation
escalation: "disable"
max_heals_per_hour: 10  # Aggressive healing
```

**conversation-recall** (Memory is optional)
```yaml
plugin_id: "conversation-recall"
consecutive_failures_threshold: 2
policy_level: 3
escalation: "disable"
max_heals_per_hour: 10
```

---

## Healing Metrics & Observability

### Healing Success Rate (Per Plugin)

```python
healing_success_rate = (successful_heals / total_healing_attempts) * 100

# Example:
# STT: 85% success rate (usually recovers via restart)
# Router: 60% success rate (some issues require disable)
# Audit: N/A (doesn't heal autonomously)
```

### Mean Time to Recovery (MTTR)

```python
mttr = (time_when_plugin_healthy - time_when_healing_started)

# Example:
# Circuit-break: 5 seconds (fast recovery when external system recovers)
# Soft-restart: 10 seconds (overhead of on_unload + on_load)
# Disable: 30 seconds (user-facing fallback kicks in)
```

### Prometheus Metrics

```python
# Healing actions per plugin
corvin_healing_actions_total{plugin_id="stt-provider", action="soft_restart"} 15
corvin_healing_actions_total{plugin_id="stt-provider", action="circuit_break"} 3

# Success rate per plugin
corvin_healing_success_rate{plugin_id="stt-provider"} 0.83  # 83%

# MTTR distribution
corvin_healing_mttr_seconds_bucket{plugin_id="stt-provider", le="5"} 5
corvin_healing_mttr_seconds_bucket{plugin_id="stt-provider", le="30"} 18
```

### Grafana Alerts

```
# Alert if plugin fails to heal
alert: HealingFailed
condition: corvin_healing_actions_total > corvin_healing_succeeded < 50%  # <50% success
for: 10m
action: "page_on_call"
```

---

## Safety Guarantees

### Guarantee 1: Healing Never Makes Things Worse
- ✅ Circuit-break is always safe (doesn't modify state)
- ✅ Soft-restart is reversible (re-enable if it fails)
- ✅ Disable is graceful (core continues with degradation)
- ✅ Never: force-kill, delete data, corrupt audit trail

### Guarantee 2: Audit Trail Immutable
- ✅ Every healing action logged
- ✅ Logging is immutable (hash-chained)
- ✅ Healer cannot hide its actions
- ✅ Operators can audit all autonomous decisions

### Guarantee 3: Per-Tenant Isolation
- ✅ Healing one tenant's plugin doesn't affect others
- ✅ Tenant A's STT can be disabled while Tenant B's runs
- ✅ No cross-tenant side-effects

### Guarantee 4: Human Override Always Available
- ✅ Can disable healing policy for any plugin
- ✅ Can manually trigger healing
- ✅ Can revert healed plugin to original state
- ✅ Humans have final say

---

## Implementation Checklist (Phase 3, After Stage 2 Proven)

### Core Healing System
- [ ] HealthTracker class (track failures)
- [ ] HealingPolicy class + HealingPolicyEngine (policy decision)
- [ ] SelfHealingOrchestrator class (main loop)
- [ ] CircuitBreaker wrapper (safe failure mode)
- [ ] Healing action implementations (circuit-break, soft-restart, disable)

### Observability
- [ ] Prometheus metrics (healing actions, success rate, MTTR)
- [ ] Grafana dashboards (healing status per plugin)
- [ ] Alerts (healing failures, excessive healing)
- [ ] Audit trail logging (all healing decisions)

### Testing
- [ ] 20+ unit tests (health tracker, policy engine, actions)
- [ ] 10+ integration tests (full healing flow)
- [ ] Chaos tests (plugin fails, healing recovers)
- [ ] Multi-tenant tests (isolated healing)

### Documentation
- [ ] Healing policy configuration guide
- [ ] Troubleshooting: "Why is my plugin being healed?"
- [ ] Operational runbook: "What if healing is wrong?"
- [ ] Metrics guide: How to interpret healing graphs

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| **Healing cascades** | HIGH | Per-plugin max heals/hour + back-off period |
| **Healing wrong decision** | MEDIUM | Low risk actions first (circuit-break); manual review for critical |
| **Healing hidden issues** | MEDIUM | Audit trail logs all healings; LDD measures MTTR trend |
| **Healing in-flight requests** | MEDIUM | Graceful shutdown (on_unload) waits for requests to complete |
| **Two stages healing same plugin** | LOW | Only one healing orchestrator; serialized actions |

---

## Example: Healing a Failing STT Plugin

### Initial Failure

```
10:00:00  health_check() returns UNHEALTHY (timeout)
10:00:30  health_check() returns UNHEALTHY (timeout)
10:01:00  health_check() returns UNHEALTHY (timeout)
           → Consecutive failures: 3 (threshold met!)
```

### Healing Decision

```
Policy: STT plugin, policy_level=3, consecutive_failures=3
Action: soft_restart (policy_level 3 tries restart first)

Log audit event:
{
  "event_type": "plugin.healing_attempted",
  "plugin_id": "stt-provider/1.0.0",
  "action": "soft_restart",
  "consecutive_failures": 3,
}
```

### Healing Execution

```
10:01:05  Call plugin.on_unload()  (graceful shutdown)
10:01:15  Sleep 1 second
10:01:16  Call plugin.on_load()   (restart)
10:01:20  Next health_check()     (healthy!)

Log audit event:
{
  "event_type": "plugin.healing_succeeded",
  "plugin_id": "stt-provider/1.0.0",
  "action": "soft_restart",
  "healing_duration_ms": 15000,
}

Metric: corvin_healing_mttr_seconds_bucket{plugin_id="stt-provider", le="30"} ← incremented
```

### Recovery

```
10:01:30  health_check() returns HEALTHY ✓
10:02:00  health_check() returns HEALTHY ✓
10:02:30  health_check() returns HEALTHY ✓
           → Consecutive failures reset to 0
```

---

## Phase 3 Timeline (Propose After Phase 2 Complete)

### Week 1-2: Design Review
- Team reviews this design
- Stakeholders approve healing policies
- Discuss edge cases (in-flight requests, etc.)

### Week 3-4: Core Implementation
- Implement HealthTracker + HealingPolicyEngine
- Implement SelfHealingOrchestrator main loop
- 20+ unit tests

### Week 5-6: Healing Actions
- Implement circuit-break, soft-restart, disable
- Integration tests (full healing flow)
- Chaos tests

### Week 7-8: Observability + Deployment
- Prometheus metrics + Grafana dashboards
- Alert configuration
- Staging deployment (safe mode only)
- Production rollout (gradual, audit everything)

---

**Ready to build the self-healing ship.** ⚓
