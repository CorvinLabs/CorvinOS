"""Tests for Execution Context Badge — Phase 1 foundation.

Tests cover:
  - ExecutionContext dataclass serialization/deserialization
  - Model source detection (Claude, OpenRouter)
  - Model name normalization
  - Engine detection
  - Delegation mode detection
  - Token counting from various API formats
  - Builder pattern for capturing turn lifecycle
"""

import pytest
import time
from pathlib import Path

from corvin_console.execution_context import (
    ExecutionContext,
    ExecutionContextBuilder,
    ModelSource,
    EngineId,
    DelegationMode,
    detect_model_source,
    normalize_model_name,
    detect_engine,
    detect_delegation_mode,
    extract_token_usage,
)


class TestModelSourceDetection:
    """Test model source detection from model names."""

    def test_detect_claude_models(self):
        """Claude models are identified correctly."""
        assert detect_model_source("claude-3-5-sonnet") == ModelSource.CLAUDE
        assert detect_model_source("claude-3-5-sonnet-20241022") == ModelSource.CLAUDE
        assert detect_model_source("Claude-3-5-Opus") == ModelSource.CLAUDE
        assert detect_model_source("CLAUDE-2") == ModelSource.CLAUDE

    def test_local_ollama_and_hermes_names_are_not_classified(self):
        """ADR-2087: nothing classifies a NEW turn as local Ollama / Hermes."""
        for name in ("ollama:mistral", "ollama/mistral", "OLLAMA:llama2",
                     "hermes-local-3", "Hermes-2-Pro"):
            assert detect_model_source(name) == ModelSource.UNKNOWN, name

    def test_detect_openrouter_models(self):
        """OpenRouter models are identified."""
        assert detect_model_source("openrouter:meta-llama/llama-2") == ModelSource.OPENROUTER
        assert detect_model_source("openrouter/meta-llama/llama-2") == ModelSource.OPENROUTER
        assert detect_model_source("OpenRouter:mistral") == ModelSource.OPENROUTER

    def test_detect_unknown_models(self):
        """Unknown or invalid models return UNKNOWN."""
        assert detect_model_source("") == ModelSource.UNKNOWN
        assert detect_model_source(None) == ModelSource.UNKNOWN
        assert detect_model_source("gpt-4") == ModelSource.UNKNOWN
        assert detect_model_source("gemini-2") == ModelSource.UNKNOWN
        assert detect_model_source("random-model") == ModelSource.UNKNOWN


class TestModelNameNormalization:
    """Test model name normalization to canonical forms."""

    def test_normalize_claude(self):
        """Claude model names are lowercased and timestamps stripped."""
        assert normalize_model_name("claude-3-5-sonnet") == "claude-3-5-sonnet"
        assert normalize_model_name("claude-3-5-sonnet-20241022") == "claude-3-5-sonnet"
        assert normalize_model_name("Claude-3-5-Sonnet-20240229") == "claude-3-5-sonnet"
        assert normalize_model_name("CLAUDE-OPUS-20240229") == "claude-opus"

    def test_normalize_openrouter(self):
        """OpenRouter names: : → / (first separator only)."""
        assert normalize_model_name("openrouter:meta-llama/llama-2") == "openrouter/meta-llama/llama-2"
        assert normalize_model_name("openrouter/mistral") == "openrouter/mistral"
        assert normalize_model_name("OpenRouter:Mistral-7B") == "openrouter/mistral-7b"

    def test_normalize_empty(self):
        """Empty model names return empty string."""
        assert normalize_model_name("") == ""
        assert normalize_model_name(None) == ""

    def test_normalize_with_explicit_source(self):
        """Normalization is faster with explicit source hint."""
        # With source provided, skips detection
        assert normalize_model_name("claude-3-5-sonnet-20241022", ModelSource.CLAUDE) == "claude-3-5-sonnet"


class TestEngineDetection:
    """Test engine detection from runtime state."""

    def test_detect_claude_code_engine(self):
        """Claude Code engine is detected from state."""
        assert detect_engine({"engine_id": "claude_code"}) == EngineId.CLAUDE_CODE
        assert detect_engine({"engine_id": "Claude_Code"}) == EngineId.CLAUDE_CODE

    def test_detect_acs_engine(self):
        """ACS engine is detected."""
        assert detect_engine({"engine_id": "acs"}) == EngineId.ACS
        assert detect_engine({"spawn_via": "worker"}) == EngineId.ACS
        assert detect_engine({"delegation_mode": "acs"}) == EngineId.ACS

    def test_detect_tde_engine(self):
        """TDE engine is detected."""
        assert detect_engine({"engine_id": "tde"}) == EngineId.TDE
        assert detect_engine({"delegation_mode": "tde"}) == EngineId.TDE

    def test_hermes_engine_is_not_detected(self):
        """ADR-2087: runtime state never classifies as Hermes any more."""
        assert detect_engine({"engine_id": "hermes"}) == EngineId.UNKNOWN
        assert detect_engine({"spawn_via": "http"}) == EngineId.UNKNOWN

    def test_historical_hermes_record_still_parses(self):
        """Records already written keep parsing: the enum values stay."""
        ctx = ExecutionContext.from_dict(
            {"engine_id": "hermes", "model_source": "ollama", "model_name": "ollama/mistral"})
        assert ctx.engine_id == EngineId.HERMES
        assert ctx.model_source == ModelSource.OLLAMA

    def test_detect_unknown_engine(self):
        """Unknown engine returns UNKNOWN."""
        assert detect_engine({}) == EngineId.UNKNOWN
        assert detect_engine({"engine_id": "unknown"}) == EngineId.UNKNOWN
        assert detect_engine({"engine_id": "copilot"}) == EngineId.UNKNOWN


class TestDelegationModeDetection:
    """Test delegation mode detection."""

    def test_detect_native_delegation(self):
        """Native (non-delegated) turns are detected."""
        assert detect_delegation_mode({}) == DelegationMode.NATIVE
        assert detect_delegation_mode({"delegation_enabled": False}) == DelegationMode.NATIVE

    def test_detect_acs_delegation(self):
        """ACS delegation is detected."""
        assert detect_delegation_mode({"delegation_mode": "acs"}) == DelegationMode.ACS
        assert detect_delegation_mode({"acs_run_id": "run-123"}) == DelegationMode.ACS

    def test_detect_tde_delegation(self):
        """TDE delegation is detected."""
        assert detect_delegation_mode({"delegation_mode": "tde"}) == DelegationMode.TDE
        assert detect_delegation_mode({"tde_run_id": "tde-456"}) == DelegationMode.TDE

    def test_detect_fallback_delegation(self):
        """Fallback (delegated but reverted) is detected."""
        assert detect_delegation_mode({"delegation_mode": "fallback"}) == DelegationMode.FALLBACK
        assert detect_delegation_mode({"fallback_reason": "quota_exceeded"}) == DelegationMode.FALLBACK


class TestTokenUsageExtraction:
    """Test token counting from various API formats."""

    def test_extract_anthropic_format(self):
        """Anthropic API format {input_tokens, output_tokens}."""
        usage = {"input_tokens": 150, "output_tokens": 50}
        in_tok, out_tok = extract_token_usage(usage)
        assert in_tok == 150
        assert out_tok == 50

    def test_extract_openrouter_format(self):
        """OpenRouter format {prompt_tokens, completion_tokens}."""
        usage = {"prompt_tokens": 200, "completion_tokens": 75}
        in_tok, out_tok = extract_token_usage(usage)
        assert in_tok == 200
        assert out_tok == 75

    def test_extract_generic_format(self):
        """Generic format {tokens_in, tokens_out}."""
        usage = {"tokens_in": 100, "tokens_out": 25}
        in_tok, out_tok = extract_token_usage(usage)
        assert in_tok == 100
        assert out_tok == 25

    def test_extract_short_generic_format(self):
        """Abbreviated format {in, out}."""
        usage = {"in": 180, "out": 60}
        in_tok, out_tok = extract_token_usage(usage)
        assert in_tok == 180
        assert out_tok == 60

    def test_extract_none_on_empty(self):
        """Empty or None usage returns (None, None)."""
        assert extract_token_usage(None) == (None, None)
        assert extract_token_usage({}) == (None, None)

    def test_extract_partial_usage(self):
        """Partial usage data returns (None, None)."""
        assert extract_token_usage({"input_tokens": 100}) == (None, None)
        assert extract_token_usage({"output_tokens": 50}) == (None, None)


class TestExecutionContextSerialization:
    """Test ExecutionContext serialization to/from dict."""

    def test_serialize_to_dict(self):
        """ExecutionContext serializes to JSON-compatible dict."""
        ctx = ExecutionContext(
            engine_id=EngineId.CLAUDE_CODE,
            model_source=ModelSource.CLAUDE,
            model_name="claude-3-5-sonnet",
            delegation_mode=DelegationMode.NATIVE,
            duration_ms=1234,
            tokens_input=150,
            tokens_output=50,
            tool_calls_count=2,
            tenant_id="tenant-123",
            turn_number=3,
            exit_code=0,
        )
        data = ctx.to_dict()
        assert data["engine_id"] == "claude_code"
        assert data["model_source"] == "claude"
        assert data["model_name"] == "claude-3-5-sonnet"
        assert data["duration_ms"] == 1234
        assert data["tokens_input"] == 150
        assert data["tokens_output"] == 50
        assert data["tool_calls_count"] == 2

    def test_deserialize_from_dict(self):
        """ExecutionContext deserializes from dict."""
        data = {
            "engine_id": "acs",
            "model_source": "claude",
            "model_name": "claude-opus",
            "delegation_mode": "acs",
            "acs_run_id": "run-999",
            "duration_ms": 5000,
            "tokens_input": 200,
            "tokens_output": 100,
            "exit_code": 0,
        }
        ctx = ExecutionContext.from_dict(data)
        assert ctx.engine_id == EngineId.ACS
        assert ctx.model_source == ModelSource.CLAUDE
        assert ctx.model_name == "claude-opus"
        assert ctx.delegation_mode == DelegationMode.ACS
        assert ctx.acs_run_id == "run-999"
        assert ctx.duration_ms == 5000

    def test_roundtrip_serialization(self):
        """Serialization and deserialization are inverse operations."""
        ctx1 = ExecutionContext(
            engine_id=EngineId.TDE,
            model_source=ModelSource.OPENROUTER,
            model_name="openrouter/mistral",
            delegation_mode=DelegationMode.TDE,
            tde_router_decision="route_local",
            duration_ms=2500,
            tokens_input=300,
            tokens_output=120,
            tool_calls_count=3,
            tenant_id="tenant-456",
            exit_code=0,
        )
        data = ctx1.to_dict()
        ctx2 = ExecutionContext.from_dict(data)
        assert ctx2.engine_id == ctx1.engine_id
        assert ctx2.model_source == ctx1.model_source
        assert ctx2.model_name == ctx1.model_name
        assert ctx2.delegation_mode == ctx1.delegation_mode
        assert ctx2.tde_router_decision == ctx1.tde_router_decision
        assert ctx2.duration_ms == ctx1.duration_ms
        assert ctx2.tokens_input == ctx1.tokens_input

    def test_deserialize_unknown_enums(self):
        """Unknown enum values degrade gracefully."""
        data = {
            "engine_id": "future_engine",
            "model_source": "unknown_source",
            "delegation_mode": "unknown_mode",
        }
        ctx = ExecutionContext.from_dict(data)
        assert ctx.engine_id == EngineId.UNKNOWN
        assert ctx.model_source == ModelSource.UNKNOWN
        assert ctx.delegation_mode == DelegationMode.NATIVE


class TestExecutionContextBuilder:
    """Test ExecutionContextBuilder for turn lifecycle tracking."""

    def test_builder_basic_flow(self):
        """Builder captures turn lifecycle: start → usage → complete."""
        builder = ExecutionContextBuilder(tenant_id="tenant-123", turn_number=0)
        ctx = builder.start(engine_id="claude_code", model_name="claude-3-5-sonnet") \
                    .set_usage({"input_tokens": 100, "output_tokens": 50}) \
                    .add_tool_call() \
                    .add_tool_call() \
                    .set_exit_code(0) \
                    .complete()

        assert ctx.engine_id == EngineId.CLAUDE_CODE
        assert ctx.model_name == "claude-3-5-sonnet"
        assert ctx.model_source == ModelSource.CLAUDE
        assert ctx.tokens_input == 100
        assert ctx.tokens_output == 50
        assert ctx.tool_calls_count == 2
        assert ctx.exit_code == 0
        assert ctx.duration_ms >= 0  # May be 0 if execution is very fast
        assert ctx.started_at is not None
        assert ctx.completed_at is not None

    def test_builder_duration_tracking(self):
        """Builder tracks elapsed time accurately."""
        builder = ExecutionContextBuilder()
        builder.start(engine_id="claude_code", model_name="claude-opus")
        time.sleep(0.05)  # 50ms
        ctx = builder.complete()

        # Duration should be ≥ 50ms (allow for timing variance)
        assert ctx.duration_ms >= 40

    def test_builder_acs_flow(self):
        """Builder supports ACS delegation flow."""
        builder = ExecutionContextBuilder(tenant_id="t1", turn_number=1)
        ctx = builder.start(engine_id="acs", model_name="claude-3-5-sonnet") \
                    .set_delegation(mode="acs", acs_run_id="run-abc-123") \
                    .set_usage({"input_tokens": 250, "output_tokens": 100}) \
                    .set_exit_code(0) \
                    .complete()

        assert ctx.engine_id == EngineId.ACS
        assert ctx.delegation_mode == DelegationMode.ACS
        assert ctx.acs_run_id == "run-abc-123"

    def test_builder_tde_flow(self):
        """Builder supports TDE delegation flow."""
        builder = ExecutionContextBuilder(tenant_id="t2", turn_number=2)
        ctx = builder.start(engine_id="tde", model_name="claude-sonnet") \
                    .set_delegation(mode="tde", tde_router_decision="route_worker") \
                    .set_usage({"input_tokens": 150, "output_tokens": 75}) \
                    .add_tool_call() \
                    .set_exit_code(0) \
                    .complete()

        assert ctx.engine_id == EngineId.TDE
        assert ctx.delegation_mode == DelegationMode.TDE
        assert ctx.tde_router_decision == "route_worker"

    def test_builder_error_flow(self):
        """Builder handles error/non-zero exit."""
        builder = ExecutionContextBuilder()
        ctx = builder.start(engine_id="claude_code", model_name="claude-haiku") \
                    .set_exit_code(1) \
                    .complete()

        assert ctx.exit_code == 1
        assert ctx.started_at is not None
        assert ctx.completed_at is not None

    def test_builder_graceful_empty_start(self):
        """Builder handles empty start (all defaults)."""
        builder = ExecutionContextBuilder()
        ctx = builder.start().complete()

        assert ctx.engine_id == EngineId.UNKNOWN
        assert ctx.model_name == ""
        assert ctx.duration_ms >= 0

    def test_builder_enum_auto_conversion(self):
        """Builder accepts both string and enum for engine/mode."""
        # String form
        ctx1 = ExecutionContextBuilder().start(engine_id="acs").complete()
        assert ctx1.engine_id == EngineId.ACS

        # Enum form
        ctx2 = ExecutionContextBuilder().start(engine_id=EngineId.ACS).complete()
        assert ctx2.engine_id == EngineId.ACS

        # Both equal
        assert ctx1.engine_id == ctx2.engine_id


class TestExecutionContextIntegration:
    """Integration tests — real-world turn scenarios."""

    def test_native_claude_code_turn(self):
        """Scenario: native Claude Code turn (no delegation)."""
        builder = ExecutionContextBuilder(tenant_id="default", turn_number=0)
        ctx = (builder
            .start(engine_id="claude_code", model_name="claude-3-5-sonnet-20241022")
            .set_delegation(mode="native")
            .set_usage({"input_tokens": 150, "output_tokens": 45})
            .set_exit_code(0)
            .complete())

        data = ctx.to_dict()
        restored = ExecutionContext.from_dict(data)

        assert restored.engine_id == EngineId.CLAUDE_CODE
        assert restored.model_name == "claude-3-5-sonnet"
        assert restored.delegation_mode == DelegationMode.NATIVE
        assert restored.exit_code == 0

    def test_acs_delegated_turn(self):
        """Scenario: turn delegated to ACS workers."""
        builder = ExecutionContextBuilder(tenant_id="ent1", turn_number=5)
        ctx = (builder
            .start(engine_id="acs", model_name="claude-opus")
            .set_delegation(mode="acs", acs_run_id="run-xyz-789")
            .set_usage({"input_tokens": 500, "output_tokens": 200})
            .add_tool_call()
            .add_tool_call()
            .add_tool_call()
            .set_exit_code(0)
            .complete())

        assert ctx.acs_run_id == "run-xyz-789"
        assert ctx.tool_calls_count == 3
        assert ctx.tokens_input == 500

    def test_tde_routed_turn(self):
        """Scenario: turn routed through TDE."""
        builder = ExecutionContextBuilder(tenant_id="org2", turn_number=2)
        ctx = (builder
            .start(engine_id="tde", model_name="claude-sonnet")
            .set_delegation(mode="tde", tde_router_decision="route_large_context")
            .set_usage({"input_tokens": 8000, "output_tokens": 500})
            .set_exit_code(0)
            .complete())

        assert ctx.tde_router_decision == "route_large_context"
        assert ctx.tokens_input == 8000

    def test_fallback_scenario(self):
        """Scenario: delegation attempted but fell back to native."""
        builder = ExecutionContextBuilder(tenant_id="trial", turn_number=1)
        ctx = (builder
            .start(engine_id="claude_code", model_name="claude-haiku")
            .set_delegation(mode="fallback")  # Tried ACS/TDE but reverted
            .set_usage({"input_tokens": 100, "output_tokens": 30})
            .set_exit_code(0)
            .complete())

        assert ctx.delegation_mode == DelegationMode.FALLBACK
        assert ctx.engine_id == EngineId.CLAUDE_CODE

class TestEdgeCases:
    """Test edge cases and robustness."""

    def test_very_long_model_name(self):
        """Handles very long model names."""
        long_name = "claude-" + "x" * 500 + "-20241022"
        normalized = normalize_model_name(long_name)
        assert normalized.startswith("claude-")

    def test_unicode_model_names(self):
        """Handles unicode in model names gracefully."""
        # Provider tags might carry unicode
        name = normalize_model_name("openrouter:mistral:café")
        assert "mistral" in name.lower()

    def test_malformed_token_usage(self):
        """Gracefully handles malformed token counts."""
        # Non-integer tokens
        usage = {"input_tokens": "abc", "output_tokens": "xyz"}
        try:
            extract_token_usage(usage)
        except (TypeError, ValueError):
            # Expected — extraction might fail on bad data
            pass

    def test_context_builder_multiple_completions(self):
        """Builder can be completed multiple times (immutable state)."""
        builder = ExecutionContextBuilder(tenant_id="t1", turn_number=0)
        builder.start(engine_id="claude_code")

        ctx1 = builder.complete()
        time.sleep(0.01)
        ctx2 = builder.complete()

        # Both should have non-zero duration (timing-dependent)
        assert ctx1.duration_ms > 0
        assert ctx2.duration_ms >= ctx1.duration_ms

    def test_missing_optional_fields(self):
        """ExecutionContext handles missing optional fields."""
        ctx = ExecutionContext()
        data = ctx.to_dict()
        restored = ExecutionContext.from_dict(data)

        assert restored.acs_run_id is None
        assert restored.tokens_input is None
        assert restored.tokens_output is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
