"""corvin_compute — opt-in iterative compute worker plugin (ADR-0013).

See ``docs/decisions/0013-compute-worker-plugin.md`` for the design
contract and ``docs/decisions/0013-implementation-plan.md`` for the
phased rollout.

This package MUST NOT ``import anthropic`` (cost contract; CI lint
enforces it). Future LLM-aware strategies authenticate via
``claude -p`` subprocess — mirror of the Layer-11 dialectic pattern.
"""
from __future__ import annotations

from .version import __version__

__all__ = ["__version__"]
