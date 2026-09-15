"""Tests for MCP Plugin Manager M1 (ADR-0096).

Run with:
    cd operator/mcp_manager
    python -m pytest tests/ -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Make sure the package is importable from the tests directory.
_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))

import mcp_manager.catalog as catalog
import mcp_manager.activate as activate
import mcp_manager.installer as installer


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_corvin(monkeypatch, tmp_path):
    """Point CORVIN_HOME at a fresh temp dir for isolation."""
    corvin = tmp_path / ".corvin"
    corvin.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(corvin))
    # Flush the hot-reload cache between tests.
    activate._active_cache.clear()
    return corvin


TID = "_default"


# ── catalog ───────────────────────────────────────────────────────────────────


class TestCatalog:
    def test_empty_catalog(self, tmp_corvin):
        assert catalog.list_tools(TID) == []

    def test_add_and_get(self, tmp_corvin):
        entry = {
            "id": "test-tool",
            "source": "npm:test-tool@1.0.0",
            "installed_at": "2026-06-07T00:00:00+00:00",
            "runtime": {"command": "npx", "args": ["-y", "test-tool@1.0.0"]},
            "secrets": [],
            "compliance": {"locality": "us_cloud", "network_egress": "required"},
        }
        catalog.add_tool(TID, entry)
        got = catalog.get_tool(TID, "test-tool")
        assert got is not None
        assert got["id"] == "test-tool"
        assert got["source"] == "npm:test-tool@1.0.0"

    def test_list_tools(self, tmp_corvin):
        for i in range(3):
            catalog.add_tool(TID, {
                "id": f"tool-{i}",
                "source": f"npm:tool-{i}@1.0.0",
                "installed_at": "2026-06-07T00:00:00+00:00",
                "runtime": {"command": "npx", "args": ["-y", f"tool-{i}@1.0.0"]},
                "secrets": [],
                "compliance": {},
            })
        tools = catalog.list_tools(TID)
        assert len(tools) == 3
        ids = {t["id"] for t in tools}
        assert ids == {"tool-0", "tool-1", "tool-2"}

    def test_remove_tool(self, tmp_corvin):
        catalog.add_tool(TID, {
            "id": "removable",
            "source": "npm:removable@1.0.0",
            "installed_at": "2026-06-07T00:00:00+00:00",
            "runtime": {"command": "npx", "args": []},
            "secrets": [],
            "compliance": {},
        })
        assert catalog.get_tool(TID, "removable") is not None
        assert catalog.remove_tool(TID, "removable") is True
        assert catalog.get_tool(TID, "removable") is None

    def test_remove_nonexistent(self, tmp_corvin):
        assert catalog.remove_tool(TID, "does-not-exist") is False

    def test_overwrite_tool(self, tmp_corvin):
        catalog.add_tool(TID, {
            "id": "my-tool",
            "source": "npm:my-tool@1.0.0",
            "installed_at": "...",
            "runtime": {"command": "npx", "args": ["-y", "my-tool@1.0.0"]},
            "secrets": [],
            "compliance": {},
        })
        catalog.add_tool(TID, {
            "id": "my-tool",
            "source": "npm:my-tool@2.0.0",
            "installed_at": "...",
            "runtime": {"command": "npx", "args": ["-y", "my-tool@2.0.0"]},
            "secrets": [],
            "compliance": {},
        })
        assert catalog.get_tool(TID, "my-tool")["source"] == "npm:my-tool@2.0.0"


# ── installer ─────────────────────────────────────────────────────────────────


class TestInstaller:
    def test_install_npm_versioned(self, tmp_corvin):
        entry = installer.install(
            "npm:@modelcontextprotocol/server-brave-search@0.6.2", TID
        )
        assert entry["id"] == "modelcontextprotocol-server-brave-search"
        assert entry["source"] == "npm:@modelcontextprotocol/server-brave-search@0.6.2"
        assert entry["runtime"]["command"] == "npx"
        assert "@modelcontextprotocol/server-brave-search@0.6.2" in entry["runtime"]["args"]

    def test_install_npm_no_version(self, tmp_corvin):
        entry = installer.install("npm:some-mcp-server", TID)
        assert entry["runtime"]["args"] == ["-y", "some-mcp-server"]

    def test_install_persists_to_catalog(self, tmp_corvin):
        installer.install("npm:brave-search@0.6.2", TID)
        assert catalog.get_tool(TID, "brave-search") is not None

    def test_install_local_missing_dir(self, tmp_corvin):
        with pytest.raises(ValueError, match="not a directory"):
            installer.install("local:/does/not/exist", TID)

    def test_install_local_no_manifest_node(self, tmp_corvin, tmp_path):
        pkg_dir = tmp_path / "my-node-tool"
        pkg_dir.mkdir()
        (pkg_dir / "package.json").write_text('{"name": "my-node-tool"}')
        (pkg_dir / "index.js").write_text("// entry")
        entry = installer.install(f"local:{pkg_dir}", TID)
        assert entry["runtime"]["command"] == "node"
        assert "index.js" in entry["runtime"]["args"][0]

    def test_install_local_no_manifest_no_structure(self, tmp_corvin, tmp_path):
        empty = tmp_path / "empty-tool"
        empty.mkdir()
        with pytest.raises(ValueError, match="Cannot auto-detect"):
            installer.install(f"local:{empty}", TID)

    def test_install_local_with_yaml_manifest(self, tmp_corvin, tmp_path):
        pytest.importorskip("yaml")
        tool_dir = tmp_path / "my-tool"
        tool_dir.mkdir()
        (tool_dir / "mcp-tool.yaml").write_text(
            "id: my-custom-tool\n"
            "runtime:\n"
            "  command: python3\n"
            "  args: ['server.py']\n"
        )
        entry = installer.install(f"local:{tool_dir}", TID)
        assert entry["id"] == "my-custom-tool"
        assert entry["runtime"]["command"] == "python3"

    def test_unsupported_source(self, tmp_corvin):
        # "ftp:" is genuinely unsupported; docker: is now M4-supported.
        with pytest.raises(ValueError, match="Unsupported source type"):
            installer.install("ftp://somewhere/tool", TID)

    def test_uninstall(self, tmp_corvin):
        installer.install("npm:some-tool@1.0.0", TID)
        assert installer.uninstall("some-tool", TID) is True
        assert catalog.get_tool(TID, "some-tool") is None

    def test_uninstall_nonexistent(self, tmp_corvin):
        assert installer.uninstall("ghost-tool", TID) is False


# ── activate ──────────────────────────────────────────────────────────────────


class TestActivate:
    def _add_tool(self, tid, tool_id):
        catalog.add_tool(tid, {
            "id": tool_id,
            "source": f"npm:{tool_id}@1.0.0",
            "installed_at": "2026-06-07T00:00:00+00:00",
            "runtime": {"command": "npx", "args": ["-y", f"{tool_id}@1.0.0"]},
            "secrets": [],
            "compliance": {"locality": "local", "network_egress": "none"},
        })

    def test_activate_user_scope(self, tmp_corvin):
        self._add_tool(TID, "brave-search")
        activate.activate(TID, "brave-search", "user")
        active = activate.load_active(TID)
        assert "brave-search" in active["user"]

    def test_activate_idempotent(self, tmp_corvin):
        self._add_tool(TID, "brave-search")
        activate.activate(TID, "brave-search", "user")
        activate.activate(TID, "brave-search", "user")
        active = activate.load_active(TID)
        assert active["user"].count("brave-search") == 1

    def test_deactivate(self, tmp_corvin):
        self._add_tool(TID, "brave-search")
        activate.activate(TID, "brave-search", "user")
        assert activate.deactivate(TID, "brave-search", "user") is True
        active = activate.load_active(TID)
        assert "brave-search" not in active["user"]

    def test_deactivate_nonactive(self, tmp_corvin):
        self._add_tool(TID, "brave-search")
        assert activate.deactivate(TID, "brave-search", "user") is False

    def test_invalid_scope(self, tmp_corvin):
        self._add_tool(TID, "brave-search")
        with pytest.raises(ValueError, match="Invalid scope"):
            activate.activate(TID, "brave-search", "invalid-scope")

    def test_activate_unknown_tool(self, tmp_corvin):
        with pytest.raises(ValueError, match="not installed"):
            activate.activate(TID, "ghost-tool", "user")

    def test_get_active_mcp_servers_empty(self, tmp_corvin):
        assert activate.get_active_mcp_servers(TID) == {}

    def test_get_active_mcp_servers_single(self, tmp_corvin):
        self._add_tool(TID, "brave-search")
        activate.activate(TID, "brave-search", "user")
        servers = activate.get_active_mcp_servers(TID)
        assert "brave-search" in servers
        srv = servers["brave-search"]
        assert srv["command"] == "npx"
        assert "brave-search@1.0.0" in srv["args"]

    def test_get_active_mcp_servers_with_secrets(self, tmp_corvin, monkeypatch, tmp_path):
        vault = tmp_path / "secrets.json"
        vault.write_text('{"API_KEY": "test-val"}')
        monkeypatch.setenv("CORVIN_SECRET_VAULT", str(vault))
        catalog.add_tool(TID, {
            "id": "secret-tool",
            "source": "npm:secret-tool@1.0.0",
            "installed_at": "2026-06-07T00:00:00+00:00",
            "runtime": {"command": "npx", "args": ["-y", "secret-tool@1.0.0"]},
            "secrets": [{"name": "API_KEY", "vault_key": "api_key", "required": True}],
            "compliance": {"locality": "local", "network_egress": "none"},
        })
        activate.activate(TID, "secret-tool", "user")
        servers = activate.get_active_mcp_servers(TID)
        assert servers["secret-tool"]["env"]["API_KEY"] == "${API_KEY}"

    def test_hot_reload_after_activate(self, tmp_corvin):
        self._add_tool(TID, "tool-a")
        # First load (empty)
        assert activate.get_active_mcp_servers(TID) == {}
        # Activate changes the file → cache should invalidate on next call
        activate.activate(TID, "tool-a", "user")
        servers = activate.get_active_mcp_servers(TID)
        assert "tool-a" in servers

    def test_multiple_scopes_dedup(self, tmp_corvin):
        self._add_tool(TID, "shared-tool")
        activate.activate(TID, "shared-tool", "user")
        activate.activate(TID, "shared-tool", "session", session_key="discord:test")
        ids = activate.get_active_tool_ids(TID, session_key="discord:test")
        assert ids.count("shared-tool") == 1

    def test_all_scopes_merged(self, tmp_corvin, tmp_path):
        for i in range(3):
            self._add_tool(TID, f"tool-{i}")
        project_dir = str(tmp_path / "proj")
        Path(project_dir).mkdir()
        activate.activate(TID, "tool-0", "session", session_key="discord:test")
        activate.activate(TID, "tool-1", "project", project_dir=project_dir)
        activate.activate(TID, "tool-2", "user")
        servers = activate.get_active_mcp_servers(
            TID, session_key="discord:test", project_dir=project_dir,
        )
        assert set(servers.keys()) == {"tool-0", "tool-1", "tool-2"}


# ── parse_npm_source ──────────────────────────────────────────────────────────


class TestParseNpmSource:
    def test_scoped_with_version(self):
        pkg, ver = installer.parse_npm_source(
            "@modelcontextprotocol/server-brave-search@0.6.2"
        )
        assert pkg == "@modelcontextprotocol/server-brave-search"
        assert ver == "0.6.2"

    def test_unscoped_with_version(self):
        pkg, ver = installer.parse_npm_source("some-package@1.2.3")
        assert pkg == "some-package"
        assert ver == "1.2.3"

    def test_no_version(self):
        pkg, ver = installer.parse_npm_source("some-package")
        assert pkg == "some-package"
        assert ver == ""
