"""Adversarial tests for the live A2A probe and the continuous watch.

A monitor that crashes, hangs, leaks a key or leaves a half-written file behind on exactly the day the host
misbehaves is worse than none. So the host here is a FAKE that lies, hangs, answers garbage or floods; and the
pure analysis gets hostile inputs. Call-site level: the real CLI (``python a2a_live_watch.py``) is run as a
subprocess against the fake, and the systemd unit's ExecStart is checked to point at a script that exists.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import a2a_live_probe as probe  # noqa: E402
import a2a_live_watch as watch  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
NOW = 1_791_600_000.0  # 2026-10-10 ~00:00 UTC area; only relative arithmetic matters


def _sig(key: str, d: dict) -> str:
    return hmac.new(bytes.fromhex(key), json.dumps(d, separators=(",", ":"), sort_keys=True).encode(),
                    hashlib.sha256).hexdigest()


class FakeHost:
    """A stand-in A2A host. ``mode`` decides how it misbehaves."""

    def __init__(self, origins_dir: Path, mode: str = "good", capacity: str = "available", feed: dict | None = None):
        self.origins, self.mode, self.capacity = origins_dir, mode, capacity
        self.feed = feed if feed is not None else {
            "peers": [{"peer_id": "p1", "label": "Peer One", "presence": "online", "task_capacity": None,
                       "last_check_at": time.time(), "last_ok_at": time.time()},
                      {"peer_id": "gone", "label": "Old", "presence": "removed", "task_capacity": None}],
            "messages": [], "stages": {}}
        host = self
        self.hits = 0

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: D401
                pass

            def _send(self, code, body, ctype="application/json"):
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except OSError:
                    pass

            def _misbehave(self) -> bool:
                host.hits += 1
                if host.mode == "html":
                    self._send(200, b"<html>captive portal</html>", "text/html")
                elif host.mode == "err500":
                    self._send(500, {"detail": "boom"})
                elif host.mode == "slow":
                    time.sleep(3)
                    self._send(200, {})
                elif host.mode == "huge":
                    self._send(200, b"x" * (probe.MAX_BODY + 10))
                else:
                    return False
                return True

            def do_GET(self):  # noqa: N802
                if self._misbehave():
                    return
                if self.path.startswith("/v1/console/auth/local-login"):
                    self._send(200, {"ok": True})
                elif self.path.startswith("/v1/console/a2a/feed"):
                    feed = dict(host.feed)
                    if host.mode == "stale":
                        feed.pop("stages", None)
                        feed["peers"] = [{k: v for k, v in p.items() if k != "task_capacity"} for p in feed["peers"]]
                    self._send(200, feed)
                else:
                    self._send(404, {})

            def do_POST(self):  # noqa: N802
                if self._misbehave():
                    return
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    req = json.loads(self.rfile.read(n))
                except ValueError:
                    self._send(400, {"reason": "invalid_json"})
                    return
                try:
                    cfg = json.loads((host.origins / f"{req['origin_id']}.json").read_text())
                except (OSError, KeyError, ValueError):
                    self._send(403, {"reason": "ping_rejected"})
                    return
                base = {k: req[k] for k in ("ping_id", "issued_at", "origin_id")}
                if not hmac.compare_digest(req.get("signature", ""), _sig(cfg["hmac_key"], base)):
                    if host.mode == "accepts_forged":
                        pass
                    else:
                        self._send(403, {"reason": "ping_rejected"})
                        return
                pong = {"ok": True, "task_id": req["ping_id"], "instance_id": "fake-instance",
                        "protocol_version": "1", "server_time": int(time.time())}
                if host.mode != "stale":
                    pong["task_capacity"] = host.capacity
                tid, tsig = req.get("task_id"), req.get("task_sig")
                if isinstance(tid, str) and isinstance(tsig, str):
                    ok = hmac.compare_digest(tsig, _sig(cfg["hmac_key"], dict(base, task_id=tid)))
                    if ok or host.mode == "honours_reaimed":
                        pong["task_stage"] = {"stage": "unknown"}
                key = cfg["recv_key"] if host.mode != "badsig" else "ab" * 32
                pong["signature"] = _sig(key, pong)
                self._send(200, pong)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class _Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.origins = self.tmp / "origins"
        self.origins.mkdir()
        self.hosts: list[FakeHost] = []

    def host(self, **kw) -> FakeHost:
        h = FakeHost(self.origins, **kw)
        self.hosts.append(h)
        self.addCleanup(h.close)
        return h

    def probe(self, h: FakeHost, timeout=3.0):
        return probe.probe_host(h.url, origins_dir=self.origins, timeout_s=timeout)

    def leftovers(self):
        return [p.name for p in self.origins.glob("live-probe-*")]


def _by_name(checks):
    return {c.name: c for c in checks}


class TestProbeAgainstHonestAndBrokenHosts(_Base):
    def test_a_healthy_host_passes_every_check(self):
        checks, feed = self.probe(self.host())
        self.assertEqual([c.name for c in checks if not c.ok], [], [c.detail for c in checks])
        self.assertEqual(len(checks), 6)
        self.assertIsInstance(feed, dict)
        self.assertEqual(self.leftovers(), [])

    def test_a_host_on_old_code_fails_exactly_the_capacity_and_feed_checks(self):
        checks, _ = self.probe(self.host(mode="stale"))
        failed = {c.name for c in checks if not c.ok}
        self.assertEqual(failed, {"pong_capacity", "feed_shape"})
        self.assertIn("restart", _by_name(checks)["pong_capacity"].detail)

    def test_every_hostile_transport_is_a_failed_check_never_an_exception(self):
        for mode, timeout in (("html", 3), ("err500", 3), ("huge", 5), ("slow", 0.4), ("badsig", 3)):
            with self.subTest(mode=mode):
                t = time.time()
                checks, feed = self.probe(self.host(mode=mode), timeout=timeout)
                self.assertEqual(len(checks), 6)
                # a lying signature poisons every check that relies on the pong; the forged-ping refusal
                # and the console feed are separate paths and legitimately still pass
                expect_ok = {"forged_refused", "feed_shape"} if mode == "badsig" else set()
                self.assertEqual({c.name for c in checks if c.ok}, expect_ok, [(c.name, c.ok) for c in checks])
                self.assertLess(time.time() - t, 12, f"{mode}: the probe must be bounded")
                self.assertEqual(self.leftovers(), [], f"{mode}: the throwaway origin must be gone")

    def test_a_host_that_is_down_fails_everything_quickly_and_cleanly(self):
        h = self.host()
        url = h.url
        h.close()
        t = time.time()
        checks, feed = probe.probe_host(url, origins_dir=self.origins, timeout_s=2)
        self.assertTrue(all(not c.ok for c in checks))
        self.assertIsNone(feed)
        self.assertLess(time.time() - t, 8)
        self.assertEqual(self.leftovers(), [])

    def test_a_pong_signed_with_the_wrong_key_is_not_believed(self):
        checks, _ = self.probe(self.host(mode="badsig"))
        d = _by_name(checks)
        self.assertFalse(d["ordinary_ping"].ok)
        self.assertIn("does not verify", d["ordinary_ping"].detail)

    def test_a_host_that_accepts_a_forged_ping_is_caught(self):
        d = _by_name(self.probe(self.host(mode="accepts_forged"))[0])
        self.assertFalse(d["forged_refused"].ok)

    def test_a_host_that_honours_a_reaimed_signature_is_caught(self):
        d = _by_name(self.probe(self.host(mode="honours_reaimed"))[0])
        self.assertFalse(d["reaimed_ignored"].ok)

    def test_an_unknown_capacity_value_in_the_feed_is_a_failure(self):
        feed = {"peers": [{"peer_id": "p", "task_capacity": "<script>"}], "messages": [], "stages": {}}
        d = _by_name(self.probe(self.host(feed=feed))[0])
        self.assertFalse(d["feed_shape"].ok)

    def test_a_peer_row_without_the_key_is_a_failure_even_for_a_removed_peer(self):
        feed = {"peers": [{"peer_id": "gone", "presence": "removed"}], "messages": [], "stages": {}}
        d = _by_name(self.probe(self.host(feed=feed))[0])
        self.assertFalse(d["feed_shape"].ok)
        self.assertIn("task_capacity", d["feed_shape"].detail)


class TestProbeSafety(_Base):
    def test_no_key_material_ever_reaches_a_result(self):
        seen: list[str] = []
        real = probe.secrets.token_hex

        def spy(n):
            v = real(n)
            seen.append(v)
            return v
        with mock.patch.object(probe.secrets, "token_hex", spy):
            checks, _ = self.probe(self.host())
            checks2, _ = self.probe(self.host(mode="badsig"))
        blob = json.dumps([c.as_dict() for c in checks + checks2])
        self.assertTrue(seen)
        for k in seen:
            self.assertNotIn(k, blob)

    def test_the_throwaway_origin_is_private_and_removed_even_when_the_probe_body_raises(self):
        with self.assertRaises(RuntimeError):
            with probe.throwaway_origin(self.origins) as o:
                f = self.origins / f"{o['origin_id']}.json"
                self.assertEqual(f.stat().st_mode & 0o777, 0o600)
                raise RuntimeError("boom")
        self.assertEqual(self.leftovers(), [])

    def test_an_unwritable_origins_dir_is_reported_and_the_rest_still_runs(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores directory modes")
        ro = self.tmp / "ro"
        ro.mkdir()
        ro.chmod(0o500)
        self.addCleanup(lambda: ro.chmod(0o700))
        checks, feed = probe.probe_host(self.host().url, origins_dir=ro, timeout_s=2)
        d = _by_name(checks)
        self.assertFalse(d["probe_origin"].ok)
        self.assertIn("NOT probed", d["probe_origin"].detail)
        self.assertTrue(d["feed_shape"].ok)

    def test_an_origins_path_that_is_a_file_is_reported_not_raised(self):
        f = self.tmp / "not-a-dir"
        f.write_text("x")
        checks, _ = probe.probe_host(self.host().url, origins_dir=f, timeout_s=2)
        self.assertFalse(_by_name(checks)["probe_origin"].ok)

    def test_a_symlinked_origin_target_is_never_followed(self):
        target = self.tmp / "victim"
        target.write_text("precious")
        # an attacker pre-creates the path the probe would use (random 48-bit id — here forced)
        with mock.patch.object(probe.uuid, "uuid4", return_value=mock.Mock(hex="deadbeefcafe0123456789")):
            (self.origins / "live-probe-deadbeefcafe.json").symlink_to(target)
            with self.assertRaises(OSError):
                with probe.throwaway_origin(self.origins):
                    pass
        self.assertEqual(target.read_text(), "precious")


class TestAnalysePeers(unittest.TestCase):
    D0 = watch.utc_midnight(NOW)

    def feed(self, peers, msgs=()):
        return {"peers": peers, "messages": list(msgs)}

    def peer(self, **kw):
        return {"peer_id": "p1", "label": "Peer", "presence": "online", "last_check_at": NOW - 10, **kw}

    def resp(self, status, ts, error=None):
        return {"peer_id": "p1", "direction": "in", "kind": "response", "status": status, "ts": ts, "error": error}

    def names(self, feed):
        return [c.name for c in watch.analyse_peers(feed, NOW)]

    def test_pool_pressure_counts_todays_utc_runs_only_and_warns_at_eight(self):
        runs = lambda n, ts: [self.resp("ok", ts + i) for i in range(n)]  # noqa: E731
        self.assertNotIn("peer_pool_pressure", self.names(self.feed([self.peer()], runs(7, self.D0 + 60))))
        self.assertIn("peer_pool_pressure", self.names(self.feed([self.peer()], runs(8, self.D0 + 60))))
        yesterday = runs(20, self.D0 - 3600)
        self.assertNotIn("peer_pool_pressure", self.names(self.feed([self.peer()], yesterday)))

    def test_a_timeout_spent_a_unit_a_refusal_did_not(self):
        msgs = [self.resp("timeout", self.D0 + 10 + i) for i in range(8)]
        self.assertIn("peer_pool_pressure", self.names(self.feed([self.peer()], msgs)))
        msgs = [self.resp("rejected", self.D0 + 10 + i) for i in range(8)]
        self.assertNotIn("peer_pool_pressure", self.names(self.feed([self.peer()], msgs)))

    def test_a_refusal_streak_needs_three_in_a_row_and_names_the_reason(self):
        two = [self.resp("ok", NOW - 50), self.resp("rejected", NOW - 40), self.resp("rejected", NOW - 30)]
        self.assertNotIn("peer_refusing", self.names(self.feed([self.peer()], two)))
        three = two + [self.resp("rejected", NOW - 20, "daily compute limit is used up")]
        found = [c for c in watch.analyse_peers(self.feed([self.peer()], three), NOW) if c.name == "peer_refusing"]
        self.assertEqual(len(found), 1)
        self.assertIn("daily compute limit", found[0].detail)
        broken = three + [self.resp("ok", NOW - 10)]
        self.assertNotIn("peer_refusing", self.names(self.feed([self.peer()], broken)))

    def test_offline_stale_probe_and_limit_reached(self):
        n = self.names(self.feed([self.peer(presence="offline", last_check_at=NOW - 900, task_capacity="limit_reached")]))
        self.assertEqual(set(n), {"peer_offline", "peer_probe_stale", "peer_limit_reached"})

    def test_removed_and_disabled_peers_are_not_nagged_about(self):
        for pres in ("removed", "disabled"):
            self.assertEqual(self.names(self.feed([self.peer(presence=pres, last_check_at=NOW - 9999,
                                                              task_capacity="limit_reached")])), [])

    def test_every_finding_is_a_warning_never_critical(self):
        msgs = [self.resp("ok", self.D0 + i) for i in range(9)] + [self.resp("rejected", NOW - i) for i in range(4)]
        cs = watch.analyse_peers(self.feed([self.peer(presence="offline", task_capacity="limit_reached")], msgs), NOW)
        self.assertTrue(cs and all(c.severity == "warn" and not c.ok for c in cs))

    def test_hostile_and_malformed_feeds_never_raise(self):
        junk = [None, [], "x", 7, {}, {"peers": None}, {"peers": [None, 1, "x", {}, {"peer_id": ""}]},
                {"peers": [{"peer_id": "p", "label": "L" * 10_000, "last_check_at": True, "presence": "online"}],
                 "messages": [None, 3, {"peer_id": "p", "direction": "in", "kind": "response", "status": {"x": 1}, "ts": "NaN"},
                              {"peer_id": "p", "ts": float("inf"), "status": "ok", "direction": "in", "kind": "response"}]},
                {"peers": [{"peer_id": "p", "last_check_at": float("nan"), "presence": "online"}], "messages": "no"}]
        for j in junk:
            with self.subTest(feed=str(j)[:40]):
                out = watch.analyse_peers(j, NOW)
                self.assertIsInstance(out, list)
                for c in out:
                    self.assertLess(len(c.detail), 400 + 1)

    def test_a_bool_is_not_a_timestamp(self):
        p = self.peer(last_check_at=True)
        self.assertNotIn("peer_probe_stale", self.names(self.feed([p])))


class TestFreshnessAndVerdict(unittest.TestCase):
    def test_a_host_older_than_the_source_is_critical(self):
        c = watch.source_is_newer(1000.0, 5000.0, "a2a_worker.py")
        self.assertFalse(c.ok)
        self.assertEqual(c.severity, "critical")
        self.assertIn("a2a_worker.py", c.detail)
        self.assertIn("restart", c.detail)

    def test_slack_and_equality_are_fresh(self):
        self.assertTrue(watch.source_is_newer(1000.0, 1000.0).ok)
        self.assertTrue(watch.source_is_newer(1000.0, 1004.0).ok)
        self.assertFalse(watch.source_is_newer(1000.0, 1006.0).ok)

    def test_unknown_start_is_a_visible_skip_not_a_pass_by_silence(self):
        c = watch.source_is_newer(None, 5000.0)
        self.assertTrue(c.ok)
        self.assertIn("skipped", c.detail)

    def test_the_watch_and_tests_do_not_count_as_host_code(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            d = repo / "corvin_operator" / "bridges" / "shared"
            d.mkdir(parents=True)
            old = d / "a2a_worker.py"
            old.write_text("x")
            os.utime(old, (1000, 1000))
            for name in ("a2a_live_watch.py", "a2a_live_probe.py", "test_a2a_anything.py"):
                (d / name).write_text("x")
            ts, name = watch.newest_source(repo)
            self.assertEqual((ts, name), (1000.0, "a2a_worker.py"))

    def test_verdict_levels(self):
        ok, warn, crit = (watch.Check("a", True), watch.Check("b", False, severity="warn"), watch.Check("c", False))
        self.assertEqual(watch.verdict([ok]), "healthy")
        self.assertEqual(watch.verdict([ok, warn]), "degraded")
        self.assertEqual(watch.verdict([ok, warn, crit]), "broken")
        self.assertEqual(watch.verdict([]), "healthy")


class TestPersistenceAndRuns(_Base):
    def report(self, verdict="healthy", n=0):
        return {"ts": NOW + n, "at": "t", "url": "u", "verdict": verdict, "checks": [], "peers": []}

    def test_status_file_is_valid_json_and_the_log_appends(self):
        home = self.tmp / "home"
        watch.persist(self.report(), home)
        watch.persist(self.report("broken", 1), home)
        self.assertEqual(json.loads((home / "logs" / "a2a_watch.status.json").read_text())["verdict"], "broken")
        lines = (home / "logs" / "a2a_watch.jsonl").read_text().splitlines()
        self.assertEqual([json.loads(x)["verdict"] for x in lines], ["healthy", "broken"])
        self.assertFalse(list((home / "logs").glob("*.tmp")))

    def test_the_log_is_capped_and_keeps_the_newest_lines(self):
        home = self.tmp / "home"
        with mock.patch.object(watch, "LOG_MAX_BYTES", 500), mock.patch.object(watch, "LOG_KEEP_LINES", 5):
            for i in range(60):
                watch.persist(self.report(n=i), home)
        lines = (home / "logs" / "a2a_watch.jsonl").read_text().splitlines()
        self.assertLessEqual(len(lines), 30)
        self.assertEqual(json.loads(lines[-1])["ts"], NOW + 59)

    def test_a_second_concurrent_run_is_skipped_not_run(self):
        home = self.tmp / "home"
        with watch._single_instance(home) as first:
            self.assertTrue(first)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = watch.main(["--url", "http://127.0.0.1:1", "--home", str(home), "--unit", ""])
            self.assertEqual(rc, 0)
            self.assertIn("skipped", buf.getvalue())
        with watch._single_instance(home) as again:
            self.assertTrue(again, "the lock must be released")

    def _main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = watch.main(list(args))
        return rc, out.getvalue(), err.getvalue()

    def test_exit_codes_healthy_broken_and_cannot_run(self):
        home = self.tmp / "home"
        h = self.host()
        rc, out, _ = self._main("--url", h.url, "--home", str(home), "--unit", "", "--origins-dir", str(self.origins))
        self.assertEqual(rc, 0, out)
        self.assertIn("HEALTHY", out)
        stale = self.host(mode="stale")
        rc, out, _ = self._main("--url", stale.url, "--home", str(home), "--unit", "", "--origins-dir", str(self.origins))
        self.assertEqual(rc, 1)
        self.assertIn("BROKEN", out)
        with mock.patch.object(watch, "run_once", side_effect=RuntimeError("bug")):
            rc, _, err = self._main("--url", h.url, "--home", str(home), "--unit", "")
        self.assertEqual(rc, 2)
        self.assertIn("could not run", err)

    def test_quiet_prints_nothing_when_healthy(self):
        rc, out, _ = self._main("--url", self.host().url, "--home", str(self.tmp / "h"), "--unit", "",
                                "--origins-dir", str(self.origins), "--quiet")
        self.assertEqual((rc, out), (0, ""))

    def test_an_unwritable_home_does_not_change_the_verdict(self):
        blocker = self.tmp / "afile"
        blocker.write_text("x")
        rc, out, _ = self._main("--url", self.host().url, "--home", str(blocker / "sub"), "--unit", "",
                                "--origins-dir", str(self.origins))
        self.assertEqual(rc, 0, out)

    def test_a_host_that_is_down_is_broken_in_bounded_time(self):
        h = self.host()
        url = h.url
        h.close()
        t = time.time()
        rc, out, _ = self._main("--url", url, "--home", str(self.tmp / "h"), "--unit", "",
                                "--origins-dir", str(self.origins), "--timeout", "2")
        self.assertEqual(rc, 1)
        self.assertLess(time.time() - t, 15)
        self.assertEqual(self.leftovers(), [])


class TestCallSites(_Base):
    """The proof that the mechanism is WIRED, not merely present."""

    def test_the_real_cli_runs_as_a_subprocess_and_writes_its_status(self):
        home = self.tmp / "home"
        h = self.host()
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.tmp), "CORVIN_HOME": str(home),
               "REMOTE_ORIGINS_DIR": str(self.origins)}
        r = subprocess.run([sys.executable, str(Path(watch.__file__)), "--url", h.url, "--unit", ""],
                           env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("HEALTHY", r.stdout)
        status = json.loads((home / "logs" / "a2a_watch.status.json").read_text())
        self.assertEqual(status["verdict"], "healthy")
        self.assertEqual(len(status["checks"]), 7)

    def test_the_systemd_units_point_at_a_script_that_exists(self):
        svc = (REPO / "ops" / "systemd" / "corvin-a2a-watch.service").read_text()
        tmr = (REPO / "ops" / "systemd" / "corvin-a2a-watch.timer").read_text()
        self.assertIn("a2a_live_watch.py", svc)
        self.assertTrue((REPO / "corvin_operator" / "bridges" / "shared" / "a2a_live_watch.py").is_file())
        self.assertIn("Unit=corvin-a2a-watch.service", tmr)
        self.assertIn("OnBootSec", tmr)
        self.assertIn("OnUnitActiveSec", tmr)

    def test_the_e2e_test_and_the_watch_share_one_probe(self):
        e2e = (REPO / "tests" / "e2e" / "a2a" / "test_live_host_a2a_contract_e2e.py").read_text()
        self.assertIn("a2a_live_probe", e2e, "the live E2E must run the same probe the watch runs")
        self.assertIn("probe_host", Path(watch.__file__).read_text())


if __name__ == "__main__":
    unittest.main()
