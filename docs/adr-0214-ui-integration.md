# ADR-0214: Engine Visibility in Console Chat + Bridge

## Overview

ADR-0214 (Tiered Delegation Engine) now exposes engine selection information that should be displayed to users in both Console Chat and Bridge/Messenger interfaces.

## Data Format

Every turn's result includes an `engine_selection` dict:

```python
result = {
    "engine_selection": {
        "engine": "tiered_delegation" | "acs" | "claude_code",
        "confidence": 0.0-1.0,           # Probability selected engine is optimal
        "override": None | "acs",         # User override via /use-engine, if any
        "l34_forced": True | False,       # True if L34 gate forced claude_code
        "trivial": True | False,          # True if detected as simple task
        "signals": {...}                  # Optional: detailed signals (if /debug-engine)
    }
}
```

## UI Display Recommendations

### Console Chat

**Engine Badge** (show on every message):
```
🎯 Engine: [engine_name]  [confidence%]
```

Example:
```
🎯 Engine: tiered_delegation  (75% confidence)
🎯 Engine: acs  (92% confidence, parallel task)
🎯 Engine: claude_code  (trivial task)
```

**Detailed Breakdown** (if /debug-engine):
```
Engine Selection Details:
  • Engine: tiered_delegation
  • Confidence: 75%
  • Parallelization: 60% of steps can run in parallel
  • Task Type: code_generation
  • Context Availability: Yes
  • Historical Loss: 5%
```

### Bridge/Messenger (Discord, Slack, etc.)

**Inline Badge** (Discord):
```
🎯 Engine: tiered_delegation (75% confidence)
```

**Metadata Annotation** (Slack):
```
:robot_face: Processed via tiered_delegation (75% confidence)
```

## Engine Meanings for Users

| Engine | Meaning | When Selected |
|--------|---------|---------------|
| **tiered_delegation** | Context-preserving, iterative refinement | Coding, research, complex tasks with feedback loops |
| **acs** | Parallel-optimized for large-scale data | Big-data processing, independent batch jobs |
| **claude_code** | Local, inline execution (default) | Simple tasks, sensitive data, user fallback |

## Diagnostic Hints

### Why did it pick THIS engine?

Use `/debug-engine` to see signals:

```
/debug-engine
Your task here...
```

Returns detailed `signals` dict showing:
- `parallelization_ratio`: % of steps that can run in parallel
- `data_mb`: Estimated data volume
- `task_type`: Classification (code_generation, reasoning, etc.)
- `historical_loss_pct`: Average loss from prior runs
- `iteration_loops`: Estimated refinement loops needed

### Why was it forced to claude_code?

Check `l34_forced`:
- **True** → Data sensitivity detected (CONFIDENTIAL/RESTRICTED), L34 gate blocked delegation
- **False** → Engine chosen normally

### Why was it marked trivial?

Check `trivial`:
- **True** → Task is simple (low token count, 1 step, simple complexity), executed locally for speed
- **False** → Task routed to engine selection

## Implementation Checklist for UI Teams

- [ ] **Console Chat:** Display engine badge on every message
- [ ] **Console Chat:** Show `confidence` as percentage next to engine name
- [ ] **Console Chat:** Add `/debug-engine` command support (parse and display `signals` dict)
- [ ] **Bridge:** Add engine info to message metadata (Discord: embed field, Slack: context block)
- [ ] **Bridge:** Handle L34-forced case (show "sensitive data" reason)
- [ ] **Bridge:** Handle trivial task case (show "simple task" label)
- [ ] **Logging:** Log `engine_selection` with every turn for audit trail

## Example Code (Pseudocode)

### Console Chat Component

```python
def format_engine_badge(engine_selection):
    engine = engine_selection['engine']
    confidence = int(engine_selection['confidence'] * 100)
    l34_forced = engine_selection['l34_forced']
    trivial = engine_selection['trivial']
    override = engine_selection['override']
    
    if override:
        return f"🎯 Engine: {engine}  (user override)"
    elif l34_forced:
        return f"🎯 Engine: {engine}  (L34-blocked sensitive data)"
    elif trivial:
        return f"🎯 Engine: {engine}  ({confidence}%, trivial task)"
    else:
        return f"🎯 Engine: {engine}  ({confidence}% confidence)"
```

### Bridge Message Metadata

```python
def add_engine_metadata(discord_embed, engine_selection):
    embed.add_field(
        name="⚙️ Engine Used",
        value=f"{engine_selection['engine']} ({int(engine_selection['confidence']*100)}%)",
        inline=True
    )
```

## Troubleshooting

**Question:** Why does the engine keep switching?
**Answer:** Check `/debug-engine` output. If task structure changes, signals change, and engine selection adapts. This is expected. Use `/use-engine [name]` to force a specific engine if you know better.

**Question:** Why was my sensitive data forced to claude_code?
**Answer:** L34 gate detected CONFIDENTIAL/RESTRICTED data (emails, passwords, API keys, etc.). This is a safety feature and cannot be overridden with `/use-engine`.

**Question:** Why is everything marked "trivial"?
**Answer:** Tasks with <500 tokens, 1 step, and "simple" complexity are auto-routed to claude_code for speed. No visibility into full engine selection for trivial tasks.

## Related

- ADR-0214: Tiered Delegation Engine
- L34DelegationGate: Data-safety enforcement
- RobustEngineDetector: Engine selection logic
