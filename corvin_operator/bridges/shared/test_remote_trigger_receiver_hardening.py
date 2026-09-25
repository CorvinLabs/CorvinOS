"""Regression tests — A2A inbound hardening (review round 2026-09-25).

Each class pins one verified defect of ``RemoteTriggerReceiver``:

* never-raises contract: unauthenticated inputs (non-ASCII signature,
  ``ttl_s`` = Infinity / 1e400, non-object body, deeply nested schema) used to
  RAISE out of ``receive()`` → HTTP 500 → origin-id existence oracle;
* a replayed envelope charged the real peer's rate-limit token;
* ``a2a.nonce_collision_detected`` was never written (tenant_id=None);
* ``allow_bash=false`` left Monitor / PowerShell / … reachable, and the
  subagent deny named only the legacy ``Task`` alias, not ``Agent``;
* 10 peers × concurrent receives keep working.

Every test injects its own ``forge_se`` and points ``VOICE_AUDIT_PATH`` /
``CORVIN_HOME`` at a temp dir — nothing reaches a real audit chain.
"""
from __future__ import annotations

import collections
import hashlib
import hmac as _hmac
import json
import os
import secrets
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import remote_trigger_receiver as rtr  # noqa: E402


class _RecordingSE:
    """Stand-in for forge.security_events: records (event_type, details)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def write_event(self, path, event_type, **kw):
        with self._lock:
            self.events.append((event_type, dict(kw.get("details") or {})))
        return {"hash": "x"}

    def get_audit_chain_tail(self, path):
        return ""

    def of(self, event_type: str) -> list[dict]:
        with self._lock:
            return [d for e, d in self.events if e == event_type]

    def reasons(self) -> list[str]:
        return [d.get("reason") for d in self.of("A2A.request_rejected")]


class _Base(unittest.TestCase):
    ORIGIN = "peer1"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="a2a-hard-")
        self.home = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {
            "CORVIN_HOME": str(self.home),
            "VOICE_AUDIT_PATH": str(self.home / "audit.jsonl"),
            "CORVIN_A2A_ATTESTATION_DISABLED": "1",
        })
        self._env.start()
        os.environ.pop("CORVIN_TENANT_ID", None)
        os.environ.pop("REMOTE_ORIGINS_DIR", None)
        self.origins = self.home / "origins"
        self.origins.mkdir()
        self.keys: dict[str, str] = {}
        self.add_origin(self.ORIGIN)
        self.se = _RecordingSE()
        self.recv = rtr.RemoteTriggerReceiver(
            origins_dir=self.origins, nonce_store=rtr.NonceStore(),
            forge_se=self.se, instance_id="iid", force_m1_only=True,
        )

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def add_origin(self, oid: str, **extra) -> None:
        hk = secrets.token_hex(32)
        cfg = {"enabled": True, "hmac_key": hk, "recv_key": secrets.token_hex(32)}
        cfg.update(extra)
        p = self.origins / f"{oid}.json"
        p.write_text(json.dumps(cfg))
        p.chmod(0o600)
        self.keys[oid] = hk

    def envelope(self, oid: str | None = None, *, sign: bool = True, **over) -> dict:
        oid = oid or self.ORIGIN
        e = {
            "task_id": secrets.token_hex(4), "nonce": secrets.token_hex(8),
            "issued_at": time.time(), "origin_id": oid, "instruction": "hi",
            "result_schema": {}, "ttl_s": 60, "sender_instance_id": "s",
            "attachments": [], "signature": "",
        }
        e.update(over)
        if sign:
            te = rtr.TaskEnvelope.from_dict(e)
            e["signature"] = _hmac.new(
                bytes.fromhex(self.keys[oid]), te.canonical_payload(),
                hashlib.sha256,
            ).hexdigest()
        return e


class TestReceiveNeverRaises(_Base):
    """Finding 1: unauthenticated input must yield the unknown-origin shape."""

    def _assert_unknown_origin_shape(self, resp):
        self.assertEqual(resp.status, "rejected")
        self.assertEqual(resp.signature, "", "pre-auth rejection must be unsigned")
        self.assertEqual(resp.data, {})

    def test_ttl_infinity_is_rejected_not_raised(self):
        for ttl in (json.loads("Infinity"), json.loads("1e400"), float("-inf")):
            with self.subTest(ttl=ttl):
                resp = self.recv.receive(self.envelope(sign=False, ttl_s=ttl))
                self._assert_unknown_origin_shape(resp)

    def test_non_ascii_signature_known_origin_is_rejected_not_raised(self):
        resp = self.recv.receive(self.envelope(sign=False, signature="é" * 64))
        self._assert_unknown_origin_shape(resp)
        self.assertIn("bad_signature", self.se.reasons())

    def test_non_ascii_signature_same_shape_as_unknown_origin(self):
        known = self.recv.receive(self.envelope(sign=False, signature="é"))
        unknown = self.recv.receive(
            self.envelope("nosuch", sign=False, signature="é"))
        for r in (known, unknown):
            self._assert_unknown_origin_shape(r)
        self.assertEqual(
            sorted(known.to_dict()), sorted(unknown.to_dict()),
            "response shape must not reveal whether the origin exists")

    def test_non_hex_and_wrong_length_signatures_are_bad_signature(self):
        for sig in ("zz" * 32, "ab", "0" * 63, "0" * 65):
            with self.subTest(sig=sig):
                resp = self.recv.receive(self.envelope(sign=False, signature=sig))
                self._assert_unknown_origin_shape(resp)

    def test_non_dict_body_is_rejected_not_raised(self):
        for body in ([], "x", 7, None):
            with self.subTest(body=body):
                resp = self.recv.receive(body)  # type: ignore[arg-type]
                self._assert_unknown_origin_shape(resp)
                self.assertEqual(resp.task_id, "")
        self.assertIn("envelope_not_object", self.se.reasons())

    def test_deeply_nested_result_schema_is_rejected_not_raised(self):
        deep: dict = {}
        cur = deep
        for _ in range(5000):
            cur["a"] = {}
            cur = cur["a"]
        resp = self.recv.receive(self.envelope(sign=False, result_schema=deep))
        self._assert_unknown_origin_shape(resp)

    def test_unexpected_internal_exception_becomes_audited_rejection(self):
        with mock.patch.object(
            rtr.RemoteTriggerReceiver, "_validate",
            side_effect=KeyError("boom"),
        ):
            resp = self.recv.receive(self.envelope())
        self._assert_unknown_origin_shape(resp)
        self.assertIn("internal_error:KeyError", self.se.reasons())

    def test_echoed_ids_are_capped(self):
        resp = self.recv.receive(
            self.envelope("nosuch", sign=False, task_id="t" * 10_000))
        self.assertLessEqual(len(resp.task_id), 256)

    def test_valid_envelope_still_ok(self):
        self.assertEqual(self.recv.receive(self.envelope()).status, "ok")


class TestReplayDoesNotSpendRateToken(_Base):
    """Finding 3: a replay must be refused BEFORE the rate limiter charges."""

    def test_replay_flood_does_not_rate_limit_real_peer(self):
        captured = self.envelope()
        self.assertEqual(self.recv.receive(dict(captured)).status, "ok")
        for _ in range(80):  # attacker, no key: replays the sniffed envelope
            self.recv.receive(dict(captured))
        self.assertEqual(set(self.se.reasons()), {"replay"})
        fresh = self.recv.receive(self.envelope())
        self.assertEqual(fresh.status, "ok",
                         f"real peer locked out; reasons={self.se.reasons()[-3:]}")

    def test_rate_limited_request_gives_its_nonce_back(self):
        self.add_origin("slow", rate_limit_rpm=1)
        first = self.envelope("slow")
        self.assertEqual(self.recv.receive(first).status, "ok")
        second = self.envelope("slow")
        self.assertEqual(self.recv.receive(dict(second)).status, "rejected")
        self.assertEqual(self.se.reasons()[-1], "rate_limited")
        # Same nonce retried after the bucket refills: NOT a replay.
        with mock.patch.object(rtr.RemoteTriggerReceiver, "_check_rate_limit",
                               return_value=True):
            again = self.recv.receive(dict(second))
        self.assertEqual(again.status, "ok", self.se.reasons()[-1])

    def test_nonce_quota_refusal_has_its_own_reason(self):
        # The per-origin cap now follows the origin's rate limit (0 = only
        # the global cap), so pin it directly for this reason-code test.
        with mock.patch.object(rtr.RemoteTriggerReceiver, "_per_origin_nonce_cap",
                               staticmethod(lambda _cfg: 1)):
            self.add_origin("q", rate_limit_rpm=0)
            self.assertEqual(self.recv.receive(self.envelope("q")).status, "ok")
            self.assertEqual(self.recv.receive(self.envelope("q")).status, "rejected")
        self.assertEqual(self.se.reasons()[-1], "nonce_origin_quota_exceeded")
        self.assertEqual(self.se.of("a2a.nonce_collision_detected"), [],
                         "a full quota is not a replay")


class TestNonceCollisionEventIsWritten(_Base):
    """Finding 6: the event requires tenant_id; None meant it never landed."""

    def test_replay_writes_collision_event_with_default_tenant(self):
        e = self.envelope()
        self.recv.receive(dict(e))
        self.recv.receive(dict(e))
        evs = self.se.of("a2a.nonce_collision_detected")
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["tenant_id"], "_default")
        self.assertEqual(evs[0]["nonce_prefix"], e["nonce"][:8])
        self.assertEqual(set(evs[0]),
                         {"tenant_id", "nonce_prefix", "epoch", "collision_count"})

    def test_collision_event_honours_corvin_tenant_id(self):
        with mock.patch.dict(os.environ, {"CORVIN_TENANT_ID": "acme"}):
            e = self.envelope()
            self.recv.receive(dict(e))
            self.recv.receive(dict(e))
        self.assertEqual(
            self.se.of("a2a.nonce_collision_detected")[0]["tenant_id"], "acme")


class TestWorkerToolDenyList(_Base):
    """Finding 7: allow_bash=false must deny every code-executing tool."""

    def _disallowed(self, cfg: dict) -> list[str]:
        import a2a_worker

        captured: dict = {}

        class _WR:
            engine_name = "claude_code"
            duration_ms = 1
            status = "error"

        def _fake_spawn(**kw):
            captured["d"] = kw.get("disallowed_tools")
            return _WR()

        env = rtr.TaskEnvelope.from_dict(self.envelope())
        with mock.patch.object(a2a_worker, "spawn_a2a_worker", _fake_spawn):
            self.recv._spawn_and_filter(
                env=env, origin_config={"allowed_personas": ["assistant"], **cfg},
                start=time.time(), inbound_attachments=[])
        return list(captured.get("d") or [])

    def test_bash_denied_covers_monitor_powershell_and_shell_control(self):
        d = self._disallowed({"allow_bash": False})
        for tool in ("Bash", "Monitor", "PowerShell", "TaskStop", "KillShell",
                     "KillBash", "BashOutput", "TaskOutput"):
            self.assertIn(tool, d)
        self.assertEqual(d[0], "Bash")

    def test_bash_allowed_does_not_deny_shell_tools(self):
        d = self._disallowed({"allow_bash": True, "allow_network": True,
                              "allow_write_files": True, "allow_subagents": True,
                              "allow_read_files": True})
        for tool in ("Bash", "Monitor", "PowerShell"):
            self.assertNotIn(tool, d)

    def test_subagent_deny_names_agent_and_legacy_task(self):
        d = self._disallowed({"allow_bash": False})
        self.assertIn("Agent", d)
        self.assertIn("Task", d)

    def test_operator_disallowed_list_is_preserved_without_duplicates(self):
        d = self._disallowed({"allow_bash": False,
                              "disallowed_tools": ["Monitor", "Custom"]})
        self.assertIn("Custom", d)
        self.assertEqual(d.count("Monitor"), 1)


class TestTenPeersConcurrent(_Base):
    """Keep the measured 150/150: 10 peers x 15 concurrent receives."""

    def test_ten_peers_concurrent_all_ok(self):
        from a2a_nonce_store import PersistentNonceStore

        for i in range(10):
            self.add_origin(f"p{i}", rate_limit_rpm=0)
        recv = rtr.RemoteTriggerReceiver(
            origins_dir=self.origins,
            nonce_store=PersistentNonceStore(self.home / "n.db"),
            forge_se=self.se, instance_id="iid", force_m1_only=True,
        )
        statuses: collections.Counter = collections.Counter()
        errors: collections.Counter = collections.Counter()
        lock = threading.Lock()

        def worker(oid: str) -> None:
            for _ in range(15):
                try:
                    st = recv.receive(self.envelope(oid)).status
                except Exception as exc:  # pragma: no cover - the defect
                    with lock:
                        errors[type(exc).__name__] += 1
                    continue
                with lock:
                    statuses[st] += 1

        ts = [threading.Thread(target=worker, args=(f"p{i}",)) for i in range(10)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(60)
        self.assertEqual(dict(errors), {})
        self.assertEqual(dict(statuses), {"ok": 150}, self.se.reasons()[:5])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestPerOriginWorkerCap(unittest.TestCase):
    """Round 3: one worker-enabled origin could hold the whole shared work
    executor; now at most _MAX_WORKERS_PER_ORIGIN run per origin."""

    def test_slots_are_per_origin_and_released(self):
        cap = rtr._MAX_WORKERS_PER_ORIGIN
        got = [rtr._worker_slot_acquire("peerA") for _ in range(cap + 1)]
        self.assertEqual(got, [True] * cap + [False])
        self.assertTrue(rtr._worker_slot_acquire("peerB"), "another origin is unaffected")
        rtr._worker_slot_release("peerA")
        self.assertTrue(rtr._worker_slot_acquire("peerA"))
        for _ in range(cap):
            rtr._worker_slot_release("peerA")
        rtr._worker_slot_release("peerB")
        self.assertEqual(rtr._worker_inflight, {})


class TestBusyIsSaidSo(unittest.TestCase):
    """Round 7: a busy peer answered with the same bare `rejected` as a policy
    refusal, so the user never learned a retry would work."""

    def test_signed_rejection_carries_busy_but_unsigned_stays_empty(self):
        rec = rtr.RemoteTriggerReceiver(origins_dir=Path(tempfile.mkdtemp()), nonce_store=rtr.NonceStore(),
                                        instance_id="iid", forge_se=mock.MagicMock())
        signed = rec._rejected_response("t", "o", b"k" * 32, reason="busy")
        self.assertEqual(signed.data, {"reason": "busy"})
        self.assertTrue(signed.signature)
        unsigned = rec._rejected_response("t", "o", None, reason="busy")
        self.assertEqual(unsigned.data, {}, "an unsigned rejection must keep empty data")


class TestRound8BusyFeedRecordAndFilesOnly(_Base):
    """Round 8: (a) the receiver's OWN feed record of a busy refusal carries
    data.reason (the UI's "your instance was busy" hint keys on it, and an
    ``error`` string made it unreachable); (b) a message with attachments and
    no text is a legitimate message, not an empty-instruction injection."""

    def test_busy_refusal_feed_record_has_reason_not_error(self):
        import a2a_feed
        self.add_origin("busyp", spawn_worker=True, allowed_personas=["assistant"])
        recorded: list[dict] = []
        cap = rtr._MAX_WORKERS_PER_ORIGIN
        for _ in range(cap):
            self.assertTrue(rtr._worker_slot_acquire("busyp"))
        try:
            recv = rtr.RemoteTriggerReceiver(
                origins_dir=self.origins, nonce_store=rtr.NonceStore(),
                forge_se=self.se, instance_id="iid")  # worker path enabled
            with mock.patch.object(a2a_feed, "record", lambda **kw: recorded.append(kw)), \
                 mock.patch.object(rtr, "_clag_gate_a2a", lambda lid: None):
                resp = recv.receive(self.envelope("busyp"))
        finally:
            for _ in range(cap):
                rtr._worker_slot_release("busyp")
        self.assertEqual(resp.status, "rejected")
        self.assertEqual(resp.data, {"reason": "busy"})
        out = [r for r in recorded if r.get("kind") == "response"]
        self.assertEqual(len(out), 1, recorded)
        self.assertEqual(out[0].get("data"), {"reason": "busy"})
        self.assertIsNone(out[0].get("error"))

    def test_files_only_instruction_reaches_the_engine(self):
        import a2a_worker
        seen: list[str] = []

        def factory(*a, **kw):
            raise RuntimeError("stop after sanitize")

        orig = a2a_worker.sanitize_instruction

        def spy(text):
            seen.append(text)
            return orig(text)

        att = {"name": "a.png", "mime": "image/png",
               "content_b64": "iVBORw0KGgo=", "sha256": hashlib.sha256(b"\x89PNG\r\n\x1a\n").hexdigest()}
        with mock.patch.object(a2a_worker, "sanitize_instruction", spy):
            try:
                a2a_worker.spawn_a2a_worker(
                    instruction="  ", origin_id="o1", task_id="t1", persona="assistant",
                    ttl_s=60, engine_factory=factory, inbound_attachments=[att],
                    result_schema={})
            except a2a_worker.InjectionAttempt as exc:  # pragma: no cover - the defect
                self.fail(f"files-only message refused as injection: {exc.reason}")
            except Exception:
                pass  # engine / workspace failures are not this test's business
        self.assertEqual(seen, [a2a_worker.ATTACHMENTS_ONLY_INSTRUCTION])
        # Text-less AND file-less stays refused.
        with self.assertRaises(a2a_worker.InjectionAttempt):
            a2a_worker.spawn_a2a_worker(
                instruction="", origin_id="o1", task_id="t2", persona="assistant",
                ttl_s=60, engine_factory=factory, inbound_attachments=[], result_schema={})

    def test_sender_puts_a_stand_in_on_the_wire_but_feeds_what_was_typed(self):
        import remote_trigger_sender as rts
        self.assertEqual(rts.ATTACHMENTS_ONLY_INSTRUCTION,
                         __import__("a2a_worker").ATTACHMENTS_ONLY_INSTRUCTION)
        sender = rts.RemoteTriggerSender.__new__(rts.RemoteTriggerSender)
        sender._registry = mock.MagicMock()
        sender._registry.load.return_value = {"label": "Bob"}
        wire: list[str] = []
        fed: list[str] = []

        def impl(endpoint_id, instruction, **kw):
            wire.append(instruction)
            return mock.MagicMock()

        with mock.patch.object(sender, "_send_impl", impl), \
             mock.patch.object(rts, "_record_feed_task",
                               lambda eid, tid, text, atts, lbl: fed.append(text)), \
             mock.patch.object(rts, "_record_feed_response", lambda *a, **kw: None):
            sender.send("bob", "", attachments=[{"name": "a.png"}])
            sender.send("bob", "hello", attachments=[{"name": "a.png"}])
        self.assertEqual(wire, [rts.ATTACHMENTS_ONLY_INSTRUCTION, "hello"])
        self.assertEqual(fed, ["", "hello"])


class TestRound10TaskDirection(_Base):
    """Round 10: friendship HMAC keys are identical on both ends, so a task WE
    signed for the peer verified against our own origin file — an eavesdropper
    could POST it back and have us run our own instruction "from" the peer."""

    def _recv(self, iid: str):
        return rtr.RemoteTriggerReceiver(origins_dir=self.origins, nonce_store=rtr.NonceStore(),
                                         forge_se=self.se, instance_id=iid, force_m1_only=True)

    def test_our_own_task_reflected_back_is_refused(self):
        resp = self._recv("iid-me").receive(self.envelope(sender_instance_id="iid-me"))
        self.assertEqual(resp.status, "rejected")
        self.assertIn("sender_is_self", self.se.reasons())

    def test_a_sender_other_than_the_bound_peer_is_refused(self):
        self.add_origin("bound", _peer_instance_id="iid-peer")
        recv = self._recv("iid-me")
        bad = recv.receive(self.envelope("bound", sender_instance_id="iid-other"))
        self.assertEqual(bad.status, "rejected")
        self.assertIn("sender_not_bound_peer", self.se.reasons())
        good = recv.receive(self.envelope("bound", sender_instance_id="iid-peer"))
        self.assertEqual(good.status, "ok")

    def test_an_unbound_or_legacy_sender_still_works(self):
        recv = self._recv("iid-me")
        self.assertEqual(recv.receive(self.envelope(sender_instance_id="iid-peer")).status, "ok")
        self.assertEqual(recv.receive(self.envelope(sender_instance_id="")).status, "ok")


class TestRound10AttachmentNames(unittest.TestCase):
    def test_trailing_newline_name_is_refused(self):
        import a2a_attachments as aa
        with self.assertRaises(aa.AttachmentError):
            aa.validate_attachments([{"name": "a\n", "mime": "text/plain",
                                      "sha256": hashlib.sha256(b"x").hexdigest(),
                                      "content_b64": "eA=="}])
        aa.validate_attachments([{"name": "a", "mime": "text/plain",
                                  "sha256": hashlib.sha256(b"x").hexdigest(),
                                  "content_b64": "eA=="}])
