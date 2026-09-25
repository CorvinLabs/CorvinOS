"""A2A feed content store (a2a_feed.py) — both sides of a real exchange.

A real RemoteTriggerSender sends over real HTTP to a real
RemoteTriggerReceiver (the harness from test_remote_trigger_sender.py). Both
live in this process and share one feed dir, so one exchange must leave all
four records: out/task + in/task (receiver) + out/response (receiver) +
in/response (sender).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

import test_remote_trigger_sender as h  # harness + module-level forge mocks

import a2a_feed
import remote_trigger_sender as rts

PNG = b"\x89PNG\r\n\x1a\n" + b"feed-test-image" * 20


def _png_att() -> dict:
    return {"name": "shot.png", "mime": "image/png",
            "sha256": hashlib.sha256(PNG).hexdigest(),
            "content_b64": base64.b64encode(PNG).decode()}


class _Harness(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.origins_dir = root / "origins"
        self.endpoints_dir = root / "endpoints"
        self.feed = root / "feed"
        for d in (self.origins_dir, self.endpoints_dir):
            d.mkdir()
        self._env = {k: os.environ.get(k) for k in (
            "CORVIN_INSTANCE_ID_PATH", "CORVIN_A2A_ATTESTATION_DISABLED", "CORVIN_A2A_FEED_DIR")}
        os.environ["CORVIN_INSTANCE_ID_PATH"] = str(root / "iid.json")
        os.environ["CORVIN_A2A_ATTESTATION_DISABLED"] = "1"
        os.environ["CORVIN_A2A_FEED_DIR"] = str(self.feed)
        h._write_origin_file(self.origins_dir, h.ORIGIN_ID)
        self.server = h._FakeReceiverServer(self.origins_dir, instance_id="recv-iid")
        self.server.start()
        h._write_endpoint_file(self.endpoints_dir, h.ENDPOINT_ID, self.server.url)

    def tearDown(self) -> None:
        self.server.stop()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()


class TestExchangeRecordsBothSides(_Harness):

    def test_roundtrip_with_image_leaves_four_records(self):
        sender = rts.RemoteTriggerSender(endpoints_dir=self.endpoints_dir)
        result = sender.send(h.ENDPOINT_ID, "describe this", attachments=[_png_att()])
        self.assertTrue(result.ok, result)

        msgs = a2a_feed.read(limit=0)
        shape = [(m["direction"], m["kind"]) for m in msgs]
        self.assertEqual(shape, [("out", "task"), ("in", "task"), ("out", "response"), ("in", "response")])
        self.assertTrue(all(m["task_id"] == result.task_id for m in msgs))

        out_task, in_task = msgs[0], msgs[1]
        self.assertEqual(out_task["text"], "describe this")
        self.assertEqual(in_task["text"], "describe this")
        self.assertEqual(in_task["peer_id"], h.ORIGIN_ID)
        sha = hashlib.sha256(PNG).hexdigest()
        self.assertEqual([a["sha256"] for a in in_task["attachments"]], [sha])
        self.assertEqual(msgs[3]["status"], "ok")

        blob = a2a_feed.blob_path(sha)
        self.assertIsNotNone(blob)
        self.assertEqual(blob.read_bytes(), PNG)
        self.assertEqual(len(list((self.feed / "blobs").iterdir())), 1, "blobs must dedupe")
        for p in (blob, self.feed / "messages.jsonl"):
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)

    def test_unauthenticated_envelope_never_reaches_the_store(self):
        cfg_path = self.endpoints_dir / f"{h.ENDPOINT_ID}.json"
        cfg = json.loads(cfg_path.read_text())
        cfg["hmac_key"] = "3" * 64  # receiver does not know this key
        cfg_path.write_text(json.dumps(cfg))
        cfg_path.chmod(0o600)

        result = rts.RemoteTriggerSender(endpoints_dir=self.endpoints_dir).send(
            h.ENDPOINT_ID, "forged instruction")
        self.assertFalse(result.ok)
        msgs = a2a_feed.read(limit=0)
        # Sender side keeps its own attempt + the failure; the receiver stored nothing.
        self.assertEqual([(m["direction"], m["kind"]) for m in msgs], [("out", "task"), ("in", "response")])
        self.assertNotEqual(msgs[1]["status"], "ok")

    def test_broken_store_never_breaks_a_send(self):
        blocker = Path(self._tmp.name) / "not-a-dir"
        blocker.write_text("x")
        os.environ["CORVIN_A2A_FEED_DIR"] = str(blocker)
        result = rts.RemoteTriggerSender(endpoints_dir=self.endpoints_dir).send(h.ENDPOINT_ID, "still works")
        self.assertTrue(result.ok)


class TestRetention(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = os.environ.get("CORVIN_A2A_FEED_DIR")
        os.environ["CORVIN_A2A_FEED_DIR"] = self._tmp.name

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("CORVIN_A2A_FEED_DIR", None)
        else:
            os.environ["CORVIN_A2A_FEED_DIR"] = self._prev
        self._tmp.cleanup()

    def test_compaction_drops_expired_records_and_orphan_blobs(self):
        old = a2a_feed.record(direction="in", kind="task", peer_id="p", task_id="old",
                              text="old", attachments=[_png_att()])
        a2a_feed.record(direction="in", kind="task", peer_id="p", task_id="new", text="new")
        root = Path(self._tmp.name)
        lines = (root / "messages.jsonl").read_text().splitlines()
        rec = json.loads(lines[0])
        rec["ts"] = time.time() - (a2a_feed.RETENTION_DAYS + 1) * 86400
        (root / "messages.jsonl").write_text(json.dumps(rec) + "\n" + lines[1] + "\n")

        self.assertEqual([m["task_id"] for m in a2a_feed.read(limit=0)], ["new"])
        removed, blobs = a2a_feed.compact(root)
        self.assertEqual((removed, blobs), (1, 1))
        self.assertIsNone(a2a_feed.blob_path(old["attachments"][0]["sha256"]))

    def test_blob_path_refuses_non_digest(self):
        for bad in ("../../etc/passwd", "a" * 63, "Z" * 64, ""):
            self.assertIsNone(a2a_feed.blob_path(bad))

    def test_clear_removes_everything(self):
        a2a_feed.record(direction="out", kind="task", peer_id="p", task_id="t",
                        text="x", attachments=[_png_att()])
        self.assertEqual(a2a_feed.clear(), (1, 1))
        self.assertEqual(a2a_feed.read(limit=0), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
