"""Live E2E for Layer 30 — spawns real codex / opencode / forge binaries.

These tests verify the **structural correctness** of the generated
MCP configs against the real CLI binaries' parsers and runners. They
skip cleanly when the binaries are absent so single-operator setups
without all three engines installed still pass run-all-tests.sh.

What's tested per binary:
  * Forge MCP server (always available) — JSON-RPC tools/list returns
    forge_tool / forge_promote / forge_list, proving the
    materialised config + persona env actually starts a working
    MCP server end-to-end.
  * Codex 0.125+ — `codex mcp list` with our CODEX_HOME enumerates
    both MCP servers as "enabled" with correct args + env. Verifies
    the [mcp_servers.<name>] TOML schema is accepted.
  * OpenCode 1.14+ — `opencode mcp list` from the working_dir
    (where we wrote opencode.json) reports both servers as
    "connected", proving the {mcp: {<name>: {type, command,
    environment, enabled}}} JSON schema is accepted AND the
    referenced binaries actually spawn.

NOT tested live (would burn API credits):
  * Actually delegating a real "create me a tool" prompt to a worker
    and observing it call mcp__forge__forge_tool. That's the
    operator's smoke test — see CLAUDE.md Layer 30 References.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


_PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_DIR))

from corvin_delegate.mcp_config_builder import (  # noqa: E402
    ENGINE_CLAUDE_CODE,
    ENGINE_CODEX,
    ENGINE_OPENCODE,
    build_mcp_specs,
    materialise_for_engine,
)


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------


def _find_codex() -> str | None:
    """Prefer the nvm install (codex >= 0.125, the version the engine
    factory targets); fall back to PATH lookup."""
    nvm = Path.home() / ".nvm/versions/node/v22.22.0/bin/codex"
    if nvm.exists():
        return str(nvm)
    return shutil.which("codex")


def _find_opencode() -> str | None:
    home = Path.home() / ".opencode/bin/opencode"
    if home.exists():
        return str(home)
    return shutil.which("opencode")


_CODEX_BIN = _find_codex()
_OPENCODE_BIN = _find_opencode()


# ---------------------------------------------------------------------------
# Forge MCP standalone (no engine binary needed)
# ---------------------------------------------------------------------------


class ForgeMcpLiveTests(unittest.TestCase):
    """Spawn the forge MCP server with our materialised config + env
    and confirm we can JSON-RPC against it."""

    def test_forge_mcp_responds_to_tools_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Sandbox the forge tree so this test doesn't pollute the
            # operator's real $CORVIN_HOME.
            home = tmp_path / "corvin-home"
            (home / "global" / "forge").mkdir(parents=True)

            specs = build_mcp_specs(
                persona="coder",
                forge_enabled=True,
                skill_forge_enabled=False,
            )
            result = materialise_for_engine(
                engine_id=ENGINE_CLAUDE_CODE,
                specs=specs,
                tempdir=tmp_path,
            )
            cfg_path = result["spawn_kwargs"]["mcp_config_path"]
            cfg = json.loads(Path(cfg_path).read_text())
            forge_spec = cfg["mcpServers"]["forge"]

            env = os.environ.copy()
            env.update(forge_spec["env"])
            env["CORVIN_HOME"] = str(home)

            cmd = [forge_spec["command"]] + forge_spec["args"]
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                bufsize=1,
            )
            try:
                proc.stdin.write(json.dumps({
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "live-e2e", "version": "0.1"},
                    }
                }) + "\n")
                proc.stdin.flush()
                proc.stdin.write(json.dumps({
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                }) + "\n")
                proc.stdin.flush()
                proc.stdin.write(json.dumps({
                    "jsonrpc": "2.0", "id": 2, "method": "tools/list",
                }) + "\n")
                proc.stdin.flush()

                responses: list[dict] = []
                deadline = time.time() + 10
                while time.time() < deadline and len(responses) < 2:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    try:
                        responses.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            finally:
                try:
                    proc.stdin.write(json.dumps({
                        "jsonrpc": "2.0", "id": 99, "method": "shutdown",
                    }) + "\n")
                    proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    proc.wait(timeout=2)
                # Close pipes explicitly to silence ResourceWarning.
                for fh in (proc.stdin, proc.stdout, proc.stderr):
                    if fh is not None:
                        try:
                            fh.close()
                        except (BrokenPipeError, OSError):
                            pass

            self.assertEqual(len(responses), 2, "expected init + tools/list")
            tools = responses[1]["result"]["tools"]
            names = [t["name"] for t in tools]
            for required in ("forge_tool", "forge_promote", "forge_list"):
                self.assertIn(required, names,
                              f"forge MCP did not expose {required}")


# ---------------------------------------------------------------------------
# Codex live config-parse (requires codex 0.125+)
# ---------------------------------------------------------------------------


@unittest.skipUnless(_CODEX_BIN, "codex CLI not installed")
class CodexConfigParseLiveTests(unittest.TestCase):

    def test_codex_mcp_list_enumerates_both_servers(self):
        """codex 0.125+ honours $CODEX_HOME and reads config.toml at
        startup. With our materialised file, `codex mcp list` should
        enumerate both servers as enabled."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            specs = build_mcp_specs(
                persona="coder",
                forge_enabled=True,
                skill_forge_enabled=True,
            )
            result = materialise_for_engine(
                engine_id=ENGINE_CODEX,
                specs=specs,
                tempdir=tmp_path,
            )
            env = os.environ.copy()
            env.update(result["env_overlay"])

            try:
                r = subprocess.run(
                    [_CODEX_BIN, "mcp", "list"],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
            except subprocess.TimeoutExpired:
                self.fail("codex mcp list timed out")
            # Skip if this codex is too old for CODEX_HOME (0.114 ignores it).
            if "CODEX_HOME points to" in r.stderr and "does not exist" in r.stderr:
                self.skipTest("codex too old for CODEX_HOME override")
            if "No MCP servers configured" in r.stdout:
                self.skipTest(
                    "codex version ignores CODEX_HOME (likely 0.114 or older)")
            self.assertEqual(r.returncode, 0, f"stderr: {r.stderr[:300]}")
            self.assertIn("forge", r.stdout,
                          f"forge missing; stdout: {r.stdout}")
            self.assertIn("skill_forge", r.stdout,
                          f"skill_forge missing; stdout: {r.stdout}")
            # Status should NOT be "disabled" / "error".
            self.assertNotIn("disabled", r.stdout.lower())


# ---------------------------------------------------------------------------
# OpenCode live config-parse (requires opencode 1.14+)
# ---------------------------------------------------------------------------


@unittest.skipUnless(_OPENCODE_BIN, "opencode CLI not installed")
class OpenCodeConfigParseLiveTests(unittest.TestCase):

    def test_opencode_mcp_list_reports_connected(self):
        """opencode 1.14+ resolves opencode.json from cwd. With our
        materialised file, `opencode mcp list` should show both servers
        as connected (i.e. it actually spawned them and got a response)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            wd = tmp_path / "workdir"
            wd.mkdir()
            specs = build_mcp_specs(
                persona="coder",
                forge_enabled=True,
                skill_forge_enabled=True,
            )
            materialise_for_engine(
                engine_id=ENGINE_OPENCODE,
                specs=specs,
                tempdir=tmp_path,
                working_dir=wd,
            )
            try:
                r = subprocess.run(
                    [_OPENCODE_BIN, "mcp", "list"],
                    cwd=str(wd),
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
            except subprocess.TimeoutExpired:
                self.fail("opencode mcp list timed out")
            self.assertEqual(r.returncode, 0,
                             f"stderr: {r.stderr[:400]}")
            self.assertIn("forge", r.stdout,
                          f"forge missing; stdout: {r.stdout}")
            self.assertIn("skill_forge", r.stdout,
                          f"skill_forge missing; stdout: {r.stdout}")
            # `connected` is the success status opencode 1.14 prints
            # for a server it managed to start + handshake with.
            self.assertIn("connected", r.stdout.lower(),
                          f"opencode reports failure; stdout: {r.stdout}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
