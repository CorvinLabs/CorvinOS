# Execution Context Badge — Phase 1 Integration Guide

## Overview

The Execution Context Badge captures structured metadata about every turn across all engines and model sources. Phase 1 provides the foundation: `ExecutionContext` dataclass, model/engine detection, and token counting utilities.

## Key Components

### 1. ExecutionContext Dataclass

Unified schema for all turn metadata:

```python
from corvin_console.execution_context import ExecutionContext, ExecutionContextBuilder

# Via builder (recommended for turn lifecycle tracking)
builder = ExecutionContextBuilder(tenant_id="tenant-1", turn_number=0)
ctx = (builder
    .start(engine_id="claude_code", model_name="claude-3-5-sonnet-20241022")
    .set_delegation(mode="native")
    .set_usage({"input_tokens": 150, "output_tokens": 50})
    .add_tool_call()
    .add_tool_call()
    .set_exit_code(0)
    .complete())

# Or directly
ctx = ExecutionContext(
    engine_id=EngineId.CLAUDE_CODE,
    model_source=ModelSource.CLAUDE,
    model_name="claude-3-5-sonnet",
    delegation_mode=DelegationMode.NATIVE,
    tokens_input=150,
    tokens_output=50,
    tool_calls_count=2,
    exit_code=0,
)
```

### 2. Model Detection

Auto-detect model source from model name:

```python
from corvin_console.execution_context import (
    detect_model_source,
    normalize_model_name,
    ModelSource
)

# Detection
assert detect_model_source("claude-3-5-sonnet") == ModelSource.CLAUDE
assert detect_model_source("ollama:mistral") == ModelSource.OLLAMA
assert detect_model_source("openrouter:meta-llama/llama-2") == ModelSource.OPENROUTER

# Normalization (canonical form)
assert normalize_model_name("claude-3-5-sonnet-20241022") == "claude-3-5-sonnet"
assert normalize_model_name("ollama:mistral:latest") == "ollama/mistral:latest"
assert normalize_model_name("openrouter:mistral") == "openrouter/mistral"
```

### 3. Engine Detection

Detect which engine is running:

```python
from corvin_console.execution_context import detect_engine, EngineId

# From runtime state
assert detect_engine({"engine_id": "claude_code"}) == EngineId.CLAUDE_CODE
assert detect_engine({"spawn_via": "http"}) == EngineId.HERMES
assert detect_engine({"delegation_mode": "acs"}) == EngineId.ACS
```

### 4. Delegation Mode Detection

Track whether a turn was delegated:

```python
from corvin_console.execution_context import detect_delegation_mode, DelegationMode

assert detect_delegation_mode({}) == DelegationMode.NATIVE
assert detect_delegation_mode({"delegation_mode": "acs"}) == DelegationMode.ACS
assert detect_delegation_mode({"acs_run_id": "run-123"}) == DelegationMode.ACS
```

### 5. Token Counting

Extract tokens from various API formats:

```python
from corvin_console.execution_context import extract_token_usage

# Anthropic format
usage = {"input_tokens": 150, "output_tokens": 50}
in_tok, out_tok = extract_token_usage(usage)
assert in_tok == 150 and out_tok == 50

# OpenRouter format
usage = {"prompt_tokens": 200, "completion_tokens": 75}
in_tok, out_tok = extract_token_usage(usage)
assert in_tok == 200 and out_tok == 75

# Generic format
usage = {"tokens_in": 100, "tokens_out": 25}
in_tok, out_tok = extract_token_usage(usage)
assert in_tok == 100 and out_tok == 25
```

## Integration Points (Phase 2)

These integrations are planned for Phase 2:

### In chat_runtime.py

1. **stream_turn()** — Direct Claude Code turns:
   ```python
   builder = ExecutionContextBuilder(tenant_id=sess.tenant_id, turn_number=sess.turn_count)
   
   builder.start(
       engine_id=_os_engine,
       model_name=_os_model_used
   )
   
   # ... capture usage from subprocess result ...
   
   ctx = builder.set_usage(usage_from_result).complete()
   message.metadata.execution_context = ctx.to_dict()
   ```

2. **_stream_hermes_turn()** — Local Hermes engine:
   ```python
   builder.start(engine_id="hermes", model_name=model_name)
   builder.set_delegation(mode="native")
   # Track tokens from HermesEngine response
   ctx = builder.complete()
   ```

3. **_stream_tde_turn()** — Tiered Delegation:
   ```python
   builder.set_delegation(
       mode="tde",
       tde_router_decision=router_output["decision"]
   )
   ```

4. **ACS Delegation Branch** — Worker delegation:
   ```python
   builder.set_delegation(
       mode="acs",
       acs_run_id=run.run_id
   )
   ```

### In audit chain

Track execution context in L16 bridge audit:

```python
# os_turn.completed event
_os_audit("os_turn.completed", {
    "engine_id": ctx.engine_id.value,
    "model_name": ctx.model_name,
    "duration_ms": ctx.duration_ms,
    "tokens_input": ctx.tokens_input,
    "tokens_output": ctx.tokens_output,
})
```

### In console routes

Expose execution context in REST API responses:

```python
# GET /api/turns/<turn_id>
return {
    "turn": {
        "text": "...",
        "execution_context": {
            "engine_id": "claude_code",
            "model_name": "claude-3-5-sonnet",
            "duration_ms": 1234,
            "tokens_input": 150,
            "tokens_output": 50,
        }
    }
}
```

## Enums

### ModelSource
- `CLAUDE` — Anthropic API models (claude-3-*, etc.)
- `OLLAMA` — Local Ollama HTTP
- `OPENROUTER` — OpenRouter API routing
- `HERMES` — Hermes local fallback
- `UNKNOWN` — Unrecognized

### EngineId
- `CLAUDE_CODE` — Direct claude subprocess
- `ACS` — ACS fan-out workers
- `TDE` — Tiered Delegation Engine
- `HERMES` — Layer-22 WorkerEngine
- `UNKNOWN` — Unrecognized

### DelegationMode
- `NATIVE` — Direct OS engine (non-delegated)
- `ACS` — Delegated to ACS workers
- `TDE` — Delegated to TDE router
- `FALLBACK` — Delegated but fell back to native

## Serialization

ExecutionContext is fully serializable for audit/storage:

```python
# To JSON-compatible dict
data = ctx.to_dict()

# From JSON
ctx = ExecutionContext.from_dict(data)
```

## Testing

Full test suite in `core/console/tests/test_execution_context.py`:

```bash
uv run pytest core/console/tests/test_execution_context.py -v
```

48 passing tests covering:
- Model source detection (claude, ollama, openrouter, hermes)
- Model name normalization
- Engine detection
- Delegation mode detection
- Token counting (Anthropic, OpenRouter, generic formats)
- Serialization/deserialization (roundtrip)
- Builder lifecycle tracking
- Real-world integration scenarios
- Edge cases and robustness

## Next Steps (Phase 2)

1. Wire ExecutionContext capture in `stream_turn()` for direct Claude Code turns
2. Extend to `_stream_hermes_turn()` for local engine execution
3. Track delegation in `_stream_tde_turn()` and ACS branch
4. Emit execution context in L16 audit chain
5. Expose in REST API and frontend badge rendering
