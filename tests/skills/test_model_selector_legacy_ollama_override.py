"""ADR-2087: a tier override saved with the removed local-Ollama provider
must route on native Anthropic, never to a local model id."""
from core.skills.os_skills.model_selector import ModelSelector


def test_legacy_ollama_override_routes_to_anthropic_tier_model():
    for prov in ("ollama", "ollama_local"):
        sel = ModelSelector(overrides={t: {"provider": prov, "model": "mistral:7b"}
                                       for t in ("simple", "medium", "complex")})
        r = sel.classify("hi", tenant_id="_default")
        assert r.recommended_provider in (None, "anthropic")
        assert r.recommended_model.startswith("claude-")
        assert "mistral" not in r.recommended_model
