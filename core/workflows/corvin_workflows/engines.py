"""WorkerEngine contract + a deterministic stub for tests.

The runtime never imports anthropic / openai / google-cloud — engines are
pluggable adapters. A production engine wraps a real LLM CLI (claude -p,
codex exec, opencode run); the stub returns canned responses keyed by
(agent_name, iteration). That lets the E2E test prove the orchestration
shape without spending tokens or wall-clock.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


@dataclass
class EngineCall:
    """One spawn against an engine.

    The dispatcher gives the engine the agent name, the merged inputs/state
    slice it should see, and a free-form instructions string the manager (or
    the static node) supplied. The engine returns an arbitrary JSON-shaped
    dict; the runner stores it under the node's id.
    """

    agent: str
    instructions: str
    inputs: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    iteration: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class WorkerEngine(Protocol):
    """The single contract every backend implements."""

    name: str

    def spawn(self, call: EngineCall) -> dict[str, Any]:  # pragma: no cover - protocol
        ...


@dataclass
class StubEngine:
    """Deterministic test engine.

    Lookup order for each call:
      1. exact match on (agent, iteration) in `responses`
      2. exact match on agent in `responses`
      3. callable fallback `default(call) -> dict`

    The engine records every call into `history` so tests can assert order.
    """

    name: str = "stub"
    responses: dict[Any, dict[str, Any]] = field(default_factory=dict)
    default: Callable[[EngineCall], dict[str, Any]] | None = None
    history: list[EngineCall] = field(default_factory=list)

    def spawn(self, call: EngineCall) -> dict[str, Any]:
        self.history.append(call)
        key = (call.agent, call.iteration)
        if key in self.responses:
            return dict(self.responses[key])
        if call.agent in self.responses:
            return dict(self.responses[call.agent])
        if self.default is not None:
            return self.default(call)
        raise KeyError(
            f"StubEngine: no canned response for agent={call.agent!r} iter={call.iteration} "
            f"and no default callable set"
        )
