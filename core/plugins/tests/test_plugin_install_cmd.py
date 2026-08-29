"""Tests for `corvin plugin install` runtime command (ADR-0248/0249).

Tests the trust and origin handling in the install flow:
- URL rejection (only local paths)
- Community plugin confirmation flow
- Vetted plugin signature verification
- Trust anchor handling (empty set, valid key, invalid key)
- Idempotent behavior

Run:
    python -m pytest core/plugins/tests/test_plugin_install_cmd.py -v
"""
from __future__ import annotations

import base64
import json
import sys
from argparse import Namespace
from pathlib import Path
from unittest import mock

import pytest

# Access the launcher CLI module
_REPO = Path(__file__).resolve().parents[3]
_LAUNCHER = _REPO / "ops" / "launcher"
if str(_LAUNCHER) not in sys.path:
    sys.path.insert(0, str(_LAUNCHER))

from corvin.plugin_runtime_cmd import cmd_install  # noqa: E402


def _keypair():
    """Generate an Ed25519 keypair for testing."""
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
    )

    priv = Ed25519PrivateKey.generate()
    der = priv.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )
    return priv, base64.urlsafe_b64encode(der).decode().rstrip("=")


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


@pytest.fixture
def temp_corvin_home(tmp_path, monkeypatch):
    """Provide a temporary CORVIN_HOME for tests."""
    corvin_home = tmp_path / ".corvin"
    corvin_home.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(corvin_home))

    # Mock the paths module to use our temp home
    with mock.patch(
        "corvinOS.shared.paths.corvin_home",
        return_value=corvin_home,
    ):
        with mock.patch(
            "corvinOS.shared.paths.tenant_home",
            return_value=corvin_home / "tenants" / "_default",
        ):
            with mock.patch(
                "corvinOS.shared.paths._resolve_tenant_id",
                return_value="_default",
            ):
                yield corvin_home


@pytest.fixture
def plugin_dir(tmp_path):
    """Create a minimal plugin directory for testing."""
    plugin_root = tmp_path / "test-plugin"
    plugin_root.mkdir()

    # plugin.yaml
    (plugin_root / "plugin.yaml").write_text(
        """plugin_id: com.example.test-plugin
plugin_type: router_backend
version: 1.0.0
display_name: Test Plugin
boot_layer: installed
origin: community
pii_risk: low
requires_consent: false
""",
        encoding="utf-8",
    )

    # plugin.py (minimal valid plugin)
    (plugin_root / "plugin.py").write_text(
        """
from corvin_plugins.protocol import BasePlugin, HealthStatus

class TestPlugin(BasePlugin):
    plugin_id = "com.example.test-plugin"
    plugin_type = "router_backend"
    version = "1.0.0"

    def on_load(self):
        pass

    def health_check(self):
        return HealthStatus(ok=True, message="ok")

    def route(self, task):
        return None
""",
        encoding="utf-8",
    )

    # pyproject.toml
    (plugin_root / "pyproject.toml").write_text(
        """[project]
name = "test-plugin"
version = "1.0.0"
requires-python = ">=3.11"

[project.entry-points."corvin.plugins"]
test_plugin = "plugin:TestPlugin"
""",
        encoding="utf-8",
    )

    return plugin_root


# ── URL rejection tests ──────────────────────────────────────────────────────

def test_install_rejects_http_url(plugin_dir, capsys):
    """URL arguments must be rejected; only local paths allowed."""
    args = Namespace(path="http://example.com/plugin.zip", tenant=None, yes=False)
    rc = cmd_install(args)

    assert rc == 1, "HTTP URL should be rejected"
    _, err = capsys.readouterr()
    assert "URL arguments are not supported" in err or "http://" in err


def test_install_rejects_https_url(plugin_dir, capsys):
    """HTTPS URLs must be rejected."""
    args = Namespace(path="https://example.com/plugin.zip", tenant=None, yes=False)
    rc = cmd_install(args)

    assert rc == 1
    _, err = capsys.readouterr()
    assert "URL arguments are not supported" in err or "https://" in err


def test_install_rejects_ftp_url(plugin_dir, capsys):
    """FTP URLs must be rejected."""
    args = Namespace(path="ftp://example.com/plugin", tenant=None, yes=False)
    rc = cmd_install(args)

    assert rc == 1
    _, err = capsys.readouterr()
    assert "URL arguments are not supported" in err


def test_install_rejects_file_url(plugin_dir, capsys):
    """file:// URLs must be rejected."""
    args = Namespace(path="file:///home/user/plugin", tenant=None, yes=False)
    rc = cmd_install(args)

    assert rc == 1
    _, err = capsys.readouterr()
    assert "URL arguments are not supported" in err


# ── Path validation tests ────────────────────────────────────────────────────

def test_install_rejects_nonexistent_directory(capsys):
    """Install must reject a non-existent directory."""
    args = Namespace(path="/nonexistent/path/plugin", tenant=None, yes=False)
    rc = cmd_install(args)

    assert rc == 2
    _, err = capsys.readouterr()
    assert "not found" in err


# ── Community plugin confirmation tests ──────────────────────────────────────

def test_install_community_plugin_with_yes_flag(
    plugin_dir, temp_corvin_home, monkeypatch, capsys
):
    """Community plugin with --yes should install without prompting."""
    args = Namespace(path=str(plugin_dir), tenant=None, yes=True)

    rc = cmd_install(args)

    assert rc == 0, "Installation should succeed with --yes"
    out, _ = capsys.readouterr()
    assert "Installed" in out


def test_install_community_plugin_requires_confirmation(
    plugin_dir, temp_corvin_home, monkeypatch, capsys
):
    """Community plugin without --yes should prompt and reject on 'n'."""
    # Simulate user entering 'n'
    monkeypatch.setattr("builtins.input", lambda x: "n")

    # Ensure enforcement is OFF so the prompt is shown (not refused outright)
    with mock.patch("corvin_plugins.trust.enforcement_enabled", return_value=False):
        args = Namespace(path=str(plugin_dir), tenant=None, yes=False)
        rc = cmd_install(args)

    assert rc == 1, "Installation should be cancelled on 'n'"
    out, _ = capsys.readouterr()
    assert "Installation cancelled" in out


def test_install_community_plugin_accepts_confirmation(
    plugin_dir, temp_corvin_home, monkeypatch, capsys
):
    """Community plugin with user 'y' should install."""
    monkeypatch.setattr("builtins.input", lambda x: "y")

    # Ensure enforcement is OFF so the prompt is shown
    with mock.patch("corvin_plugins.trust.enforcement_enabled", return_value=False):
        args = Namespace(path=str(plugin_dir), tenant=None, yes=False)
        rc = cmd_install(args)

    assert rc == 0, "Installation should succeed with 'y'"
    out, _ = capsys.readouterr()
    assert "Installed" in out


def test_install_community_plugin_no_tty_fails(
    plugin_dir, temp_corvin_home, monkeypatch, capsys
):
    """Community plugin without TTY and without --yes should fail."""
    # Simulate no TTY (EOFError on input)
    from unittest.mock import Mock
    mock_input = Mock(side_effect=EOFError)
    monkeypatch.setattr("builtins.input", mock_input)

    # Ensure enforcement is OFF so the prompt is attempted
    with mock.patch("corvin_plugins.trust.enforcement_enabled", return_value=False):
        args = Namespace(path=str(plugin_dir), tenant=None, yes=False)
        rc = cmd_install(args)

    assert rc == 1
    _, err = capsys.readouterr()
    assert "no TTY" in err


# ── Vetted plugin tests ──────────────────────────────────────────────────────

def test_install_vetted_plugin_without_trust_anchor_fails(
    plugin_dir, temp_corvin_home, monkeypatch, capsys
):
    """Vetted plugin with no trust anchor should fail (forged verdict)."""
    # Create a signed manifest
    pytest.importorskip("cryptography")
    import yaml
    priv, pub = _keypair()
    from corvin_plugins.trust import manifest_signing_digest

    manifest_data = {
        "plugin_id": "com.example.test-plugin",
        "plugin_type": "router_backend",
        "version": "1.0.0",
        "display_name": "Test Plugin",
        "boot_layer": "installed",
        "origin": "vetted",
        "pii_risk": "low",
        "requires_consent": False,
    }

    # Sign the manifest
    sig = priv.sign(manifest_signing_digest(manifest_data))
    manifest_data["signature"] = {
        "algorithm": "ed25519",
        "public_key": pub,
        "value": _b64(sig),
    }

    # Write signed manifest as YAML
    (plugin_dir / "plugin.yaml").write_text(
        yaml.dump(manifest_data), encoding="utf-8"
    )

    # No trust anchor configured (empty anchors)
    with mock.patch("corvin_plugins.trust.enforcement_enabled", return_value=False):
        args = Namespace(path=str(plugin_dir), tenant=None, yes=False)
        rc = cmd_install(args)

    # Should fail because key is not pinned (forged verdict)
    assert rc == 1
    _, err = capsys.readouterr()
    assert "forged" in err.lower() or "trust" in err.lower()


def test_install_builtin_plugin_always_allowed(
    plugin_dir, temp_corvin_home, capsys
):
    """Builtin plugins should always be allowed (not even need confirmation)."""
    # Create a builtin manifest
    manifest_text = """plugin_id: com.example.test-plugin
plugin_type: router_backend
version: 1.0.0
display_name: Test Plugin
boot_layer: installed
origin: builtin
pii_risk: low
requires_consent: false
"""
    (plugin_dir / "plugin.yaml").write_text(manifest_text, encoding="utf-8")

    args = Namespace(path=str(plugin_dir), tenant=None, yes=False)
    rc = cmd_install(args)

    # Should succeed without confirmation (no input call needed)
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "Installed" in out


# ── Trust anchor file handling ───────────────────────────────────────────────

def test_install_with_trust_anchor_file(
    plugin_dir, temp_corvin_home, monkeypatch, capsys
):
    """Trust anchors should be read from ~/.corvin/global/plugin_trust_anchors.txt"""
    pytest.importorskip("cryptography")
    import yaml
    priv, pub = _keypair()
    from corvin_plugins.trust import manifest_signing_digest

    # Create and configure trust anchor file
    anchors_file = temp_corvin_home / "global" / "plugin_trust_anchors.txt"
    anchors_file.parent.mkdir(parents=True, exist_ok=True)
    anchors_file.write_text(f"# Maintainer key\n{pub}\n", encoding="utf-8")

    # Create signed manifest
    manifest_data = {
        "plugin_id": "com.example.test-plugin",
        "plugin_type": "router_backend",
        "version": "1.0.0",
        "display_name": "Test Plugin",
        "boot_layer": "installed",
        "origin": "vetted",
        "pii_risk": "low",
        "requires_consent": False,
    }

    sig = priv.sign(manifest_signing_digest(manifest_data))
    manifest_data["signature"] = {
        "algorithm": "ed25519",
        "public_key": pub,
        "value": _b64(sig),
    }

    (plugin_dir / "plugin.yaml").write_text(
        yaml.dump(manifest_data), encoding="utf-8"
    )

    args = Namespace(path=str(plugin_dir), tenant=None, yes=False)
    rc = cmd_install(args)

    # Should succeed because key is pinned
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "Installed" in out or "Trust verified" in out


# ── Integration tests ────────────────────────────────────────────────────────

def test_install_stores_plugin_in_registry(
    plugin_dir, temp_corvin_home, monkeypatch, capsys
):
    """Installed plugin should be recorded in the tenant registry."""
    args = Namespace(path=str(plugin_dir), tenant=None, yes=True)
    rc = cmd_install(args)

    assert rc == 0

    # Verify registry was written
    registry_path = (
        temp_corvin_home / "tenants" / "_default" / "plugins" / "registry.yaml"
    )
    assert registry_path.exists(), "Registry should be created"


def test_install_rejects_duplicate_plugin(
    plugin_dir, temp_corvin_home, monkeypatch, capsys
):
    """Installing the same plugin twice should fail."""
    args = Namespace(path=str(plugin_dir), tenant=None, yes=True)

    # First install
    rc1 = cmd_install(args)
    assert rc1 == 0

    # Second install (same plugin)
    rc2 = cmd_install(args)
    assert rc2 == 1, "Duplicate install should fail"
    _, err = capsys.readouterr()
    assert "already installed" in err


__all__ = [
    "test_install_rejects_http_url",
    "test_install_rejects_https_url",
    "test_install_rejects_ftp_url",
    "test_install_rejects_file_url",
    "test_install_rejects_nonexistent_directory",
    "test_install_community_plugin_with_yes_flag",
    "test_install_community_plugin_requires_confirmation",
    "test_install_community_plugin_accepts_confirmation",
    "test_install_community_plugin_no_tty_fails",
    "test_install_vetted_plugin_without_trust_anchor_fails",
    "test_install_builtin_plugin_always_allowed",
    "test_install_with_trust_anchor_file",
    "test_install_stores_plugin_in_registry",
    "test_install_rejects_duplicate_plugin",
]
