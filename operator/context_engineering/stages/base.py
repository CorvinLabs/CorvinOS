"""ContextStage contract + the ContextBundle (ADR-0280 / CONCEPT-0006 §8–§10).

A stage transforms context → richer context: `run(bundle, ctx) -> (bundle,
telemetry)`. The `ContextBundle` is a THIN wrapper that OWNS the `RichTaskBrief`
as the single source of truth for brief data (CONCEPT §10(3)); `text_sections`
and the `scratch` projections are derived from it, while `scratch` also carries
explicit typed HANDOFF slots (e.g. `needs`) a producer writes and a consumer
reads. `tools_to_bind`/`skills_to_bind`/`synthesised_prompt` are the P-B/P-C
channels — empty/None in P-A.

Stages declare `requires` (dependency edges — the runner topo-sorts; memory is
the root that constructs the brief) and `effect` (`pure` runs pre-gate;
`egress`/`forge` run post-gate, P-C/P-D) and `trust` (`builtin` only runs
in-process until the P-G sandbox exists, ADR-0285).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# scratch key table (ADR-0280 R2(d)): producer → key → kind → consumer.
# `projection` = derived read-only view of the brief; `handoff` = producer-writes/
# consumer-reads slot (not a projection of a task brief).
SCRATCH_KEYS: dict[str, dict[str, str]] = {
    "memory_matches": {"producer": "memory", "kind": "projection", "consumer": "approach_synthesis,blocker_id"},
    "related_decisions": {"producer": "graph", "kind": "projection", "consumer": "skill,approach_synthesis,blocker_id"},
    "recommended_skills": {"producer": "skill", "kind": "projection", "consumer": "approach_synthesis"},
    "needs": {"producer": "llm_synthesis", "kind": "handoff", "consumer": "toolforge,skillforge"},
}


@dataclass
class ContextBundle:
    """In-flight context for one turn. `brief` (RichTaskBrief) is the SSOT."""
    task: str
    brief: Any = None                              # RichTaskBrief, built by memory
    text_sections: list = field(default_factory=list)   # derived at the boundary
    tools_to_bind: list = field(default_factory=list)   # P-B (ForgeToolRef)
    skills_to_bind: list = field(default_factory=list)  # P-B (SkillRef)
    synthesised_prompt: "str | None" = None             # P-C
    scratch: dict = field(default_factory=dict)         # projections + handoff slots


@dataclass
class StageCtx:
    """Read-only context handed to every stage."""
    tenant_id: str = "_default"
    session_id: str = ""
    workdir: Any = None
    config: dict = field(default_factory=dict)     # this stage's per-stage config
    task_obj: Any = None                           # the _task_adapter view


@dataclass
class StageTelemetry:
    """What a stage reports — mirrors the ADR-0278 Decision-Record per-stage shape."""
    stage: str
    status: str = "ok"                             # ok | failed | not_run | skipped
    confidence_tier: "str | None" = None
    duration_ms: "float | None" = None
    sources: list = field(default_factory=list)    # [{id, score}]
    error: "str | None" = None
    reason: "str | None" = None

    def to_trace(self) -> dict:
        d: dict[str, Any] = {"stage": self.stage, "status": self.status}
        if self.confidence_tier is not None:
            d["confidence_tier"] = self.confidence_tier
        if self.duration_ms is not None:
            d["duration_ms"] = self.duration_ms
        if self.sources:
            d["sources"] = self.sources
        if self.error:
            d["error"] = self.error
        if self.reason:
            d["reason"] = self.reason
        return d


@runtime_checkable
class ContextStage(Protocol):
    id: str
    requires: tuple            # stage ids that must run before this one
    effect: str                # "pure" | "egress" | "forge"
    trust: str                 # "builtin" | "community"

    def run(self, bundle: ContextBundle, ctx: StageCtx) -> "tuple[ContextBundle, StageTelemetry]":
        ...
