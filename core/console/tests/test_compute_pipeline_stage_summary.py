"""A finished pipeline stage must show its real iteration count and loss in the console.

The coordinator never wrote a stage_summary.json and a stage's iterations live under compute/runs/<run_id>, so every
finished stage card read "0 iter" with no loss data. The coordinator now records run id, best loss and iteration count;
the console follows the run id to the iteration files.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for p in (_REPO / "core" / "console", _REPO / "core" / "compute", _REPO / "corvin_operator" / "forge"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

compute = pytest.importorskip("corvin_console.routes.compute")
from corvin_compute.pipeline.coordinator import PipelineCoordinator  # noqa: E402
from corvin_compute.pipeline.manifest import PipelineManifest, PipelineStore, StageSpec  # noqa: E402


def test_finished_stage_card_has_iterations_and_loss(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")

    def runner(tool_name, payload, datasources=None):
        return {"loss": float(payload["x"]) / 10.0, "meta": {}}

    stage = StageSpec(stage_id="s1", tool_name="demo.tool", strategy="grid", param_grid={"x": [1, 2, 3]},
                      budget={"max_iterations": 3})
    manifest = PipelineManifest(pipeline_id="pipeline_t", tenant_id="_default", stages=[stage], steering_gate=False)
    PipelineStore(tmp_path, "_default", "pipeline_t").write_manifest(manifest)
    coord = PipelineCoordinator(pipeline_id="pipeline_t", manifest=manifest, corvin_home=tmp_path, runner_fn=runner,
                                audit_emit=lambda *a, **k: None, gate_queue=asyncio.Queue())

    asyncio.run(coord.run())

    stage_dir = tmp_path / "tenants" / "_default" / "compute" / "pipelines" / "pipeline_t" / "stages" / "s1"
    written = json.loads((stage_dir / "stage_summary.json").read_text())
    assert written["state"] == "complete" and written["total_iterations"] == 3 and written["run_id"]

    card = compute._read_stage_summary(stage_dir)
    assert card["iter_count"] == 3
    assert card["best_loss"] == pytest.approx(0.1)
    assert [it["loss"] for it in card["iterations"]] == pytest.approx([0.1, 0.2, 0.3])
