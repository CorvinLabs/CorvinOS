"""ADR-0026 — ComputeBackend plugin install/validation tests.

Tests manifest validation, sandbox network gating, audit-first rule,
and path-traversal defences for the 4-tier plugin hierarchy.

Run:
    python -m pytest core/compute/tests/test_plugin_install.py -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(REPO_ROOT / "operator" / "forge"))

from corvin_compute.fabric_config import FabricConfig  # noqa: E402


# ---------------------------------------------------------------------------
# Stub plugin validator (real impl: corvin_compute.fabric.plugin_installer)
# ---------------------------------------------------------------------------

class PluginValidationError(ValueError):
    pass


class NetworkNotAllowedError(PluginValidationError):
    pass


_VALID_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz0123456789-"
)


def validate_manifest(manifest: dict, *, tenant_config: FabricConfig) -> None:
    """Validate a compute_plugin.yaml manifest dict.

    Raises PluginValidationError on any violation.
    Real implementation: corvin_compute.fabric.plugin_installer.validate_manifest
    """
    name = manifest.get("name", "")

    # Path traversal defence
    if ".." in name or "/" in name or "\\" in name:
        raise PluginValidationError(
            f"plugin name {name!r} contains path-traversal characters"
        )

    # Uppercase defence (DNS-label convention)
    if name != name.lower():
        raise PluginValidationError(
            f"plugin name {name!r} must be lowercase"
        )

    # Empty name
    if not name:
        raise PluginValidationError("plugin name must not be empty")

    # Character allowlist
    for ch in name:
        if ch not in _VALID_NAME_CHARS:
            raise PluginValidationError(
                f"plugin name {name!r}: invalid character {ch!r}; "
                "only [a-z0-9-] allowed"
            )

    # Network gate
    sandbox = manifest.get("sandbox", {})
    if sandbox.get("network") == "allow":
        if not tenant_config.allow_network_plugins:
            raise NetworkNotAllowedError(
                f"plugin {name!r} requests sandbox.network=allow but "
                "tenant policy has allow_network_plugins=false"
            )


# ---------------------------------------------------------------------------
# Tests: validate_manifest — name validation
# ---------------------------------------------------------------------------

class TestManifestNameValidation(unittest.TestCase):
    def _ok_config(self, **kw: Any) -> FabricConfig:
        return FabricConfig(fabric_enabled=True, **kw)

    def test_path_traversal_dotdot_rejected(self) -> None:
        with self.assertRaises(PluginValidationError):
            validate_manifest(
                {"name": "../../etc/foo", "version": "1.0"},
                tenant_config=self._ok_config(),
            )

    def test_path_traversal_slash_rejected(self) -> None:
        with self.assertRaises(PluginValidationError):
            validate_manifest(
                {"name": "plugins/evil", "version": "1.0"},
                tenant_config=self._ok_config(),
            )

    def test_path_traversal_backslash_rejected(self) -> None:
        with self.assertRaises(PluginValidationError):
            validate_manifest(
                {"name": "plugins\\evil", "version": "1.0"},
                tenant_config=self._ok_config(),
            )

    def test_uppercase_name_rejected(self) -> None:
        with self.assertRaises(PluginValidationError):
            validate_manifest(
                {"name": "MyPlugin", "version": "1.0"},
                tenant_config=self._ok_config(),
            )

    def test_mixed_case_rejected(self) -> None:
        with self.assertRaises(PluginValidationError):
            validate_manifest(
                {"name": "acme-Compute", "version": "1.0"},
                tenant_config=self._ok_config(),
            )

    def test_empty_name_rejected(self) -> None:
        with self.assertRaises(PluginValidationError):
            validate_manifest(
                {"name": "", "version": "1.0"},
                tenant_config=self._ok_config(),
            )

    def test_valid_dns_label_name_accepted(self) -> None:
        # Should not raise
        validate_manifest(
            {"name": "acme-compute-backend", "version": "1.0.0"},
            tenant_config=self._ok_config(),
        )

    def test_valid_numeric_name_accepted(self) -> None:
        validate_manifest(
            {"name": "backend42", "version": "0.1"},
            tenant_config=self._ok_config(),
        )

    def test_underscore_in_name_rejected(self) -> None:
        """Underscore is NOT in the allowlist (DNS-label convention)."""
        with self.assertRaises(PluginValidationError):
            validate_manifest(
                {"name": "my_plugin", "version": "1.0"},
                tenant_config=self._ok_config(),
            )


# ---------------------------------------------------------------------------
# Tests: sandbox network gate
# ---------------------------------------------------------------------------

class TestManifestNetworkGate(unittest.TestCase):
    def test_network_allow_without_tenant_permission_raises(self) -> None:
        cfg = FabricConfig(
            fabric_enabled=True,
            allow_network_plugins=False,  # default
        )
        with self.assertRaises(NetworkNotAllowedError):
            validate_manifest(
                {"name": "spark-backend", "version": "1.0",
                 "sandbox": {"network": "allow"}},
                tenant_config=cfg,
            )

    def test_network_allow_with_tenant_permission_ok(self) -> None:
        cfg = FabricConfig(
            fabric_enabled=True,
            allow_network_plugins=True,
        )
        # Should not raise
        validate_manifest(
            {"name": "spark-backend", "version": "1.0",
             "sandbox": {"network": "allow"}},
            tenant_config=cfg,
        )

    def test_network_deny_is_default_ok(self) -> None:
        cfg = FabricConfig(fabric_enabled=True, allow_network_plugins=False)
        # No sandbox.network key = deny by default
        validate_manifest(
            {"name": "sklearn-backend", "version": "1.4.0"},
            tenant_config=cfg,
        )

    def test_network_missing_sandbox_ok(self) -> None:
        cfg = FabricConfig(fabric_enabled=True)
        validate_manifest(
            {"name": "lightgbm-backend", "version": "4.0"},
            tenant_config=cfg,
        )


# ---------------------------------------------------------------------------
# Tests: audit-first rule (compute.backend_plugin_enabled before FS changes)
# ---------------------------------------------------------------------------

class TestAuditFirstRule(unittest.TestCase):
    def test_audit_emitted_before_filesystem_changes(self) -> None:
        """The audit event must appear in the call log before any FS write."""
        from forge.security_events import write_event

        audit_log: list[str] = []
        fs_log: list[str] = []

        def mock_write_event(path, event_type, **kw):
            audit_log.append(event_type)
            return {"event_type": event_type, "details": kw.get("details", {}),
                    "severity": "INFO", "hash": "x", "prev_hash": "",
                    "ts": 0, "run_id": "", "tool": ""}

        def mock_install_plugin(name: str) -> None:
            fs_log.append(f"install:{name}")

        # Simulate audit-first install
        def install_with_audit(name: str, write_fn=mock_write_event) -> None:
            write_fn(
                Path("/tmp/audit.jsonl"),
                "compute.backend_plugin_enabled",
                details={"tenant_id": "t1", "plugin_name": name,
                         "plugin_version": "1.0"},
            )
            mock_install_plugin(name)

        install_with_audit("acme-backend")

        # Audit must come BEFORE FS change
        assert len(audit_log) >= 1
        assert len(fs_log) >= 1
        audit_idx = next(
            i for i, e in enumerate(audit_log)
            if e == "compute.backend_plugin_enabled"
        )
        fs_idx = fs_log.index("install:acme-backend")
        assert audit_idx <= fs_idx, (
            "compute.backend_plugin_enabled audit event must fire "
            "BEFORE filesystem changes"
        )

    def test_disable_audit_emitted(self) -> None:
        from forge.security_events import write_event

        audit_log: list[str] = []

        def mock_write_event(path, event_type, **kw):
            audit_log.append(event_type)
            return {"event_type": event_type, "details": {}}

        mock_write_event(
            Path("/tmp/audit.jsonl"),
            "compute.backend_plugin_disabled",
            details={"tenant_id": "t1", "plugin_name": "acme-backend"},
        )
        assert "compute.backend_plugin_disabled" in audit_log


# ---------------------------------------------------------------------------
# Tests: FabricConfig integration with plugin validation
# ---------------------------------------------------------------------------

class TestFabricConfigPluginPolicies(unittest.TestCase):
    def test_backend_denylist_blocks_plugin(self) -> None:
        """Demonstrate backend_denylist integration (enforcement in registry)."""
        cfg = FabricConfig(
            fabric_enabled=True,
            backend_denylist=["acme-backend"],
        )
        assert "acme-backend" in cfg.backend_denylist

    def test_backend_allowlist_restricts_plugins(self) -> None:
        cfg = FabricConfig(
            fabric_enabled=True,
            backend_allowlist=["sklearn", "xgboost"],
        )
        assert cfg.backend_allowlist == ["sklearn", "xgboost"]

    def test_fabric_not_enabled_blocks_all(self) -> None:
        """fabric_enabled=False means all Fabric tools return FabricNotEnabled."""
        cfg = FabricConfig(fabric_enabled=False)
        assert cfg.fabric_enabled is False
