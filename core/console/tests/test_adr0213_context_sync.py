"""Unit tests — ADR-0213 ACS delegation result → OS engine transcript sync.

ADR-0114's delegation branch never invokes `claude -p` for the OS role, so
the CLI's own on-disk transcript never advanced even though the branch
still called ``touch(sess, increment_turn=True)`` — the next turn's
``resume = turn_count > 0`` check then appended ``--continue`` onto a
transcript that never saw the delegation. ADR-0213 fixes this with a
tool-less ``claude -p [--continue] --max-turns 1 --disallowedTools "*"``
sync call (``_sync_acs_result_to_transcript``) plus a C1 fallback: only
advance ``turn_count`` when that sync call provably succeeded.

These tests verify the wiring around ``_sync_acs_result_to_transcript``
(patched — no real subprocess) end-to-end through ``stream_turn``:
  (A) sync succeeds  → turn_count advances, os_turn.context_sync(synced=True)
  (B) sync fails     → turn_count does NOT advance (C1 fallback),
                        os_turn.context_sync(synced=False)
  (C) sync raises    → same as (B), exception never escapes the turn
  (D) _build_args(purpose="context_sync") shape is minimal + tool-less

A REAL end-to-end run (actual ACSRuntime + actual `claude -p` subprocesses)
lives in test_adr0213_context_sync_live.py, gated behind CLAUDE_LIVE_E2E=1.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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


class _FakeACSResult:
    run_id = "acs-test-000"
    workflow_id = "wf-000"
    status = "success"
    summary = "ACS completed OK"
    # Matches the REAL ACSResult dataclass shape (operator/bridges/shared/
    # acs_runtime.py): final_output is a dict, not a string. A test double
    # using a string here previously masked a real AttributeError crash in
    # _compress_acs_result_for_context (see the dedicated regression tests
    # below for the empty-summary case that actually exercises this field).
    final_output = {"done": True}
    error = None
    iterations = 1
    workers_spawned = 1
    budget_breach = False
    elapsed_s = 0.1
    run_dir = None


class ADR0213ContextSyncTest(unittest.TestCase):
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
        for mod_name in ("license", "license.compute_quota", "license.limits", "acs_runtime"):
            sys.modules.pop(mod_name, None)

    def _pin_house_rules_allowed(self):
        import house_rules as _hr  # type: ignore
        _hr._house_rules_classifier = (  # type: ignore[assignment]
            lambda task, rules, auth, **kw: ("", 0.0, "test clear")
        )

    def _inject_license_ok(self):
        limits_mod = types.ModuleType("license.limits")

        class _FakeLicenseLimitError(Exception):
            pass

        limits_mod.LicenseLimitError = _FakeLicenseLimitError  # type: ignore[attr-defined]
        quota_mod = types.ModuleType("license.compute_quota")
        quota_mod.increment_and_check = lambda *a, **kw: None  # type: ignore[attr-defined]
        sys.modules["license"] = types.ModuleType("license")
        sys.modules["license.compute_quota"] = quota_mod
        sys.modules["license.limits"] = limits_mod

    def _inject_fake_acs(self):
        acs_mod = types.ModuleType("acs_runtime")

        class FakeACSRuntime:
            def __init__(self, **kw):
                pass

            async def run(self, spec, run_id=None):
                return _FakeACSResult()

        acs_mod.ACSRuntime = FakeACSRuntime  # type: ignore[attr-defined]
        acs_mod.ACSResult = _FakeACSResult   # type: ignore[attr-defined]
        sys.modules["acs_runtime"] = acs_mod

    def _run_delegated_turn(self, context_sync: bool = True):
        """Drive one delegated turn.

        ``context_sync`` selects the state of the `acs_context_sync` feature
        flag — the ADR-0213 replay ships dark, so these tests must say which
        state they exercise (CLAUDE.md § Feature Flags: both states get tests).
        """
        self._pin_house_rules_allowed()
        self._inject_license_ok()
        self._inject_fake_acs()
        prompt = ("review and refactor the entire authentication module, "
                  "fix all bugs, and add comprehensive tests")

        def _flag(flag_id, tenant_id="_default"):
            return context_sync if flag_id == "acs_context_sync" else False

        with (
            patch.object(self.cr, "_delegation_enabled", return_value=True),
            patch.object(self.cr, "_should_delegate", return_value=True),
            # ACS worker engine selected: this file is about the ACS branch's
            # transcript sync, not about which engine a stock install picks
            # (the default is `native`, which never enters this branch).
            patch.object(self.cr._feature_flags, "worker_engine_mode",
                         return_value="acs"),
            patch.object(self.cr._feature_flags, "is_enabled", side_effect=_flag),
        ):
            return _drain(self.cr.stream_turn(self.sess, prompt))

    # ── (A) sync succeeds → turn_count advances ─────────────────────────
    def test_sync_success_advances_turn_count_and_audits(self) -> None:
        fake_audit = MagicMock()
        fake_audit.audit_event = MagicMock()
        with (
            patch.object(self.cr, "_sync_acs_result_to_transcript", return_value=True),
            patch.object(self.cr, "_bridge_audit", fake_audit),
        ):
            events = self._run_delegated_turn()

        self.assertEqual(events[-1].get("type"), "done")
        self.assertEqual(self.sess.turn_count, 1,
                         "turn_count must advance once the transcript sync succeeded")

        sync_calls = [c for c in fake_audit.audit_event.call_args_list
                      if c.args and c.args[0] == "os_turn.context_sync"]
        self.assertTrue(sync_calls, "os_turn.context_sync audit event must be emitted")
        details = sync_calls[0].kwargs.get("details", {})
        # run_id is minted fresh per delegated turn (chat_runtime's own
        # "acs-web-<ts>-<hex>" id), not the fake result's run_id — just
        # assert it was threaded through, not a specific literal.
        self.assertTrue(str(details.get("delegated_run_id", "")).startswith("acs-web-"))
        self.assertTrue(details.get("synced"))

    # ── (B) sync fails → C1 fallback: turn_count does NOT advance ───────
    def test_sync_failure_applies_c1_fallback(self) -> None:
        fake_audit = MagicMock()
        fake_audit.audit_event = MagicMock()
        with (
            patch.object(self.cr, "_sync_acs_result_to_transcript", return_value=False),
            patch.object(self.cr, "_bridge_audit", fake_audit),
        ):
            events = self._run_delegated_turn()

        self.assertEqual(events[-1].get("type"), "done",
                         "turn must still complete cleanly even when the sync call failed")
        self.assertEqual(self.sess.turn_count, 0,
                         "turn_count must NOT advance when the transcript sync failed "
                         "(C1 fallback) — otherwise the next --continue turn would "
                         "resume a transcript that never recorded this delegation")

        sync_calls = [c for c in fake_audit.audit_event.call_args_list
                      if c.args and c.args[0] == "os_turn.context_sync"]
        self.assertTrue(sync_calls)
        self.assertFalse(sync_calls[0].kwargs.get("details", {}).get("synced"))

        # The chat history still shows the result — only turn_count is held back.
        turns_path = self.cr._turns_path(self.sess.tenant_id, self.sess.sid)
        self.assertTrue(turns_path.exists())
        text = turns_path.read_text(encoding="utf-8")
        self.assertIn("ACS completed OK", text)

    # ── (C) sync raises → swallowed, same fallback as (B) ───────────────
    def test_sync_exception_is_swallowed_and_falls_back(self) -> None:
        with (
            patch.object(self.cr, "_sync_acs_result_to_transcript",
                        side_effect=RuntimeError("boom")),
        ):
            events = self._run_delegated_turn()

        self.assertEqual(events[-1].get("type"), "done",
                         "an exception inside the sync helper must never escape the turn")
        self.assertEqual(self.sess.turn_count, 0)

    # ── (D) _build_args(purpose="context_sync") is minimal + tool-less ──
    def test_build_args_context_sync_shape(self) -> None:
        args = self.cr._build_args(self.sess, resume=True, model="claude-sonnet-5",
                                   task_text="dummy task", purpose="context_sync")
        self.assertIn("--disallowedTools", args)
        self.assertEqual(args[args.index("--disallowedTools") + 1], "*")
        self.assertIn("--max-turns", args)
        self.assertEqual(args[args.index("--max-turns") + 1], "1")
        self.assertIn("--continue", args)
        self.assertIn("--model", args)
        self.assertNotIn("--mcp-config", args)
        self.assertNotIn("--add-dir", args)
        self.assertNotIn("--allowedTools", args)
        self.assertNotIn("--dangerously-skip-permissions", args)

    # ── (E) compression must not crash on structured-only results ───────
    # Adversarial review finding: ACSResult.final_output is a dict, not a
    # string like summary/error — a naive `or` chain across all three picks
    # the dict the moment summary is empty and hands it straight to
    # .strip(), which raises AttributeError on any successful,
    # structured-output-only run (the common shape for file-writing tasks).
    def test_compress_handles_empty_summary_with_dict_final_output(self) -> None:
        class _StructuredResult:
            status = "success"
            summary = ""
            final_output = {"file": "lighthouse.txt", "lines": 3}
            error = ""

        note = self.cr._compress_acs_result_for_context(
            _StructuredResult(), "write a haiku", "acs-test-001")
        self.assertIn("lighthouse.txt", note)
        self.assertNotIn("(no result text)", note)

    def test_compress_falls_back_to_error_when_summary_and_output_empty(self) -> None:
        class _ErrorResult:
            status = "failed"
            summary = ""
            final_output = {}
            error = "worker crashed"

        note = self.cr._compress_acs_result_for_context(
            _ErrorResult(), "write a haiku", "acs-test-002")
        self.assertIn("worker crashed", note)

    def test_compress_handles_all_empty(self) -> None:
        class _EmptyResult:
            status = "failed"
            summary = ""
            final_output = {}
            error = ""

        note = self.cr._compress_acs_result_for_context(
            _EmptyResult(), "write a haiku", "acs-test-003")
        self.assertIn("(no result text)", note)

    def test_build_args_context_sync_first_turn_omits_continue(self) -> None:
        args = self.cr._build_args(self.sess, resume=False, model=None,
                                   task_text="dummy task", purpose="context_sync")
        self.assertNotIn("--continue", args)


class ADR0213ContextSyncFlagOffTest(ADR0213ContextSyncTest):
    """The OFF half of the flag pair: with `acs_context_sync` dark, the turn
    must complete normally, spawn NO extra `claude -p` replay, and leave
    turn_count where it was (the pre-ADR-0213 C1 behavior) — never error."""

    def test_flag_off_completes_the_turn_without_the_replay(self) -> None:
        called = {"n": 0}
        real = self.cr._sync_acs_result_to_transcript

        def _counting(*a, **kw):
            called["n"] += 1
            return real(*a, **kw)

        with patch.object(self.cr, "_sync_acs_result_to_transcript",
                          side_effect=_counting):
            events = self._run_delegated_turn(context_sync=False)

        self.assertEqual(events[-1].get("type"), "done",
                         "a dark flag must be a QUIET path, not an error")
        self.assertEqual(called["n"], 0,
                         "no extra claude -p replay may run while the flag is off")
        self.assertEqual(self.sess.turn_count, 0,
                         "without a transcript write, turn_count must not advance "
                         "(C1 fallback) — otherwise the next --continue resumes "
                         "a transcript that never recorded this delegation")

    # The inherited ON-state tests would run a second time under this subclass
    # with the same (default) flag state; drop them so each state is asserted
    # exactly once and a failure names the right half of the pair.
    test_sync_success_advances_turn_count_and_audits = None  # type: ignore[assignment]
    test_sync_failure_applies_c1_fallback = None  # type: ignore[assignment]
    test_sync_exception_is_swallowed_and_falls_back = None  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
