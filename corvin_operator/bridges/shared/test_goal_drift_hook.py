"""Unit tests for goal_drift_hook.py's pure logic and fail-closed branches
(PLAN-0931 Step 1). These supplement — never replace — the E2E wiring proof
in test_goal_drift_bridge_e2e.py and test_goal_drift_console_e2e.py, which
drive the real turn loops per the E2E Wiring Proof gate.

Run: python3 -m pytest corvin_operator/bridges/shared/test_goal_drift_hook.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


class GoalDriftHookTestBase(unittest.TestCase):
    def setUp(self) -> None:
        # ignore_cleanup_errors: anchor.py's _StoreLock holds an flock'd file
        # (cel_anchors/.store.lock) whose fd can outlive the test on some
        # platforms — unrelated to this hook's own correctness, just test
        # teardown hygiene for a lock file this test's code path creates.
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ["CORVIN_HOME"] = self.tmp.name
        os.environ["XDG_CONFIG_HOME"] = self.tmp.name
        os.environ["CORVIN_AUDIT_ANCHOR_KEY"] = str(Path(self.tmp.name) / "anchor.key")
        os.environ["FORGE_ROOT"] = str(Path(self.tmp.name) / "forge")
        os.environ["CORVIN_TENANT_ID"] = "_default"
        for mod in ("goal_drift_hook", "corvin_core.feature_flags",
                    "corvin_operator.context_engineering.anchor",
                    "core.session_manager.monitors.goal_alignment"):
            sys.modules.pop(mod, None)
        import goal_drift_hook  # noqa: PLC0415
        self.hook = goal_drift_hook

    def tearDown(self) -> None:
        self.tmp.cleanup()
        for k in ("CORVIN_HOME", "XDG_CONFIG_HOME", "CORVIN_AUDIT_ANCHOR_KEY",
                  "FORGE_ROOT", "CORVIN_TENANT_ID"):
            os.environ.pop(k, None)


class TestFailClosedBranches(GoalDriftHookTestBase):
    def test_flag_off_by_default_returns_none(self):
        r = self.hook.maybe_check_goal_drift("_default", "sess1", "t1", "some work")
        self.assertIsNone(r)

    def test_empty_tenant_returns_none(self):
        self.assertIsNone(self.hook.maybe_check_goal_drift("", "sess1", "t1", "work"))

    def test_empty_session_key_returns_none(self):
        self.assertIsNone(self.hook.maybe_check_goal_drift("_default", "", "t1", "work"))

    def test_empty_current_work_returns_none(self):
        self.assertIsNone(self.hook.maybe_check_goal_drift("_default", "sess1", "t1", ""))

    def test_flag_on_no_anchor_fact_returns_none_not_error(self):
        """No goal fact to compare against is NOT an error — distinct from a
        broken monitor. Flag on, anchor on, but no 'goal'-kind fact seeded."""
        from corvin_core import feature_flags as ff  # noqa: PLC0415
        ff.set_enabled("goal_drift_monitor_enabled", True, "_default")
        ff.set_enabled("cel_load_bearing_anchor", True, "_default")
        r = self.hook.maybe_check_goal_drift("_default", "brand-new-sess", "t1", "anything")
        self.assertIsNone(r)

    def test_monitor_exception_never_raises(self):
        """A broken monitor must never break a turn (PLAN-0931 verification #5)."""
        from corvin_core import feature_flags as ff  # noqa: PLC0415
        from corvin_operator.context_engineering import anchor  # noqa: PLC0415
        ff.set_enabled("goal_drift_monitor_enabled", True, "_default")
        ff.set_enabled("cel_load_bearing_anchor", True, "_default")
        anchor.add_fact("_default", "sess-exc", "goal", "some goal text")

        monitor = self.hook._get_monitor()

        def _boom(_state):
            raise RuntimeError("monitor exploded")
        monitor.check = _boom

        try:
            r = self.hook.maybe_check_goal_drift("_default", "sess-exc", "t1", "work")
        except Exception as exc:  # noqa: BLE001
            self.fail(f"maybe_check_goal_drift raised {exc!r}; must never raise")
        self.assertIsNone(r)


class TestAlertConversion(GoalDriftHookTestBase):
    def test_alert_dict_shape_and_audit_written(self):
        """Full flow: 3 consecutive low-similarity turns -> alert dict with
        the expected JSON-safe shape, and a hash-chained audit record with
        the session key FINGERPRINTED, never the raw key."""
        from corvin_core import feature_flags as ff  # noqa: PLC0415
        from corvin_operator.context_engineering import anchor  # noqa: PLC0415
        ff.set_enabled("goal_drift_monitor_enabled", True, "_default")
        ff.set_enabled("cel_load_bearing_anchor", True, "_default")
        anchor.add_fact("_default", "sess-alert", "goal",
                         "implement the payment refund flow for orders")

        r = None
        for _ in range(3):
            r = self.hook.maybe_check_goal_drift(
                "_default", "sess-alert", "t1",
                "completely unrelated topic about weather forecasting",
            )
        self.assertIsInstance(r, dict)
        self.assertEqual(r["alert_type"], "goal_drift_detected")
        self.assertEqual(r["severity"], "warning")
        self.assertEqual(r["consecutive_low_count"], 3)
        self.assertIsInstance(r["similarity_score"], float)
        # No non-JSON-safe object (dataclass/enum) leaked across the boundary.
        import json  # noqa: PLC0415
        json.dumps(r)

        from forge.paths import tenant_audit_chain  # noqa: PLC0415
        chain = tenant_audit_chain("_default")
        self.assertTrue(chain.is_file(), "audit chain file must exist after an alert")
        lines = [l for l in chain.read_text().splitlines() if "goal_drift.alert_raised" in l]
        self.assertEqual(len(lines), 1)
        import json as _json  # noqa: PLC0415
        rec = _json.loads(lines[0])
        self.assertEqual(rec["severity"], "WARNING")
        self.assertNotIn("sess-alert", _json.dumps(rec),
                         "raw session key must never reach the chain")
        self.assertEqual(rec["details"]["session_key_fingerprint"], self.hook._fp("sess-alert"))

    def test_flag_off_after_alert_condition_writes_nothing(self):
        """Flag OFF must suppress even a seeded, drift-worthy session —
        proves the hook re-checks the flag on EVERY call, not just once."""
        from corvin_operator.context_engineering import anchor  # noqa: PLC0415
        anchor.add_fact("_default", "sess-off", "goal", "a very specific goal")
        for _ in range(5):
            r = self.hook.maybe_check_goal_drift(
                "_default", "sess-off", "t1", "totally different unrelated content",
            )
            self.assertIsNone(r)
        from forge.paths import tenant_audit_chain  # noqa: PLC0415
        chain = tenant_audit_chain("_default")
        if chain.is_file():
            self.assertNotIn("goal_drift.alert_raised", chain.read_text())


if __name__ == "__main__":
    unittest.main()
