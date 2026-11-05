"""/task worker spawn: environment, tenant and model (DLG-8, review 2026-11-05).

The console ``/task`` worker ran ``claude -p`` with the console process's FULL
environment (every API key, bot token and the audit anchor key), no
``CORVIN_TENANT_ID`` of its own (its hooks attributed to the console process
tenant) and no ``--model`` (the Console Models-page worker choice was ignored).

This drives the REAL pool path — ``TaskWorkerPool._execute_task`` → audit-first
→ pre-spawn gates → ``create_subprocess_exec`` — against a recording stand-in
``claude`` and asserts on what the child process actually received.
"""
from __future__ import annotations

import asyncio
import json
import stat
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_CONSOLE_PKG = _REPO / "core" / "console"
if str(_CONSOLE_PKG) not in sys.path:
    sys.path.insert(0, str(_CONSOLE_PKG))

from corvin_console import task_worker_pool as twp  # noqa: E402
from corvin_console.task_queue import TaskQueue, TaskStatus  # noqa: E402

_SECRETS = {
    "OPENAI_API_KEY": "sk-dlg8-openai-marker",
    "GITHUB_TOKEN": "ghp_dlg8githubmarker000000000000000000",
    "CORVIN_WDAT_KEY": "dlg8-wdat-marker",
    "SOME_BOT_TOKEN": "dlg8-bot-marker",
}

_FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys
out = os.environ["FAKE_CLAUDE_OUT"]
json.dump({"argv": sys.argv[1:], "env": dict(os.environ)},
          open(os.path.join(out, "spawn.json"), "w"))
sys.stdin.read()
print(json.dumps({"type": "system", "subtype": "init"}))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "result": "fake answer"}))
'''

PIN = "claude-sonnet-5-5"


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
    for name, value in _SECRETS.items():
        monkeypatch.setenv(name, value)
    # The console process runs for its boot tenant; the task belongs to another.
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    # Inputs of the ACS worker-model rule that must not leak in from the host.
    monkeypatch.delenv("CORVIN_ACS_WORKER_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.delenv("CORVIN_ENGINE_ID", raising=False)
    return out


@pytest.fixture
def corvin_home(tmp_path, monkeypatch):
    home = tmp_path / "corvin_home"
    for tid in ("_default", "beta"):
        (home / "tenants" / tid / "global").mkdir(parents=True)
    # The Models-page worker pin of tenant "beta" (as the Console persists it:
    # provider-qualified) — and a different one for the process tenant.
    (home / "tenants" / "beta" / "global" / "tenant.corvin.yaml").write_text(
        "spec:\n  engine_models:\n    claude_code:\n"
        f"      worker_model: anthropic/{PIN}\n", encoding="utf-8")
    (home / "tenants" / "_default" / "global" / "tenant.corvin.yaml").write_text(
        "spec:\n  engine_models:\n    claude_code:\n"
        "      worker_model: anthropic/claude-opus-5-5\n", encoding="utf-8")
    for p in home.rglob("tenant.corvin.yaml"):
        p.chmod(0o600)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    return home


def _run_pool_once(home: Path, tenant: str, instruction: str) -> str:
    queue = TaskQueue(home / "tenants" / tenant / "global")
    task_id = queue.enqueue(tenant, "test-chat-key", instruction, check_quota=False)
    entry = queue.dequeue(tenant)
    assert entry is not None and entry.task_id == task_id
    asyncio.run(twp.TaskWorkerPool(queue)._execute_task(entry))
    return task_id


def test_task_worker_spawn_env_and_model_through_the_pool(fake_claude, corvin_home, monkeypatch):
    """The real pool path. The task runs for the PROCESS tenant here: the
    audit-first ``task.spawn_started`` record is refused for any other tenant
    (AuditTenantMismatch at the chain chokepoint), so a foreign-tenant task
    never reaches the spawn in this process — the per-tenant resolution is
    pinned by the two tests below."""
    monkeypatch.setenv("CORVIN_TENANT_ID", "beta")
    task_id = _run_pool_once(corvin_home, "beta", "summarise the notes")
    spawn_file = fake_claude / "spawn.json"
    assert spawn_file.exists(), "the pool never spawned the worker"
    rec = json.loads(spawn_file.read_text(encoding="utf-8"))
    argv, env = rec["argv"], rec["env"]

    for name, value in _SECRETS.items():
        assert name not in env, f"{name} reached the /task worker"
        assert value not in json.dumps(rec)
    assert env["CORVIN_TENANT_ID"] == "beta"

    # The Models-page worker pin, normalised (no provider prefix on the CLI).
    assert argv[argv.index("--model") + 1] == PIN, argv

    entry = TaskQueue(corvin_home / "tenants" / "beta" / "global").get_task(task_id, "beta")
    assert entry is not None and entry.status == TaskStatus.COMPLETED, entry


def test_worker_env_binds_the_task_tenant_not_the_process_tenant(fake_claude, corvin_home):
    env = twp._worker_spawn_env("beta")  # the process tenant is "_default"
    assert env["CORVIN_TENANT_ID"] == "beta"
    for name in _SECRETS:
        assert name not in env


def test_worker_model_is_the_task_tenants_console_choice(fake_claude, corvin_home):
    assert twp._task_worker_model("beta") == PIN  # process tenant "_default" pins Opus
    assert twp._task_worker_model("_default") == "claude-opus-5-5"


def test_task_worker_env_refuses_when_the_strip_is_unavailable(monkeypatch):
    """Fail-closed: no shared strip helper → no env → no spawn."""
    monkeypatch.setattr(twp, "_acs_worker_env_helpers", lambda: None)
    with pytest.raises(RuntimeError, match="worker env"):
        twp._worker_spawn_env("beta")
