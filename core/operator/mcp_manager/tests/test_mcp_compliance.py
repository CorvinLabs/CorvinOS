"""Tests for MCP Plugin Manager M2 — compliance, SHA256, vault, GitHub/pip install."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))

import mcp_manager.catalog as catalog
import mcp_manager.activate as activate
import mcp_manager.installer as installer
import mcp_manager.compliance as compliance

TID = "_default"


@pytest.fixture()
def tmp_corvin(monkeypatch, tmp_path):
    corvin = tmp_path / ".corvin"
    corvin.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(corvin))
    activate._active_cache.clear()
    return corvin


# ADR-0173 made data-residency opt-in: the DEFAULT matrix is permissive
# (us_cloud allowed for PUBLIC/INTERNAL/CONFIDENTIAL). Blocking behavior now
# requires an explicit strict tenant matrix, which this fixture opts into.
STRICT_RESIDENCY_YAML = """\
spec:
  data_classification:
    matrix:
      PUBLIC: [local, eu_cloud, us_cloud]
      INTERNAL: [local, eu_cloud]
      CONFIDENTIAL: [local]
      SECRET: [local]
"""


@pytest.fixture()
def strict_residency(tmp_corvin):
    """Opt-in strict data-residency tenant (ADR-0173): us_cloud blocked for INTERNAL."""
    g = tmp_corvin / "tenants" / TID / "global"
    g.mkdir(parents=True)
    (g / "tenant.corvin.yaml").write_text(STRICT_RESIDENCY_YAML, encoding="utf-8")
    return tmp_corvin


_STRICT_MATRIX = {
    "local": frozenset({"PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET"}),
    "eu_cloud": frozenset({"PUBLIC", "INTERNAL"}),
    "us_cloud": frozenset({"PUBLIC"}),
    "unknown": frozenset({"PUBLIC"}),
}


def _add_tool(tid, tool_id, **overrides):
    entry = {
        "id": tool_id,
        "source": f"npm:{tool_id}@1.0.0",
        "installed_at": "2026-06-07T00:00:00+00:00",
        "runtime": {"command": "npx", "args": ["-y", f"{tool_id}@1.0.0"]},
        "secrets": [],
        "compliance": {"locality": "local", "network_egress": "none"},
        **overrides,
    }
    catalog.add_tool(tid, entry)
    return entry


# ── SHA256 verification ───────────────────────────────────────────────────────


class TestSha256:
    def _make_tarball(self, tmp_corvin, tool_id, content=b"hello") -> str:
        installs = catalog.catalog_dir(TID) / "installs"
        installs.mkdir(parents=True, exist_ok=True)
        tarball = installs / f"{tool_id}.tar.gz"
        with tarfile.open(tarball, "w:gz") as tf:
            import io
            info = tarfile.TarInfo(name=f"{tool_id}/index.js")
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
        sha = hashlib.sha256(content).hexdigest()
        # SHA256 of the tarball file itself (not the content)
        sha256 = hashlib.sha256()
        with open(tarball, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def test_verify_sha256_match(self, tmp_corvin):
        tool_id = "sha-tool"
        expected = self._make_tarball(tmp_corvin, tool_id)
        ok, reason = compliance.verify_sha256(tool_id, expected, TID)
        assert ok is True
        assert reason == ""

    def test_verify_sha256_mismatch(self, tmp_corvin):
        tool_id = "sha-tool"
        self._make_tarball(tmp_corvin, tool_id)
        ok, reason = compliance.verify_sha256(tool_id, "a" * 64, TID)
        assert ok is False
        assert "sha256 mismatch" in reason

    def test_verify_sha256_missing_tarball(self, tmp_corvin):
        ok, reason = compliance.verify_sha256("ghost-tool", "a" * 64, TID)
        assert ok is False
        assert "not found" in reason

    def test_spawn_blocked_on_sha_mismatch(self, tmp_corvin):
        tool_id = "bad-sha-tool"
        _add_tool(TID, tool_id, sha256="wronghash" * 8)
        activate.activate(TID, tool_id)
        # No tarball → SHA verification fails → tool excluded from servers
        servers = activate.get_active_mcp_servers(TID)
        assert tool_id not in servers


# ── Secret vault ──────────────────────────────────────────────────────────────


class TestSecretVault:
    def _vault_path(self, tmp_path) -> Path:
        v = tmp_path / "secrets.json"
        return v

    def test_check_secrets_no_secrets(self):
        entry = {"id": "t", "secrets": []}
        ok, _ = compliance.check_secrets(entry)
        assert ok is True

    def test_check_secrets_present(self, monkeypatch, tmp_path):
        vault = tmp_path / "secrets.json"
        vault.write_text(json.dumps({"BRAVE_API_KEY": "secret-val"}))
        monkeypatch.setenv("CORVIN_SECRET_VAULT", str(vault))
        entry = {"id": "t", "secrets": [{"name": "BRAVE_API_KEY", "required": True}]}
        ok, _ = compliance.check_secrets(entry)
        assert ok is True

    def test_check_secrets_missing_required(self, monkeypatch, tmp_path):
        vault = tmp_path / "secrets.json"
        vault.write_text(json.dumps({}))
        monkeypatch.setenv("CORVIN_SECRET_VAULT", str(vault))
        entry = {"id": "t", "secrets": [{"name": "BRAVE_API_KEY", "required": True}]}
        ok, reason = compliance.check_secrets(entry)
        assert ok is False
        assert "missing_secret" in reason
        assert "BRAVE_API_KEY" in reason

    def test_optional_secret_not_blocking(self, monkeypatch, tmp_path):
        vault = tmp_path / "secrets.json"
        vault.write_text(json.dumps({}))
        monkeypatch.setenv("CORVIN_SECRET_VAULT", str(vault))
        entry = {"id": "t", "secrets": [{"name": "OPT_KEY", "required": False}]}
        ok, _ = compliance.check_secrets(entry)
        assert ok is True

    def test_spawn_blocked_on_missing_secret(self, tmp_corvin, monkeypatch, tmp_path):
        vault = tmp_path / "secrets.json"
        vault.write_text(json.dumps({}))
        monkeypatch.setenv("CORVIN_SECRET_VAULT", str(vault))
        tool_id = "secret-tool"
        _add_tool(TID, tool_id,
                  secrets=[{"name": "REQUIRED_KEY", "required": True}])
        activate.activate(TID, tool_id)
        servers = activate.get_active_mcp_servers(TID)
        assert tool_id not in servers

    def test_spawn_succeeds_with_secret_in_vault(self, tmp_corvin, monkeypatch, tmp_path):
        vault = tmp_path / "secrets.json"
        vault.write_text(json.dumps({"REQUIRED_KEY": "val"}))
        monkeypatch.setenv("CORVIN_SECRET_VAULT", str(vault))
        tool_id = "secret-tool-ok"
        _add_tool(TID, tool_id,
                  secrets=[{"name": "REQUIRED_KEY", "required": True}])
        activate.activate(TID, tool_id)
        servers = activate.get_active_mcp_servers(TID)
        assert tool_id in servers
        assert servers[tool_id]["env"]["REQUIRED_KEY"] == "${REQUIRED_KEY}"


# ── L34 locality compliance ───────────────────────────────────────────────────


class TestL34Locality:
    def test_local_tool_allowed(self, tmp_corvin):
        entry = {"id": "t", "compliance": {"locality": "local"}}
        ok, _ = compliance.check_locality(entry, TID)
        assert ok is True

    def test_eu_cloud_allowed_by_default(self, tmp_corvin):
        # Default matrix allows eu_cloud for INTERNAL
        entry = {"id": "t", "compliance": {"locality": "eu_cloud"}}
        ok, _ = compliance.check_locality(entry, TID)
        assert ok is True

    def test_us_cloud_allowed_for_internal_by_default(self, tmp_corvin):
        # ADR-0173: default matrix is permissive — us_cloud allowed for INTERNAL.
        entry = {"id": "t", "compliance": {"locality": "us_cloud"}}
        ok, _ = compliance.check_locality(entry, TID)
        assert ok is True

    def test_us_cloud_blocked_with_strict_residency_opt_in(self, strict_residency):
        # Opt-in strict matrix (ADR-0173): us_cloud has PUBLIC only → INTERNAL blocked.
        entry = {"id": "t", "compliance": {"locality": "us_cloud"}}
        ok, _ = compliance.check_locality(entry, TID)
        assert ok is False

    def test_unknown_locality_blocked(self, tmp_corvin):
        entry = {"id": "t", "compliance": {"locality": "unknown"}}
        ok, _ = compliance.check_locality(entry, TID)
        # unknown has PUBLIC only → INTERNAL not in set → blocked
        assert ok is False

    def test_missing_compliance_field(self, tmp_corvin):
        entry = {"id": "t"}
        ok, _ = compliance.check_locality(entry, TID)
        # No compliance → locality defaults to "unknown" → blocked
        assert ok is False

    def test_activation_blocked_on_l34_violation(self, strict_residency):
        _add_tool(TID, "us-cloud-tool",
                  compliance={"locality": "us_cloud", "network_egress": "required"})
        from mcp_manager.compliance import ComplianceError
        with pytest.raises(ComplianceError, match="L34"):
            activate.activate(TID, "us-cloud-tool")

    def test_spawn_blocked_on_l34_after_bypass(self, strict_residency, monkeypatch):
        _add_tool(TID, "us-cloud-tool2",
                  compliance={"locality": "us_cloud"})
        # Bypass activation check to simulate a tool activated before policy tightened
        with patch.object(compliance, "check_activation_compliance"):
            activate.activate(TID, "us-cloud-tool2")
        servers = activate.get_active_mcp_servers(TID)
        assert "us-cloud-tool2" not in servers


# ── L35 egress compliance ─────────────────────────────────────────────────────


class TestL35Egress:
    def test_no_hosts_no_check(self):
        entry = {"id": "t", "compliance": {"locality": "local", "hosts": []}}
        ok, _ = compliance.check_egress(entry, TID)
        assert ok is True

    def test_no_hosts_field_no_check(self):
        entry = {"id": "t", "compliance": {"locality": "local"}}
        ok, _ = compliance.check_egress(entry, TID)
        assert ok is True

    def test_egress_allowed_when_gate_not_configured(self, tmp_corvin):
        # No tenant config → EgressGate returns None → no check → allowed
        entry = {"id": "t", "compliance": {"hosts": ["api.brave.com"]}}
        ok, _ = compliance.check_egress(entry, TID)
        assert ok is True

    def test_egress_check_with_mock_gate(self, monkeypatch):
        mock_gate = MagicMock()
        # Simulate gate denying the host
        denied = MagicMock()
        denied.allowed = False
        denied.reason = "default_deny"
        mock_gate.validate.return_value = denied
        monkeypatch.setattr(compliance, "_load_egress_gate", lambda tid: mock_gate)

        entry = {"id": "t", "compliance": {"hosts": ["api.evil.com"]}}
        ok, reason = compliance.check_egress(entry, TID)
        assert ok is False
        assert "l35_egress" in reason
        assert "api.evil.com" in reason

    def test_egress_allowed_with_mock_gate(self, monkeypatch):
        mock_gate = MagicMock()
        allowed = MagicMock()
        allowed.allowed = True
        mock_gate.validate.return_value = allowed
        monkeypatch.setattr(compliance, "_load_egress_gate", lambda tid: mock_gate)

        entry = {"id": "t", "compliance": {"hosts": ["api.brave.com"]}}
        ok, _ = compliance.check_egress(entry, TID)
        assert ok is True


# ── pip installer (M2) ────────────────────────────────────────────────────────


class TestPipInstaller:
    def test_install_pip_basic(self, tmp_corvin):
        entry = installer.install("pip:mcp-server-sqlite@1.0.0", TID)
        assert entry["id"] == "mcp-server-sqlite"
        assert entry["source"] == "pip:mcp-server-sqlite==1.0.0"
        assert entry["runtime"]["command"] in ("uvx", "python3")

    def test_install_pip_no_version(self, tmp_corvin):
        entry = installer.install("pip:mcp-server-sqlite", TID)
        assert entry["id"] == "mcp-server-sqlite"

    def test_install_pip_persists(self, tmp_corvin):
        installer.install("pip:mcp-server-sqlite@1.0.0", TID)
        assert catalog.get_tool(TID, "mcp-server-sqlite") is not None

    def test_install_pip_locality_local(self, tmp_corvin):
        entry = installer.install("pip:some-local-tool@0.1.0", TID)
        assert entry["compliance"]["locality"] == "local"


# ── GitHub installer (M2) ─────────────────────────────────────────────────────


class TestGithubInstaller:
    def test_missing_ref_rejected(self, tmp_corvin):
        with pytest.raises(ValueError, match="requires a tag"):
            installer.install("github:owner/repo", TID)

    def test_branch_without_allow_unpin_rejected(self, tmp_corvin):
        with pytest.raises(ValueError, match="allow-unpin"):
            installer.install("github:owner/repo@main", TID)

    def test_invalid_spec(self, tmp_corvin):
        with pytest.raises(ValueError, match="Invalid github spec"):
            installer.install("github:notavalidspec", TID)

    def test_version_tag_accepted(self, tmp_corvin):
        # Mock the download + extract to avoid real network calls
        fake_sha = "a" * 64
        fake_entry = {
            "id": "owner-repo",
            "source": "github:owner/repo@v1.2.3",
            "installed_at": "now",
            "sha256": fake_sha,
            "runtime": {"command": "node", "args": ["/tmp/index.js"]},
            "secrets": [],
            "compliance": {"locality": "local", "network_egress": "none"},
        }
        with patch.object(installer, "_download_and_verify", return_value=fake_sha), \
             patch.object(installer, "_extract_tarball", return_value="owner-repo-abc1234"), \
             patch.object(installer, "_detect_runtime",
                          return_value={"command": "node", "args": []}):
            # Create the extract dir so the code doesn't fail
            installs = catalog.catalog_dir(TID) / "installs" / "owner-repo"
            installs.mkdir(parents=True, exist_ok=True)
            entry = installer.install("github:owner/repo@v1.2.3", TID)
        assert entry["sha256"] == fake_sha
        assert catalog.get_tool(TID, "owner-repo") is not None

    def test_sha_mismatch_blocks_spawn(self, tmp_corvin):
        _add_tool(TID, "github-tool",
                  sha256="expectedhash" * 5,
                  compliance={"locality": "local", "network_egress": "none"})
        # No tarball on disk → sha verification fails → spawn blocked
        with patch.object(compliance, "check_activation_compliance"):
            activate.activate(TID, "github-tool")
        servers = activate.get_active_mcp_servers(TID)
        assert "github-tool" not in servers

    def test_commit_sha_accepted(self, tmp_corvin):
        assert installer._LOOKS_LIKE_VERSION.match("a3f8b12c")
        assert installer._LOOKS_LIKE_VERSION.match("v1.2.3")

    def test_branch_name_rejected(self, tmp_corvin):
        assert not installer._LOOKS_LIKE_VERSION.match("main")
        assert not installer._LOOKS_LIKE_VERSION.match("feature-branch")

    def test_tarball_path_traversal_rejected(self, tmp_corvin, tmp_path):
        import io
        tarball = tmp_path / "evil.tar.gz"
        with tarfile.open(tarball, "w:gz") as tf:
            info = tarfile.TarInfo(name="../../../etc/passwd")
            info.size = 5
            tf.addfile(info, io.BytesIO(b"evil!"))
        with pytest.raises(ValueError, match="Unsafe path"):
            installer._extract_tarball(tarball, tmp_path / "dest")

    def test_uninstall_cleans_artifacts(self, tmp_corvin, tmp_path):
        _add_tool(TID, "clean-github",
                  compliance={"locality": "local", "network_egress": "none"})
        installs = catalog.catalog_dir(TID) / "installs"
        installs.mkdir(parents=True, exist_ok=True)
        tarball = installs / "clean-github.tar.gz"
        tarball.write_bytes(b"fake")
        extract_dir = installs / "clean-github"
        extract_dir.mkdir()

        assert installer.uninstall("clean-github", TID) is True
        assert not tarball.exists()
        assert not extract_dir.exists()


# ── filter_compliant_servers ──────────────────────────────────────────────────


class TestFilterCompliantServers:
    def test_local_tool_passes(self, tmp_corvin):
        servers = {"local-tool": {"command": "npx", "args": []}}
        entries = {"local-tool": {"compliance": {"locality": "local"},
                                  "secrets": []}}
        result = compliance.filter_compliant_servers(servers, entries, TID)
        assert "local-tool" in result

    def test_eu_cloud_tool_passes(self, tmp_corvin):
        servers = {"eu-tool": {"command": "npx", "args": []}}
        entries = {"eu-tool": {"compliance": {"locality": "eu_cloud"},
                               "secrets": []}}
        result = compliance.filter_compliant_servers(servers, entries, TID)
        assert "eu-tool" in result

    def test_us_cloud_tool_blocked(self, monkeypatch, tmp_corvin):
        # Blocking requires the strict opt-in matrix (ADR-0173).
        monkeypatch.setattr(compliance, "_load_tenant_matrix", lambda tid: _STRICT_MATRIX)
        servers = {"us-tool": {"command": "npx", "args": []}}
        entries = {"us-tool": {"compliance": {"locality": "us_cloud"},
                               "secrets": []}}
        result = compliance.filter_compliant_servers(servers, entries, TID)
        assert "us-tool" not in result

    def test_tool_without_catalog_entry_passes(self, tmp_corvin):
        # Unknown tool (no entry) passes through — defensive
        servers = {"unknown-tool": {"command": "npx", "args": []}}
        result = compliance.filter_compliant_servers(servers, {}, TID)
        assert "unknown-tool" in result

    def test_multiple_tools_mixed_compliance(self, monkeypatch, tmp_path, tmp_corvin):
        monkeypatch.setattr(compliance, "_load_tenant_matrix", lambda tid: _STRICT_MATRIX)
        vault = tmp_path / "secrets.json"
        vault.write_text(json.dumps({}))
        monkeypatch.setenv("CORVIN_SECRET_VAULT", str(vault))
        servers = {
            "good": {"command": "npx", "args": []},
            "bad": {"command": "npx", "args": []},
        }
        entries = {
            "good": {"compliance": {"locality": "local"}, "secrets": []},
            "bad": {"compliance": {"locality": "us_cloud"}, "secrets": []},
        }
        result = compliance.filter_compliant_servers(servers, entries, TID)
        assert "good" in result
        assert "bad" not in result
