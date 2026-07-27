"""Unit tests for the Plugin-Builder scaffold index (ADR-0253).

Console-route-level coverage of the same module lives in
``core/console/tests/test_plugins_route.py::TestScaffoldedPlugins`` — these
tests cover the module directly: ordering, tenant isolation, and the
``MAX_ENTRIES`` bound, none of which the route test exercises.
"""
from __future__ import annotations

import pytest

from plugin_builder import index_store
from plugin_builder.generators.scaffold import ScaffoldResult
from plugin_builder.models import (
    Classification,
    Constraints,
    DependencySpec,
    PluginIdea,
    PluginKind,
    ProblemStatement,
    Tier,
)


@pytest.fixture(autouse=True)
def _corvin_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin_home"))


def _idea(name: str) -> PluginIdea:
    return PluginIdea(
        plugin_name=name,
        problem=ProblemStatement("problem", "audience", "none", "mvp"),
        dependencies=DependencySpec(),
        constraints=Constraints(),
    )


def _result(plugin_id: str) -> ScaffoldResult:
    classification = Classification(
        kind=PluginKind.PROVIDER, tier=Tier.B_COMPUTE, confidence=1.0,
        rationale="test", plugin_type="data_connector",
    )
    return ScaffoldResult(
        dest=None, plugin_id=plugin_id, classification=classification,
        doc_files=(), scaffold_files=(), warnings=(),
    )


def test_empty_before_any_scaffold():
    assert index_store.list_scaffolds("_default") == []


def test_record_then_list_roundtrip():
    idea = _idea("Postgres Connector")
    result = _result("community.postgres-connector")
    index_store.record("_default", idea, result)

    entries = index_store.list_scaffolds("_default")
    assert len(entries) == 1
    assert entries[0]["plugin_id"] == "community.postgres-connector"
    assert entries[0]["display_name"] == "Postgres Connector"
    assert entries[0]["kind"] == "provider"
    assert entries[0]["tier"] == "B"
    assert entries[0]["plugin_type"] == "data_connector"
    assert isinstance(entries[0]["created_at"], float)


def test_multiple_records_preserve_insertion_order():
    for name in ("First", "Second", "Third"):
        index_store.record("_default", _idea(name), _result(f"community.{name.lower()}"))
    names = [e["display_name"] for e in index_store.list_scaffolds("_default")]
    assert names == ["First", "Second", "Third"]


def test_tenants_do_not_see_each_others_scaffolds():
    index_store.record("tenant-a", _idea("A-Plugin"), _result("community.a-plugin"))
    index_store.record("tenant-b", _idea("B-Plugin"), _result("community.b-plugin"))

    a = index_store.list_scaffolds("tenant-a")
    b = index_store.list_scaffolds("tenant-b")
    assert [e["display_name"] for e in a] == ["A-Plugin"]
    assert [e["display_name"] for e in b] == ["B-Plugin"]


def test_index_survives_a_corrupt_file():
    path = index_store._index_path("_default")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json{{{", encoding="utf-8")
    assert index_store.list_scaffolds("_default") == []
    # And recording afterwards overwrites the corruption rather than raising.
    index_store.record("_default", _idea("Recovered"), _result("community.recovered"))
    assert len(index_store.list_scaffolds("_default")) == 1


def test_max_entries_bound_keeps_the_most_recent():
    for i in range(index_store.MAX_ENTRIES + 5):
        index_store.record("_default", _idea(f"P{i}"), _result(f"community.p{i}"))
    entries = index_store.list_scaffolds("_default")
    assert len(entries) == index_store.MAX_ENTRIES
    assert entries[-1]["display_name"] == f"P{index_store.MAX_ENTRIES + 4}"
    assert entries[0]["display_name"] == "P5"
