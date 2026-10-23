"""The console must read the manifest keys the compute worker actually writes.

The worker's flat run manifest carries ``accepted_at`` and ``param_grid``; the console readers asked for
``started_at`` and ``params`` (the keys an older console-side writer used), so every real run listed no
start time and every experiment comparison table showed empty parameters.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parents[3]
for p in (_REPO / "core" / "console", _REPO / "corvin_operator" / "forge"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

compute = pytest.importorskip("corvin_console.routes.compute")


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    return tmp_path / "tenants" / "_default" / "compute"


def _worker_run(runs_root: Path, run_id: str) -> None:
    d = runs_root / run_id
    (d / "iterations").mkdir(parents=True)
    # exactly the shape corvin_compute writes (see worker._op_submit_run / state.py)
    (d / "manifest.json").write_text(json.dumps({
        "run_id": run_id, "tool_name": "assistant.spotify_streams_forecast", "strategy": "grid",
        "accepted_at": 1792772941.5, "budget": {"max_iterations": 25},
        "param_grid": {"window": [2, 3], "alpha": [0.5, 1.0]},
    }), encoding="utf-8")
    (d / "summary.json").write_text(json.dumps({"state": "budget_exhausted", "best_loss": 0.0189, "best_iter": 1}), encoding="utf-8")
    (d / "iterations" / "iter_1.json").write_text(json.dumps({"iter": 1, "loss": 0.0189}), encoding="utf-8")


def test_run_list_shows_the_start_time_the_worker_wrote(home):
    _worker_run(home / "runs", "compute_abc")
    rec = SimpleNamespace(tenant_id="_default")

    out = compute.compute_status(rec)

    run = next(r for r in out["runs"] if r["run_id"] == "compute_abc")
    assert run["started_at"] == 1792772941.5


def test_experiment_comparison_shows_the_parameters_the_worker_wrote(home):
    _worker_run(home / "runs", "compute_abc")
    exp_dir = home / "experiments" / "exp_1"
    exp_dir.mkdir(parents=True)
    (exp_dir / "experiment.json").write_text(json.dumps({
        "experiment_id": "exp_1", "name": "e", "run_ids": ["compute_abc"],
        "baseline_run_id": "compute_abc", "champion_run_id": None, "created_at": 1,
    }), encoding="utf-8")
    rec = SimpleNamespace(tenant_id="_default")

    out = compute.get_experiment("exp_1", rec)

    row = out["runs_detail"][0]
    assert row["params"] == {"window": [2, 3], "alpha": [0.5, 1.0]}
    assert row["started_at"] == 1792772941.5
    assert row["is_baseline"] is True
