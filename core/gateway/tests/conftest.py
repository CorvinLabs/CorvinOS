"""Gateway test-suite conftest.

Peer address of the test client (adversarial review E-09, 2026-09-03)
--------------------------------------------------------------------
``corvin_gateway.app._jwt_guard`` now requires a Bearer JWT from every
NON-loopback peer. Starlette's ``TestClient`` reports the peer as
``("testclient", 50000)`` by default — not an IP address at all, which the
guard treats (fail-closed) as remote and answers 401.

The existing suite models the LOCAL deployment: an operator on the same host
talking to a loopback-bound gateway. That is the peer this fixture gives every
``TestClient`` that does not name one explicitly — ``("127.0.0.1", 50000)`` —
so the suite keeps testing the routes rather than the guard. Tests that
exercise the remote path pass ``client=("203.0.113.9", 4242)`` themselves
(see ``test_jwt_guard_peer.py``); an explicit ``client=`` always wins.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

_LOOPBACK_PEER = ("127.0.0.1", 50000)


@pytest.fixture(autouse=True)
def _loopback_test_client(monkeypatch):
    original_init = TestClient.__init__

    def _init(self, *args, **kwargs):
        if "client" not in kwargs:
            kwargs["client"] = _LOOPBACK_PEER
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(TestClient, "__init__", _init)
    yield
