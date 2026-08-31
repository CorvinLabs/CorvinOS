"""Tests for DataSource MCP tool definitions and dispatcher (ADR-0026 Section D).

25 test cases:
- datasource_register returns handle + schema_snapshot
- datasource_list returns connection summaries
- datasource_schema returns PII-redacted snapshot
- datasource_test returns {ok, latency_ms}
- datasource_unregister removes manifest + checkpoint
- datasource_preview hard cap at 20 rows (n_rows=100 → ≤ 20)
- datasource_preview applies PII redaction
- FabricNotEnabled returned when fabric_enabled=False
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from corvin_compute.fabric.datasources.mcp_tools import (
    call_datasource_tool,
    datasource_register_tool_def,
    datasource_list_tool_def,
    datasource_schema_tool_def,
    datasource_test_tool_def,
    datasource_unregister_tool_def,
    datasource_preview_tool_def,
    _PREVIEW_MAX_ROWS,
)
from corvin_compute.fabric.datasources.registry import DataSourceRegistry, ConnectionSummary


def _make_registry():
    reg = MagicMock(spec=DataSourceRegistry)
    reg._home = Path("/tmp/fake_home")
    return reg


def _noop_audit(event, details):
    pass


class TestToolDefinitions(unittest.TestCase):
    def test_register_tool_def_has_name(self):
        td = datasource_register_tool_def()
        self.assertEqual(td["name"], "datasource_register")
        self.assertIn("inputSchema", td)

    def test_list_tool_def(self):
        td = datasource_list_tool_def()
        self.assertEqual(td["name"], "datasource_list")

    def test_schema_tool_def(self):
        td = datasource_schema_tool_def()
        self.assertEqual(td["name"], "datasource_schema")
        self.assertIn("name", td["inputSchema"]["properties"])

    def test_test_tool_def(self):
        td = datasource_test_tool_def()
        self.assertEqual(td["name"], "datasource_test")

    def test_unregister_tool_def(self):
        td = datasource_unregister_tool_def()
        self.assertEqual(td["name"], "datasource_unregister")

    def test_preview_tool_def_max_20(self):
        td = datasource_preview_tool_def()
        self.assertEqual(td["name"], "datasource_preview")
        n_rows_schema = td["inputSchema"]["properties"]["n_rows"]
        self.assertEqual(n_rows_schema.get("maximum"), 20)

    def test_preview_max_rows_constant(self):
        self.assertEqual(_PREVIEW_MAX_ROWS, 20)


class TestFabricDisabled(unittest.TestCase):
    def test_fabric_not_enabled_returns_error(self):
        reg = _make_registry()
        result = call_datasource_tool(
            "datasource_list",
            {},
            reg,
            _noop_audit,
            tenant_config={"fabric_enabled": False},
        )
        self.assertIn("error", result)
        self.assertEqual(result["error"], "FabricNotEnabled")

    def test_fabric_enabled_passes_through(self):
        reg = _make_registry()
        reg.list_connections.return_value = []
        result = call_datasource_tool(
            "datasource_list",
            {},
            reg,
            _noop_audit,
            tenant_config={"fabric_enabled": True},
        )
        self.assertNotIn("error", result)


class TestDatasourceRegister(unittest.TestCase):
    def test_register_valid_manifest(self):
        reg = _make_registry()
        manifest_raw = {
            "name": "my-source",
            "adapter": "postgresql",
            "source": {"region": "eu-central-1"},
            "auth": {"method": "vault", "secret_keys": ["PG_USER"]},
            "pii_handling": "redact",
        }
        audit_events = []
        result = call_datasource_tool(
            "datasource_register",
            {"manifest": manifest_raw},
            reg,
            lambda e, d: audit_events.append((e, d)),
        )
        self.assertIn("handle", result)
        self.assertEqual(result["handle"], "my-source")
        self.assertEqual(result["adapter"], "postgresql")
        self.assertEqual(len(audit_events), 1)
        self.assertEqual(audit_events[0][0], "datasource.registered")

    def test_register_invalid_auth_method(self):
        reg = _make_registry()
        result = call_datasource_tool(
            "datasource_register",
            {"manifest": {
                "name": "ds1",
                "adapter": "postgresql",
                "source": {"region": "eu-central-1"},
                "auth": {"method": "basic"},
            }},
            reg,
            _noop_audit,
        )
        self.assertEqual(result["error"], "InvalidAuthMethod")

    def test_register_audit_has_key_names_not_values(self):
        reg = _make_registry()
        audit_events = []
        call_datasource_tool(
            "datasource_register",
            {"manifest": {
                "name": "ds2",
                "adapter": "postgresql",
                "source": {"region": "eu-central-1"},
                "auth": {"method": "vault", "secret_keys": ["PGUSER", "PGPASS"]},
                "pii_handling": "redact",
            }},
            reg,
            lambda e, d: audit_events.append((e, d)),
        )
        _, details = audit_events[0]
        self.assertEqual(details["auth_secret_key_names"], ["PGUSER", "PGPASS"])


class TestDatasourceList(unittest.TestCase):
    def test_list_returns_connection_summaries(self):
        reg = _make_registry()
        reg.list_connections.return_value = [
            ConnectionSummary(name="ds1", adapter="postgresql", region="eu-central-1"),
            ConnectionSummary(name="ds2", adapter="snowflake", region="us-east-1"),
        ]
        result = call_datasource_tool("datasource_list", {}, reg, _noop_audit)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["connections"][0]["name"], "ds1")

    def test_list_empty_returns_zero(self):
        reg = _make_registry()
        reg.list_connections.return_value = []
        result = call_datasource_tool("datasource_list", {}, reg, _noop_audit)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["connections"], [])


class TestDatasourceSchema(unittest.TestCase):
    def test_schema_returns_columns(self):
        reg = _make_registry()
        mock_manifest = MagicMock()
        mock_manifest.adapter = "postgresql"
        mock_manifest.schema_hint = {
            "columns": [
                {"name": "id", "dtype": "integer", "pii_tagged": False},
                {"name": "email", "dtype": "string", "pii_tagged": True},
            ]
        }
        reg.load_manifest.return_value = mock_manifest

        result = call_datasource_tool(
            "datasource_schema",
            {"name": "ds1"},
            reg,
            _noop_audit,
        )
        self.assertIn("columns", result)
        self.assertIn("email", result["pii_tagged_columns"])
        self.assertNotIn("id", result["pii_tagged_columns"])

    def test_schema_not_found(self):
        reg = _make_registry()
        reg.load_manifest.side_effect = FileNotFoundError("not found")
        result = call_datasource_tool("datasource_schema", {"name": "x"}, reg, _noop_audit)
        self.assertEqual(result["error"], "NotFound")


class TestDatasourceTest(unittest.TestCase):
    def test_test_returns_ok_and_latency(self):
        reg = _make_registry()
        mock_manifest = MagicMock()
        mock_manifest.adapter = "postgresql"
        reg.load_manifest.return_value = mock_manifest

        result = call_datasource_tool("datasource_test", {"name": "ds1"}, reg, _noop_audit)
        self.assertIn("ok", result)
        self.assertIn("latency_ms", result)
        # The MCP path only validates the manifest — it cannot probe
        # connectivity (no bwrap / no vault), so it MUST NOT report a green
        # ok=True. Real reachability runs via registry.test_connection.
        self.assertFalse(result["ok"])
        self.assertIn("not_tested", result["note"])

    def test_test_emits_audit_event(self):
        reg = _make_registry()
        mock_manifest = MagicMock()
        mock_manifest.adapter = "postgresql"
        reg.load_manifest.return_value = mock_manifest

        events = []
        call_datasource_tool("datasource_test", {"name": "ds1"}, reg,
                             lambda e, d: events.append((e, d)))
        self.assertEqual(events[0][0], "datasource.connection_tested")


class TestDatasourcePreview(unittest.TestCase):
    def test_preview_hard_cap_at_20(self):
        reg = _make_registry()
        mock_manifest = MagicMock()
        mock_manifest.schema_hint = None
        reg.load_manifest.return_value = mock_manifest

        result = call_datasource_tool(
            "datasource_preview",
            {"name": "ds1", "n_rows": 100},  # requesting 100
            reg,
            _noop_audit,
        )
        self.assertLessEqual(result["n_rows_requested"], 20)

    def test_preview_pii_columns_redacted(self):
        reg = _make_registry()
        mock_manifest = MagicMock()
        mock_manifest.schema_hint = {
            "columns": [
                {"name": "id", "dtype": "integer", "pii_tagged": False},
                {"name": "email", "dtype": "string", "pii_tagged": True},
                {"name": "phone", "dtype": "string", "pii_tagged": True},
            ]
        }
        reg.load_manifest.return_value = mock_manifest

        result = call_datasource_tool(
            "datasource_preview",
            {"name": "ds1", "n_rows": 5},
            reg,
            _noop_audit,
        )
        self.assertIn("email", result["pii_columns_redacted"])
        self.assertIn("phone", result["pii_columns_redacted"])
        self.assertNotIn("id", result["pii_columns_redacted"])

    def test_preview_emits_audit_event(self):
        reg = _make_registry()
        mock_manifest = MagicMock()
        mock_manifest.schema_hint = None
        reg.load_manifest.return_value = mock_manifest

        events = []
        call_datasource_tool(
            "datasource_preview", {"name": "ds1"}, reg,
            lambda e, d: events.append((e, d)),
        )
        self.assertEqual(events[0][0], "datasource.preview_generated")

    def test_preview_not_found(self):
        reg = _make_registry()
        reg.load_manifest.side_effect = FileNotFoundError("not found")
        result = call_datasource_tool("datasource_preview", {"name": "x"}, reg, _noop_audit)
        self.assertEqual(result["error"], "NotFound")


class TestDatasourceUnregister(unittest.TestCase):
    def test_unregister_emits_audit(self):
        reg = _make_registry()
        reg._home = Path("/tmp/fake_home_unrg")
        # Make load_manifest raise to simulate non-existing manifest
        reg.load_manifest.side_effect = FileNotFoundError("not found")

        events = []
        result = call_datasource_tool(
            "datasource_unregister",
            {"name": "ds-to-remove"},
            reg,
            lambda e, d: events.append((e, d)),
        )
        self.assertEqual(events[0][0], "datasource.unregistered")
        self.assertEqual(result["removed"], "ds-to-remove")

    def test_unknown_tool_returns_error(self):
        reg = _make_registry()
        result = call_datasource_tool("datasource_unknown_tool", {}, reg, _noop_audit)
        self.assertEqual(result["error"], "UnknownTool")


class TestNameValidationAndTenant(unittest.TestCase):
    """F5 — path-traversal / cross-tenant hardening of the dispatcher."""

    def test_unregister_path_traversal_rejected(self):
        reg = _make_registry()
        reg._home = Path("/tmp/fake_home_trav")
        events = []
        result = call_datasource_tool(
            "datasource_unregister",
            {"name": "../../../../etc/other_tenant/manifest"},
            reg,
            lambda e, d: events.append((e, d)),
        )
        self.assertEqual(result["error"], "InvalidName")
        # No unregister audit / no filesystem action for a rejected name.
        self.assertEqual(events, [])

    def test_schema_traversal_rejected(self):
        reg = _make_registry()
        result = call_datasource_tool(
            "datasource_schema", {"name": "../secret"}, reg, _noop_audit,
        )
        self.assertEqual(result["error"], "InvalidName")
        reg.load_manifest.assert_not_called()

    def test_preview_traversal_rejected(self):
        reg = _make_registry()
        result = call_datasource_tool(
            "datasource_preview", {"name": "a/b"}, reg, _noop_audit,
        )
        self.assertEqual(result["error"], "InvalidName")
        reg.load_manifest.assert_not_called()

    def test_test_traversal_rejected(self):
        reg = _make_registry()
        result = call_datasource_tool(
            "datasource_test", {"name": "x..y"}, reg, _noop_audit,
        )
        self.assertEqual(result["error"], "InvalidName")
        reg.load_manifest.assert_not_called()

    def test_tenant_id_taken_from_caller_not_args(self):
        # A tenant_id smuggled through args must be IGNORED; the authenticated
        # tenant passed by the caller is what routes the load.
        reg = _make_registry()
        mock_manifest = MagicMock()
        mock_manifest.adapter = "postgresql"
        mock_manifest.schema_hint = {"columns": []}
        reg.load_manifest.return_value = mock_manifest
        call_datasource_tool(
            "datasource_schema",
            {"name": "ds1", "tenant_id": "victim_tenant"},
            reg,
            _noop_audit,
            tenant_id="auth_tenant",
        )
        # Registry was asked for the AUTHENTICATED tenant, never the args one.
        _, kwargs = reg.load_manifest.call_args
        called_tenant = kwargs.get("tenant_id")
        if called_tenant is None:
            called_tenant = reg.load_manifest.call_args[0][1]
        self.assertEqual(called_tenant, "auth_tenant")
        self.assertNotEqual(called_tenant, "victim_tenant")

    def test_valid_name_passes(self):
        reg = _make_registry()
        mock_manifest = MagicMock()
        mock_manifest.adapter = "postgresql"
        mock_manifest.schema_hint = {"columns": []}
        reg.load_manifest.return_value = mock_manifest
        result = call_datasource_tool(
            "datasource_schema", {"name": "valid-name_1"}, reg, _noop_audit,
        )
        self.assertNotIn("error", result)


class TestResidencyFailClosed(unittest.TestCase):
    """Regression: the residency gate must fail CLOSED for schema + preview.

    Previously an unexpected exception during the residency check (e.g.
    manifest.source is None → AttributeError) was swallowed and execution fell
    through to RETURN the PII-tagged data with a clean success audit.
    """

    def _manifest_that_breaks_residency(self):
        # tenant_config with a data_residency zone forces validate_residency to
        # read manifest.source.region; source=None → AttributeError.
        m = MagicMock()
        m.name = "ds-broken"
        m.adapter = "postgresql"
        m.source = None  # → AttributeError inside validate_residency
        m.schema_hint = {
            "columns": [{"name": "email", "dtype": "string", "pii_tagged": True}],
        }
        return m

    _STRICT_TENANT = {"fabric_enabled": True, "data_residency": "eu"}

    def test_schema_denies_on_unexpected_residency_error(self):
        reg = _make_registry()
        reg.load_manifest.return_value = self._manifest_that_breaks_residency()
        events = []
        result = call_datasource_tool(
            "datasource_schema", {"name": "ds-broken"}, reg,
            lambda e, d: events.append((e, d)),
            tenant_config=self._STRICT_TENANT,
        )
        # DENIED — no data returned
        self.assertEqual(result["error"], "ResidencyCheckError")
        self.assertNotIn("columns", result)
        self.assertNotIn("pii_tagged_columns", result)
        # No clean success audit; a check-error audit was emitted
        emitted = [e for e, _ in events]
        self.assertNotIn("datasource.schema_refreshed", emitted)
        self.assertIn("datasource.residency_check_error", emitted)

    def test_preview_denies_on_unexpected_residency_error(self):
        reg = _make_registry()
        reg.load_manifest.return_value = self._manifest_that_breaks_residency()
        events = []
        result = call_datasource_tool(
            "datasource_preview", {"name": "ds-broken", "n_rows": 5}, reg,
            lambda e, d: events.append((e, d)),
            tenant_config=self._STRICT_TENANT,
        )
        self.assertEqual(result["error"], "ResidencyCheckError")
        self.assertNotIn("rows", result)
        self.assertNotIn("pii_columns_redacted", result)
        emitted = [e for e, _ in events]
        self.assertNotIn("datasource.preview_generated", emitted)
        self.assertIn("datasource.residency_check_error", emitted)

    def test_schema_still_denies_on_real_violation(self):
        # us-east-1 is outside the eu zone → DataResidencyViolation (fail-closed).
        reg = _make_registry()
        m = MagicMock()
        m.name = "ds-us"
        m.adapter = "postgresql"
        m.source.region = "us-east-1"
        m.schema_hint = {"columns": []}
        reg.load_manifest.return_value = m
        result = call_datasource_tool(
            "datasource_schema", {"name": "ds-us"}, reg, _noop_audit,
            tenant_config=self._STRICT_TENANT,
        )
        self.assertEqual(result["error"], "DataResidencyViolation")

    def test_schema_allows_when_region_in_zone(self):
        # eu-central-1 is in the eu zone → success path unaffected.
        reg = _make_registry()
        m = MagicMock()
        m.name = "ds-eu"
        m.adapter = "postgresql"
        m.source.region = "eu-central-1"
        m.schema_hint = {
            "columns": [{"name": "email", "dtype": "string", "pii_tagged": True}],
        }
        reg.load_manifest.return_value = m
        events = []
        result = call_datasource_tool(
            "datasource_schema", {"name": "ds-eu"}, reg,
            lambda e, d: events.append((e, d)),
            tenant_config=self._STRICT_TENANT,
        )
        self.assertNotIn("error", result)
        self.assertIn("email", result["pii_tagged_columns"])
        self.assertIn("datasource.schema_refreshed", [e for e, _ in events])

    def test_register_denies_on_missing_residency_module(self):
        # ImportError on the residency module must DENY (fail-closed), not
        # silently skip the check.
        reg = _make_registry()
        manifest_raw = {
            "name": "ds-imp",
            "adapter": "postgresql",
            "source": {"region": "eu-central-1"},
            "auth": {"method": "vault", "secret_keys": ["PG_USER"]},
            "pii_handling": "redact",
        }
        real_import = __import__

        def _blocked_import(name, *a, **k):
            if name.endswith("residency") or name == "residency":
                raise ImportError("residency module unavailable")
            return real_import(name, *a, **k)

        events = []
        with patch("builtins.__import__", side_effect=_blocked_import):
            result = call_datasource_tool(
                "datasource_register", {"manifest": manifest_raw}, reg,
                lambda e, d: events.append((e, d)),
                tenant_config={"fabric_enabled": True, "data_residency": "eu"},
            )
        self.assertEqual(result["error"], "ResidencyCheckError")
        self.assertNotIn("handle", result)
        emitted = [e for e, _ in events]
        self.assertNotIn("datasource.registered", emitted)
        self.assertIn("datasource.residency_check_error", emitted)


if __name__ == "__main__":
    unittest.main(verbosity=2)
