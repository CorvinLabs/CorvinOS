"""In-process stage registry (ADR-0280 / ADR-0289).

First-party (`trust=builtin`) stages self-register at import and run IN-process.

A **community** stage never does. `register_community_stage()` takes a file path
and stores a `SandboxedStage` PROXY — the community object itself is never
imported here, so there is no code path on which its `run` executes in this
process (ADR-0289). Before P-G there was no isolation at all, so `register_stage`
simply refused anything non-builtin; that refusal survives for the direct entry
point, because handing this function a live foreign object is still the mistake
it always was.
"""
from __future__ import annotations

from pathlib import Path

from .base import ContextStage

_STAGES: dict[str, ContextStage] = {}


def register_stage(stage: ContextStage) -> None:
    """Register a first-party stage INSTANCE by its id.

    Refuses anything non-builtin: a live foreign object handed to this function
    would run in-process, which is exactly what the sandbox exists to prevent.
    Community stages go through :func:`register_community_stage` instead — they
    are identified by a PATH, never by an object."""
    if getattr(stage, "trust", "builtin") != "builtin":
        raise ValueError(
            f"refusing non-builtin stage {stage.id!r} on the in-process path: "
            "community stages must be registered by path via "
            "register_community_stage() so they run in the P-G sandbox "
            "(ADR-0289)")
    _STAGES[stage.id] = stage


def register_community_stage(stage_id: str, module_path, *,
                             requires: tuple = ()) -> bool:
    """Register a community stage by FILE PATH, wrapped in the sandbox proxy.

    Returns True when registered, False when this host has no isolation
    (no bwrap, no Docker) — there the stage stays unavailable and the palette
    remains builtin-only, exactly as before P-G (ADR-0284 R2). Fail-closed: a
    host that cannot isolate does not run community code.
    """
    from .sandbox import SandboxedStage, sandbox_available  # noqa: PLC0415

    if not sandbox_available():
        return False
    p = Path(module_path)
    if not p.is_file():
        return False
    _STAGES[str(stage_id)] = SandboxedStage(stage_id, p, requires=requires)
    return True


def unregister_stage(stage_id: str) -> bool:
    """Drop a stage from the registry. Returns True when one was removed."""
    return _STAGES.pop(str(stage_id), None) is not None


def builtin_ids() -> list:
    """Ids of the vetted first-party stages — the grade gate's always-eligible
    set (ADR-0285 ``is_default_eligible``)."""
    return sorted(sid for sid, s in _STAGES.items()
                  if getattr(s, "trust", "builtin") == "builtin")


def get_stage(stage_id: str) -> "ContextStage | None":
    return _STAGES.get(stage_id)


def known_ids() -> list:
    return sorted(_STAGES)


def all_specs() -> list:
    """(id, requires, effect, trust) for each registered stage — for the editor."""
    return [{"id": s.id, "requires": list(s.requires), "effect": s.effect,
             "trust": s.trust} for s in _STAGES.values()]
