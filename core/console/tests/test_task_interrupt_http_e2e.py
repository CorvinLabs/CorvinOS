"""E2E (PLAN-0958 P1): a killed worker is INTERRUPTED, visible over HTTP.

Real boundaries, nothing in the middle is mocked:
  * the REAL tasks router (``routes/tasks.py``) mounted on a FastAPI app and
    driven through ``TestClient`` — ``POST /v1/console/tasks`` creates the task,
    ``GET /v1/console/tasks/{id}`` reads it back, ``POST .../abort`` cancels it;
  * the REAL ``TaskWorkerPool._execute_task`` spawns a fake ``claude`` binary
    (``exec sleep``) through ``asyncio.create_subprocess_exec``;
  * the worker process is killed with SIGKILL from outside.

Only the auth dependencies are overridden (no session in a test) and the L44
house-rules classifier is pinned to ALLOW (no live LLM), as in the other
worker-pool suites.

One injected condition, stated: the forbidden-transition check over HTTP
simulates a stale read (the handler reads RUNNING while the log already says
COMPLETED). It exercises the real guard and the real 409 mapping.

Sandbox: CORVIN_HOME / XDG_CONFIG_HOME / HOME / CORVIN_AUDIT_ANCHOR_KEY (absolute)
in a temp dir; FORGE_ROOT unset.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import signal
import stat
import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in ("core/console", "corvin_operator/bridges/shared", "corvin_operator/forge"):
    _path = str(_REPO / _p)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from corvin_console import deps  # noqa: E402
from corvin_console import task_worker_pool as twp  # noqa: E402
from corvin_console.routes import tasks as tasks_route  # noqa: E402
from corvin_console.routes import tasks_impl  # noqa: E402
from corvin_console.task_queue import TaskQueue  # noqa: E402
from corvin_core.task_states import TaskState  # noqa: E402

_TENANT = "_default"

# Prints the engine's init line, then becomes the sleeping process itself
# (``exec``) so SIGKILL on the pid reaches the process that holds stdout.
_FAKE_CLAUDE_HANGS = r"""#!/usr/bin/env bash
cat > "$FAKE_CLAUDE_OUT/stdin.txt"
printf '%s\n' '{"type":"system","subtype":"init"}'
exec sleep 60
"""


class _Rec:
    tenant_id = _TENANT
    sid_fingerprint = "test-fp"
    csrf_token = "t"


@pytest.fixture
def client(tmp_path, monkeypatch):
    home = tmp_path / "corvin"
    (home / "tenants" / _TENANT / "global").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CORVIN_TENANT_ID", _TENANT)
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.delenv("FORGE_ROOT", raising=False)

    out = tmp_path / "out"
    out.mkdir()
    binary = tmp_path / "claude"
    binary.write_text(_FAKE_CLAUDE_HANGS, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("FAKE_CLAUDE_OUT", str(out))
    monkeypatch.setenv("CORVIN_CLAUDE_BIN", str(binary))
    monkeypatch.setenv("CLAUDE_BIN", str(binary))

    import house_rules as _hr  # type: ignore  # noqa: PLC0415 — bridges/shared on sys.path via twp
    monkeypatch.setattr(
        _hr, "_house_rules_classifier",
        lambda task, rules, auth, **kw: ("", 0.99, "forced allow for test"),
    )

    app = FastAPI()
    app.include_router(tasks_route.router, prefix="/v1/console")
    app.dependency_overrides[deps.require_csrf] = lambda: _Rec()
    app.dependency_overrides[deps.require_session] = lambda: _Rec()
    return TestClient(app)


def _queue() -> TaskQueue:
    return TaskQueue(tasks_impl._forge_paths.tenant_global_dir(_TENANT))


def _create(client: TestClient, instruction: str) -> str:
    resp = client.post("/v1/console/tasks", json={
        "chat_key": "web:e2e", "instruction": instruction,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["task_id"]


def _run_worker_and_kill(queue: TaskQueue, task_id: str) -> int | None:
    """Dequeue the task, run the real worker, SIGKILL its engine process.

    Returns the exit code the worker recorded for the killed process."""
    entry = queue.dequeue(_TENANT)
    assert entry is not None and entry.task_id == task_id

    async def drive() -> None:
        pool = twp.TaskWorkerPool(queue)
        run = asyncio.ensure_future(pool._execute_task(entry))
        deadline = time.monotonic() + 30
        while task_id not in twp._active_procs:
            assert not run.done(), "worker finished before the engine was spawned"
            assert time.monotonic() < deadline, "engine process never registered"
            await asyncio.sleep(0.05)
        os.kill(twp._active_procs[task_id].pid, signal.SIGKILL)
        await asyncio.wait_for(run, 30)

    asyncio.run(drive())
    return queue.get_task(task_id).exit_code


def test_killed_worker_is_interrupted_and_get_shows_it(client):
    task_id = _create(client, "summarise the long report")

    rc = _run_worker_and_kill(_queue(), task_id)
    assert rc == -9

    resp = client.get(f"/v1/console/tasks/{task_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == TaskState.INTERRUPTED.value == "interrupted"
    assert body["exit_code"] == -9
    assert body["ended_at"] is None  # interrupted is not terminal


def test_interrupted_task_can_still_be_cancelled_over_http(client):
    task_id = _create(client, "cancel after interruption")
    _run_worker_and_kill(_queue(), task_id)

    resp = client.post(f"/v1/console/tasks/{task_id}/abort")
    assert resp.status_code == 200, resp.text
    assert client.get(f"/v1/console/tasks/{task_id}").json()["status"] == "cancelled"


def test_forbidden_transition_is_refused_over_http_with_409(client, monkeypatch):
    """A completed task whose abort read is stale (RUNNING) is refused, not 500."""
    task_id = _create(client, "already finished")
    queue = _queue()
    entry = queue.dequeue(_TENANT)
    assert entry is not None and entry.task_id == task_id
    queue.update_status(task_id, TaskState.COMPLETED, exit_code=0)

    real_get = TaskQueue.get_task
    stale = {"pending": True}

    def stale_read_once(self, tid, tenant_id=None):
        entry_now = real_get(self, tid, tenant_id=tenant_id)
        # Only the abort handler's tenant-scoped read is stale; the guard's own
        # read inside update_status sees the real COMPLETED state.
        if tenant_id is not None and stale["pending"] and entry_now is not None:
            stale["pending"] = False
            return dataclasses.replace(entry_now, status=TaskState.RUNNING)
        return entry_now

    monkeypatch.setattr(TaskQueue, "get_task", stale_read_once)
    resp = client.post(f"/v1/console/tasks/{task_id}/abort")
    assert resp.status_code == 409, resp.text
    assert "completed -> cancelled" in resp.json()["detail"]

    monkeypatch.setattr(TaskQueue, "get_task", real_get)
    assert client.get(f"/v1/console/tasks/{task_id}").json()["status"] == "completed"
