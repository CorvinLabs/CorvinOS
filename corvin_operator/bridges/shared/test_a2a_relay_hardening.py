"""Regression tests for the 2026-09-25 A2A relay hardening round (a2a_relay.py).

  2. TestListenerConcurrency  — the listener awaited each delivery inline: one
                                long worker run blocked every other peer's
                                ping/task on the socket (head-of-line).
  4. TestFrameLimits          — frames > 512 KB were refused with an error
                                frame WITHOUT task_id; the client ignored it and
                                waited out its timeout; limits disagreed. A
                                1 MiB-attachment envelope must cross the relay.
  6. TestSharedKidQueueing    — a delivery reaching only the SENDER's own
                                listener counted "delivered" and was lost.
  7. TestMetricsWiring        — record_* had zero callers; /metrics was zeros.
  +  TestRelayEndToEnd        — real relay (uvicorn), real listeners, real
                                receivers/senders: a ~900 KB attachment, and 10
                                friendships sharing one relay (11 listeners).

Run: python -m pytest corvin_operator/bridges/shared/test_a2a_relay_hardening.py
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import secrets
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import a2a_friendship as ft  # noqa: E402
import a2a_relay  # noqa: E402

_LOG = logging.getLogger("test.relay")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _deliver_frame(kid: str, key: str, task_id: str, body: dict) -> str:
    n, c = ft.encrypt_for_relay(key, json.dumps(body).encode())
    return json.dumps({"type": "deliver", "to_kid": kid, "from_kid": f"{kid}:reply:{task_id}",
                       "nonce": n, "ciphertext": c, "task_id": task_id})


class _FakeWS:
    """Minimal listener-side socket: yields the given frames, records sends."""

    def __init__(self, frames: list[str], name: str = "ws") -> None:
        self.frames = frames
        self.name = name
        self.sent: list[tuple[float, dict]] = []
        self.t0 = time.monotonic()

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for f in self.frames:
            yield f

    async def send(self, txt: str) -> None:
        self.sent.append((round(time.monotonic() - self.t0, 2), json.loads(txt)))


class _SlowReceiver:
    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def receive(self, payload):  # noqa: ANN001
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(self.delay)
        with self._lock:
            self.active -= 1

        class R:
            def to_dict(_s):
                return {"task_id": payload["task_id"], "status": "ok", "signature": "x"}
        return R()


# ── 2. Listener concurrency ─────────────────────────────────────────────

class TestListenerConcurrency(unittest.TestCase):
    def _listener(self, receiver) -> a2a_relay.RelayListener:
        return a2a_relay.RelayListener(relay_url="ws://unused", receiver=receiver,
                                       origins_dir="/nonexistent", instance_id="me-instance")

    def test_slow_task_does_not_block_ping(self):
        """hol.py: a 2 s worker run for peer A must not delay peer B's ping."""
        ka, kb = secrets.token_hex(32), secrets.token_hex(32)
        frames = [
            _deliver_frame("peerA", ka, "t1", {"task_id": "t1", "instruction": "long",
                                               "sender_instance_id": "other"}),
            _deliver_frame("peerB", kb, "p1", {"ping_id": "p1", "issued_at": int(time.time()),
                                               "origin_id": "peerB", "signature": "00"}),
        ]
        ws = _FakeWS(frames)
        asyncio.run(self._listener(_SlowReceiver(2.0))._serve(
            ws, {"peerA": ka, "peerB": kb}, set(), _LOG))
        order = [(t, m["to_kid"]) for t, m in ws.sent]
        self.assertEqual([k for _t, k in order], ["peerB:reply:p1", "peerA:reply:t1"])
        self.assertLess(order[0][0], 1.0, order)   # ping answered while the task runs
        # responses are task_id-correlated, never positional
        self.assertEqual({m["task_id"] for _t, m in ws.sent}, {"p1", "t1"})

    def test_heavy_dispatch_is_bounded_and_pings_bypass_the_bound(self):
        key = secrets.token_hex(32)
        n_tasks = a2a_relay._MAX_CONCURRENT_DELIVERIES + 4
        frames = [_deliver_frame("peer", key, f"t{i}", {"task_id": f"t{i}", "instruction": "x",
                                                         "sender_instance_id": "other"})
                  for i in range(n_tasks)]
        frames.append(_deliver_frame("peer", key, "p1", {"ping_id": "p1", "issued_at": int(time.time()),
                                                          "origin_id": "peer", "signature": "00"}))
        recv = _SlowReceiver(0.6)
        ws = _FakeWS(frames)
        asyncio.run(self._listener(recv)._serve(ws, {"peer": key}, set(), _LOG))
        self.assertEqual(recv.max_active, a2a_relay._MAX_CONCURRENT_DELIVERIES)
        self.assertEqual(len(ws.sent), n_tasks + 1)
        ping_t = next(t for t, m in ws.sent if m["task_id"] == "p1")
        self.assertLess(ping_t, 0.5)

    def test_inflight_delivery_answers_on_the_new_socket_after_reconnect(self):
        key = secrets.token_hex(32)
        old = _FakeWS([_deliver_frame("peer", key, "t1", {"task_id": "t1", "instruction": "x",
                                                            "sender_instance_id": "other"})], "old")
        new = _FakeWS([], "new")
        lst = self._listener(_SlowReceiver(0.5))

        async def scenario():
            lst._ws = old
            await lst._serve(old, {"peer": key}, set(), _LOG, drain=False)  # socket "drops"
            lst._ws = new                                                   # reconnected
            await asyncio.gather(*list(lst._inflight))
        asyncio.run(scenario())
        self.assertEqual(old.sent, [])
        self.assertEqual([m["task_id"] for _t, m in new.sent], ["t1"])

    def test_own_task_still_dropped(self):
        key = secrets.token_hex(32)
        ws = _FakeWS([_deliver_frame("peer", key, "t1", {"task_id": "t1", "instruction": "x",
                                                           "sender_instance_id": "me-instance"})])
        asyncio.run(self._listener(_SlowReceiver(0))._serve(ws, {"peer": key}, set(), _LOG))
        self.assertEqual(ws.sent, [])


# ── local relay server helper ───────────────────────────────────────────

class _RelayServer:
    def __init__(self, app=None, state: a2a_relay.RelayState | None = None) -> None:
        import uvicorn
        from fastapi import FastAPI
        self.state = state or a2a_relay.RelayState()
        if app is None:
            app = FastAPI()
            app.include_router(a2a_relay.build_relay_router(self.state))
        self.port = _free_port()
        self.url = f"ws://127.0.0.1:{self.port}/v1/a2a/relay/connect"
        cfg = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error",
                             ws_max_size=a2a_relay._WS_CLIENT_MAX_SIZE)
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started and time.time() < deadline:
            time.sleep(0.05)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(5)


# ── 4. Frame limits + error correlation ─────────────────────────────────

class TestFrameLimits(unittest.TestCase):
    def test_limits_are_one_consistent_chain(self):
        r = a2a_relay
        self.assertGreaterEqual(r._MAX_MESSAGE_BYTES, r._MAX_CIPHERTEXT_HEX + 512)
        self.assertGreater(r._WS_CLIENT_MAX_SIZE, r._MAX_MESSAGE_BYTES)
        # a max-size A2A envelope: 1 MiB attachments, base64 (4/3) + JSON overhead
        env_bytes = (1024 * 1024 * 4) // 3 + 64 * 1024
        self.assertLessEqual(2 * (env_bytes + 16), r._MAX_CIPHERTEXT_HEX)

    def test_local_size_check_fails_before_any_network(self):
        huge = "ab" * (a2a_relay._MAX_CIPHERTEXT_HEX // 2 + 10)
        t0 = time.monotonic()
        with self.assertRaises(a2a_relay.RelayTransportError) as cm:
            asyncio.run(a2a_relay.relay_deliver_and_wait(
                relay_url="ws://127.0.0.1:1/unused", my_kid="k:reply:t", my_relay_auth_key="cd" * 32,
                to_kid="k", nonce_hex="00", ciphertext_hex=huge, task_id="t", timeout_s=10))
        self.assertEqual(cm.exception.reason, "message_too_large")
        self.assertFalse(cm.exception.maybe_delivered)
        self.assertLess(time.monotonic() - t0, 1.0)

    def test_old_relay_error_without_task_id_fails_immediately(self):
        """An already-deployed relay (512 KB cap, no task_id on its error
        frame) must not make the new client wait out its timeout."""
        import websockets

        async def old_relay(ws):
            async for raw in ws:
                m = json.loads(raw) if len(raw) < 512 * 1024 else None
                if m and m.get("type") == "register":
                    await ws.send(json.dumps({"type": "registered", "kid": m["kid"]}))
                else:
                    await ws.send(json.dumps({"type": "error", "reason": "message_too_large"}))

        async def scenario():
            async with websockets.serve(old_relay, "127.0.0.1", 0, max_size=None) as srv:
                port = srv.sockets[0].getsockname()[1]
                t0 = time.monotonic()
                with self.assertRaises(a2a_relay.RelayTransportError) as cm:
                    await a2a_relay.relay_deliver_and_wait(
                        relay_url=f"ws://127.0.0.1:{port}", my_kid="k:reply:t",
                        my_relay_auth_key="cd" * 32, to_kid="k", nonce_hex="00",
                        ciphertext_hex="ab" * 400_000, task_id="t", timeout_s=10)
                return cm.exception, time.monotonic() - t0
        exc, took = asyncio.run(scenario())
        self.assertEqual(exc.reason, "message_too_large")
        self.assertFalse(exc.maybe_delivered)
        self.assertLess(took, 2.0)

    def test_new_relay_echoes_task_id_on_oversize_and_invalid_frames(self):
        import websockets
        srv = _RelayServer()
        try:
            async def scenario():
                async with websockets.connect(srv.url, max_size=None) as ws:
                    big = json.dumps({"type": "deliver", "to_kid": "k", "from_kid": "k:r",
                                      "nonce": "00", "ciphertext": "a" * (a2a_relay._MAX_MESSAGE_BYTES + 1),
                                      "task_id": "task-big"})
                    await ws.send(big)
                    e1 = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    await ws.send(json.dumps({"type": "deliver", "task_id": "task-bad"}))
                    e2 = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    return e1, e2
            e1, e2 = asyncio.run(scenario())
        finally:
            srv.stop()
        self.assertEqual(e1, {"type": "error", "reason": "message_too_large", "task_id": "task-big"})
        self.assertEqual(e2, {"type": "error", "reason": "invalid_deliver", "task_id": "task-bad"})

    def test_listener_and_client_accept_frames_above_websockets_default(self):
        """The old client kept websockets' 1 MiB max_size: a legitimate big
        response tore the connection down (close 1009)."""
        srv = _RelayServer()
        key = secrets.token_hex(32)
        try:
            async def peer(ready: asyncio.Event):
                import websockets
                async with websockets.connect(srv.url, max_size=a2a_relay._WS_CLIENT_MAX_SIZE) as ws:
                    await ws.send(json.dumps({"type": "register", "kid": "kidB",
                                              "relay_auth_key": ft.derive_relay_auth_key(key)}))
                    await ws.recv()
                    ready.set()
                    async for raw in ws:
                        m = json.loads(raw)
                        if m.get("type") == "deliver":
                            await ws.send(json.dumps({  # 2.4 MB response frame
                                "type": "deliver", "to_kid": m["from_kid"], "from_kid": "kidB",
                                "nonce": "00", "ciphertext": "ab" * 1_200_000, "task_id": m["task_id"]}))

            async def scenario():
                ready = asyncio.Event()
                t = asyncio.create_task(peer(ready))
                await ready.wait()
                r = await a2a_relay.relay_deliver_and_wait(
                    relay_url=srv.url, my_kid="kidB:reply:t1", my_relay_auth_key="cd" * 32,
                    to_kid="kidB", nonce_hex="00", ciphertext_hex="ab" * 1_000_000,
                    task_id="t1", timeout_s=10)
                t.cancel()
                return r
            r = asyncio.run(scenario())
        finally:
            srv.stop()
        self.assertEqual(len(r["ciphertext"]), 2_400_000)


# ── 6. Shared-kid fan-out: a delivery only to the sender's own listener ─

class _WS:
    def __init__(self) -> None:
        self.got: list[str] = []

    async def send_text(self, t: str) -> None:
        self.got.append(json.loads(t)["task_id"])


class TestSharedKidQueueing(unittest.TestCase):
    def test_delivery_to_only_own_listener_is_queued_for_the_peer(self):
        st = a2a_relay.RelayState(metrics=mock.Mock())
        auth, kid = "ab" * 32, "kidAB"
        tag_a, tag_b = a2a_relay.instance_tag(kid, "A"), a2a_relay.instance_tag(kid, "B")
        wa, wb = _WS(), _WS()
        ca = st.open_connection(wa)
        st.register(ca, kid, auth, tag_a)
        st.note_registered_kid(ca, kid)
        cb = st.open_connection(wb)
        st.register(cb, kid, auth, tag_b)
        st.note_registered_kid(cb, kid)
        st.close_connection(cb)  # B's listener drops briefly

        out = asyncio.run(st.deliver(kid, {"type": "deliver", "to_kid": kid, "from_kid": kid + ":reply:t1",
                                           "task_id": "t1", "nonce": "00", "ciphertext": "00"},
                                     from_instance_tag=tag_a))
        self.assertEqual(out, "queued")
        self.assertEqual(wa.got, [])                          # own listener not bothered
        # A's own listener reconnecting first must NOT consume B's message
        self.assertEqual(st.flush_queue(kid, tag_a), [])
        cb2 = st.open_connection(wb)
        st.register(cb2, kid, auth, tag_b)
        self.assertEqual([m["task_id"] for m in st.flush_queue(kid, tag_b)], ["t1"])

    def test_live_peer_gets_it_directly_and_untagged_clients_keep_old_behaviour(self):
        st = a2a_relay.RelayState(metrics=mock.Mock())
        auth, kid = "ab" * 32, "kidAB"
        wa, wb = _WS(), _WS()
        ca = st.open_connection(wa)
        st.register(ca, kid, auth, a2a_relay.instance_tag(kid, "A"))
        cb = st.open_connection(wb)
        st.register(cb, kid, auth, a2a_relay.instance_tag(kid, "B"))
        msg = {"type": "deliver", "to_kid": kid, "from_kid": "x", "task_id": "t1",
               "nonce": "00", "ciphertext": "00"}
        self.assertEqual(asyncio.run(st.deliver(
            kid, msg, from_instance_tag=a2a_relay.instance_tag(kid, "A"))), "delivered")
        self.assertEqual((wa.got, wb.got), ([], ["t1"]))
        # old sender (no tag): fan-out to both, as before
        self.assertEqual(asyncio.run(st.deliver(kid, dict(msg, task_id="t2"))), "delivered")
        self.assertEqual((wa.got, wb.got), (["t2"], ["t1", "t2"]))


# ── 7. Metrics wiring ───────────────────────────────────────────────────

class TestMetricsWiring(unittest.TestCase):
    def test_relay_state_feeds_the_collector(self):
        m = mock.Mock()
        st = a2a_relay.RelayState(metrics=m)
        c = st.open_connection(_WS())
        st.register(c, "kid1", "ab" * 32)
        st.register(c, "kid1", "cd" * 32)  # mismatch
        asyncio.run(st.deliver("kid1", {"task_id": "t"}))
        asyncio.run(st.deliver("unknown", {"task_id": "t"}))
        st.close_connection(c)
        regs = [call.args for call in m.record_registration.call_args_list]
        self.assertEqual(regs, [("_default", "success"), ("_default", "auth_key_mismatch")])
        outs = [call.args[1] for call in m.record_delivery.call_args_list]
        self.assertEqual(outs, ["delivered", "dropped"])
        self.assertEqual([call.args for call in m.update_active_connections.call_args_list],
                         [("_default", 1), ("_default", 0)])

    def test_listener_records_handshake(self):
        m = mock.Mock()
        lst = a2a_relay.RelayListener(relay_url="ws://unused", receiver=None,
                                      origins_dir="/nonexistent", instance_id="me")
        lst._register_sent_at = {"k1": time.monotonic(), "k2": time.monotonic()}
        ws = _FakeWS([json.dumps({"type": "registered", "kid": "k1"}),
                      json.dumps({"type": "register_rejected", "kid": "k2", "reason": "auth_key_mismatch"})])
        with mock.patch.object(a2a_relay, "_metrics_collector", lambda: m):
            asyncio.run(lst._serve(ws, {}, set(), _LOG))
        self.assertEqual([c.args[:2] for c in m.record_handshake.call_args_list],
                         [("_default", "success"), ("_default", "rejected")])

    def test_relay_app_exposes_metrics(self):
        from fastapi.testclient import TestClient
        m = mock.Mock()
        m.generate_metrics_text.return_value = b"a2a_relay_deliver_attempts_total 3\n"
        with mock.patch.object(a2a_relay, "_metrics_collector", lambda: m):
            r = TestClient(a2a_relay.build_relay_app()).get("/metrics")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"a2a_relay_deliver_attempts_total 3", r.content)

    def test_metrics_module_imports_prometheus_when_present(self):
        """a2a_relay_metrics imported a nonexistent ``Registry`` name, so
        PROMETHEUS_AVAILABLE was False even with prometheus_client installed."""
        import importlib
        try:
            import prometheus_client  # noqa: F401
        except ImportError:
            self.skipTest("prometheus_client not installed in this interpreter")
        import a2a_relay_metrics
        importlib.reload(a2a_relay_metrics)
        self.assertTrue(a2a_relay_metrics.PROMETHEUS_AVAILABLE)


# ── End-to-end: real relay, real listeners, real receivers/senders ──────

IID = {n: f"{n * 8}-0000-4000-8000-{n * 12}" for n in "abcdefghijklm"}


class _Instance:
    def __init__(self, root: Path, name: str) -> None:
        import remote_trigger_receiver as rtr
        self.name = name
        self.iid = IID[name]
        self.origins = root / name / "origins"
        self.endpoints = root / name / "endpoints"
        self.origins.mkdir(parents=True)
        self.endpoints.mkdir(parents=True)
        self.got: list[dict] = []
        outer = self

        class Capturing(rtr.RemoteTriggerReceiver):
            def receive(self, env):  # noqa: ANN001
                outer.got.append(env)
                return super().receive(env)
        self.receiver = Capturing(origins_dir=self.origins, nonce_store=rtr.NonceStore(),
                                  instance_id=self.iid, force_m1_only=True, forge_se=mock.Mock())

    def sender(self):
        import remote_trigger_sender as rts
        return rts.RemoteTriggerSender(endpoints_dir=self.endpoints, instance_id=self.iid,
                                       forge_se=mock.Mock())


def _pair(x: _Instance, y: _Instance, dead_url: str) -> str:
    """Friendship between x and y: both hold the same kid + key; the direct
    URL is dead so every exchange must go through the relay."""
    kid, key = secrets.token_hex(8), secrets.token_hex(32)
    tok = ft.FriendshipToken(kid=kid, key=key, url=dead_url, label=None, expires=None)
    for inst in (x, y):
        o = ft.to_origin_dict(tok)
        o["spawn_worker"] = False
        for d, data in ((inst.origins, o), (inst.endpoints, ft.to_endpoint_dict(tok))):
            p = d / f"{kid}.json"
            p.write_text(json.dumps(data))
            p.chmod(0o600)
    return kid


class TestRelayEndToEnd(unittest.TestCase):
    def setUp(self) -> None:
        import remote_trigger_sender as rts
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.relay = _RelayServer()
        self.dead_url = f"http://127.0.0.1:{_free_port()}"  # connection refused
        self._patches = [
            mock.patch.dict(os.environ, {"CORVIN_HOME": str(self.root / "home"),
                                         "CORVIN_A2A_ATTESTATION_DISABLED": "1"}),
            mock.patch("corvin_core.feature_flags.is_enabled", return_value=True),
            mock.patch("a2a_friendship.get_my_relay_url", return_value=self.relay.url),
            mock.patch("remote_trigger_sender._record_feed_task", lambda *a, **k: None),
            mock.patch("remote_trigger_sender._record_feed_response", lambda *a, **k: None),
            mock.patch.object(rts.RemoteTriggerSender, "_build_network_attestation",
                              staticmethod(lambda cfg: None)),
        ]
        for p in self._patches:
            p.start()
        os.environ.pop(rts._REMOTE_ENDPOINTS_ENV, None)
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.loop_thread.start()
        self.listeners: list[a2a_relay.RelayListener] = []

    def tearDown(self) -> None:
        for lst in self.listeners:
            self.loop.call_soon_threadsafe(lst.stop)
        time.sleep(0.3)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.loop_thread.join(5)
        self.relay.stop()
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def _listen(self, inst: _Instance, n_kids: int) -> None:
        lst = a2a_relay.RelayListener(relay_url=self.relay.url, receiver=inst.receiver,
                                      origins_dir=inst.origins, instance_id=inst.iid)
        self.listeners.append(lst)
        asyncio.run_coroutine_threadsafe(lst.run_forever(reconnect_backoff_s=0.5), self.loop)
        deadline = time.time() + 15
        while lst.status.get("registered", 0) < n_kids and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(lst.status.get("registered"), n_kids, lst.status)

    def test_900kb_attachment_crosses_the_relay(self):
        from a2a_attachments import Attachment
        a, b = _Instance(self.root, "a"), _Instance(self.root, "b")
        kid = _pair(a, b, self.dead_url)
        self._listen(a, 1)
        self._listen(b, 1)
        blob = secrets.token_bytes(900 * 1024)
        att = Attachment.from_bytes(name="data.bin", mime="application/octet-stream", content=blob)
        res = a.sender().send(kid, "please look at the attachment", attachments=[att], timeout_s=30)
        self.assertTrue(res.ok, res)
        self.assertEqual(res.instance_id, b.iid)
        self.assertEqual(len(b.got), 1)            # exactly the peer ran it — A's listener skipped it
        self.assertEqual(a.got, [])
        self.assertEqual(b.got[0]["attachments"][0]["sha256"], att.sha256)

    def test_ten_friendships_share_one_relay(self):
        hub = _Instance(self.root, "a")
        peers = [_Instance(self.root, n) for n in "bcdefghijk"]
        kids = [_pair(hub, p, self.dead_url) for p in peers]
        self._listen(hub, 10)
        for p in peers:
            self._listen(p, 1)
        self.assertEqual(len(self.listeners), 11)

        def hub_to(i):
            return hub.sender().send(kids[i], f"hello peer {i}", timeout_s=30)

        def peer_to_hub(i):
            return peers[i].sender().send(kids[i], f"hello hub from {i}", timeout_s=30)
        with concurrent.futures.ThreadPoolExecutor(20) as ex:
            futs = [ex.submit(hub_to, i) for i in range(10)] + \
                   [ex.submit(peer_to_hub, i) for i in range(10)]
            results = [f.result(60) for f in futs]
        self.assertTrue(all(r.ok for r in results), [r for r in results if not r.ok])
        self.assertEqual([r.instance_id for r in results[:10]], [p.iid for p in peers])
        self.assertEqual({r.instance_id for r in results[10:]}, {hub.iid})
        self.assertEqual(len(hub.got), 10)
        self.assertTrue(all(len(p.got) == 1 for p in peers))
        # and every pairing still answers a ping over the relay
        for i, p in enumerate(peers):
            self.assertTrue(p.sender().ping(kids[i]).reachable)


if __name__ == "__main__":
    unittest.main()


class TestPingsNotStarvedByWorkerRuns(unittest.TestCase):
    """Round 2: heavy deliveries ran on asyncio's DEFAULT executor, which pings
    share — on a small host (default pool = cpu+4 threads) a few worker runs
    queued every ping behind them. Heavy work now has its own executor."""

    def test_ping_answers_while_more_heavy_runs_than_default_threads(self):
        import types
        stub = types.ModuleType("a2a_http_server")
        stub.process_ping_request = lambda payload, receiver, **kw: (200, {"ok": True, "ping_id": payload["ping_id"]})

        class _Resp:
            def __init__(self, tid):
                self.tid = tid

            def to_dict(self):
                return {"task_id": self.tid, "status": "ok"}

        class _Slow:
            def receive(self, payload):
                time.sleep(2.0)
                return _Resp(payload["task_id"])

        class _WS:
            def __init__(self):
                self.sent = []

            async def send(self, frame):
                self.sent.append(json.loads(frame))

        hk = secrets.token_hex(32)

        def frame(payload, tid):
            n, c = ft.encrypt_for_relay(hk, json.dumps(payload).encode())
            return {"type": "deliver", "to_kid": "kidA", "from_kid": f"kidA:reply:{tid}",
                    "nonce": n, "ciphertext": c, "task_id": tid}

        async def main():
            loop = asyncio.get_running_loop()
            loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=4))
            listener = a2a_relay.RelayListener(relay_url="ws://x", receiver=_Slow(),
                                               origins_dir="/nonexistent", instance_id="me-0000")
            ws = _WS()
            listener._ws = ws
            heavy = [asyncio.create_task(listener._handle_deliver(
                ws, frame({"task_id": f"t{i}", "instruction": "x", "sender_instance_id": "peer"}, f"t{i}"),
                {"kidA": hk})) for i in range(10)]
            await asyncio.sleep(0.2)
            t0 = time.monotonic()
            await listener._handle_deliver(ws, frame({"ping_id": "p1", "issued_at": 0, "origin_id": "o"}, "p1"),
                                           {"kidA": hk})
            elapsed = time.monotonic() - t0
            await asyncio.gather(*heavy)
            return elapsed

        with mock.patch.dict(sys.modules, {"a2a_http_server": stub}):
            elapsed = asyncio.run(main())
        self.assertLess(elapsed, 1.0, f"ping waited {elapsed:.2f}s behind worker runs")


class TestPerKidQueueBudget(unittest.TestCase):
    """Round 9: the queue had only a GLOBAL byte cap, and one kid's 32 slots
    of ~8 MB frames exceeded it — a single self-registered kid filled the whole
    budget, so every other briefly-offline peer's delivery was dropped."""

    class _WS:
        async def send_text(self, t):
            pass

    def _offline_slot(self, st, kid: str, key: str) -> None:
        cid = st.open_connection(self._WS())
        self.assertIsNone(st.register(cid, kid, key))
        st.note_registered_kid(cid, kid)
        st.close_connection(cid)

    def test_one_kid_cannot_monopolise_the_queue(self):
        async def scenario():
            st = a2a_relay.RelayState(metrics=object())
            self._offline_slot(st, "attacker-kid", "a" * 64)
            big = "0" * (a2a_relay._MAX_CIPHERTEXT_HEX - 64)
            outs = [await st.deliver("attacker-kid", {
                "type": "deliver", "to_kid": "attacker-kid", "from_kid": "x",
                "nonce": "n", "ciphertext": big, "task_id": f"t{i}"}) for i in range(12)]
            self._offline_slot(st, "victim-kid", "b" * 64)
            victim = await st.deliver("victim-kid", {
                "type": "deliver", "to_kid": "victim-kid", "from_kid": "y",
                "nonce": "n", "ciphertext": "ab" * 100, "task_id": "v1"})
            return outs, victim, st._queued_bytes

        outs, victim, queued = asyncio.run(scenario())
        self.assertEqual(outs[0], "queued", "one full-size frame per offline recipient still queues")
        self.assertEqual(set(outs[1:]), {"dropped"})
        self.assertLessEqual(queued, a2a_relay._MAX_QUEUE_BYTES_PER_KID + 1000)
        self.assertEqual(victim, "queued")
