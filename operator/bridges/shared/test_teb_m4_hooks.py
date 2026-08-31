"""Unit tests for TEB hooks M4 — all engines (ADR-0087 M4)."""

from __future__ import annotations

try:
    from teb.hook_dispatcher import HookDispatcher
    from teb import HookContext
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False


def test_hook_dispatcher_pre_hooks():
    """Test pre-hook dispatch."""
    if not HAS_DEPS:
        return

    dispatcher = HookDispatcher()

    hook_called = []
    def test_hook(ctx: HookContext) -> None:
        hook_called.append(ctx.tool_name)

    dispatcher.register("pre_tool_use", test_hook)

    ctx = HookContext(
        engine_id="test_engine",
        tool_name="Read",
        args={"path": "/tmp/test.txt"},
        chat_key="test:chat_1"
    )

    result = dispatcher.dispatch_pre(ctx)
    assert result is True
    assert len(hook_called) == 1
    assert hook_called[0] == "Read"


def test_hook_dispatcher_pre_hook_denial():
    """Test pre-hook can deny execution."""
    if not HAS_DEPS:
        return

    dispatcher = HookDispatcher()

    def deny_hook(ctx: HookContext) -> None:
        ctx.denied = True
        ctx.denial_reason = "forbidden tool"

    dispatcher.register("pre_tool_use", deny_hook)

    ctx = HookContext(
        engine_id="test_engine",
        tool_name="Bash",
        args={},
        chat_key="test:chat_1"
    )

    result = dispatcher.dispatch_pre(ctx)
    assert result is False
    assert ctx.denied is True
    assert ctx.denial_reason == "forbidden tool"


def test_hook_dispatcher_post_hooks():
    """Test post-hook dispatch."""
    if not HAS_DEPS:
        return

    dispatcher = HookDispatcher()

    post_hook_calls = []
    def post_hook(ctx: HookContext) -> None:
        post_hook_calls.append((ctx.tool_name, ctx.result))

    dispatcher.register("post_tool_use", post_hook)

    ctx = HookContext(
        engine_id="test_engine",
        tool_name="Bash",
        args={},
        chat_key="test:chat_1",
        result="bash output"
    )

    dispatcher.dispatch_post(ctx)
    assert len(post_hook_calls) == 1
    assert post_hook_calls[0] == ("Bash", "bash output")


def test_hook_dispatcher_multiple_pre_hooks():
    """Test multiple pre-hooks execute in order."""
    if not HAS_DEPS:
        return

    dispatcher = HookDispatcher()

    call_order = []

    def hook1(ctx: HookContext) -> None:
        call_order.append("hook1")

    def hook2(ctx: HookContext) -> None:
        call_order.append("hook2")

    dispatcher.register("pre_tool_use", hook1)
    dispatcher.register("pre_tool_use", hook2)

    ctx = HookContext(
        engine_id="test_engine",
        tool_name="Bash",
        args={},
        chat_key="test:chat_1"
    )

    result = dispatcher.dispatch_pre(ctx)
    assert result is True
    assert call_order == ["hook1", "hook2"]


def test_hook_dispatcher_first_denial_stops_chain():
    """Test that first denial stops pre-hook chain."""
    if not HAS_DEPS:
        return

    dispatcher = HookDispatcher()

    call_count = []

    def hook1(ctx: HookContext) -> None:
        call_count.append("hook1")
        ctx.denied = True
        ctx.denial_reason = "stopped"

    def hook2(ctx: HookContext) -> None:
        # Should not be called
        call_count.append("hook2")

    dispatcher.register("pre_tool_use", hook1)
    dispatcher.register("pre_tool_use", hook2)

    ctx = HookContext(
        engine_id="test_engine",
        tool_name="Bash",
        args={},
        chat_key="test:chat_1"
    )

    result = dispatcher.dispatch_pre(ctx)
    assert result is False
    assert len(call_count) == 1
    assert call_count[0] == "hook1"


def test_engine_capabilities_declare_teb_hooks():
    """Test that engines declare teb_brokered hooks capability."""
    if not HAS_DEPS:
        return

    # Check that capability keys are correct
    from agents.codex_cli import CodexCliEngine
    from agents.opencode_cli import OpenCodeEngine
    from agents.hermes_engine import HermesEngine

    assert CodexCliEngine.capabilities.get("hooks") == "teb_brokered"
    assert OpenCodeEngine.capabilities.get("hooks") == "teb_brokered"
    assert HermesEngine.capabilities.get("hooks") == "teb_brokered"


if __name__ == "__main__":
    print("✅ M4 TEB hooks tests loaded")
