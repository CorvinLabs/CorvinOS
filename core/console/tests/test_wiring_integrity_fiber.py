"""ADR-0215 Phase 2: WiringIntegrityFiber + TokenSavingsFiber.

These are the runtime ("Nervensystem") half of ADR-0215's proof mechanism —
operator/orchestration/wiring_gate.py is the CI-time half. Both read the
same WIRING.yaml manifests; this Fiber additionally re-checks reachability
at scan time (catches post-merge drift) and cross-references real tde.*
audit traffic.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))

from corvin_console.aco.nerve_builtins import (  # noqa: E402
    TokenSavingsFiber,
    WiringIntegrityFiber,
    _BUILTIN_FIBERS,
    _load_wiring_manifests,
)


class TestWiringIntegrityFiber(unittest.TestCase):
    def test_registered_in_builtin_fibers(self):
        ids = [f.fiber_id for f in _BUILTIN_FIBERS]
        self.assertIn("aco.wiring_integrity", ids)
        self.assertIn("aco.token_savings", ids)

    def test_load_wiring_manifests_finds_real_components(self):
        components = _load_wiring_manifests()
        names = {c["name"] for c in components}
        # Spot-check a few components we know are declared in the real
        # manifests (see operator/orchestration/{,tde/}WIRING.yaml).
        self.assertIn("tde_engine", names)
        self.assertIn("streaming_executor", names)
        self.assertIn("initial_analysis", names)

    def test_scan_does_not_raise(self):
        signals = WiringIntegrityFiber().scan()
        self.assertIsInstance(signals, list)

    def test_scan_reports_manifest_stats(self):
        signals = WiringIntegrityFiber().scan()
        stats = [s for s in signals if s.signal_type == "wiring.manifest_stats"]
        self.assertEqual(len(stats), 1)
        self.assertGreater(stats[0].data["live_count"], 0)
        self.assertGreater(stats[0].data["deferred_count"], 0)

    def test_real_repo_has_zero_broken_live_entry_points(self):
        # This IS the runtime proof this Fiber exists for: if this ever
        # fails, either the manifest lied when it said `live`, or something
        # broke a live entry_point after the CI gate last ran.
        signals = WiringIntegrityFiber().scan()
        broken = [s for s in signals if s.signal_type == "wiring.live_entry_point_broken"]
        self.assertEqual(broken, [], [s.message for s in broken])

    def test_deferred_components_are_not_reported_as_broken(self):
        # streaming_executor / detector_plugin_registry are `deferred` —
        # they must never appear in the broken list just for lacking a
        # production caller (that's the whole point of `deferred`).
        signals = WiringIntegrityFiber().scan()
        broken_names = {
            s.data.get("name") for s in signals
            if s.signal_type == "wiring.live_entry_point_broken"
        }
        self.assertNotIn("streaming_executor", broken_names)
        self.assertNotIn("detector_plugin_registry", broken_names)

    def test_broken_live_entry_point_detected(self):
        # Synthetic regression check: temporarily point a fake `live` entry
        # at a module that doesn't exist and confirm the Fiber catches it —
        # proves the check is discriminating, not just always-green.
        import corvin_console.aco.nerve_builtins as nb

        fiber = WiringIntegrityFiber()
        original = nb._load_wiring_manifests
        try:
            nb._load_wiring_manifests = lambda: [{
                "name": "totally_fake_component",
                "status": "live",
                "entry_point": "totally_nonexistent_module_xyz_123:Foo",
                "_manifest": "fake/WIRING.yaml",
            }]
            signals = fiber.scan()
            broken = [s for s in signals if s.signal_type == "wiring.live_entry_point_broken"]
            self.assertEqual(len(broken), 1)
            self.assertEqual(broken[0].data["name"], "totally_fake_component")
        finally:
            nb._load_wiring_manifests = original


class TestTokenSavingsFiber(unittest.TestCase):
    def test_scan_does_not_raise(self):
        signals = TokenSavingsFiber().scan()
        self.assertIsInstance(signals, list)

    def test_scan_reports_latency_stats_and_is_honest_about_tokens(self):
        signals = TokenSavingsFiber().scan()
        stats = [s for s in signals if s.signal_type == "wiring.tde_latency_stats"]
        self.assertEqual(len(stats), 1)
        # Load-bearing honesty check (this is the whole point of the
        # Fiber): it must never claim token savings are measured, because
        # they are not instrumented (see class docstring).
        self.assertIs(stats[0].data["token_usage_instrumented"], False)
        self.assertIn("Tokens: NICHT instrumentiert", stats[0].message)

    def test_scan_never_raises_on_empty_or_missing_audit_log(self):
        import corvin_console.aco.nerve_builtins as nb

        fiber = TokenSavingsFiber()
        original = nb._ensure_bridges_on_path

        def _break_audit_resolution():
            raise RuntimeError("simulated: audit module unavailable")

        try:
            nb._ensure_bridges_on_path = _break_audit_resolution
            signals = fiber.scan()
            self.assertEqual(signals, [])
        finally:
            nb._ensure_bridges_on_path = original


if __name__ == "__main__":
    unittest.main()
