"""ADR-0159 — Platform Independence tests.

Tests for:
  M1 — Auto-detect engine selector (adapter.py)
  M2 — SandboxProvider abstraction
  M3 — TimerProvider abstraction
"""
from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

# Ensure forge is on path
_REPO = Path(__file__).resolve().parents[3]
_FORGE = str(_REPO / "operator" / "forge")
_SHARED = str(_REPO / "operator" / "bridges" / "shared")
for _p in (_FORGE, _SHARED):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── M2: SandboxProvider ──────────────────────────────────────────────────────

class TestSandboxProvider(unittest.TestCase):
    def setUp(self):
        # Reset cached tier between tests
        import forge.sandbox_provider as sp
        sp._DETECTED_TIER = None

    def tearDown(self):
        import forge.sandbox_provider as sp
        sp._DETECTED_TIER = None
        os.environ.pop("CORVIN_SANDBOX", None)

    def test_env_override_bwrap(self):
        import forge.sandbox_provider as sp
        os.environ["CORVIN_SANDBOX"] = "bwrap"
        tier = sp.detect_sandbox_tier()
        self.assertEqual(tier, sp.SandboxTier.BWRAP)

    def test_env_override_docker(self):
        import forge.sandbox_provider as sp
        os.environ["CORVIN_SANDBOX"] = "docker"
        tier = sp.detect_sandbox_tier()
        self.assertEqual(tier, sp.SandboxTier.DOCKER)

    def test_env_override_none(self):
        import forge.sandbox_provider as sp
        os.environ["CORVIN_SANDBOX"] = "none"
        tier = sp.detect_sandbox_tier()
        self.assertEqual(tier, sp.SandboxTier.NONE)

    def test_bwrap_detected_when_on_path(self):
        import forge.sandbox_provider as sp
        with patch("forge.sandbox_provider._have_bwrap", return_value=True):
            tier = sp.detect_sandbox_tier()
        self.assertEqual(tier, sp.SandboxTier.BWRAP)

    def test_docker_fallback_when_no_bwrap(self):
        import forge.sandbox_provider as sp
        with patch("forge.sandbox_provider._have_bwrap", return_value=False), \
             patch("forge.sandbox_provider._have_docker", return_value=True):
            tier = sp.detect_sandbox_tier()
        self.assertEqual(tier, sp.SandboxTier.DOCKER)

    def test_none_fallback_when_nothing_available(self):
        import forge.sandbox_provider as sp
        with patch("forge.sandbox_provider._have_bwrap", return_value=False), \
             patch("forge.sandbox_provider._have_docker", return_value=False):
            tier = sp.detect_sandbox_tier()
        self.assertEqual(tier, sp.SandboxTier.NONE)

    def test_bwrap_capabilities(self):
        import forge.sandbox_provider as sp
        caps = sp.SandboxCapabilities.for_tier(sp.SandboxTier.BWRAP)
        self.assertTrue(caps.has_network_isolation)
        self.assertTrue(caps.has_fs_namespacing)
        self.assertTrue(caps.has_process_isolation)

    def test_none_capabilities(self):
        import forge.sandbox_provider as sp
        caps = sp.SandboxCapabilities.for_tier(sp.SandboxTier.NONE)
        self.assertFalse(caps.has_network_isolation)
        self.assertFalse(caps.has_fs_namespacing)
        self.assertFalse(caps.has_process_isolation)

    def test_tier_cached(self):
        import forge.sandbox_provider as sp
        call_count = [0]
        orig = sp._have_bwrap
        def counting_bwrap():
            call_count[0] += 1
            return True
        with patch("forge.sandbox_provider._have_bwrap", side_effect=counting_bwrap):
            sp.detect_sandbox_tier()
            sp.detect_sandbox_tier()  # second call should use cache
        # The first call invokes _have_bwrap, the second uses cache
        self.assertLessEqual(call_count[0], 1)

    def test_force_redetect(self):
        import forge.sandbox_provider as sp
        with patch("forge.sandbox_provider._have_bwrap", return_value=False), \
             patch("forge.sandbox_provider._have_docker", return_value=False):
            t1 = sp.detect_sandbox_tier()
        self.assertEqual(t1, sp.SandboxTier.NONE)
        # Now bwrap becomes available — force re-detect
        with patch("forge.sandbox_provider._have_bwrap", return_value=True):
            t2 = sp.detect_sandbox_tier(force=True)
        self.assertEqual(t2, sp.SandboxTier.BWRAP)

    def test_is_sandbox_available_true(self):
        import forge.sandbox_provider as sp
        with patch("forge.sandbox_provider._have_bwrap", return_value=True):
            self.assertTrue(sp.is_sandbox_available())

    def test_is_sandbox_available_false(self):
        import forge.sandbox_provider as sp
        with patch("forge.sandbox_provider._have_bwrap", return_value=False), \
             patch("forge.sandbox_provider._have_docker", return_value=False):
            self.assertFalse(sp.is_sandbox_available())


# ── M3: TimerProvider ────────────────────────────────────────────────────────

class TestTimerProvider(unittest.TestCase):
    def setUp(self):
        import timer_provider as tp
        tp._PROVIDER_INSTANCE = None
        os.environ.pop("CORVIN_TIMER_PROVIDER", None)

    def tearDown(self):
        import timer_provider as tp
        tp._PROVIDER_INSTANCE = None
        os.environ.pop("CORVIN_TIMER_PROVIDER", None)

    def test_get_thread_provider_via_env(self):
        import timer_provider as tp
        os.environ["CORVIN_TIMER_PROVIDER"] = "thread"
        provider = tp.get_timer_provider()
        self.assertIsInstance(provider, tp.ThreadTimerProvider)

    def test_get_systemd_provider_via_env(self):
        import timer_provider as tp
        os.environ["CORVIN_TIMER_PROVIDER"] = "systemd"
        provider = tp.get_timer_provider()
        self.assertIsInstance(provider, tp.SystemdTimerProvider)

    def test_force_thread_provider(self):
        import timer_provider as tp
        provider = tp.get_timer_provider(force_thread=True)
        self.assertIsInstance(provider, tp.ThreadTimerProvider)

    def test_thread_schedule_interval_fires(self):
        import timer_provider as tp
        provider = tp.ThreadTimerProvider()
        fired = threading.Event()
        provider.schedule_interval("test-interval", 0.05, lambda: fired.set())
        ok = fired.wait(timeout=1.0)
        provider.cancel_all()
        self.assertTrue(ok, "interval job did not fire within 1s")

    def test_thread_cancel_stops_firing(self):
        import timer_provider as tp
        provider = tp.ThreadTimerProvider()
        fire_count = [0]
        fired_at_least_once = threading.Event()
        def _inc():
            fire_count[0] += 1
            fired_at_least_once.set()
        provider.schedule_interval("test-cancel", 0.05, _inc)
        # Wait until at least one fire is confirmed (avoids timing-dependent sleep)
        fired_at_least_once.wait(timeout=1.0)
        provider.cancel("test-cancel")
        count_at_cancel = fire_count[0]
        time.sleep(0.20)  # well past one interval period
        self.assertEqual(fire_count[0], count_at_cancel,
                         "timer fired after cancel — cancel/re-arm race not fixed")

    def test_thread_interval_zero_raises(self):
        import timer_provider as tp
        provider = tp.ThreadTimerProvider()
        with self.assertRaises(ValueError):
            provider.schedule_interval("zero-sec", 0, lambda: None)

    def test_thread_schedule_daily_fires(self):
        import timer_provider as tp
        provider = tp.ThreadTimerProvider()
        fired = threading.Event()
        # Patch _seconds_until to fire very soon (0.08s)
        with patch("timer_provider._seconds_until", return_value=0.08):
            provider.schedule_daily("test-daily", 3, 30, lambda: fired.set())
        ok = fired.wait(timeout=1.5)
        provider.cancel_all()
        self.assertTrue(ok, "daily job did not fire within 1.5s")

    def test_thread_cancel_clears_cancelled_set(self):
        import timer_provider as tp
        provider = tp.ThreadTimerProvider()
        provider.schedule_interval("j1", 60, lambda: None)
        provider.cancel("j1")
        self.assertIn("j1", provider._cancelled)
        # Re-schedule same job — _cancelled must be cleared so it can fire
        fired = threading.Event()
        provider.schedule_interval("j1", 0.05, lambda: fired.set())
        ok = fired.wait(timeout=1.0)
        provider.cancel("j1")
        self.assertTrue(ok, "re-scheduled job did not fire after cancel+re-register")

    def test_thread_cancel_nonexistent_job(self):
        import timer_provider as tp
        provider = tp.ThreadTimerProvider()
        # Should not raise
        provider.cancel("nonexistent-job")

    def test_seconds_until_future(self):
        import timer_provider as tp
        # Give it a time 1 hour from now: should return ~3600s (roughly)
        from datetime import datetime, timedelta
        future = datetime.now() + timedelta(hours=1)
        delta = tp._seconds_until(future.hour, future.minute)
        # Should be roughly 1 hour ±2 min
        self.assertGreater(delta, 3400)
        self.assertLess(delta, 3700)

    def test_provider_cached(self):
        import timer_provider as tp
        os.environ["CORVIN_TIMER_PROVIDER"] = "thread"
        p1 = tp.get_timer_provider()
        p2 = tp.get_timer_provider()
        self.assertIs(p1, p2)

    def test_have_systemd_false_on_non_linux(self):
        import timer_provider as tp
        with patch.object(tp.sys, "platform", "darwin"):
            self.assertFalse(tp._have_systemd())

    def test_no_anthropic_import(self):
        """Enforcement: timer_provider must not import anthropic (CI AST lint rule)."""
        import ast
        src = Path(_SHARED) / "timer_provider.py"
        tree = ast.parse(src.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                for name in names:
                    self.assertFalse(
                        (name or "").startswith("anthropic"),
                        f"timer_provider.py must not import anthropic (found: {name!r})",
                    )


# ── M2: sandbox_provider no anthropic import ─────────────────────────────────

class TestSandboxSelfTest(unittest.TestCase):
    """Verify _check_sandbox_provider() severity levels."""

    def setUp(self):
        import forge.sandbox_provider as sp
        sp._DETECTED_TIER = None
        os.environ.pop("CORVIN_SANDBOX", None)

    def tearDown(self):
        import forge.sandbox_provider as sp
        sp._DETECTED_TIER = None
        os.environ.pop("CORVIN_SANDBOX", None)

    def test_none_tier_emits_warning(self):
        os.environ["CORVIN_SANDBOX"] = "none"
        import forge.sandbox_provider as sp
        sp._DETECTED_TIER = None
        from self_test import _check_sandbox_provider, WARNING
        results = _check_sandbox_provider()
        hits = [r for r in results if r.name == "forge.sandbox_tier" and not r.ok]
        self.assertTrue(hits, "expected a failed forge.sandbox_tier check")
        self.assertEqual(hits[0].severity, WARNING,
                         "none-tier without require_sandbox must be WARNING")

    def test_none_tier_with_require_sandbox_emits_critical(self):
        os.environ["CORVIN_SANDBOX"] = "none"
        import forge.sandbox_provider as sp
        sp._DETECTED_TIER = None
        from self_test import _check_sandbox_provider, CRITICAL, _load_tenant_config_for_self_test
        # Inject a config that has require_sandbox=true
        fake_cfg = {"spec": {"forge": {"require_sandbox": True}}}
        with patch("self_test._load_tenant_config_for_self_test", return_value=fake_cfg):
            results = _check_sandbox_provider()
        hits = [r for r in results if r.name == "forge.sandbox_tier" and not r.ok]
        self.assertTrue(hits, "expected a failed forge.sandbox_tier check")
        self.assertEqual(hits[0].severity, CRITICAL,
                         "require_sandbox=true must escalate to CRITICAL")

    def test_bwrap_tier_emits_info(self):
        import forge.sandbox_provider as sp
        sp._DETECTED_TIER = None
        from self_test import _check_sandbox_provider, INFO
        with patch("forge.sandbox_provider._have_bwrap", return_value=True):
            sp._DETECTED_TIER = None
            results = _check_sandbox_provider()
        hits = [r for r in results if r.name == "forge.sandbox_tier"]
        self.assertTrue(hits)
        self.assertEqual(hits[0].severity, INFO)
        self.assertTrue(hits[0].ok)


class TestSandboxProviderNoAnthropicImport(unittest.TestCase):
    def test_no_anthropic_import(self):
        import ast
        src = Path(_FORGE) / "forge" / "sandbox_provider.py"
        tree = ast.parse(src.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                for name in names:
                    self.assertFalse(
                        (name or "").startswith("anthropic"),
                        f"sandbox_provider.py must not import anthropic (found: {name!r})",
                    )


# ── M1: Auto-detect engine in adapter ────────────────────────────────────────

class TestM1AutoDetectEngine(unittest.TestCase):
    """Smoke tests for the adapter's ADR-0159 M1 auto-detect block.

    We cannot import adapter.py fully in unit tests (too many deps), so we
    test the logic by extracting the relevant block as a standalone helper.
    """

    def _auto_detect(self, claude_on_path: bool,
                     env_engine: str = "") -> str | None:
        """Mirror of the ADR-0159 M1 auto-detect block in adapter.py."""
        import shutil
        profile: dict = {}
        _env_engine = env_engine.strip()
        if _env_engine:
            profile["default_engine"] = _env_engine
        else:
            if shutil.which("claude") is None:
                profile["default_engine"] = "hermes"
        return profile.get("default_engine")

    def test_hermes_when_claude_absent(self):
        with patch("shutil.which", return_value=None):
            result = self._auto_detect(claude_on_path=False)
        self.assertEqual(result, "hermes")

    def test_none_when_claude_present(self):
        # When claude is on PATH, auto-detect leaves engine unset
        # (ClaudeCode fallback at bottom of adapter).
        with patch("shutil.which", return_value="/usr/bin/claude"):
            result = self._auto_detect(claude_on_path=True)
        self.assertIsNone(result)

    def test_env_override_wins(self):
        with patch("shutil.which", return_value=None):
            result = self._auto_detect(claude_on_path=False,
                                       env_engine="opencode")
        self.assertEqual(result, "opencode")

    def test_env_override_prevents_hermes(self):
        with patch("shutil.which", return_value=None):
            result = self._auto_detect(claude_on_path=False,
                                       env_engine="claude_code")
        self.assertEqual(result, "claude_code")


if __name__ == "__main__":
    unittest.main()
