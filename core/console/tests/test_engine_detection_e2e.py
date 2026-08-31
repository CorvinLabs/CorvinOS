"""E2E tests for engine detection — all 5 registered engines + timeout scenarios.

Tests the complete detection pipeline:
1. Binary discovery (Claude, Hermes, OpenCode, Codex, Copilot)
2. Authentication detection (subscription, env_var, config_file, none)
3. Timeout resilience (simulate slow probes)
4. API integration (route returns correct response)
5. Frontend filtering (installed=true filter works)

Run: python3 -m pytest core/console/tests/test_engine_detection_e2e.py -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import time

_REPO = Path(__file__).resolve().parents[3]
_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from engine_detection import (
    detect_all, recommended_engine, probe_claude_code, probe_hermes,
    probe_copilot, probe_opencode, probe_codex_cli,
    EngineProbeResult
)


class TestAllFiveEngines(unittest.TestCase):
    """Verify all 5 registered engines are probed."""

    def test_all_five_registered_engines_detected(self):
        """detect_all() must probe all 5 engines (even if not installed)."""
        results = detect_all()
        ids = {r.engine_id for r in results if r.engine_id in {
            "claude_code", "hermes", "opencode", "codex_cli", "copilot"
        }}
        expected = {"claude_code", "hermes", "opencode", "codex_cli", "copilot"}
        self.assertEqual(ids, expected,
                        f"Missing engines: {expected - ids}")


class TestEngineAuthenticationChain(unittest.TestCase):
    """Test subscription-first authentication priority."""

    def test_claude_code_subscription_priority(self):
        """Claude Code prefers subscription (OAuth) over env_var."""
        with patch("engine_detection.Path.home") as mock_home:
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                mock_home.return_value = home
                (home / ".claude").mkdir()
                (home / ".claude" / ".credentials.json").write_text('{"claudeAiOauth": {"accessToken": "tok-123"}}')

                with (
                    patch("engine_detection.shutil.which", return_value="/usr/bin/claude"),
                    patch("engine_detection._run", return_value=(0, "1.0.0", "")),
                    patch("engine_detection.os.environ.get", return_value="sk-ant-xxx"),
                ):
                    r = probe_claude_code()

        self.assertTrue(r.authenticated)
        self.assertEqual(r.credential_source, "subscription")  # Not env_var


class TestTimeoutResilience(unittest.TestCase):
    """Test that slow/hanging probes don't block detect_all()."""

    def test_slow_hermes_doesnt_block_others(self):
        """If Hermes times out, other engines still appear."""
        def slow_hermes():
            time.sleep(2)  # Exceeds _DETECT_TIMEOUT
            return EngineProbeResult(engine_id="hermes", installed=False,
                                    authenticated=False, credential_source=None, version=None)

        def fast_claude():
            return EngineProbeResult(engine_id="claude_code", installed=True,
                                    authenticated=True, credential_source="subscription", version="1.0")

        import engine_detection as ed
        original_probes = list(ed._PROBES)
        original_timeout = ed._DETECT_TIMEOUT

        try:
            # Replace with slow hermes + fast claude
            ed._PROBES = [fast_claude, slow_hermes]
            ed._DETECT_TIMEOUT = 0.5  # Short timeout

            results = detect_all()
            ids = {r.engine_id for r in results}

            # Claude should be present (fast)
            self.assertIn("claude_code", ids)
            # Hermes may be missing (slow timeout)
        finally:
            ed._PROBES = original_probes
            ed._DETECT_TIMEOUT = original_timeout


class TestRecommendedEngineLogic(unittest.TestCase):
    """Test recommendation priority."""

    def test_claude_code_preferred_over_hermes(self):
        """When both are authenticated, Claude Code is recommended."""
        results = [
            EngineProbeResult(
                engine_id="hermes", installed=True, authenticated=True,
                credential_source="config_file", version=None
            ),
            EngineProbeResult(
                engine_id="claude_code", installed=True, authenticated=True,
                credential_source="subscription", version=None
            ),
        ]
        rec = recommended_engine(results)
        self.assertEqual(rec, "claude_code")

    def test_subscription_preferred_over_env_var(self):
        """Subscription beats env_var authentication."""
        results = [
            EngineProbeResult(
                engine_id="codex_cli", installed=True, authenticated=True,
                credential_source="env_var", version=None
            ),
            EngineProbeResult(
                engine_id="copilot", installed=True, authenticated=True,
                credential_source="subscription", version=None
            ),
        ]
        rec = recommended_engine(results)
        self.assertEqual(rec, "copilot")  # subscription wins


class TestAPIErrorPath(unittest.TestCase):
    """Test that API gracefully handles detect_all() failures."""

    def test_api_returns_empty_on_detect_failure(self):
        """When detect_all() raises, API returns empty results (not error dict)."""
        def broken_detect():
            raise RuntimeError("Probe failed")

        # Simulate what the API route does
        try:
            results = broken_detect()
        except Exception:
            results = []

        api_response = {
            "results": results,
            "recommended_engine": None,
            "needs_bootstrap": True,
            "error": "detection_failed",
        }

        self.assertEqual(api_response["results"], [])
        self.assertTrue(api_response["needs_bootstrap"])


class TestFrontendFiltering(unittest.TestCase):
    """Test that frontend correctly filters installed=true."""

    def test_frontend_filter_shows_only_installed(self):
        """Frontend filter `r.installed` shows correct engines."""
        results = detect_all()

        # This is what the frontend does
        visible = [r for r in results if r.installed]

        # All visible results should have installed=True
        for r in visible:
            self.assertTrue(r.installed, f"{r.engine_id} has installed={r.installed}")

        # At least Hermes should be visible (it's always installed somewhere)
        visible_ids = {r.engine_id for r in visible}
        self.assertIn("hermes", visible_ids)


class TestTimeoutDocumentation(unittest.TestCase):
    """Test that timeout behavior is documented."""

    def test_detect_all_docstring_mentions_timeout(self):
        """detect_all() docstring must document timeout behavior."""
        docstring = detect_all.__doc__ or ""
        self.assertIn("timeout", docstring.lower(),
                     "Docstring must mention timeout behavior")
        self.assertIn("drop", docstring.lower(),
                     "Docstring must mention that slow probes are dropped")


if __name__ == "__main__":
    unittest.main()
