"""A RUNNING host speaks the current A2A contract — checked over its real HTTP boundary.

Unit and in-process E2E suites prove the code; they cannot prove that the PROCESS an operator actually runs has
loaded it. A service keeps serving the code it was started with until it is restarted — the console bundle can be
new while the A2A receiver behind it is not.

This runs the SAME probe the continuous watch runs (``corvin_operator/bridges/shared/a2a_live_probe.py``): a check
only one of them exercised would be a dead mechanism for the other. Point it at any host, hermetically:

    CORVIN_LIVE_URL=http://127.0.0.1:8765 \\
    python3 -m pytest --noconftest tests/e2e/a2a/test_live_host_a2a_contract_e2e.py -q

``--noconftest``: the test is plain HTTP; the repo's root conftest pulls in the console package and fails in any
environment without ``corvin_core`` on the path (a detached systemd job hit exactly that).

It never touches a real pairing: the probe drops a THROWAWAY origin (random id/keys, mode 0600) and removes it
again. Skipped unless ``CORVIN_LIVE_URL`` is set. Contract (ADR-2242 §2/§8): see ``a2a_live_probe``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "corvin_operator" / "bridges" / "shared"))

import a2a_live_probe as probe  # noqa: E402

BASE = (os.environ.get("CORVIN_LIVE_URL") or "").rstrip("/")
pytestmark = pytest.mark.skipif(not BASE, reason="set CORVIN_LIVE_URL to a running host")

EXPECTED = ("ordinary_ping", "pong_capacity", "task_status_unknown", "reaimed_ignored", "forged_refused",
            "feed_shape")


@pytest.fixture(scope="module")
def checks() -> dict:
    found, _feed = probe.probe_host(BASE)
    return {c.name: c for c in found}


def test_the_probe_ran_every_check(checks):
    assert set(EXPECTED) <= set(checks), f"missing: {set(EXPECTED) - set(checks)}; got {sorted(checks)}"
    assert "probe_origin" not in checks or checks["probe_origin"].ok, checks["probe_origin"].detail


@pytest.mark.parametrize("name", EXPECTED)
def test_contract_check(checks, name):
    c = checks[name]
    assert c.ok, f"{name}: {c.detail}"
