#!/usr/bin/env python3
"""Bridge worker-engine parity (`bridge_worker_engine_parity`, ADR-0255).

Until this ADR, a bridge turn could only ever reach the ACS fan-out through
the narrow big-data carve-out (`bridge_big_data_delegation`) — the operator's
Settings -> AI Engines `worker_engine` mode, an explicit `/delegate`, and the
console's own triage heuristic had NO effect on Discord/Telegram/etc. This
pins: (1) flag-off is a byte-identical pass-through to the unchanged
`_maybe_delegate_big_data`, (2) flag-on reaches the shared
`delegation_policy.resolve_worker_engine` ladder, (3) `/delegate` always wins,
(4) `mode="tde"` always degrades to native on a bridge (ADR-0221/0222 stay
frozen — this ADR does not build TDE execution for bridges), and (5) console
and bridge agree given identical tenant config and prompt.

Run: python3 operator/bridges/shared/test_bridge_worker_engine_parity.py
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
os.environ.setdefault("CORVIN_ADAPTER_SANDBOX", "0")

import adapter  # noqa: E402

BIG = "Analysiere 500 GB Serverlogs aus dem Data Lake auf Anomalie-Muster"
SMALL = "wie spät ist es?"
FANOUT = ("Recherchiere mit mehreren Workern parallel die Konkurrenzprodukte "
          "und vergleiche Preise, Features und Kundenbewertungen aus "
          "mindestens fünf verschiedenen Quellen im Detail.")


def _call(prompt=SMALL, channel="discord"):
    return adapter._maybe_delegate_worker(
        prompt, channel=channel, chat_key="chat-1", persona="")


class _Flags:
    """Patch `corvin_console.feature_flags` for one test, without touching
    the operator's real tenant config."""

    def __init__(self, *, parity: bool, mode: str = "native"):
        self._parity = parity
        self._mode = mode

    def __enter__(self):
        import corvin_console.feature_flags as ff
        self._p1 = patch.object(
            ff, "is_enabled",
            side_effect=lambda fid, tid="_default": (
                fid == "bridge_worker_engine_parity" and self._parity
            ),
        )
        self._p2 = patch.object(ff, "worker_engine_mode", return_value=self._mode)
        self._p1.start()
        self._p2.start()
        return self

    def __exit__(self, *exc):
        self._p1.stop()
        self._p2.stop()
        return False


def _fake_acs_ok(answer="Die Logs zeigen drei Anomalie-Cluster in Region EU."):
    """Inject a fake `acs_runtime` + `license.compute_quota` that succeed
    without touching the real ACS runtime or spending real quota."""
    rt = types.ModuleType("acs_runtime")

    class _Result:
        final_output = answer
        summary = ""

    class _Ok:
        def __init__(self, **kw):
            pass

        async def run(self, spec, run_id=None):
            return _Result()

    rt.ACSRuntime = _Ok
    quota = types.ModuleType("license.compute_quota")
    quota.increment_and_check = lambda *a, **k: None
    return {"acs_runtime": rt, "license.compute_quota": quota}


class FlagOffTest(unittest.TestCase):
    """The shipped default: byte-identical to `_maybe_delegate_big_data`."""

    def test_flag_off_delegates_to_big_data_only_path(self):
        with _Flags(parity=False), \
             patch.object(adapter, "_maybe_delegate_big_data",
                          return_value="big-data answer") as spy:
            answer, prompt = _call(BIG)
        spy.assert_called_once()
        self.assertEqual(answer, "big-data answer")
        self.assertEqual(prompt, BIG, "no /delegate present — prompt unchanged")

    def test_flag_off_ordinary_fanout_prompt_still_runs_direct(self):
        """Without parity, a fan-out-shaped (non-big-data) prompt must NOT
        delegate — proves the old narrow gate is untouched."""
        with _Flags(parity=False):
            answer, prompt = _call(FANOUT)
        self.assertIsNone(answer)
        self.assertEqual(prompt, FANOUT)


class DelegatePrefixTest(unittest.TestCase):
    """`/delegate` strips itself from the prompt EVEN on the degrade path."""

    def test_delegate_prefix_stripped_when_it_degrades_to_native(self):
        with _Flags(parity=True, mode="native"), \
             patch.object(adapter, "_check_compliance_or_fail",
                          return_value="[compliance] denied"):
            answer, prompt = _call("/delegate analysiere das bitte")
        self.assertIsNone(answer, "a denied gate must degrade to the direct turn")
        self.assertEqual(prompt, "analysiere das bitte",
                         "the raw '/delegate' text must never reach the LLM, "
                         "even when delegation itself failed")

    def test_bare_delegate_stripped_to_empty(self):
        with _Flags(parity=True, mode="native"), \
             patch.object(adapter, "_check_compliance_or_fail",
                          return_value="[compliance] denied"):
            answer, prompt = _call("/delegate")
        self.assertIsNone(answer)
        self.assertEqual(prompt, "")

    def test_delegatex_is_not_a_command(self):
        """Word-boundary guard: '/delegatex' is a plain prompt, not the
        directive — mirrors the console's 2026-07-24 fix."""
        with _Flags(parity=True, mode="native"):
            answer, prompt = _call("/delegatex foo bar")
        self.assertIsNone(answer)
        self.assertEqual(prompt, "/delegatex foo bar")

    def test_delegate_forces_acs_even_in_native_mode(self):
        with _Flags(parity=True, mode="native"), \
             patch.object(adapter, "_check_compliance_or_fail", return_value=None), \
             patch.object(adapter, "_check_egress_or_fail", return_value=None), \
             patch.dict(sys.modules, _fake_acs_ok("delegated answer")):
            answer, prompt = _call("/delegate " + SMALL)
        self.assertEqual(answer, "delegated answer")
        self.assertEqual(prompt, SMALL)


class TriageHeuristicTest(unittest.TestCase):
    """Non-/delegate prompts route through the shared triage heuristic."""

    def test_small_talk_never_delegates(self):
        with _Flags(parity=True, mode="acs"):
            answer, prompt = _call(SMALL)
        self.assertIsNone(answer)
        self.assertEqual(prompt, SMALL)

    def test_fanout_shaped_prompt_delegates_in_acs_mode(self):
        with _Flags(parity=True, mode="acs"), \
             patch.object(adapter, "_check_compliance_or_fail", return_value=None), \
             patch.object(adapter, "_check_egress_or_fail", return_value=None), \
             patch.dict(sys.modules, _fake_acs_ok("fan-out answer")):
            answer, prompt = _call(FANOUT)
        self.assertEqual(answer, "fan-out answer")

    def test_fanout_shaped_prompt_stays_direct_in_native_mode(self):
        """Big-data is the ONE auto-delegation in native mode; an ordinary
        fan-out-shaped prompt (not big-data) must stay direct."""
        with _Flags(parity=True, mode="native"):
            answer, prompt = _call(FANOUT)
        self.assertIsNone(answer)

    def test_big_data_delegates_even_in_native_mode(self):
        """Rung 2 of the ladder: big-data-shaped work delegates in EVERY
        mode, including native — the one auto-delegation a native install
        performs (CLAUDE.md)."""
        with _Flags(parity=True, mode="native"), \
             patch.object(adapter, "_check_compliance_or_fail", return_value=None), \
             patch.object(adapter, "_check_egress_or_fail", return_value=None), \
             patch.dict(sys.modules, _fake_acs_ok("big-data answer")):
            answer, prompt = _call(BIG)
        self.assertEqual(answer, "big-data answer")

    def test_coding_shaped_prompt_never_delegates(self):
        """The band does not widen (ADR-0229 §1) — coding stays direct even
        with parity on and mode=acs, exactly as on console."""
        with _Flags(parity=True, mode="acs"):
            answer, prompt = _call(
                "Fix the bug in auth.py where login crashes with a "
                "NullPointerException on empty passwords.")
        self.assertIsNone(answer)


class TdeStaysFrozenTest(unittest.TestCase):
    """ADR-0221 P3/P4: mode=tde degrades to native on a bridge BY DEFAULT.

    Since TDE_ROBUST_USABLE_PLAN Step 4 the freeze is the flag-OFF default
    (the opt-in `bridge_tde_execution` flag lifts it per tenant), not "by
    construction" any more. These tests do NOT enable that flag, so they pin the
    shipped default: mode=tde → native. Flag-on real TDE execution + degrade +
    background measurement are covered in test_bridge_tde_execution.py."""

    def test_tde_mode_never_produces_a_tde_answer(self):
        with _Flags(parity=True, mode="tde"), \
             patch.object(adapter, "_check_compliance_or_fail", return_value=None), \
             patch.object(adapter, "_check_egress_or_fail", return_value=None), \
             patch.dict(sys.modules, _fake_acs_ok("should not run")):
            answer, prompt = _call(SMALL)
        self.assertIsNone(answer, "mode=tde must degrade to native on a bridge")

    def test_tde_mode_with_delegate_still_goes_to_acs_not_tde(self):
        """`/delegate` always resolves to ACS on the ladder, in every mode —
        it must not somehow attempt TDE."""
        with _Flags(parity=True, mode="tde"), \
             patch.object(adapter, "_check_compliance_or_fail", return_value=None), \
             patch.object(adapter, "_check_egress_or_fail", return_value=None), \
             patch.dict(sys.modules, _fake_acs_ok("acs, not tde")):
            answer, prompt = _call("/delegate " + SMALL)
        self.assertEqual(answer, "acs, not tde")

    def test_worker_engine_target_hardcodes_tde_unavailable(self):
        target = adapter._worker_engine_target(
            SMALL, mode="tde", force_delegate=False)
        self.assertEqual(target, "native")


class ConsoleBridgeAgreementTest(unittest.TestCase):
    """Console and bridge must land on the identical engine for identical
    input — the actual parity claim ADR-0255 makes."""

    def test_console_and_bridge_agree_on_every_mode(self):
        sys.path.insert(0, str(_HERE.parents[2] / "core" / "console"))
        from corvin_console import chat_runtime as cr

        for mode in ("native", "acs", "tde"):
            for prompt in (SMALL, BIG, FANOUT):
                bridge = adapter._worker_engine_target(
                    prompt, mode=mode, force_delegate=False)
                console = cr._worker_engine_target(
                    prompt, mode=mode, force_delegate=False)
                # Regardless of mode: with the default (bridge_tde_execution
                # off — these tests don't enable it) the bridge never produces
                # "tde". Flag-on TDE execution is covered separately.
                self.assertNotEqual(bridge, "tde")
                if mode == "tde" and prompt != BIG:
                    # Rung 5 (TDE availability) is the only rung where bridge
                    # and console may legitimately diverge: console may reach
                    # real "tde" if a claude CLI + TDE modules happen to be
                    # importable in THIS dev environment; the bridge always
                    # hard-codes unavailable. Both must at least agree they
                    # are not "acs" (force_delegate=False, not big-data, so
                    # rungs 1/2 do not fire).
                    self.assertNotEqual(console, "acs")
                    continue
                # Every other cell — including mode="tde" WITH a big-data
                # prompt, which rung 2 forces to "acs" regardless of mode —
                # must agree exactly.
                self.assertEqual(
                    bridge, console,
                    f"mode={mode} prompt={prompt!r}: bridge={bridge} "
                    f"console={console}")

    def test_console_and_bridge_agree_on_delegate(self):
        sys.path.insert(0, str(_HERE.parents[2] / "core" / "console"))
        from corvin_console import chat_runtime as cr

        bridge = adapter._worker_engine_target(
            SMALL, mode="native", force_delegate=True)
        console = cr._worker_engine_target(
            SMALL, mode="native", force_delegate=True)
        self.assertEqual(bridge, console, "acs")


if __name__ == "__main__":
    unittest.main(verbosity=2)
