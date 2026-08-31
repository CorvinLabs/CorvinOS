# CorvinOS Brain Configuration Reference

## File Location

**Default:** `~/.corvin/brain-config.yaml`

**Custom:** Set `CORVIN_BRAIN_CONFIG` environment variable to override.

## Schema

```yaml
# ~/.corvin/brain-config.yaml
brain:
  # Global settings
  poll_interval_s: 5                 # Main loop interval (seconds)
  max_event_queue_size: 10000        # Event buffer size
  event_timeout_s: 60                # Async handler timeout

  subsystems:
    # Each subsystem entry
    - name: subsystem_name           # Required: unique ID
      enabled: true                  # Optional: true by default
      path: core/.../subsystem.py    # Optional: for custom plugins
      class: SubsystemClassName      # Optional: class name
      params:                         # Optional: subsystem-specific params
        key: value
```

## Built-in Subsystems

### HealthMonitor

Detects stalls and error rates.

```yaml
- name: health_monitor
  enabled: true
  params:
    stall_timeout_min: 10            # Stall detection timeout (minutes)
    error_rate_threshold: 0.3        # Error rate % before alert
    token_burn_check_interval: 5     # Check interval (turns)
```

### ContextBridge

Manages session splits and memory checkpoints.

```yaml
- name: context_bridge
  enabled: true
  params:
    checkpoint_interval_turns: 25    # Save checkpoint every N turns
    memory_tier_sizes:               # Tier sizes for memory compression
      - 500                          # Tier 1: 500 tokens
      - 2000                         # Tier 2: 2000 tokens
      - 8000                         # Tier 3: 8000 tokens (full)
    max_checkpoints_per_task: 10     # Max checkpoints to retain
```

### LoopEngineer

Auto-healing with strategy ladder.

```yaml
- name: loop_engineer
  enabled: true
  params:
    max_retries: 5                   # Max retry attempts
    strategy_ladder:                 # Strategies in order
      - direct_fix                   # 1. Retry same approach
      - pivot_approach               # 2. Try different angle
      - decompose                    # 3. Break into smaller tasks
      - escalate                     # 4. Ask human
```

### Orchestrator

Task scheduling and parallelism.

```yaml
- name: orchestrator
  enabled: true
  params:
    max_parallel_sessions: 3         # Max concurrent tasks
    dependency_aware: true           # Track task dependencies
```

### LearningEngine (Phase 2)

Learn from error/strategy patterns.

```yaml
- name: learning_engine
  enabled: false                     # Disabled by default
  path: core/orchestration/subsystems/learning_engine.py
  class: LearningEngine
  params:
    db_path: ~/.corvin/learning/engine.db  # Where to store learning data
    min_confidence: 0.5              # Min confidence to recommend
    learning_window_turns: 100       # Remember last N outcomes
```

### CostController (Phase 2)

Budget enforcement and cost estimation.

```yaml
- name: cost_controller
  enabled: false
  path: core/orchestration/subsystems/cost_controller.py
  class: CostController
  params:
    daily_budget_usd: 50.0           # Daily API cost budget
    preferred_model: claude-3.5-haiku  # Default model for estimation
    cost_warning_threshold: 0.8      # Warn at 80% of budget
```

### SafetyValidator (Phase 2)

Forbidden action detection.

```yaml
- name: safety_validator
  enabled: false
  path: core/orchestration/subsystems/safety_validator.py
  class: SafetyValidator
  params:
    forbidden_actions:               # Strings to block
      - rm -rf
      - sudo
      - delete_all
      - drop database
    max_retry_attempts: 3            # Before escalation
```

### StrategyAdvisor (Phase 2)

Strategy success prediction.

```yaml
- name: strategy_advisor
  enabled: false
  path: core/orchestration/subsystems/strategy_advisor.py
  class: StrategyAdvisor
  params:
    model: claude-3.5-sonnet         # Model for strategy analysis
    cache_predictions: true          # Cache success predictions
```

## Custom Plugins

Load your own subsystems:

```yaml
- name: my_custom_plugin
  enabled: true
  path: ~/.corvin/plugins/my_plugin.py  # Relative to home or absolute
  class: MyPlugin                        # Must inherit Subsystem
  params:
    custom_param: value
    another_param: 123
```

**Plugin Requirements:**
- File must export a class inheriting `Subsystem`
- Class name matches `class` parameter
- Implement all 5 required methods:
  - `name` (property)
  - `version` (property)
  - `startup(hub)`
  - `on_event(name, data)` (async)
  - `handle_request(type, **kw)` (async)
  - `shutdown()`

## Environment Overrides

```bash
# Override config file location
export CORVIN_BRAIN_CONFIG=/custom/path/config.yaml

# Override poll interval
export CORVIN_BRAIN_POLL_INTERVAL=10

# Disable specific subsystem (via override)
export CORVIN_BRAIN_DISABLE_SAFETY_VALIDATOR=true
```

## Example Configurations

### Minimal (Phase 1 only)

```yaml
brain:
  poll_interval_s: 5
  subsystems:
    - name: health_monitor
      enabled: true
    - name: context_bridge
      enabled: true
    - name: loop_engineer
      enabled: true
    - name: orchestrator
      enabled: true
```

### Full (Phase 1 + Phase 2)

```yaml
brain:
  poll_interval_s: 5
  subsystems:
    - name: health_monitor
      enabled: true
    - name: context_bridge
      enabled: true
    - name: loop_engineer
      enabled: true
      params:
        max_retries: 5
    - name: orchestrator
      enabled: true
    - name: learning_engine
      enabled: true
    - name: cost_controller
      enabled: true
      params:
        daily_budget_usd: 100.0
    - name: safety_validator
      enabled: true
    - name: strategy_advisor
      enabled: true
```

### Development (Fast, Verbose)

```yaml
brain:
  poll_interval_s: 1                # Poll every 1s for quick feedback
  max_event_queue_size: 1000        # Smaller queue for testing
  subsystems:
    - name: health_monitor
      enabled: true
      params:
        stall_timeout_min: 1        # Aggressive stall detection
    - name: loop_engineer
      enabled: true
      params:
        max_retries: 2              # Fewer retries for testing
    # ... other subsystems
```

### Production (Conservative)

```yaml
brain:
  poll_interval_s: 10               # Poll every 10s to reduce CPU
  max_event_queue_size: 50000       # Large queue for stability
  subsystems:
    - name: health_monitor
      enabled: true
      params:
        stall_timeout_min: 30       # Long timeout to avoid false positives
    - name: cost_controller
      enabled: true
      params:
        daily_budget_usd: 1000.0    # Generous budget
    - name: safety_validator
      enabled: true                 # Conservative: safety first
    # ... other subsystems
```

## Validation

Before starting the Brain:

```bash
# Check config syntax
corvin-brain config validate

# Validate all subsystems are loadable
corvin-brain config validate --check-plugins

# Dry run (load config, register subsystems, exit)
corvin-brain config validate --dry-run
```

## Troubleshooting

### Config file not found

```
ERROR: Brain config not found at ~/.corvin/brain-config.yaml

Solution:
1. Create default config: corvin-brain config init
2. Or set custom path: export CORVIN_BRAIN_CONFIG=/path/to/config.yaml
```

### YAML syntax error

```
ERROR: Failed to parse config: YAML error at line 15

Solution:
1. Verify YAML syntax (tabs not spaces, proper indentation)
2. Use online YAML validator: yamllint.com
3. Run: corvin-brain config validate --debug
```

### Plugin not found

```
ERROR: Failed to register my_custom_plugin: module not found

Solution:
1. Check path exists: ls ~/.corvin/plugins/my_plugin.py
2. Check Python syntax: python -m py_compile ~/.corvin/plugins/my_plugin.py
3. Check class name matches: grep "class MyPlugin" ~/.corvin/plugins/my_plugin.py
```

### Budget exceeded

```
WARNING: Cost exceeded daily budget ($50.00)

Solution:
1. Increase daily_budget_usd in cost_controller params
2. Use cheaper model: preferred_model: claude-3.5-haiku
3. Query budget status: corvin-brain budget status
```

## Performance Tuning

| Parameter | Decrease | Increase |
|-----------|----------|----------|
| `poll_interval_s` | More responsive to stalls | Lower CPU usage |
| `max_event_queue_size` | Lower memory | Resilient to bursts |
| `stall_timeout_min` | Catch stalls faster | Fewer false positives |
| `max_retries` | Fail faster | More resilient |

---

**Config Version:** 1.0  
**Brain Version:** 0.2  
**Last Updated:** 2026-08-16
