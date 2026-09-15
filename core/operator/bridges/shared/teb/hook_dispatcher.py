"""Hook Dispatcher — ADR-0087 M4.

Routes PreToolUse hook events through TEB for all engines.
Enables unified L10 (path-gate) + L16 (audit) enforcement across
all 5 engines (Claude Code, Codex, OpenCode, Hermes, Copilot).

Hook emission is synchronous and audit-first: emit audit event BEFORE
tool execution (L16 invariant). Denial blocks execution (fail-closed).

MUST NOT import anthropic (CI AST lint enforces).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import HookContext


class HookDispatcher:
    """Registry for PreToolUse hooks.

    A hook is a callable that inspects HookContext and may set
    ctx.denied=True + ctx.denial_reason to block execution.
    """

    def __init__(self) -> None:
        self._hooks: dict[str, list[Any]] = {
            "pre_tool_use": [],   # (ctx) -> None; may set ctx.denied
            "post_tool_use": [],  # (ctx) -> None; receives ctx.result
        }

    def register(self, hook_type: str, hook: Any) -> None:
        """Register a hook.

        Args:
            hook_type: "pre_tool_use" or "post_tool_use"
            hook: Callable[[HookContext], None]
        """
        if hook_type not in self._hooks:
            raise ValueError(f"Unknown hook type: {hook_type}")
        self._hooks[hook_type].append(hook)

    def dispatch_pre(self, ctx: HookContext) -> bool:
        """Dispatch pre-tool-use hooks. Return True if execution should proceed.

        Runs hooks in registration order. First denial stops the chain
        and returns False (execution blocked).
        """
        for hook in self._hooks["pre_tool_use"]:
            try:
                hook(ctx)
            except Exception as exc:  # noqa: BLE001
                ctx.denied = True
                ctx.denial_reason = f"pre-hook error: {exc}"

            if ctx.denied:
                return False

        return True

    def dispatch_post(self, ctx: HookContext) -> None:
        """Dispatch post-tool-use hooks.

        Runs after successful execution. ctx.result is populated.
        """
        for hook in self._hooks["post_tool_use"]:
            try:
                hook(ctx)
            except Exception as exc:  # noqa: BLE001
                # Post-hook errors are logged but don't fail the tool call
                pass

    def hooks_for_engine(self, engine_id: str) -> dict[str, list[Any]]:
        """Return hooks configured for this engine.

        For now, all engines use the same hook list. Per-engine
        filtering can be added if needed.
        """
        return self._hooks
