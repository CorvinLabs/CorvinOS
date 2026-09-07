"""Test Security Fix #10: Metrics Exporter with HMAC-SHA256 Protection.

Tests verify the high-level metrics exporter API with tamper protection.
Multiple test cases ensure:
  - HMAC computation on export
  - HMAC verification on import
  - Fail-closed rejection of tampering
  - Audit trail logging
  - Batch operations
"""

import json
import pytest
import tempfile
from pathlib import Path
from datetime import datetime

from core.learning.metrics_exporter import (
    MetricsExporter,
    MetricsImporter,
    MetricsSnapshot,
    MetricsIntegrityValidator,
)
from core.infinite_session.crypto_binding import CryptoBinding, canonical_json


class TestMetricsSnapshotDataclass:
    """Test MetricsSnapshot immutable dataclass."""

    def test_snapshot_creation(self):
        """Create MetricsSnapshot with all required fields."""
        snapshot = MetricsSnapshot(
            tenant_id="_default",
            export_id="test-123",
            timestamp="2026-09-07T12:00:00Z",
            metrics={"accuracy": 0.95, "latency_ms": 42},
            payload_hmac="abc123def456" * 5 + "abcd",  # 64 chars
        )

        assert snapshot.tenant_id == "_default"
        assert snapshot.export_id == "test-123"
        assert snapshot.metrics["accuracy"] == 0.95
        assert len(snapshot.payload_hmac) == 64

    def test_snapshot_to_dict(self):
        """Convert snapshot to dict for JSON serialization."""
        snapshot = MetricsSnapshot(
            tenant_id="_default",
            export_id="test-123",
            timestamp="2026-09-07T12:00:00Z",
            metrics={"accuracy": 0.95},
            payload_hmac="a" * 64,
        )

        snapshot_dict = snapshot.to_dict()

        assert snapshot_dict["tenant_id"] == "_default"
        assert snapshot_dict["metrics"]["accuracy"] == 0.95
        assert snapshot_dict["payload_hmac"] == "a" * 64
        assert snapshot_dict["signature_algorithm"] == "hmac-sha256"

    def test_snapshot_immutable(self):
        """Snapshot is frozen (immutable)."""
        snapshot = MetricsSnapshot(
            tenant_id="_default",
            export_id="test-123",
            timestamp="2026-09-07T12:00:00Z",
            metrics={"accuracy": 0.95},
            payload_hmac="a" * 64,
        )

        with pytest.raises(AttributeError):
            snapshot.tenant_id = "tenant-2"


class TestMetricsExporterBasic:
    """Test basic MetricsExporter functionality (Fix #10 Test Case 1)."""

    def test_export_creates_snapshot_with_hmac(self, tmp_path):
        """Export creates MetricsSnapshot with valid HMAC (FIX #10 TEST 1)."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))

        snapshot = exporter.export(
            tenant_id="_default",
            metrics={"accuracy": 0.95, "latency_ms": 42},
            export_id="fix10-test-1",
        )

        assert snapshot is not None
        assert isinstance(snapshot, MetricsSnapshot)
        assert snapshot.tenant_id == "_default"
        assert snapshot.metrics["accuracy"] == 0.95
        assert len(snapshot.payload_hmac) == 64  # SHA256 hex is 64 chars
        assert snapshot.signature_algorithm == "hmac-sha256"

    def test_export_hmac_verifiable(self, tmp_path):
        """Exported HMAC can be verified using crypto binding (FIX #10 TEST 1)."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        crypto = CryptoBinding(corvin_home=str(tmp_path))

        snapshot = exporter.export(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="fix10-verify",
        )

        assert snapshot is not None

        # Reconstruct the payload and verify HMAC
        payload = {
            "timestamp": snapshot.timestamp,
            "tenant_id": snapshot.tenant_id,
            "metrics": snapshot.metrics,
            "export_id": snapshot.export_id,
        }

        verified, error = crypto.verify_bytes(
            "_default",
            canonical_json(payload),
            snapshot.payload_hmac,
        )

        assert verified is True
        assert error == ""

    def test_export_different_metrics_different_hmac(self, tmp_path):
        """Different metrics produce different HMACs (FIX #10 TEST 1)."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))

        snapshot_1 = exporter.export(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="export-1",
        )

        snapshot_2 = exporter.export(
            tenant_id="_default",
            metrics={"accuracy": 0.94},
            export_id="export-2",
        )

        assert snapshot_1 is not None
        assert snapshot_2 is not None
        assert snapshot_1.payload_hmac != snapshot_2.payload_hmac


class TestMetricsExporterTenantScoped:
    """Test that exports are tenant-scoped (Fix #10 Test Case 1)."""

    def test_export_hmac_tenant_scoped(self, tmp_path):
        """Different tenants have different HMACs for same metrics (FIX #10 TEST 1)."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        crypto = CryptoBinding(corvin_home=str(tmp_path))

        # Ensure both tenant keys exist
        crypto._ensure_key_exists("_default")
        crypto._ensure_key_exists("tenant-2")

        metrics = {"accuracy": 0.95, "latency_ms": 42}

        snapshot_default = exporter.export(
            tenant_id="_default",
            metrics=metrics,
            export_id="test-001",
        )

        snapshot_tenant2 = exporter.export(
            tenant_id="tenant-2",
            metrics=metrics,
            export_id="test-001",
        )

        assert snapshot_default is not None
        assert snapshot_tenant2 is not None
        # HMACs should differ due to different tenant keys
        assert snapshot_default.payload_hmac != snapshot_tenant2.payload_hmac


class TestMetricsImporterValid:
    """Test importing valid metrics with correct HMAC (Fix #10 Test Case 2)."""

    def test_import_valid_snapshot(self, tmp_path):
        """Import with valid HMAC succeeds and does not update state (FIX #10 TEST 2)."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        importer = MetricsImporter(corvin_home=str(tmp_path))

        # Export metrics
        snapshot = exporter.export(
            tenant_id="_default",
            metrics={"accuracy": 0.95, "latency_ms": 42},
            export_id="fix10-test-2",
        )

        assert snapshot is not None

        # Import with valid HMAC
        ok, error = importer.verify_and_import(
            tenant_id="_default",
            snapshot_dict=snapshot.to_dict(),
        )

        assert ok is True
        assert error == ""

    def test_import_valid_snapshot_with_callback(self, tmp_path):
        """Import with valid HMAC invokes on_valid callback (FIX #10 TEST 2)."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        importer = MetricsImporter(corvin_home=str(tmp_path))

        # Export metrics
        snapshot = exporter.export(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="fix10-test-2-callback",
        )

        assert snapshot is not None

        # Track callback invocation
        callback_invoked = []

        def on_valid_callback(metrics):
            callback_invoked.append(metrics)

        # Import with callback
        ok, error = importer.verify_and_import(
            tenant_id="_default",
            snapshot_dict=snapshot.to_dict(),
            on_valid=on_valid_callback,
        )

        assert ok is True
        assert error == ""
        assert len(callback_invoked) == 1
        assert callback_invoked[0]["accuracy"] == 0.95


class TestMetricsImporterTampered:
    """Test that tampered imports are rejected (Fix #10 Test Case 3)."""

    def test_import_tampered_accuracy_rejected(self, tmp_path):
        """Import fails when accuracy metric is modified (FIX #10 TEST 3)."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        importer = MetricsImporter(corvin_home=str(tmp_path))

        # Export metrics
        snapshot = exporter.export(
            tenant_id="_default",
            metrics={"accuracy": 0.95, "latency_ms": 42},
            export_id="fix10-test-3-accuracy",
        )

        assert snapshot is not None

        # Tamper: modify accuracy
        tampered_dict = snapshot.to_dict()
        tampered_dict["metrics"]["accuracy"] = 0.99

        # Import should fail
        ok, error = importer.verify_and_import(
            tenant_id="_default",
            snapshot_dict=tampered_dict,
        )

        assert ok is False
        assert "HMAC verification failed" in error

    def test_import_tampered_export_id_rejected(self, tmp_path):
        """Import fails when export_id is modified (FIX #10 TEST 3)."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        importer = MetricsImporter(corvin_home=str(tmp_path))

        # Export metrics
        snapshot = exporter.export(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="fix10-test-3-id",
        )

        assert snapshot is not None

        # Tamper: modify export_id
        tampered_dict = snapshot.to_dict()
        tampered_dict["export_id"] = "FORGED"

        # Import should fail
        ok, error = importer.verify_and_import(
            tenant_id="_default",
            snapshot_dict=tampered_dict,
        )

        assert ok is False
        assert "HMAC verification failed" in error

    def test_import_tampered_hmac_rejected(self, tmp_path):
        """Import fails when HMAC is corrupted (FIX #10 TEST 3)."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        importer = MetricsImporter(corvin_home=str(tmp_path))

        # Export metrics
        snapshot = exporter.export(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="fix10-test-3-hmac",
        )

        assert snapshot is not None

        # Tamper: corrupt the HMAC
        tampered_dict = snapshot.to_dict()
        tampered_dict["payload_hmac"] = "0" * 63 + "f"

        # Import should fail
        ok, error = importer.verify_and_import(
            tenant_id="_default",
            snapshot_dict=tampered_dict,
        )

        assert ok is False
        assert "HMAC verification failed" in error

    def test_import_tampered_callback_not_invoked(self, tmp_path):
        """Callback is NOT invoked on HMAC failure (fail-closed) (FIX #10 TEST 3)."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        importer = MetricsImporter(corvin_home=str(tmp_path))

        # Export metrics
        snapshot = exporter.export(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="fix10-test-3-callback",
        )

        assert snapshot is not None

        # Tamper
        tampered_dict = snapshot.to_dict()
        tampered_dict["metrics"]["accuracy"] = 0.99

        # Track callback
        callback_invoked = []

        def on_valid_callback(metrics):
            callback_invoked.append(metrics)
            raise AssertionError("Callback should NOT be invoked on tampering!")

        # Import with tampered data
        ok, error = importer.verify_and_import(
            tenant_id="_default",
            snapshot_dict=tampered_dict,
            on_valid=on_valid_callback,
        )

        assert ok is False
        assert len(callback_invoked) == 0  # Callback NOT invoked


class TestMetricsExporterBatch:
    """Test batch export operations."""

    def test_export_batch(self, tmp_path):
        """Batch export produces multiple snapshots with HMACs."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))

        metrics_list = [
            {"accuracy": 0.95, "latency_ms": 42},
            {"accuracy": 0.94, "latency_ms": 43},
            {"accuracy": 0.96, "latency_ms": 41},
        ]

        snapshots, errors = exporter.export_batch(
            tenant_id="_default",
            metrics_list=metrics_list,
        )

        assert len(errors) == 0
        assert len(snapshots) == 3
        for snapshot in snapshots:
            assert len(snapshot.payload_hmac) == 64
            assert snapshot.signature_algorithm == "hmac-sha256"


class TestMetricsImporterBatch:
    """Test batch import operations."""

    def test_import_batch_valid(self, tmp_path):
        """Batch import verifies all HMACs."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        importer = MetricsImporter(corvin_home=str(tmp_path))

        metrics_list = [
            {"accuracy": 0.95},
            {"accuracy": 0.94},
            {"accuracy": 0.96},
        ]

        # Export
        snapshots, export_errors = exporter.export_batch(
            tenant_id="_default",
            metrics_list=metrics_list,
        )

        assert len(export_errors) == 0

        # Import
        snapshot_dicts = [s.to_dict() for s in snapshots]
        success_count, import_errors = importer.verify_and_import_batch(
            tenant_id="_default",
            snapshot_dicts=snapshot_dicts,
        )

        assert success_count == 3
        assert len(import_errors) == 0

    def test_import_batch_with_tampered(self, tmp_path):
        """Batch import rejects tampered metrics."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        importer = MetricsImporter(corvin_home=str(tmp_path))

        metrics_list = [
            {"accuracy": 0.95},
            {"accuracy": 0.94},
            {"accuracy": 0.96},
        ]

        # Export
        snapshots, export_errors = exporter.export_batch(
            tenant_id="_default",
            metrics_list=metrics_list,
        )

        assert len(export_errors) == 0

        # Tamper the second snapshot
        snapshot_dicts = [s.to_dict() for s in snapshots]
        snapshot_dicts[1]["metrics"]["accuracy"] = 0.50

        # Import should fail for the tampered one
        success_count, import_errors = importer.verify_and_import_batch(
            tenant_id="_default",
            snapshot_dicts=snapshot_dicts,
        )

        assert success_count == 2  # Only 2 succeed
        assert len(import_errors) == 1
        assert "HMAC verification failed" in import_errors[0]


class TestMetricsIntegrityValidator:
    """Test standalone integrity validation without state updates."""

    def test_validate_snapshot_valid(self, tmp_path):
        """Validate accepts valid snapshot."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        validator = MetricsIntegrityValidator(corvin_home=str(tmp_path))

        # Export metrics
        snapshot = exporter.export(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="validate-1",
        )

        assert snapshot is not None

        # Validate
        ok, error = validator.validate_snapshot(
            tenant_id="_default",
            snapshot_dict=snapshot.to_dict(),
        )

        assert ok is True
        assert error == ""

    def test_validate_snapshot_invalid(self, tmp_path):
        """Validate rejects tampered snapshot."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        validator = MetricsIntegrityValidator(corvin_home=str(tmp_path))

        # Export metrics
        snapshot = exporter.export(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="validate-2",
        )

        assert snapshot is not None

        # Tamper
        tampered_dict = snapshot.to_dict()
        tampered_dict["metrics"]["accuracy"] = 0.99

        # Validate should fail
        ok, error = validator.validate_snapshot(
            tenant_id="_default",
            snapshot_dict=tampered_dict,
        )

        assert ok is False
        assert "HMAC verification failed" in error

    def test_validate_batch(self, tmp_path):
        """Batch validation without state updates."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        validator = MetricsIntegrityValidator(corvin_home=str(tmp_path))

        metrics_list = [
            {"accuracy": 0.95},
            {"accuracy": 0.94},
            {"accuracy": 0.96},
        ]

        snapshots, export_errors = exporter.export_batch(
            tenant_id="_default",
            metrics_list=metrics_list,
        )

        assert len(export_errors) == 0

        snapshot_dicts = [s.to_dict() for s in snapshots]
        valid_count, invalid = validator.validate_batch(
            tenant_id="_default",
            snapshot_dicts=snapshot_dicts,
        )

        assert valid_count == 3
        assert len(invalid) == 0


class TestAuditLogging:
    """Test that operations are audited."""

    def test_export_audit_event(self, tmp_path):
        """Export operation is audited."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))

        audit_events = []

        def audit_callback(event):
            audit_events.append(event)

        snapshot = exporter.export(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="audit-export",
            audit_callback=audit_callback,
        )

        assert snapshot is not None
        assert len(audit_events) == 1
        assert audit_events[0]["event_type"] == "metrics_exported"
        assert audit_events[0]["export_id"] == "audit-export"

    def test_import_audit_event(self, tmp_path):
        """Import operation is audited."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        importer = MetricsImporter(corvin_home=str(tmp_path))

        # Export
        snapshot = exporter.export(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="audit-import",
        )

        assert snapshot is not None

        audit_events = []

        def audit_callback(event):
            audit_events.append(event)

        # Import
        ok, error = importer.verify_and_import(
            tenant_id="_default",
            snapshot_dict=snapshot.to_dict(),
            audit_callback=audit_callback,
        )

        assert ok is True
        assert len(audit_events) == 1
        assert audit_events[0]["event_type"] == "metrics_imported"

    def test_import_tamper_audit_event(self, tmp_path):
        """Tamper detection is audited."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        importer = MetricsImporter(corvin_home=str(tmp_path))

        # Export
        snapshot = exporter.export(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="audit-tamper",
        )

        assert snapshot is not None

        # Tamper
        tampered_dict = snapshot.to_dict()
        tampered_dict["metrics"]["accuracy"] = 0.99

        audit_events = []

        def audit_callback(event):
            audit_events.append(event)

        # Import
        ok, error = importer.verify_and_import(
            tenant_id="_default",
            snapshot_dict=tampered_dict,
            audit_callback=audit_callback,
        )

        assert ok is False
        assert len(audit_events) > 0
        assert audit_events[-1]["event_type"] == "metrics_import_invalid"


class TestErrorHandling:
    """Test error handling and edge cases."""

    def test_export_invalid_tenant_id(self, tmp_path):
        """Export with invalid tenant_id fails."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))

        snapshot = exporter.export(
            tenant_id="invalid/tenant",
            metrics={"accuracy": 0.95},
            export_id="test-invalid",
        )

        assert snapshot is None

    def test_export_invalid_metrics_type(self, tmp_path):
        """Export with non-dict metrics fails."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))

        snapshot = exporter.export(
            tenant_id="_default",
            metrics=[0.95, 42],  # List, not dict
            export_id="test-invalid",
        )

        assert snapshot is None

    def test_import_invalid_tenant_id(self, tmp_path):
        """Import with invalid tenant_id fails."""
        importer = MetricsImporter(corvin_home=str(tmp_path))

        ok, error = importer.verify_and_import(
            tenant_id="invalid/tenant",
            snapshot_dict={"payload_hmac": "abc123"},
        )

        assert ok is False
        assert "Invalid tenant_id" in error

    def test_import_invalid_snapshot_type(self, tmp_path):
        """Import with non-dict snapshot fails."""
        importer = MetricsImporter(corvin_home=str(tmp_path))

        ok, error = importer.verify_and_import(
            tenant_id="_default",
            snapshot_dict=[0.95, 42],  # List, not dict
        )

        assert ok is False
        assert "Snapshot must be a dict" in error

    def test_import_missing_hmac(self, tmp_path):
        """Import with missing HMAC fails."""
        importer = MetricsImporter(corvin_home=str(tmp_path))

        ok, error = importer.verify_and_import(
            tenant_id="_default",
            snapshot_dict={"metrics": {"accuracy": 0.95}},
        )

        assert ok is False
        assert "payload_hmac is required" in error
