"""Tests for the semantic-context-retriever builtin plugin (ADR-0598).

Covers:
  1. plugin.yaml passes the ADR-0247 validation gate (validate_manifest_file).
  2. BM25 select() ranks a query-overlapping candidate above a non-overlapping one.
  3. select() only ever narrows/reorders — never adds a non-candidate.
  4. E2E: with the provider set_active (via the real registry + on_load), the
     ADR-0599 CEL memory seam (operator/context_engineering/stages/memory.py)
     applies the ranking; once released it is a byte-identical passthrough.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parents[1]
_REPO = Path(__file__).resolve().parents[6]

# ── Load the hyphenated-dir provider module by path ──────────────────────────
_spec = importlib.util.spec_from_file_location(
    "semantic_context_retriever_provider", _PLUGIN_DIR / "provider.py"
)
provider_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(provider_mod)
SemanticContextRetriever = provider_mod.SemanticContextRetriever


# ── 1. Manifest gate ─────────────────────────────────────────────────────────
def test_plugin_yaml_passes_adr0247_gate():
    from corvin_plugins.validation import validate_manifest_file

    report = validate_manifest_file(_PLUGIN_DIR / "plugin.yaml")
    assert report.ok, f"manifest rejected: {[str(f) for f in report.errors]}"
    # context_retriever is a consumed surface (ADR-0599), so no unconsumed warning.
    codes = {f.code for f in report.warnings}
    assert "type.unconsumed" not in codes


# ── Candidate helper ─────────────────────────────────────────────────────────
def _memory_match(title: str, preview: str, *, filename: str = "m.md"):
    from context_engineering.rich_task_brief import MemoryMatch

    return MemoryMatch(
        filename=filename,
        title=title,
        relevance_score=0.5,
        source_file=str(_PLUGIN_DIR / filename),
        timestamp=datetime.now(timezone.utc),
        content_preview=preview,
    )


@pytest.fixture(autouse=True)
def _operator_on_path():
    p = str(_REPO / "operator")
    if p not in sys.path:
        sys.path.insert(0, p)
    yield


# ── 2. BM25 ranks overlap above non-overlap ──────────────────────────────────
def test_select_ranks_query_overlap_first():
    r = SemanticContextRetriever()
    no_overlap = _memory_match("Cooking recipes", "how to bake sourdough bread")
    overlap = _memory_match(
        "Database migration guide", "steps to migrate the database schema safely"
    )
    # Original order puts the irrelevant one first — BM25 must flip it.
    candidates = [no_overlap, overlap]
    selected = r.select("how do I run a database migration", candidates)
    assert selected[0] is overlap
    assert selected[1] is no_overlap


# ── 3. Never additive ────────────────────────────────────────────────────────
def test_select_never_adds_only_subsets():
    r = SemanticContextRetriever()
    candidates = [
        _memory_match("Alpha", "unrelated alpha text"),
        _memory_match("Beta database", "database schema and migration notes"),
        _memory_match("Gamma", "gamma unrelated"),
    ]
    # With a budget, the result narrows; every returned item must be an input.
    selected = r.select("database migration", candidates, budget=2)
    assert len(selected) == 2
    assert all(any(s is c for c in candidates) for s in selected)
    # No budget: reorder-only, same length, same set (identity-preserved).
    reordered = r.select("database migration", candidates)
    assert len(reordered) == len(candidates)
    assert all(any(s is c for c in candidates) for s in reordered)


def test_select_never_raises_on_bad_query():
    r = SemanticContextRetriever()
    candidates = [_memory_match("X", "y")]
    # Empty / punctuation-only query has no tokens → passthrough, no raise.
    assert r.select("", candidates) is candidates
    assert r.select("!!! ???", candidates) is candidates
    assert r.select("q", []) == []


# ── 4. E2E through the real registry + the CEL memory seam ───────────────────
def _build_ctx():
    from corvin_plugins.protocol import PluginContext
    from corvin_plugins.providers import context_retriever

    return PluginContext(
        plugin_id="semantic-context-retriever",
        tenant_id="_default",
        corvin_home=_PLUGIN_DIR,
        config={},
        audit_emit=lambda _e, _d: None,
        context_retriever_registry=context_retriever._registry,
    )


def test_e2e_seam_uses_active_provider_then_passthrough():
    from corvin_plugins.providers import context_retriever
    from corvin_plugins.registry import PluginRegistry
    from context_engineering.rich_task_brief import (
        MemoryContext,
        RichTaskBrief,
    )
    from context_engineering.stages.memory import _apply_context_retriever

    # Ensure a clean slot for this test.
    context_retriever.clear()

    no_overlap = _memory_match("Cooking recipes", "how to bake sourdough bread")
    overlap = _memory_match(
        "Database migration guide", "steps to migrate the database schema safely"
    )

    def _fresh_brief():
        return RichTaskBrief(
            raw_input="how do I run a database migration",
            enriched_task=object(),
            memory_context=MemoryContext(matches=[no_overlap, overlap]),
            timestamp=datetime.now(timezone.utc),
        )

    class _Ctx:
        tenant_id = "_default"

    # ---- Inactive (bundled passthrough): seam is a no-op, order preserved. ----
    brief = _fresh_brief()
    _apply_context_retriever(brief, _Ctx())
    assert brief.memory_context.matches[0] is no_overlap  # unchanged

    # ---- Active: register through the REAL registry (sets loading ctx +
    #      calls on_load → set_active into the module-level registry). ----
    registry = PluginRegistry()
    plugin = SemanticContextRetriever()
    registry.register(plugin, _build_ctx())
    assert context_retriever.get_active() is plugin

    brief2 = _fresh_brief()
    _apply_context_retriever(brief2, _Ctx())
    # The seam applied the BM25 ranking: overlap is now first.
    assert brief2.memory_context.matches[0] is overlap
    assert brief2.memory_context.matches[1] is no_overlap

    # ---- Unregister → on_unload releases the slot → passthrough again. ----
    registry.unregister("semantic-context-retriever")
    assert not isinstance(
        context_retriever.get_active(), SemanticContextRetriever
    )
    brief3 = _fresh_brief()
    _apply_context_retriever(brief3, _Ctx())
    assert brief3.memory_context.matches[0] is no_overlap  # back to no-op

    context_retriever.clear()


# ── 5. E2E through the REAL boot discovery path (bootstrap_builtin) ───────────
def test_bootstrap_builtin_discovers_and_activates(tmp_path):
    """The plugin loads through the production discovery+load path and set_active.

    This does NOT instantiate the class directly. It drives
    ``bootstrap.bootstrap_builtin`` — the mechanism ``bootstrap_all`` runs at every
    boot — pointed at the real ``core/plugins/buildin`` root. That path discovers
    the nested ``plugin.yaml``, validates it through the ADR-0247 gate, imports
    ``provider.py`` by file path, instantiates the class and registers it with
    ``origin=builtin`` — which runs ``on_load`` → ``set_active``. The assertion is
    that the module-level ``context_retriever`` slot is now this plugin, i.e. the
    CEL/TDE seams are no longer on the passthrough.
    """
    from corvin_plugins import bootstrap
    from corvin_plugins.providers import context_retriever
    from corvin_plugins.registry import get_registry, unregister

    context_retriever.clear()
    # Guard: if a prior boot in this process already loaded it, start clean.
    if "semantic-context-retriever" in get_registry().discover():
        unregister("semantic-context-retriever")

    try:
        loaded = bootstrap.bootstrap_builtin(
            tenant_id="_default",
            corvin_home=tmp_path,  # irrelevant for origin=builtin (slot gate exempt)
            tenant_config={},
        )
        assert "semantic-context-retriever" in loaded, (
            f"builtin discovery did not load the plugin; loaded={loaded}"
        )
        active = context_retriever.get_active()
        # NOT isinstance: bootstrap_builtin imports provider.py by FILE PATH under
        # its own module name, so the class object differs from this test file's
        # separate top-of-file load — two loads, two class identities. In
        # production only bootstrap_builtin loads it, so there is one identity.
        # Assert by the plugin's stable identity + class name instead, and prove
        # the seam is really active by exercising the ranking below.
        assert getattr(active, "plugin_id", None) == "semantic-context-retriever"
        assert type(active).__name__ == "SemanticContextRetriever"
        # Functional proof: the active provider actually ranks (not passthrough).
        no_overlap = _memory_match("Cooking recipes", "how to bake sourdough bread")
        overlap = _memory_match(
            "Database migration guide", "steps to migrate the database schema"
        )
        ranked = active.select("database migration", [no_overlap, overlap])
        assert ranked[0] is overlap
        # And it must be attributed as a builtin on the installed boot layer.
        assert get_registry().boot_layer_of("semantic-context-retriever").value == (
            "installed"
        )
    finally:
        if "semantic-context-retriever" in get_registry().discover():
            unregister("semantic-context-retriever")
        context_retriever.clear()


def test_bootstrap_builtin_respects_opt_out(tmp_path):
    """``spec.plugins.builtin_disabled`` skips a named builtin (no set_active)."""
    from corvin_plugins import bootstrap
    from corvin_plugins.providers import context_retriever
    from corvin_plugins.registry import get_registry, unregister

    context_retriever.clear()
    if "semantic-context-retriever" in get_registry().discover():
        unregister("semantic-context-retriever")

    try:
        loaded = bootstrap.bootstrap_builtin(
            tenant_id="_default",
            corvin_home=tmp_path,
            tenant_config={
                "spec": {
                    "plugins": {"builtin_disabled": ["semantic-context-retriever"]}
                }
            },
        )
        assert "semantic-context-retriever" not in loaded
        assert not isinstance(
            context_retriever.get_active(), SemanticContextRetriever
        )
    finally:
        if "semantic-context-retriever" in get_registry().discover():
            unregister("semantic-context-retriever")
        context_retriever.clear()
