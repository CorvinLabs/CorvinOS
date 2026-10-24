"""One Forge under all agentic-compute runtimes: a tool the ACS manager forges is then optimised by Bayesian
search, swept by a grid and driven by HAC - all through the REAL Forge runner (bwrap sandbox), no stubbed runner.

Chain under test:
  ACSRuntime.run() -> manager ``forge`` request (publish: true) -> RunForge: static check, sandbox self-test,
  registration in the tenant's user-scope Forge  ->  corvin_compute.cli._build_runner_fn (the worker's real runner)
  -> ComputeRun(bayesian | grid) and HACEngine/HACCoordinator, every iteration a sandboxed Forge call.

Independent ground truth: the tool computes (x-0.7)^2 + (y-0.3)^2, so the optimum is known without the run.
Only the two ACS LLM calls are scripted (the live LLM variant is acs-forge-live-e2e in the playground repo).
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
for _p in (_HERE, _REPO, _REPO / "core" / "compute", _REPO / "corvin_operator" / "forge",
           _REPO / "corvin_operator" / "skill-forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="needs bwrap")

from test_acs_runtime_forge import Harness, _decision_delegate, _spec, COMPLETE, _sandboxed_member  # noqa: E402,F401

QUAD_IMPL = (
    "import json, sys\n"
    "d = json.load(sys.stdin)\n"
    "x = float(d.get('x', 0)); y = float(d.get('y', 0))\n"
    "print(json.dumps({'loss': (x - 0.7) ** 2 + (y - 0.3) ** 2}))\n"
)
QUAD_TOOL = {
    "name": "dynamic.quad_loss",
    "description": "Loss (x-0.7)^2 + (y-0.3)^2 for the parameter search",
    "input_schema": {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}}},
    "impl": QUAD_IMPL,
    "test_input": {"x": 0.7, "y": 0.3},
    "expect": {"loss": 0.0},
}
PUBLISH = {"dynamic_tools": {"enabled": True, "allowed_namespaces": ["dynamic"], "publish": True}}


@pytest.fixture
def forged(tmp_path):
    """Run a real ACSRuntime.run in which the manager forges + publishes the tool; return the harness."""
    h = Harness(tmp_path)
    result = h.run([_decision_delegate(forge={"tools": [QUAD_TOOL]}), COMPLETE], _spec(**PUBLISH))
    assert result.status == "success", result.error
    return h


@pytest.fixture
def runner(forged):
    from corvin_compute import cli
    r = cli._build_runner_fn(tenant_id="_default")
    assert r is not None, "the real Forge runner could not be built"
    return r


def _user_registry():
    from forge.registry import Registry
    from forge.scope import scope_root
    return Registry(scope_root("user", tenant_id="_default"))


def _spec_run(strategy, iters, tool="dynamic.quad_loss", seed=3):
    from corvin_compute.budget import Budget
    from corvin_compute.driver import ComputeRunSpec
    return ComputeRunSpec(
        tenant_id="_default", tool_name=tool,
        param_grid={"x": {"type": "float_uniform", "low": 0, "high": 1},
                    "y": {"type": "float_uniform", "low": 0, "high": 1}} if strategy != "grid"
        else {"x": [0.0, 0.35, 0.7, 1.0], "y": [0.0, 0.3, 0.6, 1.0]},
        loss_metric="loss", strategy_name=strategy,
        budget=Budget(max_iterations=iters, max_wall_clock_s=120, convergence_eps=1e-9, stall_after_n=iters),
        minimise=True, seed=seed,
    )


def _run(spec, runner, tmp_path):
    from corvin_compute.driver import ComputeRun
    from corvin_compute import strategies as strat_pkg
    events = []
    run = ComputeRun(spec, corvin_home=tmp_path / "compute-home", runner_fn=runner,
                     strategy_factory=strat_pkg.load_strategy,
                     audit_emit=lambda e, **d: events.append(e))
    return run.run(), events


# ---------------------------------------------------------------------------------------------------- publish
def test_the_forged_tool_is_published_to_the_tenant_forge_and_audited(forged):
    pub = forged.events("acs.tool_published")
    assert len(pub) == 1 and pub[0]["name"] == "dynamic.quad_loss", forged.audit
    spec = _user_registry().get("dynamic.quad_loss")
    assert spec is not None and spec.scope == "user" and spec.meta.get("origin") == "acs"
    assert "def " not in json.dumps(forged.events("acs.tool_published"))      # no source in the audit chain


def test_publish_never_overwrites_an_existing_user_tool(tmp_path):
    from forge.registry import Registry
    from forge.scope import scope_root
    h = Harness(tmp_path)
    # a user tool of that name (different case) exists BEFORE the run
    root = scope_root("user", tenant_id="_default")
    root.mkdir(parents=True, exist_ok=True)
    Registry(root).create(name="dynamic.Quad_Loss", description="mine", input_schema={"type": "object"},
                          impl="print('{}')\n", scope="user", tenant_id="_default")
    h.run([_decision_delegate(forge={"tools": [QUAD_TOOL]}), COMPLETE], _spec(**PUBLISH))
    assert h.events("acs.tool_published") == []
    assert [e["reason"] for e in h.events("acs.tool_publish_rejected")] == ["duplicate"]
    assert Registry(root).get("dynamic.Quad_Loss").description == "mine"
    assert Registry(root).get("dynamic.quad_loss") is None
    assert len(h.events("acs.tool_forged")) == 1                              # still usable for the run itself


def test_without_publish_the_other_runtimes_do_not_see_the_tool(tmp_path):
    h = Harness(tmp_path)
    h.run([_decision_delegate(forge={"tools": [QUAD_TOOL]}), COMPLETE],
          _spec(dynamic_tools={"enabled": True, "allowed_namespaces": ["dynamic"]}))
    assert len(h.events("acs.tool_forged")) == 1
    assert _user_registry().get("dynamic.quad_loss") is None


# ------------------------------------------------------------------------------------- the runtimes, real Forge
def test_bayesian_optimisation_finds_the_optimum_of_the_acs_forged_tool(runner, tmp_path):
    pytest.importorskip("sklearn")
    rec, events = _run(_spec_run("bayesian", 14), runner, tmp_path)
    assert rec.state in ("converged", "completed", "budget_exhausted", "done", "succeeded") or rec.total_iterations >= 10, rec
    assert rec.total_iterations >= 10
    assert rec.best_loss is not None and rec.best_loss < 0.03, rec          # random-uniform median would be ~0.17
    assert _user_registry().get("dynamic.quad_loss").call_count >= rec.total_iterations + 1   # + the ACS self-test... in the published registry


def test_grid_sweep_hits_the_exact_optimum_through_the_forge(runner, tmp_path):
    rec, _ = _run(_spec_run("grid", 16), runner, tmp_path)
    assert rec.total_iterations == 16
    assert rec.best_loss is not None and rec.best_loss < 1e-9, rec            # (0.7, 0.3) is on the grid


def test_hac_drives_two_sub_managers_over_the_forged_tool(runner, forged, tmp_path):
    from corvin_compute.engine_protocol import ComputeSpec
    from corvin_compute.hac.engine import HACEngine

    events = []
    engine = HACEngine(corvin_home=tmp_path / "hac-home", runner_fn=runner,
                       audit_emit=lambda e, sev, d=None, **k: events.append((e, d)))
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    engine.set_loop(loop)
    try:
        stage = {"stage_id": "s1", "tool_name": "dynamic.quad_loss",
                 "param_grid": {"x": [0.0, 0.35, 0.7, 1.0], "y": [0.0, 0.3, 0.6, 1.0]}}
        job = engine.submit(ComputeSpec(
            engine="hac", tenant_id="_default", budget={"max_iterations": 40, "max_wall_clock_s": 120},
            extra={"sub_managers": [
                {"manager_id": "m1", "stages": [stage], "budget_fraction": 0.4, "min_iterations": 16, "strategy": "grid"},
                {"manager_id": "m2", "stages": [stage], "budget_fraction": 0.4, "min_iterations": 16, "strategy": "grid"}],
                "backprop_gate": False, "max_backprop_rounds": 1}))
        res = engine.result(job, wait_s=150)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=5)
    assert res.state in ("converged", "budget"), res
    managers = res.result.get("managers") or {}
    assert set(managers) == {"m1", "m2"}, res.result
    # every sub-manager ran 16 sandboxed Forge calls and found the grid optimum (0.7, 0.3)
    assert all(m["best_loss"] is not None and m["best_loss"] < 1e-9 for m in managers.values()), res.result
    assert all(abs(m["best_params"]["x"] - 0.7) < 1e-9 and abs(m["best_params"]["y"] - 0.3) < 1e-9
               for m in managers.values()), res.result
    assert res.result["final_root_loss"] is not None and res.result["final_root_loss"] < 1e-9
    assert any(e == "compute.hac_terminal" for e, _ in events)
    # the calls really went through the Forge registry the ACS run published into (2 managers x 16, + self-tests)
    assert _user_registry().get("dynamic.quad_loss").call_count >= 32


def test_pipeline_runs_two_stages_over_the_forged_tool(runner, forged, tmp_path):
    from corvin_compute.engine_protocol import ComputeSpec
    from corvin_compute.pipeline.engine import PipelineEngine

    engine = PipelineEngine(corvin_home=tmp_path / "pipe-home", runner_fn=runner, audit_emit=lambda *a, **k: None)
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    engine.set_loop(loop)
    grid = {"x": [0.0, 0.35, 0.7, 1.0], "y": [0.0, 0.3, 0.6, 1.0]}
    try:
        job = engine.submit(ComputeSpec(
            engine="pipeline", tenant_id="_default", budget={},
            extra={"steering_gate": False, "stages": [
                {"stage_id": "coarse", "tool_name": "dynamic.quad_loss", "strategy": "grid", "param_grid": grid,
                 "budget": {"max_iterations": 16, "max_wall_clock_s": 60}},
                {"stage_id": "fine", "tool_name": "dynamic.quad_loss", "strategy": "grid", "param_grid": grid,
                 "budget": {"max_iterations": 16, "max_wall_clock_s": 60}}]}))
        res = engine.result(job, wait_s=120)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=5)
    blob = json.dumps(res.result, default=str)
    assert res.state == "converged", res
    assert "coarse" in blob and "fine" in blob, blob[:600]
    assert _user_registry().get("dynamic.quad_loss").call_count >= 32


SHIFTED_TOOL = dict(QUAD_TOOL, name="dynamic.shifted_loss", test_input={"x": 0.2, "y": 0.9}, expect={"loss": 0.0},
                    impl=QUAD_IMPL.replace("0.7", "0.2").replace("0.3", "0.9"),
                    description="Loss (x-0.2)^2 + (y-0.9)^2, optimum elsewhere than dynamic.quad_loss")


def test_a_tool_forged_by_acs_in_the_middle_of_a_pipeline_is_used_by_the_next_stage(runner, forged, tmp_path):
    """Pipeline stage 1 runs on tool A; at the steering gate a second ACS run forges tool B, the gate notes it
    (``forge_noted``) and inserts a stage on B (``add_stage``). The inserted stage must find B's optimum."""
    import time
    from corvin_compute.engine_protocol import ComputeSpec, GateAction
    from corvin_compute.pipeline.engine import PipelineEngine

    events = []
    engine = PipelineEngine(corvin_home=tmp_path / "pipe-home", runner_fn=runner,
                            audit_emit=lambda e, sev=None, d=None, **k: events.append(e))
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    engine.set_loop(loop)
    grid_a = {"x": [0.0, 0.35, 0.7, 1.0], "y": [0.0, 0.3, 0.6, 1.0]}
    grid_b = {"x": [0.2, 0.5], "y": [0.9, 0.1]}
    try:
        job = engine.submit(ComputeSpec(
            engine="pipeline", tenant_id="_default", budget={},
            extra={"steering_gate": True, "steering_gate_timeout_s": 120, "stages": [
                {"stage_id": "first", "tool_name": "dynamic.quad_loss", "strategy": "grid", "param_grid": grid_a,
                 "budget": {"max_iterations": 16, "max_wall_clock_s": 60}},
                {"stage_id": "last", "tool_name": "dynamic.quad_loss", "strategy": "grid", "param_grid": grid_a,
                 "budget": {"max_iterations": 16, "max_wall_clock_s": 60}}]}))
        deadline = time.monotonic() + 60
        while engine.status(job).state != "gate_open":
            assert time.monotonic() < deadline, engine.status(job)
            time.sleep(0.1)

        # the pipeline is parked at its gate; NOW the ACS manager forges and publishes tool B
        h2 = Harness(tmp_path / "second-acs")
        (tmp_path / "second-acs").mkdir()
        h2.run([_decision_delegate(forge={"tools": [SHIFTED_TOOL]}), COMPLETE], _spec(**PUBLISH))
        assert len(h2.events("acs.tool_published")) == 1, h2.audit

        engine.gate_action(job, GateAction("forge_noted", {"tool_name": "dynamic.shifted_loss", "forge_run_id": "forge-e2e"}))
        engine.gate_action(job, GateAction("add_stage", {"stage": {
            "stage_id": "added", "tool_name": "dynamic.shifted_loss", "strategy": "grid", "param_grid": grid_b,
            "budget": {"max_iterations": 4, "max_wall_clock_s": 60}}}))
        engine.gate_action(job, GateAction("resume", {}))     # leaves gate 0 -> runs "added"
        engine.gate_action(job, GateAction("resume", {}))     # leaves gate 1 (between "added" and "last")
        res = engine.result(job, wait_s=120)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=5)
    assert res.state == "converged", res
    assert set(res.result["stages"]) == {"first", "added", "last"}, res.result
    assert res.result["forged_tools"] == [{"gate_index": 0, "tool_name": "dynamic.shifted_loss"}], res.result
    added = res.result["stages"]["added"]
    assert added["best_loss"] is not None and added["best_loss"] < 1e-9, added   # optimum (0.2, 0.9) of the NEW tool
    assert _user_registry().get("dynamic.shifted_loss").call_count >= 5
    assert "compute.pipeline_tool_forged" in events


def test_negative_control_without_publish_the_same_optimisation_cannot_run(tmp_path):
    """Proves the positive tests are not vacuous: the runner only knows the tenant Forge, so an unpublished
    (run-local) tool is unknown to it and every iteration fails."""
    h = Harness(tmp_path)
    h.run([_decision_delegate(forge={"tools": [QUAD_TOOL]}), COMPLETE],
          _spec(dynamic_tools={"enabled": True, "allowed_namespaces": ["dynamic"]}))
    from corvin_compute import cli
    runner = cli._build_runner_fn(tenant_id="_default")
    rec, _ = _run(_spec_run("grid", 16), runner, tmp_path)
    assert rec.best_loss is None or rec.best_loss > 1e-9 or rec.state in ("failed", "error"), rec
