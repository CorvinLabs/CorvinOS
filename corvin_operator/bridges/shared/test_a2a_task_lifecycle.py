"""ADR-2242 — A2A message lifecycle: stage state machine, signed task-status query,
sender-side stage observation.

Layers covered:
  * ``a2a_task_state``: monotonic / terminal-immutable / origin-bound / restart-honest.
  * the REAL ``RemoteTriggerReceiver.receive()`` with a worker that blocks, while the
    REAL ``process_ping_request`` core answers signed task-status queries — the stage
    ``processing`` must be observable mid-run, and the order must be preserved.
  * adversarial queries: forged / re-aimed signature, foreign origin, unknown / oversized
    ids, and a tampered answer (the signed response covers ``task_stage``).
  * pre-authentication traffic must never grow the stage store.
  * the sender's ``_StagePoller`` decisions (changes only, stop at terminal, stop on
    unsupported peer, keep asking after an unconfirmed send).

The cross-process proof over a real relay lives in ``test_a2a_zero_config_e2e.py``.
"""
from __future__ import annotations

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
import uuid
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_mock_se = mock.MagicMock()
_mock_se.write_event = mock.MagicMock(return_value={"hash": "abc"})
_patches = [mock.patch("remote_trigger_receiver._forge_se", _mock_se)]
_saved_license: dict[str, object | None] = {}


def setUpModule() -> None:
    import spawn_gates  # noqa: PLC0415
    _patches.append(mock.patch.object(spawn_gates, "check_l44", lambda *a, **kw: None))
    for p in _patches:
        p.start()
    for name in ("license.compute_quota", "license.limits"):
        _saved_license[name] = sys.modules.get(name)
        sys.modules[name] = None  # type: ignore[assignment]


def tearDownModule() -> None:
    for p in _patches:
        p.stop()
    for name, mod in _saved_license.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


import a2a_feed  # noqa: E402
import a2a_http_server as srv  # noqa: E402
import a2a_task_state as ats  # noqa: E402
import remote_trigger_receiver as rtr  # noqa: E402
import remote_trigger_sender as rts  # noqa: E402

HMAC_KEY = "f" * 64
RECV_KEY = "e" * 64
ORIGIN = "lifecycle-origin"


def _write_origin(d: Path, origin: str = ORIGIN, *, spawn_worker: bool = True) -> None:
    p = d / f"{origin}.json"
    p.write_text(json.dumps({
        "origin_id": origin, "hmac_key": HMAC_KEY, "recv_key": RECV_KEY, "enabled": True,
        "max_ttl_s": 300, "allowed_personas": ["assistant"], "spawn_worker": spawn_worker}))
    p.chmod(0o600)


def _envelope(task_id: str, *, key: str = HMAC_KEY, origin: str = ORIGIN) -> dict:
    env = {"task_id": task_id, "nonce": secrets.token_hex(32), "issued_at": time.time(),
           "origin_id": origin, "instruction": "do the thing", "result_schema": {},
           "ttl_s": 60, "sender_instance_id": "sender-iid", "attachments": [], "signature": ""}
    payload = {k: v for k, v in env.items() if k != "signature"}
    env["signature"] = _hmac.new(bytes.fromhex(key), json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(),
        hashlib.sha256).hexdigest()
    return env


def _query(task_id: str, *, key: str = HMAC_KEY, origin: str = ORIGIN,
           task_sig_key: str | None = None, task_id_in_sig: str | None = None) -> dict:
    """A signed ping carrying a task-stage query, exactly as the sender builds it."""
    ping_id, issued_at = str(uuid.uuid4()), int(time.time())
    base = {"ping_id": ping_id, "issued_at": issued_at, "origin_id": origin}
    sign = lambda d, k: _hmac.new(bytes.fromhex(k), json.dumps(  # noqa: E731
        d, separators=(",", ":"), sort_keys=True).encode(), hashlib.sha256).hexdigest()
    req = dict(base, signature=sign(base, key), task_id=task_id)
    req["task_sig"] = sign(dict(base, task_id=task_id_in_sig or task_id), task_sig_key or key)
    return req


@dataclass
class _Ev:
    type: str
    text: str | None = None
    usage: dict | None = None
    error: str | None = None


class _BlockingEngine:
    """spawn() blocks until released — the worker is 'processing' for as long as we say."""
    name = "fake"
    capabilities: dict = {}

    def __init__(self, started: threading.Event, release: threading.Event, out: str = '{"summary":"ok"}'):
        self._started, self._release, self._out = started, release, out

    def spawn(self, prompt, **kwargs):
        self._started.set()
        self._release.wait(20)
        return iter([_Ev(type="text_delta", text=self._out),
                     _Ev(type="turn_completed", text=self._out)])

    def cancel(self):
        pass


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.origins = self.tmp / "origins"
        self.origins.mkdir()
        self._env = mock.patch.dict(os.environ, {"CORVIN_A2A_FEED_DIR": str(self.tmp / "feed")})
        self._env.start()
        ats._reset_for_tests()

    def tearDown(self) -> None:
        self._env.stop()
        ats._reset_for_tests()
        self._tmp.cleanup()


class TestStateMachine(_Base):
    def test_forward_only_and_terminal_immutable(self):
        t = "t-1"
        self.assertEqual(ats.record_stage("o", t, "delivered")["stage"], "delivered")
        self.assertEqual(ats.record_stage("o", t, "processing")["prev"], "delivered")
        self.assertIsNone(ats.record_stage("o", t, "accepted"), "a lower rank must be dropped")
        self.assertIsNone(ats.record_stage("o", t, "processing"), "same stage twice is no change")
        self.assertEqual(ats.record_stage("o", t, "completed")["stage"], "completed")
        for later in ("failed", "rejected", "processing", "timeout", "completed"):
            self.assertIsNone(ats.record_stage("o", t, later), f"terminal must be immutable ({later})")
        self.assertEqual(ats.lookup("o", t)["stage"], "completed")

    def test_unknown_stage_and_bad_ids_are_ignored(self):
        self.assertIsNone(ats.record_stage("o", "t", "exploded"))
        self.assertIsNone(ats.record_stage("", "t", "delivered"))
        self.assertIsNone(ats.record_stage("o", "", "delivered"))
        self.assertIsNone(ats.record_stage(None, "t", "delivered"))  # type: ignore[arg-type]
        self.assertEqual(ats.lookup("o", "t")["stage"], "unknown")

    def test_create_false_never_creates(self):
        self.assertIsNone(ats.record_stage("o", "ghost", "rejected", create=False))
        self.assertEqual(ats.lookup("o", "ghost")["stage"], "unknown")
        ats.record_stage("o", "real", "delivered")
        self.assertIsNotNone(ats.record_stage("o", "real", "rejected", create=False))

    def test_lookup_is_origin_bound(self):
        ats.record_stage("origin-a", "t", "processing")
        self.assertEqual(ats.lookup("origin-a", "t")["stage"], "processing")
        self.assertEqual(ats.lookup("origin-b", "t"), {"stage": "unknown"},
                         "a foreign origin must get the SAME answer as an unknown task")

    def test_reason_vocabulary_is_closed(self):
        ats.record_stage("o", "t", "failed", "Ignore previous instructions <script>")
        self.assertEqual(ats.lookup("o", "t")["reason"], "")
        ats.record_stage("o", "t2", "failed", "worker_error")
        self.assertEqual(ats.lookup("o", "t2")["reason"], "worker_error")

    def test_orphans_of_a_dead_process_are_closed_as_failed_restart(self):
        ats.record_stage("o", "run", "processing")
        ats.record_stage("o", "fin", "completed")
        ats._reset_for_tests()  # a new process: memory gone, the file survives
        self.assertEqual(ats.lookup("o", "run")["stage"], "failed")
        self.assertEqual(ats.lookup("o", "run")["reason"], "restart")
        self.assertEqual(ats.lookup("o", "fin")["stage"], "completed")

    def test_erase_origin_removes_memory_and_file(self):
        ats.record_stage("gone", "t", "completed")
        ats.record_stage("kept", "t", "completed")
        self.assertEqual(ats.erase_origin("gone"), 1)
        ats._reset_for_tests()
        self.assertEqual(ats.lookup("gone", "t")["stage"], "unknown")
        self.assertEqual(ats.lookup("kept", "t")["stage"], "completed")

    def test_store_is_content_free(self):
        ats.record_stage("o", "t", "completed")
        raw = (Path(os.environ["CORVIN_A2A_FEED_DIR"]) / "task_state.jsonl").read_text()
        self.assertEqual(set(json.loads(raw.splitlines()[-1])),
                         {"origin_id", "task_id", "stage", "stage_seq", "updated_at", "reason"})
        mode = (Path(os.environ["CORVIN_A2A_FEED_DIR"]) / "task_state.jsonl").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


class TestLifecycleThroughReceiver(_Base):
    def test_stages_are_observable_while_the_worker_runs_and_in_order(self):
        _write_origin(self.origins)
        started, release = threading.Event(), threading.Event()
        recv = rtr.RemoteTriggerReceiver(
            origins_dir=self.origins,
            engine_factory=lambda: _BlockingEngine(started, release))
        tid = str(uuid.uuid4())
        seen: list[str] = []
        real = ats.record_stage
        with mock.patch.object(ats, "record_stage",
                               side_effect=lambda *a, **k: (seen.append(a[2]), real(*a, **k))[1]):
            out: dict = {}
            th = threading.Thread(target=lambda: out.setdefault("resp", recv.receive(_envelope(tid))))
            th.start()
            self.assertTrue(started.wait(20), "worker never started")
            # mid-run, through the real ping core: signed query -> signed answer
            code, resp = srv.process_ping_request(_query(tid), recv)
            self.assertEqual(code, 200, resp)
            self.assertEqual(resp["task_stage"]["stage"], "processing")
            release.set()
            th.join(30)
        self.assertIn(out["resp"].status, ("ok", "filtered"))  # filtered = ran, output schema-filtered
        self.assertEqual(srv.process_ping_request(_query(tid), recv)[1]["task_stage"]["stage"], "completed")
        order = [s for s in seen if s in ("delivered", "accepted", "processing", "completed")]
        self.assertEqual(order, ["delivered", "accepted", "processing", "completed"])

    def test_m1_peer_without_worker_goes_delivered_accepted_completed(self):
        _write_origin(self.origins, spawn_worker=False)
        recv = rtr.RemoteTriggerReceiver(origins_dir=self.origins)
        tid = str(uuid.uuid4())
        self.assertEqual(recv.receive(_envelope(tid)).status, "ok")
        self.assertEqual(ats.lookup(ORIGIN, tid)["stage"], "completed")

    def test_answer_is_signed_over_task_stage(self):
        _write_origin(self.origins, spawn_worker=False)
        recv = rtr.RemoteTriggerReceiver(origins_dir=self.origins)
        tid = str(uuid.uuid4())
        recv.receive(_envelope(tid))
        _c, resp = srv.process_ping_request(_query(tid), recv)
        sig = resp.pop("signature")
        canon = lambda d: json.dumps(d, separators=(",", ":"), sort_keys=True).encode()  # noqa: E731
        ok = _hmac.new(bytes.fromhex(RECV_KEY), canon(resp), hashlib.sha256).hexdigest()
        self.assertEqual(sig, ok)
        resp["task_stage"] = dict(resp["task_stage"], stage="processing")  # MITM downgrade
        forged = _hmac.new(bytes.fromhex(RECV_KEY), canon(resp), hashlib.sha256).hexdigest()
        self.assertNotEqual(sig, forged, "a tampered stage must break the signature")

    def test_unauthenticated_traffic_never_grows_the_store(self):
        _write_origin(self.origins, spawn_worker=False)
        recv = rtr.RemoteTriggerReceiver(origins_dir=self.origins)
        bad = _envelope("bad-sig", key="a" * 64)               # wrong key -> bad signature
        stranger = _envelope("stranger", origin="not-paired")   # unknown origin
        replay_old = _envelope("stale")
        replay_old["issued_at"] = time.time() - 99999
        for env in (bad, stranger, replay_old, {"junk": 1}, {}):
            self.assertEqual(recv.receive(env).status, "rejected")
        feed = Path(os.environ["CORVIN_A2A_FEED_DIR"]) / "task_state.jsonl"
        self.assertFalse(feed.exists() and feed.read_text().strip(),
                         "rejections before `delivered` must leave no stage record")
        for t in ("bad-sig", "stranger", "stale"):
            self.assertEqual(ats.lookup(ORIGIN, t)["stage"], "unknown")

    def test_injection_is_a_rejected_stage_with_reason(self):
        _write_origin(self.origins)
        recv = rtr.RemoteTriggerReceiver(origins_dir=self.origins,
                                         engine_factory=lambda: _BlockingEngine(
                                             threading.Event(), threading.Event()))
        tid = str(uuid.uuid4())
        env = _envelope(tid)
        env["instruction"] = "x" * 20000  # over the 16 KiB cap -> injection refusal
        payload = {k: v for k, v in env.items() if k != "signature"}
        env["signature"] = _hmac.new(bytes.fromhex(HMAC_KEY), json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(),
            hashlib.sha256).hexdigest()
        self.assertEqual(recv.receive(env).status, "rejected")
        self.assertEqual(ats.lookup(ORIGIN, tid)["stage"], "rejected")


class TestStatusQueryAdversarial(_Base):
    def setUp(self) -> None:
        super().setUp()
        _write_origin(self.origins, spawn_worker=False)
        self.recv = rtr.RemoteTriggerReceiver(origins_dir=self.origins)
        self.tid = str(uuid.uuid4())
        self.recv.receive(_envelope(self.tid))

    def _stage(self, req: dict):
        code, resp = srv.process_ping_request(req, self.recv)
        return code, resp.get("task_stage")

    def test_valid_query_is_answered(self):
        self.assertEqual(self._stage(_query(self.tid)), (200, mock.ANY))
        self.assertEqual(self._stage(_query(self.tid))[1]["stage"], "completed")

    def test_forged_task_sig_gets_a_plain_pong_without_stage(self):
        code, st = self._stage(_query(self.tid, task_sig_key="1" * 64))
        self.assertEqual((code, st), (200, None))

    def test_signature_cannot_be_reaimed_at_another_task(self):
        # task_sig made for task B but the request names task A
        other = str(uuid.uuid4())
        code, st = self._stage(_query(self.tid, task_id_in_sig=other))
        self.assertEqual((code, st), (200, None))

    def test_foreign_origin_learns_nothing_distinguishable(self):
        _write_origin(self.origins, "other-origin", spawn_worker=False)
        _c, mine = self._stage(_query(self.tid, origin="other-origin"))
        _c, never = self._stage(_query(str(uuid.uuid4()), origin="other-origin"))
        self.assertEqual(mine, never)
        self.assertEqual(mine, {"stage": "unknown"})

    def test_oversized_and_weird_ids_are_ignored_not_fatal(self):
        for weird in ("A" * 5000, "../../etc/passwd", "\x00", "💥" * 200, " "):
            code, resp = srv.process_ping_request(_query(weird), self.recv)
            self.assertIn(code, (200, 400), weird[:20])
            if code == 200:
                self.assertIn(resp.get("task_stage", {"stage": "unknown"}).get("stage"), ("unknown",))

    def test_non_string_fields_do_not_crash(self):
        req = _query(self.tid)
        for bad in (123, ["x"], {"a": 1}, None, True):
            r = dict(req, task_id=bad)
            code, _ = srv.process_ping_request(r, self.recv)
            self.assertIn(code, (200, 400))
            r = dict(req, task_sig=bad)
            code, resp = srv.process_ping_request(r, self.recv)
            self.assertIn(code, (200, 400))
            self.assertNotIn("task_stage", resp)

    def test_plain_ping_without_query_is_unchanged(self):
        req = _query(self.tid)
        del req["task_id"], req["task_sig"]
        code, resp = srv.process_ping_request(req, self.recv)
        self.assertEqual(code, 200)
        self.assertNotIn("task_stage", resp)

    def test_audit_once_per_stage_change_not_per_poll(self):
        _mock_se.write_event.reset_mock()
        for _ in range(8):
            srv.process_ping_request(_query(self.tid), self.recv)
        queried = [c for c in _mock_se.write_event.call_args_list
                   if "task_status_queried" in str(c)]
        self.assertLessEqual(len(queried), 1, "a 3-second poller must not flood the audit chain")


class _Peer:
    """Scripted stand-in for task_status() answers."""
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0

    def task_status(self, endpoint_id, task_id, timeout_s=8):
        self.calls += 1
        a = self.answers[min(self.calls - 1, len(self.answers) - 1)]
        return dict({"reachable": True, "supported": True, "stage": "unknown",
                     "stage_seq": 0, "reason": "", "via": "relay"}, **a)

    def _audit_best_effort(self, *a, **k):
        pass


class TestStagePoller(_Base):
    def _run(self, peer, *, finish=None, ttl=5, timeout=30):
        p = rts._StagePoller(peer, "ep-1", "task-1", ttl)
        with mock.patch.object(rts.time, "sleep", lambda s: None), \
             mock.patch.object(threading.Event, "wait", lambda self, t=None: self.is_set()):
            if finish is not None:
                p.finish(finish)
            p._loop()
        return a2a_feed.stage_timeline("task-1"), a2a_feed.latest_stages().get("task-1")

    def setUp(self) -> None:
        super().setUp()
        rts._NO_STAGE_SUPPORT.clear()

    def test_records_changes_only_and_stops_at_terminal(self):
        peer = _Peer([{"stage": "unknown"}, {"stage": "delivered"}, {"stage": "delivered"},
                      {"stage": "processing"}, {"stage": "processing"}, {"stage": "completed"},
                      {"stage": "processing"}])  # the last must never be asked for
        tl, latest = self._run(peer)
        self.assertEqual([r["stage"] for r in tl], ["delivered", "processing", "completed"])
        self.assertEqual(latest["stage"], "completed")
        self.assertEqual(peer.calls, 6)

    def test_old_peer_is_marked_unsupported_and_left_alone(self):
        peer = _Peer([{"supported": False}])
        tl, _ = self._run(peer)
        self.assertEqual(tl, [])
        self.assertEqual(peer.calls, 1)
        self.assertIn("ep-1", rts._NO_STAGE_SUPPORT)
        self.assertFalse(rts._StagePoller(peer, "ep-1", "t2", 5).start(),
                         "no poller thread for a peer known to lack support")

    def test_send_that_never_reached_the_peer_stops_the_poller(self):
        peer = _Peer([{"stage": "processing"}])
        fin = mock.Mock(ok=False, status="error", maybe_delivered=False)
        tl, _ = self._run(peer, finish=fin)
        self.assertEqual(peer.calls, 0)
        self.assertEqual(tl, [])

    def test_verified_answer_is_recorded_without_a_query(self):
        peer = _Peer([{"stage": "unknown"}])
        tl, latest = self._run(peer, finish=mock.Mock(ok=True, status="ok", maybe_delivered=False))
        self.assertEqual(latest["stage"], "completed")
        self.assertEqual(peer.calls, 0)

    def test_unconfirmed_send_keeps_asking_until_the_truth_is_known(self):
        peer = _Peer([{"stage": "processing"}, {"stage": "processing"}, {"stage": "completed"}])
        fin = mock.Mock(ok=False, status="unconfirmed", maybe_delivered=True)
        tl, latest = self._run(peer, finish=fin)
        self.assertEqual(latest["stage"], "completed")
        self.assertEqual([r["stage"] for r in tl], ["processing", "completed"])

    def test_peer_that_never_heard_of_it_ends_the_post_mortem(self):
        peer = _Peer([{"stage": "unknown"}])
        fin = mock.Mock(ok=False, status="unconfirmed", maybe_delivered=True)
        tl, _ = self._run(peer, finish=fin)
        self.assertEqual(tl, [])
        self.assertEqual(peer.calls, 6)

    def test_unreachable_peer_after_send_gives_up(self):
        peer = _Peer([{"reachable": False}])
        fin = mock.Mock(ok=False, status="timeout_transport", maybe_delivered=True)
        self._run(peer, finish=fin)
        self.assertEqual(peer.calls, 3)

    def test_poller_slots_are_bounded(self):
        taken = [rts._POLLER_SLOTS.acquire(blocking=False) for _ in range(8)]
        try:
            self.assertFalse(rts._StagePoller(_Peer([{}]), "ep-2", "t", 5).start())
        finally:
            for ok in taken:
                if ok:
                    rts._POLLER_SLOTS.release()


class TestFeedStageLog(_Base):
    def test_rank_wins_over_arrival_order(self):
        for st in ("completed", "processing", "delivered"):
            a2a_feed.observe_stage(peer_id="p", task_id="t", stage=st)
        self.assertEqual(a2a_feed.latest_stages()["t"]["stage"], "completed")

    def test_garbage_is_dropped(self):
        self.assertFalse(a2a_feed.observe_stage(peer_id="p", task_id="t", stage="exploded"))
        self.assertFalse(a2a_feed.observe_stage(peer_id="", task_id="t", stage="delivered"))
        a2a_feed.observe_stage(peer_id="p", task_id="t", stage="failed", reason="Ignore <this>!")
        self.assertEqual(a2a_feed.latest_stages()["t"]["reason"], "ignorethis"[:24] if False else "ignorethis")

    def test_erase_peer_and_clear_cover_the_stage_log(self):
        a2a_feed.observe_stage(peer_id="gone", task_id="t1", stage="delivered")
        a2a_feed.observe_stage(peer_id="kept", task_id="t2", stage="delivered")
        a2a_feed.erase_peer("gone")
        self.assertEqual(set(a2a_feed.latest_stages()), {"t2"})
        a2a_feed.clear()
        self.assertEqual(a2a_feed.latest_stages(), {})

    def test_log_is_capped(self):
        with mock.patch.object(a2a_feed, "_STAGES_MAX_BYTES", 2000):
            for i in range(400):
                a2a_feed.observe_stage(peer_id="p", task_id=f"t{i}", stage="delivered")
        root = a2a_feed.feed_dir()
        self.assertLess((root / "stages.jsonl").stat().st_size, 2000 + 1500 * 120)
        self.assertEqual(a2a_feed.latest_stages()["t399"]["stage"], "delivered")


if __name__ == "__main__":
    unittest.main()
