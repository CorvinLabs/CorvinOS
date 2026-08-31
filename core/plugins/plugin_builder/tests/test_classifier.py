"""Classifier tests (ADR-0253 Phase 2)."""
from __future__ import annotations

from plugin_builder import classifier
from plugin_builder.classifier import classify
from plugin_builder.models import (
    Constraints,
    DependencySpec,
    PluginIdea,
    PluginKind,
    ProblemStatement,
)


def _idea(problem: str, *, name: str = "Test Plugin", scope: str = "",
          egress: bool = False) -> PluginIdea:
    return PluginIdea(
        plugin_name=name,
        problem=ProblemStatement(problem, "someone", "none", "mvp"),
        dependencies=DependencySpec(requires_network_egress=egress),
        constraints=Constraints(scope_notes=scope),
    )


def test_mcp_server_classification():
    c = classify(_idea("I want to build a new MCP server exposing tools"))
    assert c.kind == PluginKind.MCP_SERVER
    assert c.tier.value == "C"


def test_skill_classification():
    c = classify(_idea("Just a markdown skill with prompt only instructions, no code"))
    assert c.kind == PluginKind.SKILL
    assert c.tier.value == "A"


def test_hook_classification_picks_extension_point():
    c = classify(_idea("A hook to override model selection for one step"))
    assert c.kind == PluginKind.HOOK
    assert c.tier.value == "B"
    assert c.extension_point == "engine.model_selection"


def test_provider_classification_postgres_maps_to_data_connector():
    c = classify(_idea("Query a Postgres database directly from a turn"))
    assert c.kind == PluginKind.PROVIDER
    assert c.plugin_type == "data_connector"
    # data_connector has no shipped template and is unconsumed today — both
    # should surface as risk flags so an author sees them before writing code.
    assert any("no shipped scaffold template" in f for f in c.risk_flags)
    assert any("NOTHING currently invokes it" in f for f in c.risk_flags)


def test_provider_classification_notifications():
    c = classify(_idea("Send push notifications and email alerts to users"))
    assert c.kind == PluginKind.PROVIDER
    assert c.plugin_type == "notification_backend"


def test_provider_classification_router_is_consumed_no_dead_flag():
    c = classify(_idea("A custom router that decides persona dispatch"))
    assert c.kind == PluginKind.PROVIDER
    assert c.plugin_type == "router_backend"
    assert not any("NOTHING currently invokes it" in f for f in c.risk_flags)


def test_integration_fallback_on_network_egress_with_no_keyword_match():
    c = classify(_idea("Do a thing with an external partner system", egress=True))
    assert c.kind == PluginKind.INTEGRATION


def test_custom_fallback_when_nothing_matches():
    c = classify(_idea("xyzzy plugh quux"))
    assert c.kind == PluginKind.CUSTOM
    assert c.confidence < 0.5
    assert any("maintainer conversation" in f for f in c.risk_flags)


def test_tier_b_and_c_always_carry_license_risk_flag():
    c_b = classify(_idea("A database connector for reporting"))
    c_c = classify(_idea("A standalone MCP server for reporting tools"))
    assert any("executable layer" in f for f in c_b.risk_flags)
    assert any("executable layer" in f for f in c_c.risk_flags)


def test_deterministic_same_input_same_output():
    idea = _idea("Recall memory and long-term memory storage via vector store")
    assert classify(idea) == classify(idea)


def test_degrades_gracefully_without_corvin_plugins(monkeypatch):
    monkeypatch.setattr(classifier, "_REGISTRY_AVAILABLE", False)
    monkeypatch.setattr(classifier, "KNOWN_PLUGIN_TYPES", frozenset())
    c = classify(_idea("Query a Postgres database directly from a turn"))
    assert c.kind == PluginKind.PROVIDER
    assert c.plugin_type == "data_connector"
    assert any("not importable" in f for f in c.risk_flags)


def test_never_raises_on_empty_idea():
    c = classify(_idea(""))
    assert c.kind == PluginKind.CUSTOM
