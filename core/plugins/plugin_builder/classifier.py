"""Auto-classification: interview answers → :class:`~.models.Classification`
(ADR-0253 Phase 2).

Keyword-scored, not ML — the input is a handful of short free-text answers, and
a transparent scoring table beats an opaque model for a decision the user is
about to review and can dispute ("why did you call this a Hook?"). Every
:class:`~.models.Classification` carries a ``rationale`` string that names the
matched keywords for exactly that reason.

**No second taxonomy.** For ``PluginKind.PROVIDER`` the classifier resolves to a
real ``corvin_plugins.protocol.KNOWN_PLUGIN_TYPES`` entry — never a name it
invented — because ADR-0233/CLAUDE.md forbid a parallel plugin-type vocabulary.
If ``corvin_plugins`` is not importable (this module is usable standalone, e.g.
in a lightweight test environment) classification still runs; only the
type-specific enrichment (dead-surface warning, buildable-template pointer) is
skipped, and the ``Classification`` is marked with a ``risk_flag`` saying so.
"""
from __future__ import annotations

from .models import Classification, PluginIdea, PluginKind, Tier

try:
    from corvin_plugins.protocol import KNOWN_PLUGIN_TYPES
    from corvin_plugins.surface_map import surface_for

    _REGISTRY_AVAILABLE = True
except ImportError:  # pragma: no cover — exercised by test_classifier_no_registry
    KNOWN_PLUGIN_TYPES = frozenset()  # type: ignore[assignment]
    surface_for = None  # type: ignore[assignment]
    _REGISTRY_AVAILABLE = False

try:
    from corvin_plugins.extension_points import KNOWN_EXTENSION_POINTS
except ImportError:  # pragma: no cover
    KNOWN_EXTENSION_POINTS = frozenset()  # type: ignore[assignment]


# ── Keyword tables ───────────────────────────────────────────────────────────
#
# Ordered by specificity, not alphabetically: MCP/Skill/Hook are checked before
# the broader Provider/Integration buckets so a plugin idea that mentions both
# "MCP server" and "database" classifies on the more specific, more consequential
# signal (an MCP server is Tier C; a bare database connector is Tier B).

_KIND_KEYWORDS: dict[PluginKind, tuple[str, ...]] = {
    PluginKind.MCP_SERVER: (
        "mcp server", "mcp-server", "model context protocol", "subprocess",
        "protocol server", "standalone server", "tool server", "new mcp tool",
        "expose tools", "mcp",
    ),
    PluginKind.SKILL: (
        "skill", "markdown", "prompt only", "prompt-only", "instructions only",
        "no code", "no-code", "checklist", "playbook", "guidance",
    ),
    PluginKind.HOOK: (
        "hook", "override", "intercept", "extension point", "customi",
        "replace the default", "routing decision", "model selection",
        "workflow gate", "engine selection",
    ),
}

#: Provider bucket: keyword → KNOWN_PLUGIN_TYPES entry. Checked after the three
#: buckets above so a "database notification hook" still lands on HOOK, not
#: PROVIDER — the more structurally consequential classification wins ties.
_PLUGIN_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "data_connector": (
        "database", "postgres", "postgresql", "mysql", "sqlite", "warehouse",
        "data source", "connector", "sql", "snowflake", "bigquery",
    ),
    "notification_backend": (
        "notify", "notification", "alert", "email", "sms", "push message",
        "pager",
    ),
    "audit_backend": (
        "audit", "compliance sink", "siem", "log export", "audit trail",
    ),
    "recall_backend": (
        "recall", "memory store", "vector store", "embedding", "long-term memory",
    ),
    "summary_provider": (
        "summar", "condense", "digest",
    ),
    "router_backend": (
        "route", "router", "persona selection", "dispatch",
    ),
    "user_backend": (
        "auth", "authentic", "login", "sso", "okta", "identity provider",
    ),
    "compute_engine": (
        "compute job", "gpu", "batch job", "compute worker",
    ),
    "worker_engine": (
        "worker engine", "llm engine", "inference engine",
    ),
    "bridge_channel": (
        "messenger", "bridge channel", "discord", "slack", "whatsapp",
        "telegram", "signal", "teams",
    ),
    "stt_provider": (
        "speech to text", "speech-to-text", "stt", "transcribe", "transcription",
        "voice input",
    ),
}

_EXTENSION_POINT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "engine.model_selection": ("model selection", "choose the model", "pick a model"),
    "engine.engine_selection": ("engine selection", "choose the engine", "which engine"),
    "delegation.route_selection_policy": (
        "delegation route", "native or acs", "route selection", "delegation policy",
    ),
    "workflow.workflow_gate": ("workflow gate", "allow the workflow", "gate a run"),
}

#: Tier assigned per kind before any provider-type-specific override.
_KIND_TIER: dict[PluginKind, Tier] = {
    PluginKind.MCP_SERVER: Tier.C_PROTOCOL,
    PluginKind.SKILL: Tier.A_PROMPT,
    PluginKind.HOOK: Tier.B_COMPUTE,
    PluginKind.PROVIDER: Tier.B_COMPUTE,
    PluginKind.INTEGRATION: Tier.B_COMPUTE,
    PluginKind.CUSTOM: Tier.B_COMPUTE,
}


def _haystack(idea: PluginIdea) -> str:
    """Every free-text field, lower-cased, joined for substring scoring."""
    parts = [
        idea.plugin_name,
        idea.problem.problem,
        idea.problem.target_audience,
        idea.problem.existing_solutions,
        idea.problem.time_scope,
        idea.constraints.scope_notes,
        *idea.raw_answers.values(),
    ]
    return " \n ".join(p for p in parts if p).lower()


def _score(haystack: str, keywords: tuple[str, ...]) -> tuple[int, list[str]]:
    hits = [kw for kw in keywords if kw in haystack]
    return len(hits), hits


def _best_plugin_type(haystack: str) -> tuple[str | None, list[str]]:
    best_type: str | None = None
    best_hits: list[str] = []
    best_score = 0
    for plugin_type, keywords in _PLUGIN_TYPE_KEYWORDS.items():
        score, hits = _score(haystack, keywords)
        if score > best_score:
            best_score, best_type, best_hits = score, plugin_type, hits
    return best_type, best_hits


def _best_extension_point(haystack: str) -> tuple[str | None, list[str]]:
    best_point: str | None = None
    best_hits: list[str] = []
    best_score = 0
    for point, keywords in _EXTENSION_POINT_KEYWORDS.items():
        score, hits = _score(haystack, keywords)
        if score > best_score:
            best_score, best_point, best_hits = score, point, hits
    return best_point, best_hits


def _tier_risk_flags(kind: PluginKind, tier: Tier) -> list[str]:
    flags: list[str] = []
    if tier in (Tier.B_COMPUTE, Tier.C_PROTOCOL):
        flags.append(
            "Tier " + tier.value + " is an executable layer (ADR-0156): the "
            "free license tier allows only ONE active executable layer at a "
            "time. Confirm this is your intended slot before building."
        )
    if kind == PluginKind.CUSTOM:
        flags.append(
            "No clear match against the known taxonomy — this needs a "
            "maintainer conversation before implementation (ADR-0244 only "
            "recognizes 11 provider plugin_type values plus MCP/Skill/Hook)."
        )
    return flags


def classify(idea: PluginIdea) -> Classification:
    """Classify ``idea`` into a :class:`~.models.Classification`.

    Deterministic and side-effect-free: same idea in, same classification out.
    Never raises on ambiguous or empty input — worst case is
    ``PluginKind.CUSTOM`` with low confidence and a risk flag, which is exactly
    the "surface risk early" behaviour ADR-0253 asks for, not an exception the
    interview flow would have to catch.
    """
    haystack = _haystack(idea)

    scored: list[tuple[PluginKind, int, list[str]]] = []
    for kind, keywords in _KIND_KEYWORDS.items():
        score, hits = _score(haystack, keywords)
        if score:
            scored.append((kind, score, hits))

    plugin_type, type_hits = _best_plugin_type(haystack)
    if plugin_type is not None:
        scored.append((PluginKind.PROVIDER, len(type_hits), type_hits))

    if not scored:
        if idea.dependencies.requires_network_egress:
            kind, hits, rationale_hits = PluginKind.INTEGRATION, [], [
                "no keyword match, but the interview recorded network egress "
                "— defaulting to Integration rather than Custom",
            ]
        else:
            kind, hits, rationale_hits = PluginKind.CUSTOM, [], [
                "no keyword in the interview answers matched any known kind",
            ]
        confidence = 0.2
        extension_point = None
    else:
        scored.sort(key=lambda t: t[1], reverse=True)
        kind, top_score, hits = scored[0]
        total = sum(s for _, s, _ in scored)
        confidence = round(top_score / total, 2) if total else 0.2
        # A single, unambiguous hit is still worth surfacing with real
        # confidence — round-tripping through `top/total` alone would make one
        # clean keyword match (e.g. just "mcp server") look as shaky as a
        # three-way tie, which is the opposite of what happened.
        if len(scored) == 1:
            confidence = max(confidence, 0.75)
        rationale_hits = [f"matched {kw!r}" for kw in hits] or ["no keyword hits"]
        extension_point = None
        if kind == PluginKind.HOOK:
            extension_point, ep_hits = _best_extension_point(haystack)
            if ep_hits:
                rationale_hits += [f"extension point cue {kw!r}" for kw in ep_hits]

    if kind == PluginKind.PROVIDER and plugin_type is not None:
        if _REGISTRY_AVAILABLE and plugin_type not in KNOWN_PLUGIN_TYPES:
            # The keyword table and the live registry drifted — treat it as
            # unclassifiable rather than emit a plugin_type the registry will
            # reject at manifest time (PluginRecord.__post_init__).
            plugin_type = None
            kind = PluginKind.CUSTOM

    risk_flags = _tier_risk_flags(kind, _KIND_TIER[kind])
    if kind == PluginKind.PROVIDER and plugin_type is not None and _REGISTRY_AVAILABLE:
        surface = surface_for(plugin_type)
        if not surface.consumed:
            risk_flags = (
                f"'{plugin_type}' registers but NOTHING currently invokes it "
                f"(ADR-0245): {surface.dead_reason}",
                *risk_flags,
            )
        if surface.template is None:
            risk_flags = (
                f"'{plugin_type}' has no shipped scaffold template yet — the "
                "generated plugin will use the Plugin-Builder's own generic "
                "provider boilerplate instead of `corvin plugin new`.",
                *risk_flags,
            )
    elif kind == PluginKind.PROVIDER and not _REGISTRY_AVAILABLE:
        risk_flags = (
            "corvin_plugins is not importable in this environment — plugin_type "
            "could not be validated against the live registry.",
            *risk_flags,
        )

    rationale = f"Classified as {kind.value} — " + "; ".join(rationale_hits)

    return Classification(
        kind=kind,
        tier=_KIND_TIER[kind],
        confidence=confidence,
        rationale=rationale,
        plugin_type=plugin_type if kind == PluginKind.PROVIDER else None,
        extension_point=extension_point,
        risk_flags=tuple(risk_flags),
    )


__all__ = ["classify"]
