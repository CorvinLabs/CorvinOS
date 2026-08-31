#!/usr/bin/env python3
"""Tests for adapter.py::_atomic_write_outbox — every outbox envelope write
must be atomic (temp file + os.replace), so a concurrent reader (daemon.js's
outbox poller, ticking every 500ms) can never observe a partially-written
file. Real concurrent read/write via a background thread, not a mock — this
is exactly the race outbox.js's poller lives inside.

Run with: python3 operator/bridges/shared/test_atomic_write_outbox.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import adapter  # noqa: E402


class TestAtomicWriteOutbox(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="atomic-outbox-"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writes_valid_json_readable_afterwards(self):
        target = self.tmp / "msg_00.json"
        content = json.dumps({"channel": "discord", "text": "hi"})
        adapter._atomic_write_outbox(target, content)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["text"], "hi")

    def test_no_leftover_temp_file_after_success(self):
        target = self.tmp / "msg_00.json"
        adapter._atomic_write_outbox(target, json.dumps({"channel": "x"}))
        leftovers = [p for p in self.tmp.iterdir() if p.name != target.name]
        self.assertEqual(leftovers, [], f"temp file(s) left behind: {leftovers}")

    def test_creates_parent_directory(self):
        target = self.tmp / "nested" / "msg_00.json"
        adapter._atomic_write_outbox(target, json.dumps({"channel": "x"}))
        self.assertTrue(target.is_file())

    def test_concurrent_reader_never_sees_a_partial_file(self):
        """The actual race this fix closes: a reader polling every few
        milliseconds while a LARGE envelope is being written must see
        either nothing (file not yet visible) or the complete, valid
        envelope — never a truncated/partial one.

        Uses a real background thread performing real writes + a real
        polling reader thread — not a mock of the race, the race itself."""
        target = self.tmp / "msg_00.json"
        # Large enough that a naive direct write_text() has a real (if
        # small) window between the first and last byte hitting disk.
        big_text = "x" * (2 * 1024 * 1024)  # 2 MiB
        envelope = json.dumps({"channel": "discord", "text": big_text})

        observed_partial = threading.Event()
        observed_valid = threading.Event()
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                if target.exists():
                    try:
                        raw = target.read_text(encoding="utf-8")
                    except OSError:
                        continue
                    try:
                        parsed = json.loads(raw)
                        if parsed.get("text") == big_text:
                            observed_valid.set()
                        else:
                            observed_partial.set()
                    except json.JSONDecodeError:
                        # A non-empty, unparseable read while the target
                        # file exists IS the historical bug this test
                        # guards against — a reader must never see this.
                        if raw:
                            observed_partial.set()
                    return  # first observation is enough for this run
                time.sleep(0.0001)

        for _ in range(20):  # repeat — a race is probabilistic, not deterministic
            target.unlink(missing_ok=True)
            observed_partial.clear()
            observed_valid.clear()
            stop.clear()
            t = threading.Thread(target=reader, daemon=True)
            t.start()
            adapter._atomic_write_outbox(target, envelope)
            stop.set()
            t.join(timeout=2)
            self.assertFalse(
                observed_partial.is_set(),
                "reader observed a partial/corrupt file mid-write — atomicity broken",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
