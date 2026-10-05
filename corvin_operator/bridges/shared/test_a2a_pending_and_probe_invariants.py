"""Invariants that must hold at EVERY call site, not just the one a review
found (feedback: fix the primitive, then test that nothing bypasses it).

1. A chat-staged send / token request is handed out exactly once, even to
   racing confirms (review R1 2026-10-05: read-then-unlink let both win).
2. Staging refuses what the confirm card could not honestly show: a token
   that never expires, text over the receiver's byte cap.
3. Every function that probes a peer (``.ping(`` / ``_ping_peer(``) also
   records the outcome through ``stamp_probe`` — or is listed below with the
   reason it does not. A new probe path that forgets it fails this test.
"""
from __future__ import annotations

import ast
import tempfile
import threading
import unittest
from pathlib import Path

import a2a_chat_friendship_token as pft
import a2a_chat_pending_send as ps

_REPO = Path(__file__).resolve().parents[3]

# Functions that ping without stamping, each with its reason.
_PROBE_ALLOWLIST = {
    ("a2a_connectivity.py", "_ping_peer"): "returns the outcome; both callers stamp it",
    ("remote_trigger_sender.py", "ping"): "the probe itself",
    ("remote_trigger_sender.py", "_http_ping_probe"): "the probe transport",
}
_PROBE_FILES = [
    _REPO / "corvin_operator/bridges/shared/a2a_connectivity.py",
    _REPO / "corvin_operator/bridges/shared/a2a_friendship.py",
    _REPO / "corvin_operator/bridges/shared/remote_trigger_sender.py",
    _REPO / "core/console/corvin_console/routes/a2a_pair.py",
    _REPO / "corvin_operator/voice/scripts/corvin_a2a.py",
]


class ClaimOnceTests(unittest.TestCase):
    def test_racing_confirms_get_the_send_exactly_once(self):
        d = Path(tempfile.mkdtemp())
        for _ in range(20):
            rec = ps.create_pending_send(d, peer_id="p", text="hi")
            barrier = threading.Barrier(8)
            got: list = []

            def confirm():
                barrier.wait()
                got.append(ps.pop_pending_send(d, rec["pending_id"]))

            threads = [threading.Thread(target=confirm) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(sum(1 for g in got if g), 1, got)

    def test_token_request_claimed_once(self):
        d = Path(tempfile.mkdtemp())
        rec = pft.create_pending_token_request(d, label="x", ttl_hours=24)
        self.assertIsNotNone(pft.pop_pending_token_request(d, rec["pending_id"]))
        self.assertIsNone(pft.pop_pending_token_request(d, rec["pending_id"]))

    def test_staging_refuses_never_expiring_token(self):
        d = Path(tempfile.mkdtemp())
        for bad in (0, -1, float("nan"), float("inf"), 10**9):
            with self.assertRaises(ValueError, msg=bad):
                pft.create_pending_token_request(d, ttl_hours=bad)

    def test_staging_refuses_text_over_byte_cap(self):
        d = Path(tempfile.mkdtemp())
        with self.assertRaises(ValueError):
            ps.create_pending_send(d, peer_id="p", text="ä" * (9 * 1024))  # 18 KiB in UTF-8
        ps.create_pending_send(d, peer_id="p", text="a" * (16 * 1024))


class EveryProbeIsStampedTests(unittest.TestCase):
    @staticmethod
    def _probing_functions(path: Path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = {getattr(c.func, "attr", None) or getattr(c.func, "id", None)
                     for c in ast.walk(node) if isinstance(c, ast.Call)}
            if calls & {"ping", "_ping_peer"}:
                yield node.name, "stamp_probe" in calls

    def test_every_probe_path_stamps_or_is_allowlisted(self):
        offenders = []
        seen = 0
        for f in _PROBE_FILES:
            for name, stamps in self._probing_functions(f):
                seen += 1
                if not stamps and (f.name, name) not in _PROBE_ALLOWLIST:
                    offenders.append(f"{f.name}::{name}")
        self.assertGreaterEqual(seen, 5, "positive control: the scan must find the known probe paths")
        self.assertEqual(offenders, [], "probe without stamp_probe — presence would go stale")

    def test_the_scan_catches_an_injected_violation(self):
        src = "def new_probe(s):\n    return s.ping('kid')\n"
        tmp = Path(tempfile.mkdtemp()) / "x.py"
        tmp.write_text(src)
        self.assertEqual(list(self._probing_functions(tmp)), [("new_probe", False)])


if __name__ == "__main__":
    unittest.main()


class Round2RegressionTests(unittest.TestCase):
    def test_claimed_record_survives_a_concurrent_sweep(self):
        """rename kept the staged mtime: a record staged > 60 s ago looked like
        a crash leftover the moment it was claimed and a sweep deleted it."""
        import os
        import time as _t
        from unittest import mock
        import a2a_pending_claim as claim_mod
        d = Path(tempfile.mkdtemp())
        rec = ps.create_pending_send(d, peer_id="p", text="hi")
        pdir = ps._pending_dir(d)
        staged = pdir / f"{rec['pending_id']}.json"
        old = _t.time() - 300
        os.utime(staged, (old, old))
        real_read = Path.read_text

        def read_after_sweep(self, *a, **k):
            if self.suffix == ".claimed":
                claim_mod.sweep_stale_claims(pdir)   # another confirm's sweep, mid-claim
            return real_read(self, *a, **k)

        with mock.patch.object(Path, "read_text", read_after_sweep):
            got = ps.pop_pending_send(d, rec["pending_id"])
        self.assertIsNotNone(got, "an in-flight claim was swept away")

    def test_default_nonce_store_refuses_to_degrade(self):
        import a2a_nonce_store as ns
        home = Path(tempfile.mkdtemp())
        (home / "global").mkdir()
        (home / "global" / "nonces").write_text("not a directory")
        with self.assertRaises(RuntimeError):
            ns.default_nonce_store(home)

    def test_attachment_names_safe_on_windows(self):
        from a2a_attachments import AttachmentError, validate_attachment_name, validate_attachments
        import base64, hashlib
        for bad in ("CON", "nul.txt", "COM1.log", "lpt9", "report."):
            with self.assertRaises(AttachmentError, msg=bad):
                validate_attachment_name(bad)
        validate_attachment_name("console.txt")
        def att(name):
            data = b"x"
            return {"name": name, "mime": "text/plain", "content_b64": base64.b64encode(data).decode(),
                    "sha256": hashlib.sha256(data).hexdigest(), "size": 1}
        with self.assertRaises(AttachmentError):
            validate_attachments([att("a.txt"), att("A.TXT")])
