"""Review 2026-11-19 round 3, R3-BR-5: stage / manager ids are unique, case-insensitively."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO / "core" / "compute") not in sys.path:
    sys.path.insert(0, str(_REPO / "core" / "compute"))

from corvin_compute.engine_protocol import ComputeSpec  # noqa: E402
from corvin_compute.hac.coordinator import HACCoordinator  # noqa: E402
from corvin_compute.hac.manifest import (  # noqa: E402
    HACManifest, LossWeights, SubManagerSpec, require_unique_manager_ids,
)
from corvin_compute.pipeline.coordinator import PipelineCoordinator  # noqa: E402
from corvin_compute.pipeline.engine import PipelineEngine  # noqa: E402
from corvin_compute.pipeline.manifest import (  # noqa: E402
    PipelineManifest, StageSpec, require_unique_stage_ids,
)


def _stage(sid: str) -> dict:
    return {"stage_id": sid, "tool_name": "t", "param_grid": {"a": [1]}}


@pytest.mark.parametrize("ids", [("s1", "s1"), ("s1", "S1"), ("a", "b", "A")])
def test_pipeline_submit_refuses_duplicate_stage_ids(tmp_path, ids):
    calls: list = []
    eng = PipelineEngine(corvin_home=tmp_path, runner_fn=lambda n, p: calls.append(n) or {"loss": 1.0})

    async def go():
        eng.set_loop(asyncio.get_running_loop())
        spec = ComputeSpec(engine="pipeline", tenant_id="_default", budget={"max_iterations": 2},
                           extra={"stages": [_stage(i) for i in ids], "steering_gate": False})
        with pytest.raises(ValueError, match="duplicate stage_id"):
            eng.submit(spec)

    asyncio.run(go())
    assert eng._coordinators == {} and calls == []


def test_pipeline_submit_accepts_distinct_stage_ids(tmp_path):
    eng = PipelineEngine(corvin_home=tmp_path, runner_fn=lambda n, p: {"loss": 1.0})

    async def go():
        eng.set_loop(asyncio.get_running_loop())
        spec = ComputeSpec(engine="pipeline", tenant_id="_default", budget={"max_iterations": 2},
                           extra={"stages": [_stage("s1"), _stage("s2")], "steering_gate": False})
        pid = eng.submit(spec)
        await asyncio.wait_for(asyncio.wrap_future(eng._futures[pid]), timeout=30)
        return eng._coordinators[pid].get_result_dict()

    result = asyncio.run(go())
    assert sorted(result["stages"]) == ["s1", "s2"]


def test_the_coordinator_refuses_duplicates_built_directly(tmp_path):
    m = PipelineManifest(pipeline_id="pipeline_d", tenant_id="_default",
                         stages=[StageSpec.from_dict(_stage("x")), StageSpec.from_dict(_stage("X"))],
                         steering_gate=False, steering_gate_timeout_s=5, budget={})
    with pytest.raises(ValueError, match="duplicate stage_id"):
        PipelineCoordinator(pipeline_id="pipeline_d", manifest=m, corvin_home=tmp_path,
                            runner_fn=lambda n, p: {}, audit_emit=None, gate_queue=asyncio.Queue())


def test_add_stage_at_a_gate_cannot_reuse_an_id(tmp_path):
    async def go():
        q: asyncio.Queue = asyncio.Queue()
        m = PipelineManifest(pipeline_id="pipeline_g", tenant_id="_default",
                             stages=[StageSpec.from_dict(_stage("s1")),
                                     StageSpec.from_dict(_stage("s2"))],
                             steering_gate=True, steering_gate_timeout_s=5,
                             budget={"max_iterations": 3})
        c = PipelineCoordinator(pipeline_id="pipeline_g", manifest=m, corvin_home=tmp_path,
                                runner_fn=lambda n, p: {"loss": 1.0}, audit_emit=None, gate_queue=q)
        q.put_nowait({"action_type": "add_stage", "payload": {"stage": _stage("S1")}})
        q.put_nowait({"action_type": "add_stage", "payload": {"stage": _stage("s3")}})
        q.put_nowait({"action_type": "resume", "payload": {}})
        for _ in range(3):
            q.put_nowait({"action_type": "resume", "payload": {}})
        await asyncio.wait_for(c.run(), timeout=30)
        return c

    result = asyncio.run(go()).get_result_dict()
    assert sorted(result["stages"]) == ["s1", "s2", "s3"]


def test_helpers():
    require_unique_stage_ids([_stage("a"), _stage("b")])
    with pytest.raises(ValueError):
        require_unique_stage_ids([_stage("a"), _stage("A")])


def test_hac_manager_ids_are_compared_case_insensitively(tmp_path):
    sms = [SubManagerSpec.from_dict({"manager_id": mid, "stages": []}) for mid in ("m", "M")]
    with pytest.raises(ValueError, match="duplicate manager_id"):
        require_unique_manager_ids(sms)
    m = HACManifest(hac_id="hac_case", tenant_id="_default", sub_managers=sms,
                    loss_weights=LossWeights.from_dict({"weights": {"m": 0.5, "M": 0.5}}), budget={})
    with pytest.raises(ValueError, match="duplicate manager_id"):
        HACCoordinator(hac_id="hac_case", manifest=m, corvin_home=tmp_path,
                       runner_fn=lambda n, p: {}, audit_emit=None, gate_queue=asyncio.Queue())


def test_hac_stages_without_ids_are_not_duplicates():
    sms = [SubManagerSpec.from_dict({"manager_id": "m1", "stages": [
        {"tool_name": "a", "param_grid": {"x": [1]}}, {"tool_name": "b", "param_grid": {"x": [1]}}]})]
    require_unique_manager_ids(sms)
    dup = [SubManagerSpec.from_dict({"manager_id": "m1", "stages": [
        {"stage_id": "s", "tool_name": "a"}, {"stage_id": "S", "tool_name": "b"}]})]
    with pytest.raises(ValueError, match="duplicate stage_id"):
        require_unique_manager_ids(dup)
