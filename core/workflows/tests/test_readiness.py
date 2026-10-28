"""Readiness report (ADR-2275): validator + synthetic stub run + acceptance case + repairs.

Runs the REAL DAGRunner with the deterministic stub; no model, no network.
"""
from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest
import yaml

from corvin_workflows import WorkflowDoc, register_node_type
from corvin_workflows.node_types import NODE_TYPES
from corvin_workflows.readiness import (
    ReadinessReport,
    _reachability,
    check_readiness,
    validate_acceptance,
)
from corvin_workflows.runner import NodeResult, RunResult

EXAMPLE = Path(__file__).resolve().parents[1] / "corvin_workflows" / "examples" / "all_node_types.awp.yaml"

ACCEPTANCE = {
    "inputs": {"topic": "x"},
    "stub_responses": {"researcher": {"items": ["a", "b", "c"], "tone": "urgent"}, "writer": {"text": "w"}},
    "expect": {"state": "complete", "nodes_reached": ["research", "join", "reply"],
               "final_contains": {"join.report": "urgent"}},
}


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    monkeypatch.setenv("ADAPTER_OUTBOX", str(outbox))
    return outbox


def _sig(report: ReadinessReport, name: str):
    return next(s for s in report.signals if s.name == name)


def _simple(extra_nodes: list[dict] | None = None) -> str:
    graph = [{"id": "a", "type": "agent", "agent": "worker", "depends_on": []}]
    graph += extra_nodes or []
    return yaml.safe_dump({
        "awp": "1.0.0",
        "workflow": {"name": "wf_ready", "description": "d"},
        "orchestration": {"engine": "dag", "graph": graph},
    })


def test_a_all_node_types_with_acceptance_is_ready():
    r = check_readiness(EXAMPLE.read_text(), acceptance=ACCEPTANCE, repairs=0)
    assert r.ready and r.synthetic and r.run_state == "complete"
    assert [s.status for s in r.signals] == ["pass", "pass", "pass", "info", "pass", "info"]


def test_b_dead_branch_fails_validator_and_does_not_run():
    text = _simple([{"id": "b", "type": "agent", "agent": "w", "depends_on": ["ghost"]}])
    r = check_readiness(text, acceptance=ACCEPTANCE, repairs=0)
    v = _sig(r, "validator")
    assert v.status == "fail" and "R8" in v.detail
    assert r.run_state is None and not r.ready
    assert _sig(r, "reachability").status == "unmeasured"


def test_unparseable_yaml_is_a_validator_failure():
    r = check_readiness("::: not [yaml", acceptance=None, repairs=0)
    assert _sig(r, "validator").status == "fail" and not r.ready


def test_c_failed_unprovoked_node_names_the_node():
    boom = {"id": "boom", "type": "code", "language": "python3", "depends_on": ["a"],
            "source": "def main():\n    raise RuntimeError('x')\n", "outputs": ["o"],
            "retry": {"error_strategy": "fail_branch"}}
    r = check_readiness(_simple([boom]), acceptance=None, repairs=0)
    s = _sig(r, "reachability")
    assert s.status == "fail" and "'boom'" in s.detail and not r.ready


def test_c2_node_missing_from_the_run_is_named():
    doc = WorkflowDoc.from_dict(yaml.safe_load(_simple()))
    run = RunResult(workflow="w", state="complete", inputs={})
    s = _reachability(doc, run, False, {})
    assert s.status == "fail" and "'a'" in s.detail
    run.nodes["a"] = NodeResult("a", "agent", 0, 0, {}, status="success")
    assert _reachability(doc, run, False, {}).status == "pass"


def test_provoked_fail_branch_is_legitimate():
    nodes = [
        {"id": "flaky", "type": "agent", "agent": "flaky_src", "depends_on": ["a"],
         "retry": {"error_strategy": "fail_branch"}},
        {"id": "after", "type": "agent", "agent": "w", "depends_on": ["flaky"]},
    ]
    acc = {"stub_responses": {"flaky_src": {"__raise__": True}}, "expect": {"state": "complete", "nodes_reached": ["a"]}}
    r = check_readiness(_simple(nodes), acceptance=acc, repairs=0)
    assert r.ready, r.signals


def test_d_no_acceptance_is_unmeasured_and_not_ready():
    r = check_readiness(EXAMPLE.read_text(), acceptance=None, repairs=0)
    assert _sig(r, "acceptance").status == "unmeasured"
    assert _sig(r, "reachability").status == "pass"
    assert not r.ready


def test_acceptance_failure_and_malformed():
    bad = {**ACCEPTANCE, "expect": {"nodes_reached": ["research"], "final_contains": {"join.report": "NOPE"}}}
    assert _sig(check_readiness(EXAMPLE.read_text(), acceptance=bad, repairs=0), "acceptance").status == "fail"
    assert _sig(check_readiness(_simple(), acceptance={"expect": {"state": "weird"}}, repairs=0), "acceptance").status == "fail"
    assert validate_acceptance({"expect": {}}) is not None
    assert validate_acceptance({"expect": {"state": "complete"}, "x": 1}) is not None
    assert validate_acceptance(ACCEPTANCE) is None


def test_repairs_above_ceiling_fail():
    ok = check_readiness(EXAMPLE.read_text(), acceptance=ACCEPTANCE, repairs=3)
    assert ok.ready
    over = check_readiness(EXAMPLE.read_text(), acceptance=ACCEPTANCE, repairs=4)
    assert _sig(over, "repairs").status == "fail" and not over.ready


def test_e_endless_node_times_out_within_the_limit():
    stop = threading.Event()

    def spin(*, node, engine, state, inputs, audit):
        while not stop.is_set():
            time.sleep(0.01)
        return {}

    register_node_type("spin_test_only", validate=None, execute=spin)
    try:
        text = _simple([{"id": "s", "type": "spin_test_only", "depends_on": ["a"]}])
        t0 = time.perf_counter()
        r = check_readiness(text, acceptance=ACCEPTANCE | {"expect": {"state": "complete"}}, repairs=0, wall_limit_s=0.5)
        assert time.perf_counter() - t0 <= 1.5
        s = _sig(r, "reachability")
        assert s.status == "fail" and s.detail == "timeout" and not r.ready
    finally:
        stop.set()
        NODE_TYPES.pop("spin_test_only", None)


def test_f_no_network_and_no_outbox_write(monkeypatch, _sandbox):
    def no_socket(*a, **k):
        raise AssertionError("the readiness check opened a socket")

    monkeypatch.setattr(socket, "socket", no_socket)
    monkeypatch.setattr(socket, "create_connection", no_socket)
    r = check_readiness(EXAMPLE.read_text(), acceptance=ACCEPTANCE, repairs=0)
    assert r.ready
    assert list(_sandbox.glob("*.json")) == []  # deliver/answer never reached the outbox


def test_reports_are_reproducible():
    one = check_readiness(EXAMPLE.read_text(), acceptance=ACCEPTANCE, repairs=1)
    two = check_readiness(EXAMPLE.read_text(), acceptance=ACCEPTANCE, repairs=1)
    assert one.signals == two.signals and one.run_state == two.run_state


def test_real_node_registry_is_not_mutated():
    before = {k: v["execute"] for k, v in NODE_TYPES.items()}
    check_readiness(EXAMPLE.read_text(), acceptance=ACCEPTANCE, repairs=0)
    assert before == {k: v["execute"] for k, v in NODE_TYPES.items()}


def test_report_carries_per_node_outcomes_in_execution_order():
    r = check_readiness(EXAMPLE.read_text(), acceptance=ACCEPTANCE, repairs=0)
    by = {n.node_id: n.status for n in r.nodes}
    assert by["research"] == "success" and by["low_path"] == "skipped" and by["high_path"] == "success"
    assert [n.node_id for n in r.nodes][0] == "research"
    assert r.as_dict()["nodes"][0]["node_id"] == "research"


# ── node_model (ADR-2308): a cost hint, never a blocker ──────────────────────────────────────────

def test_node_model_counts_model_nodes_without_a_model_and_never_blocks():
    text = _simple([
        {"id": "b", "type": "agent", "agent": "w", "depends_on": ["a"], "model": "haiku"},
        {"id": "c", "type": "route", "mode": "condition", "depends_on": ["a"],
         "cases": [{"id": "x", "when": {"selector": "a.ok", "op": "==", "value": True}},
                   {"id": "y", "when": "default"}]},
    ])
    r = check_readiness(text, acceptance=None, repairs=0)
    s = _sig(r, "node_model")
    # "a" has no model, "b" has one, the condition route never calls a model and is not counted
    assert (s.status, s.value) == ("info", 1)
    assert "1 of 2 model node(s)" in s.detail
    assert "a" not in s.detail.split() and "b" not in s.detail.split()   # counts only, no node ids


def test_node_model_reports_all_set_when_every_model_node_has_one():
    text = yaml.safe_dump({
        "awp": "1.0.0",
        "workflow": {"name": "wf_ready", "description": "d"},
        "orchestration": {"engine": "dag", "graph": [
            {"id": "a", "type": "agent", "agent": "worker", "depends_on": [], "model": "sonnet"}]},
    })
    s = _sig(check_readiness(text, acceptance=None, repairs=0), "node_model")
    assert (s.status, s.value) == ("info", 0) and "all 1 model node(s)" in s.detail


def test_node_model_is_not_measured_when_the_validator_fails():
    r = check_readiness("::: not [yaml", acceptance=None, repairs=0)
    assert _sig(r, "node_model").status == "info" and _sig(r, "node_model").detail == "not measured"
