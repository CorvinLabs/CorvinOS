#!/usr/bin/env python3
"""Big-data carve-out on the messenger bridges (`bridge_big_data_delegation`).

Until 2026-07-26 the bridges had no Tier-1 delegation at all, so a big-data
task on Discord/WhatsApp ran as one direct turn while the Console fanned the
same task out to ACS. This adds the carve-out — behind a dark flag — and pins
the property that matters most: **every** failure path degrades to the direct
turn. A bridge message must never fail because delegation was unavailable.

Run: python3 operator/bridges/shared/test_adapter_big_data_delegation.py
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
os.environ.setdefault("CORVIN_ADAPTER_SANDBOX", "0")

import adapter  # noqa: E402

BIG = "Analysiere 500 GB Serverlogs aus dem Data Lake auf Anomalie-Muster"
SMALL = "wie spät ist es?"


def _call(prompt=BIG):
    return adapter._maybe_delegate_big_data(
        prompt, channel="discord", chat_key="chat-1", persona="")


class _FlagOn:
    """Turn the flag on without touching the operator's real config."""

    def __enter__(self):
        import corvin_console.feature_flags as ff
        self._p = patch.object(ff, "is_enabled",
                               side_effect=lambda fid, tid="_default":
                               fid == "bridge_big_data_delegation")
        self._p.start()
        return self

    def __exit__(self, *exc):
        self._p.stop()
        return False


class FlagOffTest(unittest.TestCase):
    """The shipped default: bridges behave exactly as before."""

    def test_big_data_is_not_delegated_while_dark(self):
        self.assertIsNone(_call(), "a dark flag must leave the direct turn alone")

    def test_small_talk_is_never_delegated(self):
        self.assertIsNone(_call(SMALL))

    def test_dark_flag_short_circuits_before_anything_else_runs(self):
        """`None` alone does not prove the feature is off — with the flag check
        deleted the code still returns None (it just fails further down). So
        assert that NOTHING downstream is even reached.

        (Mutation-checked: removing the flag check left the two tests above
        green; this one goes red.)
        """
        reached = {"gate": 0, "quota": 0, "runtime": 0}

        import types as _t
        rt = _t.ModuleType("acs_runtime")

        class _Spy:
            def __init__(self, **kw):
                reached["runtime"] += 1

        rt.ACSRuntime = _Spy
        quota = _t.ModuleType("license.compute_quota")

        def _q(*a, **k):
            reached["quota"] += 1

        quota.increment_and_check = _q

        def _gate(*a, **k):
            reached["gate"] += 1
            return None

        with patch.object(adapter, "_check_compliance_or_fail", _gate), \
             patch.object(adapter, "_check_egress_or_fail", _gate), \
             patch.dict(sys.modules, {"acs_runtime": rt,
                                      "license.compute_quota": quota}):
            self.assertIsNone(_call())

        self.assertEqual(
            reached, {"gate": 0, "quota": 0, "runtime": 0},
            f"a dark feature must not touch gates, quota or the runtime: {reached}")


class FlagOnTest(unittest.TestCase):
    """With the flag on, only the big-data shape delegates — and every
    failure still falls back to the direct turn."""

    def test_non_big_data_still_runs_direct(self):
        with _FlagOn():
            self.assertIsNone(_call(SMALL))

    def test_denied_l34_gate_degrades_to_direct_turn(self):
        with _FlagOn(), patch.object(adapter, "_check_compliance_or_fail",
                                     return_value="[compliance] denied"):
            self.assertIsNone(_call(), "a denied gate must degrade, not refuse")

    def test_denied_l35_gate_degrades_to_direct_turn(self):
        with _FlagOn(), \
             patch.object(adapter, "_check_compliance_or_fail", return_value=None), \
             patch.object(adapter, "_check_egress_or_fail",
                          return_value="[egress] denied"):
            self.assertIsNone(_call())

    def test_raising_gate_degrades_to_direct_turn(self):
        with _FlagOn(), patch.object(adapter, "_check_compliance_or_fail",
                                     side_effect=RuntimeError("gate exploded")):
            self.assertIsNone(_call(), "a broken gate must never fan out")

    def test_l34_gate_is_actually_consulted(self):
        """Guard against the fan-out running ungated (the C1 bug class)."""
        seen = {}

        def _spy(engine, **kw):
            seen["engine"] = getattr(engine, "name", None)
            seen["prompt"] = kw.get("prompt")
            return "[compliance] denied"   # deny → returns None, no ACS spawn

        with _FlagOn(), patch.object(adapter, "_check_compliance_or_fail", _spy):
            _call()
        self.assertEqual(seen.get("engine"), "acs",
                         "the gate must be asked about the ACS spawn class, "
                         "not the direct engine")
        self.assertIn("500 GB", seen.get("prompt") or "",
                      "the gate must see the actual prompt")

    def test_exhausted_quota_degrades_to_direct_turn(self):
        """Quota is mocked to RAISE — never call the real charger from a test,
        it would consume the operator's actual compute units."""
        import types as _t
        over = _t.ModuleType("license.compute_quota")

        def _boom(*a, **k):
            raise RuntimeError("compute quota exhausted")

        over.increment_and_check = _boom
        spawned = {"n": 0}
        fake_rt = _t.ModuleType("acs_runtime")

        class _NeverRuns:
            def __init__(self, **kw):
                spawned["n"] += 1

        fake_rt.ACSRuntime = _NeverRuns
        with _FlagOn(), \
             patch.object(adapter, "_check_compliance_or_fail", return_value=None), \
             patch.object(adapter, "_check_egress_or_fail", return_value=None), \
             patch.dict(sys.modules, {"license.compute_quota": over,
                                      "acs_runtime": fake_rt}):
            self.assertIsNone(_call())
        self.assertEqual(spawned["n"], 0,
                         "an exhausted pool must block the fan-out BEFORE it spawns")

    def test_runtime_failure_degrades_to_direct_turn(self):
        import types as _t
        fake = _t.ModuleType("acs_runtime")

        class _Boom:
            def __init__(self, **kw):
                raise RuntimeError("no workers today")

        fake.ACSRuntime = _Boom
        with _FlagOn(), \
             patch.object(adapter, "_check_compliance_or_fail", return_value=None), \
             patch.object(adapter, "_check_egress_or_fail", return_value=None), \
             patch.dict(sys.modules, {"acs_runtime": fake,
                                      "license.compute_quota": _quota_ok()}):
            self.assertIsNone(_call())

    def test_successful_run_returns_the_delegated_answer(self):
        """The ON half of the pair: a clean run must actually short-circuit the
        direct turn — otherwise the flag is decoration."""
        import types as _t
        fake = _t.ModuleType("acs_runtime")

        class _Result:
            final_output = "Die Logs zeigen drei Anomalie-Cluster in Region EU."
            summary = ""

        class _Ok:
            def __init__(self, **kw):
                pass

            async def run(self, spec, run_id=None):
                return _Result()

        fake.ACSRuntime = _Ok
        with _FlagOn(), \
             patch.object(adapter, "_check_compliance_or_fail", return_value=None), \
             patch.object(adapter, "_check_egress_or_fail", return_value=None), \
             patch.dict(sys.modules, {"acs_runtime": fake,
                                      "license.compute_quota": _quota_ok()}):
            out = _call()
        self.assertEqual(out, _Result.final_output)


def _quota_ok():
    import types as _t
    m = _t.ModuleType("license.compute_quota")
    m.increment_and_check = lambda *a, **k: None
    return m


class ClassifierIsSharedTest(unittest.TestCase):
    """The bridge must classify with the SAME function as the console — a
    second copy would drift, which is what the shared module exists to stop."""

    def test_bridge_uses_the_shared_classifier(self):
        src = (_HERE / "adapter.py").read_text(encoding="utf-8")
        fn = src.split("def _maybe_delegate_big_data", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("from delegation_policy import is_big_data_task", fn)

    def test_console_and_bridge_agree(self):
        from delegation_policy import is_big_data_task
        sys.path.insert(0, str(_HERE.parents[2] / "core" / "console"))
        from corvin_console import chat_runtime as cr
        for prompt in (BIG, SMALL, "Verarbeite 5 Millionen Verkaufsdaten",
                       "Screene 5 Millionen Kandidaten", "2 TB SSD einbauen"):
            self.assertEqual(is_big_data_task(prompt), cr._is_big_data_task(prompt),
                             f"classifiers disagree on {prompt!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
