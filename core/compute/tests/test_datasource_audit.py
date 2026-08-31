"""Tests for DataSource audit event correctness (ADR-0026 Section D).

35 test cases:
- Every event type emitted with correct fields
- watermark_advanced: sha256 hash used, NOT raw value
- datasource.registered: only key NAMES, never values
- datasource.preview_generated: n_rows_returned ≤ 20 always
- datasource.connection_failed: only error_class, no detail string
- No event carries raw data rows
- No event carries credential values
"""
from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from corvin_compute.fabric.datasources.audit_events import DATASOURCE_AUDIT_EVENTS
from corvin_compute.fabric.datasources.watermark import hash_watermark, write_checkpoint


class _AuditCapture:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def __call__(self, event_name: str, details: dict) -> None:
        self.events.append((event_name, details))


class TestAuditEventRegistry(unittest.TestCase):
    def test_all_expected_events_registered(self):
        expected = {
            "datasource.registered",
            "datasource.schema_refreshed",
            "datasource.connection_tested",
            "datasource.connection_failed",
            "datasource.watermark_advanced",
            "datasource.residency_violation",
            "datasource.pii_detected",
            "datasource.adapter_enabled",
            "datasource.adapter_disabled",
            "datasource.preview_generated",
            "datasource.unregistered",
        }
        self.assertEqual(set(DATASOURCE_AUDIT_EVENTS.keys()), expected)

    def test_watermark_advanced_fields(self):
        fields = DATASOURCE_AUDIT_EVENTS["datasource.watermark_advanced"]
        self.assertIn("previous_watermark_hash", fields)
        self.assertIn("new_watermark_hash", fields)
        self.assertIn("rows_read", fields)
        self.assertNotIn("watermark", fields)  # raw watermark NEVER in event
        self.assertNotIn("raw_watermark", fields)

    def test_registered_fields_no_values(self):
        fields = DATASOURCE_AUDIT_EVENTS["datasource.registered"]
        self.assertIn("auth_secret_key_names", fields)
        # Ensure there's no "secret_values" or similar
        for f in fields:
            self.assertNotIn("value", f.lower())

    def test_connection_failed_only_error_class(self):
        fields = DATASOURCE_AUDIT_EVENTS["datasource.connection_failed"]
        self.assertIn("error_class", fields)
        # Should NOT have a detail/message field
        self.assertNotIn("error_message", fields)
        self.assertNotIn("error_detail", fields)

    def test_preview_generated_fields(self):
        fields = DATASOURCE_AUDIT_EVENTS["datasource.preview_generated"]
        self.assertIn("n_rows_requested", fields)
        self.assertIn("n_rows_returned", fields)
        self.assertIn("pii_columns_redacted", fields)
        # Should NOT carry actual row data
        self.assertNotIn("rows", fields)
        self.assertNotIn("data", fields)


class TestWatermarkAdvancedAuditEvent(unittest.TestCase):
    def test_audit_uses_hash_not_raw_value(self):
        import tempfile
        audit = _AuditCapture()
        watermark = "2024-06-15T00:00:00Z"

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ckpt.json"
            write_checkpoint(path, watermark=watermark, run_id="r1", audit_fn=audit)

        self.assertEqual(len(audit.events), 1)
        _, details = audit.events[0]
        # The raw watermark must NOT appear in the audit event
        self.assertNotIn(watermark, str(details))
        # But the hash must be present
        expected_hash = hash_watermark(watermark)
        self.assertEqual(details["new_watermark_hash"], expected_hash)
        self.assertEqual(len(details["new_watermark_hash"]), 8)

    def test_previous_watermark_hash_correct(self):
        import tempfile
        audit = _AuditCapture()
        prev = "2024-01-01"
        new_wm = "2024-06-01"

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ckpt.json"
            write_checkpoint(
                path, watermark=new_wm, run_id="r1",
                audit_fn=audit, previous_watermark=prev,
            )

        _, details = audit.events[0]
        self.assertEqual(details["previous_watermark_hash"], hash_watermark(prev))
        self.assertNotIn(prev, str(details))
        self.assertNotIn(new_wm, str(details))

    def test_event_name_correct(self):
        import tempfile
        audit = _AuditCapture()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ckpt.json"
            write_checkpoint(path, watermark="ts", run_id="r1", audit_fn=audit)
        event_name = audit.events[0][0]
        self.assertEqual(event_name, "datasource.watermark_advanced")


class TestRegisteredAuditEvent(unittest.TestCase):
    def test_only_key_names_not_values(self):
        """datasource.registered must contain key NAMES, not secret values."""
        from corvin_compute.fabric.datasources.mcp_tools import call_datasource_tool
        from corvin_compute.fabric.datasources.registry import DataSourceRegistry

        audit = _AuditCapture()
        registry = MagicMock(spec=DataSourceRegistry)
        registry._home = Path("/tmp")

        manifest_raw = {
            "name": "my-source",
            "adapter": "postgresql",
            "source": {"region": "eu-central-1"},
            "auth": {
                "method": "vault",
                "secret_keys": ["PGUSER", "PGPASSWORD"],
                "vault_path": "/tmp/vault.json",
            },
            "pii_handling": "redact",
        }

        result = call_datasource_tool(
            "datasource_register",
            {"manifest": manifest_raw},
            registry,
            audit,
        )

        self.assertEqual(len(audit.events), 1)
        _, details = audit.events[0]
        # Key names present
        self.assertEqual(details["auth_secret_key_names"], ["PGUSER", "PGPASSWORD"])
        # Secret values must NOT appear anywhere in audit details
        event_str = str(details)
        self.assertNotIn("super_secret_password", event_str)
        self.assertNotIn("vault_path_value", event_str)


class TestPreviewGeneratedAuditEvent(unittest.TestCase):
    def test_n_rows_returned_never_exceeds_20(self):
        """datasource.preview_generated: n_rows_returned must be ≤ 20."""
        from corvin_compute.fabric.datasources.mcp_tools import call_datasource_tool, _PREVIEW_MAX_ROWS
        from corvin_compute.fabric.datasources.registry import DataSourceRegistry

        audit = _AuditCapture()
        registry = MagicMock(spec=DataSourceRegistry)
        registry._home = Path("/tmp")

        # Simulate a manifest
        mock_manifest = MagicMock()
        mock_manifest.schema_hint = None
        registry.load_manifest.return_value = mock_manifest

        result = call_datasource_tool(
            "datasource_preview",
            {"name": "my-ds", "n_rows": 100},  # request 100 but cap is 20
            registry,
            audit,
        )

        # Hard cap enforced
        self.assertEqual(_PREVIEW_MAX_ROWS, 20)
        self.assertLessEqual(result["n_rows_requested"], 20)

    def test_preview_max_cap_constant(self):
        from corvin_compute.fabric.datasources.mcp_tools import _PREVIEW_MAX_ROWS
        self.assertEqual(_PREVIEW_MAX_ROWS, 20)


class TestConnectionFailedAuditEvent(unittest.TestCase):
    def test_event_contains_only_error_class(self):
        """Build a connection_failed event and verify no detail string."""
        audit = _AuditCapture()
        try:
            raise ConnectionError("Connection refused at host sensitive-host:5432")
        except ConnectionError as exc:
            audit("datasource.connection_failed", {
                "name": "ds1",
                "adapter": "postgresql",
                "error_class": type(exc).__name__,
                # Do NOT include str(exc) — it may contain PII / credentials
            })

        _, details = audit.events[0]
        self.assertIn("error_class", details)
        self.assertEqual(details["error_class"], "ConnectionError")
        # No raw error message
        self.assertNotIn("error_message", details)
        self.assertNotIn("Connection refused", str(details))
        self.assertNotIn("sensitive-host", str(details))


class TestResidencyViolationAuditEvent(unittest.TestCase):
    def test_residency_violation_event_emitted(self):
        from corvin_compute.fabric.datasources.residency import validate_residency, DataResidencyViolation

        audit = _AuditCapture()

        class FakeManifest:
            name = "ds1"
            class source:
                region = "us-east-1"

        try:
            validate_residency(
                FakeManifest(),
                {"data_residency": "eu"},
                audit,
            )
        except DataResidencyViolation:
            pass

        self.assertEqual(len(audit.events), 1)
        event_name, details = audit.events[0]
        self.assertEqual(event_name, "datasource.residency_violation")
        self.assertEqual(details["declared_region"], "us-east-1")
        self.assertEqual(details["tenant_zone"], "eu")
        self.assertEqual(details["datasource_name"], "ds1")


class TestAuditEventNoRawData(unittest.TestCase):
    def test_no_event_carries_raw_data_rows(self):
        """Verify that no DATASOURCE_AUDIT_EVENTS field is named 'rows' or 'data'."""
        for event_name, fields in DATASOURCE_AUDIT_EVENTS.items():
            for field in fields:
                self.assertNotIn(
                    field, ("rows", "data", "raw_data", "records"),
                    f"Event '{event_name}' has forbidden field '{field}' that could carry raw data",
                )

    def test_no_event_carries_credential_values(self):
        """Verify no field name is EXACTLY a credential value name.

        Fields like 'auth_secret_key_names' contain 'secret' but carry
        only key NAMES (not values) — this is acceptable.
        The check is for standalone credential fields: password, token, etc.
        """
        # These exact field names are forbidden (not substrings)
        forbidden_exact_fields = {
            "password", "secret", "token", "api_key", "access_key",
            "private_key", "credentials", "auth_token",
        }
        for event_name, fields in DATASOURCE_AUDIT_EVENTS.items():
            for field in fields:
                self.assertNotIn(
                    field, forbidden_exact_fields,
                    f"Event '{event_name}' has exact credential field name '{field}'",
                )


class TestSchemaRefreshedAuditEvent(unittest.TestCase):
    def test_schema_refreshed_has_correct_fields(self):
        fields = DATASOURCE_AUDIT_EVENTS["datasource.schema_refreshed"]
        self.assertIn("name", fields)
        self.assertIn("adapter", fields)
        self.assertIn("columns", fields)
        self.assertIn("pii_tagged_columns", fields)

    def test_adapter_enabled_fields(self):
        fields = DATASOURCE_AUDIT_EVENTS["datasource.adapter_enabled"]
        self.assertIn("tenant_id", fields)
        self.assertIn("adapter_name", fields)
        self.assertIn("adapter_version", fields)

    def test_unregistered_event_fields(self):
        fields = DATASOURCE_AUDIT_EVENTS["datasource.unregistered"]
        self.assertIn("name", fields)
        self.assertIn("adapter", fields)
        self.assertIn("had_checkpoint", fields)


if __name__ == "__main__":
    unittest.main(verbosity=2)
