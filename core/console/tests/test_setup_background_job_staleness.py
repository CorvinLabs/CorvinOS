"""Characterization tests for a CONFIRMED test blind spot: both background
job states in routes/setup.py — `_WA_START_STATE` (WhatsApp bridge start,
POST /setup/whatsapp/start) and `_WELCOME_CHECK_STATE` (welcome-check,
POST /setup/welcome-check) — are flipped to {"state": "running"} right
before a daemon thread starts, and NOTHING in the module ever marks either
state stale: no `started_at` timestamp is recorded, no timeout/watchdog
exists, and a second POST while the job is "running" is a pure no-op that
just echoes the same stuck dict back.

This is reachable in production, not theoretical: `bm.start_channel_detached`
(-> `_materialise_channel`) shells out to `subprocess.run(_npm_install_cmd(...))`
with NO `timeout=` argument (operator/bridges/bridge_manager.py), and the
welcome-check job's STT/TTS round-trip calls into voice_doctor with no
timeout on the TTS leg either. A stalled npm registry / hung TTS provider
call genuinely blocks the daemon thread forever, and every subsequent poll
(and even a fresh retry POST) returns the exact same stuck "running" state
indefinitely, with no client-visible way to recover short of a server
restart.

These tests do not (yet) assert a fix — no staleness field exists in the
code to assert on. They pin TODAY's undesirable behavior (a background job
that hangs forever keeps the shared state stuck at "running" forever, with
no started_at/staleness marker anywhere and no self-healing on retry) so
that whoever adds the staleness mechanism has a red test to turn green,
and so a future accidental "fix" that quietly makes the retry a silent
no-op forever cannot ship unnoticed.

Harness follows the FastAPI TestClient + isolated-CORVIN_HOME + auth-bypass
pattern established by test_setup_welcome_check.py / test_engine_detect_routes_adr0125.py.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))


def _reset_modules():
    for key in list(sys.modules):
        if key == "corvin_console" or key.startswith("corvin_console."):
            del sys.modules[key]


@contextmanager
def _sandbox(tmp_path: Path):
    """FastAPI TestClient with an isolated CORVIN_HOME and bypassed auth."""
    home = tmp_path / "corvin_home"
    tenant_id = "_default"
    (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)

    prev_home = os.environ.get("CORVIN_HOME")
    prev_tid = os.environ.get("CORVIN_TENANT_ID")
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_id

    try:
        _reset_modules()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from corvin_console.app import router as console_router
        from corvin_console.deps import require_csrf, require_session

        app = FastAPI()
        app.include_router(console_router, prefix="/v1/console")

        mock_session = MagicMock()
        mock_session.username = "test_admin"
        mock_session.tenant_id = "_default"
        mock_session.role = "admin"
        mock_session.sid_fingerprint = "test_fp_0123456789ab"

        app.dependency_overrides[require_session] = lambda: mock_session
        app.dependency_overrides[require_csrf] = lambda: mock_session

        with TestClient(app, raise_server_exceptions=False) as tc:
            yield tc
    finally:
        for k, v in [("CORVIN_HOME", prev_home), ("CORVIN_TENANT_ID", prev_tid)]:
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


def _install_fake_house_rules(*, warn: str | None = None) -> None:
    mod = types.ModuleType("house_rules")

    def _boot_check(log_fn=None):
        if warn and callable(log_fn):
            log_fn(warn)

    mod.house_rules_boot_health_check = _boot_check
    sys.modules["house_rules"] = mod


def _install_fake_voice_doctor(*, stt_ok: bool = True, tts_ok: bool = True) -> None:
    mod = types.ModuleType("voice_doctor")
    mod._DOCTOR_TTS_TEXT = "test"
    mod._check_stt = lambda timeout_s: (stt_ok, "ok" if stt_ok else "stt broken")
    mod._check_tts = lambda text: (tts_ok, "ok" if tts_ok else "tts broken", None)
    sys.modules["voice_doctor"] = mod


class TestWhatsappStartJobHang(unittest.TestCase):
    """Blind spot: `_run_wa_start_job` -> `bm.start_channel_detached` can
    block forever (e.g. a hung npm install, since bridge_manager.py's
    subprocess.run() call has no timeout=). Nothing in setup.py detects or
    times out a stuck job."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._tmp_path = Path(self._tmp)
        # The never-set event is what makes the fake worker hang "forever"
        # for the duration of this test; we set it in tearDown so the
        # daemon thread can exit cleanly and not leak past the test.
        self._release = threading.Event()

    def tearDown(self):
        self._release.set()
        # Give the daemon thread a moment to unblock and finish so it
        # doesn't keep touching module-level state after the test module
        # is torn down.
        time.sleep(0.05)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _fake_bridge_manager_that_hangs(self):
        bm = types.ModuleType("bridge_manager")

        def _start_channel_detached(channel, progress=None):
            if callable(progress):
                progress("Installing dependencies…")
            # Simulate an npm-install-style hang: this call never returns
            # until the test explicitly releases it (mirrors the real,
            # timeout-less subprocess.run() in bridge_manager.py).
            self._release.wait()
            return {"ok": True}

        bm.start_channel_detached = _start_channel_detached
        return bm

    def test_post_returns_promptly_even_though_worker_hangs(self):
        """The POST endpoint itself must not block on the worker — this
        part already works and must keep working."""
        with _sandbox(self._tmp_path) as tc:
            with patch(
                "corvin_console.routes.setup._import_bridge_manager",
                return_value=self._fake_bridge_manager_that_hangs(),
            ):
                t0 = time.monotonic()
                resp = tc.post("/v1/console/setup/whatsapp/start")
                elapsed = time.monotonic() - t0

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["state"], "running")
        self.assertLess(elapsed, 2.0, "POST must return immediately, not wait for the worker")

    def test_status_stays_running_forever_with_no_staleness_marker(self):
        """Pins today's gap: once the worker hangs, GET status keeps
        returning state=running indefinitely, and there is no started_at
        (or any other staleness) field anywhere in the payload to let a
        client detect the job is stuck rather than merely slow."""
        with _sandbox(self._tmp_path) as tc:
            with patch(
                "corvin_console.routes.setup._import_bridge_manager",
                return_value=self._fake_bridge_manager_that_hangs(),
            ):
                start = tc.post("/v1/console/setup/whatsapp/start")
                self.assertEqual(start.json()["state"], "running")

                # Poll repeatedly over a short window — a real hang (npm
                # stalled on a dead registry connection) can last minutes;
                # here we only need to show the state never self-heals.
                for _ in range(5):
                    time.sleep(0.05)
                    status = tc.get("/v1/console/setup/whatsapp/start/status").json()
                    self.assertEqual(
                        status["state"], "running",
                        "job never actually finishes in this test, so it must "
                        "stay 'running' -- if this ever flips to done/error "
                        "on its own it means a timeout/watchdog now exists "
                        "and this characterization test should be updated",
                    )
                    self.assertNotIn(
                        "started_at", status,
                        "no staleness timestamp exists yet -- this assertion "
                        "documents the gap; it should be replaced with a "
                        "real staleness assertion once one is added",
                    )

    def test_retry_post_while_stuck_is_a_pure_noop_returns_same_stuck_state(self):
        """The only documented 'recovery' path (POST again) is actually not
        a recovery at all while state=='running' -- it is a no-op that
        returns the exact same in-flight dict, so a user who retries after
        a timeout on the frontend gets nothing new."""
        with _sandbox(self._tmp_path) as tc:
            with patch(
                "corvin_console.routes.setup._import_bridge_manager",
                return_value=self._fake_bridge_manager_that_hangs(),
            ):
                first = tc.post("/v1/console/setup/whatsapp/start").json()
                retry = tc.post("/v1/console/setup/whatsapp/start").json()

        self.assertEqual(first["state"], "running")
        self.assertEqual(retry["state"], "running")
        self.assertEqual(
            first, retry,
            "a retry POST while the job is stuck must currently be "
            "indistinguishable from the original response -- there is no "
            "way for the client to force a fresh attempt",
        )


class TestWelcomeCheckJobHang(unittest.TestCase):
    """Same blind spot for the welcome-check job: `_run_welcome_check_job`
    calls `.result()` on 4 ThreadPoolExecutor futures with no timeout, so a
    single hanging probe (e.g. voice_doctor's TTS call, which itself has no
    timeout) blocks the whole job, and `_WELCOME_CHECK_STATE` for that
    tenant stays 'running' forever with no started_at/staleness field."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._tmp_path = Path(self._tmp)
        self._orig_house_rules = sys.modules.get("house_rules")
        self._orig_voice_doctor = sys.modules.get("voice_doctor")
        self._release = threading.Event()

    def tearDown(self):
        self._release.set()
        time.sleep(0.05)
        shutil.rmtree(self._tmp, ignore_errors=True)
        for name, orig in (
            ("house_rules", self._orig_house_rules),
            ("voice_doctor", self._orig_voice_doctor),
        ):
            if orig is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = orig

    def _hanging_stt_tts(self):
        def _fn():
            # Mirrors voice_doctor._check_tts calling a provider with no
            # timeout at all -- this future's .result() never returns
            # until released.
            self._release.wait()
            return ({"status": "ok", "detail": ""}, {"status": "ok", "detail": ""})
        return _fn

    def test_status_stays_running_forever_with_no_staleness_marker(self):
        _install_fake_house_rules(warn=None)
        _install_fake_voice_doctor(stt_ok=True, tts_ok=True)

        with _sandbox(self._tmp_path) as tc:
            with (
                patch("corvin_console.routes.setup._default_engine", return_value="claude_code"),
                patch(
                    "corvin_console.routes.setup.test_engine",
                    return_value={"ok": True, "detail": ""},
                ),
                patch(
                    "corvin_console.routes.setup._welcome_check_stt_tts",
                    side_effect=self._hanging_stt_tts(),
                ),
            ):
                start = tc.post("/v1/console/setup/welcome-check")
                self.assertEqual(start.json()["state"], "running")

                for _ in range(5):
                    time.sleep(0.05)
                    status = tc.get("/v1/console/setup/welcome-check/status").json()
                    self.assertEqual(
                        status["state"], "running",
                        "job never finishes here, so it must stay 'running' "
                        "-- if this flips on its own a timeout/watchdog now "
                        "exists and this test should be updated",
                    )
                    self.assertNotIn(
                        "started_at", status,
                        "no staleness timestamp exists yet -- documents the gap",
                    )

    def test_retry_post_while_stuck_is_a_pure_noop_returns_same_stuck_state(self):
        _install_fake_house_rules(warn=None)
        _install_fake_voice_doctor(stt_ok=True, tts_ok=True)

        with _sandbox(self._tmp_path) as tc:
            with (
                patch("corvin_console.routes.setup._default_engine", return_value="claude_code"),
                patch(
                    "corvin_console.routes.setup.test_engine",
                    return_value={"ok": True, "detail": ""},
                ),
                patch(
                    "corvin_console.routes.setup._welcome_check_stt_tts",
                    side_effect=self._hanging_stt_tts(),
                ),
            ):
                first = tc.post("/v1/console/setup/welcome-check").json()
                retry = tc.post("/v1/console/setup/welcome-check").json()

        self.assertEqual(first["state"], "running")
        self.assertEqual(retry["state"], "running")
        self.assertEqual(
            first, retry,
            "a retry POST while the welcome-check job is stuck must "
            "currently be indistinguishable from the original response",
        )


if __name__ == "__main__":
    unittest.main()
