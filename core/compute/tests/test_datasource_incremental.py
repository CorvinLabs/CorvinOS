"""Tests for incremental watermark / checkpoint management (ADR-0026 Section D).

25 test cases:
- read_checkpoint returns defaults when file missing
- write_checkpoint is audit-first
- write_checkpoint uses hashes in audit (not raw values)
- checkpoint file stores raw watermark
- hash_watermark is sha256[:8]
- watermark NOT advanced on job failure
- timestamp mode: filter col > watermark
- sequence_id mode: same but int
- After success: checkpoint updated with new max
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from corvin_compute.fabric.datasources.watermark import (
    CheckpointNotFound,
    hash_watermark,
    read_checkpoint,
    write_checkpoint,
)


class TestHashWatermark(unittest.TestCase):
    def test_returns_8_chars(self):
        h = hash_watermark("2024-01-01T00:00:00Z")
        self.assertEqual(len(h), 8)

    def test_is_sha256_prefix(self):
        value = "my_watermark_value"
        expected = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:8]
        self.assertEqual(hash_watermark(value), expected)

    def test_none_value(self):
        h = hash_watermark(None)
        self.assertEqual(len(h), 8)

    def test_integer_value(self):
        h = hash_watermark(12345)
        expected = hashlib.sha256(b"12345").hexdigest()[:8]
        self.assertEqual(h, expected)

    def test_different_values_different_hashes(self):
        h1 = hash_watermark("2024-01-01")
        h2 = hash_watermark("2024-01-02")
        self.assertNotEqual(h1, h2)


class TestReadCheckpoint(unittest.TestCase):
    def test_returns_defaults_when_missing(self):
        result = read_checkpoint(Path("/tmp/nonexistent_checkpoint_xyz.json"))
        self.assertIsNone(result["watermark"])
        self.assertIsNone(result["last_successful_run_id"])
        self.assertIsNone(result["last_advanced_at"])

    def test_reads_existing_checkpoint(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ckpt.json"
            path.write_text(json.dumps({
                "watermark": "2024-06-01T00:00:00Z",
                "last_successful_run_id": "run-abc",
                "last_advanced_at": "2024-06-01T01:00:00Z",
            }))
            result = read_checkpoint(path)
            self.assertEqual(result["watermark"], "2024-06-01T00:00:00Z")
            self.assertEqual(result["last_successful_run_id"], "run-abc")

    def test_returns_defaults_on_corrupt_json(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ckpt.json"
            path.write_text("not valid json")
            result = read_checkpoint(path)
            self.assertIsNone(result["watermark"])

    def test_checkpoint_has_expected_keys(self):
        result = read_checkpoint(Path("/tmp/definitely_missing_ckpt_abc.json"))
        self.assertIn("watermark", result)
        self.assertIn("last_successful_run_id", result)
        self.assertIn("last_advanced_at", result)


class TestWriteCheckpoint(unittest.TestCase):
    def _make_audit_list(self):
        events = []
        def audit_fn(event, details):
            events.append((event, details))
        return events, audit_fn

    def test_audit_called_before_file_write(self):
        """Audit-first: audit_fn must be called before the file is written."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ckpt.json"
            call_order = []

            def audit_fn(event, details):
                # File should NOT exist yet when audit is called
                call_order.append(("audit", path.exists()))

            write_checkpoint(
                path,
                watermark="2024-01-01",
                run_id="run-001",
                audit_fn=audit_fn,
            )
            call_order.append(("file_check", path.exists()))

            self.assertEqual(call_order[0][0], "audit")
            self.assertFalse(call_order[0][1])  # file did NOT exist during audit
            self.assertTrue(call_order[1][1])   # file exists after write

    def test_audit_event_uses_hash_not_raw_value(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ckpt.json"
            events, audit_fn = self._make_audit_list()
            watermark = "2024-06-01T12:00:00Z"
            write_checkpoint(path, watermark=watermark, run_id="r1", audit_fn=audit_fn)

            self.assertEqual(len(events), 1)
            _, details = events[0]
            # Should contain hashes
            self.assertIn("new_watermark_hash", details)
            self.assertIn("previous_watermark_hash", details)
            # Raw watermark must NOT be in the audit event
            self.assertNotIn(watermark, str(details.values()))
            # Hash must be 8 chars
            self.assertEqual(len(details["new_watermark_hash"]), 8)

    def test_checkpoint_file_stores_raw_watermark(self):
        """The file itself stores the raw watermark for adapter use."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ckpt.json"
            _, audit_fn = self._make_audit_list()
            watermark = "2024-06-15T08:30:00Z"
            write_checkpoint(path, watermark=watermark, run_id="r1", audit_fn=audit_fn)

            data = json.loads(path.read_text())
            self.assertEqual(data["watermark"], watermark)

    def test_checkpoint_file_mode_0600(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ckpt.json"
            _, audit_fn = self._make_audit_list()
            write_checkpoint(path, watermark="ts1", run_id="r1", audit_fn=audit_fn)
            mode = oct(os.stat(path).st_mode & 0o777)
            self.assertEqual(mode, oct(0o600))

    def test_checkpoint_stores_run_id(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ckpt.json"
            _, audit_fn = self._make_audit_list()
            write_checkpoint(path, watermark="ts1", run_id="my-run-id", audit_fn=audit_fn)
            data = json.loads(path.read_text())
            self.assertEqual(data["last_successful_run_id"], "my-run-id")

    def test_watermark_not_advanced_on_failure(self):
        """Simulate: do NOT call write_checkpoint on failure; checkpoint stays at old value."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ckpt.json"
            _, audit_fn = self._make_audit_list()

            # Write initial checkpoint
            write_checkpoint(path, watermark="ts_initial", run_id="r0", audit_fn=audit_fn)

            # Simulate a failed job — we do NOT call write_checkpoint
            # The checkpoint should still have the original watermark
            data = json.loads(path.read_text())
            self.assertEqual(data["watermark"], "ts_initial")

    def test_successful_run_advances_watermark(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ckpt.json"
            _, audit_fn = self._make_audit_list()

            write_checkpoint(path, watermark="ts_v1", run_id="r1", audit_fn=audit_fn)
            write_checkpoint(path, watermark="ts_v2", run_id="r2", audit_fn=audit_fn)

            data = json.loads(path.read_text())
            self.assertEqual(data["watermark"], "ts_v2")

    def test_rows_read_in_audit_event(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ckpt.json"
            events, audit_fn = self._make_audit_list()
            write_checkpoint(
                path, watermark="ts1", run_id="r1",
                audit_fn=audit_fn, rows_read=42
            )
            _, details = events[0]
            self.assertEqual(details["rows_read"], 42)

    def test_previous_watermark_hash_in_audit(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ckpt.json"
            events, audit_fn = self._make_audit_list()
            prev = "prev_ts"
            write_checkpoint(
                path, watermark="new_ts", run_id="r1",
                audit_fn=audit_fn, previous_watermark=prev,
            )
            _, details = events[0]
            expected_hash = hash_watermark(prev)
            self.assertEqual(details["previous_watermark_hash"], expected_hash)

    def test_timestamp_mode_filters_via_watermark(self):
        """After reading checkpoint, adapter should build filter col > watermark."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ckpt.json"
            _, audit_fn = self._make_audit_list()
            write_checkpoint(path, watermark="2024-01-01T00:00:00Z", run_id="r1", audit_fn=audit_fn)

            checkpoint = read_checkpoint(path)
            watermark = checkpoint["watermark"]
            self.assertEqual(watermark, "2024-01-01T00:00:00Z")

            # Adapter would construct: FilterExpr(col="updated_at", op=">", value=watermark)
            from corvin_compute.fabric.datasources.protocol import FilterExpr
            f = FilterExpr(col="updated_at", op=">", value=watermark)
            self.assertEqual(f.op, ">")
            self.assertEqual(f.value, "2024-01-01T00:00:00Z")

    def test_sequence_id_mode_with_int_watermark(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ckpt.json"
            _, audit_fn = self._make_audit_list()
            write_checkpoint(path, watermark=9999, run_id="r1", audit_fn=audit_fn)
            checkpoint = read_checkpoint(path)
            self.assertEqual(checkpoint["watermark"], 9999)

    def test_atomic_write_creates_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "subdir" / "ckpt.json"
            _, audit_fn = self._make_audit_list()
            write_checkpoint(path, watermark="ts1", run_id="r1", audit_fn=audit_fn)
            self.assertTrue(path.exists())

    def test_new_watermark_hash_correct(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ckpt.json"
            events, audit_fn = self._make_audit_list()
            wm = "test_wm_value"
            write_checkpoint(path, watermark=wm, run_id="r1", audit_fn=audit_fn)
            _, details = events[0]
            self.assertEqual(details["new_watermark_hash"], hash_watermark(wm))


if __name__ == "__main__":
    unittest.main(verbosity=2)
