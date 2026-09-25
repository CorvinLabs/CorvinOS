"""Regression tests — A2A HTTP surfaces (stdlib server + ingress), 2026-09-25.

* ping: a non-ASCII signature raised in hmac.compare_digest (500 for known
  origins only → origin-existence oracle); the pre-auth rate limit was keyed
  on the CLAIMED origin_id, so 60 unauthenticated pings naming a real peer
  locked that peer's genuine pings out.
* slowloris: the socket timeout was per recv(), the ingress peer gate + rate
  limit ran only in do_POST (after headers), and handler threads were
  unbounded. Now: accept-time gate, whole-request read deadline, thread caps.

All sockets are loopback, all servers ephemeral-port; nothing leaves the host.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import http.client
import json
import os
import secrets
import socket
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import a2a_http_server as hs  # noqa: E402
import a2a_ingress as ing  # noqa: E402
import remote_trigger_receiver as rtr  # noqa: E402


class _SE:
    def __init__(self):
        self.events = []

    def write_event(self, path, event_type, **kw):
        self.events.append((event_type, kw.get("details") or {}))
        return {"hash": "x"}

    def get_audit_chain_tail(self, path):
        return ""


class _Env(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="a2a-http-")
        self.home = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {
            "CORVIN_HOME": str(self.home),
            "VOICE_AUDIT_PATH": str(self.home / "audit.jsonl"),
            "CORVIN_A2A_ATTESTATION_DISABLED": "1",
        })
        self._env.start()
        os.environ.pop("REMOTE_ORIGINS_DIR", None)
        self.origins = self.home / "origins"
        self.origins.mkdir()
        self.hk = secrets.token_hex(32)
        self.rk = secrets.token_hex(32)
        for oid in ("peer1", "peer2"):
            p = self.origins / f"{oid}.json"
            p.write_text(json.dumps({"enabled": True, "hmac_key": self.hk,
                                     "recv_key": self.rk}))
            p.chmod(0o600)
        self.se = _SE()
        self.recv = rtr.RemoteTriggerReceiver(
            origins_dir=self.origins, nonce_store=rtr.NonceStore(),
            forge_se=self.se, instance_id="iid", force_m1_only=True)
        with hs._PING_RATE_LOCK:
            hs._PING_RATE_BUCKETS.clear()
            hs._PING_SEEN.clear()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def ping(self, oid="peer1", *, sig=None, issued_at=None, ping_id="p1"):
        now = int(time.time()) if issued_at is None else issued_at
        canon = json.dumps({"ping_id": ping_id, "issued_at": now, "origin_id": oid},
                           separators=(",", ":"), sort_keys=True)
        good = _hmac.new(bytes.fromhex(self.hk), canon.encode(),
                         hashlib.sha256).hexdigest()
        return {"ping_id": ping_id, "issued_at": now, "origin_id": oid,
                "signature": good if sig is None else sig}


class TestPingSignatureShape(_Env):
    def test_non_ascii_signature_is_opaque_403_not_raise(self):
        for oid in ("peer1", "peer2", "nosuch"):
            with self.subTest(oid=oid):
                st, body = hs.process_ping_request(
                    self.ping(oid, sig="é"), self.recv, client_addr=f"10.0.0.{len(oid)}")
                self.assertEqual((st, body), (403, {"reason": "ping_rejected"}))

    def test_valid_ping_still_200(self):
        st, body = hs.process_ping_request(self.ping(), self.recv,
                                           client_addr="10.0.0.1")
        self.assertEqual(st, 200)
        self.assertEqual(body["task_id"], "p1")


class TestPingRateLimitKeying(_Env):
    def test_unauthenticated_flood_naming_peer_does_not_lock_it_out(self):
        for i in range(int(hs._PING_PREAUTH_PAIR_RPM) + 80):  # attacker, own address, no key
            hs.process_ping_request(self.ping(sig="00" * 32), self.recv,
                                    client_addr="10.9.9.9")
        st, _ = hs.process_ping_request(self.ping(), self.recv,
                                        client_addr="10.0.0.1")
        self.assertEqual(st, 200)

    def test_unauthenticated_flood_without_address_does_not_lock_peer_out(self):
        for i in range(80):
            hs.process_ping_request(self.ping(sig="00" * 32), self.recv)
        st, _ = hs.process_ping_request(self.ping(), self.recv)
        self.assertEqual(st, 200)

    def test_flooding_address_is_limited_pre_auth(self):
        codes = [hs.process_ping_request(self.ping(sig="00" * 32), self.recv,
                                         client_addr="10.9.9.9")[0]
                 for _ in range(int(hs._PING_PREAUTH_PAIR_RPM) + 10)]
        self.assertIn(429, codes)

    def test_junk_flood_behind_a_proxy_does_not_starve_genuine_peers(self):
        # Round 2: nginx → 127.0.0.1 makes every peer share one address. A
        # flood of pings with made-up origin ids must not 429 a genuine one.
        for i in range(3000):
            hs.process_ping_request(dict(self.ping(sig="00" * 32), origin_id=f"junk-{i}"),
                                    self.recv, client_addr="127.0.0.1")
        st, _ = hs.process_ping_request(self.ping(), self.recv, client_addr="127.0.0.1")
        self.assertEqual(st, 200)

    def test_authenticated_origin_budget_charged_only_after_hmac(self):
        # Legit holder from many addresses: the per-ORIGIN post-auth bucket
        # (60/min) still caps it.
        codes = [hs.process_ping_request(self.ping(ping_id=f"p{i}"), self.recv,
                                         client_addr=f"10.1.{i // 250}.{i % 250}")[0]
                 for i in range(70)]
        self.assertEqual(codes[:60], [200] * 60)
        self.assertIn(429, codes[60:])


def _connect(port: int) -> socket.socket:
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    return s


def _recv_all(s: socket.socket, timeout: float = 5.0) -> bytes:
    s.settimeout(timeout)
    out = b""
    try:
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            out += chunk
    except (socket.timeout, OSError):
        pass
    return out


class TestStandaloneServerDeadlineAndCaps(_Env):
    def _serve(self, **kw):
        srv = hs.build_server(origins_dir=self.origins, nonce_store=rtr.NonceStore(),
                              forge_se=self.se, instance_id="iid", force_m1_only=True)
        for k, v in kw.items():
            setattr(srv, k, v)
        if "max_handler_threads" in kw:
            srv._slots = threading.BoundedSemaphore(kw["max_handler_threads"])
        srv.RequestHandlerClass.request_read_deadline_s = 1.0
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        return srv, srv.server_address[1]

    def test_trickling_client_is_cut_at_the_whole_request_deadline(self):
        srv, port = self._serve()
        s = _connect(port)
        s.sendall(b"P")
        t0 = time.monotonic()
        closed_at = None
        while time.monotonic() - t0 < 6:
            try:
                s.sendall(b"a")  # 1 byte / 0.2 s — never trips a per-recv timeout
            except OSError:
                closed_at = time.monotonic() - t0
                break
            time.sleep(0.2)
            if srv.active_handlers() == 0 and time.monotonic() - t0 > 1.2:
                closed_at = time.monotonic() - t0
                break
        s.close()
        self.assertIsNotNone(closed_at, "trickling connection outlived the deadline")
        self.assertLess(closed_at, 4.0)
        deadline = time.monotonic() + 3
        while srv.active_handlers() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(srv.active_handlers(), 0)

    def test_thread_cap_answers_503_instead_of_spawning(self):
        srv, port = self._serve(max_handler_threads=2)
        held = [_connect(port) for _ in range(2)]
        for s in held:
            s.sendall(b"P")
        time.sleep(0.3)
        extra = _connect(port)
        data = _recv_all(extra, timeout=2)
        self.assertIn(b"503", data.split(b"\r\n", 1)[0])
        self.assertIn(b"overloaded", data)
        for s in held + [extra]:
            s.close()

    def test_legit_receive_over_http_still_ok(self):
        srv, port = self._serve()
        e = {"task_id": "t1", "nonce": secrets.token_hex(8), "issued_at": time.time(),
             "origin_id": "peer1", "instruction": "hi", "result_schema": {},
             "ttl_s": 60, "sender_instance_id": "s", "attachments": [],
             "signature": ""}
        te = rtr.TaskEnvelope.from_dict(e)
        e["signature"] = _hmac.new(bytes.fromhex(self.hk), te.canonical_payload(),
                                   hashlib.sha256).hexdigest()
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("POST", "/v1/a2a/receive", body=json.dumps(e),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        self.assertEqual(r.status, 200)
        self.assertEqual(json.loads(r.read())["status"], "ok")

    def test_non_ascii_signature_over_http_is_200_rejected_not_500(self):
        srv, port = self._serve()
        e = {"task_id": "t1", "nonce": "n", "issued_at": time.time(),
             "origin_id": "peer1", "instruction": "hi", "result_schema": {},
             "ttl_s": 60, "sender_instance_id": "s", "attachments": [],
             "signature": "é"}
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("POST", "/v1/a2a/receive", body=json.dumps(e).encode(),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        self.assertEqual(r.status, 200)
        self.assertEqual(json.loads(r.read())["status"], "rejected")


class TestIngressAcceptTimeGate(_Env):
    def _serve(self):
        cfg = ing.IngressConfig(enabled=True, port=0, host="127.0.0.1")
        stats = ing.IngressStats()
        srv = ing.build_ingress_server(receiver=self.recv, endpoints_dir=self.home,
                                       pending_dir=self.home, config=cfg, stats=stats)
        srv.RequestHandlerClass.request_read_deadline_s = 1.0
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        return srv, srv.server_address[1], stats

    def test_disallowed_peer_refused_before_sending_any_byte(self):
        with mock.patch.object(ing, "peer_allowed", return_value=False):
            srv, port, stats = self._serve()
            s = _connect(port)
            data = _recv_all(s, timeout=2)  # we send NOTHING
        self.assertIn(b"403", data.split(b"\r\n", 1)[0])
        self.assertIn(b"peer_not_allowed", data)
        self.assertEqual(stats.snapshot()["rejected_peer"], 1)
        self.assertEqual(srv.active_handlers(), 0)

    def test_rate_limit_applies_at_accept_time(self):
        real = ing._RateLimiter
        with mock.patch.object(ing, "_RateLimiter",
                               lambda: real(burst=2, per_s=0.0)):
            srv, port, stats = self._serve()
        held = [_connect(port) for _ in range(2)]
        third = _connect(port)
        data = _recv_all(third, timeout=2)
        self.assertIn(b"429", data.split(b"\r\n", 1)[0])
        self.assertEqual(stats.snapshot()["rate_limited"], 1)
        for s in held + [third]:
            s.close()

    def test_per_peer_cap(self):
        with mock.patch.object(ing, "_MAX_PER_PEER", 2):
            srv, port, stats = self._serve()
        held = [_connect(port) for _ in range(2)]
        for s in held:
            s.sendall(b"P")
        time.sleep(0.3)
        extra = _connect(port)
        data = _recv_all(extra, timeout=2)
        self.assertIn(b"503", data.split(b"\r\n", 1)[0])
        self.assertEqual(stats.snapshot()["overloaded"], 1)
        for s in held + [extra]:
            s.close()

    def test_signed_ping_through_ingress_is_200(self):
        srv, port, stats = self._serve()
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("POST", "/v1/a2a/ping", body=json.dumps(self.ping()),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        self.assertEqual(r.status, 200, r.read())
        self.assertEqual(stats.snapshot()["served"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestPingReplayDoesNotDrainBudget(_Env):
    def test_replayed_ping_is_answered_without_spending_the_peers_budget(self):
        replay = self.ping()
        codes = [hs.process_ping_request(dict(replay), self.recv, client_addr="10.0.0.9")[0]
                 for _ in range(80)]
        self.assertNotIn(429, codes, "a replayed ping drained the origin budget")
        self.assertEqual(hs.process_ping_request(self.ping(ping_id="fresh-1"), self.recv,
                                                 client_addr="10.0.0.1")[0], 200)
