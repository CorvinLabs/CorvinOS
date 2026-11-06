"""A worker restart continues an interrupted run; it does not start it over.

Measured 2026-11-06 (review): a run left ``running`` on disk was resumed from iteration 0 —
the grid index lives in memory only — and rewrote ``iterations/0001.json…`` it had already
written, so the budget effectively doubled and the history was destroyed.

Real worker (Unix socket, the worker's own forge runner, bwrap) started over a home that
holds an interrupted run; recovery runs as part of ``serve_forever`` exactly like a service
start. The interruption is made from real files: a real run is stopped after two iterations
and put back to ``running`` with its full budget.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bwrap") is None, reason="needs bwrap")

from _real_worker import real_worker, seed_user_tool  # noqa: E402
from corvin_compute import cli, strategies as strat_pkg  # noqa: E402
from corvin_compute.budget import Budget  # noqa: E402
from corvin_compute.driver import ComputeRun, ComputeRunSpec  # noqa: E402
from corvin_compute.state import RunStore, new_run_id  # noqa: E402

QUAD = ("import json, sys\n"
        "d = json.load(sys.stdin)\n"
        "print(json.dumps({'loss': (float(d['x']) - 2) ** 2}))\n")
SCHEMA = {"type": "object", "properties": {"x": {"type": "number"}}, "required": ["x"],
          "additionalProperties": False}


def test_a_restarted_worker_continues_after_the_recorded_iterations(monkeypatch):
    home = Path(tempfile.mkdtemp(prefix="cr-"))
    monkeypatch.setenv("CORVIN_HOME", str(home))
    try:
        seed_user_tool("probe.quad", QUAD, SCHEMA)
        runner = cli._build_runner_fn(tenant_id="_default", corvin_home_path=home)

        # a real run, stopped after 2 of 6 points ...
        rid = new_run_id()
        spec = ComputeRunSpec(
            tenant_id="_default", tool_name="probe.quad", param_grid={"x": [0, 1, 2, 3, 4, 5]},
            loss_metric="loss", strategy_name="grid",
            budget=Budget.from_dict({"max_iterations": 2, "max_wall_clock_s": 120}))
        ComputeRun(spec, corvin_home=home, runner_fn=runner,
                   strategy_factory=strat_pkg.load_strategy, run_id=rid).run()
        store = RunStore(home, "_default")
        iters = store.root / rid / "iterations"
        before = {p.name: p.read_bytes() for p in iters.glob("*.json")}
        assert sorted(before) == ["0001.json", "0002.json"]

        # ... put back to "running" with its full budget: the state a killed worker leaves
        summary = store.read_summary(rid)
        summary["state"] = "running"
        store.write_summary(rid, summary)
        manifest = store.read_manifest(rid)
        manifest["budget"]["max_iterations"] = 6
        store.write_manifest(rid, manifest)

        with real_worker(home) as (client, _server):
            for _ in range(100):
                if store.read_summary(rid).get("state") not in ("running", "queued"):
                    break
                time.sleep(0.2)
            result = client.get_result(rid)

        assert result["state"] != "failed", result
        after = {p.name: p.read_bytes() for p in iters.glob("*.json")}
        assert sorted(after) == [f"{i:04d}.json" for i in range(1, 7)], sorted(after)
        for name, raw in before.items():
            assert after[name] == raw, f"{name} was rewritten by the recovery"
        assert result["total_iterations"] == 6
        assert result["best_loss"] == 0.0 and result["best_params"] == {"x": 2}
        xs = [store.read_iterations(rid)[i].params["x"] for i in range(6)]
        assert xs == [0, 1, 2, 3, 4, 5], "each point exactly once, in grid order"
    finally:
        shutil.rmtree(home, ignore_errors=True)
