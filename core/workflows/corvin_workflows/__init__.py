"""Corvin L26 — AWP-Workflow-Bridge.

Public API:
- load_workflow(path)        : YAML → WorkflowDoc
- validate(doc)              : R1..R10 validation (raises WorkflowInvalid)
- DAGRunner                  : topological DAG execution with node-type dispatch
- WorkerEngine (Protocol)    : the spawn-contract every backend implements
- StubEngine                 : deterministic test engine
"""

from .storage import WorkflowDoc, load_workflow, dump_workflow
from .validator import validate, WorkflowInvalid
from .runner import DAGRunner, RunResult, NodeResult, ResumeContext, resume_workflow
from .engines import WorkerEngine, StubEngine, EngineCall
from .node_types import NODE_TYPES, register_node_type, WorkflowPaused

__all__ = [
    "WorkflowDoc",
    "load_workflow",
    "dump_workflow",
    "validate",
    "WorkflowInvalid",
    "DAGRunner",
    "RunResult",
    "NodeResult",
    "ResumeContext",
    "resume_workflow",
    "WorkerEngine",
    "StubEngine",
    "EngineCall",
    "NODE_TYPES",
    "register_node_type",
    "WorkflowPaused",
]
