"""Unit tests for engine_detection.py — ADR-0125.

Tests all five engine probes and the orchestration functions with
fully mocked subprocesses and filesystem access. No real binaries
are invoked; no real network connections are made.

Run: python3 -m pytest core/console/tests/test_engine_detection_adr0125.py -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure the shared module is importable from the repo root.
_REPO = Path(__file__).resolve().parents[3]
_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from engine_detection import (
    EngineProbeResult,
    detect_all,
    probe_claude_code,
    probe_codex_cli,
    probe_copilot,
    probe_hermes,
    probe_opencode,
    recommended_engine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_run(rc: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    """Return a mock for _run that always succeeds with the given values."""
    mock = MagicMock(return_value=(rc, stdout, stderr))
    return mock


# ---------------------------------------------------------------------------
# probe_claude_code
# ---------------------------------------------------------------------------

class TestProbeClaudeCode(unittest.TestCase):

    def test_not_installed(self):
        with patch("engine_detection.shutil.which", return_value=None):
            r = probe_claude_code()
        self.assertFalse(r.installed)
        self.assertFalse(r.authenticated)
        self.assertIsNone(r.credential_source)
        self.assertEqual(r.engine_id, "claude_code")

    def test_subscription_via_credentials_json(self):
        creds = {"claudeAiOauth": {"accessToken": "tok-123"}}
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = Path(tmp) / ".claude"
            claude_dir.mkdir()
            creds_file = claude_dir / ".credentials.json"
            creds_file.write_text(json.dumps(creds))
            with (
                patch("engine_detection.shutil.which", return_value="/usr/bin/claude"),
                patch("engine_detection._run", return_value=(0, "1.2.3", "")),
                patch("engine_detection.Path.home", return_value=Path(tmp)),
            ):
                r = probe_claude_code()
        self.assertTrue(r.installed)
        self.assertTrue(r.authenticated)
        self.assertEqual(r.credential_source, "subscription")

    def test_subscription_via_access_token(self):
        creds = {"accessToken": "tok-abc"}
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".claude").mkdir()
            (Path(tmp) / ".claude" / ".credentials.json").write_text(json.dumps(creds))
            with (
                patch("engine_detection.shutil.which", return_value="/usr/bin/claude"),
                patch("engine_detection._run", return_value=(0, "1.0.0", "")),
                patch("engine_detection.Path.home", return_value=Path(tmp)),
            ):
                r = probe_claude_code()
        self.assertEqual(r.credential_source, "subscription")

    def test_env_var_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("engine_detection.shutil.which", return_value="/usr/bin/claude"),
                patch("engine_detection._run", return_value=(0, "1.0.0", "")),
                patch("engine_detection.Path.home", return_value=Path(tmp)),
                patch("engine_detection.os.environ.get", side_effect=lambda k, d=None:
                      "sk-test123" if k == "ANTHROPIC_API_KEY" else d),
            ):
                r = probe_claude_code()
        self.assertTrue(r.authenticated)
        self.assertEqual(r.credential_source, "env_var")

    def test_installed_not_authenticated(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("engine_detection.shutil.which", return_value="/usr/bin/claude"),
                patch("engine_detection._run", return_value=(0, "1.0.0", "")),
                patch("engine_detection.Path.home", return_value=Path(tmp)),
                patch("engine_detection.os.environ.get", return_value=None),
            ):
                r = probe_claude_code()
        self.assertTrue(r.installed)
        self.assertFalse(r.authenticated)
        self.assertEqual(r.credential_source, "none")

    def test_version_extracted(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("engine_detection.shutil.which", return_value="/usr/bin/claude"),
                patch("engine_detection._run", return_value=(0, "claude 2.1.0\n", "")),
                patch("engine_detection.Path.home", return_value=Path(tmp)),
                patch("engine_detection.os.environ.get", return_value=None),
            ):
                r = probe_claude_code()
        self.assertEqual(r.version, "claude 2.1.0")

    def test_corrupted_creds_json_falls_through(self):
        """Corrupted credentials file should not crash — fall through to env_var check."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".claude").mkdir()
            (Path(tmp) / ".claude" / ".credentials.json").write_text("not-json!!!")
            with (
                patch("engine_detection.shutil.which", return_value="/usr/bin/claude"),
                patch("engine_detection._run", return_value=(0, "1.0.0", "")),
                patch("engine_detection.Path.home", return_value=Path(tmp)),
                patch("engine_detection.os.environ.get", return_value=None),
            ):
                r = probe_claude_code()
        # Should fall through to none (not crash)
        self.assertEqual(r.credential_source, "none")


# ---------------------------------------------------------------------------
# probe_hermes
# ---------------------------------------------------------------------------

class TestProbeHermes(unittest.TestCase):

    def _mock_ollama_response(self, models: list[str]):
        """Return a mock urllib.request.urlopen context manager with model data."""
        import io
        data = json.dumps({"models": [{"name": m} for m in models]}).encode()
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read = MagicMock(return_value=data)
        return mock_resp

    def test_not_installed_and_not_running(self):
        import urllib.error
        with (
            patch("engine_detection.shutil.which", return_value=None),
            patch("engine_detection.urllib.request.urlopen",
                  side_effect=urllib.error.URLError("refused")),
        ):
            r = probe_hermes()
        self.assertFalse(r.installed)
        self.assertFalse(r.authenticated)
        self.assertIsNone(r.credential_source)
        self.assertEqual(r.engine_id, "hermes")

    def test_running_with_models(self):
        mock_resp = self._mock_ollama_response(["qwen3:8b", "qwen3:1.7b"])
        with (
            patch("engine_detection.shutil.which", return_value="/usr/bin/ollama"),
            patch("engine_detection.urllib.request.urlopen", return_value=mock_resp),
            patch("engine_detection._run", return_value=(0, "ollama 0.5.0", "")),
        ):
            r = probe_hermes()
        self.assertTrue(r.installed)
        self.assertTrue(r.authenticated)
        self.assertEqual(r.credential_source, "config_file")
        self.assertEqual(r.models, ["qwen3:8b", "qwen3:1.7b"])

    def test_running_no_models(self):
        mock_resp = self._mock_ollama_response([])
        with (
            patch("engine_detection.shutil.which", return_value="/usr/bin/ollama"),
            patch("engine_detection.urllib.request.urlopen", return_value=mock_resp),
            patch("engine_detection._run", return_value=(0, "0.5.0", "")),
        ):
            r = probe_hermes()
        self.assertTrue(r.installed)
        self.assertFalse(r.authenticated)
        self.assertEqual(r.credential_source, "none")
        self.assertEqual(r.models, [])

    def test_installed_not_running(self):
        import urllib.error
        with (
            patch("engine_detection.shutil.which", return_value="/usr/bin/ollama"),
            patch("engine_detection.urllib.request.urlopen",
                  side_effect=urllib.error.URLError("refused")),
            patch("engine_detection._run", return_value=(0, "0.5.0", "")),
        ):
            r = probe_hermes()
        self.assertTrue(r.installed)
        self.assertFalse(r.authenticated)
        self.assertEqual(r.credential_source, "none")

    def test_docker_only_no_binary(self):
        """Ollama via Docker — no binary in PATH but API is reachable."""
        mock_resp = self._mock_ollama_response(["llama3.2:3b"])
        with (
            patch("engine_detection.shutil.which", return_value=None),
            patch("engine_detection.urllib.request.urlopen", return_value=mock_resp),
        ):
            r = probe_hermes()
        # installed=True because the API responded
        self.assertTrue(r.installed)
        self.assertTrue(r.authenticated)
        self.assertEqual(r.models, ["llama3.2:3b"])


# ---------------------------------------------------------------------------
# probe_copilot
# ---------------------------------------------------------------------------

class TestProbeCopilot(unittest.TestCase):

    def test_not_installed(self):
        with patch("engine_detection.shutil.which", return_value=None):
            r = probe_copilot()
        self.assertFalse(r.installed)
        self.assertIsNone(r.credential_source)

    def test_subscription_via_config_file(self):
        cfg = {"github_token": "ghs_xxx"}
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".copilot").mkdir()
            (Path(tmp) / ".copilot" / "config.json").write_text(json.dumps(cfg))
            with (
                patch("engine_detection.shutil.which", return_value="/usr/bin/copilot"),
                patch("engine_detection._run", return_value=(0, "1.0.56", "")),
                patch("engine_detection.Path.home", return_value=Path(tmp)),
            ):
                r = probe_copilot()
        self.assertEqual(r.credential_source, "subscription")
        self.assertTrue(r.authenticated)

    def test_subscription_via_gh_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            def run_side_effect(cmd, **kwargs):
                if cmd[0] == "copilot":
                    return (0, "1.0.56", "")
                if cmd == ["gh", "auth", "status"]:
                    return (0, "Logged in to github.com as user (oauth_token)", "")
                return (-1, "", "")

            with (
                patch("engine_detection.shutil.which", return_value="/usr/bin/copilot"),
                patch("engine_detection._run", side_effect=run_side_effect),
                patch("engine_detection.Path.home", return_value=Path(tmp)),
            ):
                r = probe_copilot()
        self.assertEqual(r.credential_source, "subscription")

    def test_env_var_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("engine_detection.shutil.which", return_value="/usr/bin/copilot"),
                patch("engine_detection._run", return_value=(1, "", "not logged in")),
                patch("engine_detection.Path.home", return_value=Path(tmp)),
                patch("engine_detection.os.environ.get",
                      side_effect=lambda k, d=None: "ghp_xxx" if k == "GH_TOKEN" else d),
            ):
                r = probe_copilot()
        self.assertEqual(r.credential_source, "env_var")

    def test_not_authenticated(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("engine_detection.shutil.which", return_value="/usr/bin/copilot"),
                patch("engine_detection._run", return_value=(1, "", "not logged in")),
                patch("engine_detection.Path.home", return_value=Path(tmp)),
                patch("engine_detection.os.environ.get", return_value=None),
            ):
                r = probe_copilot()
        self.assertEqual(r.credential_source, "none")
        self.assertFalse(r.authenticated)


# ---------------------------------------------------------------------------
# probe_opencode
# ---------------------------------------------------------------------------

class TestProbeOpencode(unittest.TestCase):

    def test_not_installed(self):
        with patch("engine_detection.shutil.which", return_value=None):
            r = probe_opencode()
        self.assertFalse(r.installed)

    def test_anthropic_api_key(self):
        with (
            patch("engine_detection.shutil.which", return_value="/usr/bin/opencode"),
            patch("engine_detection._run", return_value=(0, "0.1.0", "")),
            patch("engine_detection.os.environ.get",
                  side_effect=lambda k, d=None: "sk-ant-xxx" if k == "ANTHROPIC_API_KEY" else d),
        ):
            r = probe_opencode()
        self.assertEqual(r.credential_source, "env_var")

    def test_openai_api_key(self):
        import urllib.error
        with (
            patch("engine_detection.shutil.which", return_value="/usr/bin/opencode"),
            patch("engine_detection._run", return_value=(0, "0.1.0", "")),
            patch("engine_detection.os.environ.get",
                  side_effect=lambda k, d=None:
                      None if k == "ANTHROPIC_API_KEY" else ("sk-oai" if k == "OPENAI_API_KEY" else d)),
            patch("engine_detection.urllib.request.urlopen",
                  side_effect=urllib.error.URLError("refused")),
        ):
            r = probe_opencode()
        self.assertEqual(r.credential_source, "env_var")

    def test_no_keys_no_ollama(self):
        import urllib.error
        with (
            patch("engine_detection.shutil.which", return_value="/usr/bin/opencode"),
            patch("engine_detection._run", return_value=(0, "0.1.0", "")),
            patch("engine_detection.os.environ.get", return_value=None),
            patch("engine_detection.urllib.request.urlopen",
                  side_effect=urllib.error.URLError("refused")),
        ):
            r = probe_opencode()
        self.assertEqual(r.credential_source, "none")
        self.assertFalse(r.authenticated)


# ---------------------------------------------------------------------------
# probe_codex_cli
# ---------------------------------------------------------------------------

class TestProbeCodexCli(unittest.TestCase):

    def test_not_installed(self):
        with patch("engine_detection.shutil.which", return_value=None):
            r = probe_codex_cli()
        self.assertIsNone(r.credential_source)

    def test_openai_key_present(self):
        with (
            patch("engine_detection.shutil.which", return_value="/usr/bin/codex"),
            patch("engine_detection._run", return_value=(0, "0.2.0", "")),
            patch("engine_detection.os.environ.get",
                  side_effect=lambda k, d=None: "sk-oai" if k == "OPENAI_API_KEY" else d),
        ):
            r = probe_codex_cli()
        self.assertEqual(r.credential_source, "env_var")
        self.assertTrue(r.authenticated)

    def test_no_key(self):
        with (
            patch("engine_detection.shutil.which", return_value="/usr/bin/codex"),
            patch("engine_detection._run", return_value=(0, "0.2.0", "")),
            patch("engine_detection.os.environ.get", return_value=None),
        ):
            r = probe_codex_cli()
        self.assertEqual(r.credential_source, "none")


# ---------------------------------------------------------------------------
# detect_all + recommended_engine
# ---------------------------------------------------------------------------

class TestDetectAll(unittest.TestCase):

    def test_returns_five_results(self):
        """detect_all() must probe all five registered engines (plus any discovered extras)."""
        results = detect_all()
        ids = {r.engine_id for r in results}
        registered = {"claude_code", "hermes", "opencode", "codex_cli", "copilot"}
        self.assertTrue(registered.issubset(ids), f"Missing registered engines: {registered - ids}")

    def test_probe_exception_does_not_crash(self):
        """A failing probe must be silently dropped — detect_all never raises."""
        def _bad_probe():
            raise RuntimeError("disk on fire")

        import engine_detection as ed
        original_probes = list(ed._PROBES)
        original_discover = ed._discover_extra_engines
        ed._PROBES = [_bad_probe, _bad_probe, _bad_probe, _bad_probe, _bad_probe]
        # Replace discovery too so no real binaries bleed into the count.
        ed._discover_extra_engines = lambda: []
        try:
            results = detect_all()
        finally:
            ed._PROBES = original_probes
            ed._discover_extra_engines = original_discover
        self.assertEqual(results, [])

    def test_partial_probe_failure(self):
        """Only the failing probe is dropped; others still appear."""
        def _bad_probe():
            raise RuntimeError("oops")

        import engine_detection as ed
        original_probes = list(ed._PROBES)
        original_discover = ed._discover_extra_engines
        # Replace just the first registered probe; suppress discovery so extra
        # installed binaries (aider, llm, gemini...) don't bleed into the count.
        ed._PROBES = [_bad_probe] + original_probes[1:]
        ed._discover_extra_engines = lambda: []
        try:
            results = detect_all()
        finally:
            ed._PROBES = original_probes
            ed._discover_extra_engines = original_discover
        # Should still have 4 registered results (one probe failed, 4 succeeded)
        self.assertEqual(len(results), 4)

    def test_concurrent_timeout_drops_slow_probes(self):
        """Probes that exceed _DETECT_TIMEOUT are silently dropped."""
        import time
        import engine_detection as ed

        def _slow_probe():
            time.sleep(20)  # exceeds _DETECT_TIMEOUT
            return EngineProbeResult(engine_id="slow", installed=False, authenticated=False,
                                     credential_source=None, version=None)

        def _fast_probe():
            return EngineProbeResult(engine_id="fast", installed=True, authenticated=True,
                                     credential_source="subscription", version="1.0")

        original = list(ed._PROBES)
        original_timeout = ed._DETECT_TIMEOUT
        ed._PROBES = [_slow_probe, _fast_probe]
        ed._DETECT_TIMEOUT = 0.5  # short timeout so test runs quickly
        try:
            results = detect_all()
        finally:
            ed._PROBES = original
            ed._DETECT_TIMEOUT = original_timeout
        # Only the fast probe should appear; slow probe timed out
        ids = {r.engine_id for r in results}
        self.assertIn("fast", ids)
        self.assertNotIn("slow", ids)


class TestProbeCopilotGhAuthRobustness(unittest.TestCase):
    """Covers the fix for MAJOR #3 — gh auth status stderr compatibility."""

    def test_gh_auth_authenticated_via_stderr_only(self):
        """Older gh versions write to stderr, not stdout — rc=0 alone must suffice."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            def run_side_effect(cmd, **kwargs):
                if cmd[0] == "copilot":
                    return (0, "1.0.56", "")
                if cmd == ["gh", "auth", "status"]:
                    # Older gh: output on stderr, stdout empty
                    return (0, "", "✓ Logged in to github.com as user")
                return (-1, "", "")

            with (
                patch("engine_detection.shutil.which", return_value="/usr/bin/copilot"),
                patch("engine_detection._run", side_effect=run_side_effect),
                patch("engine_detection.Path.home", return_value=Path(tmp)),
            ):
                r = probe_copilot()
        self.assertTrue(r.authenticated)
        self.assertEqual(r.credential_source, "subscription")

    def test_gh_auth_not_authenticated_on_nonzero_rc(self):
        """gh returns nonzero when not logged in — must not classify as subscription."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            def run_side_effect(cmd, **kwargs):
                if cmd[0] == "copilot":
                    return (0, "1.0.56", "")
                if cmd == ["gh", "auth", "status"]:
                    return (1, "", "not logged in to any account")
                return (-1, "", "")

            with (
                patch("engine_detection.shutil.which", return_value="/usr/bin/copilot"),
                patch("engine_detection._run", side_effect=run_side_effect),
                patch("engine_detection.Path.home", return_value=Path(tmp)),
                patch("engine_detection.os.environ.get", return_value=None),
            ):
                r = probe_copilot()
        self.assertFalse(r.authenticated)
        self.assertEqual(r.credential_source, "none")


class TestRecommendedEngine(unittest.TestCase):

    def _r(self, eid: str, auth: bool, src: str | None) -> EngineProbeResult:
        return EngineProbeResult(
            engine_id=eid,
            installed=True,
            authenticated=auth,
            credential_source=src,
            version=None,
            models=[],
        )

    def test_subscription_preferred_over_env_var(self):
        results = [
            self._r("codex_cli", True, "env_var"),
            self._r("claude_code", True, "subscription"),
        ]
        self.assertEqual(recommended_engine(results), "claude_code")

    def test_env_var_returned_when_no_subscription(self):
        results = [
            self._r("opencode", False, "none"),
            self._r("codex_cli", True, "env_var"),
        ]
        self.assertEqual(recommended_engine(results), "codex_cli")

    def test_config_file_returned_as_last_resort(self):
        results = [
            self._r("hermes", True, "config_file"),
        ]
        self.assertEqual(recommended_engine(results), "hermes")

    def test_none_when_nothing_authenticated(self):
        results = [
            self._r("claude_code", False, "none"),
            self._r("hermes", False, "none"),
        ]
        self.assertIsNone(recommended_engine(results))

    def test_empty_list(self):
        self.assertIsNone(recommended_engine([]))

    def test_priority_order_wins_over_list_order(self):
        """When multiple engines have subscription, _ENGINE_PRIORITY wins over list order.

        Previously this was list-order dependent (non-deterministic after concurrent
        detect_all). Now recommended_engine uses explicit _ENGINE_PRIORITY so
        claude_code always beats copilot regardless of collect order.
        """
        # copilot listed first, claude_code second — claude_code must still win
        results = [
            self._r("copilot", True, "subscription"),
            self._r("claude_code", True, "subscription"),
        ]
        self.assertEqual(recommended_engine(results), "claude_code")


if __name__ == "__main__":
    unittest.main()
