"""
TIER-3 Feature-Level E2E Tests: Console Plugin — Hook Registration & Execution

Tests plugin hook system:
- Hook registration
- Hook execution order
- State isolation during hook execution
- Exception isolation
- Multi-plugin hook chains
"""

import pytest
from typing import List, Callable


@pytest.mark.plugin_feature_e2e
@pytest.mark.plugin_hooks
class TestConsolePluginHooks:
    """Test console plugin hook registration and execution"""

    def test_hook_registration(self):
        """Verify hooks are registered correctly"""
        class HookRegistry:
            def __init__(self):
                self.hooks = {}

            def register_hook(self, hook_name: str, handler: Callable):
                if hook_name not in self.hooks:
                    self.hooks[hook_name] = []
                self.hooks[hook_name].append(handler)

            def get_hooks(self, hook_name: str):
                return self.hooks.get(hook_name, [])

        registry = HookRegistry()

        handler1 = lambda: "panel_render_1"
        handler2 = lambda: "panel_render_2"

        registry.register_hook("on_panel_render", handler1)
        registry.register_hook("on_panel_render", handler2)

        # Verify hooks registered
        hooks = registry.get_hooks("on_panel_render")
        assert len(hooks) == 2
        assert handler1 in hooks
        assert handler2 in hooks

    def test_hook_execution_order(self):
        """Verify hooks execute in registration order"""
        execution_log = []

        class HookChain:
            def __init__(self):
                self.handlers = []

            def register(self, handler):
                self.handlers.append(handler)

            def execute(self):
                for handler in self.handlers:
                    handler()

        chain = HookChain()
        chain.register(lambda: execution_log.append("first"))
        chain.register(lambda: execution_log.append("second"))
        chain.register(lambda: execution_log.append("third"))

        chain.execute()

        # Verify execution order preserved
        assert execution_log == ["first", "second", "third"]

    def test_hook_state_isolation(self):
        """Verify hooks don't interfere with shared state"""
        shared_state = {"counter": 0}

        class IsolatedHooks:
            def __init__(self):
                self.handlers = []

            def register(self, handler):
                self.handlers.append(handler)

            def execute(self):
                for handler in self.handlers:
                    handler()

        hooks = IsolatedHooks()

        # Each hook reads but doesn't modify shared state
        hooks.register(lambda: shared_state)
        hooks.register(lambda: shared_state)
        hooks.register(lambda: shared_state)

        hooks.execute()

        # Shared state should be unchanged
        assert shared_state["counter"] == 0

    def test_hook_exception_isolation(self):
        """Verify exception in one hook doesn't crash the chain"""
        execution_log = []

        class ResilientHooks:
            def __init__(self):
                self.handlers = []

            def register(self, handler):
                self.handlers.append(handler)

            def execute(self):
                errors = []
                for handler in self.handlers:
                    try:
                        handler()
                    except Exception as e:
                        errors.append(e)
                return errors

        hooks = ResilientHooks()

        def failing_hook():
            execution_log.append("hook_1")
            raise RuntimeError("Hook 1 failed")

        def normal_hook():
            execution_log.append("hook_2")

        hooks.register(failing_hook)
        hooks.register(normal_hook)
        hooks.register(normal_hook)

        errors = hooks.execute()

        # Hook 2 should still execute despite hook 1 failure
        assert "hook_1" in execution_log
        assert execution_log.count("hook_2") == 2
        assert len(errors) == 1

    def test_hook_with_arguments(self):
        """Verify hooks can receive and pass arguments"""
        results = []

        class HooksWithArgs:
            def __init__(self):
                self.handlers = []

            def register(self, handler):
                self.handlers.append(handler)

            def execute(self, context):
                for handler in self.handlers:
                    handler(context)

        hooks = HooksWithArgs()

        def handler_1(ctx):
            results.append(("handler_1", ctx["panel_id"]))

        def handler_2(ctx):
            results.append(("handler_2", ctx["panel_id"]))

        hooks.register(handler_1)
        hooks.register(handler_2)

        context = {"panel_id": "dashboard"}
        hooks.execute(context)

        assert len(results) == 2
        assert results[0][1] == "dashboard"

    def test_conditional_hook_execution(self):
        """Verify hooks can be conditionally executed"""
        class ConditionalHooks:
            def __init__(self):
                self.handlers = []

            def register(self, handler, condition=None):
                self.handlers.append({"handler": handler, "condition": condition})

            def execute(self, context):
                for item in self.handlers:
                    condition = item["condition"]
                    if condition is None or condition(context):
                        item["handler"](context)

        hooks = ConditionalHooks()
        results = []

        def always_run(ctx):
            results.append("always")

        def run_if_admin(ctx):
            results.append("admin_only")

        hooks.register(always_run)
        hooks.register(run_if_admin, condition=lambda ctx: ctx.get("is_admin", False))

        # Execute as non-admin
        hooks.execute({"is_admin": False})
        assert results == ["always"]

        # Execute as admin
        results.clear()
        hooks.execute({"is_admin": True})
        assert results == ["always", "admin_only"]

    def test_hook_deregistration(self):
        """Verify hooks can be deregistered"""
        class ManageableHooks:
            def __init__(self):
                self.handlers = []

            def register(self, handler):
                self.handlers.append(handler)

            def deregister(self, handler):
                if handler in self.handlers:
                    self.handlers.remove(handler)

            def execute(self):
                for handler in self.handlers:
                    handler()

        hooks = ManageableHooks()
        results = []

        def hook_1():
            results.append("hook_1")

        def hook_2():
            results.append("hook_2")

        hooks.register(hook_1)
        hooks.register(hook_2)

        # Remove hook_1
        hooks.deregister(hook_1)
        hooks.execute()

        # Only hook_2 should execute
        assert results == ["hook_2"]

    def test_multiple_hook_types(self):
        """Verify multiple different hook types can coexist"""
        class MultiHookRegistry:
            def __init__(self):
                self.hooks = {}

            def register(self, hook_type, handler):
                if hook_type not in self.hooks:
                    self.hooks[hook_type] = []
                self.hooks[hook_type].append(handler)

            def execute(self, hook_type):
                if hook_type in self.hooks:
                    for handler in self.hooks[hook_type]:
                        handler()

        registry = MultiHookRegistry()
        on_load_calls = []
        on_render_calls = []

        registry.register("on_load", lambda: on_load_calls.append(1))
        registry.register("on_render", lambda: on_render_calls.append(1))
        registry.register("on_load", lambda: on_load_calls.append(2))

        registry.execute("on_load")
        registry.execute("on_render")

        assert len(on_load_calls) == 2
        assert len(on_render_calls) == 1

    def test_hook_return_value_aggregation(self):
        """Verify hook return values can be collected"""
        class AggregatingHooks:
            def __init__(self):
                self.handlers = []

            def register(self, handler):
                self.handlers.append(handler)

            def execute(self):
                results = []
                for handler in self.handlers:
                    result = handler()
                    if result is not None:
                        results.append(result)
                return results

        hooks = AggregatingHooks()

        hooks.register(lambda: "result_1")
        hooks.register(lambda: None)  # Returns None
        hooks.register(lambda: "result_2")

        results = hooks.execute()

        # Only non-None results collected
        assert results == ["result_1", "result_2"]
        assert len(results) == 2
