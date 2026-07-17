"""Unit tests for engine_models.py workload-based model routing — ADR-0043."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "operator" / "bridges" / "shared") not in sys.path:
    sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))

from engine_models import get_model_tier_mapping, resolve_model_for_workload  # type: ignore


class TestModelTierMapping:
    """Test tier mapping retrieval."""

    def test_model_tier_mapping_exists(self) -> None:
        """Model tier mapping is initialized."""
        mapping = get_model_tier_mapping()
        assert isinstance(mapping, dict)
        assert "claude_code" in mapping

    def test_claude_code_tiers(self) -> None:
        """Claude Code has fast and full tiers."""
        mapping = get_model_tier_mapping()
        claude_tiers = mapping.get("claude_code")
        assert claude_tiers is not None
        assert "fast" in claude_tiers
        assert "full" in claude_tiers
        assert claude_tiers["fast"] == "claude-haiku-4-5-20251001"
        assert claude_tiers["full"] == "claude-sonnet-5"

    def test_gemini_tiers(self) -> None:
        """Gemini has fast and full tiers."""
        mapping = get_model_tier_mapping()
        gemini_tiers = mapping.get("gemini")
        assert gemini_tiers is not None
        assert "fast" in gemini_tiers
        assert "full" in gemini_tiers

    def test_mapping_is_copy(self) -> None:
        """get_model_tier_mapping returns a copy, not the reference."""
        mapping1 = get_model_tier_mapping()
        mapping2 = get_model_tier_mapping()
        assert mapping1 == mapping2
        assert mapping1 is not mapping2


class TestResolveModelForWorkload:
    """Test model resolution based on workload type."""

    def test_chat_workload_routes_to_fast(self) -> None:
        """CHAT workload uses engine's fast tier."""
        model = resolve_model_for_workload("claude_code", "chat", None)
        assert model == "claude-haiku-4-5-20251001"

    def test_code_workload_uses_user_choice(self) -> None:
        """CODE workload prefers user's chosen model."""
        model = resolve_model_for_workload("claude_code", "code", "claude-opus-4-1")
        assert model == "claude-opus-4-1"

    def test_code_workload_fallback_to_full_tier(self) -> None:
        """CODE workload falls back to full tier if no user choice."""
        model = resolve_model_for_workload("claude_code", "code", None)
        assert model == "claude-sonnet-5"

    def test_uncertain_workload_uses_user_choice(self) -> None:
        """UNCERTAIN workload always uses user's model (safe fallback)."""
        model = resolve_model_for_workload("claude_code", "uncertain", "my-custom-model")
        assert model == "my-custom-model"

    def test_uncertain_without_user_choice(self) -> None:
        """UNCERTAIN without user choice returns None."""
        model = resolve_model_for_workload("claude_code", "uncertain", None)
        assert model is None

    def test_unknown_workload_type_uses_user_choice(self) -> None:
        """Unknown workload type falls back to user choice."""
        model = resolve_model_for_workload("claude_code", "unknown_type", "claude-sonnet-5")
        assert model == "claude-sonnet-5"

    def test_unknown_engine_uses_user_choice(self) -> None:
        """Unknown engine falls back to user choice."""
        model = resolve_model_for_workload("custom_engine", "chat", "custom-model-v1")
        assert model == "custom-model-v1"

    def test_unknown_engine_without_user_choice(self) -> None:
        """Unknown engine without user choice returns None."""
        model = resolve_model_for_workload("custom_engine", "chat", None)
        assert model is None

    def test_workload_type_case_insensitive(self) -> None:
        """Workload type is case-insensitive."""
        model_lower = resolve_model_for_workload("claude_code", "chat", None)
        model_upper = resolve_model_for_workload("claude_code", "CHAT", None)
        model_mixed = resolve_model_for_workload("claude_code", "ChAt", None)
        assert model_lower == model_upper == model_mixed

    def test_chat_with_user_choice_still_uses_fast(self) -> None:
        """CHAT workload uses fast tier even when user has a choice."""
        model = resolve_model_for_workload("claude_code", "chat", "claude-opus-4-1")
        # CHAT should use fast tier, not user choice
        assert model == "claude-haiku-4-5-20251001"

    def test_gemini_chat_routing(self) -> None:
        """Gemini CHAT routes to its fast tier."""
        model = resolve_model_for_workload("gemini", "chat", None)
        assert model == "gemini-1.5-flash"

    def test_gemini_code_routing(self) -> None:
        """Gemini CODE uses user choice or full tier."""
        model = resolve_model_for_workload("gemini", "code", None)
        assert model == "gemini-2.0-pro-exp"

    def test_codex_chat_routing(self) -> None:
        """Codex CHAT routes to its fast tier."""
        model = resolve_model_for_workload("codex", "chat", None)
        assert model == "code-davinci-002"

    def test_ollama_routing(self) -> None:
        """Ollama local has fast/full tiers."""
        chat_model = resolve_model_for_workload("ollama_local", "chat", None)
        code_model = resolve_model_for_workload("ollama_local", "code", None)
        assert chat_model == "qwen2:1.5b"
        assert code_model == "qwen2:7b"


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_string_engine_id(self) -> None:
        """Empty engine ID treated as unknown engine."""
        model = resolve_model_for_workload("", "chat", "fallback-model")
        assert model == "fallback-model"

    def test_none_engine_id(self) -> None:
        """None engine ID treated as unknown engine."""
        model = resolve_model_for_workload(None, "chat", "fallback-model")  # type: ignore
        assert model == "fallback-model"

    def test_none_workload_type(self) -> None:
        """None workload type treated as unknown."""
        model = resolve_model_for_workload("claude_code", None, "my-model")  # type: ignore
        assert model == "my-model"

    def test_empty_string_user_choice(self) -> None:
        """Empty string user choice is treated as None."""
        # Empty string is falsy in Python; CODE workload should fallback to full tier
        model = resolve_model_for_workload("claude_code", "code", "")
        # Empty string is falsy, so fallback to full tier
        assert model == "claude-sonnet-5"

    def test_whitespace_only_workload_type(self) -> None:
        """Whitespace-only workload type treated as empty."""
        model = resolve_model_for_workload("claude_code", "   ", "fallback")
        # Should be treated as unknown → fallback
        assert model == "fallback"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
