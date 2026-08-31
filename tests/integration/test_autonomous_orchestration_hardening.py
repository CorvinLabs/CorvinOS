"""Regression tests for the autonomous-orchestration hardening (2026-08-28).

Each test here pins ONE defect that stopped long autonomous runs from
finishing or from reporting in, and would otherwise silently come back:

* a phase retry budget that could never be exhausted (infinite hot loop)
* a DAG that reported COMPLETED when its last phase had failed
* `on_failure='skip'` blocking its own dependents
* no way to continue a DAG whose process died mid-run
* concurrent phases of one task colliding in the heartbeat monitor
* a "timed out" phase left running detached forever
* one corrupt registry line making EVERY task unreadable
* a notification transport that closed its HTTP client after the first send
"""
from __future__ import annotations

import asyncio
import json

import pytest

from core.vibe_engineering.task_orchestrator import (
    Phase, TaskOrchestrator, TaskSpec,
)
from core.vibe_engineering.task_registry import (
    PhaseStatus, TaskRegistryPersistence, TaskStatus,
)
from core.vibe_engineering.task_heartbeat import HeartbeatConfig, TaskHeartbeat
from core.vibe_engineering.notification_router import NotificationRouter


@pytest.fixture
def registry(tmp_path):
    return TaskRegistryPersistence(registry_path=str(tmp_path / "registry.jsonl"))


# ── the retry budget must actually be a budget ───────────────────────────


@pytest.mark.asyncio
async def test_a_permanently_failing_phase_stops_retrying(registry, monkeypatch):
    """`_execute_phase` used to write retry_count=0 when marking a phase
    RUNNING, so `_handle_phase_failure` always computed 0+1=1 and the budget
    could never be exhausted: the phase retried FOREVER in a hot loop and the
    task never reached a terminal state."""
    monkeypatch.setenv("VIBE_RETRY_BACKOFF_BASE", "0")
    attempts = 0

    async def always_fails():
        nonlocal attempts
        attempts += 1
        raise RuntimeError("nope")

    spec = TaskSpec(task_id="t-budget", title="Budget", phases=[
        Phase(phase_id="p", handler=always_fails, retry_count=3, timeout_s=5),
    ])
    task = await asyncio.wait_for(TaskOrchestrator(registry).execute(spec),
                                  timeout=30)

    assert attempts == 3, f"retried {attempts} times, budget was 3"
    assert task.phases["p"].status == PhaseStatus.FAILED
    assert task.phases["p"].retry_count == 3


@pytest.mark.asyncio
async def test_a_failed_dag_is_not_reported_as_completed(registry, monkeypatch):
    """The final status was stamped COMPLETED unconditionally — a silent false
    success on exactly the runs that need an honest verdict."""
    monkeypatch.setenv("VIBE_RETRY_BACKOFF_BASE", "0")

    async def ok():
        return {"ok": True}

    async def fails():
        raise RuntimeError("nope")

    spec = TaskSpec(task_id="t-honest", title="Honest", phases=[
        Phase(phase_id="a", handler=ok, retry_count=1, timeout_s=5),
        Phase(phase_id="b", handler=fails, retry_count=1, timeout_s=5),
    ])
    task = await asyncio.wait_for(TaskOrchestrator(registry).execute(spec),
                                  timeout=30)

    assert task.status == TaskStatus.FAILED
    assert task.phases["a"].status == PhaseStatus.COMPLETED


@pytest.mark.asyncio
async def test_skip_lets_dependents_run(registry, monkeypatch):
    """`on_failure='skip'` declares the failure tolerable; the DAG used to
    stall on it anyway because a dependent required its dep COMPLETED."""
    monkeypatch.setenv("VIBE_RETRY_BACKOFF_BASE", "0")
    ran = []

    async def fails():
        raise RuntimeError("optional step failed")

    async def dependent():
        ran.append("dependent")
        return {"ok": True}

    spec = TaskSpec(task_id="t-skip", title="Skip", phases=[
        Phase(phase_id="optional", handler=fails, retry_count=1,
              on_failure="skip", timeout_s=5),
        Phase(phase_id="after", handler=dependent, depends_on=["optional"],
              retry_count=1, timeout_s=5),
    ])
    task = await asyncio.wait_for(TaskOrchestrator(registry).execute(spec),
                                  timeout=30)

    assert ran == ["dependent"]
    assert task.status == TaskStatus.COMPLETED


# ── a dead process must not strand the run ───────────────────────────────


@pytest.mark.asyncio
async def test_resume_continues_a_run_whose_process_died(registry):
    """The DAG used to live only in the execute() call stack: a process death
    left RUNNING phases in the registry with nothing able to pick them up."""
    calls = {"a": 0, "b": 0}

    async def phase_a():
        calls["a"] += 1
        return {"done": "a"}

    async def phase_b():
        calls["b"] += 1
        return {"done": "b"}

    spec = TaskSpec(task_id="t-resume", title="Resume", phases=[
        Phase(phase_id="a", handler=phase_a, timeout_s=5),
        Phase(phase_id="b", handler=phase_b, depends_on=["a"], timeout_s=5),
    ])
    orch = TaskOrchestrator(registry)

    # Simulate: phase a committed, phase b was RUNNING when the process died.
    await orch.execute(TaskSpec(task_id="t-resume", title="Resume",
                                phases=[Phase(phase_id="a", handler=phase_a,
                                              timeout_s=5)]))
    from core.vibe_engineering.task_registry import PhaseMetadata, TaskMetadata
    task = await registry.get_task("t-resume")
    await registry.append_task(TaskMetadata(
        task_id="t-resume", title="Resume", status=TaskStatus.RUNNING,
        phases={
            "a": task.phases["a"],
            "b": PhaseMetadata(phase_id="b", status=PhaseStatus.RUNNING),
        },
        created_at=task.created_at, tenant_id="_default",
    ))

    resumed = await asyncio.wait_for(orch.resume(spec), timeout=30)

    assert resumed.status == TaskStatus.COMPLETED
    assert calls["b"] == 1, "the interrupted phase must be re-armed and run"
    assert calls["a"] == 1, "a COMPLETED phase must never be re-run"


# ── the heartbeat monitor must survive concurrency ───────────────────────


@pytest.mark.asyncio
async def test_concurrent_phases_of_one_task_do_not_collide():
    """`_active_phases` was keyed by task_id alone while the orchestrator runs
    every ready phase concurrently — the second phase to finish raised KeyError
    out of the `finally`, turning a SUCCESSFUL phase into a failure."""
    hb = TaskHeartbeat(HeartbeatConfig(interval_s=999, stall_threshold_s=999,
                                       timeout_grace_s=5))

    async def work(name):
        await asyncio.sleep(0.05)
        return {"who": name}

    async def noop(_data):
        return None

    results = await asyncio.gather(*[
        hb.monitor_phase("task-1", f"phase-{i}", lambda n=i: work(n), 5,
                         noop, noop)
        for i in range(4)
    ])

    assert sorted(r["who"] for r in results) == [0, 1, 2, 3]
    assert hb._active_phases == {}


@pytest.mark.asyncio
async def test_a_timed_out_phase_is_actually_cancelled():
    """The inner phase task was created with create_task and never cancelled,
    so a "timed out" phase kept running detached — still burning the engine and
    the budget, invisibly."""
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def never_finishes():
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return {}

    async def noop(_data):
        return None

    hb = TaskHeartbeat(HeartbeatConfig(interval_s=999, stall_threshold_s=999,
                                       timeout_grace_s=0))
    with pytest.raises(asyncio.TimeoutError):
        await hb.monitor_phase("task-x", "p", never_finishes, 1, noop, noop)

    assert started.is_set()
    await asyncio.sleep(0.05)
    assert cancelled.is_set(), "the phase coroutine was left running detached"


@pytest.mark.asyncio
async def test_a_raising_notifier_does_not_fail_the_phase():
    hb = TaskHeartbeat(HeartbeatConfig(interval_s=0, stall_threshold_s=0,
                                       timeout_grace_s=5))

    async def work():
        await asyncio.sleep(1.1)
        return {"ok": True}

    async def boom(_data):
        raise RuntimeError("the notifier is broken")

    result = await hb.monitor_phase("t", "p", work, 10, boom, boom)
    assert result == {"ok": True}


# ── the registry must survive a torn write ───────────────────────────────


@pytest.mark.asyncio
async def test_one_corrupt_line_does_not_hide_every_task(registry, tmp_path):
    """A single torn line used to raise RuntimeError out of get_task, making
    EVERY task in the registry permanently unreadable — losing every in-flight
    long run at once."""
    from core.vibe_engineering.task_registry import PhaseMetadata, TaskMetadata

    await registry.append_task(TaskMetadata(
        task_id="good-1", title="Good", status=TaskStatus.RUNNING,
        phases={"p": PhaseMetadata(phase_id="p", status=PhaseStatus.PENDING)},
    ))
    with open(registry.registry_path, "a") as f:
        f.write("{this is not json\n")
    await registry.append_task(TaskMetadata(
        task_id="good-2", title="Good", status=TaskStatus.RUNNING,
        phases={"p": PhaseMetadata(phase_id="p", status=PhaseStatus.PENDING)},
    ))

    assert (await registry.get_task("good-1")) is not None
    assert (await registry.get_task("good-2")) is not None
    assert registry.corrupt_line_count == 1
    assert {t.task_id for t in await registry.list_tasks()} == {"good-1", "good-2"}


@pytest.mark.asyncio
async def test_compaction_collapses_the_log_without_losing_state(registry):
    from core.vibe_engineering.task_registry import PhaseMetadata, TaskMetadata

    for i in range(20):
        await registry.append_task(TaskMetadata(
            task_id="t", title="T", status=TaskStatus.RUNNING,
            phases={"p": PhaseMetadata(phase_id="p",
                                       status=PhaseStatus.RUNNING,
                                       retry_count=i)},
        ))
    assert await registry.compact() == 1
    task = await registry.get_task("t")
    assert task.phases["p"].retry_count == 19, "latest state must survive"
    assert len(registry.registry_path.read_text().splitlines()) == 1


# ── the notification transport must survive its own first send ───────────


@pytest.mark.asyncio
async def test_router_sends_more_than_once(monkeypatch):
    """`async with self.http_client` CLOSED the shared client on the first
    send; every later send raised "client has been closed" and was swallowed.
    At most ONE notification per process could ever be delivered."""
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://example.invalid/hook")
    posts = []

    class _Resp:
        status_code = 204
        text = ""
        headers: dict = {}

    class _Client:
        def __init__(self):
            self.closed = False

        async def post(self, url, json=None):
            if self.closed:
                raise RuntimeError("Cannot send a request, as the client has been closed.")
            posts.append(json)
            return _Resp()

        async def aclose(self):
            self.closed = True

    router = NotificationRouter(http_client=_Client())
    router._task_progress = None  # isolate the webhook transport

    for i in range(5):
        await router.on_phase_completed({"task_id": "t", "phase_id": f"p{i}"})

    assert len(posts) == 5


@pytest.mark.asyncio
async def test_router_heartbeat_is_throttled_by_time_not_modulo(monkeypatch):
    """The old gate was `elapsed_s % 300 == 0` — an exact-multiple test on a
    value sampled every ~1s, so it essentially never fired."""
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "")
    sent = []

    router = NotificationRouter()
    router._task_progress = None

    async def capture(task_id, message, *, kind, force):
        sent.append(message)

    router._send_outbox = capture

    # Not a multiple of 300 — the old gate would have dropped every one.
    for elapsed in (7, 11, 13):
        await router.on_phase_heartbeat({
            "task_id": "t", "phase_id": "p",
            "elapsed_s": elapsed, "remaining_s": 100,
        })

    assert len(sent) == 1, "first fires, the rest are throttled by interval"
    assert "7s elapsed" in sent[0]


@pytest.mark.asyncio
async def test_router_routes_progress_to_the_durable_outbox():
    """The primary transport is the backbone the daemons actually poll."""
    emitted = []

    class _TP:
        @staticmethod
        def emit(task_id, message, *, kind="progress", force=False):
            emitted.append((task_id, message, kind, force))
            return "tp_1"

    router = NotificationRouter()
    router._task_progress = _TP()

    await router.on_phase_failed({"task_id": "t", "phase_id": "p",
                                  "error": "boom"})

    assert emitted and emitted[0][0] == "t"
    assert "boom" in emitted[0][1]
    assert emitted[0][2] == "error"
    assert emitted[0][3] is True, "a failure must not be swallowed by throttling"
