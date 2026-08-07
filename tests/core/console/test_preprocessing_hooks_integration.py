"""Tests for preprocessing hooks integration (ADR-0268 Phase 2.5)."""
import tempfile
from pathlib import Path

import pytest

from core.console.corvin_console.preprocessing_hooks import (
    get_hook_registry,
    register_hook_from_package,
    run_preprocessing_hooks,
    unregister_hooks_from_package,
)


@pytest.fixture
def session_data():
    """Mock session data."""
    return {
        "chat_key": "web:test-sid",
        "turn_count": 1,
    }


@pytest.fixture
def user_data():
    """Mock user data."""
    return {
        "uid": "test-user",
        "name": "Test User",
    }


class TestRunPreprocessingHooks:
    """Tests for run_preprocessing_hooks integration."""

    @pytest.mark.asyncio
    async def test_run_without_hooks(self, session_data, user_data):
        """Running without hooks should return prompt unchanged."""
        prompt = "test prompt"
        modified, metadata = await run_preprocessing_hooks(
            prompt, session_data, user_data, tenant_id="_default"
        )
        assert modified == prompt
        assert isinstance(metadata, dict)

    @pytest.mark.asyncio
    async def test_run_with_rejecting_hook(self, session_data, user_data, tmp_path):
        """Running with rejecting hook should raise."""
        # Register a rejecting hook
        hook_file = tmp_path / "reject_hook.py"
        hook_file.write_text(
            """
def reject_all(ctx):
    ctx.reject('Rejected by test hook')
"""
        )

        registry = get_hook_registry("_default")
        from core.preprocessing import HookDefinition

        hook = HookDefinition(
            id="reject_test",
            trigger="preprocessing",
            priority=100,
            file=str(hook_file),
            function="reject_all",
        )
        registry.register_hook(hook)

        try:
            with pytest.raises(ValueError, match="Rejected by test hook"):
                await run_preprocessing_hooks(
                    "test", session_data, user_data, tenant_id="_default"
                )
        finally:
            registry.unregister_hook("reject_test")


class TestPackageHookRegistration:
    """Tests for registering/unregistering hooks from packages."""

    def test_register_hooks_from_package(self):
        """Registering hooks from a package should work."""
        hook_defs = [
            {
                "id": "pkg_hook_1",
                "trigger": "preprocessing",
                "priority": 75,
                "file": "/tmp/hook1.py",
                "function": "hook_func",
            },
        ]

        register_hook_from_package(
            "test-package", hook_defs, tenant_id="_default"
        )

        registry = get_hook_registry("_default")
        hook = registry.get_hook("pkg_hook_1")
        assert hook is not None
        assert hook.package_id == "test-package"

        # Clean up
        unregister_hooks_from_package("test-package", tenant_id="_default")

    def test_unregister_hooks_from_package(self):
        """Unregistering hooks should remove all from package."""
        hook_defs = [
            {
                "id": "pkg_hook_a",
                "trigger": "preprocessing",
                "priority": 50,
                "file": "/tmp/a.py",
                "function": "hook_a",
            },
            {
                "id": "pkg_hook_b",
                "trigger": "preprocessing",
                "priority": 50,
                "file": "/tmp/b.py",
                "function": "hook_b",
            },
        ]

        register_hook_from_package("pkg-2", hook_defs, tenant_id="_default")

        registry = get_hook_registry("_default")
        assert registry.get_hook("pkg_hook_a") is not None
        assert registry.get_hook("pkg_hook_b") is not None

        # Unregister all
        unregister_hooks_from_package("pkg-2", tenant_id="_default")

        assert registry.get_hook("pkg_hook_a") is None
        assert registry.get_hook("pkg_hook_b") is None

    def test_duplicate_hook_registration_skipped(self):
        """Registering same hook twice should skip the second."""
        hook_defs = [
            {
                "id": "duplicate_hook",
                "trigger": "preprocessing",
                "priority": 50,
                "file": "/tmp/hook.py",
                "function": "hook_func",
            },
        ]

        register_hook_from_package("pkg-1", hook_defs, tenant_id="_default")
        # Second registration should not error
        register_hook_from_package("pkg-2", hook_defs, tenant_id="_default")

        # Clean up
        registry = get_hook_registry("_default")
        registry.unregister_hook("duplicate_hook")


class TestMultiTenantHookRegistry:
    """Tests for multi-tenant hook registry isolation."""

    def test_hooks_isolated_by_tenant(self):
        """Hooks in one tenant should not affect others."""
        hook_defs_1 = [
            {
                "id": "tenant1_hook",
                "trigger": "preprocessing",
                "priority": 50,
                "file": "/tmp/h1.py",
                "function": "h1",
            },
        ]

        hook_defs_2 = [
            {
                "id": "tenant2_hook",
                "trigger": "preprocessing",
                "priority": 50,
                "file": "/tmp/h2.py",
                "function": "h2",
            },
        ]

        register_hook_from_package("pkg", hook_defs_1, tenant_id="tenant1")
        register_hook_from_package("pkg", hook_defs_2, tenant_id="tenant2")

        registry1 = get_hook_registry("tenant1")
        registry2 = get_hook_registry("tenant2")

        assert registry1.get_hook("tenant1_hook") is not None
        assert registry1.get_hook("tenant2_hook") is None

        assert registry2.get_hook("tenant2_hook") is not None
        assert registry2.get_hook("tenant1_hook") is None

        # Clean up
        unregister_hooks_from_package("pkg", tenant_id="tenant1")
        unregister_hooks_from_package("pkg", tenant_id="tenant2")
