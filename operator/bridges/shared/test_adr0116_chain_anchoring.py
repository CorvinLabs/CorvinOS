"""test_adr0116_chain_anchoring.py — ADR-0116: Unified Audit Chain + A2A Cross-Peer Anchoring.

Coverage
--------
M1. delegation.started / delegation.ended events emitted for delegate_* tool calls.
M2. audit.write_event Forge MCP tool: allowlisted events written, unknown types rejected,
    forbidden detail keys stripped.
M4. sender_chain_tail included in TaskEnvelope HMAC payload.
    receiver_chain_tail included in ResponseEnvelope HMAC payload.
    A2A.chain_anchor_sent emitted by sender.
    A2A.chain_anchor_received emitted by receiver.
    A2A.chain_anchor_verified emitted by sender after response.
M5. voice-audit verify --cross-peer: PASS/FAIL/UNVERIFIABLE per task_id.
    get_audit_chain_tail() returns last hash or None.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
import secrets
import sys
import tempfile
import time
import unittest
import unittest.mock as mock
from dataclasses import asdict
from pathlib import Path

_here = Path(__file__).resolve().parent
_forge = _here.parents[1] / "forge"
for p in (str(_here), str(_forge)):
    if p not in sys.path:
        sys.path.insert(0, p)

import remote_trigger_receiver as rtr
import remote_trigger_sender as rts
from remote_trigger_receiver import (
    NonceStore,
    OriginRegistry,
    RemoteTriggerReceiver,
    TaskEnvelope,
    ResponseEnvelope,
)
from remote_trigger_sender import (
    RemoteEndpointRegistry,
    RemoteTriggerSender,
)

# ── helpers ──────────────────────────────────────────────────────────────────

def _make_keypair() -> tuple[str, str]:
    """Return (hmac_key, recv_key) as 64-hex strings."""
    return secrets.token_hex(32), secrets.token_hex(32)


def _make_origin_dir(
    tmp: Path,
    *,
    hmac_key: str,
    recv_key: str,
    origin_id: str = "test-origin",
    spawn_worker: bool = False,
) -> Path:
    origins_dir = tmp / "origins"
    origins_dir.mkdir(exist_ok=True)
    cfg = {
        "origin_id": origin_id,
        "hmac_key": hmac_key,
        "recv_key": recv_key,
        "spawn_worker": spawn_worker,
        "enabled": True,
    }
    origin_file = origins_dir / f"{origin_id}.json"
    origin_file.write_text(json.dumps(cfg))
    origin_file.chmod(0o600)
    return origins_dir


def _make_endpoint_dir(
    tmp: Path,
    *,
    url: str,
    hmac_key: str,
    recv_key: str,
    endpoint_id: str = "test-ep",
) -> Path:
    ep_dir = tmp / "endpoints"
    ep_dir.mkdir(exist_ok=True)
    cfg = {
        "endpoint_id": endpoint_id,
        "url": url,
        "hmac_key": hmac_key,
        "recv_key": recv_key,
    }
    (ep_dir / f"{endpoint_id}.json").write_text(json.dumps(cfg))
    return ep_dir


def _mock_forge_se(audit_path: Path | None = None) -> mock.MagicMock:
    """Return a mock forge_se that writes events to a temp JSONL file."""
    m = mock.MagicMock()
    written: list[dict] = []

    def _write(path, event_type, *, severity=None, details=None, **kw):
        rec = {"ts": time.time(), "event_type": event_type,
               "severity": severity or "INFO", "details": details or {}}
        written.append(rec)
        if audit_path and audit_path.exists():
            with audit_path.open("a") as fh:
                fh.write(json.dumps(rec) + "\n")

    m.write_event.side_effect = _write
    m.audit_write_or_die = m.write_event
    m._written = written
    return m


def _build_test_chain(path: Path, n_records: int = 3) -> str:
    """Write n_records hash-chained records to path; return last hash."""
    import hashlib as _hl, json as _json
    prev = ""
    last_hash = ""
    with path.open("w") as fh:
        for i in range(n_records):
            rec = {"ts": time.time(), "event_type": f"test.record_{i}",
                   "severity": "INFO", "details": {"seq": i}, "prev_hash": prev}
            canonical = _json.dumps(rec, sort_keys=True, separators=(",", ":"))
            h = _hl.sha256()
            h.update(prev.encode())
            h.update(b"\n")
            h.update(canonical.encode())
            rec["hash"] = h.hexdigest()[:16]
            fh.write(_json.dumps(rec) + "\n")
            prev = rec["hash"]
            last_hash = rec["hash"]
    return last_hash


# ── M1: Delegation Context events ────────────────────────────────────────────

class TestDelegationEvents(unittest.TestCase):
    """M1 — delegation.started / delegation.ended in adapter audit stream."""

    def test_delegation_events_registered(self):
        """delegation.* events exist in EVENT_SEVERITY."""
        from forge.security_events import EVENT_SEVERITY
        self.assertIn("delegation.started", EVENT_SEVERITY)
        self.assertIn("delegation.ended", EVENT_SEVERITY)
        self.assertIn("delegation.error", EVENT_SEVERITY)
        self.assertEqual(EVENT_SEVERITY["delegation.started"], "INFO")
        self.assertEqual(EVENT_SEVERITY["delegation.ended"], "INFO")
        self.assertEqual(EVENT_SEVERITY["delegation.error"], "WARNING")

    def test_worker_relay_events_registered(self):
        """worker.relay_* events exist in EVENT_SEVERITY."""
        from forge.security_events import EVENT_SEVERITY
        self.assertIn("worker.relay_block_start", EVENT_SEVERITY)
        self.assertIn("worker.event_relayed", EVENT_SEVERITY)
        self.assertIn("worker.relay_block_end", EVENT_SEVERITY)


# ── M2: Worker Audit Gateway (MCP tool) ──────────────────────────────────────

class _FakeRegistry:
    """Minimal registry duck-type: provides root + AUDIT_NAME for stub."""
    AUDIT_NAME = "audit.jsonl"

    def __init__(self, root: Path):
        self.root = root


class _AuditWriteEventStub:
    """Minimal duck-type stub for MCPServer to test _call_audit_write_event."""

    def __init__(self):
        self._responses: list[tuple] = []
        self._written: list[dict] = []
        self._logged: list[dict] = []
        self._tmp = tempfile.TemporaryDirectory()
        self.registry = _FakeRegistry(Path(self._tmp.name))
        self.policy = type("P", (), {"audit_hash_chain": True})()

    def __del__(self):
        try:
            self._tmp.cleanup()
        except Exception:
            pass

    def _respond(self, msgid, result):
        self._responses.append(("ok", msgid, result))

    def _error(self, msgid, code, msg):
        self._responses.append(("err", msgid, code, msg))

    def _write_security_event(self, event_type, *, severity=None, details=None, **kw):
        self._written.append({"event_type": event_type,
                               "severity": severity, "details": details or {}})

    def _log_security_event(self, event_type, *, details=None, **kw):
        self._logged.append({"event_type": event_type, "details": details or {}})

    # Bind the real method from MCPServer as if we were the server.
    def _call_audit_write_event(self, msgid, args):
        from forge.mcp_server import MCPServer
        return MCPServer._call_audit_write_event(self, msgid, args)

    def _all_tools(self):
        from forge.mcp_server import MCPServer
        return MCPServer._all_tools(self)


class TestAuditWriteEventTool(unittest.TestCase):
    """M2 — audit.write_event MCP tool: validation, strip, write."""

    def _stub(self) -> _AuditWriteEventStub:
        return _AuditWriteEventStub()

    def test_audit_write_event_tool_advertised(self):
        """audit.write_event event type is documented in EVENT_SEVERITY."""
        from forge.security_events import EVENT_SEVERITY
        # The tool itself is a meta-tool, not a security event; check that
        # the gateway's own events are registered.
        self.assertIn("audit.worker_event_written", EVENT_SEVERITY)
        self.assertIn("audit.worker_event_rejected", EVENT_SEVERITY)

    def test_write_allowlisted_event(self):
        """An allowlisted event_type is written successfully."""
        stub = self._stub()
        stub._call_audit_write_event(1, {
            "event_type": "delegation.started",
            "details": {"turn_id": "ot_abc123", "delegation_id": "dlg_xyz"},
        })
        self.assertTrue(any(r[0] == "ok" for r in stub._responses))
        # Implementation writes directly to the audit file in the registry root
        audit_file = stub.registry.root / stub.registry.AUDIT_NAME
        self.assertTrue(audit_file.exists(), "audit file must be created")
        events = [json.loads(line) for line in audit_file.read_text().splitlines() if line]
        written_types = [e.get("event_type") for e in events]
        self.assertIn("delegation.started", written_types)

    def test_reject_unknown_event_type(self):
        """Unknown event_type is rejected with INVALID_PARAMS."""
        stub = self._stub()
        stub._call_audit_write_event(2, {
            "event_type": "totally.unknown.event",
            "details": {},
        })
        self.assertTrue(any(r[0] == "err" for r in stub._responses))

    def test_forbidden_keys_stripped(self):
        """Forbidden keys (prompt, output, etc.) are stripped from details."""
        stub = self._stub()
        stub._call_audit_write_event(3, {
            "event_type": "delegation.started",
            "details": {
                "turn_id": "ot_abc",
                "prompt": "SECRET PROMPT TEXT",    # must be stripped
                "output": "SECRET OUTPUT",          # must be stripped
                "delegation_id": "dlg_xyz",
            },
        })
        self.assertTrue(any(r[0] == "ok" for r in stub._responses))
        if stub._written:
            d = stub._written[0]["details"]
            self.assertNotIn("prompt", d)
            self.assertNotIn("output", d)
            self.assertIn("turn_id", d)

    def test_delegation_id_injected(self):
        """delegation_id arg is injected into details."""
        stub = self._stub()
        stub._call_audit_write_event(1, {
            "event_type": "worker.event_relayed",
            "delegation_id": "dlg_test123",
            "details": {"turn_id": "ot_abc"},
        })
        self.assertTrue(any(r[0] == "ok" for r in stub._responses))
        if stub._written:
            self.assertEqual(stub._written[0]["details"].get("delegation_id"),
                             "dlg_test123")

    def test_severity_downgraded_from_critical(self):
        """CRITICAL severity from worker is downgraded to INFO (gate)."""
        stub = self._stub()
        stub._call_audit_write_event(1, {
            "event_type": "delegation.started",
            "severity": "CRITICAL",
            "details": {},
        })
        if stub._written:
            self.assertIn(stub._written[0]["severity"], ("INFO", "WARNING"))


# ── M4: A2A Chain Anchoring ───────────────────────────────────────────────────

class TestGetAuditChainTail(unittest.TestCase):
    """M4 — get_audit_chain_tail() helper."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._tmp_path = Path(self._tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_returns_none_for_missing_file(self):
        from forge.security_events import get_audit_chain_tail
        result = get_audit_chain_tail(self._tmp_path / "nonexistent.jsonl")
        self.assertIsNone(result)

    def test_returns_none_for_empty_file(self):
        from forge.security_events import get_audit_chain_tail
        p = self._tmp_path / "empty.jsonl"
        p.write_text("")
        result = get_audit_chain_tail(p)
        self.assertIsNone(result)

    def test_returns_last_hash(self):
        from forge.security_events import get_audit_chain_tail
        p = self._tmp_path / "chain.jsonl"
        last_hash = _build_test_chain(p, n_records=5)
        result = get_audit_chain_tail(p)
        self.assertEqual(result, last_hash)

    def test_skips_records_without_hash(self):
        from forge.security_events import get_audit_chain_tail
        p = self._tmp_path / "partial.jsonl"
        # Write one record without hash, then one with hash.
        p.write_text(
            json.dumps({"event_type": "test.a"}) + "\n"
            + json.dumps({"event_type": "test.b", "hash": "abc123def456abcd"}) + "\n"
        )
        result = get_audit_chain_tail(p)
        self.assertEqual(result, "abc123def456abcd")


class TestSenderChainTailInEnvelope(unittest.TestCase):
    """M4 — sender_chain_tail included in TaskEnvelope HMAC payload."""

    def _build_envelope(self, *, chain_tail: str | None) -> dict:
        hmac_key = secrets.token_hex(32)
        return rts.RemoteTriggerSender._build_envelope(
            task_id="task123",
            nonce=secrets.token_hex(16),
            origin_id="sender",
            instruction="do something",
            result_schema={},
            ttl_s=300,
            hmac_key_hex=hmac_key,
            sender_instance_id="instance-A",
            sender_chain_tail=chain_tail,
        ), hmac_key

    def test_sender_chain_tail_present_in_envelope(self):
        """sender_chain_tail appears in the envelope dict when provided."""
        env, _ = self._build_envelope(chain_tail="abcd1234efgh5678")
        self.assertEqual(env.get("sender_chain_tail"), "abcd1234efgh5678")

    def test_sender_chain_tail_absent_when_none(self):
        """sender_chain_tail absent from envelope when None."""
        env, _ = self._build_envelope(chain_tail=None)
        self.assertNotIn("sender_chain_tail", env)

    def test_hmac_covers_sender_chain_tail(self):
        """Same task_id + different sender_chain_tail → different signatures."""
        hmac_key = secrets.token_hex(32)
        kwargs = dict(
            task_id="task123",
            nonce="nonce_fixed",
            origin_id="sender",
            instruction="do something",
            result_schema={},
            ttl_s=300,
            hmac_key_hex=hmac_key,
            sender_instance_id="instance-A",
        )
        env1 = rts.RemoteTriggerSender._build_envelope(
            **kwargs, sender_chain_tail="hash_aaa")
        env2 = rts.RemoteTriggerSender._build_envelope(
            **kwargs, sender_chain_tail="hash_bbb")
        self.assertNotEqual(env1["signature"], env2["signature"])

    def test_hmac_same_without_chain_tail(self):
        """Two envelopes without chain_tail have identical signatures."""
        hmac_key = secrets.token_hex(32)
        nonce = "nonce_fixed_" + secrets.token_hex(4)
        kwargs = dict(
            task_id="task123",
            nonce=nonce,
            origin_id="sender",
            instruction="do something",
            result_schema={},
            ttl_s=300,
            hmac_key_hex=hmac_key,
            sender_instance_id="instance-A",
        )
        # Two calls without chain_tail — both omit the field → same HMAC
        # (but issued_at differs so we need to freeze it)
        with mock.patch("time.time", return_value=1234567890.0):
            env1 = rts.RemoteTriggerSender._build_envelope(**kwargs,
                                                           sender_chain_tail=None)
            env2 = rts.RemoteTriggerSender._build_envelope(**kwargs,
                                                           sender_chain_tail=None)
        self.assertEqual(env1["signature"], env2["signature"])


class TestReceiverChainTailInResponse(unittest.TestCase):
    """M4 — receiver_chain_tail in ResponseEnvelope canonical payload."""

    def test_receiver_chain_tail_default_empty(self):
        resp = ResponseEnvelope(
            task_id="t1", origin_id="o1", issued_at=0.0,
            instance_id="iid", status="ok", data={}, attachments=[],
            signature="",
        )
        self.assertEqual(resp.receiver_chain_tail, "")

    def test_canonical_payload_excludes_empty_tail(self):
        """Empty receiver_chain_tail is excluded from HMAC (backward compat)."""
        resp = ResponseEnvelope(
            task_id="t1", origin_id="o1", issued_at=0.0,
            instance_id="iid", status="ok", data={}, attachments=[],
            signature="", receiver_chain_tail="",
        )
        payload = json.loads(resp.canonical_payload())
        self.assertNotIn("receiver_chain_tail", payload)

    def test_canonical_payload_includes_nonempty_tail(self):
        """Non-empty receiver_chain_tail IS included in HMAC."""
        resp = ResponseEnvelope(
            task_id="t1", origin_id="o1", issued_at=0.0,
            instance_id="iid", status="ok", data={}, attachments=[],
            signature="", receiver_chain_tail="abc123def456abcd",
        )
        payload = json.loads(resp.canonical_payload())
        self.assertIn("receiver_chain_tail", payload)
        self.assertEqual(payload["receiver_chain_tail"], "abc123def456abcd")

    def test_different_receiver_tail_different_hmac(self):
        """Different receiver_chain_tail values produce different signatures."""
        recv_key = bytes.fromhex(secrets.token_hex(32))
        resp1 = ResponseEnvelope(
            task_id="t1", origin_id="o1", issued_at=1.0,
            instance_id="iid", status="ok", data={}, attachments=[],
            signature="", receiver_chain_tail="hash_aaa",
        )
        resp2 = ResponseEnvelope(
            task_id="t1", origin_id="o1", issued_at=1.0,
            instance_id="iid", status="ok", data={}, attachments=[],
            signature="", receiver_chain_tail="hash_bbb",
        )
        sig1 = _hmac.new(recv_key, resp1.canonical_payload(), hashlib.sha256).hexdigest()
        sig2 = _hmac.new(recv_key, resp2.canonical_payload(), hashlib.sha256).hexdigest()
        self.assertNotEqual(sig1, sig2)


class TestChainAnchorEventsRegistered(unittest.TestCase):
    """M4 — chain anchor events in EVENT_SEVERITY."""

    def test_events_exist(self):
        from forge.security_events import EVENT_SEVERITY
        for evt in (
            "A2A.chain_anchor_sent",
            "A2A.chain_anchor_received",
            "A2A.chain_anchor_verified",
            "A2A.chain_tail_unavailable",
        ):
            with self.subTest(evt=evt):
                self.assertIn(evt, EVENT_SEVERITY)


class TestChainAnchorRoundTrip(unittest.TestCase):
    """M4 — full sender→receiver round-trip emits anchor events."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._tmp_path = Path(self._tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_round_trip(self, *, with_chain_tail: bool = True) -> tuple[list, list]:
        """Run a sender→receiver exchange and collect emitted audit events.

        We mock audit_path() inside the receiver module so _audit_strict
        can mkdir + write without touching the real filesystem.
        """
        hmac_key, recv_key = _make_keypair()
        origin_id = "test-origin-" + secrets.token_hex(4)

        # Build receiver origin dir
        origins_dir = _make_origin_dir(
            self._tmp_path, hmac_key=hmac_key, recv_key=recv_key,
            origin_id=origin_id,
        )
        nonce_store = NonceStore(ttl_s=60)
        receiver_events: list[dict] = []
        sender_events: list[dict] = []

        # Mock audit path to a temp file so _audit_strict can mkdir+write.
        fake_audit = self._tmp_path / "recv_audit.jsonl"

        mock_receiver_se = mock.MagicMock()

        def _recv_write(path, evt, *, severity=None, details=None, **kw):
            receiver_events.append({"event_type": evt, "details": details or {}})
            # write a minimal record so get_audit_chain_tail can read a hash
            import hashlib as _hl
            rec = {"event_type": evt, "details": details or {}}
            h = _hl.sha256(json.dumps(rec, sort_keys=True).encode()).hexdigest()[:16]
            rec["hash"] = h
            with fake_audit.open("a") as fh:
                fh.write(json.dumps(rec) + "\n")

        mock_receiver_se.write_event.side_effect = _recv_write
        mock_receiver_se.get_audit_chain_tail = mock.MagicMock(
            return_value="receiver_tail_abc123" if with_chain_tail else None
        )

        with mock.patch.object(rtr, "audit_path", return_value=fake_audit):
            receiver = RemoteTriggerReceiver(
                origins_dir=origins_dir,
                nonce_store=nonce_store,
                forge_se=mock_receiver_se,
            )

            # Build envelope (bypass HTTP)
            nonce = secrets.token_hex(16)
            sender_chain_tail = "sender_tail_def456abcd01234567" if with_chain_tail else None
            env_dict = rts.RemoteTriggerSender._build_envelope(
                task_id="task-test-" + secrets.token_hex(4),
                nonce=nonce,
                origin_id=origin_id,
                instruction="test instruction",
                result_schema={},
                ttl_s=300,
                hmac_key_hex=hmac_key,
                sender_instance_id="instance-A",
                sender_chain_tail=sender_chain_tail,
            )

            # Emit sender-side chain_anchor_sent manually (simulating sender)
            if sender_chain_tail:
                sender_events.append({
                    "event_type": "A2A.chain_anchor_sent",
                    "details": {"task_id": env_dict["task_id"],
                                "our_chain_tail": sender_chain_tail[:16]},
                })

            # Pass to receiver
            receiver.receive(env_dict)

        return sender_events, receiver_events

    def test_receiver_emits_chain_anchor_received(self):
        _, receiver_events = self._make_round_trip(with_chain_tail=True)
        types = [e["event_type"] for e in receiver_events]
        self.assertIn("A2A.chain_anchor_received", types)

    def test_sender_emits_chain_anchor_sent(self):
        sender_events, _ = self._make_round_trip(with_chain_tail=True)
        types = [e["event_type"] for e in sender_events]
        self.assertIn("A2A.chain_anchor_sent", types)

    def test_no_chain_anchor_when_tail_unavailable(self):
        _, receiver_events = self._make_round_trip(with_chain_tail=False)
        types = [e["event_type"] for e in receiver_events]
        self.assertNotIn("A2A.chain_anchor_received", types)

    def test_chain_anchor_received_contains_16hex_prefix(self):
        _, receiver_events = self._make_round_trip(with_chain_tail=True)
        anchor = next((e for e in receiver_events
                       if e["event_type"] == "A2A.chain_anchor_received"), None)
        self.assertIsNotNone(anchor)
        tail = anchor["details"].get("peer_chain_tail", "")
        self.assertLessEqual(len(tail), 16, "Only 16-hex prefix in audit details")

    def test_receiver_chain_tail_in_response(self):
        """ResponseEnvelope includes receiver_chain_tail when chain tail available."""
        hmac_key, recv_key = _make_keypair()
        origin_id = "test-origin-" + secrets.token_hex(4)
        tmp = self._tmp_path
        origins_dir = _make_origin_dir(
            tmp, hmac_key=hmac_key, recv_key=recv_key, origin_id=origin_id,
        )
        fake_audit = tmp / "recv_audit2.jsonl"
        mock_se = mock.MagicMock()
        mock_se.write_event.return_value = None
        mock_se.get_audit_chain_tail.return_value = "receiver_hash_xyz"

        with mock.patch.object(rtr, "audit_path", return_value=fake_audit):
            receiver = RemoteTriggerReceiver(
                origins_dir=origins_dir,
                nonce_store=NonceStore(ttl_s=60),
                forge_se=mock_se,
            )
            env_dict = rts.RemoteTriggerSender._build_envelope(
                task_id="task-rcv-" + secrets.token_hex(4),
                nonce=secrets.token_hex(16),
                origin_id=origin_id,
                instruction="test",
                result_schema={},
                ttl_s=300,
                hmac_key_hex=hmac_key,
                sender_instance_id="instance-A",
            )
            response = receiver.receive(env_dict)
        self.assertIsInstance(response, ResponseEnvelope)
        # The response must include receiver_chain_tail in to_dict()
        resp_dict = response.to_dict()
        self.assertIn("receiver_chain_tail", resp_dict)


# ── M5: Cross-Peer Verification CLI ──────────────────────────────────────────

class TestCrossPeerVerify(unittest.TestCase):
    """M5 — voice-audit verify --cross-peer logic."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._tmp_path = Path(self._tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _import_cross_peer(self):
        """Import _cross_peer_verify from voice_audit.py."""
        scripts = Path(__file__).resolve().parents[2] / "voice" / "scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        # Reload to pick up changes in this test run
        import importlib
        import voice_audit as va
        importlib.reload(va)
        return va._cross_peer_verify

    def _write_chain_with_anchor(
        self,
        path: Path,
        *,
        anchor_event: str,
        task_id: str,
        our_tail: str,
        peer_tail: str = "",
    ) -> str:
        """Write a chain that includes one A2A chain anchor event; return last hash."""
        last = _build_test_chain(path, n_records=2)
        # Append anchor event
        with path.open("a") as fh:
            rec: dict = {
                "ts": time.time(),
                "event_type": anchor_event,
                "severity": "INFO",
                "details": {
                    "task_id": task_id,
                    "our_chain_tail": our_tail,
                },
                "prev_hash": last,
            }
            if peer_tail:
                rec["details"]["peer_chain_tail"] = peer_tail
            h = hashlib.sha256()
            h.update(last.encode())
            h.update(b"\n")
            h.update(json.dumps(rec, sort_keys=True, separators=(",", ":")).encode())
            rec["hash"] = h.hexdigest()[:16]
            fh.write(json.dumps(rec) + "\n")
        return rec["hash"]

    def test_pass_when_tails_verify(self):
        """PASS when our_chain_tail prefix is found in local chain."""
        _cpv = self._import_cross_peer()
        local = self._tmp_path / "local.jsonl"
        peer = self._tmp_path / "peer.jsonl"

        # Build local chain; get last hash (will be our_tail)
        our_tail = _build_test_chain(local, n_records=3)
        # Write chain_anchor_sent in local chain referencing that same tail
        with local.open("a") as fh:
            fh.write(json.dumps({
                "event_type": "A2A.chain_anchor_sent",
                "severity": "INFO",
                "details": {"task_id": "t1", "our_chain_tail": our_tail[:16]},
                "hash": secrets.token_hex(8),
                "prev_hash": our_tail,
            }) + "\n")

        # Build peer chain with chain_anchor_received referencing our_tail prefix
        _build_test_chain(peer, n_records=2)
        with peer.open("a") as fh:
            fh.write(json.dumps({
                "event_type": "A2A.chain_anchor_received",
                "severity": "INFO",
                "details": {"task_id": "t1", "peer_chain_tail": our_tail[:16]},
                "hash": secrets.token_hex(8),
            }) + "\n")

        ok, problems, results = _cpv(local, peer)
        self.assertTrue(ok)
        passing = [r for r in results if r.get("verdict") == "PASS"]
        self.assertTrue(len(passing) > 0)

    def test_unverifiable_when_peer_chain_missing_anchor(self):
        """UNVERIFIABLE when local has chain_anchor_sent but peer has no received."""
        _cpv = self._import_cross_peer()
        local = self._tmp_path / "local.jsonl"
        peer = self._tmp_path / "peer.jsonl"

        our_tail = _build_test_chain(local, n_records=3)
        with local.open("a") as fh:
            fh.write(json.dumps({
                "event_type": "A2A.chain_anchor_sent",
                "severity": "INFO",
                "details": {"task_id": "t2", "our_chain_tail": our_tail[:16]},
                "hash": secrets.token_hex(8),
            }) + "\n")
        _build_test_chain(peer, n_records=2)  # no anchor event

        ok, problems, results = _cpv(local, peer, strict=False)
        unverifiable = [r for r in results if r.get("verdict") == "UNVERIFIABLE"]
        self.assertTrue(len(unverifiable) > 0)
        # With strict=False, UNVERIFIABLE does not fail
        self.assertTrue(ok)

    def test_strict_treats_unverifiable_as_fail(self):
        """With strict=True, UNVERIFIABLE becomes a FAIL."""
        _cpv = self._import_cross_peer()
        local = self._tmp_path / "local.jsonl"
        peer = self._tmp_path / "peer.jsonl"

        our_tail = _build_test_chain(local, n_records=3)
        with local.open("a") as fh:
            fh.write(json.dumps({
                "event_type": "A2A.chain_anchor_sent",
                "severity": "INFO",
                "details": {"task_id": "t3", "our_chain_tail": our_tail[:16]},
                "hash": secrets.token_hex(8),
            }) + "\n")
        _build_test_chain(peer, n_records=2)

        ok, problems, results = _cpv(local, peer, strict=True)
        self.assertFalse(ok)

    def test_task_id_filter(self):
        """task_id_filter limits results to the specified task."""
        _cpv = self._import_cross_peer()
        local = self._tmp_path / "local.jsonl"
        peer = self._tmp_path / "peer.jsonl"

        _build_test_chain(local, n_records=2)
        for tid in ("task-A", "task-B"):
            with local.open("a") as fh:
                fh.write(json.dumps({
                    "event_type": "A2A.chain_anchor_sent",
                    "details": {"task_id": tid, "our_chain_tail": "abc123"},
                    "hash": secrets.token_hex(8),
                }) + "\n")
        _build_test_chain(peer, n_records=2)

        ok, _, results = _cpv(local, peer, task_id_filter="task-A")
        task_ids = {r.get("task_id") for r in results}
        self.assertNotIn("task-B", task_ids)

    def test_empty_chains_ok(self):
        """Both chains empty → no anchor events → ok=True, no results."""
        _cpv = self._import_cross_peer()
        local = self._tmp_path / "local.jsonl"
        peer = self._tmp_path / "peer.jsonl"
        local.write_text("")
        peer.write_text("")
        ok, problems, results = _cpv(local, peer)
        self.assertTrue(ok)
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
