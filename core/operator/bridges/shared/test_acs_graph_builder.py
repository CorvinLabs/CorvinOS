"""test_acs_graph_builder.py — unit tests for ACSGraphBuilder (ADR-0104 M9b)."""
import json
import io
import zipfile
from pathlib import Path
import pytest

import sys, os
sys.path.insert(0, str(Path(__file__).resolve().parent))
from acs_graph_builder import ACSGraphBuilder, WorkflowGraph, graph_to_awp_yaml, build_awpkg_bytes


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_run_dir(tmp_path: Path) -> Path:
    """Create a minimal ACS run_dir with all expected subdirs."""
    run_dir = tmp_path / "test-run-abc12345"
    (run_dir / "subtasks").mkdir(parents=True)
    (run_dir / "workers").mkdir(parents=True)
    (run_dir / "iterations").mkdir(parents=True)
    (run_dir / "gate_results").mkdir(parents=True)

    # manifest
    _write(run_dir / "manifest.json", {
        "run_id": "test-run-abc12345",
        "workflow_id": "test-wf",
        "status": "success",
        "iterations": 2,
        "workers_spawned": 3,
    })

    # result
    _write(run_dir / "result.json", {
        "run_id": "test-run-abc12345",
        "workflow_id": "test-wf",
        "status": "success",
        "summary": "All done",
        "final_output": {},
        "error": "",
    })

    # iteration 0 — two parallel workers
    _write(run_dir / "subtasks" / "subtasks_iter_0000.json", {
        "iteration": 0,
        "subtasks": [
            {"id": "worker-a", "task": "Research topic A", "depends_on": [], "can_delegate": False},
            {"id": "worker-b", "task": "Research topic B", "depends_on": [], "can_delegate": False},
        ],
    })

    # iteration 1 — one synthesiser depending on both
    _write(run_dir / "subtasks" / "subtasks_iter_0001.json", {
        "iteration": 1,
        "subtasks": [
            {"id": "worker-c", "task": "Synthesise A and B", "depends_on": ["worker-a", "worker-b"], "can_delegate": False},
        ],
    })

    # worker results
    for wid, conf, status in [("worker-a", 0.85, "success"), ("worker-b", 0.80, "success"), ("worker-c", 0.92, "success")]:
        _write(run_dir / "workers" / f"{wid}_iter0.json", {
            "worker_id": wid, "status": status, "confidence": conf, "iteration": 0, "depth": 0,
        })

    # gate result
    _write(run_dir / "gate_results" / "gate_iter_0001.json", {
        "iteration": 1,
        "passed": True,
        "aggregate_score": 0.88,
        "gates": [
            {"gate_id": "gate1_length", "passed": True, "score": 0.95, "reason": "ok"},
            {"gate_id": "gate4_quality_score", "passed": True, "score": 0.88, "reason": "ok"},
        ],
    })

    return run_dir


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_build_basic_graph(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    graph = ACSGraphBuilder(run_dir).build()

    assert graph is not None
    assert graph.run_id == "test-run-abc12345"
    assert graph.workflow_id == "test-wf"
    assert len(graph.nodes) == 3


def test_node_ids_stable(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    graph = ACSGraphBuilder(run_dir).build()
    assert graph is not None

    ids = {n.node_id for n in graph.nodes}
    assert "w-worker-a" in ids
    assert "w-worker-b" in ids
    assert "w-worker-c" in ids


def test_explicit_depends_on(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    graph = ACSGraphBuilder(run_dir).build()
    assert graph is not None

    synth = next(n for n in graph.nodes if n.worker_id == "worker-c")
    assert "w-worker-a" in synth.depends_on
    assert "w-worker-b" in synth.depends_on


def test_implicit_depends_on_prev_iter(tmp_path):
    """Workers with no explicit depends_on inherit all nodes from the previous iteration."""
    run_dir = _make_run_dir(tmp_path)
    # Remove explicit deps from worker-c
    d = json.loads((run_dir / "subtasks" / "subtasks_iter_0001.json").read_text())
    d["subtasks"][0]["depends_on"] = []
    _write(run_dir / "subtasks" / "subtasks_iter_0001.json", d)

    graph = ACSGraphBuilder(run_dir).build()
    assert graph is not None

    synth = next(n for n in graph.nodes if n.worker_id == "worker-c")
    # Should implicitly depend on both iter-0 nodes
    assert len(synth.depends_on) == 2


def test_confidence_from_worker_meta(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    graph = ACSGraphBuilder(run_dir).build()
    assert graph is not None

    node_a = next(n for n in graph.nodes if n.worker_id == "worker-a")
    assert abs(node_a.confidence - 0.85) < 1e-6


def test_quality_aggregate(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    graph = ACSGraphBuilder(run_dir).build()
    assert graph is not None
    assert abs(graph.quality_aggregate - 0.88) < 1e-6


def test_is_empty_false_when_nodes_present(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    graph = ACSGraphBuilder(run_dir).build()
    assert graph is not None
    assert not graph.is_empty()


def test_is_empty_true_when_no_subtasks(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    # Remove subtask files
    for f in (run_dir / "subtasks").iterdir():
        f.unlink()
    graph = ACSGraphBuilder(run_dir).build()
    assert graph is not None
    assert graph.is_empty()


def test_build_returns_none_missing_dir(tmp_path):
    graph = ACSGraphBuilder(tmp_path / "nonexistent").build()
    assert graph is None


def test_awp_yaml_dag_mode(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    graph = ACSGraphBuilder(run_dir).build()
    assert graph is not None

    yaml_text = graph_to_awp_yaml(graph, mode="dag")
    assert "dag" in yaml_text
    assert "1.0.0" in yaml_text
    assert "w-worker-a" in yaml_text
    assert "w-worker-c" in yaml_text


def test_awp_yaml_template_mode(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    graph = ACSGraphBuilder(run_dir).build()
    assert graph is not None

    yaml_text = graph_to_awp_yaml(graph, mode="template")
    assert "delegation_loop" in yaml_text
    assert "max_loops" in yaml_text


def test_awpkg_bytes_structure(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    graph = ACSGraphBuilder(run_dir).build()
    assert graph is not None

    pkg = build_awpkg_bytes(graph, mode="dag")
    assert len(pkg) > 0

    with zipfile.ZipFile(io.BytesIO(pkg)) as zf:
        names = set(zf.namelist())
        assert "manifest.yaml" in names
        assert any(n.startswith("workflows/") and n.endswith(".awp.yaml") for n in names)
        assert "provenance/acs_manifest.json" in names
        assert "provenance/gate_results.json" in names
        assert "provenance/quality.json" in names


def test_awpkg_manifest_content(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    graph = ACSGraphBuilder(run_dir).build()
    assert graph is not None

    pkg = build_awpkg_bytes(graph, mode="dag")
    with zipfile.ZipFile(io.BytesIO(pkg)) as zf:
        raw = zf.read("manifest.yaml").decode("utf-8")
    assert "com.corvin" in raw
    assert "awpkg" in raw


def test_awpkg_template_mode_filename(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    graph = ACSGraphBuilder(run_dir).build()
    assert graph is not None

    pkg = build_awpkg_bytes(graph, mode="template")
    with zipfile.ZipFile(io.BytesIO(pkg)) as zf:
        names = zf.namelist()
    assert any("template-" in n for n in names)
