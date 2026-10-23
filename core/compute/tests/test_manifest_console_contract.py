"""The HAC / pipeline manifests must carry the keys the console reads (routes/compute.py).

list_hac_runs reads manifest["sub_managers"], ["fluid_reallocation"], ["started_at"]; hac_detail builds its manager
list from manifest["sub_managers"]; list_pipelines reads ["started_at"]. The engines used to persist
"manager_ids" only, so every real HAC run showed manager_count 0 and an empty manager view.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

COMPUTE_ROOT = Path(__file__).resolve().parents[1]
if str(COMPUTE_ROOT) not in sys.path:
    sys.path.insert(0, str(COMPUTE_ROOT))

from corvin_compute.hac.manifest import HACManifest, HACStore, LossWeights, SubManagerSpec  # noqa: E402
from corvin_compute.pipeline.manifest import PipelineManifest, PipelineStore, StageSpec  # noqa: E402


def test_hac_manifest_has_the_keys_the_console_reads(tmp_path):
    stage = {"stage_id": "fc_us", "tool_name": "t", "strategy": "random"}
    m = HACManifest(
        hac_id="hac_x", tenant_id="_default",
        sub_managers=[SubManagerSpec("market_us", [stage], budget_fraction=0.5), SubManagerSpec("market_gb", [stage], budget_fraction=0.5)],
        loss_weights=LossWeights(weights={"market_us": 0.5, "market_gb": 0.5}), budget={"max_iterations": 10},
        fluid_reallocation=True,
    )
    HACStore(tmp_path, "_default", "hac_x").write_manifest(m)
    data = json.loads((tmp_path / "tenants" / "_default" / "compute" / "hac" / "hac_x" / "manifest.json").read_text())

    assert [sm["manager_id"] for sm in data["sub_managers"]] == ["market_us", "market_gb"]
    assert data["sub_managers"][0]["stage_ids"] == ["fc_us"]
    assert data["sub_managers"][0]["budget_fraction"] == 0.5
    assert data["fluid_reallocation"] is True
    assert isinstance(data["started_at"], float)
    assert data["manager_ids"] == ["market_us", "market_gb"], "the old key stays for existing readers"


def test_pipeline_manifest_records_started_at(tmp_path):
    m = PipelineManifest(pipeline_id="pipeline_x", tenant_id="_default",
                         stages=[StageSpec(stage_id="s1", tool_name="t")])
    PipelineStore(tmp_path, "_default", "pipeline_x").write_manifest(m)
    data = json.loads((tmp_path / "tenants" / "_default" / "compute" / "pipelines" / "pipeline_x" / "manifest.json").read_text())
    assert isinstance(data["started_at"], float)
    assert data["stages"][0]["stage_id"] == "s1"
