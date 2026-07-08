"""Regression tests for PENTEST-10 — /v1/console/local-stats is loopback-only.

The endpoint is intentionally unauthenticated so the local dashboard JS can
fetch it without a prior login. Its docstring long claimed to be "localhost
-only", but the standalone server binds 0.0.0.0 and the route enforced nothing
— so a remote unauthenticated caller could read version / platform / engine /
instance-id-prefix / session-count / uptime for reconnaissance.

The fix gates the route on the request's peer address: a loopback client is
served, everything else gets 403. These tests drive the route through a
FastAPI TestClient with an overridden client address (Starlette's ``client=``)
to exercise both sides.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
for _p in (
    str(_REPO / "core" / "console"),
    str(_REPO / "operator"),
    str(_REPO / "operator" / "forge"),
    str(_REPO / "operator" / "bridges" / "shared"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@contextmanager
def _app_client(client_addr):
    """A FastAPI app mounting just the local_stats router, with the TestClient's
    reported peer address set to *client_addr* (host, port)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from corvin_console.routes import local_stats as ls

    home = tempfile.mkdtemp(prefix="ls-test-")
    prev = os.environ.get("CORVIN_HOME")
    os.environ["CORVIN_HOME"] = home
    try:
        app = FastAPI()
        app.include_router(ls.router)
        with TestClient(app, client=client_addr, raise_server_exceptions=False) as c:
            yield c
    finally:
        if prev is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = prev


class LocalStatsLoopbackGate(unittest.TestCase):
    def test_loopback_v4_allowed(self):
        with _app_client(("127.0.0.1", 5555)) as c:
            r = c.get("/local-stats")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        # Sanity: the snapshot fields recon would want are present when allowed.
        for key in ("version", "platform", "engine", "instance_id", "uptime_seconds"):
            self.assertIn(key, body)

    def test_loopback_v6_allowed(self):
        with _app_client(("::1", 5555)) as c:
            r = c.get("/local-stats")
        self.assertEqual(r.status_code, 200, r.text)

    def test_remote_client_rejected(self):
        with _app_client(("203.0.113.9", 40000)) as c:
            r = c.get("/local-stats")
        self.assertEqual(r.status_code, 403, r.text)
        # No recon fields leak in the rejection body.
        self.assertNotIn("version", r.json())

    def test_private_lan_client_rejected(self):
        """A caller on the LAN (server bound 0.0.0.0) is still not loopback."""
        with _app_client(("192.168.1.50", 40000)) as c:
            r = c.get("/local-stats")
        self.assertEqual(r.status_code, 403, r.text)


if __name__ == "__main__":
    unittest.main()
