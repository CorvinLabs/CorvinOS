"""PLAN-0969 WP-1: both shipped hosts run the ACO boot healer, and it writes its audit record.

A real ``uvicorn`` process per host in the sandbox (no TestClient). One session workdir with a
``chat_debug.jsonl`` is seeded first, because the healer records ``aco.boot_heal`` only after it
scanned at least one session. The first cycle starts ``_BOOT_DELAY_SECS`` (8 s) after boot.

Before the fix the standalone console (``corvinos-serve``) started no healer, and on both hosts
the record was never written (``from .. import audit`` resolved to a module that does not exist).
"""
from __future__ import annotations

import json
import time

import pytest

from ._preconditions import SKIP_REASON

if SKIP_REASON is not None:
    pytest.skip(SKIP_REASON, allow_module_level=True)

from .harness import Server  # noqa: E402

_WAIT_S = 60.0


def _seed_session(sandbox, tenant: str) -> None:
    wd = sandbox.tenant_home(tenant) / "sessions" / "web_healerseed"
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "chat_debug.jsonl").write_text(
        json.dumps({"ts": time.time(), "event": "turn.start"}) + "\n", encoding="utf-8")


def _boot_heal_records(sandbox) -> list[dict]:
    out = []
    for chain in sandbox.chain_files():
        for line in chain.read_text(encoding="utf-8", errors="replace").splitlines():
            if '"aco.boot_heal"' not in line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


@pytest.mark.parametrize("kind,name", [(Server.STANDALONE, "standalone"), (Server.GATEWAY, "gateway")])
def test_host_runs_the_boot_healer_and_audits_it(sandbox, live_tripwire, kind, name):
    _seed_session(sandbox, "_default")
    before = len(_boot_heal_records(sandbox))
    srv = Server(sandbox, kind, f"healer-{name}").start()
    try:
        deadline = time.monotonic() + _WAIT_S
        while time.monotonic() < deadline and len(_boot_heal_records(sandbox)) <= before:
            time.sleep(1.0)
        records = _boot_heal_records(sandbox)
        assert len(records) > before, (
            f"{name}: no aco.boot_heal record within {_WAIT_S:.0f}s\n{srv.log_tail(3000)}")
        details = records[-1].get("details") or {}
        assert details.get("sessions_scanned", 0) >= 1, records[-1]
        assert srv.proc is not None and srv.proc.poll() is None, f"{name} died: {srv.log_tail()}"
    finally:
        # Both hosts load the built-in plugins from the sibling Corvin-Marketplace checkout
        # (plugins live there, by rule), so stop_checked()'s REPO_ROOT-only origin check would
        # fail on code this test does not exercise. The healer code itself is in REPO_ROOT.
        srv.stop()
