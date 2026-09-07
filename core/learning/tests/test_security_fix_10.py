"""Test Security Fix #10: Export Tampering Protection via HMAC-SHA256.

Tests verify that metrics export/import is protected against tampering using
HMAC-SHA256 signatures. Attack vectors:
  - Metric values modified in transit
  - Export ID changed
  - Tenant ID forged
  - Entire payload replaced

All tampering attempts must be detected and rejected (fail-closed).
"""

import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock
from datetime import datetime

from core.learning.export_import import (
    MetricsExporter,
    MetricsImporter,
    BulkMetricsProcessor,
    ExportedMetrics,
    canonical_json,
)
from core.infinite_session.crypto_binding import CryptoBinding


class TestExportHMACComputed:
    """Test that export includes valid payload_hmac."""

    def test_export_hmac_computed(self, tmp_path):
        """Export includes valid payload_hmac field."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))

        export_data = exporter.export_metrics(
            tenant_id="_default",
            metrics={"accuracy": 0.95, "latency_ms": 42},
            export_id="test-export-001",
        )

        assert export_data is not None
        assert hasattr(export_data, "payload_hmac")
        assert isinstance(export_data.payload_hmac, str)
        assert len(export_data.payload_hmac) == 64  # SHA256 hex is 64 chars

    def test_export_hmac_valid_signature(self, tmp_path):
        """Exported HMAC is valid and can be verified."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        crypto = CryptoBinding(corvin_home=str(tmp_path))

        export_data = exporter.export_metrics(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="test-export-002",
        )

        assert export_data is not None

        # Verify the HMAC is correct
        verified, error = crypto.verify_bytes(
            "_default",
            canonical_json(export_data.payload),
            export_data.payload_hmac,
        )
        assert verified is True
        assert error == ""

    def test_export_hmac_different_metrics_different_hmac(self, tmp_path):
        """Different metrics produce different HMACs."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))

        export_1 = exporter.export_metrics(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="export-1",
        )

        export_2 = exporter.export_metrics(
            tenant_id="_default",
            metrics={"accuracy": 0.94},
            export_id="export-2",
        )

        assert export_1 is not None
        assert export_2 is not None
        assert export_1.payload_hmac != export_2.payload_hmac


class TestExportHMACTenantScoped:
    """Test that different tenants have different HMACs for same payload."""

    def test_export_hmac_tenant_scoped(self, tmp_path):
        """Different tenants have different HMACs for identical metrics."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))

        # Create signing keys for both tenants
        crypto = CryptoBinding(corvin_home=str(tmp_path))
        crypto._ensure_key_exists("_default")
        crypto._ensure_key_exists("tenant-2")

        metrics = {"accuracy": 0.95, "latency_ms": 42}

        # Export same metrics for different tenants
        export_default = exporter.export_metrics(
            tenant_id="_default",
            metrics=metrics,
            export_id="test-001",
        )

        export_tenant2 = exporter.export_metrics(
            tenant_id="tenant-2",
            metrics=metrics,
            export_id="test-001",
        )

        assert export_default is not None
        assert export_tenant2 is not None
        # HMACs should differ due to different tenant keys
        assert export_default.payload_hmac != export_tenant2.payload_hmac

    def test_export_hmac_cross_tenant_verification_fails(self, tmp_path):
        """HMAC from tenant-1 cannot be verified with tenant-2's key."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        importer = MetricsImporter(corvin_home=str(tmp_path))
        crypto = CryptoBinding(corvin_home=str(tmp_path))

        # Ensure both tenant keys exist
        crypto._ensure_key_exists("_default")
        crypto._ensure_key_exists("tenant-2")

        # Export from tenant-1
        export_data = exporter.export_metrics(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="test-001",
        )

        assert export_data is not None

        # Try to verify with tenant-2's key (should fail)
        ok, error = importer.verify_and_import(
            tenant_id="tenant-2",
            payload=export_data.payload,
            payload_hmac=export_data.payload_hmac,
        )

        assert ok is False
        assert "HMAC verification failed" in error


class TestImportHMACValid:
    """Test that import with correct HMAC succeeds."""

    def test_import_hmac_valid(self, tmp_path):
        """Import with correct HMAC succeeds."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        importer = MetricsImporter(corvin_home=str(tmp_path))

        # Export metrics
        export_data = exporter.export_metrics(
            tenant_id="_default",
            metrics={"accuracy": 0.95, "latency_ms": 42},
            export_id="test-import-001",
        )

        assert export_data is not None

        # Import with correct HMAC
        ok, error = importer.verify_and_import(
            tenant_id="_default",
            payload=export_data.payload,
            payload_hmac=export_data.payload_hmac,
        )

        assert ok is True
        assert error == ""

    def test_import_hmac_valid_with_callback(self, tmp_path):
        """Import with correct HMAC invokes callback."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        importer = MetricsImporter(corvin_home=str(tmp_path))

        # Export metrics
        export_data = exporter.export_metrics(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="test-import-002",
        )

        assert export_data is not None

        # Track callback invocation
        callback_invoked = []

        def on_valid_callback(payload):
            callback_invoked.append(payload)

        # Import with callback
        ok, error = importer.verify_and_import(
            tenant_id="_default",
            payload=export_data.payload,
            payload_hmac=export_data.payload_hmac,
            on_valid=on_valid_callback,
        )

        assert ok is True
        assert error == ""
        assert len(callback_invoked) == 1
        assert callback_invoked[0] == export_data.payload


class TestImportHMACTampered:
    """Test that import with modified payload fails HMAC check."""

    def test_import_hmac_tampered_accuracy(self, tmp_path):
        """Import fails when accuracy metric is modified in transit."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        importer = MetricsImporter(corvin_home=str(tmp_path))

        # Export metrics
        export_data = exporter.export_metrics(
            tenant_id="_default",
            metrics={"accuracy": 0.95, "latency_ms": 42},
            export_id="test-tamper-001",
        )

        assert export_data is not None

        # Tamper: modify accuracy in the payload
        tampered_payload = dict(export_data.payload)
        tampered_payload["metrics"] = dict(export_data.payload["metrics"])
        tampered_payload["metrics"]["accuracy"] = 0.99  # Changed!

        # Import tampered payload with original HMAC
        ok, error = importer.verify_and_import(
            tenant_id="_default",
            payload=tampered_payload,
            payload_hmac=export_data.payload_hmac,
        )

        assert ok is False
        assert "HMAC verification failed" in error

    def test_import_hmac_tampered_export_id(self, tmp_path):
        """Import fails when export_id is modified."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        importer = MetricsImporter(corvin_home=str(tmp_path))

        # Export metrics
        export_data = exporter.export_metrics(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="test-tamper-002",
        )

        assert export_data is not None

        # Tamper: modify export_id
        tampered_payload = dict(export_data.payload)
        tampered_payload["export_id"] = "test-tamper-FORGED"

        # Import tampered payload
        ok, error = importer.verify_and_import(
            tenant_id="_default",
            payload=tampered_payload,
            payload_hmac=export_data.payload_hmac,
        )

        assert ok is False
        assert "HMAC verification failed" in error

    def test_import_hmac_tampered_tenant_id(self, tmp_path):
        """Import fails when tenant_id in payload is modified."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        importer = MetricsImporter(corvin_home=str(tmp_path))
        crypto = CryptoBinding(corvin_home=str(tmp_path))
        crypto._ensure_key_exists("tenant-2")

        # Export from tenant-1
        export_data = exporter.export_metrics(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="test-tamper-003",
        )

        assert export_data is not None

        # Tamper: change tenant_id in payload
        tampered_payload = dict(export_data.payload)
        tampered_payload["tenant_id"] = "tenant-2"

        # Try to import with original HMAC (should fail)
        ok, error = importer.verify_and_import(
            tenant_id="_default",
            payload=tampered_payload,
            payload_hmac=export_data.payload_hmac,
        )

        assert ok is False
        assert "HMAC verification failed" in error

    def test_import_hmac_tampered_hmac_string(self, tmp_path):
        """Import fails when HMAC string itself is corrupted."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        importer = MetricsImporter(corvin_home=str(tmp_path))

        # Export metrics
        export_data = exporter.export_metrics(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="test-tamper-004",
        )

        assert export_data is not None

        # Tamper: flip a bit in the HMAC
        tampered_hmac = (
            "0" * 63 + "f"
        )  # Replace first 63 chars with 0s, last with f

        # Import with corrupted HMAC
        ok, error = importer.verify_and_import(
            tenant_id="_default",
            payload=export_data.payload,
            payload_hmac=tampered_hmac,
        )

        assert ok is False
        assert "HMAC verification failed" in error


class TestImportHMACMismatchAuditLogged:
    """Test that metrics_import_invalid audit event is logged on HMAC failure."""

    def test_import_hmac_mismatch_audit_logged(self, tmp_path):
        """HMAC mismatch triggers metrics_import_invalid audit event."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        importer = MetricsImporter(corvin_home=str(tmp_path))

        # Export metrics
        export_data = exporter.export_metrics(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="test-audit-001",
        )

        assert export_data is not None

        # Track audit events
        audit_events = []

        def audit_callback(event):
            audit_events.append(event)

        # Tamper and import
        tampered_payload = dict(export_data.payload)
        tampered_payload["metrics"]["accuracy"] = 0.99

        ok, error = importer.verify_and_import(
            tenant_id="_default",
            payload=tampered_payload,
            payload_hmac=export_data.payload_hmac,
            audit_callback=audit_callback,
        )

        assert ok is False
        assert len(audit_events) > 0

        # Check audit event
        audit_event = audit_events[-1]
        assert audit_event["event_type"] == "metrics_import_invalid"
        assert audit_event["tenant_id"] == "_default"
        assert "HMAC verification failed" in audit_event["reason"]

    def test_import_hmac_empty_string_audit_logged(self, tmp_path):
        """Empty HMAC string triggers audit event."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        importer = MetricsImporter(corvin_home=str(tmp_path))

        # Export metrics
        export_data = exporter.export_metrics(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="test-audit-002",
        )

        assert export_data is not None

        # Track audit events
        audit_events = []

        def audit_callback(event):
            audit_events.append(event)

        # Import with empty HMAC
        ok, error = importer.verify_and_import(
            tenant_id="_default",
            payload=export_data.payload,
            payload_hmac="",
            audit_callback=audit_callback,
        )

        assert ok is False
        assert len(audit_events) > 0
        assert audit_events[-1]["event_type"] == "metrics_import_invalid"


class TestImportHMACRejectsStateUpdate:
    """Test that state is NOT updated if HMAC is invalid."""

    def test_import_hmac_rejects_state_update(self, tmp_path):
        """Callback is NOT invoked on HMAC verification failure."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        importer = MetricsImporter(corvin_home=str(tmp_path))

        # Export metrics
        export_data = exporter.export_metrics(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="test-state-001",
        )

        assert export_data is not None

        # Tamper payload
        tampered_payload = dict(export_data.payload)
        tampered_payload["metrics"]["accuracy"] = 0.99

        # Track callback
        callback_invoked = []

        def on_valid_callback(payload):
            callback_invoked.append(payload)
            raise AssertionError("Callback should NOT be invoked!")

        # Import with tampered HMAC
        ok, error = importer.verify_and_import(
            tenant_id="_default",
            payload=tampered_payload,
            payload_hmac=export_data.payload_hmac,
            on_valid=on_valid_callback,
        )

        assert ok is False
        assert len(callback_invoked) == 0  # Callback NOT invoked
        assert "HMAC verification failed" in error

    def test_import_hmac_callback_failure_audited(self, tmp_path):
        """Callback failure is audited but state still not updated."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        importer = MetricsImporter(corvin_home=str(tmp_path))

        # Export metrics
        export_data = exporter.export_metrics(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="test-state-002",
        )

        assert export_data is not None

        # Callback that fails
        def failing_callback(payload):
            raise RuntimeError("Database connection failed")

        # Track audit events
        audit_events = []

        def audit_callback(event):
            audit_events.append(event)

        # Import with valid HMAC but failing callback
        ok, error = importer.verify_and_import(
            tenant_id="_default",
            payload=export_data.payload,
            payload_hmac=export_data.payload_hmac,
            on_valid=failing_callback,
            audit_callback=audit_callback,
        )

        assert ok is False
        assert "Callback failed" in error

        # Audit event should be metrics_import_callback_failed
        callback_failed_events = [
            e for e in audit_events if e["event_type"] == "metrics_import_callback_failed"
        ]
        assert len(callback_failed_events) > 0


class TestBulkMetricsProcessor:
    """Test batch export/import with HMAC protection."""

    def test_bulk_export_batch(self, tmp_path):
        """Batch export produces ExportedMetrics with valid HMACs."""
        processor = BulkMetricsProcessor(corvin_home=str(tmp_path))

        metrics_list = [
            {"accuracy": 0.95, "latency_ms": 42},
            {"accuracy": 0.94, "latency_ms": 43},
            {"accuracy": 0.96, "latency_ms": 41},
        ]

        exported, errors = processor.export_batch(
            tenant_id="_default",
            metrics_list=metrics_list,
        )

        assert len(errors) == 0
        assert len(exported) == 3
        for exp in exported:
            assert exp.payload_hmac is not None
            assert len(exp.payload_hmac) == 64

    def test_bulk_import_batch(self, tmp_path):
        """Batch import verifies all HMACs."""
        processor = BulkMetricsProcessor(corvin_home=str(tmp_path))

        metrics_list = [
            {"accuracy": 0.95},
            {"accuracy": 0.94},
            {"accuracy": 0.96},
        ]

        # Export
        exported, errors = processor.export_batch(
            tenant_id="_default",
            metrics_list=metrics_list,
        )

        assert len(errors) == 0

        # Import
        success_count, import_errors = processor.import_batch(
            tenant_id="_default",
            exported_list=exported,
        )

        assert success_count == 3
        assert len(import_errors) == 0

    def test_bulk_import_batch_with_tampered(self, tmp_path):
        """Batch import rejects tampered metrics."""
        processor = BulkMetricsProcessor(corvin_home=str(tmp_path))

        metrics_list = [
            {"accuracy": 0.95},
            {"accuracy": 0.94},
            {"accuracy": 0.96},
        ]

        # Export
        exported, errors = processor.export_batch(
            tenant_id="_default",
            metrics_list=metrics_list,
        )

        # Tamper the second export
        tampered_exported = list(exported)
        tampered_payload = dict(exported[1].payload)
        tampered_payload["metrics"]["accuracy"] = 0.50
        tampered_exported[1] = ExportedMetrics(
            payload=tampered_payload,
            payload_hmac=exported[1].payload_hmac,  # Old HMAC
            tenant_id=exported[1].tenant_id,
            timestamp=exported[1].timestamp,
            export_id=exported[1].export_id,
        )

        # Import should fail for the tampered one
        success_count, import_errors = processor.import_batch(
            tenant_id="_default",
            exported_list=tampered_exported,
        )

        assert success_count == 2  # Only 2 succeed
        assert len(import_errors) == 1
        assert "HMAC verification failed" in import_errors[0]


class TestInvalidInputHandling:
    """Test that invalid inputs are handled safely."""

    def test_export_invalid_tenant_id(self, tmp_path):
        """Export with invalid tenant_id fails."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))

        export_data = exporter.export_metrics(
            tenant_id="invalid/tenant",
            metrics={"accuracy": 0.95},
            export_id="test-invalid-001",
        )

        assert export_data is None

    def test_export_invalid_metrics_type(self, tmp_path):
        """Export with non-dict metrics fails."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))

        export_data = exporter.export_metrics(
            tenant_id="_default",
            metrics=[0.95, 42],  # List, not dict
            export_id="test-invalid-002",
        )

        assert export_data is None

    def test_import_invalid_payload_type(self, tmp_path):
        """Import with non-dict payload fails."""
        importer = MetricsImporter(corvin_home=str(tmp_path))

        ok, error = importer.verify_and_import(
            tenant_id="_default",
            payload=[0.95, 42],  # List, not dict
            payload_hmac="abc123",
        )

        assert ok is False
        assert "Payload must be a dict" in error

    def test_import_missing_hmac(self, tmp_path):
        """Import with missing HMAC fails."""
        importer = MetricsImporter(corvin_home=str(tmp_path))

        ok, error = importer.verify_and_import(
            tenant_id="_default",
            payload={"metrics": {"accuracy": 0.95}},
            payload_hmac="",
        )

        assert ok is False
        assert "payload_hmac is required" in error


class TestAuditTrail:
    """Test that all operations are audited."""

    def test_export_audit_event(self, tmp_path):
        """Export operation is audited."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))

        audit_events = []

        def audit_callback(event):
            audit_events.append(event)

        export_data = exporter.export_metrics(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="test-audit-export",
            audit_callback=audit_callback,
        )

        assert export_data is not None
        assert len(audit_events) == 1
        assert audit_events[0]["event_type"] == "metrics_exported"
        assert audit_events[0]["export_id"] == "test-audit-export"

    def test_import_audit_event(self, tmp_path):
        """Import operation is audited."""
        exporter = MetricsExporter(corvin_home=str(tmp_path))
        importer = MetricsImporter(corvin_home=str(tmp_path))

        # Export
        export_data = exporter.export_metrics(
            tenant_id="_default",
            metrics={"accuracy": 0.95},
            export_id="test-audit-import",
        )

        assert export_data is not None

        audit_events = []

        def audit_callback(event):
            audit_events.append(event)

        # Import
        ok, error = importer.verify_and_import(
            tenant_id="_default",
            payload=export_data.payload,
            payload_hmac=export_data.payload_hmac,
            audit_callback=audit_callback,
        )

        assert ok is True
        assert len(audit_events) == 1
        assert audit_events[0]["event_type"] == "metrics_imported"
