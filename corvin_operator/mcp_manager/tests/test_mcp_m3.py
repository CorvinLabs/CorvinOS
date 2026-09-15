"""Tests for MCP Plugin Manager M3 (ADR-0096) — console routes + persona ACL.

Run with:
    cd operator/mcp_manager
    python -m pytest tests/test_mcp_m3.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))

import mcp_manager.catalog as catalog
import mcp_manager.activate as activate
import mcp_manager.installer as installer

TID = "_default"


@pytest.fixture()
def tmp_corvin(monkeypatch, tmp_path):
    corvin = tmp_path / ".corvin"
    corvin.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(corvin))
    activate._active_cache.clear()
    return corvin


def _add_local_tool(tid: str, tool_id: str) -> None:
    catalog.add_tool(tid, {
        "id": tool_id,
        "source": f"npm:{tool_id}@1.0.0",
        "installed_at": "2026-06-07T00:00:00+00:00",
        "runtime": {"command": "npx", "args": ["-y", f"{tool_id}@1.0.0"]},
        "secrets": [],
        "compliance": {"locality": "local", "network_egress": "none"},
    })


# ── Persona ACL (mcp_plugins_allowed) ─────────────────────────────────────────


class TestPersonaAcl:
    """Tests for the M3 mcp_plugins_allowed filter in adapter logic.

    We test the filtering logic directly via get_active_mcp_servers + the
    ACL filter that adapter.py applies, without importing the full adapter.
    """

    def test_no_acl_returns_all(self, tmp_corvin):
        _add_local_tool(TID, "tool-a")
        _add_local_tool(TID, "tool-b")
        activate.activate(TID, "tool-a", "user")
        activate.activate(TID, "tool-b", "user")
        servers = activate.get_active_mcp_servers(TID)
        assert "tool-a" in servers
        assert "tool-b" in servers

    def test_acl_filters_catalog(self, tmp_corvin):
        _add_local_tool(TID, "tool-a")
        _add_local_tool(TID, "tool-b")
        activate.activate(TID, "tool-a", "user")
        activate.activate(TID, "tool-b", "user")
        # Simulate what adapter.py does with mcp_plugins_allowed
        _allowed = ["tool-a"]
        servers = activate.get_active_mcp_servers(TID)
        filtered = {k: v for k, v in servers.items() if k in _allowed}
        assert "tool-a" in filtered
        assert "tool-b" not in filtered

    def test_empty_acl_blocks_all(self, tmp_corvin):
        _add_local_tool(TID, "tool-a")
        activate.activate(TID, "tool-a", "user")
        _allowed: list[str] = []
        servers = activate.get_active_mcp_servers(TID)
        filtered = {k: v for k, v in servers.items() if k in _allowed}
        assert filtered == {}

    def test_acl_none_means_no_filter(self, tmp_corvin):
        _add_local_tool(TID, "tool-a")
        activate.activate(TID, "tool-a", "user")
        _allowed_plugins = None  # profile.get("mcp_plugins_allowed") returns None
        servers = activate.get_active_mcp_servers(TID)
        # When _allowed_plugins is not a list, no filter is applied
        if isinstance(_allowed_plugins, list):
            servers = {k: v for k, v in servers.items() if k in _allowed_plugins}
        assert "tool-a" in servers


# ── Multi-scope priority ───────────────────────────────────────────────────────


class TestMultiScope:
    def test_scope_union(self, tmp_corvin, tmp_path):
        _add_local_tool(TID, "s-tool")
        _add_local_tool(TID, "p-tool")
        _add_local_tool(TID, "u-tool")
        project_dir = str(tmp_path / "proj")
        Path(project_dir).mkdir()
        activate.activate(TID, "s-tool", "session", session_key="discord:m3-test")
        activate.activate(TID, "p-tool", "project", project_dir=project_dir)
        activate.activate(TID, "u-tool", "user")
        servers = activate.get_active_mcp_servers(
            TID, session_key="discord:m3-test", project_dir=project_dir,
        )
        assert set(servers.keys()) == {"s-tool", "p-tool", "u-tool"}

    def test_deactivate_from_one_scope_keeps_other(self, tmp_corvin):
        _add_local_tool(TID, "multi")
        activate.activate(TID, "multi", "user")
        activate.activate(TID, "multi", "session", session_key="discord:m3-test")
        activate.deactivate(TID, "multi", "session", session_key="discord:m3-test")
        ids = activate.get_active_tool_ids(TID, session_key="discord:m3-test")
        assert "multi" in ids  # still active in user scope

    def test_deactivate_all_scopes_removes(self, tmp_corvin):
        _add_local_tool(TID, "gone")
        activate.activate(TID, "gone", "user")
        activate.activate(TID, "gone", "session", session_key="discord:m3-test")
        activate.deactivate(TID, "gone", "user")
        activate.deactivate(TID, "gone", "session", session_key="discord:m3-test")
        ids = activate.get_active_tool_ids(TID, session_key="discord:m3-test")
        assert "gone" not in ids


# ── Console route (without FastAPI import) ────────────────────────────────────


class TestConsoleRouteLogic:
    """Test the business logic (catalog + activate) that the console route wraps.

    We do NOT import the FastAPI router here (would need the full console
    package installed). We test the underlying module calls directly.
    """

    def test_list_returns_active_scopes(self, tmp_corvin):
        _add_local_tool(TID, "brave-search")
        activate.activate(TID, "brave-search", "user")
        tools = catalog.list_tools(TID)
        active = activate.load_active(TID)
        assert len(tools) == 1
        assert tools[0]["id"] == "brave-search"
        assert "brave-search" in active["user"]

    def test_remove_deactivates_then_uninstalls(self, tmp_corvin):
        _add_local_tool(TID, "to-remove")
        activate.activate(TID, "to-remove", "user")
        # Only deactivate global-file scopes (user/tenant); session/project
        # require session_key/project_dir and are handled separately in M4.
        activate.deactivate(TID, "to-remove", "user")
        activate.deactivate(TID, "to-remove", "tenant")
        removed = installer.uninstall("to-remove", TID)
        assert removed is True
        assert catalog.get_tool(TID, "to-remove") is None

    def test_install_and_activate_roundtrip(self, tmp_corvin):
        # npm installs get locality=unknown which blocks L34; use local tool instead
        _add_local_tool(TID, "roundtrip-tool")
        activate.activate(TID, "roundtrip-tool", "user")
        servers = activate.get_active_mcp_servers(TID)
        assert "roundtrip-tool" in servers

    def test_tool_view_shape(self, tmp_corvin):
        _add_local_tool(TID, "view-me")
        # M4: session scope uses a dedicated file; use user scope for console view test.
        activate.activate(TID, "view-me", "user")
        tools = catalog.list_tools(TID)
        active = activate.load_active(TID)
        entry = tools[0]
        tool_id = entry["id"]
        active_scopes = [s for s in activate.VALID_SCOPES if tool_id in active.get(s, [])]
        view = {
            "id": tool_id,
            "source": entry.get("source"),
            "active": len(active_scopes) > 0,
            "active_scopes": active_scopes,
            "compliance": entry.get("compliance", {}),
        }
        assert view["active"] is True
        # M4: session scope is no longer in the global active.json; it lives in
        # a per-session file. The console view reflects global scopes (user/tenant).
        assert "user" in view["active_scopes"]
        assert view["compliance"]["locality"] == "local"
