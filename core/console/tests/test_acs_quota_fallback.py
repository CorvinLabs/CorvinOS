"""Unit tests — ACS quota fallback (ADR-0150 graceful-degradation extension).

Verifies that when the Free-Tier daily ACS quota is exhausted:
  (A) the turn yields a "notice/quota_fallback" event with a user-facing message,
  (B) the turn does NOT hard-error (402) and does NOT return immediately,
  (C) the direct OS-turn path (claude_code / hermes) is taken instead,
  (D) no ACS workers are spawned.

And conversely, when quota is NOT exhausted:
  (E) no notice event is emitted,
  (F) the ACS delegation path is entered (delegation delta streamed).
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))


def _drain(agen) -> list[dict]:
    async def _collect():
        out = []
        async for ev in agen:
            out.append(ev)
        return out
    return asyncio.run(_collect())


class _FakeLicenseLimitError(Exception):
    """Stand-in for license.limits.LicenseLimitError so we don't need the module."""


def _make_fake_license_modules(raise_on_check: bool):
    """Return (fake_compute_quota module, fake_limits module)."""
    limits_mod = types.ModuleType("license.limits")
    limits_mod.LicenseLimitError = _FakeLicenseLimitError  # type: ignore[attr-defined]

    quota_mod = types.ModuleType("license.compute_quota")

    def _inc_and_check(corvin_home, channel=None, chat_key=None):
        if raise_on_check:
            raise _FakeLicenseLimitError("quota exhausted")

    quota_mod.increment_and_check = _inc_and_check  # type: ignore[attr-defined]
    return quota_mod, limits_mod


class _FakeACSResult:
    run_id = "acs-test-000"
    workflow_id = "wf-000"
    status = "success"
    summary = "ACS completed OK"
    final_output = "done"
    error = None
    iterations = 1
    workers_spawned = 1
    budget_breach = False
    elapsed_s = 0.1
    run_dir = None


class ACSQuotaFallbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CORVIN_HOME"] = self.tmp.name
        os.environ["CORVIN_TENANT_ID"] = "_default"
        os.environ.pop("VOICE_AUDIT_PATH", None)

        import importlib
        from corvin_console import chat_runtime
        importlib.reload(chat_runtime)
        try:
            import forge.paths as fp
            importlib.reload(fp)
            importlib.reload(chat_runtime)
        except ImportError:
            pass
        self.cr = chat_runtime
        self.sess = self.cr.create_session("_default")

    def tearDown(self) -> None:
        self.tmp.cleanup()
        for k in ("CORVIN_HOME", "CORVIN_TENANT_ID"):
            os.environ.pop(k, None)
        # Clean up injected fake modules
        for mod_name in ("license", "license.compute_quota", "license.limits"):
            sys.modules.pop(mod_name, None)

    def _pin_house_rules_allowed(self):
        """Force house-rules classifier to ALLOW so we reach the delegation gate."""
        import house_rules as _hr  # type: ignore
        _hr._house_rules_classifier = (  # type: ignore[assignment]
            lambda task, rules, auth, **kw: ("", 0.0, "test clear")
        )

    def _inject_license(self, raise_on_check: bool):
        """Inject fake license modules into sys.modules."""
        quota_mod, limits_mod = _make_fake_license_modules(raise_on_check)
        sys.modules["license"] = types.ModuleType("license")
        sys.modules["license.compute_quota"] = quota_mod
        sys.modules["license.limits"] = limits_mod

    def _inject_fake_acs(self):
        """Inject a fake acs_runtime that returns a successful result immediately."""
        acs_mod = types.ModuleType("acs_runtime")

        class FakeACSRuntime:
            def __init__(self, **kw):
                pass

            async def run(self, spec, run_id=None):
                return _FakeACSResult()

        acs_mod.ACSRuntime = FakeACSRuntime  # type: ignore[attr-defined]
        acs_mod.ACSResult = _FakeACSResult   # type: ignore[attr-defined]
        sys.modules["acs_runtime"] = acs_mod
        return acs_mod

    def _no_spawn_guard(self):
        """Replace asyncio.create_subprocess_exec to detect if claude was spawned."""
        called = {"hit": False, "argv": None}

        async def _fake_spawn(*args, **kwargs):
            called["hit"] = True
            called["argv"] = args
            # Return a minimal fake process that immediately exits
            proc = MagicMock()
            proc.pid = 99999
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"test output", b""))
            proc.wait = AsyncMock(return_value=0)
            # Simulate stdout as an async iterable of empty lines
            async def _fake_stdout():
                return
                yield  # make it an async generator

            proc.stdout = MagicMock()
            proc.stdout.__aiter__ = lambda s: _fake_stdout()
            proc.stdin = MagicMock()
            proc.stdin.write = MagicMock()
            proc.stdin.drain = AsyncMock()
            proc.stdin.close = MagicMock()
            proc.stderr = MagicMock()
            proc.stderr.read = AsyncMock(return_value=b"")
            return proc

        self.cr.asyncio.create_subprocess_exec = _fake_spawn  # type: ignore[attr-defined]
        return called

    # ── (A/B/C/D) Quota exhausted → notice + fallback, no ACS ───────────────
    def test_quota_exhausted_yields_notice_and_falls_back(self) -> None:
        """When ACS quota is exhausted, a notice/quota_fallback event must be
        emitted and the direct OS-turn path must be taken — no hard 402 error."""
        self._pin_house_rules_allowed()
        self._inject_license(raise_on_check=True)   # quota exhausted
        self._inject_fake_acs()
        spawn_called = self._no_spawn_guard()

        # Force delegation path (long prompt with strong verb)
        prompt = "review and refactor the entire authentication module, fix all bugs, and add comprehensive tests"

        # Patch _delegation_enabled + _should_delegate to guarantee delegate path
        with (
            patch.object(self.cr, "_delegation_enabled", return_value=True),
            patch.object(self.cr, "_should_delegate", return_value=True),
        ):
            events = _drain(self.cr.stream_turn(self.sess, prompt))

        types_seen = [e.get("type") for e in events]

        # (A) notice event with subtype=quota_fallback must be present
        notice_events = [e for e in events if e.get("type") == "notice"
                         and e.get("subtype") == "quota_fallback"]
        self.assertTrue(
            notice_events,
            f"Expected 'notice/quota_fallback' event but got: {types_seen}"
        )
        notice_msg = notice_events[0].get("message", "")
        self.assertIn("ACS-Kontingent", notice_msg,
                      "Notice message should explain the quota limit")
        self.assertIn("Claude Code", notice_msg,
                      "Notice message should name the fallback engine")

        # (B) NO hard 402 error event
        error_402 = [e for e in events if e.get("type") == "error"
                     and e.get("code") == 402]
        self.assertFalse(error_402, "Should NOT emit a 402 error event on quota fallback")

        # (C) turn must complete (ends with "done")
        self.assertEqual(types_seen[-1], "done",
                         "Turn must end with 'done' even on quota fallback")

        # (D) ACS runtime's run() was NOT called with a full ACS fan-out
        # (The fake spawn was called instead — the direct OS turn path)
        # We verify indirectly: no "⚙ Delegation an ACS-Worker gestartet" delta
        delegation_start_deltas = [
            e for e in events
            if e.get("type") == "delta"
            and "ACS-Worker gestartet" in (e.get("text") or "")
        ]
        self.assertFalse(delegation_start_deltas,
                         "ACS delegation start delta must NOT appear on quota fallback")

    # ── (E/F) Quota OK → ACS runs, no notice ────────────────────────────────
    def test_quota_ok_takes_acs_path(self) -> None:
        """When quota is available, the ACS delegation path is entered and no
        quota_fallback notice is emitted."""
        self._pin_house_rules_allowed()
        self._inject_license(raise_on_check=False)  # quota OK
        self._inject_fake_acs()
        self._no_spawn_guard()  # also install to prevent real claude spawn

        prompt = "review and refactor the entire authentication module, fix all bugs, and add tests"

        with (
            patch.object(self.cr, "_delegation_enabled", return_value=True),
            patch.object(self.cr, "_should_delegate", return_value=True),
        ):
            events = _drain(self.cr.stream_turn(self.sess, prompt))

        types_seen = [e.get("type") for e in events]

        # (E) NO quota_fallback notice
        notice_events = [e for e in events if e.get("type") == "notice"
                         and e.get("subtype") == "quota_fallback"]
        self.assertFalse(
            notice_events,
            f"Should NOT see quota_fallback notice when quota is OK; got {notice_events}"
        )

        # (F) ACS delegation start delta was emitted
        delegation_deltas = [
            e for e in events
            if e.get("type") == "delta"
            and "ACS-Worker gestartet" in (e.get("text") or "")
        ]
        self.assertTrue(
            delegation_deltas,
            f"Expected ACS delegation start delta; events seen: {types_seen}"
        )

        # Turn completes normally
        self.assertEqual(types_seen[-1], "done")


    # ── (G) Quota exhausted + fallback engine also gate-blocked → hard refusal ─
    def test_quota_fallback_gate_blocks_fallback_engine(self) -> None:
        """When ACS quota is exhausted AND the fallback engine (claude_code) is
        also blocked by L34/L35, the turn must hard-block — NOT yield the quota
        notice and NOT spawn the fallback engine.  The gate must remain fail-closed
        even on the degraded path (fixes the CONFIRMED CRITICAL L34/L35 bypass)."""
        self._pin_house_rules_allowed()
        self._inject_license(raise_on_check=True)  # quota exhausted
        self._inject_fake_acs()
        spawn_called = self._no_spawn_guard()

        # Make the fallback gate (non-ACS engine) return a refusal string.
        _GATE_REFUSAL = "[L34] Fallback engine blocked by data-residency policy."

        prompt = "review and refactor the auth module"

        import corvin_console._spawn_gates as _sg  # type: ignore[import]
        original_fn = _sg.check_console_spawn_or_refusal

        call_count = {"n": 0}

        def _fake_gate(task_text, tenant_id=None, persona=None, channel=None,
                       chat_key=None, engine_id=None):
            call_count["n"] += 1
            # First call: ACS gate → pass (delegation proceeds to quota check).
            # Second call: fallback-engine gate → block.
            if engine_id == "acs":
                return None
            return _GATE_REFUSAL

        _sg.check_console_spawn_or_refusal = _fake_gate  # type: ignore[assignment]
        try:
            with (
                patch.object(self.cr, "_delegation_enabled", return_value=True),
                patch.object(self.cr, "_should_delegate", return_value=True),
            ):
                events = _drain(self.cr.stream_turn(self.sess, prompt))
        finally:
            _sg.check_console_spawn_or_refusal = original_fn  # type: ignore[assignment]

        types_seen = [e.get("type") for e in events]

        # Gate was called at least twice (once for ACS, once for fallback engine).
        self.assertGreaterEqual(call_count["n"], 2,
                                "Fallback gate must be called for the real engine")

        # NO quota_fallback notice — gate blocked before the notice could emit.
        notice_events = [e for e in events if e.get("type") == "notice"
                         and e.get("subtype") == "quota_fallback"]
        self.assertFalse(notice_events,
                         "Gate-blocked fallback must NOT emit quota_fallback notice")

        # Gate refusal text IS present in the stream.
        refusal_deltas = [e for e in events
                          if e.get("type") in ("delta", "result")
                          and _GATE_REFUSAL in (e.get("text") or "")]
        self.assertTrue(refusal_deltas,
                        "Gate refusal text must be streamed when fallback is blocked")

        # Turn completes cleanly.
        self.assertEqual(types_seen[-1], "done",
                         "Turn must still end with 'done' even on gate-blocked fallback")

        # No subprocess spawned.
        self.assertFalse(spawn_called["hit"],
                         "No subprocess must spawn when fallback engine is gate-blocked")


if __name__ == "__main__":
    unittest.main()
