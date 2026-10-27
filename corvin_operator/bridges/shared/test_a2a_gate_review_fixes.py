"""Gate-review findings 3-5 (ADR-2287): inline harvest TOCTOU, empty-host L35, erasure vs in-flight staging.

These are unit-level races on the receiver's own harvest/store primitives (the real transport is exercised by
``test_a2a_objects_*``); each docstring names the seam it uses.
"""
from __future__ import annotations

import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import a2a_attachments  # noqa: E402
import a2a_worker  # noqa: E402

SECRET = b"TOP-SECRET-HOST-FILE"


def _decoded(atts: list) -> list[bytes]:
    return [base64.b64decode(a["content_b64"]) for a in atts]


class TestInlineHarvestSwap(unittest.TestCase):
    """Finding 3. Seam: ``validate_attachment_name`` runs AFTER the symlink/resolve/nlink checks and BEFORE the
    read, so a hook there is the worst-case swap by a leftover worker process."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.out = root / "out"
        self.out.mkdir()
        self.secret = root / "secret.txt"
        self.secret.write_bytes(SECRET)
        (self.out / "data.txt").write_bytes(b"honest output")
        self._orig = a2a_attachments.validate_attachment_name

    def tearDown(self) -> None:
        a2a_attachments.validate_attachment_name = self._orig

    def _swap_hook(self, swap) -> None:
        orig = self._orig

        def hook(name):
            res = orig(name)
            swap()
            return res

        a2a_attachments.validate_attachment_name = hook

    def test_symlink_swapped_in_after_the_checks_is_not_followed(self):
        def swap():
            p = self.out / "data.txt"
            p.unlink()
            p.symlink_to(self.secret)

        self._swap_hook(swap)
        atts, _dropped = a2a_worker._harvest_report(self.out, {})
        self.assertNotIn(SECRET, b"".join(_decoded(atts)))
        self.assertEqual(atts, [])

    def test_hardlink_swapped_in_after_the_checks_is_not_read(self):
        def swap():
            p = self.out / "data.txt"
            p.unlink()
            os.link(self.secret, p)

        self._swap_hook(swap)
        atts, _dropped = a2a_worker._harvest_report(self.out, {})
        self.assertNotIn(SECRET, b"".join(_decoded(atts)))

    def test_an_untouched_file_still_harvests(self):
        atts, dropped = a2a_worker._harvest_report(self.out, {})
        self.assertEqual(_decoded(atts), [b"honest output"])
        self.assertEqual(dropped, {})


class _AllowAllGate:
    """A default-allow L35 policy with no forbidden host that matches anything."""

    def __init__(self) -> None:
        self.hosts: list[str] = []

    def validate(self, host, channel=None):
        self.hosts.append(host)

        class _R:
            allowed = True

        return _R()


class TestObjectGateEmptyHost(unittest.TestCase):
    """Finding 4 (unit level: ``object_gate`` is the one function every output path calls)."""

    def _gate(self, gate, **cfg):
        import a2a_objects
        import egress_gate

        orig = egress_gate.load_egress_gate_for_tenant
        egress_gate.load_egress_gate_for_tenant = lambda tenant: gate
        try:
            return a2a_objects.object_gate(
                origin_cfg=cfg, origin_id="origin-no-url", name="a.txt", size=1,
                classification="PUBLIC", tenant_id="_default", endpoints_dir=None)
        finally:
            egress_gate.load_egress_gate_for_tenant = orig

    def test_unknown_host_with_a_policy_present_is_refused(self):
        gate = _AllowAllGate()
        self.assertEqual(self._gate(gate), "object_peer_host_unknown")
        self.assertNotIn("unknown", gate.hosts, "L35 must never be asked about a made-up host")

    def test_the_empty_host_reason_is_in_the_closed_vocabulary_and_names_peer_url(self):
        import a2a_objects

        self.assertIn("object_peer_host_unknown", a2a_objects.REFUSALS)
        self.assertIn("peer_url", a2a_objects.REFUSAL_HINTS["object_peer_host_unknown"])

    def test_known_host_still_goes_through_the_policy(self):
        gate = _AllowAllGate()
        self.assertIsNone(self._gate(gate, peer_url="https://peer.example/a2a"))
        self.assertEqual(gate.hosts, ["peer.example"])

    def test_no_policy_means_no_restriction(self):
        self.assertIsNone(self._gate(None))


class TestErasureVersusInFlightStaging(unittest.TestCase):
    """Finding 5 (unit-level race: the harvest stages, erasure runs, THEN the receiver issues)."""

    def setUp(self) -> None:
        import unittest.mock as mock

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        env = mock.patch.dict(os.environ, {"CORVIN_A2A_FEED_DIR": str(self.root / "g" / "a2a_feed"),
                                           "CORVIN_HOME": str(self.root / "home")})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self._tmp.cleanup)
        import a2a_objects

        self.o = a2a_objects
        getattr(a2a_objects, "_reset_for_tests", a2a_objects._last_sweep.clear)()

    def _stage(self, name="out.txt"):
        p = self.root / "work" / name
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(b"data for the erased origin")
        staged, why = self.o.stage_file(p)
        self.assertIsNotNone(staged, why)
        return staged

    def _issue(self, staged, origin="peerA"):
        return self.o.issue(staged, origin_id=origin, origin_cfg={}, task_id="t",
                            audit_strict=lambda *a, **k: None, audit=lambda *a, **k: None)

    def test_issue_after_erasure_of_its_origin_refuses_and_leaves_no_record(self):
        staged = self._stage()
        self.o.erase_origin("peerA")
        ref, why = self._issue(staged)
        self.assertIsNone(ref)
        self.assertEqual(why, "object_store_error")
        root = self.o.store_dir(None)
        self.assertEqual(list(root.glob("*.json")), [])
        self.assertEqual(list(root.glob("*.bin")), [])
        self.assertEqual(list((root / "staging").glob("*.part")), [])

    def test_a_stage_that_began_after_the_erasure_is_issued(self):
        self._stage("first.txt")
        self.o.erase_origin("peerA")
        later = self._stage("later.txt")
        ref, why = self._issue(later)
        self.assertIsNone(why)
        self.assertIsNotNone(ref)

    def test_another_origin_is_unaffected(self):
        staged = self._stage()
        self.o.erase_origin("peerB")
        ref, why = self._issue(staged)
        self.assertIsNone(why)

    def test_erasure_creates_the_store_and_marker_when_nothing_was_stored_yet(self):
        root = self.o.store_dir(None)
        self.assertFalse(root.exists())
        self.o.erase_origin("peerA")
        self.assertTrue(self.o._erasure_marker(root, "peerA").is_file())
        # a harvest that began BEFORE that erasure (stamp in the past) must now be refused
        self.assertTrue(self.o._erased_since(root, "peerA", 1))

    def test_an_unparseable_marker_counts_as_erased(self):
        root = self.o.store_dir(None)
        self.o.erase_origin("peerA")
        self.o._erasure_marker(root, "peerA").write_text("not-a-number", "ascii")
        self.assertTrue(self.o._erased_since(root, "peerA", 2 ** 62))

    def test_an_unreadable_marker_counts_as_erased(self):
        root = self.o.store_dir(None)
        self.o.erase_origin("peerA")
        m = self.o._erasure_marker(root, "peerA")
        m.unlink()
        m.mkdir()          # reading a directory raises OSError other than "missing"
        self.assertTrue(self.o._erased_since(root, "peerA", 2 ** 62))

    def test_no_marker_at_all_is_not_erased(self):
        root = self.o.store_dir(None)
        self.o._ensure(root)
        self.assertFalse(self.o._erased_since(root, "peerA", 5))


class TestStageFileFifo(unittest.TestCase):
    def test_a_fifo_swapped_in_is_unreadable_and_never_blocks(self):
        import threading
        import unittest.mock as mock

        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"CORVIN_A2A_FEED_DIR": td + "/g/a2a_feed", "CORVIN_HOME": td + "/h"}):
                import a2a_objects

                fifo = Path(td) / "out.fifo"
                os.mkfifo(fifo)
                out: list = []
                t = threading.Thread(target=lambda: out.append(a2a_objects.stage_file(fifo)), daemon=True)
                t.start()
                t.join(5)
                if t.is_alive():   # unblock the stuck open so the process can exit, then fail
                    fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
                    os.close(fd)
                    t.join(2)
                    self.fail("stage_file blocked on a FIFO")
                self.assertEqual(out[0], (None, "unreadable"))


if __name__ == "__main__":
    unittest.main()
