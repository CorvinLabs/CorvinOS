"""ADR-2295 review findings (T-0311 round 2) — each reproduced, then closed.

Same harness as ``test_a2a_offline_pair_oob_e2e.py``: two sandboxed installations, every pairing
step through the real ``corvin_a2a.py`` CLI as a subprocess, installation A's real HTTP receiver.
The ledger concurrency (F1) and structure (F8) checks also run against the module directly, since
the race is between writers, not between CLI calls.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import a2a_friendship  # noqa: E402
import a2a_offline_pairing as oob  # noqa: E402
from test_a2a_offline_pair_oob_e2e import (  # noqa: E402
    CLI, GRACE_OVER, _json_tail, _PairHarness, setUpModule,  # noqa: F401 — setUpModule pins the tier
)


def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


# An RS256-SHAPED token: the sender attaches a network_attestation block for it. Its signature is
# not a real one — no sender of an offline pair holds a hosted-service SesT.
_RS256_TOKEN = f"{_b64({'alg': 'RS256', 'typ': 'JWT'})}.{_b64({'sub': 'x'})}.c2ln"


class LedgerUnit(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.led = Path(self._tmp.name) / "a2a" / "ledger.json"

    def test_f1_concurrent_records_lose_nothing_and_a_reuse_is_refused(self):
        n = 40
        fps = [oob.fingerprint("r", f"s{i}", "k") for i in range(n)]
        barrier = threading.Barrier(n)
        errs: list[str] = []

        def go(i: int) -> None:
            barrier.wait()
            try:
                oob.record_fingerprint(fps[i], kid=f"{i:016x}", side="receiver", peer_id=f"p{i}",
                                       pairing_id=f"pid{i}", path=self.led)
            except Exception as e:  # noqa: BLE001
                errs.append(repr(e))

        ts = [threading.Thread(target=go, args=(i,)) for i in range(n)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errs, [])
        rows = json.loads(self.led.read_text())["fingerprints"]
        self.assertEqual(len(rows), n, "no record lost")
        for i, fp in enumerate(fps):
            self.assertTrue(oob.fingerprint_used(fp, self.led))
            with self.assertRaises(oob.OfflinePairError) as cm:
                oob.record_fingerprint(fp, kid="ffffffffffffffff", side="receiver", peer_id="other",
                                       pairing_id="x", path=self.led)
            self.assertEqual(cm.exception.reason, "fingerprint_reused")

    def test_f8_a_ledger_of_the_wrong_shape_is_refused_not_read_as_empty(self):
        fp = oob.fingerprint("r", "s", "k")
        self.led.parent.mkdir(parents=True)
        for bad in ({"fp": [{"fingerprint": oob.normalise(fp)}]},
                    [{"fingerprint": oob.normalise(fp)}],
                    {"fingerprints": "x"}):
            self.led.write_text(json.dumps(bad))
            with self.assertRaises(oob.OfflinePairError) as cm:
                oob.fingerprint_used(fp, self.led)
            self.assertEqual(cm.exception.reason, "ledger_unreadable", bad)
            with self.assertRaises(oob.OfflinePairError):
                oob.record_fingerprint(fp, kid="0" * 16, side="sender", peer_id="p",
                                       pairing_id="x", path=self.led)


class ReviewFindingsE2E(_PairHarness):
    def _pending_pair(self) -> dict:
        handoff = self._step1_a_pairs()
        return self._step2_b_imports(handoff)

    def test_f2_a_refused_confirm_never_prints_the_correct_fingerprint(self):
        imp = self._pending_pair()
        right = self._a_shows(imp["instance_id"])
        r = self._a_confirms(imp["instance_id"], "0000-1111-2222-3333-4444")
        self.assertNotEqual(r.returncode, 0)
        out = r.stdout + r.stderr
        self.assertNotIn(right, out)
        self.assertNotIn(oob.normalise(right), out)
        self.assertNotIn(oob.prefix(right), out)
        self.assertIn("do not confirm", out.lower())
        r = self._b_confirms("ABCD-ABCD-ABCD-ABCD-ABCD")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn(right, r.stdout + r.stderr)
        self.assertNotIn(oob.normalise(right), r.stdout + r.stderr)

    def test_f3_deleting_oob_state_does_not_turn_a_pending_pair_into_a_legacy_one(self):
        self._pending_pair()
        for d, name in ((self.a.origins, "peer-b"), (self.b.endpoints, "to-a")):
            p = d / f"{name}.json"
            cfg = json.loads(p.read_text())
            cfg.pop("oob_state")
            cfg["enabled"] = True
            p.write_text(json.dumps(cfg))
            p.chmod(0o600)
        self._serve_a()
        self._assert_sender_refused(self._send())
        # the receiving side on its own: B's endpoint confirmed properly, A's origin still edited
        p = self.b.endpoints / "to-a.json"
        cfg = json.loads(p.read_text())
        cfg["oob_state"] = "confirmed"
        p.write_text(json.dumps(cfg))
        p.chmod(0o600)
        self._assert_receiver_refused(self._send(), "origin_offline_pair_unconfirmed")

    def test_f7_a_sender_attaching_an_attestation_block_still_gets_the_out_of_band_check(self):
        self._full_pair()
        self._serve_a()
        self._manifest = GRACE_OVER
        res = self.b.cli("send", "to-a", "hello", "--timeout", "30", CORVIN_LICENSE_KEY=_RS256_TOKEN)
        self.assertTrue(self._sent_ok(res), res.stdout + res.stderr[-2000:])
        out = _json_tail(res.stdout)
        recv = [r["details"] for r in self.a.records("A2A.envelope_received")
                if r["details"].get("task_id") == out["task_id"]]
        self.assertEqual(recv[0].get("attestation"), "attested_out_of_band")
        # a tampered confirmed origin is refused even though the envelope carries a block
        p = self.a.origins / "peer-b.json"
        o = json.loads(p.read_text())
        o["oob_fingerprint"] = "FFFF-FFFF-FFFF-FFFF-FFFF"
        p.write_text(json.dumps(o))
        p.chmod(0o600)
        res = self.b.cli("send", "to-a", "hello", "--timeout", "30", CORVIN_LICENSE_KEY=_RS256_TOKEN)
        self._assert_receiver_refused(res, "out_of_band_attestation_invalid")

    def test_f8_a_malformed_ledger_refuses_the_confirm(self):
        imp = self._pending_pair()
        led = self.a.home / "tenants" / "_default" / "global" / "a2a" / "offline_pair_fingerprints.json"
        led.parent.mkdir(parents=True, exist_ok=True)
        led.write_text(json.dumps({"fp": []}))
        r = self._a_confirms(imp["instance_id"], imp["fingerprint"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ledger", r.stderr)
        self.assertEqual(json.loads((self.a.origins / "peer-b.json").read_text())["oob_state"], "pending")

    def test_f9_a_replayed_endpoint_file_under_another_instance_id_is_refused_by_key_id(self):
        self._full_pair()
        old = json.loads((self.b.endpoints / "to-a.json").read_text())
        replay = dict(old, enabled=False, oob_state="pending", instance_id="iid-somebody-else",
                      endpoint_id="to-x")
        for k in ("attestation", "oob_fingerprint", "oob_confirmed_at"):
            replay.pop(k, None)
        f = self.b.root / "replay.json"
        f.write_text(json.dumps(replay))
        r = self.b.cli("pair-import", str(f), "--json")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("already used", r.stderr)
        self.assertFalse((self.b.endpoints / "to-x.json").exists())

    def test_f10_confirm_waits_for_the_config_lock(self):
        imp = self._pending_pair()
        with a2a_friendship.config_file_lock(self.a.origins):
            r = self._a_confirms(imp["instance_id"], imp["fingerprint"])
        self.assertNotEqual(r.returncode, 0, "a held config lock must not be ignored")
        self.assertIn("busy", r.stderr.lower())
        self.assertEqual(json.loads((self.a.origins / "peer-b.json").read_text())["oob_state"], "pending")
        # and nothing was spent: the same confirm succeeds once the lock is free
        r = self._a_confirms(imp["instance_id"], imp["fingerprint"])
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])

    def test_f10_a_confirm_interrupted_after_the_ledger_row_can_be_completed(self):
        imp = self._pending_pair()
        p = self.a.origins / "peer-b.json"
        pending = p.read_text()
        r = self._a_confirms(imp["instance_id"], imp["fingerprint"])
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        # as if the process died between the ledger reservation and the file write: the file
        # is still pending and the row still `reserved` (round 3: only such a row resumes)
        p.write_text(pending)
        p.chmod(0o600)
        led_p = self.a.home / "tenants" / "_default" / "global" / "a2a" / "offline_pair_fingerprints.json"
        led = json.loads(led_p.read_text())
        led["fingerprints"][0]["status"] = "reserved"
        led_p.write_text(json.dumps(led))
        r = self._a_confirms(imp["instance_id"], imp["fingerprint"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr[-2000:])
        self.assertEqual(json.loads(p.read_text())["oob_state"], "confirmed")
        # the resume is for THIS pair only: another peer name with the same keys is still a reuse
        other = json.loads(pending)
        other["origin_id"] = "peer-c"
        q = self.a.origins / "peer-c.json"
        q.write_text(json.dumps(other))
        q.chmod(0o600)
        r = self.a.cli("pair-confirm", "peer-c", "--peer-instance-id", imp["instance_id"],
                       "--fingerprint", imp["fingerprint"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("already used", r.stderr)


class RefutationRoundE2E(_PairHarness):
    """Round 3: a COMPLETED pairing is never resumed; the ledger lock fails closed."""

    def _pending_pair(self) -> dict:
        return self._step2_b_imports(self._step1_a_pairs())

    def test_hand_copied_printed_file_after_a_completed_confirm_is_a_reuse(self):
        # exactly /tmp/review-t0311/f10_cli.sh: pair, import, confirm, delete, hand-copy, confirm
        res = self.a.cli("pair", "peer-b", "http://127.0.0.1:9/", "--offline-pair")
        self.assertEqual(res.returncode, 0, res.stderr[-2000:])
        printed = _json_tail(res.stdout)
        f = self.b.root / "ep.json"
        f.write_text(json.dumps(printed))
        r = self.b.cli("pair-import", str(f), "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        imp = _json_tail(r.stdout)
        ep_id, fp = imp["endpoint_id"], imp["fingerprint"]
        r = self.b.cli("pair-confirm", ep_id, "--fingerprint", fp)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        target = self.b.endpoints / f"{ep_id}.json"
        target.unlink()
        r = self.b.cli("pair-import", str(f), "--force")
        self.assertNotEqual(r.returncode, 0, "re-import of a used file is refused")
        target.write_text(json.dumps(printed))          # the printed file is already `pending`
        target.chmod(0o600)
        r = self.b.cli("pair-confirm", ep_id, "--fingerprint", fp)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("already used", r.stderr)
        cfg = json.loads(target.read_text())
        self.assertEqual(cfg["oob_state"], "pending")
        self.assertFalse(cfg["enabled"])
        refused = self.b.records("a2a.offline_pair_refused")
        self.assertEqual(refused[-1]["details"]["reason"], "fingerprint_reused")

    def _ledger(self) -> Path:
        return self.a.home / "tenants" / "_default" / "global" / "a2a" / "offline_pair_fingerprints.json"

    def test_a_reserved_row_resumes_and_a_completed_row_does_not(self):
        imp = self._pending_pair()
        p = self.a.origins / "peer-b.json"
        pending = p.read_text()
        r = self._a_confirms(imp["instance_id"], imp["fingerprint"])
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        rows = json.loads(self._ledger().read_text())["fingerprints"]
        self.assertEqual([row["status"] for row in rows], ["completed"])
        # crash AFTER the completed mark: putting the pending file back is a replay, not a resume
        p.write_text(pending)
        p.chmod(0o600)
        r = self._a_confirms(imp["instance_id"], imp["fingerprint"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("already used", r.stderr)
        # crash BETWEEN reserve and the file write: the row is `reserved` -> resumable
        led = json.loads(self._ledger().read_text())
        led["fingerprints"][0]["status"] = "reserved"
        self._ledger().write_text(json.dumps(led))
        r = self._a_confirms(imp["instance_id"], imp["fingerprint"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr[-2000:])
        self.assertEqual(json.loads(p.read_text())["oob_state"], "confirmed")
        rows = json.loads(self._ledger().read_text())["fingerprints"]
        self.assertEqual([row["status"] for row in rows], ["completed"])

    def test_an_unavailable_ledger_lock_refuses_the_confirm(self):
        imp = self._pending_pair()
        lock = self._ledger().parent / ".offline_pair_ledger.lock"
        lock.mkdir(parents=True)                           # a lock file that cannot be opened
        r = self._a_confirms(imp["instance_id"], imp["fingerprint"])
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("lock", r.stderr)
        cfg = json.loads((self.a.origins / "peer-b.json").read_text())
        self.assertEqual(cfg["oob_state"], "pending")
        self.assertFalse(cfg["enabled"])


if __name__ == "__main__":
    unittest.main()
