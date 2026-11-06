"""Worker start / stop semantics around interrupted runs (adversarial review 2026-11-06).

* Recovery used to run every interrupted run to completion BEFORE the socket opened, so a restart
  left the worker unreachable for the whole remaining runtime (a hanging tool: forever) and the
  recovered runs could be neither aborted nor interrupted.
* SIGTERM cancels the serving task; ``asyncio.run`` then cancels the run tasks while they await
  their threads: the ``finally`` saw state "running" and sent a false "finished" notice and started
  queued runs mid-shutdown.
* Two workers on one home recovered the same runs and unlinked each other's socket.
Stub runner (no forge, no bwrap): the subject is the worker, not the tool.
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _real_worker import real_worker  # noqa: E402
from corvin_compute import strategies as strat_pkg  # noqa: E402
from corvin_compute.budget import Budget  # noqa: E402
from corvin_compute.client import WorkerClient  # noqa: E402
from corvin_compute.driver import ComputeRun, ComputeRunSpec  # noqa: E402
from corvin_compute.endpoint import endpoint_exists  # noqa: E402
from corvin_compute.state import RunStore, new_run_id  # noqa: E402
from corvin_compute.worker import WorkerServer  # noqa: E402

N = 300


def _slow(tool_name, payload):
    time.sleep(0.04)
    return {"loss": (float(payload["x"]) - 2) ** 2}


def _very_slow(tool_name, payload):
    """An iteration that cannot finish before ``asyncio.run`` cancels the run task (else the
    old behaviour would hide behind a lucky race)."""
    time.sleep(0.5)
    return {"loss": (float(payload["x"]) - 2) ** 2}


@pytest.fixture
def home():
    d = Path(tempfile.mkdtemp(prefix="cw-"))
    (d / "tenants" / "_default" / "compute" / "runs").mkdir(parents=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _seed_interrupted(home: Path, done: int = 2, n: int = N) -> str:
    """A run stopped after ``done`` points and put back to ``running`` with its full budget."""
    rid = new_run_id()
    spec = ComputeRunSpec(tenant_id="_default", tool_name="t", param_grid={"x": list(range(n))},
                          loss_metric="loss", strategy_name="grid",
                          budget=Budget.from_dict({"max_iterations": done, "max_wall_clock_s": 600}))
    ComputeRun(spec, corvin_home=home, runner_fn=_slow, strategy_factory=strat_pkg.load_strategy,
               run_id=rid).run()
    store = RunStore(home, "_default")
    s = store.read_summary(rid)
    s["state"] = "running"
    store.write_summary(rid, s)
    m = store.read_manifest(rid)
    m["budget"]["max_iterations"] = n
    store.write_manifest(rid, m)
    return rid


def test_recovery_does_not_hold_the_socket_and_the_recovered_run_can_be_aborted(home):
    rid = _seed_interrupted(home)
    store = RunStore(home, "_default")
    iters = store.root / rid / "iterations"
    before = {p.name: p.read_bytes() for p in iters.glob("*.json")}
    t0 = time.monotonic()
    with real_worker(home, runner_fn=_slow) as (client, _server):
        assert time.monotonic() - t0 < 6, "the socket must open without waiting for the recovered run"
        n_at_ready = len(list(iters.glob("*.json")))
        assert store.read_summary(rid)["state"] == "running" and n_at_ready < N, \
            "the recovered run must still be running when the worker answers"
        r = client._call("abort_run", {"compute_handle": rid})      # a recovered run IS a run
        assert r.get("state", r.get("status")) is not None or r, r
        for _ in range(100):
            if store.read_summary(rid)["state"] not in ("running", "queued"):
                break
            time.sleep(0.1)
    summary = store.read_summary(rid)
    assert summary["state"] == "aborted", summary
    after = {p.name: p.read_bytes() for p in iters.glob("*.json")}
    assert 2 <= len(after) < N
    for name, raw in before.items():
        assert after[name] == raw


def test_recovered_runs_honour_max_concurrent_runs(home):
    a, b = _seed_interrupted(home, n=N), _seed_interrupted(home, n=N)
    store = RunStore(home, "_default")
    with real_worker(home, runner_fn=_slow, max_concurrent_runs=1) as (client, server):
        time.sleep(0.8)
        states = sorted(server._runs[r].state for r in (a, b))
        assert states == ["queued", "running"], states
        for r in (a, b):
            client._call("abort_run", {"compute_handle": r})
        time.sleep(0.5)


def test_sigterm_style_cancellation_is_an_interruption_not_a_finish(home):
    """The CLI turns SIGTERM into ``task.cancel()`` on the serving task; ``asyncio.run`` then
    cancels the run tasks. No completion notice, no queued run started, run stays resumable."""
    sock = home / "tenants" / "_default" / "compute" / "w.sock"
    server = WorkerServer(tenant_id="_default", corvin_home=home, socket_path=sock,
                          max_concurrent_runs=1, runner_fn=_very_slow)
    notified: list = []
    server._notify_compute_done = lambda rid, st: notified.append((rid, st))
    ids: list = []

    async def cli_like():
        try:
            await server.serve_forever()
        finally:
            await server.stop()

    async def main():
        t = asyncio.create_task(cli_like())
        while not endpoint_exists(sock):
            await asyncio.sleep(0.02)
        client = WorkerClient(sock, timeout_s=30)
        for _ in range(2):          # the second one queues (max_concurrent_runs=1)
            r = await asyncio.to_thread(
                client.submit_run, tool_name="t", param_grid={"x": list(range(N))}, loss_metric="loss",
                strategy="grid", notify=None, budget={"max_iterations": N, "max_wall_clock_s": 600})
            ids.append(r["compute_handle"])
        await asyncio.sleep(0.9)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    asyncio.run(main())
    store = RunStore(home, "_default")
    assert notified == [], f"a shutdown is not a finish: {notified}"
    s1 = store.read_summary(ids[0])
    assert s1["state"] == "running" and 0 < s1["total_iterations"] < 10, s1
    assert server._runs[ids[1]].state == "queued", "no queued run may start during shutdown"
    assert not (store.root / ids[1] / "iterations").exists()


def test_a_second_worker_on_the_same_home_is_refused(home):
    sock = home / "tenants" / "_default" / "compute" / "worker.sock"
    with real_worker(home, runner_fn=_slow):
        second = WorkerServer(tenant_id="_default", corvin_home=home, socket_path=sock, runner_fn=_slow)
        with pytest.raises(RuntimeError, match="already serves"):
            asyncio.run(second.serve_forever())
    # the lock is released with the first worker
    with real_worker(home, runner_fn=_slow) as (client, _server):
        assert client._call("ping", {})["pong"] is True
