"""Tests for BackendRegistry — 30 test cases (ADR-0026 §A)."""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Adjust import path
sys.path.insert(0, str(Path(__file__).parent.parent))

from corvin_compute.fabric.backends.manifest import (
    ManifestValidationError,
    PluginManifest,
    validate_manifest,
)
from corvin_compute.fabric.backends.registry import (
    BackendRegistry,
    NetworkNotApproved,
)


# ---------------------------------------------------------------------------
# validate_manifest tests
# ---------------------------------------------------------------------------

class TestValidateManifest:
    def _base_manifest(self) -> dict:
        return {
            "name": "test-backend",
            "version": "1.0.0",
            "author": "Test Author",
            "backend_class": "test_module.TestBackend",
        }

    def test_valid_minimal_manifest(self):
        raw = self._base_manifest()
        m = validate_manifest(raw)
        assert m.name == "test-backend"
        assert m.version == "1.0.0"
        assert m.author == "Test Author"
        assert m.auth_method == "none"

    def test_valid_full_manifest(self):
        raw = self._base_manifest()
        raw.update({
            "capabilities": ["distributed"],
            "sandbox": {"network": "allow"},
            "auth": {"method": "vault", "secret_keys": ["MY_KEY"]},
            "steering_map": {"lr": "learning_rate"},
            "audit_events": ["mybackend.started"],
        })
        m = validate_manifest(raw)
        assert "distributed" in m.capabilities
        assert m.sandbox.network == "allow"
        assert m.auth_method == "vault"
        assert m.auth_secret_keys == ["MY_KEY"]
        assert m.steering_map == {"lr": "learning_rate"}

    def test_missing_name_raises(self):
        raw = self._base_manifest()
        del raw["name"]
        with pytest.raises(ManifestValidationError, match="name"):
            validate_manifest(raw)

    def test_missing_version_raises(self):
        raw = self._base_manifest()
        del raw["version"]
        with pytest.raises(ManifestValidationError, match="version"):
            validate_manifest(raw)

    def test_missing_author_raises(self):
        raw = self._base_manifest()
        del raw["author"]
        with pytest.raises(ManifestValidationError, match="author"):
            validate_manifest(raw)

    def test_missing_backend_class_raises(self):
        raw = self._base_manifest()
        del raw["backend_class"]
        with pytest.raises(ManifestValidationError, match="backend_class"):
            validate_manifest(raw)

    # --- Path-traversal name tests ---
    def test_name_dotdot_slash_raises(self):
        raw = self._base_manifest()
        raw["name"] = "../../etc/passwd"
        with pytest.raises(ManifestValidationError, match="path-traversal"):
            validate_manifest(raw)

    def test_name_with_forward_slash_raises(self):
        raw = self._base_manifest()
        raw["name"] = "some/path"
        with pytest.raises(ManifestValidationError, match="path-traversal"):
            validate_manifest(raw)

    def test_name_with_backslash_raises(self):
        raw = self._base_manifest()
        raw["name"] = "some\\path"
        with pytest.raises(ManifestValidationError, match="path-traversal"):
            validate_manifest(raw)

    def test_name_dotdot_only_raises(self):
        raw = self._base_manifest()
        raw["name"] = ".."
        with pytest.raises(ManifestValidationError):
            validate_manifest(raw)

    def test_name_with_special_chars_raises(self):
        raw = self._base_manifest()
        raw["name"] = "backend!evil"
        with pytest.raises(ManifestValidationError, match="disallowed"):
            validate_manifest(raw)

    def test_name_with_space_raises(self):
        raw = self._base_manifest()
        raw["name"] = "my backend"
        with pytest.raises(ManifestValidationError, match="disallowed"):
            validate_manifest(raw)

    def test_valid_name_with_dots_and_hyphens(self):
        raw = self._base_manifest()
        raw["name"] = "my-backend.v2"
        m = validate_manifest(raw)
        assert m.name == "my-backend.v2"

    # --- Auth method tests ---
    def test_auth_vault_allowed(self):
        raw = self._base_manifest()
        raw["auth"] = {"method": "vault", "secret_keys": ["KEY"]}
        m = validate_manifest(raw)
        assert m.auth_method == "vault"

    def test_auth_none_allowed(self):
        raw = self._base_manifest()
        raw["auth"] = {"method": "none"}
        m = validate_manifest(raw)
        assert m.auth_method == "none"

    def test_auth_aws_keys_rejected(self):
        raw = self._base_manifest()
        raw["auth"] = {"method": "aws_keys"}
        with pytest.raises(ManifestValidationError, match="auth.method"):
            validate_manifest(raw)

    def test_auth_plaintext_rejected(self):
        raw = self._base_manifest()
        raw["auth"] = {"method": "plaintext"}
        with pytest.raises(ManifestValidationError, match="auth.method"):
            validate_manifest(raw)

    def test_auth_env_var_rejected(self):
        raw = self._base_manifest()
        raw["auth"] = {"method": "env_var"}
        with pytest.raises(ManifestValidationError, match="auth.method"):
            validate_manifest(raw)

    def test_backend_class_traversal_rejected(self):
        raw = self._base_manifest()
        raw["backend_class"] = "../evil/module.EvilClass"
        with pytest.raises(ManifestValidationError, match="path-traversal"):
            validate_manifest(raw)

    def test_parallel_defaults(self):
        raw = self._base_manifest()
        m = validate_manifest(raw)
        assert m.parallel.inter_job.compatible is True
        assert m.parallel.intra_backend.model == "thread"

    def test_parallel_incompatible_backend(self):
        raw = self._base_manifest()
        raw["parallel"] = {"inter_job": {"compatible": False, "max_concurrent_instances": 1}}
        m = validate_manifest(raw)
        assert m.parallel.inter_job.compatible is False

    def test_sandbox_network_deny_default(self):
        raw = self._base_manifest()
        m = validate_manifest(raw)
        assert m.sandbox.network == "deny"

    def test_audit_events_list(self):
        raw = self._base_manifest()
        raw["audit_events"] = ["mybackend.started", "mybackend.done"]
        m = validate_manifest(raw)
        assert "mybackend.started" in m.audit_events


# ---------------------------------------------------------------------------
# BackendRegistry tests
# ---------------------------------------------------------------------------

class TestBackendRegistry:
    def _make_registry(self, **kwargs) -> BackendRegistry:
        return BackendRegistry(tenant_id="test", **kwargs)

    def test_discover_finds_builtin_backends(self):
        reg = self._make_registry()
        reg.discover()
        # Should at minimum find sklearn and the other builtins (if importable)
        names = reg.list_backends()
        assert len(names) >= 1  # at least one bundle backend

    def test_list_backends_sorted(self):
        reg = self._make_registry()
        reg.discover()
        names = reg.list_backends()
        assert names == sorted(names)

    def test_get_unknown_backend_returns_none(self):
        reg = self._make_registry()
        reg.discover()
        assert reg.get("nonexistent_backend_xyz") is None

    def test_get_manifest_returns_manifest(self):
        reg = self._make_registry()
        reg.discover()
        names = reg.list_backends()
        if names:
            m = reg.get_manifest(names[0])
            assert m is not None
            assert isinstance(m, PluginManifest)

    def test_network_plugin_denied_without_flag(self):
        reg = self._make_registry(allow_network_plugins=False)
        manifest = PluginManifest(
            name="net-backend",
            version="1.0.0",
            author="Test",
            backend_class="mod.NetBackend",
        )
        manifest.sandbox.network = "allow"
        backend = MagicMock()
        backend.name = "net-backend"
        with pytest.raises(NetworkNotApproved):
            reg.register(manifest, backend, check_network=True)

    def test_network_plugin_allowed_with_flag(self):
        reg = self._make_registry(allow_network_plugins=True)
        manifest = PluginManifest(
            name="net-backend",
            version="1.0.0",
            author="Test",
            backend_class="mod.NetBackend",
        )
        manifest.sandbox.network = "allow"
        backend = MagicMock()
        backend.name = "net-backend"
        reg.register(manifest, backend, check_network=True)
        assert "net-backend" in reg.list_backends()

    def test_tenant_level_overrides_bundle(self):
        """A tenant-level backend with the same name should replace the bundle."""
        reg = self._make_registry()
        # Manually register a bundle backend first
        bundle_manifest = PluginManifest(
            name="sklearn",
            version="0.9.0",
            author="Corvin",
            backend_class="corvin_compute.fabric.backends.builtin.sklearn_backend.SklearnBackend",
        )
        bundle_backend = MagicMock()
        bundle_backend.name = "sklearn"
        reg.register(bundle_manifest, bundle_backend, check_network=False)

        # Now register a "tenant" override
        tenant_manifest = PluginManifest(
            name="sklearn",
            version="2.0.0",
            author="ACME Corp",
            backend_class="acme.HardenedSklearn",
        )
        tenant_backend = MagicMock()
        tenant_backend.name = "sklearn"
        reg.register(tenant_manifest, tenant_backend, check_network=False)

        # The tenant version should win
        m = reg.get_manifest("sklearn")
        assert m is not None
        assert m.version == "2.0.0"
        assert m.author == "ACME Corp"

    def test_register_without_network_check(self):
        reg = self._make_registry(allow_network_plugins=False)
        manifest = PluginManifest(
            name="safe-backend",
            version="1.0.0",
            author="Test",
            backend_class="mod.SafeBackend",
        )
        manifest.sandbox.network = "deny"
        backend = MagicMock()
        backend.name = "safe-backend"
        reg.register(manifest, backend, check_network=False)
        assert reg.get("safe-backend") is backend


# ---------------------------------------------------------------------------
# AST lint test
# ---------------------------------------------------------------------------

class TestAstLintBackends:
    """No anthropic import in any backend file."""

    def _collect_files(self) -> list[Path]:
        fabric_root = Path(__file__).parent.parent / "corvin_compute" / "fabric"
        return list(fabric_root.rglob("*.py"))

    def test_no_anthropic_import_in_backends(self):
        files = self._collect_files()
        assert len(files) > 0, "No files found"
        violations = []
        for path in files:
            source = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "anthropic" or alias.name.startswith("anthropic."):
                            violations.append(f"{path}:{node.lineno}")
                elif isinstance(node, ast.ImportFrom):
                    if (node.module or "").startswith("anthropic"):
                        violations.append(f"{path}:{node.lineno}")
        assert not violations, f"Forbidden anthropic imports: {violations}"
