"""
PHASE 9 REMEDIATION STREAM 3: Tenant Isolation Tests

Verify complete tenant isolation across:
1. Plugin Manager (no hardcoded tenant_id)
2. Chat Learning Wrapper (no hardcoded tenant_id)
3. Voice Orchestration (no hardcoded tenant_id)
4. Audit log filtering (no cross-tenant leakage)

Exit Criteria:
- [ ] No hardcoded tenant_id/operator_id in code
- [ ] All queries filtered by session tenant
- [ ] Multi-tenant tests verify complete isolation
- [ ] No cross-tenant data leakage
- [ ] All tests pass

ADR-2029: User-Centric CorvinOS Control Plane
"""

import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch
from dataclasses import asdict

from corvin_console.control_plane.plugin_manager import (
    PluginManager,
    BootLayer,
    PluginInfo,
)
from corvin_console.chat_learning_wrapper import (
    ChatLearningWrapper,
    get_chat_learning_wrapper,
)
from corvin_console.voice_summary_orchestration import (
    OrchestrationCompleteEvent,
    TaskResult,
)


class TestPluginManagerTenantIsolation:
    """Test plugin manager enforces strict tenant isolation."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry_path = Path(self.temp_dir.name) / "plugins.json"
        self.manager = PluginManager(registry_path=self.registry_path)

    def teardown_method(self):
        """Clean up test fixtures."""
        self.temp_dir.cleanup()

    @pytest.mark.asyncio
    async def test_install_plugin_requires_explicit_tenant_id(self):
        """Verify tenant_id is REQUIRED, not defaulted to 'default'."""
        # This should succeed with explicit tenant_id
        result = await self.manager.install_plugin(
            plugin_id="test.plugin",
            name="Test Plugin",
            version="1.0.0",
            boot_layer="bundled",
            tenant_id="tenant_alpha",
            operator_id="operator_1",
        )
        assert result["status"] == "success"
        assert "tenant_alpha" in self.manager.plugins

    @pytest.mark.asyncio
    async def test_install_plugin_with_empty_tenant_id_fails(self):
        """Verify empty tenant_id is rejected (fail-closed)."""
        with pytest.raises(ValueError, match="tenant_id must be a non-empty string"):
            await self.manager.install_plugin(
                plugin_id="test.plugin",
                name="Test Plugin",
                version="1.0.0",
                boot_layer="bundled",
                tenant_id="",
                operator_id="operator_1",
            )

    @pytest.mark.asyncio
    async def test_install_plugin_with_none_tenant_id_fails(self):
        """Verify None tenant_id is rejected (fail-closed)."""
        with pytest.raises(ValueError, match="tenant_id must be a non-empty string"):
            await self.manager.install_plugin(
                plugin_id="test.plugin",
                name="Test Plugin",
                version="1.0.0",
                boot_layer="bundled",
                tenant_id=None,
                operator_id="operator_1",
            )

    @pytest.mark.asyncio
    async def test_tenant_alpha_cannot_see_tenant_beta_plugins(self):
        """Verify tenant alpha cannot access plugins installed by tenant beta."""
        # Install plugin for tenant_alpha
        await self.manager.install_plugin(
            plugin_id="plugin.alpha",
            name="Alpha Plugin",
            version="1.0.0",
            boot_layer="bundled",
            tenant_id="tenant_alpha",
            operator_id="operator_1",
        )

        # Install plugin for tenant_beta
        await self.manager.install_plugin(
            plugin_id="plugin.beta",
            name="Beta Plugin",
            version="1.0.0",
            boot_layer="bundled",
            tenant_id="tenant_beta",
            operator_id="operator_2",
        )

        # tenant_alpha lists plugins
        alpha_plugins = await self.manager.list_plugins(tenant_id="tenant_alpha")
        alpha_plugin_ids = [p.plugin_id for p in alpha_plugins]

        # tenant_beta lists plugins
        beta_plugins = await self.manager.list_plugins(tenant_id="tenant_beta")
        beta_plugin_ids = [p.plugin_id for p in beta_plugins]

        # Verify no cross-tenant leakage
        assert "plugin.alpha" in alpha_plugin_ids
        assert "plugin.beta" not in alpha_plugin_ids

        assert "plugin.beta" in beta_plugin_ids
        assert "plugin.alpha" not in beta_plugin_ids

    @pytest.mark.asyncio
    async def test_audit_log_filters_by_tenant(self):
        """Verify audit log is strictly filtered by tenant (no cross-tenant leakage)."""
        # Install plugins for two tenants
        await self.manager.install_plugin(
            plugin_id="plugin.alpha",
            name="Alpha Plugin",
            version="1.0.0",
            boot_layer="bundled",
            tenant_id="tenant_alpha",
            operator_id="operator_alpha",
        )

        await self.manager.install_plugin(
            plugin_id="plugin.beta",
            name="Beta Plugin",
            version="1.0.0",
            boot_layer="bundled",
            tenant_id="tenant_beta",
            operator_id="operator_beta",
        )

        # Disable plugin for tenant_beta
        await self.manager.disable_plugin(
            plugin_id="plugin.beta",
            tenant_id="tenant_beta",
            operator_id="operator_beta",
        )

        # Get audit logs for each tenant
        alpha_audit = self.manager.get_audit_log(tenant_id="tenant_alpha")
        beta_audit = self.manager.get_audit_log(tenant_id="tenant_beta")

        # Verify tenant_alpha audit log contains ONLY alpha events
        assert len(alpha_audit) > 0
        for event in alpha_audit:
            assert event["tenant_id"] == "tenant_alpha"
            assert event["plugin_id"] in ["plugin.alpha"]

        # Verify tenant_beta audit log contains ONLY beta events
        assert len(beta_audit) > 0
        for event in beta_audit:
            assert event["tenant_id"] == "tenant_beta"
            assert event["plugin_id"] in ["plugin.beta"]

        # Verify no cross-tenant leakage
        alpha_plugin_ids = [e["plugin_id"] for e in alpha_audit]
        beta_plugin_ids = [e["plugin_id"] for e in beta_audit]

        assert "plugin.beta" not in alpha_plugin_ids
        assert "plugin.alpha" not in beta_plugin_ids

    @pytest.mark.asyncio
    async def test_enable_plugin_tenant_scoped(self):
        """Verify enable_plugin is strictly tenant-scoped."""
        # Install plugin for tenant_alpha
        await self.manager.install_plugin(
            plugin_id="plugin.test",
            name="Test Plugin",
            version="1.0.0",
            boot_layer="bundled",
            tenant_id="tenant_alpha",
            operator_id="operator_1",
        )

        # tenant_beta cannot enable alpha's plugin
        result = await self.manager.enable_plugin(
            plugin_id="plugin.test",
            tenant_id="tenant_beta",
            operator_id="operator_2",
        )

        # Should fail because tenant_beta doesn't have this plugin
        assert result["status"] == "error"
        assert "not found" in result["message"]

    @pytest.mark.asyncio
    async def test_audit_event_includes_operator_id_from_session(self):
        """Verify audit events include operator_id from session, not hardcoded."""
        operator_id = "operator_custom_123"

        await self.manager.install_plugin(
            plugin_id="plugin.test",
            name="Test Plugin",
            version="1.0.0",
            boot_layer="bundled",
            tenant_id="tenant_test",
            operator_id=operator_id,
        )

        audit_log = self.manager.get_audit_log(tenant_id="tenant_test")
        assert len(audit_log) > 0

        # Verify operator_id is from session, not hardcoded
        event = audit_log[0]
        assert event["operator_id"] == operator_id
        assert event["operator_id"] != "default"
        assert event["operator_id"] != "unknown"


class TestChatLearningWrapperTenantIsolation:
    """Test chat learning wrapper enforces strict tenant isolation."""

    def test_chat_learning_wrapper_requires_explicit_tenant_id(self):
        """Verify tenant_id is REQUIRED, not defaulted to 'default'."""
        with pytest.raises(
            TypeError, match="missing 1 required positional argument"
        ):
            ChatLearningWrapper()  # No tenant_id → should fail

    def test_chat_learning_wrapper_rejects_empty_tenant_id(self):
        """Verify empty tenant_id is rejected (fail-closed)."""
        with pytest.raises(ValueError, match="tenant_id must be a non-empty string"):
            ChatLearningWrapper(tenant_id="")

    def test_chat_learning_wrapper_rejects_none_tenant_id(self):
        """Verify None tenant_id is rejected (fail-closed)."""
        with pytest.raises(ValueError, match="tenant_id must be a non-empty string"):
            ChatLearningWrapper(tenant_id=None)

    def test_get_chat_learning_wrapper_requires_explicit_tenant_id(self):
        """Verify get_chat_learning_wrapper requires explicit tenant_id."""
        with pytest.raises(
            TypeError, match="missing 1 required positional argument"
        ):
            get_chat_learning_wrapper()  # No tenant_id → should fail

    def test_get_chat_learning_wrapper_rejects_empty_tenant_id(self):
        """Verify get_chat_learning_wrapper rejects empty tenant_id."""
        with pytest.raises(ValueError, match="tenant_id must be a non-empty string"):
            get_chat_learning_wrapper(tenant_id="")

    def test_tenant_alpha_and_beta_have_separate_wrappers(self):
        """Verify different tenants get separate wrapper instances."""
        wrapper_alpha = get_chat_learning_wrapper(tenant_id="tenant_alpha")
        wrapper_beta = get_chat_learning_wrapper(tenant_id="tenant_beta")

        # Verify they are different instances
        assert wrapper_alpha is not wrapper_beta

        # Verify they have correct tenant_ids
        assert wrapper_alpha.tenant_id == "tenant_alpha"
        assert wrapper_beta.tenant_id == "tenant_beta"

    def test_same_tenant_returns_same_wrapper_instance(self):
        """Verify same tenant returns cached wrapper instance."""
        wrapper1 = get_chat_learning_wrapper(tenant_id="tenant_alpha")
        wrapper2 = get_chat_learning_wrapper(tenant_id="tenant_alpha")

        # Verify they are the same instance (singleton per tenant)
        assert wrapper1 is wrapper2


class TestVoiceOrchestrationTenantIsolation:
    """Test voice orchestration enforces strict tenant isolation."""

    def test_orchestration_event_requires_explicit_tenant_id(self):
        """Verify tenant_id is REQUIRED in OrchestrationCompleteEvent."""
        # Should fail without tenant_id
        with pytest.raises(
            TypeError, match="missing 1 required positional argument: 'tenant_id'"
        ):
            OrchestrationCompleteEvent(event_type="orchestration.completed")

    def test_orchestration_event_rejects_empty_tenant_id(self):
        """Verify empty tenant_id is rejected (fail-closed)."""
        with pytest.raises(ValueError, match="tenant_id must be a non-empty string"):
            OrchestrationCompleteEvent(
                event_type="orchestration.completed", tenant_id=""
            )

    def test_orchestration_event_rejects_none_tenant_id(self):
        """Verify None tenant_id is rejected (fail-closed)."""
        with pytest.raises(ValueError, match="tenant_id must be a non-empty string"):
            OrchestrationCompleteEvent(
                event_type="orchestration.completed", tenant_id=None
            )

    def test_orchestration_event_with_valid_tenant_id(self):
        """Verify valid tenant_id is accepted."""
        event = OrchestrationCompleteEvent(
            event_type="orchestration.completed",
            tenant_id="tenant_alpha",
            tasks=[
                TaskResult(task_name="task1", status="success", duration_seconds=1.5)
            ],
        )

        assert event.tenant_id == "tenant_alpha"
        assert event.event_type == "orchestration.completed"
        assert len(event.tasks) == 1

    def test_different_tenants_have_isolated_events(self):
        """Verify events from different tenants are isolated."""
        event_alpha = OrchestrationCompleteEvent(
            event_type="orchestration.completed",
            tenant_id="tenant_alpha",
            tasks=[
                TaskResult(
                    task_name="task_alpha", status="success", duration_seconds=1.0
                )
            ],
        )

        event_beta = OrchestrationCompleteEvent(
            event_type="orchestration.completed",
            tenant_id="tenant_beta",
            tasks=[
                TaskResult(
                    task_name="task_beta", status="success", duration_seconds=2.0
                )
            ],
        )

        # Verify complete isolation
        assert event_alpha.tenant_id != event_beta.tenant_id
        assert event_alpha.tasks[0].task_name != event_beta.tasks[0].task_name


class TestHardcodedValueElimination:
    """Verify all hardcoded tenant_id/operator_id values are eliminated."""

    def test_no_hardcoded_tenant_id_in_plugin_manager(self):
        """Verify plugin_manager.py has no hardcoded tenant_id."""
        manager_path = Path(__file__).parent.parent / "control_plane" / "plugin_manager.py"
        content = manager_path.read_text()

        # Should not have hardcoded tenant_id="default"
        assert 'tenant_id="default"' not in content
        assert "tenant_id = \"default\"" not in content
        assert "tenant_id: str = \"default\"" not in content

    def test_no_hardcoded_tenant_id_in_chat_learning_wrapper(self):
        """Verify chat_learning_wrapper.py has no hardcoded tenant_id defaults."""
        wrapper_path = (
            Path(__file__).parent.parent / "chat_learning_wrapper.py"
        )
        content = wrapper_path.read_text()

        # Should not have hardcoded tenant_id="default" as parameter default
        assert "def __init__(self, tenant_id: str = \"default\")" not in content
        assert "def get_chat_learning_wrapper(tenant_id: str = \"default\")" not in content

    def test_no_hardcoded_tenant_id_in_voice_orchestration(self):
        """Verify voice_summary_orchestration.py has no hardcoded tenant_id."""
        voice_path = (
            Path(__file__).parent.parent / "voice_summary_orchestration.py"
        )
        content = voice_path.read_text()

        # Should not have hardcoded tenant_id="default" in dataclass
        assert 'tenant_id: str = "default"' not in content


class TestAuditLogImmutability:
    """Test audit log immutability and tenant-scoped storage."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry_path = Path(self.temp_dir.name) / "plugins.json"
        self.manager = PluginManager(registry_path=self.registry_path)

    def teardown_method(self):
        """Clean up test fixtures."""
        self.temp_dir.cleanup()

    @pytest.mark.asyncio
    async def test_audit_log_records_events_in_order(self):
        """Verify audit log records events in chronological order."""
        # Install first plugin
        await self.manager.install_plugin(
            plugin_id="plugin.first",
            name="First Plugin",
            version="1.0.0",
            boot_layer="bundled",
            tenant_id="tenant_alpha",
            operator_id="operator_1",
        )

        # Install second plugin
        await self.manager.install_plugin(
            plugin_id="plugin.second",
            name="Second Plugin",
            version="2.0.0",
            boot_layer="bundled",
            tenant_id="tenant_alpha",
            operator_id="operator_1",
        )

        # Get audit log
        audit_log = self.manager.get_audit_log(tenant_id="tenant_alpha")

        # Verify events are recorded in order
        assert len(audit_log) == 2
        assert audit_log[0]["plugin_id"] == "plugin.first"
        assert audit_log[1]["plugin_id"] == "plugin.second"
        assert audit_log[0]["event_type"] == "plugin_installed"
        assert audit_log[1]["event_type"] == "plugin_installed"

    @pytest.mark.asyncio
    async def test_audit_events_include_tenant_id(self):
        """Verify every audit event includes tenant_id."""
        await self.manager.install_plugin(
            plugin_id="plugin.test",
            name="Test Plugin",
            version="1.0.0",
            boot_layer="bundled",
            tenant_id="tenant_alpha",
            operator_id="operator_1",
        )

        audit_log = self.manager.get_audit_log(tenant_id="tenant_alpha")

        # Verify every event has tenant_id
        for event in audit_log:
            assert "tenant_id" in event
            assert event["tenant_id"] == "tenant_alpha"
            assert event["tenant_id"] is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
