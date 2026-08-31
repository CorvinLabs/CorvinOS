"""Tests for HookRegistry (ADR-0268 Phase 2)."""
import asyncio
import tempfile
from pathlib import Path

import pytest

from core.preprocessing import HookDefinition, HookRegistry, PreprocessContext


@pytest.fixture
def registry():
    """Create a hook registry."""
    return HookRegistry(tenant_id="_default")


@pytest.fixture
def simple_hook_file(tmp_path):
    """Create a simple hook function for testing."""
    hook_file = tmp_path / "simple_hook.py"
    hook_file.write_text(
        """
def my_hook(ctx):
    ctx.turn['modified_by_hook'] = True
    ctx.metadata['hook_ran'] = True
"""
    )
    return hook_file


@pytest.fixture
def async_hook_file(tmp_path):
    """Create an async hook function for testing."""
    hook_file = tmp_path / "async_hook.py"
    hook_file.write_text(
        """
import asyncio

async def my_async_hook(ctx):
    await asyncio.sleep(0)
    ctx.turn['async_hook_ran'] = True
"""
    )
    return hook_file


@pytest.fixture
def rejecting_hook_file(tmp_path):
    """Create a hook that rejects turns."""
    hook_file = tmp_path / "rejecting_hook.py"
    hook_file.write_text(
        """
def reject_hook(ctx):
    if ctx.turn.get('block_me'):
        ctx.reject('Blocked by policy')
"""
    )
    return hook_file


class TestHookRegistry:
    """Tests for basic HookRegistry functionality."""

    def test_register_hook(self, registry):
        """Registering a hook should add it to the registry."""
        hook = HookDefinition(
            id="test_hook",
            trigger="preprocessing",
            priority=50,
            file="/tmp/hook.py",
            function="my_hook",
        )
        registry.register_hook(hook)
        assert registry.get_hook("test_hook") is not None

    def test_register_multiple_hooks_sorted_by_priority(self, registry):
        """Hooks should be sorted by priority (higher first)."""
        hook1 = HookDefinition(
            id="low_priority",
            trigger="preprocessing",
            priority=10,
            file="/tmp/h1.py",
            function="h1",
        )
        hook2 = HookDefinition(
            id="high_priority",
            trigger="preprocessing",
            priority=100,
            file="/tmp/h2.py",
            function="h2",
        )
        registry.register_hook(hook1)
        registry.register_hook(hook2)

        hooks = registry.get_hooks("preprocessing")
        assert hooks[0].id == "high_priority"
        assert hooks[1].id == "low_priority"

    def test_unregister_hook(self, registry):
        """Unregistering a hook should remove it."""
        hook = HookDefinition(
            id="to_remove",
            trigger="preprocessing",
            priority=50,
            file="/tmp/hook.py",
            function="my_hook",
        )
        registry.register_hook(hook)
        assert registry.get_hook("to_remove") is not None

        registry.unregister_hook("to_remove")
        assert registry.get_hook("to_remove") is None

    def test_unknown_trigger_raises(self, registry):
        """Unknown trigger should raise ValueError."""
        hook = HookDefinition(
            id="bad_trigger",
            trigger="unknown_trigger",
            priority=50,
            file="/tmp/hook.py",
            function="my_hook",
        )
        with pytest.raises(ValueError, match="Unknown hook trigger"):
            registry.register_hook(hook)


class TestHookExecution:
    """Tests for hook execution."""

    @pytest.mark.asyncio
    async def test_run_simple_hook(self, registry, simple_hook_file):
        """Running a simple hook should modify context."""
        hook = HookDefinition(
            id="simple",
            trigger="preprocessing",
            priority=50,
            file=str(simple_hook_file),
            function="my_hook",
        )
        registry.register_hook(hook)

        ctx = PreprocessContext(
            turn={"data": "test"},
            session={},
            user={},
        )
        ctx = await registry.run_pipeline(ctx, trigger="preprocessing")

        assert ctx.turn.get("modified_by_hook") is True
        assert ctx.metadata.get("hook_ran") is True

    @pytest.mark.asyncio
    async def test_run_async_hook(self, registry, async_hook_file):
        """Running an async hook should work correctly."""
        hook = HookDefinition(
            id="async_hook",
            trigger="preprocessing",
            priority=50,
            file=str(async_hook_file),
            function="my_async_hook",
        )
        registry.register_hook(hook)

        ctx = PreprocessContext(
            turn={"data": "test"},
            session={},
            user={},
        )
        ctx = await registry.run_pipeline(ctx, trigger="preprocessing")

        assert ctx.turn.get("async_hook_ran") is True

    @pytest.mark.asyncio
    async def test_hook_error_doesnt_crash_pipeline(self, registry, tmp_path):
        """Errors in hooks should be logged but not crash the pipeline."""
        bad_hook_file = tmp_path / "bad_hook.py"
        bad_hook_file.write_text(
            """
def bad_hook(ctx):
    raise ValueError("Hook failed!")
"""
        )

        hook = HookDefinition(
            id="bad_hook",
            trigger="preprocessing",
            priority=50,
            file=str(bad_hook_file),
            function="bad_hook",
        )
        registry.register_hook(hook)

        ctx = PreprocessContext(
            turn={"data": "test"},
            session={},
            user={},
        )
        ctx = await registry.run_pipeline(ctx, trigger="preprocessing")

        # Pipeline should continue despite error
        assert "hook_errors" in ctx.metadata
        assert len(ctx.metadata["hook_errors"]) == 1
        assert "Hook failed!" in ctx.metadata["hook_errors"][0]["error"]

    @pytest.mark.asyncio
    async def test_hook_rejection(self, registry, rejecting_hook_file):
        """Hooks should be able to reject turns."""
        hook = HookDefinition(
            id="rejector",
            trigger="preprocessing",
            priority=50,
            file=str(rejecting_hook_file),
            function="reject_hook",
        )
        registry.register_hook(hook)

        ctx = PreprocessContext(
            turn={"block_me": True},
            session={},
            user={},
        )
        ctx = await registry.run_pipeline(ctx, trigger="preprocessing")

        assert ctx.metadata.get("rejected") is True
        assert "Blocked by policy" in ctx.metadata.get("rejection_reason")

    @pytest.mark.asyncio
    async def test_empty_pipeline(self, registry):
        """Running empty pipeline should return context unchanged."""
        ctx = PreprocessContext(
            turn={"data": "test"},
            session={},
            user={},
        )
        result = await registry.run_pipeline(ctx, trigger="preprocessing")
        assert result is ctx
        assert result.turn == {"data": "test"}


class TestHookLoading:
    """Tests for hook function loading."""

    def test_load_hook_function(self, registry, simple_hook_file):
        """Loading a hook function should work."""
        hook = HookDefinition(
            id="simple",
            trigger="preprocessing",
            priority=50,
            file=str(simple_hook_file),
            function="my_hook",
        )
        registry.register_hook(hook)

        func = registry.load_hook_function(hook)
        assert callable(func)

    def test_load_missing_hook_file(self, registry):
        """Loading missing hook file should raise."""
        hook = HookDefinition(
            id="missing",
            trigger="preprocessing",
            priority=50,
            file="/nonexistent/file.py",
            function="my_hook",
        )
        registry.register_hook(hook)

        with pytest.raises(FileNotFoundError):
            registry.load_hook_function(hook)

    def test_load_missing_hook_function(self, registry, tmp_path):
        """Loading missing hook function should raise."""
        hook_file = tmp_path / "hook.py"
        hook_file.write_text("def other_func(): pass")

        hook = HookDefinition(
            id="bad_func",
            trigger="preprocessing",
            priority=50,
            file=str(hook_file),
            function="nonexistent_func",
        )
        registry.register_hook(hook)

        with pytest.raises(AttributeError, match="nonexistent_func"):
            registry.load_hook_function(hook)
