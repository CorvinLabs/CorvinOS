"""Plugin-Builder — assisted plugin development (ADR-0253).

Interview → auto-classify → generate (Idea/Architecture/ADR/Plan) → scaffold.
Entered via the ``/plugin-builder`` console command (behind the
``plugin_builder_enabled`` feature flag, off by default) or driven directly —
``InterviewSession`` has no opinion about its transport.

This package emits artifacts and never loads them (ADR-0244's constraint,
restated for this tool): a plugin scaffolded here depends only on
``corvin_plugins``, never on ``plugin_builder``. Deleting this package leaves
every previously generated plugin working.
"""
from __future__ import annotations

from .classifier import classify
from .interview import InterviewPhase, InterviewSession
from .models import (
    Classification,
    Constraints,
    DependencySpec,
    PluginIdea,
    PluginKind,
    ProblemStatement,
    Tier,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "classify",
    "InterviewPhase",
    "InterviewSession",
    "Classification",
    "Constraints",
    "DependencySpec",
    "PluginIdea",
    "PluginKind",
    "ProblemStatement",
    "Tier",
]
