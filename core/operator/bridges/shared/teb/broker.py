"""ToolExecutionBroker — ADR-0069 M1.

Intercepts MCP tool calls and runs the full Corvin hook pipeline:
  1. Pre-hook  : path-gate check (L10 equivalent for non-CC engines)
  2. Execute   : call the real tool via tool_registry
  3. Post-hook : artifact registration (L33), audit emission (L16)

Wire-up into the Forge MCP server is M2 (not yet done); this module
can be unit-tested standalone.

MUST NOT import anthropic (CI AST lint enforces).
"""

from __future__ import annotations

import time
from typing import Any, Callable

from . import BrokerResult, HookContext

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

ToolExecutor = Callable[[str, dict[str, Any]], Any]
PreHook = Callable[[HookContext], None]   # may set ctx.denied + ctx.denial_reason
PostHook = Callable[[HookContext], None]  # receives ctx.result


class ToolExecutionBroker:
    """Stateless broker — instantiate once, reuse across tool calls.

    pre_hooks  : run in order before execution; any hook may deny.
    post_hooks : run in order after execution (skipped on denial).
    """

    def __init__(
        self,
        tool_executor: ToolExecutor,
        pre_hooks: list[PreHook] | None = None,
        post_hooks: list[PostHook] | None = None,
    ) -> None:
        self._executor = tool_executor
        self._pre_hooks: list[PreHook] = pre_hooks or []
        self._post_hooks: list[PostHook] = post_hooks or []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(
        self,
        engine_id: str,
        tool_name: str,
        args: dict[str, Any],
        chat_key: str = "",
        executor: ToolExecutor | None = None,
    ) -> BrokerResult:
        """Run the full hook pipeline around one tool call.

        ``executor`` overrides the instance-level ``_executor`` for this
        single call (thread-safe — no shared mutable state).
        """
        active_executor = executor if executor is not None else self._executor
        ctx = HookContext(
            engine_id=engine_id,
            tool_name=tool_name,
            args=args,
            chat_key=chat_key,
        )
        events: list[dict[str, Any]] = []
        t0 = time.monotonic()

        # --- pre-hooks (path-gate, etc.) ---
        for hook in self._pre_hooks:
            try:
                hook(ctx)
            except Exception as exc:  # noqa: BLE001
                ctx.denied = True
                ctx.denial_reason = f"pre-hook error: {exc}"
            if ctx.denied:
                events.append({
                    "event": "tool_call.denied",
                    "engine_id": engine_id,
                    "tool": tool_name,
                    "reason": ctx.denial_reason,
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                })
                return BrokerResult(
                    success=False,
                    output=None,
                    denied=True,
                    denial_reason=ctx.denial_reason,
                    audit_events=events,
                )

        events.append({
            "event": "tool_call.start",
            "engine_id": engine_id,
            "tool": tool_name,
        })

        # --- execute ---
        try:
            result = active_executor(tool_name, args)
            ctx.result = result
        except Exception as exc:  # noqa: BLE001
            events.append({
                "event": "tool_call.failed",
                "engine_id": engine_id,
                "tool": tool_name,
                "error": str(exc),
                "duration_ms": int((time.monotonic() - t0) * 1000),
            })
            return BrokerResult(
                success=False,
                output=None,
                audit_events=events,
            )

        # --- post-hooks (artifact registration, etc.) ---
        # Failures are best-effort (observability); we log but do not block.
        import logging as _logging
        _teb_log = _logging.getLogger("corvin.teb")
        for hook in self._post_hooks:
            try:
                hook(ctx)
            except Exception as _hook_exc:  # noqa: BLE001
                _teb_log.warning(
                    "teb post-hook %s failed for tool %s: %s",
                    getattr(hook, "__name__", repr(hook)),
                    tool_name,
                    _hook_exc,
                )
                events.append({
                    "event": "tool_call.post_hook_error",
                    "engine_id": engine_id,
                    "tool": tool_name,
                    "hook": getattr(hook, "__name__", "unknown"),
                })

        events.append({
            "event": "tool_call.done",
            "engine_id": engine_id,
            "tool": tool_name,
            "duration_ms": int((time.monotonic() - t0) * 1000),
        })

        return BrokerResult(
            success=True,
            output=result,
            audit_events=events,
        )

    # ------------------------------------------------------------------
    # Hook registration helpers
    # ------------------------------------------------------------------

    def add_pre_hook(self, hook: PreHook) -> None:
        self._pre_hooks.append(hook)

    def add_post_hook(self, hook: PostHook) -> None:
        self._post_hooks.append(hook)
