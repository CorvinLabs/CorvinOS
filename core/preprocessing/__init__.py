"""Preprocessing pipeline — hooks that run before LLM turns (ADR-0268 Phase 2)."""
from __future__ import annotations

from .hook_registry import (
    HookDefinition,
    HookRegistry,
    PreprocessContext,
)

__all__ = [
    "HookDefinition",
    "HookRegistry",
    "PreprocessContext",
]
