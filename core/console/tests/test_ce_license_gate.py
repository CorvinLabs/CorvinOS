"""P0 — Context-Engineering license gate (ADR-0276).

enforce_ce_quota meters CE turns on a SEPARATE pool and DEGRADES over budget
(never blocks). Verified with faked license primitives (like test_acs_quota_
fallback), so no real license/tier is touched:
  - separate pool: increment_and_check called with counter_file=
    context_engineering_quota.json AND feature=context_engineering_units_per_day
    (NOT compute_quota.json) — H1.
  - over budget → returns False (degrade), does NOT raise — H2.
  - license module missing → False (fail-closed, deny enrichment) — I3.
  - operational I/O error → True (fail-open, enrich) .
  - build_brief on a False gate → (None, {degraded}), zero stages run.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]


def _load_cel():
    """Load the CEL package by file path (the same trick chat_runtime uses)."""
    cel_dir = _REPO / "operator" / "context_engineering"
    spec = importlib.util.spec_from_file_location(
        "context_engineering", str(cel_dir / "__init__.py"),
        submodule_search_locations=[str(cel_dir)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["context_engineering"] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeLimitError(Exception):
    pass


def _inject_license(*, raise_on_check=False, io_error=False, calls=None):
    lim = types.ModuleType("license.limits")
    lim.LicenseLimitError = _FakeLimitError  # type: ignore[attr-defined]
    cq = types.ModuleType("license.compute_quota")

    def _inc(corvin_home, *, channel=None, chat_key=None,
             feature="compute_units_per_day", counter_file="compute_quota.json"):
        if calls is not None:
            calls.append({"feature": feature, "counter_file": counter_file,
                          "channel": channel})
        if raise_on_check:
            raise _FakeLimitError("ce budget exhausted")
        if io_error:
            raise OSError("disk hiccup")
    cq.increment_and_check = _inc  # type: ignore[attr-defined]
    val = types.ModuleType("license.validator")
    val.load_license_from_env = lambda: None  # type: ignore[attr-defined]
    sys.modules["license"] = types.ModuleType("license")
    sys.modules["license.limits"] = lim
    sys.modules["license.compute_quota"] = cq
    sys.modules["license.validator"] = val


class CeLicenseGateTest(unittest.TestCase):
    def setUp(self):
        self.cel = _load_cel()

    def tearDown(self):
        for m in ("license", "license.limits", "license.compute_quota",
                  "license.validator", "context_engineering"):
            sys.modules.pop(m, None)

    def test_separate_pool_counter_and_feature(self):
        calls = []
        _inject_license(calls=calls)
        ok = self.cel.enforce_ce_quota("_default")
        self.assertTrue(ok)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["counter_file"], "context_engineering_quota.json")
        self.assertEqual(calls[0]["feature"], "context_engineering_units_per_day")
        self.assertNotEqual(calls[0]["counter_file"], "compute_quota.json")

    def test_over_budget_degrades_not_blocks(self):
        _inject_license(raise_on_check=True)
        # must RETURN False, not raise
        self.assertFalse(self.cel.enforce_ce_quota("_default"))

    def test_import_missing_fail_closed(self):
        for m in ("license", "license.limits", "license.compute_quota",
                  "license.validator"):
            sys.modules.pop(m, None)
        # Block the import so it raises ImportError → fail-closed False.
        sys.modules["license.compute_quota"] = None  # type: ignore[assignment]
        try:
            self.assertFalse(self.cel.enforce_ce_quota("_default"))
        finally:
            sys.modules.pop("license.compute_quota", None)

    def test_operational_error_fail_open(self):
        _inject_license(io_error=True)
        # a non-LimitError (I/O hiccup) must fail-OPEN → True (enrich)
        self.assertTrue(self.cel.enforce_ce_quota("_default"))

    def test_build_brief_degrades_on_false_gate(self):
        _inject_license(raise_on_check=True)  # gate returns False
        brief, trace = self.cel.build_brief("some task", "_default", meter=True)
        self.assertIsNone(brief)
        self.assertEqual(trace.get("degraded"), "ce_budget_or_license")
        self.assertEqual(trace["stages"], [], "zero stages when degraded")


if __name__ == "__main__":
    unittest.main()
