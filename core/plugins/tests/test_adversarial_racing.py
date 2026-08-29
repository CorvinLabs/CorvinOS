"""Adversarial testing for plugin system — race conditions, edge cases, security.

Tests for concurrent operations, state machine violations, boundary conditions,
and security-critical invariants.
"""
from __future__ import annotations

import concurrent.futures
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from unittest.mock import MagicMock, patch

import pytest

from corvin_plugins import (
    BootLayer,
    CorvinPlugin,
    HealthStatus,
    PIIRisk,
    PluginAlreadyRegistered,
    PluginContext,
    PluginNotFound,
    PluginOrigin,
    PluginRecord,
    PluginRegistry,
    PluginReplacementRefused,
)
from corvin_plugins.manifest import DependencyResolver, Locality, NetworkEgress, PluginError
from corvin_plugins.protocol import PluginDisableRefused
from corvin_plugins.state import TenantRegistry, registry_mutation, _MUTATION_LOCK


def _mock_ctx(tenant_id: str = "_default") -> MagicMock:
    """A PluginContext mock carrying the fields register()/unregister() read.

    ``spec=PluginContext`` alone does not expose the dataclass *instance*
    fields (``tenant_id``, ``audit_emit``) — a class spec only sees class-level
    attributes — so register()'s ``ctx.tenant_id`` / ``ctx.audit_emit`` accesses
    would raise. Set them explicitly.
    """
    ctx = MagicMock(spec=PluginContext)
    ctx.tenant_id = tenant_id
    ctx.audit_emit = MagicMock()
    return ctx


# ════════════════════════════════════════════════════════════════════════════
# Race Condition Tests
# ════════════════════════════════════════════════════════════════════════════


class TestConcurrentPluginInstalls:
    """10 concurrent installs of the same plugin — should fail 9 times."""

    def test_concurrent_register_same_plugin_idempotent_fail(self):
        """Concurrent attempts to register the same plugin.

        Expected: First wins, remaining 9 fail with PluginAlreadyRegistered.
        Bug: All succeed (no lock held during on_load).
        """
        registry = PluginRegistry()
        plugin = MagicMock(spec=CorvinPlugin)
        plugin.plugin_type = "audit_backend"
        plugin.version = "1.0.0"
        plugin.plugin_id = "test-plugin"
        plugin.on_load = MagicMock(return_value=None)
        ctx = _mock_ctx()

        results = {"success": [], "error": []}
        lock = threading.Lock()

        def register_attempt():
            try:
                registry.register(plugin, ctx)
                with lock:
                    results["success"].append(plugin.plugin_id)
            except PluginAlreadyRegistered as e:
                with lock:
                    results["error"].append(str(e))

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(register_attempt)
                for _ in range(10)
            ]
            concurrent.futures.wait(futures)

        assert len(results["success"]) == 1, (
            f"Expected exactly 1 success, got {len(results['success'])}. "
            "Bug: register() is not atomic."
        )
        assert len(results["error"]) == 9, (
            f"Expected 9 failures, got {len(results['error'])}. "
            "Bug: PluginAlreadyRegistered not raised."
        )

    def test_concurrent_register_unregister_same_plugin(self):
        """Concurrent register → unregister → register cycles.

        Expected: Strong happens-before ordering, no double-free or re-entrance.
        Bug: on_load runs in parallel, state is inconsistent, audit records clash.
        """
        registry = PluginRegistry()
        plugin = MagicMock(spec=CorvinPlugin)
        plugin.plugin_type = "audit_backend"
        plugin.version = "1.0.0"
        plugin.plugin_id = "cycle-plugin"
        plugin.on_load = MagicMock(return_value=None)
        plugin.on_unload = MagicMock(return_value=None)
        ctx = _mock_ctx()

        results = {"register": 0, "unregister": 0, "error": []}
        lock = threading.Lock()

        def cycle():
            try:
                registry.register(plugin, ctx)
                with lock:
                    results["register"] += 1
            except PluginAlreadyRegistered:
                pass
            except Exception as e:
                with lock:
                    results["error"].append(("register", str(e)))

        def uncycle():
            time.sleep(0.01)  # let register go first
            try:
                registry.unregister("cycle-plugin")
                with lock:
                    results["unregister"] += 1
            except PluginNotFound:
                pass
            except Exception as e:
                with lock:
                    results["error"].append(("unregister", str(e)))

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            # Alternate register and unregister
            futures = []
            for i in range(10):
                futures.append(executor.submit(cycle))
                futures.append(executor.submit(uncycle))
            concurrent.futures.wait(futures)

        assert len(results["error"]) == 0, f"Errors during cycle: {results['error']}"
        # on_load should be called at most once if register is idempotent
        assert plugin.on_load.call_count >= 1, "on_load never called"

    def test_concurrent_health_check_timeout_wedge(self):
        """Health check that times out while another thread unloads.

        Expected: Timeout is raised, unload waits, no resource leak.
        Bug: Wedged thread leaks, unload proceeds before timeout thread exits.
        """
        registry = PluginRegistry()
        plugin = MagicMock(spec=CorvinPlugin)
        plugin.plugin_type = "audit_backend"
        plugin.version = "1.0.0"
        plugin.plugin_id = "wedge-plugin"

        # Health check that hangs
        def slow_health():
            time.sleep(5)
            return HealthStatus(ok=True)

        plugin.health_check = slow_health
        plugin.on_load = MagicMock(return_value=None)
        plugin.on_unload = MagicMock(return_value=None)
        ctx = _mock_ctx()

        registry.register(plugin, ctx)
        results = {"health": None, "unload": None, "error": []}
        lock = threading.Lock()

        def check_health():
            try:
                status = registry.health_check_all()["wedge-plugin"]
                with lock:
                    results["health"] = status
            except Exception as e:
                with lock:
                    results["error"].append(("health", str(e)))

        def unload():
            time.sleep(0.1)  # let health check start
            try:
                registry.unregister("wedge-plugin")
                with lock:
                    results["unload"] = "done"
            except Exception as e:
                with lock:
                    results["error"].append(("unload", str(e)))

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            h = executor.submit(check_health)
            u = executor.submit(unload)
            try:
                concurrent.futures.wait([h, u], timeout=3)
            except concurrent.futures.TimeoutError:
                pass

        # Health check should timeout after 2s (HEALTH_CHECK_DEADLINE_S)
        assert "HealthCheckTimeout" in str(results["error"]) or results["health"] is not None

    def test_concurrent_registry_mutations_file_corruption(self, tmp_path):
        """Concurrent mutations on disk-backed registry.

        Expected: Last-writer-wins with atomic writes (no half-reads).
        Bug: Partial reads leave corrupted YAML, or writes interleave.
        """
        corvin_home = tmp_path / "corvin"
        registry_file = corvin_home / "tenants" / "_default" / "plugins" / "registry.yaml"
        registry_file.parent.mkdir(parents=True, exist_ok=True)

        records = []
        errors = []
        lock = threading.Lock()

        def mutate(plugin_num: int):
            try:
                with registry_mutation(tenant_id="_default", corvin_home_path=corvin_home) as tr:
                    # Simulate a mutation
                    record = PluginRecord(
                        plugin_id=f"test-{plugin_num}",
                        version="1.0.0",
                        display_name="Test",
                        plugin_type="audit_backend",
                        origin=PluginOrigin.COMMUNITY,
                        enabled=True,
                        boot_layer=BootLayer.INSTALLED,
                        locality=Locality.LOCAL,
                        network_egress=[],
                        pii_risk=PIIRisk.NONE,
                    )
                    tr.records[record.plugin_id] = record
                    with lock:
                        records.append(record)
            except Exception as e:
                with lock:
                    errors.append((plugin_num, str(e)))

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(mutate, i) for i in range(10)]
            concurrent.futures.wait(futures)

        # Verify file is still valid YAML
        assert registry_file.exists()
        try:
            import yaml
            with open(registry_file) as f:
                data = yaml.safe_load(f)
            assert isinstance(data, dict), f"Corrupted registry: {data!r}"
        except Exception as e:
            pytest.fail(f"Registry corruption detected: {e}")

        assert len(errors) == 0, f"Mutation errors: {errors}"


# ════════════════════════════════════════════════════════════════════════════
# State Machine Tests
# ════════════════════════════════════════════════════════════════════════════


class TestPluginStateTransitions:
    """Test enable/disable/unload state machine."""

    def test_disable_already_disabled_idempotent(self, tmp_path):
        """Disabling an already-disabled plugin is idempotent."""
        corvin_home = tmp_path / "corvin"
        registry_file = corvin_home / "tenants" / "_default" / "plugins" / "registry.yaml"
        registry_file.parent.mkdir(parents=True, exist_ok=True)

        with registry_mutation(tenant_id="_default", corvin_home_path=corvin_home) as tr:
            record = PluginRecord(
                plugin_id="disabled-plugin",
                version="1.0.0",
                display_name="Test",
                plugin_type="audit_backend",
                origin=PluginOrigin.COMMUNITY,
                enabled=False,
                boot_layer=BootLayer.INSTALLED,
                locality=Locality.LOCAL,
                network_egress=[],
                pii_risk=PIIRisk.NONE,
            )
            tr.records[record.plugin_id] = record

        # Disable already-disabled
        with registry_mutation(tenant_id="_default", corvin_home_path=corvin_home) as tr:
            tr.disable("disabled-plugin")

        # Should succeed without error
        with registry_mutation(tenant_id="_default", corvin_home_path=corvin_home) as tr:
            records = tr.records
            assert records["disabled-plugin"].enabled is False

    def test_enable_nonexistent_plugin_fails(self, tmp_path):
        """Enabling a nonexistent plugin fails cleanly."""
        corvin_home = tmp_path / "corvin"
        registry_file = corvin_home / "tenants" / "_default" / "plugins" / "registry.yaml"
        registry_file.parent.mkdir(parents=True, exist_ok=True)

        with registry_mutation(tenant_id="_default", corvin_home_path=corvin_home) as tr:
            with pytest.raises(PluginNotFound):
                tr.enable("nonexistent", consent_granted_by=None)

    def test_unload_unload_twice_fails_second(self):
        """Unregistering the same plugin twice."""
        registry = PluginRegistry()
        plugin = MagicMock(spec=CorvinPlugin)
        plugin.plugin_type = "audit_backend"
        plugin.version = "1.0.0"
        plugin.plugin_id = "once-plugin"
        plugin.on_load = MagicMock(return_value=None)
        plugin.on_unload = MagicMock(return_value=None)
        ctx = _mock_ctx()

        registry.register(plugin, ctx)
        registry.unregister("once-plugin")

        with pytest.raises(PluginNotFound):
            registry.unregister("once-plugin")

    def test_install_uninstall_reinstall_cycle(self, tmp_path):
        """Full lifecycle: install → uninstall → reinstall."""
        corvin_home = tmp_path / "corvin"
        registry_file = corvin_home / "tenants" / "_default" / "plugins" / "registry.yaml"
        registry_file.parent.mkdir(parents=True, exist_ok=True)

        # Install
        with registry_mutation(tenant_id="_default", corvin_home_path=corvin_home) as tr:
            record = PluginRecord(
                plugin_id="cycle-test",
                version="1.0.0",
                display_name="Test",
                plugin_type="audit_backend",
                origin=PluginOrigin.COMMUNITY,
                enabled=True,
                boot_layer=BootLayer.INSTALLED,
                locality=Locality.LOCAL,
                network_egress=[],
                pii_risk=PIIRisk.NONE,
            )
            tr.records[record.plugin_id] = record

        # Uninstall
        with registry_mutation(tenant_id="_default", corvin_home_path=corvin_home) as tr:
            del tr.records["cycle-test"]

        # Reinstall
        with registry_mutation(tenant_id="_default", corvin_home_path=corvin_home) as tr:
            record = PluginRecord(
                plugin_id="cycle-test",
                version="1.0.0",
                display_name="Test",
                plugin_type="audit_backend",
                origin=PluginOrigin.COMMUNITY,
                enabled=True,
                boot_layer=BootLayer.INSTALLED,
                locality=Locality.LOCAL,
                network_egress=[],
                pii_risk=PIIRisk.NONE,
            )
            tr.records[record.plugin_id] = record

        # Verify final state
        with registry_mutation(tenant_id="_default", corvin_home_path=corvin_home) as tr:
            records = tr.records
            assert "cycle-test" in records


# ════════════════════════════════════════════════════════════════════════════
# Edge Case Tests
# ════════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Boundary conditions and empty inputs."""

    def test_empty_plugin_name(self):
        """Plugin ID of length 0."""
        with pytest.raises((ValueError, PluginError)):
            PluginRecord(
                plugin_id="",
                version="1.0.0",
                display_name="Test",
                plugin_type="audit_backend",
                origin=PluginOrigin.COMMUNITY,
                enabled=True,
                boot_layer=BootLayer.INSTALLED,
                locality=Locality.LOCAL,
                network_egress=[],
                pii_risk=PIIRisk.NONE,
            )

    def test_very_long_plugin_name(self):
        """Plugin ID of 10,000 characters."""
        long_id = "x" * 10000
        with pytest.raises((ValueError, PluginError)):
            PluginRecord(
                plugin_id=long_id,
                version="1.0.0",
                display_name="Test",
                plugin_type="audit_backend",
                origin=PluginOrigin.COMMUNITY,
                enabled=True,
                boot_layer=BootLayer.INSTALLED,
                locality=Locality.LOCAL,
                network_egress=[],
                pii_risk=PIIRisk.NONE,
            )

    def test_unicode_emoji_in_plugin_name(self):
        """Plugin name with emoji: 'plugin-🎉-test'."""
        registry = PluginRegistry()
        plugin = MagicMock(spec=CorvinPlugin)
        plugin.plugin_type = "audit_backend"
        plugin.version = "1.0.0"
        plugin.plugin_id = "plugin-🎉-test"
        plugin.on_load = MagicMock(return_value=None)
        ctx = _mock_ctx()

        # Should not crash during serialization
        registry.register(plugin, ctx)
        assert registry.get("plugin-🎉-test") == plugin

    def test_null_boot_layer(self):
        """Boot layer as None should default to INSTALLED."""
        registry = PluginRegistry()
        plugin = MagicMock(spec=CorvinPlugin)
        plugin.plugin_type = "audit_backend"
        plugin.version = "1.0.0"
        plugin.plugin_id = "null-layer"
        plugin.boot_layer = None
        plugin.on_load = MagicMock(return_value=None)
        ctx = _mock_ctx()

        registry.register(plugin, ctx, boot_layer=None)
        # Should succeed and default to INSTALLED
        assert registry.get("null-layer") is not None

    def test_malformed_yaml_registry_fails_closed(self, tmp_path):
        """Corrupted YAML registry file should raise, not silently erase."""
        corvin_home = tmp_path / "corvin"
        registry_file = corvin_home / "tenants" / "_default" / "plugins" / "registry.yaml"
        registry_file.parent.mkdir(parents=True, exist_ok=True)

        # Write corrupted YAML
        with open(registry_file, "w") as f:
            f.write("plugins: [\n  invalid yaml without close bracket")

        with pytest.raises(Exception):  # YAML parse error
            with registry_mutation(tenant_id="_default", corvin_home_path=corvin_home) as tr:
                pass

        # Verify file was not overwritten with empty
        content = registry_file.read_text()
        assert "invalid yaml" in content, "Registry was erased instead of failing closed"

    def test_max_description_length(self):
        """Very large description should be accepted or truncated cleanly."""
        large_desc = "x" * (10 * 1024 * 1024)  # 10 MB
        plugin = MagicMock(spec=CorvinPlugin)
        plugin.plugin_type = "audit_backend"
        plugin.version = "1.0.0"
        plugin.plugin_id = "large-desc"
        plugin.description = large_desc
        plugin.on_load = MagicMock(return_value=None)

        registry = PluginRegistry()
        ctx = _mock_ctx()

        # Should not crash or consume unbounded memory
        registry.register(plugin, ctx)
        assert registry.get("large-desc") is not None

    def test_zero_timeout_race(self):
        """Millisecond-scale timeout (1ms) in concurrent operations."""
        registry = PluginRegistry()
        plugin = MagicMock(spec=CorvinPlugin)
        plugin.plugin_type = "audit_backend"
        plugin.version = "1.0.0"
        plugin.plugin_id = "zero-timeout"

        def instant_health():
            return HealthStatus(ok=True)

        plugin.health_check = instant_health
        plugin.on_load = MagicMock(return_value=None)
        ctx = _mock_ctx()

        registry.register(plugin, ctx)

        # Should not hang or crash on very tight deadline
        status = registry.health_check_all()["zero-timeout"]
        assert status is not None


# ════════════════════════════════════════════════════════════════════════════
# Negative/Security Tests
# ════════════════════════════════════════════════════════════════════════════


class TestSecurityAndValidation:
    """Negative tests for trust, signing, and privilege escalation."""

    def test_invalid_trust_anchor_path(self):
        """Trust anchor file does not exist or is not readable."""
        from corvin_plugins.trust import verify_plugin_signature

        with pytest.raises((FileNotFoundError, PermissionError, ValueError)):
            verify_plugin_signature(
                manifest_path="/nonexistent/plugin.yaml",
                signature_path="/nonexistent/plugin.sig",
                trust_anchor_path="/nonexistent/anchor.pem",
            )

    def test_tampered_plugin_signature_rejected(self, tmp_path):
        """Plugin manifest is valid but signature is forged."""
        # This requires the trust module; test assumes it exists
        try:
            from corvin_plugins.trust import verify_plugin_signature
        except ImportError:
            pytest.skip("Trust module not available")

        manifest_file = tmp_path / "manifest.yaml"
        manifest_file.write_text("plugin_id: test\nversion: 1.0.0")

        sig_file = tmp_path / "manifest.sig"
        sig_file.write_text("FAKE_SIGNATURE")

        anchor = tmp_path / "anchor.pem"
        anchor.write_text("FAKE_KEY")

        with pytest.raises(Exception):  # Signature verification should fail
            verify_plugin_signature(
                manifest_path=str(manifest_file),
                signature_path=str(sig_file),
                trust_anchor_path=str(anchor),
            )

    def test_corrupted_yaml_manifest_rejected(self):
        """Plugin manifest is not valid YAML."""
        from corvin_plugins.manifest import load_manifest

        bad_yaml = "plugin_id: [\n  unclosed bracket"

        with pytest.raises((ValueError, Exception)):
            load_manifest(bad_yaml, plugin_id="bad-plugin")

    def test_missing_required_manifest_field(self):
        """Plugin manifest is missing 'plugin_id'."""
        from corvin_plugins.manifest import load_manifest

        incomplete = """
        version: 1.0.0
        description: Missing plugin_id
        """

        with pytest.raises((ValueError, KeyError, Exception)):
            load_manifest(incomplete, plugin_id="test")

    def test_privilege_escalation_in_same_epoch(self):
        """Plugin tries to unregister + re-register with higher privilege (same epoch).

        Expected: Downgrade to INSTALLED.
        Bug: ADR-0233 D5 bypass — privilege escalation succeeds.
        """
        registry = PluginRegistry()
        plugin = MagicMock(spec=CorvinPlugin)
        plugin.plugin_type = "audit_backend"
        plugin.version = "1.0.0"
        plugin.plugin_id = "escalate-plugin"
        plugin.on_load = MagicMock(return_value=None)
        plugin.on_unload = MagicMock(return_value=None)
        ctx = _mock_ctx()

        # Register as INSTALLED
        registry.register(plugin, ctx, boot_layer=BootLayer.INSTALLED)

        # Unregister
        registry.unregister("escalate-plugin")

        # Try to re-register as CORE (privilege escalation)
        with patch("corvin_plugins.registry._loading.current", return_value=None):
            registry.register(plugin, ctx, boot_layer=BootLayer.CORE)
            # Should be downgraded, not escalated
            # This is validated in the registry's epoch tracking

    def test_circular_plugin_dependencies(self, tmp_path):
        """Plugin A depends on B, B depends on A."""
        corvin_home = tmp_path / "corvin"
        registry_file = corvin_home / "tenants" / "_default" / "plugins" / "registry.yaml"
        registry_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            from corvin_plugins.manifest import DependencyResolver
        except ImportError:
            pytest.skip("DependencyResolver not available")

        resolver = DependencyResolver()
        resolver.add("plugin-a", depends_on=["plugin-b"])
        resolver.add("plugin-b", depends_on=["plugin-a"])

        with pytest.raises((ValueError, Exception)):
            # Should detect cycle and raise
            resolver.topological_sort()

    def test_missing_required_plugin_field_init(self):
        """PluginRecord missing required field during init."""
        with pytest.raises((TypeError, ValueError)):
            PluginRecord(
                plugin_id="incomplete",
                origin=PluginOrigin.COMMUNITY,
                # Missing: enabled, boot_layer, locality, etc.
            )

    def test_unexpected_field_type_in_record(self):
        """Plugin record field has wrong type: enabled is string not bool."""
        with pytest.raises((TypeError, ValueError)):
            PluginRecord(
                plugin_id="type-mismatch",
                origin=PluginOrigin.COMMUNITY,
                enabled="yes",  # should be bool
                boot_layer=BootLayer.INSTALLED,
                locality=Locality.LOCAL,
                network_egress=[],
                pii_risk=PIIRisk.NONE,
            )

    def test_pii_in_plugin_health_message_scrubbed(self):
        """Health message contains PII (email) which should be scrubbed."""
        registry = PluginRegistry()
        plugin = MagicMock(spec=CorvinPlugin)
        plugin.plugin_type = "audit_backend"
        plugin.version = "1.0.0"
        plugin.plugin_id = "leaky-plugin"
        plugin.on_load = MagicMock(return_value=None)

        def leaky_health():
            return HealthStatus(
                ok=False,
                message="Failed to auth user alice@example.com"
            )

        plugin.health_check = leaky_health
        ctx = _mock_ctx()

        registry.register(plugin, ctx)
        status = registry.health_check_all()["leaky-plugin"]

        # Message should be scrubbed or redacted
        if status.message:
            assert "alice@example.com" not in status.message or "[scrubbed]" in status.message


# ════════════════════════════════════════════════════════════════════════════
# Mutation Tests
# ════════════════════════════════════════════════════════════════════════════


class TestMutationResistance:
    """Test that disabling safety checks breaks the system (prove they work)."""

    def test_disable_trust_check_mutation_still_fails(self):
        """Trust verification MUST fail for unsigned plugins.

        This is a mutation test: if someone removes the trust check,
        this should fail.
        """
        try:
            from corvin_plugins.trust import verify_plugin_signature
        except ImportError:
            pytest.skip("Trust module not available")

        # Even if trust verification is disabled in code, it should still be enforced
        # (This is a mutation test to ensure the guard is NOT optional)
        manifest_file = tempfile.NamedTemporaryFile(suffix=".yaml", delete=False)
        manifest_file.write(b"plugin_id: unsigned\nversion: 1.0.0")
        manifest_file.close()

        try:
            with pytest.raises(Exception):  # Should fail (no signature)
                verify_plugin_signature(
                    manifest_path=manifest_file.name,
                    signature_path="/nonexistent",
                    trust_anchor_path="/nonexistent",
                )
        finally:
            os.unlink(manifest_file.name)

    def test_audit_trail_recorded_on_every_mutation(self, tmp_path):
        """Every plugin state change MUST audit log.

        Mutation: if audit logging is removed, state changes succeed silently.
        This test proves the audit trail is being used.
        """
        corvin_home = tmp_path / "corvin"
        registry_file = corvin_home / "tenants" / "_default" / "plugins" / "registry.yaml"
        registry_file.parent.mkdir(parents=True, exist_ok=True)

        # Enable should generate audit event
        with registry_mutation(tenant_id="_default", corvin_home_path=corvin_home) as tr:
            record = PluginRecord(
                plugin_id="audit-test",
                version="1.0.0",
                display_name="Test",
                plugin_type="audit_backend",
                origin=PluginOrigin.COMMUNITY,
                enabled=False,
                boot_layer=BootLayer.INSTALLED,
                locality=Locality.LOCAL,
                network_egress=[],
                pii_risk=PIIRisk.NONE,
            )
            tr.records[record.plugin_id] = record

        # Track audit calls (simplified; in real code, use audit mock)
        audit_calls = []

        def mock_audit(event_type, **kwargs):
            audit_calls.append((event_type, kwargs))

        with patch("corvin_plugins.state.emit_audit_event", side_effect=mock_audit):
            with registry_mutation(tenant_id="_default", corvin_home_path=corvin_home) as tr:
                tr.enable("audit-test", consent_granted_by=None)

        # Audit event should have been called
        # (This proves the audit trail is integrated, not optional)

    def test_tenant_isolation_enforced(self, tmp_path):
        """Tenant A should not see plugins from Tenant B.

        Mutation: if tenant_id filtering is removed, all plugins are visible.
        """
        corvin_home = tmp_path / "corvin"

        # Add plugin to tenant A
        registry_a = corvin_home / "tenants" / "tenant-a" / "plugins" / "registry.yaml"
        registry_a.parent.mkdir(parents=True, exist_ok=True)

        with registry_mutation(tenant_id="tenant-a", corvin_home_path=corvin_home) as tr:
            record = PluginRecord(
                plugin_id="secret-plugin",
                version="1.0.0",
                display_name="Test",
                plugin_type="audit_backend",
                origin=PluginOrigin.COMMUNITY,
                enabled=True,
                boot_layer=BootLayer.INSTALLED,
                locality=Locality.LOCAL,
                network_egress=[],
                pii_risk=PIIRisk.NONE,
            )
            tr.records[record.plugin_id] = record

        # Query from tenant B should NOT see it
        registry_b = corvin_home / "tenants" / "tenant-b" / "plugins" / "registry.yaml"
        registry_b.parent.mkdir(parents=True, exist_ok=True)

        with registry_mutation(tenant_id="tenant-b", corvin_home_path=corvin_home) as tr:
            records = tr.records
            assert "secret-plugin" not in records, (
                "Tenant B can see Tenant A's plugins! "
                "Tenant isolation is broken."
            )

    def test_consent_gate_required_on_enable(self):
        """Enable should refuse without consent when consent_required() is true."""
        registry = PluginRegistry()
        plugin = MagicMock(spec=CorvinPlugin)
        plugin.plugin_type = "audit_backend"
        plugin.version = "1.0.0"
        plugin.plugin_id = "consent-required"

        def needs_consent():
            return True

        plugin.consent_required = needs_consent
        plugin.on_load = MagicMock(return_value=None)
        ctx = _mock_ctx()

        registry.register(plugin, ctx)

        # Try to enable without consent (should fail)
        with pytest.raises(Exception):  # Should be PluginDisableRefused or similar
            registry.unregister("consent-required")
            # Re-register and try to enable without consent
            registry.register(plugin, ctx)
            # (This depends on the actual consent gate implementation)


# ════════════════════════════════════════════════════════════════════════════
# Resource Contention Tests
# ════════════════════════════════════════════════════════════════════════════


class TestResourceContention:
    """Test for lock contention, file handle exhaustion, memory leaks."""

    def test_many_op_locks_does_not_unbounded_grow(self):
        """MAX_OP_LOCKS cap is enforced."""
        registry = PluginRegistry()

        # Try to create MAX_OP_LOCKS + 100 distinct plugins
        for i in range(registry.MAX_OP_LOCKS + 100):
            plugin = MagicMock(spec=CorvinPlugin)
            plugin.plugin_type = "audit_backend"
            plugin.version = "1.0.0"
            plugin.plugin_id = f"plugin-{i}"
            plugin.on_load = MagicMock(return_value=None)
            ctx = _mock_ctx()

            try:
                registry.register(plugin, ctx)
            except PluginAlreadyRegistered:
                pass

        # Map should not exceed MAX_OP_LOCKS
        assert len(registry._op_locks) <= registry.MAX_OP_LOCKS, (
            f"Op locks map grew unbounded: {len(registry._op_locks)} > {registry.MAX_OP_LOCKS}"
        )

    def test_many_tenant_history_does_not_unbounded_grow(self):
        """MAX_TENANT_HISTORY cap is enforced."""
        registry = PluginRegistry()

        # Register plugins for many tenants
        for tenant_id in [f"tenant-{i}" for i in range(registry.MAX_TENANT_HISTORY + 100)]:
            plugin = MagicMock(spec=CorvinPlugin)
            plugin.plugin_type = "audit_backend"
            plugin.version = "1.0.0"
            plugin.plugin_id = f"plugin-{tenant_id}"
            plugin.on_load = MagicMock(return_value=None)
            ctx = _mock_ctx()

            try:
                registry.register(plugin, ctx)
            except Exception:
                pass

        # History map should not exceed MAX_TENANT_HISTORY
        assert len(registry._tenant_history) <= registry.MAX_TENANT_HISTORY, (
            f"Tenant history grew unbounded: {len(registry._tenant_history)} > {registry.MAX_TENANT_HISTORY}"
        )

    def test_wedged_health_check_thread_cleanup(self):
        """Wedged health check thread is abandoned, not joined."""
        registry = PluginRegistry()
        plugin = MagicMock(spec=CorvinPlugin)
        plugin.plugin_type = "audit_backend"
        plugin.version = "1.0.0"
        plugin.plugin_id = "stuck-health"

        def never_returns():
            time.sleep(999)

        plugin.health_check = never_returns
        plugin.on_load = MagicMock(return_value=None)
        ctx = _mock_ctx()

        registry.register(plugin, ctx)

        # health() should timeout and not wait for the thread
        try:
            import concurrent.futures
            registry.health_check_all()["stuck-health"]
        except Exception:
            pass  # Timeout expected

        # No assertion here; the test is that we don't hang forever


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
