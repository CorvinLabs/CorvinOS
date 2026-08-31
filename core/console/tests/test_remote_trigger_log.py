"""Tests for the L38 console route helpers — A2A audit-chain projection."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# Layout: core/console/tests/test_*.py — parents[0] is core/console.
_CONSOLE_PARENT = _HERE.parent  # core/console
if str(_CONSOLE_PARENT) not in sys.path:
    sys.path.insert(0, str(_CONSOLE_PARENT))
# Plugin install path used by other console tests — added for parity.
_REPO = _HERE.parents[2]
_PLUGIN_PARENT = _REPO / "plugins" / "core" / "console"
if _PLUGIN_PARENT.is_dir() and str(_PLUGIN_PARENT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_PARENT))

from corvin_console.routes import remote_trigger_log as rtl  # type: ignore[import-not-found]


def _write_chain(path: Path, events: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")


def _ev(et: str, *, ts: float = 1700000000.0,
        severity: str = "INFO", **details) -> dict:
    return {
        "ts": ts,
        "event_type": et,
        "severity": severity,
        "details": details,
        "hash": "x" * 64,
        "prev_hash": "y" * 64,
    }


class TestProjectEvent(unittest.TestCase):

    def test_curated_keys_only(self):
        rec = _ev("A2A.envelope_received", task_id="t1",
                  origin_id="o1", instruction="SECRET")
        proj = rtl._project_event(rec)
        # Curated keys present
        self.assertEqual(proj["task_id"], "t1")
        self.assertEqual(proj["origin_id"], "o1")
        # Chain integrity internals NOT present
        self.assertNotIn("hash", proj)
        self.assertNotIn("prev_hash", proj)
        # Allow-list also strips smuggled instruction etc.
        self.assertNotIn("instruction", proj)

    def test_missing_details_handled(self):
        rec = {"ts": 0, "event_type": "A2A.foo", "severity": "INFO"}
        proj = rtl._project_event(rec)
        self.assertIsNone(proj["task_id"])


class TestLoadEvents(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.chain = Path(self._tmp.name) / "chain.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_only_a2a_events_returned(self):
        _write_chain(self.chain, [
            _ev("A2A.envelope_received", origin_id="o1"),
            _ev("gateway.token_resolved"),
            _ev("A2A.engine_spawned", origin_id="o1"),
            _ev("forge.tool_created"),
        ])
        evs = rtl._load_a2a_events(
            self.chain, severity_filter=None,
            origin_filter=None, endpoint_filter=None,
        )
        self.assertEqual(len(evs), 2)

    def test_origin_filter_applied(self):
        _write_chain(self.chain, [
            _ev("A2A.envelope_received", origin_id="o1"),
            _ev("A2A.envelope_received", origin_id="o2"),
        ])
        evs = rtl._load_a2a_events(
            self.chain, severity_filter=None,
            origin_filter="o1", endpoint_filter=None,
        )
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["origin_id"], "o1")

    def test_endpoint_filter_applied(self):
        _write_chain(self.chain, [
            _ev("A2A.envelope_sent", endpoint_id="ep1"),
            _ev("A2A.envelope_sent", endpoint_id="ep2"),
        ])
        evs = rtl._load_a2a_events(
            self.chain, severity_filter=None,
            origin_filter=None, endpoint_filter="ep2",
        )
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["endpoint_id"], "ep2")

    def test_severity_filter_applied(self):
        _write_chain(self.chain, [
            _ev("A2A.envelope_received", severity="INFO", origin_id="o1"),
            _ev("A2A.request_rejected", severity="WARNING", origin_id="o2"),
        ])
        evs = rtl._load_a2a_events(
            self.chain, severity_filter="WARNING",
            origin_filter=None, endpoint_filter=None,
        )
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["severity"], "WARNING")

    def test_malformed_line_skipped(self):
        with self.chain.open("w") as fh:
            fh.write(json.dumps(_ev("A2A.envelope_received", origin_id="o1")) + "\n")
            fh.write("not-json-at-all\n")
            fh.write(json.dumps(_ev("A2A.engine_spawned", origin_id="o1")) + "\n")
        evs = rtl._load_a2a_events(
            self.chain, severity_filter=None,
            origin_filter=None, endpoint_filter=None,
        )
        self.assertEqual(len(evs), 2)

    def test_missing_chain_returns_empty(self):
        evs = rtl._load_a2a_events(
            Path("/nonexistent/chain.jsonl"),
            severity_filter=None, origin_filter=None, endpoint_filter=None,
        )
        self.assertEqual(evs, [])


class TestGroupByPeer(unittest.TestCase):

    def test_buckets_by_origin_id(self):
        evs = [
            {"origin_id": "o1", "event_type": "A2A.envelope_received"},
            {"origin_id": "o2", "event_type": "A2A.envelope_received"},
            {"origin_id": "o1", "event_type": "A2A.engine_spawned"},
        ]
        buckets = rtl._group_by_peer(evs, per_peer_cap=10)
        self.assertEqual(set(buckets.keys()), {"o1", "o2"})
        self.assertEqual(len(buckets["o1"]), 2)
        self.assertEqual(len(buckets["o2"]), 1)

    def test_buckets_by_endpoint_id_when_no_origin(self):
        evs = [
            {"endpoint_id": "ep1", "event_type": "A2A.envelope_sent"},
            {"endpoint_id": "ep1", "event_type": "A2A.response_received"},
        ]
        buckets = rtl._group_by_peer(evs, per_peer_cap=10)
        self.assertEqual(list(buckets.keys()), ["ep1"])

    def test_per_peer_cap_enforced(self):
        evs = [{"origin_id": "o1", "i": i} for i in range(20)]
        buckets = rtl._group_by_peer(evs, per_peer_cap=5)
        self.assertEqual(len(buckets["o1"]), 5)

    def test_unknown_peer_label(self):
        evs = [{"event_type": "A2A.foo"}]  # neither origin nor endpoint
        buckets = rtl._group_by_peer(evs, per_peer_cap=10)
        self.assertIn("<unknown>", buckets)


if __name__ == "__main__":
    unittest.main(verbosity=2)
