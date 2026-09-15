# M2 Engine Integration Pattern: Buffered /btw

## For Codex, OpenCode, Hermes Engines

On spawn entry, **before** calling the engine subprocess:

```python
# At top of spawn() method, after parameter validation:
from eci.transport_buffered import dequeue_all_injections

class CodexCliEngine(WorkerEngine):
    def spawn(self, prompt: str, **kwargs):
        # M2: Check for buffered /btw injections
        session_dir = kwargs.get("session_dir")  # passed by delegation layer
        if session_dir:
            buffered = dequeue_all_injections(session_dir)
            if buffered:
                prompt = buffered + "\n\n" + prompt
        
        # Now proceed with normal spawn...
        args = self._build_args(prompt, **kwargs)
        return self._iter_stream(args)
```

## ECI Manifest Update

Declare the transport type (not just a bool):

```python
# In engines/__init__.py or agents/__init__.py
"mid_stream_inject": "buffered"  # vs. "stdin_json" (Claude Code) or None
```

## Caller Pattern (Dispatcher → /btw Handler)

When `/btw "text"` is called from the chat:

```python
# In dispatcher.py or btw_handler.py
if engine.capabilities.get("mid_stream_inject") == "buffered":
    enqueue_injection(session_dir, text)
    return "✓ Queued for next turn (buffered transport)"
elif engine.capabilities.get("mid_stream_inject") == "stdin_json":
    return inject_via_stdin(text)  # Claude Code (live)
else:
    return "✗ /btw not supported on this engine"
```

## Non-Breaking

- Claude Code unaffected (stdin_json path unchanged)
- Engines without buffering return error (explicit, not silent)
- Queue is auto-cleaned on dequeue (one-time per spawn)

## Testing

See `test_btw_buffered_e2e.py` for round-trip validation.
