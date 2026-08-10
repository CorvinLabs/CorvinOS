"""In-process stage registry (ADR-0280).

First-party (`trust=builtin`) stages self-register at import. Until the P-G
subprocess sandbox exists (ADR-0285), ONLY `origin=builtin` may register/run
in-process — a non-builtin registration is refused (there is no isolation for it).
"""
from __future__ import annotations

from .base import ContextStage

_STAGES: dict[str, ContextStage] = {}


def register_stage(stage: ContextStage) -> None:
    """Register a stage instance by its id. Builtin-only until P-G (ADR-0285 R2)."""
    if getattr(stage, "trust", "builtin") != "builtin":
        raise ValueError(
            f"refusing non-builtin stage {stage.id!r}: no in-process isolation "
            "exists yet (ADR-0285 P-G)")
    _STAGES[stage.id] = stage


def get_stage(stage_id: str) -> "ContextStage | None":
    return _STAGES.get(stage_id)


def known_ids() -> list:
    return sorted(_STAGES)


def all_specs() -> list:
    """(id, requires, effect, trust) for each registered stage — for the editor."""
    return [{"id": s.id, "requires": list(s.requires), "effect": s.effect,
             "trust": s.trust} for s in _STAGES.values()]
