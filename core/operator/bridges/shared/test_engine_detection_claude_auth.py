"""probe_claude_code() must recognize all 5 Claude Code login methods.

Claude Code's own `/login` menu offers three categories:
  1. Claude account with subscription — Pro, Max, Team, or Enterprise
  2. Anthropic Console account — API usage billing
  3. 3rd-party platform — Amazon Bedrock, Microsoft Foundry, or Vertex AI

Before 2026-09-10, probe_claude_code() only recognized (1) via
~/.claude/.credentials.json and (2) via a bare ANTHROPIC_API_KEY env var.
Every 3rd-party install reported "Installed but not authenticated" even
when fully configured and working, because none of Bedrock/Vertex/Foundry
set ANTHROPIC_API_KEY, and because the setup wizards
(`/setup-bedrock`, `/setup-vertex`) write their result to
`~/.claude/settings.json`'s `env` block, not the shell's exported
environment — a check that only reads os.environ misses every
wizard-configured install.

Env var names verified against code.claude.com/docs (2026-09-10):
amazon-bedrock, google-vertex-ai, microsoft-foundry.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import engine_detection as ed  # noqa: E402


class ClaudeCodeAuthDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        # Isolate from this machine's real subscription creds and env —
        # every test controls exactly one credential source at a time.
        self._orig_find_creds = ed._find_claude_credentials
        ed._find_claude_credentials = lambda: None
        self._env_snapshot = dict(os.environ)
        for var in (
            "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
            "AWS_REGION", "AWS_DEFAULT_REGION", "ANTHROPIC_VERTEX_PROJECT_ID",
            "ANTHROPIC_FOUNDRY_RESOURCE", "CLAUDE_CONFIG_DIR",
        ):
            os.environ.pop(var, None)
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        ed._find_claude_credentials = self._orig_find_creds
        os.environ.clear()
        os.environ.update(self._env_snapshot)
        self._tmp.cleanup()

    def _write_settings_env(self, env: dict) -> None:
        os.environ["CLAUDE_CONFIG_DIR"] = self._tmp.name
        Path(self._tmp.name, "settings.json").write_text(json.dumps({"env": env}))

    def _probe(self):
        # probe_claude_code() first requires the `claude` binary to be
        # "found" — patch the binary lookup so these tests don't depend on
        # the test machine actually having it installed.
        orig_find = ed._find_binary
        ed._find_binary = lambda name: "/usr/bin/claude" if name == "claude" else None
        orig_run = ed._run
        ed._run = lambda cmd, timeout=ed._PROBE_TIMEOUT: (0, "1.0.0", "")
        try:
            return ed.probe_claude_code()
        finally:
            ed._find_binary = orig_find
            ed._run = orig_run

    def test_subscription_wins_over_everything_else(self) -> None:
        ed._find_claude_credentials = lambda: Path("/home/x/.claude/.credentials.json")
        os.environ["ANTHROPIC_API_KEY"] = "sk-should-be-ignored"
        r = self._probe()
        self.assertEqual(r.credential_source, "subscription")
        self.assertTrue(r.authenticated)

    def test_bedrock_via_shell_env(self) -> None:
        os.environ["CLAUDE_CODE_USE_BEDROCK"] = "1"
        os.environ["AWS_REGION"] = "us-east-1"
        r = self._probe()
        self.assertEqual(r.credential_source, "bedrock")
        self.assertTrue(r.authenticated)
        self.assertIn("us-east-1", r.detail)

    def test_bedrock_via_settings_json_wizard(self) -> None:
        """The realistic case: /setup-bedrock wrote to settings.json, no
        shell env vars were ever exported."""
        self._write_settings_env({"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": "prod"})
        r = self._probe()
        self.assertEqual(r.credential_source, "bedrock")
        self.assertTrue(r.authenticated)

    def test_vertex_via_settings_json_wizard(self) -> None:
        self._write_settings_env({
            "CLAUDE_CODE_USE_VERTEX": "1", "ANTHROPIC_VERTEX_PROJECT_ID": "my-gcp-project",
        })
        r = self._probe()
        self.assertEqual(r.credential_source, "vertex")
        self.assertTrue(r.authenticated)
        self.assertIn("my-gcp-project", r.detail)

    def test_foundry_via_settings_json(self) -> None:
        self._write_settings_env({
            "CLAUDE_CODE_USE_FOUNDRY": "1", "ANTHROPIC_FOUNDRY_RESOURCE": "my-azure-resource",
        })
        r = self._probe()
        self.assertEqual(r.credential_source, "foundry")
        self.assertTrue(r.authenticated)

    def test_anthropic_console_api_key(self) -> None:
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-real-key"
        r = self._probe()
        self.assertEqual(r.credential_source, "env_var")
        self.assertTrue(r.authenticated)

    def test_anthropic_auth_token_also_recognized(self) -> None:
        os.environ["ANTHROPIC_AUTH_TOKEN"] = "some-bearer-token"
        r = self._probe()
        self.assertEqual(r.credential_source, "env_var")
        self.assertTrue(r.authenticated)

    def test_none_authenticated_when_nothing_configured(self) -> None:
        r = self._probe()
        self.assertEqual(r.credential_source, "none")
        self.assertFalse(r.authenticated)

    def test_shell_env_override_wins_over_settings_json(self) -> None:
        """An explicit shell export is a deliberate override and must beat a
        stale settings.json value — same precedence as every other BYOK
        resolver in this codebase (provider_keys.resolve_by_env_var)."""
        self._write_settings_env({"CLAUDE_CODE_USE_VERTEX": "1"})
        os.environ["CLAUDE_CODE_USE_BEDROCK"] = "1"
        os.environ["AWS_REGION"] = "eu-west-1"
        r = self._probe()
        self.assertEqual(r.credential_source, "bedrock")


if __name__ == "__main__":
    unittest.main()
