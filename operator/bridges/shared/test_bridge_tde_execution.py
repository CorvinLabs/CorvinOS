#!/usr/bin/env python3
"""Bridge TDE execution (`bridge_tde_execution`, TDE_ROBUST_USABLE_PLAN Step 4).

Lifts the ADR-0221 bridge TDE freeze PER TENANT behind a ships-dark opt-in flag,
so the operator can test TDE from Discord/WhatsApp and measure it in the
background on real tasks. Pins:

  (1) flag OFF  → mode=tde still degrades to native (frozen default intact),
  (2) flag ON + mode=tde + TDE probed available → real TDE via
      `_run_tde_delegation`,
  (3) a failing TDE run (None) degrades to the native turn — the bridge's
      built-in robust degrade (self-healing on this path),
  (4) `_worker_engine_target` returns "tde" only when the flag is on AND the
      console probes pass; any probe failure keeps the frozen default,
  (5) the tde flag ALONE does not unlock ACS (that needs its own parity flag).

Run: python3 operator/bridges/shared/test_bridge_tde_execution.py
"""
from __future__ import annotations

import os
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[2] / "core" / "console"))
os.environ.setdefault("CORVIN_ADAPTER_SANDBOX", "0")

import adapter  # noqa: E402

SMALL = "erklär mir kurz was ein index in postgres ist"
FANOUT = ("Recherchiere mit mehreren Workern parallel die Konkurrenzprodukte "
          "und vergleiche Preise, Features und Kundenbewertungen aus "
          "mindestens fünf verschiedenen Quellen im Detail.")


class _TdeReady:
    """Every patch a bridge TDE turn needs: flag on, mode=tde, console probes
    pass, triage worthy — without touching real tenant config or the CLI."""

    def __init__(self, *, tde_exec=True, mode="tde", parity=False,
                 probes=True, worthy=True):
        self.kw = dict(tde_exec=tde_exec, mode=mode, parity=parity,
                       probes=probes, worthy=worthy)

    def __enter__(self):
        import corvin_console.feature_flags as ff
        import corvin_console.chat_runtime as cr
        self.stack = ExitStack()

        def _is_enabled(fid, tid="_default"):
            if fid == "bridge_tde_execution":
                return self.kw["tde_exec"]
            if fid == "bridge_worker_engine_parity":
                return self.kw["parity"]
            return False

        self.stack.enter_context(
            patch.object(ff, "is_enabled", side_effect=_is_enabled))
        self.stack.enter_context(
            patch.object(ff, "worker_engine_mode", return_value=self.kw["mode"]))
        self.stack.enter_context(
            patch.object(cr, "_tde_available", return_value=self.kw["probes"]))
        self.stack.enter_context(
            patch.object(cr, "_tde_quota_peek_ok", return_value=self.kw["probes"]))
        self.stack.enter_context(
            patch.object(cr, "should_delegate_bundled",
                         return_value=self.kw["worthy"]))
        return self

    def __exit__(self, *exc):
        self.stack.close()
        return False


def _call(prompt=SMALL):
    return adapter._maybe_delegate_worker(
        prompt, channel="discord", chat_key="c1", persona="")


class WorkerEngineTargetTest(unittest.TestCase):
    def test_flag_off_degrades_tde_to_native(self):
        # No flag patch → real default (off) → historical frozen behavior.
        target = adapter._worker_engine_target(
            SMALL, mode="tde", force_delegate=False)
        self.assertEqual(target, "native")

    def test_flag_on_probes_pass_yields_tde(self):
        with _TdeReady(probes=True):
            target = adapter._worker_engine_target(
                SMALL, mode="tde", force_delegate=False)
        self.assertEqual(target, "tde")

    def test_flag_on_probe_unavailable_degrades(self):
        with _TdeReady(probes=False):
            target = adapter._worker_engine_target(
                SMALL, mode="tde", force_delegate=False)
        self.assertEqual(target, "native",
                         "a failed probe must keep the frozen default")


class MaybeDelegateWorkerTest(unittest.TestCase):
    def test_flag_on_mode_tde_runs_tde(self):
        with _TdeReady(), \
             patch.object(adapter, "_run_tde_delegation",
                          return_value="tde answer") as spy:
            answer, prompt = _call()
        spy.assert_called_once()
        self.assertEqual(answer, "tde answer")

    def test_tde_failure_degrades_to_native(self):
        with _TdeReady(), \
             patch.object(adapter, "_run_tde_delegation", return_value=None):
            answer, prompt = _call()
        self.assertIsNone(answer, "a failing TDE run must degrade to native")

    def test_tde_flag_alone_does_not_unlock_acs(self):
        """bridge_tde_execution enters the full route, but mode=acs without the
        parity flag must NOT fan out to ACS — the tde flag only unlocks TDE."""
        with _TdeReady(mode="acs", parity=False), \
             patch.object(adapter, "_run_acs_delegation",
                          return_value="acs!") as spy:
            answer, prompt = _call(FANOUT)
        self.assertIsNone(answer, "tde flag alone must not unlock ACS")
        spy.assert_not_called()

    def test_small_talk_still_stays_direct(self):
        """Triage still gates: a non-worthy prompt stays direct even with the
        flag on (no TDE fan-out on small talk)."""
        with _TdeReady(worthy=False), \
             patch.object(adapter, "_run_tde_delegation",
                          return_value="should not run") as spy:
            answer, prompt = _call("hi")
        self.assertIsNone(answer)
        spy.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
