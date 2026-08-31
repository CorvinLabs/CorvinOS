"""Free-text dependency extraction for the idea-first interview (ADR-0262)."""
from __future__ import annotations

from plugin_builder.classifier import (
    DEPENDENCY_FIELDS,
    extract_dependency_hints,
    problem_statement_from_idea_text,
)


def test_url_in_text_resolves_egress_and_hosts():
    spec, resolved = extract_dependency_hints(
        "It calls api.stripe.com to check payment status."
    )
    assert spec.requires_network_egress is True
    assert spec.egress_hosts == ("api.stripe.com",)
    assert {"requires_network_egress", "egress_hosts"} <= resolved


def test_egress_positive_phrase_without_host_leaves_host_unresolved():
    spec, resolved = extract_dependency_hints("It talks to a third-party API for pricing.")
    assert spec.requires_network_egress is True
    assert "requires_network_egress" in resolved
    assert "egress_hosts" not in resolved


def test_egress_negative_phrase_resolves_both_egress_and_hosts_false():
    spec, resolved = extract_dependency_hints("Runs fully local, no external calls.")
    assert spec.requires_network_egress is False
    assert {"requires_network_egress", "egress_hosts"} <= resolved


def test_hyphenated_auth_keyword_is_matched():
    """Regression: 'API-Key' (hyphen) must match same as 'api key' (space) —
    found live while smoke-testing German idea text."""
    spec, resolved = extract_dependency_hints("Braucht einen API-Key.")
    assert spec.requires_auth is True
    assert "requires_auth" in resolved


def test_auth_negative_phrase_resolves_false():
    spec, resolved = extract_dependency_hints("It's a public API, no authentication needed.")
    assert spec.requires_auth is False
    assert "requires_auth" in resolved


def test_backticked_library_name_resolves():
    spec, resolved = extract_dependency_hints("Uses the `httpx` library for requests.")
    assert spec.external_libraries == ("httpx",)
    assert "external_libraries" in resolved


def test_no_library_phrase_resolves_empty():
    spec, resolved = extract_dependency_hints("No external dependencies needed.")
    assert spec.external_libraries == ()
    assert "external_libraries" in resolved


def test_vague_text_resolves_nothing():
    spec, resolved = extract_dependency_hints("It watches my calendar and reminds me of things.")
    assert resolved == frozenset()
    # Defaults are still a valid (empty) DependencySpec — callers must consult
    # `resolved`, never assume default == confirmed answer.
    assert spec.requires_network_egress is False
    assert spec.requires_auth is False


def test_all_dependency_fields_are_resolvable_at_once():
    text = "Talks to api.example.com, needs an API key, uses `requests`."
    _spec, resolved = extract_dependency_hints(text)
    assert resolved == frozenset(DEPENDENCY_FIELDS)


def test_problem_statement_from_idea_text_carries_full_text_verbatim():
    text = "A plugin that watches my calendar and reminds me of upcoming meetings."
    stmt = problem_statement_from_idea_text(text)
    assert stmt.problem == text
    assert "not specified" in stmt.target_audience
    assert stmt.existing_solutions == ""
