"""ADR-2087 — the quality LLM bridge has no local-Ollama provider any more."""
from __future__ import annotations

import urllib.request

import core.quality.specification_as_loss as sal
from core.quality.specification_as_loss import llm_bridge


def test_ollama_provider_and_handler_removed():
    assert not hasattr(llm_bridge, "OllamaRedirectHandler")
    assert not hasattr(llm_bridge.LLMBridge, "_generate_ollama")
    assert "OllamaRedirectHandler" not in sal.__all__


def test_auto_without_claude_goes_to_static_fallback(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def _no_net(*a, **kw):
        raise AssertionError("no network call expected")

    monkeypatch.setattr(urllib.request, "urlopen", _no_net)
    for provider in ("auto", "ollama"):  # a legacy "ollama" value is not a local path
        out = llm_bridge.LLMBridge(provider=provider).generate(
            {"type": "code_gen", "description": "x"}, {})
        assert out.startswith("# Generated code for: x")
