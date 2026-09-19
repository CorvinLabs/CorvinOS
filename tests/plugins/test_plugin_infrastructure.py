"""Tests for plugin supporting infrastructure (audit, security, metrics, error handling).

Comprehensive test suite covering:
* Plugin audit logging (lifecycle events, immutability, tenant isolation)
* Plugin security (capabilities, permissions, sandbox constraints)
* Plugin metrics (collection, export, per-capability stats)
* Error handling (isolation, graceful degradation, recovery)
"""
import pytest
import time
from unittest.mock import Mock, patch, MagicMock

from corvin_plugins.audit_logging import (
    PluginAuditLogger,
    PluginAuditEvent,
    PluginEventType,
)
from corvin_plugins.security import (
    PluginSecurityPolicy,
    SecurablePluginGate,
    PluginCapability,
    PermissionDeniedError,
    SandboxViolationError,
    PluginSecurityContext,
)
from corvin_plugins.metrics import (
    PluginMetricsCollector,
    PluginMetrics,
    ExecutionStats,
)
from corvin_plugins.error_handling import (
    PluginCallGuard,
    ErrorContext,
    ErrorSeverity,
    GracefulDegradation,
)


# ── Audit Logging Tests ──────────────────────────────────────────────────────

class TestPluginAuditEvent:
    """Test PluginAuditEvent immutability and validation."""

    def test_event_is_frozen(self):
        """Verify events are immutable."""
        event = PluginAuditEvent(
            event_type=PluginEventType.PLUGIN_LOADED.value,
            plugin_id="test_plugin",
            tenant_id="default",
            timestamp=time.time(),
            version="1.0.0",
            lom="test_module.py:42",
        )

        with pytest.raises(AttributeError):
            event.plugin_id = "modified"

    def test_event_validation(self):
        """Test event validation at creation."""
        with pytest.raises(ValueError, match="plugin_id required"):
            PluginAuditEvent(
                event_type=PluginEventType.PLUGIN_LOADED.value,
                plugin_id="",
                tenant_id="default",
                timestamp=time.time(),
                version="1.0.0",
                lom="test:1",
            )

        with pytest.raises(ValueError, match="timestamp must be positive"):
            PluginAuditEvent(
                event_type=PluginEventType.PLUGIN_LOADED.value,
                plugin_id="test",
                tenant_id="default",
                timestamp=-1.0,
                version="1.0.0",
                lom="test:1",
            )

    def test_event_to_dict(self):
        """Test event serialization."""
        event = PluginAuditEvent(
            event_type=PluginEventType.PLUGIN_LOADED.value,
            plugin_id="my_plugin",
            tenant_id="tenant_1",
            timestamp=1000.0,
            version="2.1.0",
            lom="module.py:100",
            boot_layer="core",
        )

        d = event.to_dict()
        assert d["plugin_id"] == "my_plugin"
        assert d["tenant_id"] == "tenant_1"
        assert d["event_type"] == "plugin.loaded"
        assert d["boot_layer"] == "core"


class TestPluginAuditLogger:
    """Test PluginAuditLogger emission."""

    def test_log_loaded(self):
        """Test logging successful plugin load."""
        audit_calls = []

        def mock_emit(event_type: str, details: dict) -> None:
            audit_calls.append((event_type, details))

        logger = PluginAuditLogger(
            audit_emit=mock_emit,
            tenant_id="test_tenant",
        )

        logger.log_loaded(
            plugin_id="test_plugin",
            version="1.0.0",
            boot_layer="core",
            plugin_type="audit_backend",
            lom="bootstrap.py:42",
            duration_ms=12.5,
        )

        assert len(audit_calls) == 1
        event_type, details = audit_calls[0]
        assert event_type == PluginEventType.PLUGIN_LOADED.value
        assert details["plugin_id"] == "test_plugin"
        assert details["tenant_id"] == "test_tenant"
        assert details["duration_ms"] == 12.5

    def test_log_execution_failed(self):
        """Test logging execution failures."""
        audit_calls = []

        def mock_emit(event_type: str, details: dict) -> None:
            audit_calls.append((event_type, details))

        logger = PluginAuditLogger(
            audit_emit=mock_emit,
            tenant_id="default",
        )

        logger.log_execution_failed(
            plugin_id="bad_plugin",
            version="1.0.0",
            capability="audit_write",
            duration_ms=50.0,
            error_type="ValueError",
            error_message="Invalid config",
            lom="plugin.py:99",
        )

        assert len(audit_calls) == 1
        event_type, details = audit_calls[0]
        assert event_type == PluginEventType.PLUGIN_EXECUTION_FAILED.value
        assert details["error_type"] == "ValueError"
        assert details["capability"] == "audit_write"

    def test_audit_emit_failure_handling(self):
        """Test graceful handling of audit emit failures."""
        def failing_emit(event_type: str, details: dict) -> None:
            raise RuntimeError("Audit system down")

        logger = PluginAuditLogger(
            audit_emit=failing_emit,
            tenant_id="default",
        )

        # Should not raise; error is logged
        logger.log_loaded(
            plugin_id="test",
            version="1.0.0",
            boot_layer="bundled",
            plugin_type="test",
            lom="test:1",
        )

    def test_tenant_isolation(self):
        """Test that audit logs are tenant-scoped."""
        calls = []

        def track_emit(event_type: str, details: dict) -> None:
            calls.append(details)

        logger1 = PluginAuditLogger(audit_emit=track_emit, tenant_id="tenant_a")
        logger2 = PluginAuditLogger(audit_emit=track_emit, tenant_id="tenant_b")

        logger1.log_loaded(
            plugin_id="plugin",
            version="1.0",
            boot_layer="core",
            plugin_type="test",
            lom="a.py:1",
        )

        logger2.log_loaded(
            plugin_id="plugin",
            version="1.0",
            boot_layer="core",
            plugin_type="test",
            lom="b.py:1",
        )

        assert calls[0]["tenant_id"] == "tenant_a"
        assert calls[1]["tenant_id"] == "tenant_b"


# ── Security Tests ──────────────────────────────────────────────────────────

class TestPluginSecurityPolicy:
    """Test security policy creation and validation."""

    def test_policy_creation(self):
        """Test creating a security policy."""
        policy = PluginSecurityPolicy(
            plugin_id="test_plugin",
            boot_layer="bundled",
            capabilities=frozenset([
                PluginCapability.CORE_PLUGIN_API.value,
                PluginCapability.DATA_READ_USER_CONTEXT.value,
            ]),
            max_file_read_size=1024 * 1024,
        )

        assert policy.plugin_id == "test_plugin"
        assert len(policy.capabilities) == 2
        assert policy.has_capability(PluginCapability.CORE_PLUGIN_API.value)

    def test_policy_is_immutable(self):
        """Test that policies are frozen."""
        policy = PluginSecurityPolicy(
            plugin_id="test",
            boot_layer="core",
        )

        with pytest.raises(AttributeError):
            policy.plugin_id = "modified"

    def test_missing_capability_raises(self):
        """Test requires_capability check."""
        policy = PluginSecurityPolicy(
            plugin_id="test",
            boot_layer="bundled",
            capabilities=frozenset(),
        )

        with pytest.raises(PermissionDeniedError):
            policy.requires_capability(PluginCapability.SYSTEM_FILE_READ.value)


class TestSecurablePluginGate:
    """Test security gate enforcement."""

    def test_check_capability_allowed(self):
        """Test checking an allowed capability."""
        policy = PluginSecurityPolicy(
            plugin_id="test",
            boot_layer="bundled",
            capabilities=frozenset([
                PluginCapability.SYSTEM_FILE_READ.value,
            ]),
        )

        gate = SecurablePluginGate(
            policy=policy,
            audit_emit=Mock(),
            tenant_id="default",
        )

        # Should not raise
        gate.check_capability(PluginCapability.SYSTEM_FILE_READ.value)

    def test_check_capability_denied(self):
        """Test checking a denied capability."""
        policy = PluginSecurityPolicy(
            plugin_id="test",
            boot_layer="bundled",
            capabilities=frozenset(),
        )

        gate = SecurablePluginGate(
            policy=policy,
            audit_emit=Mock(),
            tenant_id="default",
        )

        with pytest.raises(PermissionDeniedError):
            gate.check_capability(PluginCapability.SYSTEM_FILE_READ.value)

        assert gate.violation_count() == 1

    def test_check_file_read_size_limit(self):
        """Test file read size constraint."""
        policy = PluginSecurityPolicy(
            plugin_id="test",
            boot_layer="bundled",
            capabilities=frozenset([PluginCapability.SYSTEM_FILE_READ.value]),
            max_file_read_size=1000,
        )

        gate = SecurablePluginGate(
            policy=policy,
            audit_emit=Mock(),
            tenant_id="default",
        )

        # Should pass for small file
        gate.check_file_read("/tmp/small.txt", file_size=500)

        # Should fail for large file
        with pytest.raises(SandboxViolationError):
            gate.check_file_read("/tmp/large.txt", file_size=2000)

    def test_check_network_access(self):
        """Test network domain constraints."""
        policy = PluginSecurityPolicy(
            plugin_id="test",
            boot_layer="bundled",
            capabilities=frozenset([PluginCapability.SYSTEM_NETWORK.value]),
            allowed_domains=frozenset(["api.example.com", "*.example.org"]),
        )

        gate = SecurablePluginGate(
            policy=policy,
            audit_emit=Mock(),
            tenant_id="default",
        )

        # Allowed domain
        gate.check_network_access("api.example.com")

        # Wildcard match
        gate.check_network_access("sub.example.org")

        # Denied domain
        with pytest.raises(SandboxViolationError):
            gate.check_network_access("evil.com")


# ── Metrics Tests ────────────────────────────────────────────────────────────

class TestPluginMetricsCollector:
    """Test metrics collection and aggregation."""

    def test_record_load(self):
        """Test recording plugin load."""
        collector = PluginMetricsCollector()

        collector.record_load(
            plugin_id="test_plugin",
            version="1.0.0",
            boot_layer="core",
            duration_ms=42.5,
        )

        metrics = collector.get_metrics("test_plugin")
        assert metrics is not None
        assert metrics.total_loads == 1
        assert metrics.load_time == 42.5

    def test_record_execution(self):
        """Test recording plugin execution."""
        collector = PluginMetricsCollector()

        # Success
        collector.record_execution(
            plugin_id="test",
            capability="audit_write",
            duration_ms=10.0,
            success=True,
        )

        # Failure
        collector.record_execution(
            plugin_id="test",
            capability="audit_write",
            duration_ms=5.0,
            success=False,
            error_type="ValueError",
        )

        metrics = collector.get_metrics("test")
        stats = metrics.execution_stats["audit_write"]
        assert stats.call_count == 2
        assert stats.success_count == 1
        assert stats.failure_count == 1
        assert stats.last_error_type == "ValueError"

    def test_execution_stats_aggregation(self):
        """Test aggregated execution statistics."""
        collector = PluginMetricsCollector()

        for duration in [10, 20, 30]:
            collector.record_execution(
                plugin_id="test",
                capability="cap1",
                duration_ms=float(duration),
                success=True,
            )

        stats = collector.get_metrics("test").execution_stats["cap1"]
        assert stats.call_count == 3
        assert stats.avg_duration_ms == 20.0
        assert stats.min_duration_ms == 10.0
        assert stats.max_duration_ms == 30.0
        assert stats.error_rate == 0.0

    def test_prometheus_export(self):
        """Test Prometheus format export."""
        collector = PluginMetricsCollector()

        collector.record_load("plugin1", "1.0", "core", 5.0)
        collector.record_execution("plugin1", "cap1", 10.0, success=True)
        collector.record_execution("plugin1", "cap1", 5.0, success=False)

        prometheus = collector.render_prometheus()

        assert "corvin_plugin_loads_total" in prometheus
        assert "corvin_plugin_calls_total" in prometheus
        assert "corvin_plugin_call_success_total" in prometheus
        assert 'plugin="plugin1"' in prometheus


# ── Error Handling Tests ─────────────────────────────────────────────────────

class TestPluginCallGuard:
    """Test plugin call execution guard."""

    def test_successful_call(self):
        """Test successful plugin call returns result."""
        guard = PluginCallGuard(
            plugin_id="test",
            tenant_id="default",
            audit_emit=Mock(),
        )

        result = guard.call(
            capability="test_cap",
            fn=lambda: 42,
            fallback=0,
        )

        assert result == 42

    def test_failed_call_returns_fallback(self):
        """Test that failed calls return fallback."""
        guard = PluginCallGuard(
            plugin_id="test",
            tenant_id="default",
            audit_emit=Mock(),
        )

        def failing_fn() -> int:
            raise ValueError("Something went wrong")

        result = guard.call(
            capability="test_cap",
            fn=failing_fn,
            fallback=-1,
        )

        assert result == -1

    def test_error_audit_on_failure(self):
        """Test that errors are audited."""
        audit_calls = []

        def track_audit(event_type: str, details: dict) -> None:
            audit_calls.append((event_type, details))

        guard = PluginCallGuard(
            plugin_id="bad_plugin",
            tenant_id="default",
            audit_emit=track_audit,
        )

        guard.call(
            capability="test_cap",
            fn=lambda: 1 / 0,  # ZeroDivisionError
            fallback=None,
        )

        assert len(audit_calls) > 0
        event_type, details = audit_calls[0]
        assert event_type == "plugin.execution_failed"
        assert details["error_type"] == "ZeroDivisionError"

    def test_timeout_handling(self):
        """Test call timeout handling."""
        guard = PluginCallGuard(
            plugin_id="test",
            tenant_id="default",
            audit_emit=Mock(),
        )

        def slow_fn() -> int:
            time.sleep(1.0)
            return 42

        result = guard.call(
            capability="test_cap",
            fn=slow_fn,
            fallback=-1,
            timeout_s=0.1,
        )

        assert result == -1

    def test_error_severity_classification(self):
        """Test error severity classification."""
        guard = PluginCallGuard(
            plugin_id="test",
            tenant_id="default",
            audit_emit=Mock(),
        )

        assert (
            guard._classify_severity(TimeoutError("timeout"))
            == ErrorSeverity.RECOVERABLE
        )
        assert (
            guard._classify_severity(ValueError("bad value"))
            == ErrorSeverity.DEGRADED
        )
        assert (
            guard._classify_severity(RuntimeError("OutOfMemoryError"))
            == ErrorSeverity.CRITICAL
        )


class TestGracefulDegradation:
    """Test graceful degradation strategies."""

    def test_non_critical_plugin_degrades(self):
        """Test that non-critical plugins always degrade."""
        assert GracefulDegradation.should_degrade(
            plugin_id="optional_plugin",
            is_critical=False,
            error_severity=ErrorSeverity.CRITICAL,
        )

    def test_critical_plugin_only_degrades_on_recoverable(self):
        """Test critical plugins only degrade on recoverable errors."""
        # Should degrade on recoverable error
        assert GracefulDegradation.should_degrade(
            plugin_id="critical_plugin",
            is_critical=True,
            error_severity=ErrorSeverity.RECOVERABLE,
        )

        # Should NOT degrade on critical error (fail-fast)
        assert not GracefulDegradation.should_degrade(
            plugin_id="critical_plugin",
            is_critical=True,
            error_severity=ErrorSeverity.CRITICAL,
        )


# ── Integration Tests ────────────────────────────────────────────────────────

class TestPluginInfrastructureIntegration:
    """Integration tests combining multiple components."""

    def test_full_plugin_lifecycle_tracking(self):
        """Test complete plugin lifecycle tracking."""
        audit_calls = []

        def audit_emit(event_type: str, details: dict) -> None:
            audit_calls.append(details)

        # Initialize components
        logger = PluginAuditLogger(audit_emit=audit_emit, tenant_id="default")

        policy = PluginSecurityPolicy(
            plugin_id="test_plugin",
            boot_layer="bundled",
            capabilities=frozenset([
                PluginCapability.SYSTEM_FILE_READ.value,
            ]),
        )

        gate = SecurablePluginGate(
            policy=policy,
            audit_emit=audit_emit,
            tenant_id="default",
        )

        collector = PluginMetricsCollector()

        # Simulate plugin lifecycle
        logger.log_loaded(
            plugin_id="test_plugin",
            version="1.0.0",
            boot_layer="bundled",
            plugin_type="test",
            lom="test.py:1",
            duration_ms=10.0,
        )

        collector.record_load(
            plugin_id="test_plugin",
            version="1.0.0",
            boot_layer="bundled",
            duration_ms=10.0,
        )

        # Capability usage
        gate.check_capability(PluginCapability.SYSTEM_FILE_READ.value)

        logger.log_executed(
            plugin_id="test_plugin",
            version="1.0.0",
            capability="file_read",
            duration_ms=5.0,
            lom="test.py:42",
        )

        collector.record_execution(
            plugin_id="test_plugin",
            capability="file_read",
            duration_ms=5.0,
            success=True,
        )

        # Verify complete tracking
        assert len(audit_calls) >= 2  # load + execute
        metrics = collector.get_metrics("test_plugin")
        assert metrics.total_loads == 1
        assert metrics.execution_stats["file_read"].success_count == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
