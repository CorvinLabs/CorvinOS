"""R1..R10 validator — MVP subset of AWP's R1..R32.

Each rule is a single function so operators can extend the registry without
touching this module. Rules raise WorkflowInvalid with a stable error code.

| Code  | Rule                                                                |
|-------|---------------------------------------------------------------------|
| R1    | awp version present and parseable                                   |
| R2    | workflow.name is a non-empty snake_case-ish identifier              |
| R3    | workflow.description is a non-empty string                          |
| R4    | orchestration.engine is "dag" or "chat" (delegation_loop routes to  |
|       | the separate ACS R1-R36 validator, never reaches this module)       |
| R5    | orchestration.graph is a non-empty list                             |
| R6    | every node has a unique id (slug-shape)                             |
| R7    | every node has a known type (NODE_TYPES key)                        |
| R8    | depends_on entries refer to existing node ids                       |
| R9    | the graph has no cycles                                             |
| R10   | every node passes its own type's validator (delegation_loop needs   |
|       | a manager + budget, fan_out needs an items_from, etc.)              |
| R11   | engine "chat" requires >= 1 answer/ask_human node (ADR-0188 M7) —   |
|       | otherwise a chat-engine workflow could never actually turn-pause    |
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .node_types import NODE_TYPES

if TYPE_CHECKING:
    from .storage import WorkflowDoc


_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class WorkflowInvalid(Exception):
    """Raised when validation fails. The .code field carries the rule id."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def _r1_awp_version(doc: "WorkflowDoc") -> None:
    if not doc.awp_version or not re.match(r"^\d+\.\d+\.\d+$", doc.awp_version):
        raise WorkflowInvalid("R1", f"awp version invalid: {doc.awp_version!r}")


def _r2_name(doc: "WorkflowDoc") -> None:
    if not doc.name or not _NAME_RE.match(doc.name):
        raise WorkflowInvalid("R2", f"workflow.name invalid: {doc.name!r}")


def _r3_description(doc: "WorkflowDoc") -> None:
    if not doc.description or not isinstance(doc.description, str):
        raise WorkflowInvalid("R3", "workflow.description must be a non-empty string")


_VALID_ENGINES = {"dag", "chat"}


def _r4_engine(doc: "WorkflowDoc") -> None:
    if doc.engine not in _VALID_ENGINES:
        raise WorkflowInvalid(
            "R4",
            f"top-level engine must be one of {sorted(_VALID_ENGINES)} "
            f"(composed delegation lives inside nodes, not at the top level); got {doc.engine!r}",
        )


def _r5_graph_nonempty(doc: "WorkflowDoc") -> None:
    if not doc.graph:
        raise WorkflowInvalid("R5", "orchestration.graph must contain at least one node")


def _r6_unique_ids(doc: "WorkflowDoc") -> None:
    ids: set[str] = set()
    for n in doc.graph:
        nid = n.get("id")
        if not isinstance(nid, str) or not _ID_RE.match(nid):
            raise WorkflowInvalid("R6", f"node id invalid: {nid!r}")
        if nid in ids:
            raise WorkflowInvalid("R6", f"duplicate node id: {nid!r}")
        ids.add(nid)


def _r7_node_types(doc: "WorkflowDoc") -> None:
    for n in doc.graph:
        ntype = n.get("type", "agent")
        if ntype not in NODE_TYPES:
            raise WorkflowInvalid(
                "R7", f"node {n.get('id')!r}: unknown type {ntype!r} (known: {sorted(NODE_TYPES)})"
            )


def _r8_depends_on_refs(doc: "WorkflowDoc") -> None:
    ids = {n["id"] for n in doc.graph}
    for n in doc.graph:
        for dep in n.get("depends_on", []) or []:
            if dep not in ids:
                raise WorkflowInvalid(
                    "R8", f"node {n.get('id')!r}: depends_on references unknown id {dep!r}"
                )


def _r9_no_cycles(doc: "WorkflowDoc") -> None:
    edges = {n["id"]: list(n.get("depends_on", []) or []) for n in doc.graph}
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {nid: WHITE for nid in edges}

    def dfs(node: str, path: list[str]) -> None:
        color[node] = GRAY
        for nb in edges[node]:
            if color[nb] == GRAY:
                cycle = " -> ".join(path + [node, nb])
                raise WorkflowInvalid("R9", f"cycle detected: {cycle}")
            if color[nb] == WHITE:
                dfs(nb, path + [node])
        color[node] = BLACK

    for nid in edges:
        if color[nid] == WHITE:
            dfs(nid, [])


def _r10_per_type_validation(doc: "WorkflowDoc") -> None:
    for n in doc.graph:
        ntype = n.get("type", "agent")
        spec = NODE_TYPES[ntype]
        validator = spec.get("validate")
        if validator is not None:
            try:
                validator(n)
            except WorkflowInvalid:
                raise
            except Exception as e:  # noqa: BLE001 — surface ANY misshape as R10
                raise WorkflowInvalid(
                    "R10", f"node {n.get('id')!r}: per-type validation failed: {e}"
                ) from e


_CHAT_TURN_NODE_TYPES = {"answer", "ask_human"}


def _r11_chat_engine_needs_turn_node(doc: "WorkflowDoc") -> None:
    if doc.engine != "chat":
        return
    if not any(n.get("type") in _CHAT_TURN_NODE_TYPES for n in doc.graph):
        raise WorkflowInvalid(
            "R11",
            "engine 'chat' requires at least one 'answer' or 'ask_human' node — "
            "otherwise the workflow can never actually turn-pause and behaves "
            "exactly like a plain 'dag' workflow, which is a likely authoring mistake",
        )


RULES = (
    _r1_awp_version,
    _r2_name,
    _r3_description,
    _r4_engine,
    _r5_graph_nonempty,
    _r6_unique_ids,
    _r7_node_types,
    _r8_depends_on_refs,
    _r9_no_cycles,
    _r10_per_type_validation,
    _r11_chat_engine_needs_turn_node,
)


def validate(doc: "WorkflowDoc") -> None:
    """Run R1..R10 in order; first failure raises WorkflowInvalid."""
    for rule in RULES:
        rule(doc)
