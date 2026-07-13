"""Tests for POST /setup/whatsapp/start + GET /setup/whatsapp/start/status.

Confirmed blind spot (2026-07-13): the whole /setup/whatsapp/* surface had
zero test coverage even though the sibling /setup/welcome-check endpoint (same
file, same async-job/poll/idempotency pattern) has a dedicated test file
(test_setup_welcome_check.py). This file closes that gap for whatsapp_start:

  1. CSRF is actually enforced (require_csrf dependency, line ~711) — missing
     token and wrong token both -> 403, using a REAL session + real/derived
     CSRF token (the test_license_http_gates.py pattern), not the
     dependency-override auth bypass used below for the business-logic tests.
  2. The idempotency guard: a second POST while the job is still "running"
     is a no-op that returns the SAME in-flight state and does NOT spawn a
     second worker thread (proven via a call counter on the patched
     _run_wa_start_job, not just by asserting equal JSON).
  3. Error branch: _import_bridge_manager() returning None -> state "error"
     with "bridge manager not available".
  4. Error branch: bm.start_channel_detached(...) reporting node_missing ->
     state "error" AND result.node_steps is populated via _node_install_steps().
  5. Happy path: bm.start_channel_detached(...) ok -> state "done".

Harness follows the FastAPI TestClient + isolated-CORVIN_HOME pattern
established by test_setup_welcome_check.py (auth-bypass sandbox for
business-logic tests) and test_license_http_gates.py (real-session sandbox
for CSRF-gate tests).
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
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


def _make_home(tmp_path: Path) -> tuple[Path, str]:
    home = tmp_path / "corvin_home"
    tenant_id = "_default"
    (home / "tenants" / tenant_id / "global" / "auth").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)
    return home, tenant_id


@contextmanager
def _sandbox_bypassed(tmp_path: Path):
    """FastAPI TestClient with an isolated CORVIN_HOME and bypassed auth.

    Used for the business-logic tests (idempotency + worker error branches),
    where CSRF/session enforcement is not what's under test.
    """
    home, tenant_id = _make_home(tmp_path)

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
        mock_session.tenant_id = tenant_id
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


@contextmanager
def _sandbox_real_auth(tmp_path: Path, *, set_csrf: bool = True):
    """FastAPI TestClient with a REAL session + REAL/derived CSRF token.

    Used for the CSRF-gate tests, where the require_csrf dependency itself
    must actually run (not be dependency-overridden away).
    """
    home, tenant_id = _make_home(tmp_path)

    prev_home = os.environ.get("CORVIN_HOME")
    prev_tid = os.environ.get("CORVIN_TENANT_ID")
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_id

    try:
        _reset_modules()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from corvin_console import auth as _auth
        from corvin_console.app import router as console_router

        rec = _auth.create_session(tenant_id=tenant_id, token_fingerprint="test-fp")
        csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)

        app = FastAPI()
        app.include_router(console_router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        if set_csrf:
            client.headers.update({"X-CSRF-Token": csrf})

        yield client
    finally:
        for k, v in [("CORVIN_HOME", prev_home), ("CORVIN_TENANT_ID", prev_tid)]:
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


def _poll_wa_start(tc, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = tc.get("/v1/console/setup/whatsapp/start/status").json()
        if data.get("state") in ("done", "error"):
            return data
        time.sleep(0.02)
    raise AssertionError(f"whatsapp/start did not finish within {timeout}s")


class TestWhatsappStartCsrfGate(unittest.TestCase):
    """POST /setup/whatsapp/start requires X-CSRF-Token (require_csrf, not
    require_session) — a regression here would be a silent CSRF bypass."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_start_without_csrf_returns_403(self):
        with _sandbox_real_auth(Path(self._tmp), set_csrf=False) as client:
            resp = client.post("/v1/console/setup/whatsapp/start")
            self.assertEqual(
                resp.status_code, 403,
                f"Expected 403 without CSRF, got {resp.status_code}: {resp.text}",
            )

    def test_start_with_wrong_csrf_returns_403(self):
        with _sandbox_real_auth(Path(self._tmp), set_csrf=False) as client:
            resp = client.post(
                "/v1/console/setup/whatsapp/start",
                headers={"X-CSRF-Token": "definitely-wrong-token"},
            )
            self.assertEqual(
                resp.status_code, 403,
                f"Expected 403 with wrong CSRF, got {resp.status_code}: {resp.text}",
            )


class TestWhatsappStartIdempotency(unittest.TestCase):
    """A second call while the job is running must be a no-op returning the
    in-flight state, not a second worker thread."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_second_call_while_running_returns_in_flight_state_no_duplicate_job(self):
        call_count = 0
        release = threading.Event()
        started = threading.Event()

        def _blocking_job():
            nonlocal call_count
            call_count += 1
            started.set()
            release.wait(timeout=5.0)

        with _sandbox_bypassed(Path(self._tmp)) as tc:
            with patch(
                "corvin_console.routes.setup._run_wa_start_job",
                side_effect=_blocking_job,
            ):
                r1 = tc.post("/v1/console/setup/whatsapp/start")
                self.assertTrue(started.wait(timeout=5.0), "worker thread never started")

                r2 = tc.post("/v1/console/setup/whatsapp/start")
                release.set()

        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r1.json()["state"], "running")
        # Second call must return the SAME in-flight state, not restart it.
        self.assertEqual(r2.json(), r1.json())
        # Exactly one worker thread must have been spawned — the lock guard
        # must have short-circuited the second POST before threading.Thread(...).
        self.assertEqual(call_count, 1, "a second call while running must not spawn a duplicate job")


class TestWhatsappStartWorkerErrorBranches(unittest.TestCase):
    """Exercise _run_wa_start_job's two documented error branches plus the
    happy path, end-to-end through the real POST + poll-status routes."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_bridge_manager_unavailable_yields_error_state(self):
        with _sandbox_bypassed(Path(self._tmp)) as tc:
            with patch(
                "corvin_console.routes.setup._import_bridge_manager",
                return_value=None,
            ):
                tc.post("/v1/console/setup/whatsapp/start")
                data = _poll_wa_start(tc)

        self.assertEqual(data["state"], "error")
        self.assertEqual(data["result"]["error"], "bridge manager not available")

    def test_node_missing_yields_error_state_with_install_steps(self):
        fake_bm = MagicMock()
        fake_bm.start_channel_detached.return_value = {"ok": False, "node_missing": True}

        with _sandbox_bypassed(Path(self._tmp)) as tc:
            with patch(
                "corvin_console.routes.setup._import_bridge_manager",
                return_value=fake_bm,
            ):
                tc.post("/v1/console/setup/whatsapp/start")
                data = _poll_wa_start(tc)

        self.assertEqual(data["state"], "error")
        self.assertTrue(data["result"]["node_missing"])
        self.assertIn("node_steps", data["result"], "node_missing must attach install guidance")
        self.assertIn("steps", data["result"]["node_steps"])
        self.assertIn("download_url", data["result"]["node_steps"])

    def test_successful_start_yields_done_state(self):
        fake_bm = MagicMock()
        fake_bm.start_channel_detached.return_value = {"ok": True}

        with _sandbox_bypassed(Path(self._tmp)) as tc:
            with patch(
                "corvin_console.routes.setup._import_bridge_manager",
                return_value=fake_bm,
            ):
                tc.post("/v1/console/setup/whatsapp/start")
                data = _poll_wa_start(tc)

        self.assertEqual(data["state"], "done")
        self.assertEqual(data["result"], {"ok": True})

    def test_worker_exception_never_crashes_the_job(self):
        with _sandbox_bypassed(Path(self._tmp)) as tc:
            with patch(
                "corvin_console.routes.setup._import_bridge_manager",
                side_effect=RuntimeError("boom"),
            ):
                tc.post("/v1/console/setup/whatsapp/start")
                data = _poll_wa_start(tc)

        self.assertEqual(data["state"], "error")
        self.assertIn("Unexpected error", data["result"]["error"])


if __name__ == "__main__":
    unittest.main()
