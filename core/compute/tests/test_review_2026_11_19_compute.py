"""Compute findings of the 2026-11-19 review (A2A-2 .. A2A-5).

* A2A-2: ``stage_id`` / ``manager_id`` are path components and must be a safe
  single component, contained under the pipeline / HAC root.
* A2A-3: the PRODUCTION emitter (``cli._build_audit_emit``) writes
  ``compute.run_started`` and the pipeline / HAC events into the tenant chain.
* A2A-4: ``get_result(wait_s=...)`` for a pipeline job does not block the
  worker's event loop — status and abort stay answerable meanwhile.
* A2A-5: an abort stops a gate-less pipeline (and HAC run): no further tool
  executions; ``aborted`` is never overwritten; ``WorkerServer.stop()`` stops
  pipeline jobs too.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))
_FORGE = str(REPO_ROOT / "corvin_operator" / "forge")
if _FORGE not in sys.path:
    sys.path.append(_FORGE)

from corvin_compute import audit as compute_audit  # noqa: E402
from corvin_compute import cli as compute_cli  # noqa: E402
from corvin_compute.engine_protocol import ComputeSpec  # noqa: E402
from corvin_compute.hac.coordinator import HACCoordinator  # noqa: E402
from corvin_compute.hac.manifest import (  # noqa: E402
    HACManifest, HACStore, LossWeights, SubManagerSpec,
)
from corvin_compute.pipeline.coordinator import PipelineCoordinator  # noqa: E402
from corvin_compute.pipeline.engine import PipelineEngine  # noqa: E402
from corvin_compute.pipeline.manifest import (  # noqa: E402
    InvalidComponentId, PipelineManifest, PipelineStore, StageSpec,
)
from corvin_compute.worker import WorkerServer  # noqa: E402


# --------------------------------------------------------------------------- A2A-2

@pytest.mark.parametrize("bad", ["../x", "/etc/cron.d/x", "a/b", "", "..", "a" * 65, "x y", "s\x00"])
def test_stage_id_must_be_a_safe_path_component(bad):
    with pytest.raises(ValueError):
        StageSpec.from_dict({"stage_id": bad, "tool_name": "t"})


@pytest.mark.parametrize("bad", ["../x", "/abs/path", "m/1"])
def test_manager_id_must_be_a_safe_path_component(bad):
    with pytest.raises(ValueError):
        SubManagerSpec.from_dict({"manager_id": bad, "stages": []})


def test_valid_ids_are_accepted():
    assert StageSpec.from_dict({"stage_id": "fit_1-a", "tool_name": "t"}).stage_id == "fit_1-a"
    assert SubManagerSpec.from_dict({"manager_id": "m-1", "stages": []}).manager_id == "m-1"


def test_store_refuses_escaping_ids_even_when_the_spec_was_built_directly(tmp_path):
    store = PipelineStore(tmp_path, "_default", "pipeline_x")
    for bad in ("../x", "/abs", "a/../../b"):
        with pytest.raises(InvalidComponentId):
            store.stage_artifacts_dir(bad)
        with pytest.raises(InvalidComponentId):
            store.ensure_stage_dir(bad)
    hac = HACStore(tmp_path, "_default", "hac_x")
    with pytest.raises(InvalidComponentId):
        hac.manager_dir("../../escape")
    assert not (tmp_path / "tenants" / "_default" / "compute" / "escape").exists()
    good = store.ensure_stage_dir("ok")
    assert good.resolve().is_relative_to(store.root_dir.resolve())


def test_symlinked_stage_dir_cannot_leave_the_root(tmp_path):
    store = PipelineStore(tmp_path, "_default", "pipeline_x")
    (store.root_dir / "stages").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.root_dir / "stages" / "evil").symlink_to(outside, target_is_directory=True)
    with pytest.raises(InvalidComponentId):
        store.ensure_stage_dir("evil")


def test_add_stage_with_a_bad_id_is_refused_and_the_gate_stays_usable(tmp_path):
    async def go():
        q: asyncio.Queue = asyncio.Queue()
        m = PipelineManifest(pipeline_id="pipeline_t", tenant_id="_default",
                             stages=[StageSpec(stage_id="s1", tool_name="t", param_grid={"a": [1]}),
                                     StageSpec(stage_id="s2", tool_name="t", param_grid={"a": [1]})],
                             steering_gate=True, steering_gate_timeout_s=5,
                             budget={"max_iterations": 3})
        c = PipelineCoordinator(pipeline_id="pipeline_t", manifest=m, corvin_home=tmp_path,
                                runner_fn=lambda n, p: {"loss": 1.0}, audit_emit=None, gate_queue=q)
        q.put_nowait({"action_type": "add_stage",
                      "payload": {"stage": {"stage_id": "../../x", "tool_name": "t"}}})
        q.put_nowait({"action_type": "resume", "payload": {}})
        q.put_nowait({"action_type": "resume", "payload": {}})
        await asyncio.wait_for(c.run(), timeout=30)
        return c
    c = asyncio.run(go())
    r = c.get_result_dict()
    assert r["state"] == "converged", r
    assert sorted(r["stages"]) == ["s1", "s2"]


# --------------------------------------------------------------------------- A2A-3

def _chain_events(chain: Path) -> list[dict]:
    if not chain.exists():
        return []
    return [json.loads(line) for line in chain.read_text(encoding="utf-8").splitlines() if line.strip()]


def _event_name(rec: dict) -> str:
    return str(rec.get("event_type") or rec.get("event") or rec.get("type") or "")


def test_cli_chain_path_is_the_tenant_audit_chain(tmp_path, monkeypatch):
    from forge.paths import tenant_audit_chain
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    assert compute_cli._tenant_chain(tmp_path, "_default") == tenant_audit_chain("_default")
    # The flag wins over a differing ambient CORVIN_HOME, and the env is restored.
    other = tmp_path / "other"
    assert compute_cli._tenant_chain(other, "acme") == (
        other / "tenants" / "acme" / "global" / "forge" / "audit.jsonl")
    assert os.environ["CORVIN_HOME"] == str(tmp_path)


def test_production_emitter_writes_run_started_and_pipeline_events(tmp_path, monkeypatch):
    """The REAL cli emitter, the real audit.emit, the real chain writer."""
    home = Path(os.environ["CORVIN_HOME"])  # the conftest sandbox
    assert "/tmp" in str(home) or "pytest" in str(home), home
    emit = compute_cli._build_audit_emit(home, "_default")
    chain = compute_cli._tenant_chain(home, "_default")
    before = len(_chain_events(chain))

    m = PipelineManifest(pipeline_id="pipeline_audit", tenant_id="_default",
                         stages=[StageSpec(stage_id="s1", tool_name="t", param_grid={"a": [1, 2]}),
                                 StageSpec(stage_id="s2", tool_name="t", param_grid={"a": [1]})],
                         steering_gate=False, budget={"max_iterations": 4})
    c = PipelineCoordinator(pipeline_id="pipeline_audit", manifest=m, corvin_home=home,
                            runner_fn=lambda n, p: {"loss": float(p["a"])},
                            audit_emit=emit, gate_queue=asyncio.Queue())
    asyncio.run(c.run())
    assert c.get_result_dict()["state"] == "converged"

    new = _chain_events(chain)[before:]
    names = [_event_name(r) for r in new]
    for expected in ("compute.pipeline_started", "compute.run_started",
                     "compute.stage_completed", "compute.run_terminal",
                     "compute.pipeline_terminal"):
        assert expected in names, (expected, names)
    assert names.count("compute.run_started") == 2
    assert names.count("compute.stage_completed") == 2


def test_production_emitter_writes_hac_events(tmp_path):
    home = Path(os.environ["CORVIN_HOME"])
    emit = compute_cli._build_audit_emit(home, "_default")
    chain = compute_cli._tenant_chain(home, "_default")
    before = len(_chain_events(chain))
    sms = [SubManagerSpec.from_dict({"manager_id": mid, "strategy": "grid", "min_iterations": 1,
                                     "stages": [{"tool_name": mid, "param_grid": {"a": [1, 2]}}]})
           for mid in ("m1", "m2")]
    m = HACManifest(hac_id="hac_audit", tenant_id="_default", sub_managers=sms,
                    loss_weights=LossWeights.from_dict({"weights": {"m1": .5, "m2": .5}}),
                    budget={"max_iterations": 4}, backprop_gate=False, max_backprop_rounds=1)
    c = HACCoordinator(hac_id="hac_audit", manifest=m, corvin_home=home,
                       runner_fn=lambda n, p: {"loss": 1.0}, audit_emit=emit,
                       gate_queue=asyncio.Queue())
    asyncio.run(c.run())
    names = [_event_name(r) for r in _chain_events(chain)[before:]]
    for expected in ("compute.hac_started", "compute.hac_round_started",
                     "compute.hac_root_loss_computed", "compute.hac_terminal"):
        assert expected in names, (expected, names)


def test_every_event_the_coordinators_emit_is_in_the_allow_list():
    for ev in ("compute.pipeline_started", "compute.stage_completed",
               "compute.pipeline_gate_opened", "compute.pipeline_terminal",
               "compute.pipeline_tool_forged", "compute.hac_started",
               "compute.hac_round_started", "compute.hac_root_loss_computed",
               "compute.backprop_gate_opened", "compute.hac_budget_reallocated",
               "compute.hac_terminal"):
        assert ev in compute_audit._ALLOWED_FIELDS, ev
    assert "datasources" in compute_audit._ALLOWED_FIELDS["compute.run_started"]


def test_emitter_contract_is_event_plus_keywords(tmp_path):
    """A strict ``(event, **fields)`` double: a positional call would TypeError."""
    seen: list[tuple[str, dict]] = []

    def strict(event: str, **fields):
        seen.append((event, fields))

    m = PipelineManifest(pipeline_id="pipeline_k", tenant_id="_default",
                         stages=[StageSpec(stage_id="s1", tool_name="t", param_grid={"a": [1]})],
                         steering_gate=False, budget={"max_iterations": 2})
    c = PipelineCoordinator(pipeline_id="pipeline_k", manifest=m, corvin_home=tmp_path,
                            runner_fn=lambda n, p: {"loss": 1.0}, audit_emit=strict,
                            gate_queue=asyncio.Queue())
    asyncio.run(c.run())
    names = [e for e, _ in seen]
    assert "compute.pipeline_started" in names and "compute.pipeline_terminal" in names
    for event, fields in seen:
        compute_audit._check_allow_list(event, fields)  # raises on an unregistered field


# --------------------------------------------------------------------------- A2A-5

def _three_stage_pipeline(tmp_path, runner, emit=None):
    m = PipelineManifest(pipeline_id="pipeline_abort", tenant_id="_default",
                         stages=[StageSpec(stage_id=f"s{i}", tool_name="t",
                                           param_grid={"a": list(range(20))})
                                 for i in (1, 2, 3)],
                         steering_gate=False, budget={"max_iterations": 20})
    return PipelineCoordinator(pipeline_id="pipeline_abort", manifest=m, corvin_home=tmp_path,
                               runner_fn=runner, audit_emit=emit, gate_queue=asyncio.Queue())


def test_abort_stops_a_gateless_pipeline_without_further_tool_runs(tmp_path):
    calls: list[float] = []

    def slow(name, payload):
        calls.append(time.monotonic())
        time.sleep(0.05)
        return {"loss": 1.0}

    events: list[tuple[str, dict]] = []
    c = _three_stage_pipeline(tmp_path, slow, emit=lambda e, **f: events.append((e, f)))

    async def go():
        task = asyncio.create_task(c.run())
        await asyncio.sleep(0.3)
        c.request_abort()
        t_abort = time.monotonic()
        await asyncio.wait_for(task, timeout=10)
        return t_abort

    t_abort = asyncio.run(go())
    n_at_end = len(calls)
    time.sleep(0.4)
    assert len(calls) == n_at_end, "a tool ran after the pipeline returned"
    # At most the one iteration that was in flight when the abort arrived.
    assert sum(1 for t in calls if t > t_abort) <= 1, calls
    assert n_at_end < 20, "the first stage was not interrupted"
    assert c.get_result_dict()["state"] == "aborted"
    assert c._store.read_summary()["state"] == "aborted"
    terminals = [f for e, f in events if e == "compute.pipeline_terminal"]
    assert len(terminals) == 1 and terminals[0]["state"] == "aborted", terminals
    assert "s2" not in c.get_result_dict()["stages"]


def test_aborted_is_never_overwritten_by_failed(tmp_path):
    def crash_after_abort(name, payload):
        time.sleep(0.05)
        raise RuntimeError("boom")

    c = _three_stage_pipeline(tmp_path, crash_after_abort)

    async def go():
        task = asyncio.create_task(c.run())
        await asyncio.sleep(0.02)
        c.request_abort()
        await asyncio.wait_for(task, timeout=10)

    asyncio.run(go())
    assert c.get_status_dict()["state"] == "aborted"
    assert c._store.read_summary()["state"] == "aborted"


def test_abort_stops_a_hac_run(tmp_path):
    calls: list[float] = []

    def slow(name, payload):
        calls.append(time.monotonic())
        time.sleep(0.05)
        return {"loss": 1.0}

    sms = [SubManagerSpec.from_dict({"manager_id": mid, "strategy": "grid", "min_iterations": 1,
                                     "stages": [{"tool_name": mid, "param_grid": {"a": list(range(30))}}]})
           for mid in ("m1", "m2")]
    m = HACManifest(hac_id="hac_abort", tenant_id="_default", sub_managers=sms,
                    loss_weights=LossWeights.from_dict({"weights": {"m1": .5, "m2": .5}}),
                    budget={"max_iterations": 60}, backprop_gate=False, max_backprop_rounds=3)
    c = HACCoordinator(hac_id="hac_abort", manifest=m, corvin_home=tmp_path, runner_fn=slow,
                       audit_emit=None, gate_queue=asyncio.Queue())

    async def go():
        task = asyncio.create_task(c.run())
        await asyncio.sleep(0.3)
        c.request_abort()
        t = time.monotonic()
        await asyncio.wait_for(task, timeout=10)
        return t

    t_abort = asyncio.run(go())
    n = len(calls)
    time.sleep(0.3)
    assert len(calls) == n
    assert sum(1 for t in calls if t > t_abort) <= 2, "one in-flight iteration per sub-manager at most"
    assert c.get_result_dict()["state"] == "aborted"
    assert c._store.read_summary()["state"] == "aborted"


# --------------------------------------------------------------------------- A2A-4 + stop()

def _server(tmp_path, runner) -> tuple[WorkerServer, PipelineEngine]:
    eng = PipelineEngine(corvin_home=tmp_path, runner_fn=runner)
    srv = WorkerServer(tenant_id="_default", corvin_home=tmp_path,
                       socket_path=tmp_path / "w.sock", runner_fn=runner,
                       extra_engines=[eng])
    return srv, eng


def _submit_params():
    return {"engine": "pipeline", "tenant_id": "_default", "budget": {"max_iterations": 40},
            "extra": {"steering_gate": False,
                      "stages": [{"stage_id": f"s{i}", "tool_name": "t",
                                  "param_grid": {"a": list(range(40))}} for i in (1, 2, 3)]}}


def test_get_result_wait_does_not_block_status_or_abort(tmp_path):
    calls: list[float] = []

    def slow(name, payload):
        calls.append(time.monotonic())
        time.sleep(0.05)
        return {"loss": 1.0}

    async def go():
        srv, eng = _server(tmp_path, slow)
        eng.set_loop(asyncio.get_running_loop())
        sub = await srv._dispatch("submit_run", _submit_params())
        handle = sub["compute_handle"]
        waiter = asyncio.create_task(
            srv._dispatch("get_result", {"compute_handle": handle, "wait_s": 20.0}))
        await asyncio.sleep(0.2)
        assert not waiter.done()
        t0 = time.monotonic()
        st = await asyncio.wait_for(srv._dispatch("get_status", {"compute_handle": handle}), 2.0)
        ping = await asyncio.wait_for(srv._dispatch("ping", {}), 2.0)
        status_latency = time.monotonic() - t0
        ab = await asyncio.wait_for(srv._dispatch("abort_run", {"compute_handle": handle}), 2.0)
        res = await asyncio.wait_for(waiter, 10.0)
        return st, ping, status_latency, ab, res, time.monotonic() - t0

    st, ping, status_latency, ab, res, total = asyncio.run(go())
    assert st["state"] == "running" and ping["pong"] is True
    assert status_latency < 1.0, status_latency
    assert ab["state"] == "aborting"
    assert res["state"] == "aborted", res
    assert total < 8.0, "the waiter returned once the abort took effect, not after wait_s"
    n = len(calls)
    time.sleep(0.3)
    assert len(calls) == n


def test_worker_stop_interrupts_pipeline_jobs(tmp_path):
    calls: list[float] = []

    def slow(name, payload):
        calls.append(time.monotonic())
        time.sleep(0.05)
        return {"loss": 1.0}

    seen: list[tuple[str, dict]] = []

    async def go():
        srv, eng = _server(tmp_path, slow)
        srv.audit_emit = lambda e, **f: seen.append((e, f))
        eng.set_loop(asyncio.get_running_loop())
        sub = await srv._dispatch("submit_run", _submit_params())
        await asyncio.sleep(0.25)
        await srv.stop()
        t_stop = time.monotonic()
        for _ in range(100):
            if eng.status(sub["compute_handle"]).state == "aborted":
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)
        return eng, sub["compute_handle"], t_stop

    eng, handle, t_stop = asyncio.run(go())
    assert eng.status(handle).state == "aborted"
    assert sum(1 for t in calls if t > t_stop) <= 1, "tools kept running after stop()"
    assert len(calls) < 40
    term = [f for e, f in seen if e == "compute.worker_terminated"]
    assert term and term[0]["termination_reason"] == "shutdown_inflight_aborted"
    summary = PipelineStore(tmp_path, "_default", handle).read_summary()
    assert summary["state"] == "aborted" and summary["error"] == "worker-shutdown"


# --------------------------------------------------------------------------- R2-A2A-4
# stop() returns with every pipeline / HAC run TERMINAL on disk and in the
# audit stream, even though the caller tears the loop down right after it.

from corvin_compute.hac.engine import HACEngine  # noqa: E402


def _stop_right_after_submit(tmp_path, *, tool_s: float, drain_s: float, engine_cls, params):
    """Submit a job, call stop(), and let ``asyncio.run`` tear the loop down
    IMMEDIATELY afterwards - no polling, which is what a real shutdown does."""
    calls: list[float] = []

    def slow(name, payload):
        calls.append(time.monotonic())
        time.sleep(tool_s)
        return {"loss": 1.0}

    events: list[tuple[str, dict]] = []
    emit = lambda e, **f: events.append((e, f))  # noqa: E731

    async def go():
        eng = engine_cls(corvin_home=tmp_path, runner_fn=slow, audit_emit=emit)
        srv = WorkerServer(tenant_id="_default", corvin_home=tmp_path,
                           socket_path=tmp_path / "w.sock", runner_fn=slow,
                           extra_engines=[eng], audit_emit=emit)
        srv.stop_drain_timeout_s = drain_s
        eng.set_loop(asyncio.get_running_loop())
        sub = await srv._dispatch("submit_run", params)
        await asyncio.sleep(0.3)  # inside the first tool iteration
        t0 = time.monotonic()
        await srv.stop()
        return sub["compute_handle"], time.monotonic() - t0

    handle, stop_s = asyncio.run(go())
    return handle, stop_s, events, calls


def test_stop_waits_for_the_pipeline_terminal_record_with_a_1s_tool(tmp_path):
    handle, stop_s, events, _ = _stop_right_after_submit(
        tmp_path, tool_s=1.0, drain_s=10.0, engine_cls=PipelineEngine, params=_submit_params())
    terminals = [f for e, f in events if e == "compute.pipeline_terminal"]
    assert len(terminals) == 1, events
    assert terminals[0]["state"] == "aborted" and terminals[0]["reason"] == "worker-shutdown"
    summary = PipelineStore(tmp_path, "_default", handle).read_summary()
    assert summary.get("state") == "aborted", summary
    assert summary.get("error") == "worker-shutdown"
    # It waited for the in-flight iteration, not for the whole deadline.
    assert 0.3 < stop_s < 5.0, stop_s
    # Order: the terminal record precedes the worker's own termination record.
    names = [e for e, _ in events]
    assert names.index("compute.pipeline_terminal") < names.index("compute.worker_terminated")


def test_stop_writes_the_terminal_record_itself_when_the_deadline_passes(tmp_path):
    # The tool outlives the drain deadline: the record is written synchronously.
    handle, stop_s, events, _ = _stop_right_after_submit(
        tmp_path, tool_s=2.0, drain_s=0.2, engine_cls=PipelineEngine, params=_submit_params())
    assert stop_s < 1.5, stop_s
    terminals = [f for e, f in events if e == "compute.pipeline_terminal"]
    assert len(terminals) == 1 and terminals[0]["state"] == "aborted", events
    assert PipelineStore(tmp_path, "_default", handle).read_summary().get("state") == "aborted"
    time.sleep(2.2)  # the stage thread returns later: still exactly one record
    assert len([1 for e, _ in events if e == "compute.pipeline_terminal"]) == 1


def _hac_params(ids=("m1", "m2")):
    return {"engine": "hac", "tenant_id": "_default", "budget": {"max_iterations": 60},
            "extra": {"backprop_gate": False, "max_backprop_rounds": 3,
                      "sub_managers": [
                          {"manager_id": mid, "strategy": "grid", "min_iterations": 1,
                           "stages": [{"tool_name": mid, "param_grid": {"a": list(range(30))}}]}
                          for mid in ids]}}


@pytest.mark.parametrize("tool_s,drain_s", [(1.0, 10.0), (2.0, 0.2)])
def test_stop_leaves_a_hac_run_terminal(tmp_path, tool_s, drain_s):
    handle, _, events, _ = _stop_right_after_submit(
        tmp_path, tool_s=tool_s, drain_s=drain_s, engine_cls=HACEngine, params=_hac_params())
    terminals = [f for e, f in events if e == "compute.hac_terminal"]
    assert len(terminals) == 1, events
    assert terminals[0]["state"] == "aborted"
    assert terminals[0]["convergence_reason"] == "worker-shutdown"
    assert HACStore(tmp_path, "_default", handle).read_summary().get("state") == "aborted"
    time.sleep(tool_s + 0.3)
    assert len([1 for e, _ in events if e == "compute.hac_terminal"]) == 1


def test_a_cancelled_pipeline_coroutine_still_writes_its_terminal_record(tmp_path):
    # Loop teardown WITHOUT stop(): the task is cancelled at its to_thread await.
    def slow(name, payload):
        time.sleep(0.5)
        return {"loss": 1.0}

    events: list[tuple[str, dict]] = []
    c = _three_stage_pipeline(tmp_path, slow, emit=lambda e, **f: events.append((e, f)))

    async def go():
        task = asyncio.create_task(c.run())
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    terminals = [f for e, f in events if e == "compute.pipeline_terminal"]
    assert len(terminals) == 1 and terminals[0]["state"] == "aborted", events
    assert c._store.read_summary()["state"] == "aborted"


# --------------------------------------------------------------------------- R2-A2A-5

def test_duplicate_manager_ids_are_refused_at_submit(tmp_path):
    eng = HACEngine(corvin_home=tmp_path, runner_fn=lambda n, p: {"loss": 1.0})
    srv = WorkerServer(tenant_id="_default", corvin_home=tmp_path,
                       socket_path=tmp_path / "w.sock", extra_engines=[eng])

    async def go():
        eng.set_loop(asyncio.get_running_loop())
        # Through the worker's real submit handler, not the engine directly.
        with pytest.raises(ValueError, match="duplicate manager_id"):
            await srv._dispatch("submit_run", _hac_params(ids=("m1", "m1")))

    asyncio.run(go())
    assert eng._coordinators == {} and srv._runs == {}


def test_duplicate_manager_ids_are_refused_by_the_coordinator_too(tmp_path):
    sms = [SubManagerSpec.from_dict({"manager_id": "m1", "stages": []}) for _ in range(2)]
    m = HACManifest(hac_id="hac_dup", tenant_id="_default", sub_managers=sms,
                    loss_weights=LossWeights.from_dict({"weights": {"m1": 1.0}}), budget={})
    with pytest.raises(ValueError, match="duplicate manager_id"):
        HACCoordinator(hac_id="hac_dup", manifest=m, corvin_home=tmp_path,
                       runner_fn=lambda n, p: {}, audit_emit=None, gate_queue=asyncio.Queue())


def _gated_hac(tmp_path, runner):
    sms = [SubManagerSpec.from_dict({"manager_id": mid, "strategy": "grid", "min_iterations": 1,
                                     "stages": [{"tool_name": mid, "param_grid": {"a": list(range(30))}}]})
           for mid in ("m1", "m2")]
    m = HACManifest(hac_id="hac_gate", tenant_id="_default", sub_managers=sms,
                    loss_weights=LossWeights.from_dict({"weights": {"m1": .5, "m2": .5}}),
                    budget={"max_iterations": 60}, backprop_gate=True, max_backprop_rounds=3,
                    convergence_epsilon=-1.0)
    q: asyncio.Queue = asyncio.Queue()
    return HACCoordinator(hac_id="hac_gate", manifest=m, corvin_home=tmp_path, runner_fn=runner,
                          audit_emit=None, gate_queue=q), q


def test_gate_resume_with_a_repeated_manager_runs_it_once_and_abort_reaches_it(tmp_path):
    calls: list[tuple[str, float]] = []
    phase = {"slow": False}

    def runner(name, payload):
        calls.append((name, time.monotonic()))
        time.sleep(0.05 if phase["slow"] else 0.0)
        return {"loss": 1.0}

    c, q = _gated_hac(tmp_path, runner)
    live_max = {"n": 0}

    async def go():
        task = asyncio.create_task(c.run())
        for _ in range(400):  # round 0 runs fast, then the gate opens
            if c._state == "gate_open":
                break
            await asyncio.sleep(0.01)
        assert c._state == "gate_open"
        phase["slow"] = True
        mark = len(calls)
        q.put_nowait({"action_type": "resume",
                      "payload": {"re_run": ["m1", "m1", "nope", 7, "m1"]}})
        await asyncio.sleep(0.3)
        with c._run_lock:
            live_max["n"] = len(c._active_runs)
        c.request_abort()
        t_abort = time.monotonic()
        await asyncio.wait_for(task, timeout=10)
        return mark, t_abort

    mark, t_abort = asyncio.run(go())
    n = len(calls)
    time.sleep(0.4)
    assert len(calls) == n, "a tool ran after the HAC run returned"
    rerun = calls[mark:]
    assert rerun and {name for name, _ in rerun} == {"m1"}, rerun
    assert live_max["n"] == 1, "one ComputeRun for m1, not one per repetition"
    assert sum(1 for _, t in rerun if t > t_abort) <= 1, "abort did not reach every run"
    assert len(rerun) < 30
    assert c.get_result_dict()["state"] == "aborted"


def test_known_manager_ids_filters_and_dedupes(tmp_path):
    c, _ = _gated_hac(tmp_path, lambda n, p: {"loss": 1.0})
    assert c._known_manager_ids(["m2", "m1", "m2", "x", None, 3]) == ["m2", "m1"]
    assert c._known_manager_ids("m1") == []
    assert c._known_manager_ids(None) == []


def test_active_runs_are_keyed_by_run_id(tmp_path):
    # Two concurrent runs can never share a key, so abort reaches both.
    seen: list[list[str]] = []

    def runner(name, payload):
        time.sleep(0.05)
        return {"loss": 1.0}

    c, _ = _gated_hac(tmp_path, runner)

    async def go():
        task = asyncio.create_task(c.run())
        await asyncio.sleep(0.2)
        with c._run_lock:
            seen.append(list(c._active_runs))
        c.request_abort()
        await asyncio.wait_for(task, timeout=10)

    asyncio.run(go())
    assert len(seen[0]) == 2 and not ({"m1", "m2"} & set(seen[0])), seen


# --------------------------------------------------------------------------- stage refs

@pytest.mark.parametrize("ref", [
    "$s1.artifacts/../../../../etc/passwd", "$s1.artifacts//etc/passwd",
    "$s1.artifacts/a/../../x", "$s1.artifacts/",
])
def test_an_artifact_ref_cannot_leave_the_stage_artifact_dir(tmp_path, ref):
    c = _three_stage_pipeline(tmp_path, lambda n, p: {"loss": 1.0})
    c._completed["s1"] = {"best_loss": 1.0}
    with pytest.raises(InvalidComponentId):
        c._resolve_one_ref(ref)


def test_a_normal_artifact_ref_resolves_inside_the_stage_dir(tmp_path):
    c = _three_stage_pipeline(tmp_path, lambda n, p: {"loss": 1.0})
    c._completed["s1"] = {"best_loss": 1.0}
    base = c._store.stage_artifacts_dir("s1")
    assert c._resolve_one_ref("$s1.artifacts/out.csv") == str(base / "out.csv")
    assert c._resolve_one_ref("$s1.artifacts/sub/out.csv") == str(base / "sub" / "out.csv")


def test_an_escaping_ref_fails_the_pipeline_with_a_terminal_record(tmp_path):
    events: list[tuple[str, dict]] = []
    m = PipelineManifest(
        pipeline_id="pipeline_ref", tenant_id="_default", steering_gate=False,
        budget={"max_iterations": 2},
        stages=[StageSpec(stage_id="s1", tool_name="t", param_grid={"a": [1]}),
                StageSpec(stage_id="s2", tool_name="t", param_grid={"a": [1]},
                          inputs={"f": "$s1.artifacts/../../../x"})])
    ran: list[dict] = []
    c = PipelineCoordinator(pipeline_id="pipeline_ref", manifest=m, corvin_home=tmp_path,
                            runner_fn=lambda n, p: (ran.append(p), {"loss": 1.0})[1],
                            audit_emit=lambda e, **f: events.append((e, f)),
                            gate_queue=asyncio.Queue())
    asyncio.run(c.run())
    terminals = [f for e, f in events if e == "compute.pipeline_terminal"]
    assert len(terminals) == 1 and terminals[0]["state"] == "failed", events
    assert terminals[0]["failed_stage_id"] == "s2"
    assert len(ran) == 1, "the second stage's tool must not have been called"
