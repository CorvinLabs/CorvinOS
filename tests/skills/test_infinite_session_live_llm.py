"""LIVE E2E — a real ``claude -p --model haiku`` turn survives a "restart".

Opt-in: ``CLAUDE_LIVE_E2E=1`` and the ``claude`` CLI on PATH. Drives the real
LLM path, snapshots the turn's outcome through the real ``EventStore`` (the
core hash-chained audit writer, audit-first) under a temporary
``CORVIN_HOME``, verifies the chain, constructs a NEW store instance (the
"restart") and recovers the latest snapshot — asserting equality with what
was written.

Only content-free metadata is snapshotted (the answer's SHA-256, length and
exit code) — the schema's PII gate and CLAUDE.md's audit rules forbid raw
model output in persisted state.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time

import pytest

live = pytest.mark.skipif(
    os.environ.get("CLAUDE_LIVE_E2E", "") != "1" or shutil.which("claude") is None,
    reason="live infinite-session E2E needs CLAUDE_LIVE_E2E=1 and the claude CLI",
)
pytestmark = [live, pytest.mark.live]

TENANT = "_default"


def _claude_turn(prompt: str) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    proc = subprocess.run(
        ["claude", "-p", prompt, "--model", "haiku"],
        capture_output=True, text=True, timeout=180, env=env,
    )
    return proc.returncode, proc.stdout.strip()


def test_live_turn_snapshot_restart_recover(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_TENANT_ID", TENANT)
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)

    from core.infinite_session import EventStore, SnapshotType, snapshot_task_state

    task_id = f"live_{int(time.time())}"
    store = EventStore(TENANT)  # default = CORE hash-chained audit writer

    # Turn 1: real LLM call
    t0 = time.time()
    rc, answer = _claude_turn("Reply with exactly the single word: pong")
    duration_ms = int((time.time() - t0) * 1000)
    assert rc == 0, answer
    assert "pong" in answer.lower()

    state_1 = {
        "turn": 1,
        "exit_code": rc,
        "duration_ms": duration_ms,
        "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
        "answer_chars": len(answer),
        "model": "haiku",
    }
    snap_1, err = snapshot_task_state(TENANT, task_id, state_1, phase_id="turn_1", store=store)
    assert err == "", err

    # Turn 2: a second real call chained onto the first
    rc, answer2 = _claude_turn("Reply with exactly the single word: pang")
    assert rc == 0, answer2
    state_2 = {**state_1, "turn": 2, "answer_sha256": hashlib.sha256(answer2.encode()).hexdigest(),
               "answer_chars": len(answer2)}
    snap_2, err = snapshot_task_state(TENANT, task_id, state_2, phase_id="turn_2", store=store)
    assert err == "", err
    assert snap_2.prev_snapshot_hash == snap_1.content_hash
    assert store.verify_snapshot_chain(TENANT, task_id) == (True, "")

    # Core chain got the audit-first records
    from core.learning.event_persistence import _resolve_core_audit
    chain = _resolve_core_audit().audit_path().read_text()
    assert chain.count("infinite_session.snapshot_created") >= 2
    assert snap_1.snapshot_id in chain and snap_2.snapshot_id in chain
    assert "pong" not in chain and "pang" not in chain  # content-free

    # "Restart": brand-new store instance → recover the latest snapshot
    del store
    recovered_store = EventStore(TENANT)
    assert recovered_store.verify_snapshot_chain(TENANT, task_id) == (True, "")
    latest, err = recovered_store.get_latest_snapshot(TENANT, task_id)
    assert err == "" and latest == snap_2
    assert latest.state_dict == state_2
    assert latest.snapshot_type == SnapshotType.PHASE_CHECKPOINT
    index, _ = recovered_store.list_snapshots(TENANT, task_id)
    assert [m.seq for m in index] == [1, 2]
