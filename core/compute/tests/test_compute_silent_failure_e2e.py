"""A compute run that produced nothing must say so — over the real worker socket.

Measured 2026-11-06 on a live install: a tool that existed only in the caller's
session scope, or a param_grid whose keys did not match the tool's input_schema,
made EVERY iteration fail, and the run still ended ``budget_exhausted`` with
``best_loss: null`` and ``error: null`` — indistinguishable from a finished sweep.
A wrong ``loss_metric`` did the same without even an error on the iteration.
A 10x10 grid was also cut to 16 points by the stall heuristic and reported its
best as the grid's best.

Chain under test: WorkerClient -> Unix socket -> WorkerServer._op_submit_run
(preflight) -> ComputeRun driver -> corvin_compute.cli._build_runner_fn (the
worker's REAL runner: a forge Registry at the tenant's user scope, every
iteration a bwrap-sandboxed forge run) -> run artefacts on disk -> get_result.
Only the forge licence gate on registry.create is bypassed to seed the tool:
licensing is not the subject here.
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import threading
from pathlib import Path

import pytest

COMPUTE_ROOT = Path(__file__).resolve().parents[1]
FORGE_ROOT = Path(__file__).resolve().parents[3] / "corvin_operator" / "forge"
for _p in (COMPUTE_ROOT, FORGE_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bwrap") is None, reason="needs bwrap")

from corvin_compute.client import WorkerClient, WorkerClientError  # noqa: E402
from corvin_compute.endpoint import endpoint_exists  # noqa: E402
from corvin_compute.worker import WorkerServer  # noqa: E402

TENANT = "_default"
QUAD = ("import json, sys\n"
        "d = json.load(sys.stdin)\n"
        "x = float(d['x']); y = float(d.get('y', 0))\n"
        "print(json.dumps({'loss': (x - 2) ** 2 + (y - 1) ** 2}))\n")


state_holder: dict = {}


@pytest.fixture
def worker(tmp_path, monkeypatch):
    home = tmp_path / "corvin"
    monkeypatch.setenv("CORVIN_HOME", str(home))
    (home / "tenants" / TENANT / "compute" / "runs").mkdir(parents=True)

    import forge.registry as freg
    from forge.scope import scope_root
    monkeypatch.setattr(freg, "_require_forge_create", lambda *a, **k: None)
    user_root = scope_root("user", tenant_id=TENANT)
    user_root.mkdir(parents=True, exist_ok=True)
    freg.Registry(user_root).create(
        name="probe.quad", description="(x-2)^2 + (y-1)^2",
        input_schema={"type": "object", "properties": {"x": {"type": "number"},
                                                       "y": {"type": "number"}},
                      "required": ["x"]},
        impl=QUAD, scope="user", tenant_id=TENANT)

    from corvin_compute import cli
    runner = cli._build_runner_fn(tenant_id=TENANT, corvin_home_path=home)
    state_holder["runner"] = runner

    # AF_UNIX paths are capped at ~108 bytes; pytest's tmp_path is longer.
    import tempfile
    sock_dir = Path(tempfile.mkdtemp(prefix="cw-"))
    sock = sock_dir / "w.sock"
    state: dict = {}
    ready = threading.Event()

    def _serve():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        server = WorkerServer(tenant_id=TENANT, corvin_home=home, socket_path=sock,
                              max_concurrent_runs=2, runner_fn=runner)
        state.update(loop=loop, server=server)

        async def _main():
            t = asyncio.create_task(server.serve_forever())
            while not endpoint_exists(sock):
                await asyncio.sleep(0.01)
            ready.set()
            await t
        try:
            loop.run_until_complete(_main())
        except Exception:  # noqa: BLE001
            pass
        finally:
            loop.close()

    th = threading.Thread(target=_serve, daemon=True)
    th.start()
    assert ready.wait(10), "worker did not start"
    try:
        yield WorkerClient(sock, timeout_s=60)
    finally:
        try:
            asyncio.run_coroutine_threadsafe(state["server"].stop(), state["loop"]).result(10)
        except Exception:  # noqa: BLE001
            pass
        th.join(10)
        shutil.rmtree(sock_dir, ignore_errors=True)


def _submit(client, **kw):
    kw.setdefault("strategy", "grid")
    kw.setdefault("loss_metric", "loss")
    kw.setdefault("budget", {"max_iterations": 20, "max_wall_clock_s": 120})
    return client.submit_run(notify=None, **kw)


def _result(client, handle):
    for _ in range(20):
        res = client.get_result(handle, wait_s=10)
        if res["state"] not in ("running", "queued"):
            return res
    raise AssertionError("run did not finish")


def test_a_tool_outside_the_user_scope_is_refused_before_the_run_starts(worker):
    with pytest.raises(WorkerClientError) as exc:
        _submit(worker, tool_name="probe.session_only", param_grid={"x": [1, 2]})
    assert exc.value.error_class == "ToolNotAvailable"
    assert "forge_promote" in exc.value.message


def test_param_grid_keys_that_are_not_tool_inputs_are_refused(worker):
    with pytest.raises(WorkerClientError) as exc:
        _submit(worker, tool_name="probe.quad", param_grid={"z": [1, 2]})
    assert exc.value.error_class == "ParamGridMismatch"
    assert "['x']" in exc.value.message and "['z']" in exc.value.message


def test_every_pipeline_stage_is_preflighted(worker, monkeypatch):
    # The engine itself is not needed: the refusal must come before submission.
    with pytest.raises(WorkerClientError) as exc:
        worker._call("submit_run", {
            "engine": "pipeline", "budget": {"max_iterations": 4},
            "extra": {"steering_gate": False, "stages": [
                {"stage_id": "a", "tool_name": "probe.quad", "param_grid": {"x": [1]}},
                {"stage_id": "b", "tool_name": "probe.missing", "param_grid": {"x": [1]}}]}})
    assert exc.value.error_class == "ToolNotAvailable"
    assert "probe.missing" in exc.value.message


def test_a_run_without_one_valid_loss_ends_failed_and_names_the_cause(worker):
    h = _submit(worker, tool_name="probe.quad", param_grid={"x": [0, 1, 2, 3, 4, 5]},
                loss_metric="mape")["compute_handle"]
    res = _result(worker, h)
    assert res["state"] == "failed", res
    assert res["best_loss"] is None
    assert "LossMetricMissing" in (res["error"] or ""), res
    assert res["total_iterations"] == 3, "identical deterministic failures stop after 3"


def test_a_grid_is_not_cut_short_by_the_stall_heuristic(worker):
    grid = [0, 1, 2, 3]
    h = _submit(worker, tool_name="probe.quad", param_grid={"x": grid, "y": grid},
                budget={"max_iterations": 50, "max_wall_clock_s": 120,
                        "stall_after_n": 2, "convergence_eps": 0.5})["compute_handle"]
    res = _result(worker, h)
    assert res["total_iterations"] == 16, res
    assert res["best_loss"] == 0.0 and res["best_params"] == {"x": 2, "y": 1}
    assert res["state"] != "failed" and res["error"] is None


def test_the_real_runner_exposes_its_preflight(worker):
    assert hasattr(state_holder["runner"], "preflight")
