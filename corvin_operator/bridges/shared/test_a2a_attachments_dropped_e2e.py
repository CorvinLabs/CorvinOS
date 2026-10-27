"""E2E: output files that do not fit the response are reported, not dropped silently (PLAN-0965 F-02).

Real HTTP server (a2a_http_server), real sender, real HMAC pairing, real receiver. Only the worker engine is a
stand-in: it writes more files than one response can carry. The test goes through the sender's public ``send``
and reads what a real requester reads: the signed response ``data`` and the audit records.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
import unittest
import unittest.mock as mock
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import a2a_http_server  # noqa: E402
import a2a_worker  # noqa: E402
import remote_trigger_sender as rts  # noqa: E402

_SAVED_LICENSE_MODULES: dict[str, object | None] = {}


def setUpModule() -> None:
    import a2a_test_tier  # run as a plain script (run-all-tests.sh) there is no conftest tier pin

    a2a_test_tier.pin_member_tier()
    for name in ("license.compute_quota", "license.limits"):
        _SAVED_LICENSE_MODULES[name] = sys.modules.get(name)
        sys.modules[name] = None  # type: ignore[assignment]


def tearDownModule() -> None:
    for name, mod in _SAVED_LICENSE_MODULES.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


_emitted: list[dict] = []


def _capture(audit_path_arg, event_type, **kwargs):
    _emitted.append({"event_type": event_type, **kwargs})
    return {"hash": "abc"}


_mock_se = mock.MagicMock()
_mock_se.write_event = mock.MagicMock(side_effect=_capture)


@dataclass
class _Ev:
    type: str
    text: str | None = None
    usage: dict | None = None
    error: str | None = None


class _FloodEngine:
    """Writes ``n_files`` files of ``size`` bytes into ``out/`` and answers with a small JSON text."""

    name = "flood"
    capabilities = {"deterministic": True, "supports_attachments": True, "mid_stream_inject": False,
                    "hooks": False, "skills_tool": False}

    def __init__(self, n_files: int, size: int) -> None:
        self.n_files, self.size = n_files, size

    def spawn(self, prompt, *, working_dir=None, **_ignored) -> Iterator[_Ev]:
        out_dir = Path(working_dir) / "out"
        out_dir.mkdir(exist_ok=True)
        for i in range(self.n_files):
            (out_dir / f"part{i:02d}.bin").write_bytes(bytes([i % 251]) * self.size)
        payload = json.dumps({"ok": True})
        yield _Ev(type="text_delta", text=payload)
        yield _Ev(type="turn_completed", text=payload)

    def cancel(self) -> None:
        pass


def _write_json(path: Path, cfg: dict) -> None:
    path.write_text(json.dumps(cfg))
    path.chmod(0o600)


class TestAttachmentsDropped(unittest.TestCase):
    def _pair(self, tmp: Path, engine) -> tuple[object, object]:
        origins, endpoints = tmp / "origins", tmp / "endpoints"
        origins.mkdir()
        endpoints.mkdir()
        k_hmac, k_recv = secrets.token_hex(32), secrets.token_hex(32)
        _write_json(origins / "req.json", {
            "origin_id": "req", "hmac_key": k_hmac, "recv_key": k_recv, "enabled": True, "max_ttl_s": 300,
            "allowed_personas": ["compute-csv"], "spawn_worker": True})
        server = a2a_http_server.build_server(
            host="127.0.0.1", port=0, origins_dir=origins, engine_factory=lambda: engine,
            instance_id="iid-flood-receiver", forge_se=_mock_se)
        a2a_http_server.serve_in_thread(server)
        h, p = server.server_address[:2]
        _write_json(endpoints / "peer.json", {
            "endpoint_id": "peer", "url": f"http://{h}:{p}/v1/a2a/receive", "hmac_key": k_hmac, "recv_key": k_recv,
            "instance_id": "iid-flood-receiver", "enabled": True, "default_ttl_s": 120, "our_origin_id": "req"})
        sender = rts.RemoteTriggerSender(endpoints_dir=endpoints, instance_id="iid-requester", forge_se=_mock_se)
        return server, sender

    def setUp(self) -> None:
        _emitted.clear()
        os.environ["CORVIN_A2A_ATTESTATION_DISABLED"] = "1"
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(lambda: os.environ.pop("CORVIN_A2A_ATTESTATION_DISABLED", None))

    def _send(self, engine):
        server, sender = self._pair(Path(self._tmp.name), engine)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        # Inline only: a requester that asks for references (the default since ADR-2287) gets the
        # files over the byte cap as references instead (test_a2a_large_object_reference_e2e.py).
        return sender.send("peer", instruction="produce the parts",
                           result_schema={"properties": {"ok": {"type": "boolean"}},
                                          "attachments_by_reference": False},
                           ttl_s=60, timeout_s=60)

    def test_byte_cap_overflow_is_reported_in_the_signed_response_and_the_audit(self):
        # 20 files of 90 000 bytes: about 11 fit under 1 MiB, the rest must be counted, not lost silently.
        res = self._send(_FloodEngine(20, 90_000))
        self.assertTrue(res.ok, msg=f"status={res.status} data={res.data}")
        got = len(res.attachments)
        self.assertGreater(got, 0)
        self.assertLess(got, 20)
        dropped = res.data.get("attachments_dropped")
        self.assertIsInstance(dropped, dict, msg=f"data={res.data}")
        self.assertEqual(dropped["count"], 20 - got)
        self.assertEqual(dropped["reasons"], {"byte_cap": 20 - got})
        ev = [e for e in _emitted if e["event_type"] == "A2A.result_filtered"]
        self.assertTrue(ev, "A2A.result_filtered was not audited")
        self.assertEqual(ev[-1].get("details", {}).get("attachments_dropped_count"), 20 - got)
        # names and content never reach the audit
        self.assertNotIn("part", json.dumps([e.get("details", {}) for e in ev]))

    def test_count_cap_overflow_is_reported(self):
        # 20 tiny files: the count cap (16) bites first.
        res = self._send(_FloodEngine(20, 100))
        self.assertTrue(res.ok, msg=f"status={res.status} data={res.data}")
        self.assertEqual(len(res.attachments), 16)
        self.assertEqual(res.data["attachments_dropped"], {"count": 4, "reasons": {"count_cap": 4}})

    def test_nothing_dropped_means_no_extra_field(self):
        res = self._send(_FloodEngine(3, 100))
        self.assertTrue(res.ok, msg=f"status={res.status} data={res.data}")
        self.assertEqual(len(res.attachments), 3)
        self.assertNotIn("attachments_dropped", res.data)


class TestHarvestReport(unittest.TestCase):
    """The harvest function itself: the file selection did not change, the report is exact."""

    def test_report_matches_selection_and_wrapper_returns_the_same_list(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            for i in range(18):
                (out / f"f{i:02d}.txt").write_bytes(b"x" * 10)
            (out / "bad name.txt").write_bytes(b"x")          # space: not a legal attachment name
            (out / "skip.txt").write_bytes(b"x")              # excluded by the sender's own list
            schema = {"attachments_out_allowed": [f"f{i:02d}.txt" for i in range(18)] + ["bad name.txt"]}
            atts, dropped = a2a_worker._harvest_report(out, schema)
            self.assertEqual(len(atts), 16)
            self.assertEqual(dropped, {"bad_name": 1, "count_cap": 2})
            self.assertEqual(a2a_worker._harvest_outputs(out, schema), atts)


if __name__ == "__main__":
    unittest.main()
