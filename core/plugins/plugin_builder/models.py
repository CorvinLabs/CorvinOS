"""Data models shared by the interview, classifier and generators (ADR-0253).

Plain, dependency-free dataclasses. Nothing here imports ``corvin_plugins`` —
that dependency starts in ``generators/scaffold.py``, the one module whose job
is to hand a finished idea to the real registry-facing tooling. Keeping the
interview/classifier layer free of it means those two can be tested (and
reasoned about) without a full CorvinOS install.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class PluginKind(str, Enum):
    """The six-way taxonomy ADR-0253 interviews toward.

    This is a classification LABEL, not a second plugin-type registry — for
    ``PROVIDER`` it still resolves to one of ``corvin_plugins.protocol.
    KNOWN_PLUGIN_TYPES`` (see :attr:`Classification.plugin_type`). MCP_SERVER,
    SKILL and HOOK do not have a ``KNOWN_PLUGIN_TYPES`` entry at all — they are
    extension surfaces that live outside the plugin registry (an MCP subprocess,
    a SkillForge markdown skill, an ``extension_points`` hook).
    """

    MCP_SERVER = "mcp_server"
    SKILL = "skill"
    HOOK = "hook"
    PROVIDER = "provider"
    INTEGRATION = "integration"
    CUSTOM = "custom"


class Tier(str, Enum):
    """ADR-0156 Custom Layer System vocabulary — repo-wide canonical meaning of
    "Tier". Reused here rather than invented fresh, per CLAUDE.md's rule that
    "tier" means exactly one thing in this codebase.

    A — prompt only (Markdown, no executable code).
    B — sandboxed compute (bwrap-isolated Python/shell, L10 path-gate applies).
    C — subprocess protocol (an out-of-process MCP server).
    """

    A_PROMPT = "A"
    B_COMPUTE = "B"
    C_PROTOCOL = "C"


@dataclass(frozen=True)
class ProblemStatement:
    """Phase 1 — Problem Understanding."""

    problem: str
    target_audience: str
    existing_solutions: str
    time_scope: str


@dataclass(frozen=True)
class DependencySpec:
    """Phase 3 — the dependency half of Dependencies & Constraints."""

    external_libraries: tuple[str, ...] = ()
    requires_auth: bool = False
    requires_network_egress: bool = False
    egress_hosts: tuple[str, ...] = ()


@dataclass(frozen=True)
class Constraints:
    """Phase 3 — the constraint half of Dependencies & Constraints."""

    platform_constraints: str = ""
    mvp_only: bool = True
    scope_notes: str = ""


@dataclass(frozen=True)
class PluginIdea:
    """The complete interview record (Phases 1 + 3), independent of Phase 2's
    classification. ``plugin_name`` is a free-text display name — validated,
    slug-shaped ``plugin_id`` derivation happens in the generators, not here,
    because that derivation needs to be *lossy and reviewable* rather than a
    property of the interview record itself."""

    plugin_name: str
    problem: ProblemStatement
    dependencies: DependencySpec
    constraints: Constraints
    #: Verbatim answer text keyed by question id, for the generators to quote
    #: and for a human reviewer to audit the classifier's input.
    raw_answers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Classification:
    """Phase 2 output — produced by :func:`classifier.classify`.

    ``plugin_type`` is set only for ``kind == PluginKind.PROVIDER`` and, when
    set, is always a member of ``corvin_plugins.protocol.KNOWN_PLUGIN_TYPES``
    (checked by the classifier, not assumed by callers).
    """

    kind: PluginKind
    tier: Tier
    confidence: float
    rationale: str
    plugin_type: str | None = None
    extension_point: str | None = None
    risk_flags: tuple[str, ...] = ()


__all__ = [
    "PluginKind",
    "Tier",
    "ProblemStatement",
    "DependencySpec",
    "Constraints",
    "PluginIdea",
    "Classification",
]
