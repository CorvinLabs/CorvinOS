"""Pipeline and HAC must not report success for stages / sub-managers that produced nothing.

Adversarial review 2026-11-06: a pipeline whose stage failed every iteration ended
``converged``; a HAC run whose sub-managers all failed ended ``converged`` with an
invented root loss of 1.0 (compute_root_loss returned 1.0 for no losses), and a
partially failed HAC folded only the survivors into its composite. Stage ``inputs``
reached the grid as a scalar and made every such pipeline fail.
"""
from __future__ import annotations

import asyncio
import math
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from corvin_compute.driver import _resolve_loss  # noqa: E402
from corvin_compute.hac.attribution import compute_root_loss  # noqa: E402
from corvin_compute.hac.coordinator import HACCoordinator  # noqa: E402
from corvin_compute.hac.manifest import HACManifest, LossWeights, SubManagerSpec  # noqa: E402
from corvin_compute.pipeline.coordinator import PipelineCoordinator  # noqa: E402
from corvin_compute.pipeline.manifest import PipelineManifest, StageSpec  # noqa: E402


def _crash(name, payload):
    raise RuntimeError("tool crashed")


def _pipeline(tmp_path, stages, runner):
    m = PipelineManifest(pipeline_id="pipeline_t", tenant_id="_default", stages=stages,
                         steering_gate=False, steering_gate_timeout_s=1,
                         budget={"max_iterations": 10})
    c = PipelineCoordinator(pipeline_id="pipeline_t", manifest=m, corvin_home=tmp_path,
                            runner_fn=runner, audit_emit=None, gate_queue=asyncio.Queue())
    asyncio.run(c.run())
    return c


def test_a_pipeline_stage_without_a_valid_loss_fails_the_pipeline(tmp_path):
    c = _pipeline(tmp_path, [StageSpec(stage_id="s1", tool_name="t", param_grid={"a": [1, 2, 3, 4]}),
                             StageSpec(stage_id="s2", tool_name="t", param_grid={"a": [1]})], _crash)
    r = c.get_result_dict()
    assert r["state"] == "failed", r
    assert "s1" in (r["error"] or "")


def test_pipeline_stage_inputs_reach_the_tool(tmp_path):
    seen = []

    def runner(name, payload):
        seen.append(dict(payload))
        return {"loss": 1.0}

    c = _pipeline(tmp_path, [StageSpec(stage_id="s1", tool_name="t", param_grid={"a": [1]}),
                             StageSpec(stage_id="s2", tool_name="t", param_grid={"a": [1]},
                                       inputs={"prev": "$s1.best_params"})], runner)
    assert c.get_result_dict()["state"] == "converged"
    assert any("_input_prev" in p for p in seen)


def _hac(tmp_path, runner_for, gate):
    sms = [SubManagerSpec.from_dict({"manager_id": mid, "strategy": "grid", "min_iterations": 1,
                                     "stages": [{"tool_name": mid, "param_grid": {"a": [1, 2, 3]}}]})
           for mid in ("m1", "m2")]
    m = HACManifest(hac_id="hac_t", tenant_id="_default", sub_managers=sms,
                    loss_weights=LossWeights.from_dict({"weights": {"m1": .5, "m2": .5}}),
                    budget={"max_iterations": 6}, backprop_gate=gate, backprop_gate_timeout_s=0.05)
    c = HACCoordinator(hac_id="hac_t", manifest=m, corvin_home=tmp_path, runner_fn=runner_for,
                       audit_emit=None, gate_queue=asyncio.Queue())
    asyncio.run(c.run())
    return c.get_result_dict()


def test_hac_with_every_sub_manager_failed_is_failed_with_no_root_loss(tmp_path):
    for gate in (True, False):
        r = _hac(tmp_path, _crash, gate)
        assert r["state"] == "failed", (gate, r)
        assert r["final_root_loss"] is None
        assert r["error"]


def test_hac_with_one_failed_sub_manager_does_not_report_a_partial_composite(tmp_path):
    def runner(name, payload):
        if name == "m2":
            raise RuntimeError("m2 broken")
        return {"loss": float(payload["a"])}

    r = _hac(tmp_path, runner, gate=False)
    assert r["state"] == "failed", r
    assert "m2" in r["error"]
    assert r["final_root_loss"] is None


def test_no_root_loss_is_invented():
    assert compute_root_loss({"a": None, "b": None}, {"a": .5, "b": .5}) is None
    assert compute_root_loss({}, {}, mode="pareto") is None


def test_non_finite_losses_are_not_losses():
    for v in ("nan", float("nan"), float("inf"), "-inf", True):
        assert _resolve_loss({"loss": v}, "loss") is None, v
    assert _resolve_loss({"loss": "2.5"}, "loss") == 2.5
    assert not math.isnan(_resolve_loss({"loss": 0}, "loss"))
