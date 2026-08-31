"""Regression tests for the loopback auth-bypass window on
GET /setup/onboarding/detect (ADR-0120 M1, `detect_engines` in
core/console/corvin_console/routes/setup.py).

This route inlines its own loopback-only auth-bypass gate, independent of
(and untested compared to) the sibling patterns in ``auth_routes._is_localhost``
(see test_local_login.py) and the PENTEST-10 loopback gate on
``/v1/console/local-stats`` (see test_local_stats.py):

    loopback = request.client.host in ("127.0.0.1", "::1", "localhost")
    complete = _onboarding_complete()
    if session is None and not (loopback and not complete):
        raise HTTPException(401)

i.e. an unauthenticated caller is only ever admitted when BOTH (a) the peer
address is loopback AND (b) onboarding has not yet completed. These tests
drive the route through a FastAPI TestClient with an overridden peer address
(Starlette's ``client=`` kwarg — the same technique as test_local_stats.py)
and a real (unmocked) ``_onboarding_complete()`` backed by a real
``onboarding.json`` file in an isolated CORVIN_HOME, to prove:

  1. loopback + onboarding incomplete + no session cookie -> 200 (bypass open)
  2. loopback + onboarding complete    + no session cookie -> 401 (window closed)
  3. non-loopback + onboarding incomplete + no session cookie -> 401 (never open remotely)

Harness follows the FastAPI TestClient + isolated-CORVIN_HOME + auth-bypass
pattern established by test_engine_detect_routes_adr0125.py /
test_setup_welcome_check.py, combined with the peer-address override pattern
from test_local_stats.py. ``engine_detector.detect_all`` is mocked so no real
binaries/subprocesses are probed.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
for _p in (
    str(_REPO / "core" / "console"),
    str(_REPO / "operator" / "bridges" / "shared"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _reset_modules():
    for key in list(sys.modules):
        if key == "corvin_console" or key.startswith("corvin_console."):
            del sys.modules[key]


@contextmanager
def _sandbox(tmp_path: Path, client_addr: tuple[str, int]):
    """FastAPI TestClient with an isolated CORVIN_HOME and the TestClient's
    reported peer address set to *client_addr* (host, port). No auth
    dependency overrides are installed — GET /setup/onboarding/detect uses
    ``_optional_session`` (Cookie-driven), not ``require_session``, so an
    override would mask exactly the behavior under test."""
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

        app = FastAPI()
        app.include_router(console_router, prefix="/v1/console")

        with TestClient(app, client=client_addr, raise_server_exceptions=False) as tc:
            yield tc, home
    finally:
        for k, v in [("CORVIN_HOME", prev_home), ("CORVIN_TENANT_ID", prev_tid)]:
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


def _write_onboarding_complete(home: Path, *, complete: bool) -> None:
    path = home / "tenants" / "_default" / "global" / "onboarding.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"complete": complete}))


def _fake_probes():
    from engine_detector import EngineProbe
    return [
        EngineProbe(
            engine_id="claude_code", found=True, version="1.2.3",
            detail="ok", locality="us_cloud", capabilities=[],
        ),
    ]


class OnboardingDetectLoopbackGate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="onboarding-detect-")
        self._tmp_path = Path(self._tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_loopback_v4_and_onboarding_incomplete_bypasses_auth(self):
        """The documented bypass window: no onboarding.json at all (fresh
        install) + a loopback peer -> served without any session cookie."""
        with _sandbox(self._tmp_path, ("127.0.0.1", 5555)) as (tc, _home):
            with patch("engine_detector.detect_all", return_value=_fake_probes()):
                resp = tc.get("/v1/console/setup/onboarding/detect")

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["onboarding_complete"], False)
        self.assertEqual([p["engine_id"] for p in body["engines"]], ["claude_code"])

    def test_loopback_v6_and_onboarding_incomplete_bypasses_auth(self):
        with _sandbox(self._tmp_path, ("::1", 5555)) as (tc, _home):
            with patch("engine_detector.detect_all", return_value=_fake_probes()):
                resp = tc.get("/v1/console/setup/onboarding/detect")

        self.assertEqual(resp.status_code, 200, resp.text)

    def test_loopback_but_onboarding_complete_closes_the_window(self):
        """Core regression: once POST /setup/complete has run (onboarding.json
        complete=true), the SAME loopback peer with no session cookie must be
        rejected. If ``_onboarding_complete()`` ever regressed to always
        return False (or the boolean logic got inverted), this is the test
        that would catch the reopened bypass."""
        with _sandbox(self._tmp_path, ("127.0.0.1", 5555)) as (tc, home):
            _write_onboarding_complete(home, complete=True)
            with patch("engine_detector.detect_all", return_value=_fake_probes()):
                resp = tc.get("/v1/console/setup/onboarding/detect")

        self.assertEqual(resp.status_code, 401, resp.text)
        self.assertNotIn("engines", resp.json())

    def test_non_loopback_client_with_onboarding_incomplete_is_still_rejected(self):
        """The exemption is loopback-gated, not onboarding-state-gated alone:
        a remote (LAN or WAN) caller must never be admitted, even during the
        legitimate first-run window."""
        with _sandbox(self._tmp_path, ("192.168.1.50", 40000)) as (tc, _home):
            with patch("engine_detector.detect_all", return_value=_fake_probes()):
                resp = tc.get("/v1/console/setup/onboarding/detect")

        self.assertEqual(resp.status_code, 401, resp.text)
        self.assertNotIn("engines", resp.json())

    def test_non_loopback_client_with_onboarding_complete_is_rejected(self):
        """Belt-and-suspenders: remote + complete is the least permissive
        combination and must also 401."""
        with _sandbox(self._tmp_path, ("203.0.113.9", 40000)) as (tc, home):
            _write_onboarding_complete(home, complete=True)
            with patch("engine_detector.detect_all", return_value=_fake_probes()):
                resp = tc.get("/v1/console/setup/onboarding/detect")

        self.assertEqual(resp.status_code, 401, resp.text)


if __name__ == "__main__":
    unittest.main()
