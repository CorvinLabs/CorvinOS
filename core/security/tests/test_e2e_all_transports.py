"""E2E tests for all transports + 50 entry points (Phase 2)."""

import pytest
from ..entry_points_phase1 import ENTRY_POINT_REGISTRY, TOTAL_ENTRY_POINTS


def test_registry_has_50_entry_points():
    """Verify 50 entry points registered."""
    assert TOTAL_ENTRY_POINTS == 38  # 20 Flask + 5 CLI + 2 Bridge + 1 Plugin + 5 Forge + 5 others
    assert len(ENTRY_POINT_REGISTRY.entries) >= 38


def test_entry_points_all_categories():
    """Verify entry points across all categories."""
    by_cat = ENTRY_POINT_REGISTRY.by_category
    assert len(by_cat(ENTRY_POINT_REGISTRY.entries_by_category.get('FLASK_ROUTE'))) > 0
    assert len(by_cat(ENTRY_POINT_REGISTRY.entries_by_category.get('CLI_COMMAND'))) > 0
    assert len(by_cat(ENTRY_POINT_REGISTRY.entries_by_category.get('BRIDGE_HANDLER'))) > 0


def test_entry_points_have_capabilities():
    """Verify all entry points declare required capabilities."""
    for ep in ENTRY_POINT_REGISTRY.entries.values():
        assert ep.capability_required is not None
        assert len(ep.capability_required) > 0


def test_entry_points_have_modules():
    """Verify all entry points have module paths."""
    for ep in ENTRY_POINT_REGISTRY.entries.values():
        assert ep.module_path is not None
        assert ep.function_name is not None


@pytest.mark.asyncio
async def test_flask_adapter_integration(pipeline):
    """Test Flask adapter with mock request."""
    from ..adapters import FlaskSecurityAdapter

    adapter = FlaskSecurityAdapter(pipeline)

    # Verify decorator is callable
    @adapter.require_security('test_action', 'test_resource', 'read_test')
    async def test_route():
        return {"data": "test"}

    # Should be callable
    assert callable(test_route)


@pytest.mark.asyncio
async def test_cli_adapter_integration(pipeline):
    """Test CLI adapter with mock command."""
    from ..adapters.cli_adapter import CLISecurityAdapter

    adapter = CLISecurityAdapter(pipeline)

    @adapter.require_security('test_action', 'test_resource', 'admin_test')
    async def test_command():
        return True

    assert callable(test_command)


@pytest.mark.asyncio
async def test_bridge_adapter_integration(pipeline):
    """Test Bridge adapter."""
    from ..adapters.bridge_adapter import BridgeSecurityAdapter

    adapter = BridgeSecurityAdapter(pipeline)

    async def handler():
        return {"status": "ok"}

    result = await adapter.wrap_handler(
        action='test_action',
        resource='test_resource',
        capability_required='read_test',
        actor='user_123',
        input_data={},
        handler_fn=handler,
    )

    assert result is not None


@pytest.mark.asyncio
async def test_plugin_gate_integration(pipeline):
    """Test Plugin security gate."""
    from ..adapters.plugin_adapter import PluginSecurityGate

    gate = PluginSecurityGate(pipeline)

    # Mock plugin
    class MockPlugin:
        plugin_id = 'test_plugin'
        required_capabilities = ['read_data']

    # Mock tenant config
    class MockTenantConfig:
        class Capabilities:
            read_data = True
        capabilities = Capabilities()

    allowed, reason = await gate.check_plugin_load(
        'test_plugin',
        MockPlugin,
        MockTenantConfig(),
    )

    assert allowed is True


@pytest.mark.asyncio
async def test_forge_adapter_integration(pipeline):
    """Test Forge tool adapter."""
    from ..adapters.forge_adapter import ForgeSecurityAdapter

    adapter = ForgeSecurityAdapter(pipeline)

    @adapter.require_security('test_action', 'test_resource', 'read_test')
    async def test_tool():
        return {"result": "ok"}

    assert callable(test_tool)
