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
from unittest import mock
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


# ── concurrency (2026-09-25 adversarial review of the feed store) ─────────

_WRITER = r"""
import os, sys, base64
sys.path.insert(0, sys.argv[1])
import a2a_feed
img = base64.b64encode(b"same-image-for-every-peer" * 400).decode()
for i in range(int(sys.argv[3])):
    r = a2a_feed.record(direction="out", kind="task", peer_id=sys.argv[2], task_id=f"{sys.argv[2]}-{i}",
                        text="x", attachments=[{"name": "p.png", "mime": "image/png", "content_b64": img}])
    assert r is not None, "record() failed"
"""

_COMPACTOR = r"""
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import a2a_feed
end = time.time() + float(sys.argv[2])
while time.time() < end:
    a2a_feed.compact(Path(a2a_feed.feed_dir()))
    a2a_feed.MAX_FEED_BYTES  # keep import live
"""


class TestConcurrentStore(unittest.TestCase):
    """Several PROCESSES write, one compacts, one polls with the seq cursor."""

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

    def test_multi_process_writers_with_compaction_lose_nothing(self):
        import subprocess
        import sys as _sys
        here = str(Path(__file__).resolve().parent)
        n_proc, per = 6, 40
        comp = subprocess.Popen([_sys.executable, "-c", _COMPACTOR, here, "6"])
        writers = [subprocess.Popen([_sys.executable, "-c", _WRITER, here, f"peer{i}", str(per)])
                   for i in range(n_proc)]
        # Poll like the UI does, concurrently with the writers.
        seen: list[dict] = []
        cursor = 0
        while any(w.poll() is None for w in writers):
            page, _more = a2a_feed.read_page(after=cursor, limit=50)
            seen.extend(page)
            if page:
                cursor = max(int(m["seq"]) for m in page)
            time.sleep(0.01)
        for w in writers:
            self.assertEqual(w.wait(), 0)
        comp.wait()
        while True:
            page, more = a2a_feed.read_page(after=cursor, limit=50)
            seen.extend(page)
            if page:
                cursor = max(int(m["seq"]) for m in page)
            if not more:
                break

        stored = a2a_feed.read(limit=0)
        self.assertEqual(len(stored), n_proc * per, "records lost under concurrent writers + compaction")
        seqs = [int(m["seq"]) for m in stored]
        self.assertEqual(seqs, sorted(seqs), "file order != seq order")
        self.assertEqual(len(set(seqs)), len(seqs), "duplicate seq")
        self.assertEqual(sorted(m["id"] for m in seen), sorted(m["id"] for m in stored),
                         "the after-cursor skipped messages")
        # Every referenced blob survived the concurrent compactions.
        for m in stored:
            for a in m["attachments"]:
                self.assertIsNotNone(a2a_feed.blob_path(a["sha256"]))

    def test_after_returns_oldest_page_and_has_more(self):
        for i in range(7):
            a2a_feed.record(direction="in", kind="task", peer_id="p", task_id=f"t{i}", text=str(i))
        page, more = a2a_feed.read_page(after=0, limit=3)
        self.assertEqual([m["text"] for m in page], ["0", "1", "2"])
        self.assertTrue(more)
        page, more = a2a_feed.read_page(after=page[-1]["seq"], limit=10)
        self.assertEqual([m["text"] for m in page], ["3", "4", "5", "6"])
        self.assertFalse(more)
        older, more = a2a_feed.read_page(before=page[0]["seq"], limit=2)
        self.assertEqual([m["text"] for m in older], ["1", "2"])
        self.assertTrue(more)

    def test_clear_is_not_undone_by_a_concurrent_compaction(self):
        import threading
        for i in range(50):
            a2a_feed.record(direction="in", kind="task", peer_id="p", task_id=f"s{i}", text="secret")
        root = Path(self._tmp.name)
        stop = threading.Event()

        def compactor():
            while not stop.is_set():
                a2a_feed.compact(root)

        t = threading.Thread(target=compactor)
        t.start()
        try:
            a2a_feed.clear()
        finally:
            stop.set()
            t.join()
        self.assertEqual(a2a_feed.read(limit=0), [])

    def test_seq_survives_clear(self):
        a = a2a_feed.record(direction="in", kind="task", peer_id="p", task_id="a", text="a")
        a2a_feed.clear()
        b = a2a_feed.record(direction="in", kind="task", peer_id="p", task_id="b", text="b")
        self.assertGreater(b["seq"], a["seq"], "a client cursor must stay valid across clear()")


class TestLegacyAndBlobBudget(TestConcurrentStore):
    """Round 2: seq-less records written by the first store version, and the
    attachment bytes the line-size cap never counted."""

    def test_legacy_records_get_seq_and_history_pages_through_them(self):
        root = Path(self._tmp.name)
        lines = [json.dumps({"id": f"L{i}", "ts": time.time() - 100 + i, "direction": "in",
                             "kind": "task", "peer_id": "p", "task_id": f"l{i}", "text": str(i),
                             "attachments": []}) for i in range(30)]
        root.mkdir(parents=True, exist_ok=True)
        (root / "messages.jsonl").write_text("\n".join(lines) + "\n")
        newest, more = a2a_feed.read_page(limit=10)
        self.assertTrue(more)
        seen = [m["id"] for m in newest]
        while more:
            page, more = a2a_feed.read_page(before=min(m["seq"] for m in newest), limit=10)
            self.assertTrue(page, "Load older made no progress")
            seen = [m["id"] for m in page] + seen
            newest = page
        self.assertEqual(seen, [f"L{i}" for i in range(30)])
        rec = a2a_feed.record(direction="out", kind="task", peer_id="p", task_id="n", text="new")
        self.assertEqual(rec["seq"], 31, "the counter continues after the migrated records")

    def test_blob_budget_evicts_oldest_messages_and_their_blobs(self):
        with mock.patch.object(a2a_feed, "MAX_BLOB_BYTES", 250_000):
            for i in range(5):
                raw = os.urandom(100_000)
                a2a_feed.record(direction="in", kind="task", peer_id="p", task_id=f"b{i}", text=str(i),
                                attachments=[{"name": f"f{i}.bin", "mime": "application/octet-stream",
                                              "content_b64": base64.b64encode(raw).decode()}])
            a2a_feed.compact(Path(self._tmp.name))
            kept = a2a_feed.read(limit=0)
            blob_bytes = sum(p.stat().st_size for p in (Path(self._tmp.name) / "blobs").iterdir())
        # Over the cap → trimmed to the low-water mark (half the cap).
        self.assertLessEqual(blob_bytes, 125_000)
        self.assertEqual([m["text"] for m in kept], ["4"], "the newest messages are kept")


class TestRound3Feed(TestConcurrentStore):
    def test_seqless_records_appended_after_migration_are_delivered_live(self):
        a = a2a_feed.record(direction="in", kind="task", peer_id="p", task_id="a", text="a")
        root = Path(self._tmp.name)
        with open(root / "messages.jsonl", "a", encoding="utf-8") as fh:  # an old-code writer
            fh.write(json.dumps({"id": "old", "ts": time.time(), "direction": "in", "kind": "task",
                                 "peer_id": "p", "task_id": "o", "text": "old-writer",
                                 "attachments": []}) + "\n")
        page, _more = a2a_feed.read_page(after=a["seq"], limit=10)
        self.assertEqual([m["text"] for m in page], ["old-writer"])
        self.assertGreater(page[0]["seq"], a["seq"])

    def test_blob_budget_has_a_low_water_mark(self):
        calls = []
        real = a2a_feed.compact
        with mock.patch.object(a2a_feed, "MAX_BLOB_BYTES", 300_000), \
             mock.patch.object(a2a_feed, "compact", side_effect=lambda r: calls.append(1) or real(r)), \
             mock.patch.object(a2a_feed, "_COMPACT_INTERVAL_S", 10**9):
            a2a_feed._last_compact[str(Path(self._tmp.name))] = time.time()
            for i in range(20):
                a2a_feed.record(direction="in", kind="task", peer_id="p", task_id=f"x{i}", text=str(i),
                                attachments=[{"name": f"f{i}.bin", "mime": "application/octet-stream",
                                              "content_b64": base64.b64encode(os.urandom(50_000)).decode()}])
        # 20×50 KB against a 300 KB cap: with the half-cap low-water mark a
        # compaction every 3–4 writes (~6–8); without it one per write (~14+).
        self.assertLessEqual(len(calls), 9, f"compaction ran {len(calls)}x for 20 writes (no low-water mark)")


class TestRound4SeqOverrides(TestConcurrentStore):
    """Round 4: seq-less records get their seq from the SAME counter via a
    sidecar map — the read path never rewrites messages.jsonl (an old-version
    writer appending meanwhile lost records into the unlinked inode) and the
    counter never goes backwards (clients kept a cursor above it)."""

    def _old_writer_line(self, rid: str, text: str) -> None:
        root = Path(self._tmp.name)
        with open(root / "messages.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": rid, "ts": time.time(), "direction": "in", "kind": "task",
                                 "peer_id": "p", "task_id": rid, "text": text, "attachments": []}) + "\n")

    def test_counter_never_goes_backwards_after_clear(self):
        last = None
        for i in range(3):
            last = a2a_feed.record(direction="in", kind="task", peer_id="p", task_id=f"t{i}", text=str(i))
        cursor = last["seq"]
        a2a_feed.clear()
        self._old_writer_line("old-1", "head")
        a2a_feed.record(direction="in", kind="task", peer_id="p", task_id="n1", text="new1")
        page, _ = a2a_feed.read_page(after=cursor, limit=10)
        # Both are delivered past the client's cursor. (A seq-less line gets
        # its seq when first read, so it may sort after a later new-code
        # write — ordering among transitional records is not guaranteed,
        # delivery is.)
        self.assertEqual(sorted(m["text"] for m in page), ["head", "new1"])
        self.assertTrue(all(m["seq"] > cursor for m in page))
        self.assertEqual(len({m["seq"] for m in page}), 2)

    def test_read_path_never_replaces_the_data_file(self):
        a2a_feed.record(direction="in", kind="task", peer_id="p", task_id="a", text="a")
        self._old_writer_line("old-2", "legacy")
        path = Path(self._tmp.name) / "messages.jsonl"
        ino = path.stat().st_ino
        page, _ = a2a_feed.read_page(limit=10)
        self.assertEqual(path.stat().st_ino, ino, "a read replaced messages.jsonl")
        self.assertEqual(sorted(m["seq"] for m in page), [m["seq"] for m in page])
        # compaction folds the sidecar into the records
        a2a_feed.compact(Path(self._tmp.name))
        lines = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        self.assertTrue(all(r.get("seq") for r in lines))


class TestRound5ReaderStraddlingCompaction(TestConcurrentStore):
    def test_reader_that_overlaps_compaction_still_delivers_the_seqless_record(self):
        root = Path(self._tmp.name)
        a2a_feed._last_compact[str(root)] = time.time()
        a2a_feed.record(direction="out", kind="task", peer_id="p", task_id="t1", text="A")
        with open(root / "messages.jsonl", "a") as fh:
            fh.write(json.dumps({"id": "x-legacy", "ts": time.time(), "direction": "in", "kind": "task",
                                 "peer_id": "p", "task_id": "tx", "text": "X", "attachments": []}) + "\n")
        a2a_feed.read_page()                       # assigns X an override seq
        a2a_feed.record(direction="out", kind="task", peer_id="p", task_id="t2", text="B")
        real = a2a_feed._load_overrides
        fired = []

        def straddle(r):                            # compaction between the reader's two reads
            if not fired:
                fired.append(1)
                a2a_feed.compact(r)
            return real(r)

        with mock.patch.object(a2a_feed, "_load_overrides", side_effect=straddle):
            rows, _ = a2a_feed.read_page(after=1)
        self.assertEqual(sorted(r["text"] for r in rows), ["B", "X"])
        self.assertTrue(all(r.get("seq") for r in rows))


class TestRound7FairBudgets(TestConcurrentStore):
    def test_one_flooding_peer_cannot_evict_the_others(self):
        a2a_feed.record(direction="in", kind="task", peer_id="peerA", task_id="a1", text="hello from A")
        with mock.patch.object(a2a_feed, "MAX_FEED_BYTES", 400_000), \
             mock.patch.object(a2a_feed, "_COMPACT_INTERVAL_S", 10**9):
            a2a_feed._last_compact[str(Path(self._tmp.name))] = time.time()
            for i in range(60):
                a2a_feed.record(direction="in", kind="task", peer_id="peerB", task_id=f"b{i}",
                                text="x" * 20_000)
            a2a_feed.compact(Path(self._tmp.name))
        peers = [m["peer_id"] for m in a2a_feed.read(limit=0)]
        self.assertIn("peerA", peers, "the flooding peer evicted another peer's conversation")
        self.assertLess(peers.count("peerB"), 60)
