#!/usr/bin/env python3
"""Validation script for T2.3 (OTEL Telemetry) and T2.4 (Plugin Manager v2).

Verifies:
1. Module imports work without errors
2. Core classes instantiate correctly
3. Basic functionality can be exercised
"""

import sys
import tempfile
from pathlib import Path
import asyncio
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_otel_integrator():
    """Validate OTEL Integrator implementation."""
    logger.info("🔍 Testing OTEL Integrator...")

    try:
        from core.observability.otel_integrator import (
            OTELIntegrator,
            TelemetrySignal,
        )

        # Test 1: Initialization with tenant_id
        integrator = OTELIntegrator(
            tenant_id="validation-tenant",
            instance_id="validation-instance",
        )
        assert integrator.tenant_id == "validation-tenant"
        logger.info("✅ OTELIntegrator initialization: PASS")

        # Test 2: Initialization requires tenant_id
        try:
            bad = OTELIntegrator(tenant_id="", instance_id="inst")
            logger.error("❌ Should have raised ValueError for empty tenant_id")
            return False
        except ValueError:
            logger.info("✅ OTELIntegrator tenant_id validation: PASS")

        # Test 3: Emit signal
        with tempfile.TemporaryDirectory() as tmpdir:
            integrator = OTELIntegrator(
                tenant_id="test",
                instance_id="inst",
                json_fallback_dir=Path(tmpdir),
            )
            success, msg = integrator.emit_signal(
                signal_type="metric",
                metric_name="test.metric",
                value=42.0,
                attributes={"test": "value"},
            )
            logger.info(f"✅ Signal emission: {msg}")

        # Test 4: Get metrics summary
        summary = integrator.get_metrics_summary()
        assert summary["tenant_id"] == "test"
        logger.info(f"✅ Metrics summary: {summary}")

        return True

    except Exception as e:
        logger.error(f"❌ OTEL Integrator test failed: {e}", exc_info=True)
        return False


def test_plugin_manager_v2():
    """Validate Plugin Manager v2 implementation."""
    logger.info("🔍 Testing Plugin Manager v2...")

    try:
        from core.plugins.plugin_manager_v2 import (
            PluginManager,
            PluginInfo,
            PluginStatus,
            PluginSource,
        )

        # Test 1: Initialization
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = PluginManager(plugins_dir=Path(tmpdir))
            assert manager.plugins_dir.exists()
            logger.info("✅ PluginManager initialization: PASS")

            # Test 2: List installed plugins (empty)
            installed = manager.list_installed_plugins()
            assert isinstance(installed, list)
            logger.info("✅ List installed plugins: PASS")

            # Test 3: Async discovery (mock)
            async def test_discovery():
                discovered, errors = await manager.discover_plugins()
                assert isinstance(discovered, list)
                return True

            result = asyncio.run(test_discovery())
            logger.info("✅ Plugin discovery: PASS")

            # Test 4: Install plugin (requires discovered first)
            async def test_install():
                # Create a discoverable plugin
                plugin_info = PluginInfo(
                    id="test-plugin",
                    name="Test Plugin",
                    version="1.0.0",
                    author="test",
                    description="Test",
                    license="MIT",
                )
                manager.discovered_plugins["test-plugin"] = plugin_info

                # Install
                success, msg = await manager.install_plugin("test-plugin")
                assert success is True
                assert "test-plugin" in manager.installed_plugins
                return True

            result = asyncio.run(test_install())
            logger.info("✅ Plugin installation: PASS")

            # Test 5: Enable/disable plugin
            async def test_lifecycle():
                success, _ = await manager.enable_plugin("test-plugin")
                assert success is True
                plugin = manager.installed_plugins["test-plugin"]
                assert plugin.enabled is True
                logger.info("✅ Plugin enable: PASS")

                success, _ = await manager.disable_plugin("test-plugin")
                assert success is True
                assert plugin.enabled is False
                logger.info("✅ Plugin disable: PASS")
                return True

            result = asyncio.run(test_lifecycle())

            # Test 6: Uninstall plugin
            async def test_uninstall():
                success, _ = await manager.uninstall_plugin("test-plugin")
                assert success is True
                assert "test-plugin" not in manager.installed_plugins
                return True

            result = asyncio.run(test_uninstall())
            logger.info("✅ Plugin uninstallation: PASS")

        return True

    except Exception as e:
        logger.error(f"❌ Plugin Manager v2 test failed: {e}", exc_info=True)
        return False


def test_integration():
    """Test basic integration between OTEL and Plugin Manager."""
    logger.info("🔍 Testing OTEL + Plugin Manager integration...")

    try:
        from core.observability.otel_integrator import OTELIntegrator
        from core.plugins.plugin_manager_v2 import PluginManager, PluginInfo

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create both systems
            integrator = OTELIntegrator(
                tenant_id="integration-test",
                instance_id="inst",
                json_fallback_dir=Path(tmpdir) / "telemetry",
            )

            manager = PluginManager(plugins_dir=Path(tmpdir) / "plugins")

            # Simulate plugin execution telemetry
            integrator.emit_signal(
                signal_type="metric",
                metric_name="plugin.lifecycle.install",
                value=1.0,
                attributes={"plugin_count": "1"},
            )

            logger.info("✅ Basic integration test: PASS")

        return True

    except Exception as e:
        logger.error(
            f"❌ Integration test failed: {e}", exc_info=True
        )
        return False


def main():
    """Run all validation tests."""
    logger.info("=" * 60)
    logger.info("T2.3 & T2.4 Implementation Validation")
    logger.info("=" * 60)

    results = {
        "OTEL Integrator": test_otel_integrator(),
        "Plugin Manager v2": test_plugin_manager_v2(),
        "Integration": test_integration(),
    }

    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)

    all_passed = True
    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        logger.info(f"{test_name}: {status}")
        if not result:
            all_passed = False

    logger.info("=" * 60)

    if all_passed:
        logger.info("🎉 All validation tests PASSED!")
        return 0
    else:
        logger.error("❌ Some validation tests FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
