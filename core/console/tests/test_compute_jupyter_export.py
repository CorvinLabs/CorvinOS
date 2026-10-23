"""The Jupyter export must work without nbformat and for any experiment, not only the Spotify demo.

It answered 503 "nbformat not installed" (a dependency nothing declares) and, once installed, wrote cells that
loaded a hard-coded ``weekly_chart_aggregates.csv`` and a ``track_id`` / ``streams_p50`` table, so a notebook for a real
experiment failed on its first cells.
"""
from __future__ import annotations

import contextlib
import io
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
    root = tmp_path / "tenants" / "_default" / "compute"
    run = root / "runs" / "compute_abc"
    (run / "iterations").mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"strategy": "grid", "tool_name": "t", "accepted_at": 1.0}), encoding="utf-8")
    (run / "summary.json").write_text(json.dumps({"best_loss": 0.0189, "best_iter": 1, "total_iterations": 2, "state": "converged",
                                                  "top_k": [{"iter": 1, "loss": 0.0189}]}), encoding="utf-8")
    for i, loss in ((1, 0.0189), (2, 0.021)):
        (run / "iterations" / f"iter_{i}.json").write_text(json.dumps({"iter": i, "loss": loss}), encoding="utf-8")
    exp = root / "experiments" / "exp_1"
    exp.mkdir(parents=True)
    (exp / "experiment.json").write_text(json.dumps({"experiment_id": "exp_1", "name": "Strategy comparison", "hypothesis": "h",
                                                     "run_ids": ["compute_abc"], "champion_run_id": "compute_abc", "created_at": 1}), encoding="utf-8")
    return root


def _notebook():
    resp = compute.export_jupyter("exp_1", SimpleNamespace(tenant_id="_default"))
    return json.loads(resp.body.decode("utf-8")), resp


def test_export_is_a_valid_notebook_without_nbformat(home, monkeypatch):
    monkeypatch.setitem(sys.modules, "nbformat", None)  # an import of nbformat now raises ImportError
    nb, resp = _notebook()

    assert resp.status_code == 200
    assert nb["nbformat"] == 4
    assert {c["cell_type"] for c in nb["cells"]} == {"markdown", "code"}
    text = json.dumps(nb)
    assert "weekly_chart_aggregates" not in text and "streams_p50" not in text and "pipeline_spotify_chart_pred" not in text
    assert "Strategy comparison" in "".join(nb["cells"][0]["source"])


def test_the_code_cells_run_against_the_real_run_files(home, monkeypatch):
    monkeypatch.setenv("MPLBACKEND", "Agg")
    nb, _ = _notebook()
    ns: dict = {}
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        for cell in nb["cells"]:
            if cell["cell_type"] != "code":
                continue
            src = "".join(cell["source"])
            if "matplotlib" in src:
                pytest.importorskip("matplotlib")
            exec(compile(src, "<cell>", "exec"), ns)  # noqa: S102 — executing the export is the point of the test

    printed = out.getvalue()
    assert "compute_abc" in printed and "0.0189" in printed
    assert "best loss: 0.0189" in printed
