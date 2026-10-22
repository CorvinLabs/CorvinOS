"""peer_agent node (ADR-2276): validator, registration, retry policy.

Executor, gate and exactly-once tests are added next to these as the node is built.
Run: core/console/.venv/bin/python -m pytest core/workflows/tests/test_peer_agent_node.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from corvin_workflows import DAGRunner, StubEngine  # noqa: E402
from corvin_workflows.node_types import NODE_TYPES, node_requires_engine  # noqa: E402
from corvin_workflows.runner import _retry_config  # noqa: E402
from corvin_workflows.storage import WorkflowDoc, _parse_yaml  # noqa: E402
from corvin_workflows.validator import WorkflowInvalid, validate  # noqa: E402


def _node(**over):
    base = {
        "id": "legal_review", "type": "peer_agent", "depends_on": [],
        "endpoint_id": "ep_acme", "agent_id": "contract_reviewer", "instruction": "Review this.",
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def _validate_node(node):
    NODE_TYPES["peer_agent"]["validate"](node)


def _wf(graph_yaml: str) -> str:
    return (
        'awp: "1.1.0"\nworkflow:\n  name: wf_peer_agent\n  description: peer agent node\n'
        "orchestration:\n  engine: dag\n  graph:\n" + graph_yaml
    )


def test_registered_and_engine_free():
    assert "peer_agent" in NODE_TYPES
    assert node_requires_engine({"id": "x", "type": "peer_agent"}) is False


@pytest.mark.parametrize("node", [
    _node(),
    _node(endpoint_id=None, agent_id=None, capability="analysis"),
    _node(instruction=None, instruction_from="draft.summary", depends_on=["draft"]),
    _node(timeout_s=1),
    _node(timeout_s=900),
    _node(retry={"max_retries": 2, "error_strategy": "fail_branch"}, share_output=["result"]),
])
def test_valid_configs_pass(node):
    _validate_node(node)


@pytest.mark.parametrize("node,fragment", [
    (_node(capability="analysis"), "exactly one addressing mode"),
    (_node(endpoint_id=None, agent_id=None), "exactly one addressing mode"),
    (_node(agent_id=None), "together"),
    (_node(endpoint_id=None), "together"),
    (_node(endpoint_id="ep/acme"), "endpoint_id"),
    (_node(endpoint_id=""), "endpoint_id"),
    (_node(agent_id="../etc"), "agent_id"),
    (_node(agent_id="a" * 129), "agent_id"),
    (_node(endpoint_id=None, agent_id=None, capability="telepathy"), "capability"),
    (_node(instruction=None), "exactly one of"),
    (_node(instruction_from="draft.summary"), "exactly one of"),
    (_node(instruction="   "), "non-empty"),
    (_node(instruction=None, instruction_from=""), "non-empty"),
    (_node(instruction="x" * (16 * 1024 + 1)), "exceeds"),
    (_node(timeout_s=0), "timeout_s"),
    (_node(timeout_s=901), "timeout_s"),
    (_node(timeout_s=True), "timeout_s"),
    (_node(timeout_s=float("nan")), "timeout_s"),
    (_node(timeout_s="300"), "timeout_s"),
    (_node(endpoint="ep_acme"), "unknown key"),
    (_node(config={"endpoint_id": "x"}), "unknown key"),
])
def test_invalid_configs_are_rejected(node, fragment):
    with pytest.raises(ValueError) as exc:
        _validate_node(node)
    assert fragment in str(exc.value)


def test_workflow_validator_runs_the_peer_agent_rules_and_the_selector_rule():
    ok = _wf(
        "    - id: draft\n      type: code\n      language: python3\n      source: 'def main(): return {\"summary\": \"s\"}'\n"
        "      outputs: [summary]\n      depends_on: []\n"
        "    - id: review\n      type: peer_agent\n      endpoint_id: ep_acme\n      agent_id: reviewer\n"
        "      instruction_from: draft.summary\n      depends_on: [draft]\n"
    )
    validate(WorkflowDoc.from_dict(_parse_yaml(ok)))
    # R9b: the selector may only read a node the reader depends on
    bad = ok.replace("      depends_on: [draft]\n", "      depends_on: []\n")
    with pytest.raises(WorkflowInvalid) as exc:
        validate(WorkflowDoc.from_dict(_parse_yaml(bad)))
    assert exc.value.code == "R9"
    # R10: a per-type failure surfaces through the workflow validator
    worse = ok.replace("      agent_id: reviewer\n", "")
    with pytest.raises(WorkflowInvalid) as exc:
        validate(WorkflowDoc.from_dict(_parse_yaml(worse)))
    assert exc.value.code == "R10"


def test_retry_config_never_retries_peer_agent():
    max_retries, _i, _s = _retry_config(_node(retry={"max_retries": 2}))
    assert max_retries == 0


def test_runner_calls_a_failing_peer_node_once_despite_retry_block():
    calls = []

    def failing(*, node, engine, state, inputs, audit):
        calls.append(node["id"])
        raise RuntimeError("peer said no")

    doc = WorkflowDoc.from_dict(_parse_yaml(_wf(
        "    - id: legal_review\n      type: peer_agent\n      endpoint_id: ep_acme\n"
        "      agent_id: reviewer\n      instruction: hi\n      depends_on: []\n"
        "      retry:\n        max_retries: 2\n")))
    run = DAGRunner(doc, engine=StubEngine(), executor_overrides={"peer_agent": failing}).run({})
    assert calls == ["legal_review"], "a repeated send would be a second task at another organisation"
    assert run.state == "failed"
