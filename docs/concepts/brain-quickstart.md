# CorvinOS Brain — Quick Start Guide

## What is the Brain?

The CorvinOS Brain is an autonomous task orchestration system that:
- Monitors long-running tasks for stalls and errors
- Applies healing strategies automatically
- Learns from successes and failures
- Enforces budget and safety constraints
- Manages parallelism and task dependencies

## Quick Start (30 minutes)

### 1. Enable the Brain

The Brain is off by default in Phase 1. To enable:

```bash
# Config is at ~/.corvin/brain-config.yaml
corvin-brain  # Starts the Brain
```

### 2. Built-in Subsystems

Four subsystems are always loaded:

| Subsystem | What it does |
|-----------|-------------|
| **HealthMonitor** | Detects stalls (no activity for 10+ min) and error rate spikes |
| **ContextBridge** | Creates checkpoints on session splits; restores memory in new sessions |
| **LoopEngineer** | Applies healing strategies: direct_fix → pivot → decompose → escalate |
| **Orchestrator** | Manages parallelism (up to 3 concurrent tasks) and dependencies |

### 3. Phase 2 Subsystems (Optional)

Enable in `~/.corvin/brain-config.yaml`:

```yaml
brain:
  subsystems:
    - name: learning_engine
      enabled: true  # Off by default
      params:
        min_confidence: 0.5
    
    - name: cost_controller
      enabled: true
      params:
        daily_budget_usd: 50.0
    
    - name: safety_validator
      enabled: true
      params:
        forbidden_actions: ["rm -rf", "sudo"]
    
    - name: strategy_advisor
      enabled: true
```

## How It Works

### Example: Long-Running Refactoring

1. **Task Starts:** User asks Brain to refactor 50 files
2. **Health Monitoring:** Brain checks stall status every 5 minutes
3. **Error Detected:** Refactor hits a compilation error
4. **Strategy Applied:** LoopEngineer tries "direct_fix" (retry)
5. **Learning:** LearningEngine records error + strategy + result
6. **Safety Check:** SafetyValidator confirms fix is safe
7. **Cost Check:** CostController verifies budget allows retry
8. **Success:** Strategy works; refactor continues
9. **Escalation:** If all strategies fail, escalate to human

### Event Flow

```
User Task
    ↓
HealthMonitor (detect issues)
    ↓
LoopEngineer (apply strategy)
    ↓
LearningEngine (learn from result)
    ↓
CostController (enforce budget)
    ↓
SafetyValidator (verify safety)
    ↓
StrategyAdvisor (predict next strategy)
```

## Writing Your First Plugin

```python
# ~/.corvin/plugins/my_advisor.py

from core.orchestration.subsystems import Subsystem

class MyAdvisor(Subsystem):
    @property
    def name(self) -> str:
        return "my_advisor"

    @property
    def version(self) -> str:
        return "1.0.0"

    def startup(self, hub):
        self.hub = hub
        hub.subscribe("error_detected", self.on_error)

    async def on_event(self, event_name, event_data):
        if event_name == "error_detected":
            # Do something with the error
            await self.analyze_error(event_data)

    async def handle_request(self, request_type, **kwargs):
        if request_type == "my_request":
            return {"result": "value"}
        raise ValueError(f"Unknown request: {request_type}")

    def shutdown(self):
        pass
```

Then register in `~/.corvin/brain-config.yaml`:

```yaml
brain:
  subsystems:
    - name: my_advisor
      enabled: true
      path: ~/.corvin/plugins/my_advisor.py
      class: MyAdvisor
      params:
        setting1: value1
```

Restart the Brain, and your plugin is live!

## Configuration Reference

See `brain-config-reference.md` for full YAML schema and options.

## Architecture

For deep dive into the design:
- **ADR-0347** — Brain Subsystem Hub Architecture
- **ADR-0348** — Event Bus Pattern
- **ADR-0349** — Plugin Interface Contract
- **ADR-0350** — Configuration-Driven Plugin Loading
- **CONCEPT-0009** — Autonomous Task Orchestration

## Monitoring

```bash
# Check Brain status
corvin-brain status

# View recent events
corvin-brain logs --tail 50

# Validate config
corvin-brain config validate

# List loaded plugins
corvin-brain plugin list
```

## Troubleshooting

**Brain not starting?**
- Check `~/.corvin/brain-config.yaml` syntax (must be valid YAML)
- Check subsystem plugins exist at configured paths
- Run `corvin-brain config validate`

**Task not healing?**
- Check LoopEngineer is enabled
- Check strategy ladder is configured correctly
- Check error is not in SafetyValidator's forbidden list

**Budget exceeded?**
- Check CostController daily_budget_usd setting
- Query budget status: `corvin-brain budget status`
- Cheaper models: ask StrategyAdvisor for cheaper_alternative

## Next Steps

1. Try the Brain on a simple task (test refactoring, code cleanup)
2. Create a custom plugin for your use case
3. Tune parameters based on your experience (stall_timeout, retry_count, budget)
4. Provide feedback: what would make the Brain more useful?

---

**v0.2 Roadmap:**
- Context optimization (smart memory pruning)
- Human escalation UI (approve/reject strategies)
- Advanced learning (cross-task pattern matching)
- Telemetry dashboard (metrics + charts)
