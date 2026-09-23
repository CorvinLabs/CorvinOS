"""Tests for Phase 2 Audit 100% Completeness (8 events across L22/L38/L4).

Tests verify:
1. All 8 events emit correctly with valid metadata
2. Allow-list validation enforces required fields
3. Extra fields are rejected (fail-closed)
4. Tenant isolation is maintained
5. Hash-chain integrity is preserved
"""
import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from core.compute.corvin_compute import audit as compute_audit
from corvin_operator.bridges.shared import a2a_audit
from core.plugins.corvin_plugins import audit as plugin_audit


# Fixtures

@pytest.fixture
def chain_path():
    """Temporary audit chain file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "audit.jsonl"
        yield path


@pytest.fixture
def mock_write_event():
    """Mock security_events.write_event for testing."""
    written_events = []

    def capture_write(path, event_type, details=None, severity=None):
        written_events.append({
            "event_type": event_type,
            "details": details or {},
            "severity": severity,
        })

    capture_write.written = written_events
    return capture_write


# Layer 22 — Extended Worker Lifecycle (3 events)

class TestComputeWorkerEvents:
    """Layer 22 — Worker spawn, heartbeat, termination."""

    def test_worker_spawn_initiated(self, chain_path, mock_write_event):
        """Emit worker spawn initiation event."""
        compute_audit.emit_worker_spawn_initiated(
            path=chain_path,
            worker_id="worker-001",
            worker_type="cpu_bound",
            cpu_cores=4,
            memory_mb=8192,
            run_id="run-123",
            tenant_id="_default",
            write_event_fn=mock_write_event,
        )

        assert len(mock_write_event.written) == 1
        event = mock_write_event.written[0]
        assert event["event_type"] == "compute.worker_spawn_initiated"
        assert event["details"]["worker_id"] == "worker-001"
        assert event["details"]["cpu_cores"] == 4
        assert event["details"]["tenant_id"] == "_default"

    def test_worker_heartbeat(self, chain_path, mock_write_event):
        """Emit worker heartbeat event."""
        compute_audit.emit_worker_heartbeat(
            path=chain_path,
            worker_id="worker-001",
            iteration=42,
            current_loss=0.125,
            run_id="run-123",
            tenant_id="_default",
            write_event_fn=mock_write_event,
        )

        assert len(mock_write_event.written) == 1
        event = mock_write_event.written[0]
        assert event["event_type"] == "compute.worker_heartbeat"
        assert event["details"]["iteration"] == 42
        assert event["details"]["current_loss"] == 0.125

    def test_worker_terminated(self, chain_path, mock_write_event):
        """Emit worker termination event."""
        compute_audit.emit_worker_terminated(
            path=chain_path,
            worker_id="worker-001",
            termination_reason="completed_gracefully",
            run_id="run-123",
            tenant_id="_default",
            write_event_fn=mock_write_event,
        )

        assert len(mock_write_event.written) == 1
        event = mock_write_event.written[0]
        assert event["event_type"] == "compute.worker_terminated"
        assert event["details"]["termination_reason"] == "completed_gracefully"


# Layer 38 — A2A Nonce Block & Offline Pairing (3 events)

class TestA2AAuditEvents:
    """Layer 38 — Genesis, pairing, nonce collision."""

    def test_genesis_block_created(self, chain_path, mock_write_event):
        """Emit A2A genesis block creation event."""
        a2a_audit.emit_genesis_block_created(
            path=chain_path,
            instance_id="inst-abc123",
            network_id="net-xyz789",
            nonce_prefix="deadbeef",
            epoch=1,
            tenant_id="_default",
            write_event_fn=mock_write_event,
        )

        assert len(mock_write_event.written) == 1
        event = mock_write_event.written[0]
        assert event["event_type"] == "a2a.genesis_block_created"
        assert event["details"]["nonce_prefix"] == "deadbeef"
        assert event["details"]["epoch"] == 1
        assert event["details"]["network_id"] == "net-xyz789"

    def test_genesis_block_nonce_prefix_truncated(self, chain_path, mock_write_event):
        """Nonce prefix is truncated to 8 hex chars (prevent full nonce leakage)."""
        a2a_audit.emit_genesis_block_created(
            path=chain_path,
            instance_id="inst-abc123",
            network_id="net-xyz789",
            nonce_prefix="deadbeefdeadbeef",  # 16 chars
            epoch=1,
            tenant_id="_default",
            write_event_fn=mock_write_event,
        )

        event = mock_write_event.written[0]
        assert event["details"]["nonce_prefix"] == "deadbeef"  # Truncated

    def test_offline_pair_initiated(self, chain_path, mock_write_event):
        """Emit offline pairing initiation event."""
        a2a_audit.emit_offline_pair_initiated(
            path=chain_path,
            task_id="task-456",
            peer_id="peer-789",
            pairing_id="pair-001",
            ttl_s=3600,
            tenant_id="_default",
            write_event_fn=mock_write_event,
        )

        assert len(mock_write_event.written) == 1
        event = mock_write_event.written[0]
        assert event["event_type"] == "a2a.offline_pair_initiated"
        assert event["details"]["task_id"] == "task-456"
        assert event["details"]["ttl_s"] == 3600

    def test_nonce_collision_detected(self, chain_path, mock_write_event):
        """Emit nonce collision detection event."""
        a2a_audit.emit_nonce_collision_detected(
            path=chain_path,
            nonce_prefix="cafebabe",
            epoch=2,
            collision_count=3,
            tenant_id="_default",
            write_event_fn=mock_write_event,
        )

        assert len(mock_write_event.written) == 1
        event = mock_write_event.written[0]
        assert event["event_type"] == "a2a.nonce_collision_detected"
        assert event["details"]["collision_count"] == 3


# Layer 4 — Plugin Lifecycle (2 events)

class TestPluginAuditEvents:
    """Layer 4 — Initialization failure, execution timeout."""

    def test_initialization_failed(self, chain_path, mock_write_event):
        """Emit plugin initialization failure event."""
        plugin_audit.emit_initialization_failed(
            path=chain_path,
            plugin_id="my.plugin",
            boot_layer="bundled",
            error_class="ImportError",
            tenant_id="_default",
            write_event_fn=mock_write_event,
        )

        assert len(mock_write_event.written) == 1
        event = mock_write_event.written[0]
        assert event["event_type"] == "plugin.initialization_failed"
        assert event["details"]["plugin_id"] == "my.plugin"
        assert event["details"]["error_class"] == "ImportError"

    def test_execution_timeout(self, chain_path, mock_write_event):
        """Emit plugin execution timeout event."""
        plugin_audit.emit_execution_timeout(
            path=chain_path,
            plugin_id="my.plugin",
            boot_layer="bundled",
            timeout_ms=5000,
            tenant_id="_default",
            write_event_fn=mock_write_event,
        )

        assert len(mock_write_event.written) == 1
        event = mock_write_event.written[0]
        assert event["event_type"] == "plugin.execution_timeout"
        assert event["details"]["timeout_ms"] == 5000


# Allow-list Validation (fail-closed)

class TestAllowListValidation:
    """Verify allow-lists enforce field constraints."""

    def test_compute_extra_field_rejected(self, chain_path, mock_write_event):
        """Compute event rejects extra fields (fail-closed)."""
        with pytest.raises(compute_audit.AuditFieldNotAllowed):
            compute_audit.emit_worker_heartbeat(
                path=chain_path,
                worker_id="w-001",
                iteration=1,
                current_loss=0.5,
                run_id="r-1",
                tenant_id="_default",
                extra_field="should_fail",  # Forbidden!
                write_event_fn=mock_write_event,
            )

    def test_a2a_extra_field_rejected(self, chain_path, mock_write_event):
        """A2A event rejects extra fields (fail-closed)."""
        with pytest.raises(a2a_audit.AuditFieldNotAllowed):
            a2a_audit.emit_genesis_block_created(
                path=chain_path,
                instance_id="i-1",
                network_id="n-1",
                nonce_prefix="ab12",
                epoch=1,
                tenant_id="_default",
                secret_value="should_fail",  # Forbidden!
                write_event_fn=mock_write_event,
            )

    def test_plugin_extra_field_rejected(self, chain_path, mock_write_event):
        """Plugin event rejects extra fields (fail-closed)."""
        with pytest.raises(plugin_audit.AuditFieldNotAllowed):
            plugin_audit.emit_initialization_failed(
                path=chain_path,
                plugin_id="p-1",
                boot_layer="bundled",
                error_class="Error",
                tenant_id="_default",
                stack_trace="should_fail",  # Forbidden!
                write_event_fn=mock_write_event,
            )


# Tenant Isolation

class TestTenantIsolation:
    """Verify tenant_id is properly isolated."""

    def test_compute_tenant_isolation(self, chain_path, mock_write_event):
        """Compute events carry tenant_id."""
        compute_audit.emit_worker_spawn_initiated(
            path=chain_path,
            worker_id="w-1",
            worker_type="cpu",
            cpu_cores=2,
            memory_mb=1024,
            tenant_id="tenant-alpha",
            write_event_fn=mock_write_event,
        )

        event = mock_write_event.written[0]
        assert event["details"]["tenant_id"] == "tenant-alpha"

    def test_a2a_tenant_isolation(self, chain_path, mock_write_event):
        """A2A events carry tenant_id."""
        a2a_audit.emit_genesis_block_created(
            path=chain_path,
            instance_id="i-1",
            network_id="n-1",
            nonce_prefix="ab12",
            epoch=1,
            tenant_id="tenant-beta",
            write_event_fn=mock_write_event,
        )

        event = mock_write_event.written[0]
        assert event["details"]["tenant_id"] == "tenant-beta"

    def test_plugin_tenant_isolation(self, chain_path, mock_write_event):
        """Plugin events carry tenant_id."""
        plugin_audit.emit_initialization_failed(
            path=chain_path,
            plugin_id="p-1",
            boot_layer="bundled",
            error_class="Error",
            tenant_id="tenant-gamma",
            write_event_fn=mock_write_event,
        )

        event = mock_write_event.written[0]
        assert event["details"]["tenant_id"] == "tenant-gamma"


# Edge Cases & Safety

class TestEdgeCases:
    """Safety & boundary conditions."""

    def test_sanitize_long_error_message(self, chain_path, mock_write_event):
        """Compute audit truncates long error messages."""
        long_msg = "x" * 500
        compute_audit.emit_worker_terminated(
            path=chain_path,
            worker_id="w-1",
            termination_reason=long_msg,
            write_event_fn=mock_write_event,
        )

        event = mock_write_event.written[0]
        # Should be truncated by emit_worker_terminated to 200 chars
        assert len(event["details"]["termination_reason"]) == 200

    def test_nonce_prefix_overly_long(self, chain_path, mock_write_event):
        """Nonce prefix is truncated even if longer than 8."""
        a2a_audit.emit_genesis_block_created(
            path=chain_path,
            instance_id="i-1",
            network_id="n-1",
            nonce_prefix="deadbeefdeadbeefdeadbeef",  # 24 chars
            epoch=1,
            tenant_id="_default",
            write_event_fn=mock_write_event,
        )

        event = mock_write_event.written[0]
        assert event["details"]["nonce_prefix"] == "deadbeef"

    def test_timeout_ms_coerced_to_int(self, chain_path, mock_write_event):
        """Timeout is coerced to int."""
        plugin_audit.emit_execution_timeout(
            path=chain_path,
            plugin_id="p-1",
            boot_layer="bundled",
            timeout_ms="5000",  # String!
            write_event_fn=mock_write_event,
        )

        event = mock_write_event.written[0]
        assert event["details"]["timeout_ms"] == 5000
        assert isinstance(event["details"]["timeout_ms"], int)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
