#!/usr/bin/env python3
"""The L10 gate covers Claude Code's ``PowerShell`` tool (native Windows).

Claude Code on Windows without Git Bash runs shell commands through its
``PowerShell`` tool; hooks.json's matcher and check() only knew ``Bash``, so a
``Remove-Item``/``Set-Content`` on the audit chain or forge policy was never
inspected. Also pins that every ``${CLAUDE_PLUGIN_ROOT}`` in hooks.json is
quoted: Claude Code substitutes it as plain text, and an unquoted profile path
with a space (``C:/Users/Jane Doe/...``) made Git Bash split it -> the hook
script was not found -> non-blocking error -> the gate never ran (verified
live with Claude Code 2.1.310).
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import path_gate as pg  # type: ignore  # noqa: E402


class TestHooksJson(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((_HERE / "hooks.json").read_text(encoding="utf-8"))

    def _commands(self):
        for entries in self.cfg["hooks"].values():
            for e in entries:
                for h in e.get("hooks", []):
                    yield h["command"]

    def test_plugin_root_placeholder_is_quoted(self):
        for c in self._commands():
            if "${CLAUDE_PLUGIN_ROOT}" in c:
                self.assertRegex(c, r'"\$\{CLAUDE_PLUGIN_ROOT\}[^"]*"', c)

    def test_path_gate_matcher_covers_powershell(self):
        for e in self.cfg["hooks"]["PreToolUse"]:
            if any("path_gate.py" in h["command"] for h in e["hooks"]):
                self.assertIn("PowerShell", re.split(r"\|", e["matcher"]))
                return
        self.fail("path_gate hook missing")


class TestPowerShellTool(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve() / ".corvin"
        (self.home / "global" / "forge").mkdir(parents=True)
        (self.home / "global" / "forge" / "audit.jsonl").write_text("x")
        self.audit = str(self.home / "global" / "forge" / "audit.jsonl")
        env = mock.patch.dict(os.environ, {"CORVIN_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self._tmp.cleanup)

    def _check(self, cmd):
        return pg.check({"tool_name": "PowerShell", "tool_input": {"command": cmd}})

    def assertDenied(self, cmd):
        ok, reason = self._check(cmd)
        self.assertFalse(ok, cmd)
        self.assertIn("path_gate", reason)

    def assertAllowed(self, cmd):
        ok, reason = self._check(cmd)
        self.assertTrue(ok, f"{cmd}: {reason}")

    def test_writers_denied(self):
        self.assertDenied(f'Set-Content -Path "{self.audit}" -Value x')
        self.assertDenied(f"Set-Content -Path:'{self.audit}' -Value x")
        self.assertDenied(f'"x" | Out-File "{self.audit}"')
        self.assertDenied(f'Add-Content "{self.audit}" "y"')
        self.assertDenied(f'Copy-Item C:/tmp/evil.jsonl "{self.audit}"')

    def test_destructive_denied(self):
        self.assertDenied(f'Remove-Item -Force "{self.audit}"')
        self.assertDenied(f'del "{self.audit}"')
        self.assertDenied(f'Remove-Item -Recurse -Force "{self.home}"')
        self.assertDenied(f'Move-Item "{self.audit}" C:/tmp/x')

    def test_env_variable_forms(self):
        with mock.patch.dict(os.environ, {"CORVIN_TEST_HOME": str(self.home)}):
            self.assertDenied(
                "Remove-Item $env:CORVIN_TEST_HOME/global/forge/audit.jsonl")

    def test_redirect_and_opaque(self):
        self.assertDenied(f'echo x > "{self.audit}"')
        self.assertDenied(
            f"[System.IO.File]::WriteAllText('{self.audit}', 'x')")
        self.assertDenied(f"Invoke-Expression \"Remove-Item '{self.audit}'\"")

    def test_harmless_commands_allowed(self):
        self.assertAllowed("Get-ChildItem C:/tmp")
        self.assertAllowed(f'Get-Content "{self.audit}"')
        self.assertAllowed("Set-Content -Path C:/tmp/notes.txt -Value hi")
        self.assertAllowed("Remove-Item C:/tmp/scratch.txt")


if __name__ == "__main__":
    unittest.main()
