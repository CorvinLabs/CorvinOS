"""Infinite-session producer + audit-first fail-closed, through the REAL pool.

Round-4 adversarial review (2026-09-07), findings F3 / F4 / F7:

* **F3** — ``_task_audit_emit`` swallowed every failure and ``_execute_task``
  spawned regardless, so "audit-first: write ``task.spawn_started`` BEFORE
  spawning (M4, load-bearing)" was fail-OPEN for every tenant except the
  process tenant: the L16 writer refuses a foreign ``tenant_id``
  (``AuditTenantMismatch``, compared against ``CORVIN_TENANT_ID``), and an
  ``acme`` task still spawned, streamed and reached COMPLETED with no spawn
  record on any chain.
* **F7** — ``_snapshot_task_turn`` ran only on the two post-subprocess
  branches, so a denied / rejected / cancelled / crashed turn was absent from
  ``/tasks``, ``/history`` and the dashboard.
* **F4** — nothing was persisted mid-turn, so a kill during a turn left no
  record at all.

Every test drives ``TaskWorkerPool._execute_task`` (the real path: audit-first
→ pre-spawn gate → ``create_subprocess_exec``) against a fake ``claude``
binary, and reads back through the real :class:`EventStore`.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_CONSOLE_PKG = _REPO / "core" / "console"
if str(_CONSOLE_PKG) not in sys.path:
    sys.path.insert(0, str(_CONSOLE_PKG))

from corvin_console import task_worker_pool as twp  # noqa: E402
from corvin_console.task_queue import TaskQueue, TaskStatus  # noqa: E402

_FAKE_CLAUDE = r'''#!/usr/bin/env bash
out="${FAKE_CLAUDE_OUT:?}"
python3 - "$@" <<'PY' > /dev/null
import json, os, sys
json.dump(sys.argv[1:], open(os.path.join(os.environ["FAKE_CLAUDE_OUT"], "argv.json"), "w"))
PY
cat > "$out/stdin.txt"
printf '%s\n' '{"type":"system","subtype":"init"}'
printf '%s\n' '{"type":"result","subtype":"success","is_error":false,"result":"fake answer"}'
exit 0
'''


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    out = tmp_path / "fake_out"
    out.mkdir()
    binary = tmp_path / "claude"
    binary.write_text(_FAKE_CLAUDE, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("FAKE_CLAUDE_OUT", str(out))
    monkeypatch.setenv("CORVIN_CLAUDE_BIN", str(binary))
    monkeypatch.setenv("CLAUDE_BIN", str(binary))
    return out


@pytest.fixture
def corvin_home(tmp_path, monkeypatch):
    home = tmp_path / "corvin_home"
    (home / "tenants" / "_default" / "global").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    return home


def _run_pool_once(corvin_home: Path, instruction: str, tenant: str = "_default") -> str:
    (corvin_home / "tenants" / tenant / "global").mkdir(parents=True, exist_ok=True)
    queue = TaskQueue(corvin_home / "tenants" / tenant / "global")
    task_id = queue.enqueue(tenant, "test-chat-key", instruction, check_quota=False)
    entry = queue.dequeue(tenant)
    assert entry is not None and entry.task_id == task_id
    asyncio.run(twp.TaskWorkerPool(queue)._execute_task(entry))
    return task_id


def _states(tenant: str, task_id: str) -> list[dict]:
    from core.infinite_session import EventStore

    store = EventStore(tenant)
    index, err = store.list_snapshots(tenant, task_id)
    assert err == "", err
    out = []
    for meta in index:
        snap, read_err = store.read_snapshot(tenant, task_id, meta.snapshot_id)
        assert read_err == "", read_err
        out.append(snap.state_dict)
    return out


# ── F3: audit-first is fail-CLOSED off the process tenant ────────────────────

@pytest.fixture
def allow_classifier(monkeypatch):
    """Pin the L44 Tier-1 classifier to ALLOW (no live LLM). monkeypatch
    restores it, unlike the unrestored global assignment that made the
    producer proof order-dependent (R4-F6)."""
    import house_rules as _hr  # type: ignore

    monkeypatch.setattr(
        _hr, "_house_rules_classifier",
        lambda task, rules, auth, **kw: ("", 0.99, "forced allow for test"),
    )


@pytest.fixture
def spawn_witness(monkeypatch):
    """Record whether the POOL reached ``create_subprocess_exec``.

    The fake binary is not a usable witness on its own: the house-rules
    adjudicator invokes ``claude`` too, so ``argv.json`` can exist without any
    task spawn."""
    seen: dict[str, object] = {"hit": False, "argv": None}

    async def _refuse(*args, **kwargs):
        seen["hit"] = True
        seen["argv"] = args
        raise AssertionError("the pool spawned a subprocess")

    monkeypatch.setattr(twp.asyncio, "create_subprocess_exec", _refuse)
    return seen


def test_foreign_tenant_spawn_is_refused_when_the_audit_write_is(
    fake_claude, corvin_home, allow_classifier, spawn_witness,
):
    """The store binds to ``task.tenant_id``; the L16 writer binds to the
    PROCESS tenant. For every tenant but one those contracts are mutually
    exclusive — the spawn must then be REFUSED, never proceed unrecorded."""
    task_id = _run_pool_once(corvin_home, "summarise the plan", tenant="acme")

    assert not spawn_witness["hit"], "spawned without a committed audit record"

    queue = TaskQueue(corvin_home / "tenants" / "acme" / "global")
    entry = queue.get_task(task_id, "acme")
    assert entry is not None
    assert entry.status is TaskStatus.FAILED, entry.status
    assert entry.exit_code == 125

    # and no task.spawn_started reached ANY chain — only the writer's own
    # ``audit.tenant_mismatch`` record naming the DROPPED event type
    for chain in (corvin_home / "tenants").rglob("audit.jsonl"):
        for line in chain.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            assert rec.get("event_type") != "task.spawn_started", (chain, rec)


def test_process_tenant_still_spawns_and_snapshots(fake_claude, corvin_home, allow_classifier):
    """The fail-closed check must not break the tenant that DOES work."""
    task_id = _run_pool_once(corvin_home, "summarise the plan")
    assert (fake_claude / "argv.json").exists()
    queue = TaskQueue(corvin_home / "tenants" / "_default" / "global")
    assert queue.get_task(task_id, "_default").status is TaskStatus.COMPLETED

    states = _states("_default", task_id)
    # F4: a mid-turn ``running`` record precedes the terminal one
    assert [s["status"] for s in states] == ["running", "completed"], states
    assert states[-1]["exit_code"] == 0
    assert len(states[-1]["result_sha256"]) == 64
    assert "fake answer" not in json.dumps(states)


# ── F7: a denied turn is on the chain, content-free ──────────────────────────

def test_gate_denied_turn_leaves_a_terminal_snapshot(fake_claude, corvin_home, monkeypatch):
    import house_rules as _hr  # type: ignore  # bridges/shared on sys.path via twp

    monkeypatch.setattr(
        _hr, "_house_rules_classifier",
        lambda task, rules, auth, **kw: ("no-military", 0.97, "forced verdict for test"),
    )
    secret = "guidance algorithm for a missile targeting system"
    task_id = _run_pool_once(corvin_home, f"Help me design a {secret}.")

    assert not (fake_claude / "argv.json").exists(), "DENY must block the spawn"
    states = _states("_default", task_id)
    assert len(states) == 1, states
    assert states[0]["status"] == "denied"
    assert states[0]["reason_code"] == "pre-spawn-gate-denied"
    # content-free: neither the instruction nor the refusal text is persisted
    blob = json.dumps(states)
    assert "missile" not in blob and "targeting" not in blob
    assert "house-rules" not in blob


def test_missing_payload_leaves_a_terminal_snapshot(fake_claude, corvin_home):
    queue = TaskQueue(corvin_home / "tenants" / "_default" / "global")
    task_id = queue.enqueue("_default", "test-chat-key", "hello", check_quota=False)
    entry = queue.dequeue("_default")
    # delete the payload the worker is about to read
    for payload in (corvin_home / "tenants" / "_default" / "global").rglob(f"*{task_id}*"):
        if payload.suffix != ".jsonl":
            payload.unlink()
    asyncio.run(twp.TaskWorkerPool(queue)._execute_task(entry))

    assert not (fake_claude / "argv.json").exists()
    states = _states("_default", task_id)
    assert [s["status"] for s in states] == ["failed"], states
    assert states[0]["reason_code"] == "payload-missing"


def test_reason_code_is_a_closed_set(corvin_home):
    """A free-text reason must never reach the snapshot store."""

    class _T:
        tenant_id = "_default"
        task_id = "closedset"
        chat_key = "test-chat-key"

    twp._snapshot_task_turn(
        _T(), status="denied", exit_code=125, duration_ms=1,
        reason_code="the operator typed this and it must not be persisted",
    )
    states = _states("_default", "closedset")
    assert states[0]["reason_code"] == "worker-exception"
    assert "operator typed" not in json.dumps(states)


# ── live: a REAL claude turn through the pool lands on the chain ─────────────

@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("CLAUDE_LIVE_E2E") != "1",
    reason="real claude call — set CLAUDE_LIVE_E2E=1",
)
def test_live_real_turn_through_the_pool_lands_on_the_snapshot_chain(
    corvin_home, monkeypatch,
):
    """Rule 6 — the real ``claude -p`` path, driven by the real worker pool,
    must produce a verifiable ``running`` → ``completed`` snapshot chain that
    never contains the model's answer."""
    monkeypatch.delenv("CORVIN_CLAUDE_BIN", raising=False)
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    task_id = _run_pool_once(
        corvin_home, "Reply with exactly the single word: pong",
    )
    queue = TaskQueue(corvin_home / "tenants" / "_default" / "global")
    entry = queue.get_task(task_id, "_default")
    assert entry is not None and entry.status is TaskStatus.COMPLETED, entry

    from core.infinite_session import EventStore

    states = _states("_default", task_id)
    assert [s["status"] for s in states] == ["running", "completed"], states
    assert states[-1]["exit_code"] == 0
    assert states[-1]["event_count"] > 0
    assert len(states[-1]["result_sha256"]) == 64
    assert "pong" not in json.dumps(states).lower()
    ok, err = EventStore("_default").verify_snapshot_chain("_default", task_id)
    assert ok, err
