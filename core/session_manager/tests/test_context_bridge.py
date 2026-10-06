"""Unit tests for SessionContextBridge (PLAN-0932, ADR-2101 P4 task/goal half).

Covers PLAN-0932's 5 verification scenarios at the module/hook level. Per
the module's own docstring and the honesty note in PLAN-0932's completion
report: ``task_id`` (the field this module keys snapshots on,
``WebChatSession.task_id`` / ADR-0649) is confirmed, by code research, to
have NO production setter anywhere in this codebase today — so a full
turn-loop E2E (driving a real reset/delete with a real task_id reaching
this module) cannot exist until a caller (a future "resume task X" API or
slash command) actually sets that field. These tests are therefore the
honest ceiling of proof available for the mechanism as it stands: they
prove the module and its flag-gated hooks are correct, fail-closed, and
ready for that future caller — they do NOT claim a live call site exists
yet (none does; see the completion report for the named gap).

Run: python3 -m pytest core/session_manager/tests/test_context_bridge.py -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "corvin_operator" / "bridges" / "shared"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "corvin_operator" / "forge"))


class ContextBridgeTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ["CORVIN_HOME"] = self.tmp.name
        os.environ["XDG_CONFIG_HOME"] = self.tmp.name
        os.environ["CORVIN_AUDIT_ANCHOR_KEY"] = str(Path(self.tmp.name) / "anchor.key")
        os.environ["FORGE_ROOT"] = str(Path(self.tmp.name) / "forge")
        os.environ["CORVIN_TENANT_ID"] = "_default"
        for mod in ("core.session_manager.context_bridge", "corvin_core.feature_flags"):
            sys.modules.pop(mod, None)
        import core.session_manager.context_bridge as cb  # noqa: PLC0415
        self.cb = cb

    def tearDown(self) -> None:
        self.tmp.cleanup()
        for k in ("CORVIN_HOME", "XDG_CONFIG_HOME", "CORVIN_AUDIT_ANCHOR_KEY",
                  "FORGE_ROOT", "CORVIN_TENANT_ID"):
            os.environ.pop(k, None)

    def _audit_records(self, event_type: str) -> list[dict]:
        from forge.paths import tenant_audit_chain  # noqa: PLC0415
        chain = tenant_audit_chain("_default")
        if not chain.is_file():
            return []
        out = []
        for line in chain.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("event_type") == event_type:
                out.append(rec)
        return out


class TestRealRoundTrip(ContextBridgeTestBase):
    def test_snapshot_then_restore_round_trips(self):
        """Scenario 1 (module level): snapshot() then restore() returns the
        same opaque task_state/ldd_state/learning_events, and both events
        are audited on the real hash chain."""
        from corvin_core import feature_flags as ff  # noqa: PLC0415
        ff.set_enabled("session_context_bridge_enabled", True, "_default")

        wrote = self.cb.maybe_snapshot_context(
            "_default", "T-1234", "sess-abc",
            task_state={"goal": "ship the thing"}, ldd_state={"k": 1},
            learning_events=[{"e": "outcome"}],
        )
        self.assertTrue(wrote)

        snap = self.cb.maybe_restore_context("_default", "T-1234")
        self.assertIsNotNone(snap)
        self.assertEqual(snap.task_id, "T-1234")
        self.assertEqual(snap.session_id, "sess-abc")
        self.assertEqual(snap.task_state, {"goal": "ship the thing"})
        self.assertEqual(snap.ldd_state, {"k": 1})
        self.assertEqual(snap.learning_events, [{"e": "outcome"}])

        created = self._audit_records("context_bridge.snapshot_created")
        restored = self._audit_records("context_bridge.restored")
        self.assertEqual(len(created), 1)
        self.assertEqual(len(restored), 1)
        self.assertEqual(created[0]["details"]["task_id"], "T-1234")
        self.assertEqual(restored[0]["details"]["task_id"], "T-1234")


class TestFlagOff(ContextBridgeTestBase):
    def test_flag_off_writes_nothing_and_restores_nothing(self):
        """Scenario 2: flag OFF (default) -> no snapshot file, no restore,
        no audit record at all — the gate is inside the hook, not just at
        registration time (there is no registration time here, which makes
        this an even stronger proof: the hook alone must refuse)."""
        wrote = self.cb.maybe_snapshot_context("_default", "T-5555", "sess-x",
                                               task_state={"goal": "x"})
        self.assertFalse(wrote)
        snap = self.cb.maybe_restore_context("_default", "T-5555")
        self.assertIsNone(snap)
        self.assertEqual(self._audit_records("context_bridge.snapshot_created"), [])

    def test_flag_on_then_off_stops_new_snapshots(self):
        """Proves the flag is re-checked on every call, not cached."""
        from corvin_core import feature_flags as ff  # noqa: PLC0415
        ff.set_enabled("session_context_bridge_enabled", True, "_default")
        self.assertTrue(self.cb.maybe_snapshot_context("_default", "T-ON", "s", task_state={}))
        ff.set_enabled("session_context_bridge_enabled", False, "_default")
        self.assertFalse(self.cb.maybe_snapshot_context("_default", "T-OFF", "s", task_state={}))


class TestCorruptSnapshot(ContextBridgeTestBase):
    def test_corrupt_snapshot_file_degrades_to_none_not_error(self):
        """Scenario 3: a hand-written invalid-JSON snapshot file -> restore()
        returns None (never raises), and context_bridge.restore_failed is
        audited with reason=corrupt."""
        from corvin_core import feature_flags as ff  # noqa: PLC0415
        from forge.paths import tenant_global_dir  # noqa: PLC0415
        ff.set_enabled("session_context_bridge_enabled", True, "_default")
        path = tenant_global_dir("_default") / "session_context_bridge" / "T-CORRUPT.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json!!", encoding="utf-8")

        snap = self.cb.maybe_restore_context("_default", "T-CORRUPT")
        self.assertIsNone(snap)
        failed = self._audit_records("context_bridge.restore_failed")
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["details"]["reason"], "corrupt")

    def test_missing_snapshot_file_returns_none_without_audit_noise(self):
        """A task_id with NO snapshot at all is 'nothing to restore', not a
        failure — no restore_failed record, unlike the corrupt case."""
        from corvin_core import feature_flags as ff  # noqa: PLC0415
        ff.set_enabled("session_context_bridge_enabled", True, "_default")
        snap = self.cb.maybe_restore_context("_default", "T-NEVER-EXISTED")
        self.assertIsNone(snap)
        self.assertEqual(self._audit_records("context_bridge.restore_failed"), [])


class TestNoTaskId(ContextBridgeTestBase):
    def test_no_task_id_snapshot_is_noop_not_error(self):
        """Scenario 4: no task_id on the session -> maybe_snapshot_context
        returns False immediately, no file, no audit record — 'nothing to
        snapshot' is not an error."""
        from corvin_core import feature_flags as ff  # noqa: PLC0415
        ff.set_enabled("session_context_bridge_enabled", True, "_default")
        self.assertFalse(self.cb.maybe_snapshot_context("_default", None, "sess-y"))
        self.assertFalse(self.cb.maybe_snapshot_context("_default", "", "sess-y"))
        self.assertEqual(self._audit_records("context_bridge.snapshot_created"), [])

    def test_no_task_id_restore_is_noop(self):
        from corvin_core import feature_flags as ff  # noqa: PLC0415
        ff.set_enabled("session_context_bridge_enabled", True, "_default")
        self.assertIsNone(self.cb.maybe_restore_context("_default", None))


class TestAuditFirst(ContextBridgeTestBase):
    def test_audit_chain_unavailable_at_snapshot_time_writes_no_file(self):
        """Scenario 5: audit-first — if the hash-chain write cannot commit,
        NO snapshot file is written either (matches a2a_feed.py's 503
        pattern: a failed chain commit must leave no side effect)."""
        from corvin_core import feature_flags as ff  # noqa: PLC0415
        from forge.paths import tenant_global_dir  # noqa: PLC0415
        ff.set_enabled("session_context_bridge_enabled", True, "_default")

        import forge.security_events as se  # noqa: PLC0415
        orig = se.write_event

        def _boom(*a, **kw):
            raise RuntimeError("chain unavailable")
        se.write_event = _boom
        try:
            wrote = self.cb.maybe_snapshot_context("_default", "T-NOCHAIN", "s",
                                                    task_state={"x": 1})
        finally:
            se.write_event = orig
        self.assertFalse(wrote)
        path = tenant_global_dir("_default") / "session_context_bridge" / "T-NOCHAIN.json"
        self.assertFalse(path.exists(), "a failed audit commit must leave no file")


if __name__ == "__main__":
    unittest.main()
