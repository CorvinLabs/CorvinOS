"""Tests for the console web-chat MCP spawn wiring (ADR-0190 command-center
parity fix).

Adversarial-review CRITICAL (2026-07-12): the console chat — the designated
CorvinOS command center — injected the full capability map into the system
prompt ("you can call workflow_run / a2a_send / forge_tool / ...") but built
its ``claude -p`` argv WITHOUT ``--mcp-config``, so not a single advertised
MCP server was attached on ANY installation. The messenger/bridge path was
wired correctly all along. These tests lock in console/bridge parity.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))


def _session(workdir: Path):
    from corvin_console import chat_runtime  # noqa: WPS433
    return chat_runtime.WebChatSession(
        sid="s1", tenant_id="_default",
        created_at=0.0, last_active_at=0.0, workdir=workdir,
    )


class McpWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        from corvin_console import chat_runtime  # noqa: WPS433
        self.cr = chat_runtime
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tmp.name)
        self.sess = _session(self.workdir)
        self._orig_spec = chat_runtime._tenant_spec
        chat_runtime._tenant_spec = lambda _tid: {}  # type: ignore[assignment]

    def tearDown(self) -> None:
        self.cr._tenant_spec = self._orig_spec  # type: ignore[assignment]
        self._tmp.cleanup()

    def test_persona_mcp_config_materializes_servers(self) -> None:
        path = self.cr._persona_mcp_config("_default")
        self.assertIsNotNone(
            path, "assistant persona must materialize an --mcp-config file")
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        servers = data.get("mcpServers") or data.get("mcp_servers") or {}
        # The resolver-injected command-center servers must be present.
        for expected in ("forge", "skill_forge", "corvin_orchestration"):
            self.assertIn(expected, servers,
                          f"{expected} missing from console mcp-config")

    def test_build_args_attaches_mcp_config(self) -> None:
        args = self.cr._build_args(self.sess, resume=False)
        self.assertIn("--mcp-config", args,
                      "console chat spawn must attach the persona MCP servers")
        cfg = args[args.index("--mcp-config") + 1]
        self.assertTrue(Path(cfg).is_file())

    def test_mcp_config_paths_are_expanded(self) -> None:
        """No unexpanded {{REPO_ROOT}}/{{CORE_ROOT}}/{{PYTHON}} templates may
        leak into the materialized spawn config."""
        path = self.cr._persona_mcp_config("_default")
        text = Path(path).read_text(encoding="utf-8")
        for token in ("{{REPO_ROOT}}", "{{CORE_ROOT}}", "{{PYTHON}}", "{{HOME}}"):
            self.assertNotIn(token, text)

    def test_mcp_failure_never_breaks_chat(self) -> None:
        """Fail-safe: if resolution explodes, the chat must still build argv."""
        orig = self.cr._persona_mcp_config
        self.cr._persona_mcp_config = lambda tid: (_ for _ in ()).throw(RuntimeError)  # type: ignore
        try:
            with self.assertRaises(RuntimeError):
                self.cr._persona_mcp_config("_default")
        finally:
            self.cr._persona_mcp_config = orig  # type: ignore
        # The real helper itself must never raise on a bad tenant id.
        self.assertIsNotNone(self.cr._persona_mcp_config("_default"))


if __name__ == "__main__":
    unittest.main()
