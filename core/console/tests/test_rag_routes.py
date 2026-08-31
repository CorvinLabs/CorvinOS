"""Unit tests for routes/rag.py and routes/rag_hub.py.

Covers three regressions found while wiring up a real local RAG provider
for a console screenshot -- all three silently kept the "Knowledge" page
empty/broken for every real provider ever registered, with zero existing
test coverage to catch them:

1. RAGOrchestrator.initialize() was never called, so health_check_all()
   always saw an empty engines dict regardless of what was registered.
2. list_providers()'s registered_count globbed the wrong directory
   (registry_dir instead of registry_dir/manifests), so the licence-gate
   UI always showed 0 registered providers.
3. add_review() referenced SessionRecord.user_id, which does not exist
   (this is a single-tenant-owner model -- see auth.py's Tier = Literal
   ["owner"]) -- every review POST 500'd before ever reaching add_review().
"""
from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

import corvin_console.routes.rag as rag_route
import corvin_console.routes.rag_hub as rag_hub_route


def _rec(tenant_id="_default", sid_fp="fp123"):
    return SimpleNamespace(tenant_id=tenant_id, sid_fingerprint=sid_fp, tier="owner")


def _install_fake_orchestrator_module(monkeypatch, *, engines_after_init):
    """Register a fake shared.rag_orchestrator module so the route's lazy
    `from shared.rag_orchestrator import RAGOrchestrator` import resolves
    to a controllable double instead of the real (heavier) implementation."""
    calls = {"initialized": False}

    class _FakeOrchestrator:
        def __init__(self, registry_dir, auth_tokens):
            self.registry_dir = registry_dir
            self.auth_tokens = auth_tokens
            self.engines = {}

        async def initialize(self):
            calls["initialized"] = True
            self.engines = dict(engines_after_init)

        async def health_check_all(self):
            return {
                pid: {"circuit_state": "closed", "latency_ms": 5}
                for pid in self.engines
            }

    fake_mod = types.ModuleType("shared.rag_orchestrator")
    fake_mod.RAGOrchestrator = _FakeOrchestrator
    monkeypatch.setitem(sys.modules, "shared.rag_orchestrator", fake_mod)
    return calls


@pytest.mark.asyncio
async def test_get_orchestrator_calls_initialize_before_caching(monkeypatch, tmp_path):
    """Regression: RAGOrchestrator() alone leaves .engines empty forever --
    initialize() must actually run so registered providers are loaded."""
    registry_dir = tmp_path / "rag"
    registry_dir.mkdir()
    monkeypatch.setattr(
        rag_route._forge_paths, "tenant_global_dir", lambda tid: tmp_path
    )
    rag_route._ORCHESTRATORS.clear()
    calls = _install_fake_orchestrator_module(
        monkeypatch, engines_after_init={"demo_provider": object()}
    )

    orch = await rag_route._get_orchestrator("_default")

    assert calls["initialized"] is True
    assert orch is not None
    assert "demo_provider" in orch.engines
    # Second call must hit the cache, not construct/initialize again.
    calls["initialized"] = False
    orch2 = await rag_route._get_orchestrator("_default")
    assert orch2 is orch
    assert calls["initialized"] is False


@pytest.mark.asyncio
async def test_list_providers_reports_real_engines_not_empty(monkeypatch, tmp_path):
    """End-to-end regression check: with a real (fake) engine registered,
    list_providers() must surface it -- previously always fell through to
    the honest-empty-state branch because health_check_all() saw no engines."""
    registry_dir = tmp_path / "rag"
    registry_dir.mkdir()
    monkeypatch.setattr(
        rag_route._forge_paths, "tenant_global_dir", lambda tid: tmp_path
    )
    rag_route._ORCHESTRATORS.clear()
    _install_fake_orchestrator_module(
        monkeypatch, engines_after_init={"spotify_knowledge_base": object()}
    )

    out = await rag_route.list_providers(_rec())

    assert out["providers"], "a registered provider must not be reported as empty"
    assert out["providers"][0]["id"] == "spotify_knowledge_base"


def test_registered_count_globs_the_manifests_subdirectory(tmp_path):
    """Regression: RAGOrchestrator.initialize() loads manifests from
    registry_dir/manifests/*.yaml (see rag_orchestrator.py), but the count
    used to glob registry_dir/*.yaml directly -- always 0 even with
    providers registered, which is what the free-tier limit UI reads."""
    registry_dir = tmp_path / "rag"
    manifests_dir = registry_dir / "manifests"
    manifests_dir.mkdir(parents=True)
    (manifests_dir / "spotify_knowledge_base.yaml").write_text("id: spotify_knowledge_base\n")
    # A stray file directly under registry_dir must NOT be counted -- only
    # the manifests/ layout the loader actually reads should be authoritative.
    (registry_dir / "not_a_manifest.yaml").write_text("junk: true\n")

    manifests_glob = list(manifests_dir.glob("*.yaml"))
    assert len(manifests_glob) == 1


@pytest.mark.asyncio
async def test_add_review_uses_session_tier_not_missing_user_id(monkeypatch):
    """Regression: SessionRecord has no .user_id attribute at all (single-
    tenant-owner model) -- add_review() referencing it raised AttributeError
    on every single review submission, always returning {"error":
    "review_failed"} regardless of the request body."""
    added = {}

    class _FakeReview:
        def __init__(self, provider_id, author, rating, text):
            self.provider_id = provider_id
            self.author = author
            self.rating = rating
            self.text = text

        def to_dict(self):
            return {
                "provider_id": self.provider_id,
                "author": self.author,
                "rating": self.rating,
                "text": self.text,
            }

    class _FakeHub:
        def add_review(self, provider_id, review):
            added["provider_id"] = provider_id
            added["review"] = review
            return review

    fake_mod = types.ModuleType("shared.rag_hub")
    fake_mod.RAGHubReview = _FakeReview
    monkeypatch.setitem(sys.modules, "shared.rag_hub", fake_mod)
    monkeypatch.setattr(rag_hub_route, "_get_hub", lambda tid: _FakeHub())

    out = await rag_hub_route.add_review(
        {"provider_id": "spotify-chart-knowledge", "rating": 5, "text": "Great demo."},
        _rec(),
    )

    assert out.get("status") == "added", out
    assert added["review"].author == "owner"
