"""Strategy Protocol (ADR-0013 §D).

Strategies are pure-Python modules with a fixed contract. The worker
process executes strategy bodies in-process (not in bwrap) — they are
operator-curated code, equivalent in trust to the audit chain or
path-gate hook.

The skill linter (``operator/skill-forge/skill_forge/linter.py``) rejects
strategies that import network / subprocess modules. The cost contract
(``corvin_compute/`` MUST NOT import anthropic) is enforced via AST
walk in the Phase 13.1 test.
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

ParamSet = Mapping[str, Any]


@runtime_checkable
class Strategy(Protocol):
    """Iteration strategy contract.

    All four methods must be present; ``update`` may be a no-op.
    """

    name: str

    def suggest_batch(self, history: list, n: int) -> list[ParamSet]:
        """Return up to ``n`` parameter sets to evaluate next.

        Returning ``[]`` signals the strategy can't suggest more
        points (e.g. grid exhausted); the driver wraps this into a
        terminal state.
        """
        ...

    def update(self, history: list, new_results: list) -> None:
        """Incorporate the results of the last batch."""
        ...

    def should_stop(self, history: list) -> tuple[bool, str]:
        """Return ``(True, reason)`` if the strategy itself wants to stop."""
        ...
