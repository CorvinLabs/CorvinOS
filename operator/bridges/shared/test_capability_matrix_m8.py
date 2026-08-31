"""
ADR-0087 M8 Tests — Universal Engine Capability Matrix Refactor

Tier-1: Enum validation, matrix construction
Tier-2: Backward compat (bool checks), capability queries
Tier-3: Full matrix consistency across all 5 engines
"""

import pytest
from engines.capability_matrix import (
    CapabilityString,
    EngineCapabilityMatrix,
    CapabilityMatcher,
    CANONICAL_CAPABILITY_MATRIX,
)


# ============================================================================
# TIER-1: Enum & Matrix Validation
# ============================================================================

class TestCapabilityStringTier1:
    """Semantic capability string validation."""

    def test_predefined_strings_exist(self):
        """
        Given: CapabilityString class
        When: predefined constants accessed
        Then: all strings present (STDIN_JSON, BUFFERED, NATIVE, etc.)
        """
        assert CapabilityString.STDIN_JSON == "stdin_json"
        assert CapabilityString.BUFFERED == "buffered"
        assert CapabilityString.NATIVE == "native"
        assert CapabilityString.TEB_BROKERED == "teb_brokered"
        assert CapabilityString.APPEND_SYSTEM_PROMPT == "append_system_prompt"
        assert CapabilityString.SYSTEM_MESSAGE == "system_message"
        assert CapabilityString.PROMPT_PREFIX == "prompt_prefix"
        assert CapabilityString.CHECKPOINT == "checkpoint"
        assert CapabilityString.FCB_EMULATED == "fcb_emulated"
        assert CapabilityString.FLAG == "flag"
        assert CapabilityString.MESSAGE_ROLE == "message_role"
        assert CapabilityString.SEQUENTIAL_WRAPPER == "sequential_wrapper"

    def test_validate_capability_valid(self):
        """
        Given: valid capability string and allowed values
        When: validate() called
        Then: returns True
        """
        result = CapabilityString.validate("stdin_json", ("stdin_json", "buffered", None))
        assert result is True

    def test_validate_capability_invalid(self):
        """
        Given: invalid capability string
        When: validate() called
        Then: returns False
        """
        result = CapabilityString.validate("invalid_value", ("stdin_json", "buffered", None))
        assert result is False

    def test_validate_capability_none_allowed(self):
        """
        Given: capability=None, allowed_values includes None
        When: validate() called
        Then: returns True (None is valid for optional capabilities)
        """
        result = CapabilityString.validate(None, ("stdin_json", "buffered", None))
        assert result is True


class TestEngineCapabilityMatrixTier1:
    """Capability matrix construction & validation."""

    def test_matrix_construction_claude_code(self):
        """
        Given: EngineCapabilityMatrix for claude_code
        When: instantiated with all capabilities
        Then: all fields set correctly
        """
        matrix = EngineCapabilityMatrix(
            engine_id="claude_code",
            mid_stream_inject="stdin_json",
            hooks="native",
            skills="append_system_prompt",
            session_pinning="native",
            mcp="native",
            system_prompt="flag",
            multi_turn="native",
        )

        assert matrix.engine_id == "claude_code"
        assert matrix.mid_stream_inject == "stdin_json"
        assert matrix.hooks == "native"
        assert matrix.skills == "append_system_prompt"

    def test_matrix_validate_all_claude_code(self):
        """
        Given: Claude Code capability matrix
        When: validate_all() called
        Then: returns True (all strings valid)
        """
        matrix = EngineCapabilityMatrix(
            engine_id="claude_code",
            mid_stream_inject="stdin_json",
            hooks="native",
            skills="append_system_prompt",
            session_pinning="native",
            mcp="native",
            system_prompt="flag",
            multi_turn="native",
        )

        result = matrix.validate_all()
        assert result is True

    def test_matrix_validate_all_invalid(self):
        """
        Given: matrix with invalid capability string
        When: validate_all() called
        Then: raises ValueError
        """
        matrix = EngineCapabilityMatrix(
            engine_id="claude_code",
            mid_stream_inject="invalid_value",  # Invalid!
            hooks="native",
            skills="append_system_prompt",
            session_pinning="native",
            mcp="native",
            system_prompt="flag",
            multi_turn="native",
        )

        with pytest.raises(ValueError):
            matrix.validate_all()


# ============================================================================
# TIER-2: Queries & Backward Compatibility
# ============================================================================

class TestEngineCapabilityMatrixTier2:
    """Capability queries & backward compat."""

    def test_is_capable_true(self):
        """
        Given: matrix where mid_stream_inject="stdin_json"
        When: is_capable("mid_stream_inject") called
        Then: returns True
        """
        matrix = EngineCapabilityMatrix(
            engine_id="claude_code",
            mid_stream_inject="stdin_json",
        )

        result = matrix.is_capable("mid_stream_inject")
        assert result is True

    def test_is_capable_false(self):
        """
        Given: matrix where mcp=None
        When: is_capable("mcp") called
        Then: returns False
        """
        matrix = EngineCapabilityMatrix(
            engine_id="hermes",
            mcp=None,
        )

        result = matrix.is_capable("mcp")
        assert result is False

    def test_get_capability_returns_string(self):
        """
        Given: matrix with capability="stdin_json"
        When: get_capability("mid_stream_inject") called
        Then: returns "stdin_json"
        """
        matrix = EngineCapabilityMatrix(
            engine_id="claude_code",
            mid_stream_inject="stdin_json",
        )

        result = matrix.get_capability("mid_stream_inject")
        assert result == "stdin_json"

    def test_get_capability_returns_none(self):
        """
        Given: matrix with capability=None
        When: get_capability("mcp") called
        Then: returns None
        """
        matrix = EngineCapabilityMatrix(
            engine_id="hermes",
            mcp=None,
        )

        result = matrix.get_capability("mcp")
        assert result is None


class TestCapabilityMatcherTier2:
    """Query engines by capability."""

    def test_engines_supporting_native_hooks(self):
        """
        Given: 5 engine matrices
        When: engines_supporting("hooks", "native") called
        Then: returns ["claude_code"] only
        """
        matrices = {
            "claude_code": EngineCapabilityMatrix("claude_code", hooks="native"),
            "codex": EngineCapabilityMatrix("codex", hooks="teb_brokered"),
            "opencode": EngineCapabilityMatrix("opencode", hooks="teb_brokered"),
            "hermes": EngineCapabilityMatrix("hermes", hooks="teb_brokered"),
            "copilot": EngineCapabilityMatrix("copilot", hooks="teb_brokered"),
        }

        matcher = CapabilityMatcher(matrices)
        result = matcher.engines_supporting("hooks", "native")

        assert result == ["claude_code"]

    def test_engines_supporting_buffered_injection(self):
        """
        Given: 5 engine matrices
        When: engines_supporting("mid_stream_inject", "buffered") called
        Then: returns ["codex", "opencode", "hermes"]
        """
        matrices = {
            "claude_code": EngineCapabilityMatrix("claude_code", mid_stream_inject="stdin_json"),
            "codex": EngineCapabilityMatrix("codex", mid_stream_inject="buffered"),
            "opencode": EngineCapabilityMatrix("opencode", mid_stream_inject="buffered"),
            "hermes": EngineCapabilityMatrix("hermes", mid_stream_inject="buffered"),
            "copilot": EngineCapabilityMatrix("copilot", mid_stream_inject=None),
        }

        matcher = CapabilityMatcher(matrices)
        result = matcher.engines_supporting("mid_stream_inject", "buffered")

        assert set(result) == {"codex", "opencode", "hermes"}

    def test_engines_supporting_none_capability(self):
        """
        Given: 5 engine matrices
        When: engines_supporting("multi_turn", None) called
        Then: returns ["codex", "opencode", "hermes"]
        """
        matrices = {
            "claude_code": EngineCapabilityMatrix("claude_code", multi_turn="native"),
            "codex": EngineCapabilityMatrix("codex", multi_turn=None),
            "opencode": EngineCapabilityMatrix("opencode", multi_turn=None),
            "hermes": EngineCapabilityMatrix("hermes", multi_turn=None),
            "copilot": EngineCapabilityMatrix("copilot", multi_turn="sequential_wrapper"),
        }

        matcher = CapabilityMatcher(matrices)
        result = matcher.engines_supporting("multi_turn", None)

        assert set(result) == {"codex", "opencode", "hermes"}


# ============================================================================
# TIER-3: Full Matrix Consistency (All 5 Engines)
# ============================================================================

class TestCapabilityMatrixE2E:
    """End-to-end matrix consistency across all engines."""

    def test_e2e_canonical_matrix_valid(self):
        """
        Tier-3: Canonical frozen matrix is valid.
        Given: CANONICAL_CAPABILITY_MATRIX
        When: each engine's matrix validated
        Then: all engines pass validation
        """
        for engine_id, caps in CANONICAL_CAPABILITY_MATRIX.items():
            matrix = EngineCapabilityMatrix(
                engine_id=engine_id,
                **caps
            )
            # Should not raise
            assert matrix.validate_all() is True

    def test_e2e_all_engines_can_parse_system_prompt(self):
        """
        Tier-3: All 5 engines have system_prompt capability.
        Given: CANONICAL_CAPABILITY_MATRIX
        When: system_prompt capability queried on all engines
        Then: all return non-None value (flag/message_role/text_prefix)
        """
        for engine_id, caps in CANONICAL_CAPABILITY_MATRIX.items():
            matrix = EngineCapabilityMatrix(engine_id=engine_id, **caps)
            system_prompt_value = matrix.get_capability("system_prompt")

            # All engines must have non-None system_prompt
            assert system_prompt_value is not None, f"{engine_id} has no system_prompt"
            assert system_prompt_value in ("flag", "message_role", "text_prefix"), \
                f"{engine_id} system_prompt={system_prompt_value} invalid"

    def test_e2e_capability_consistency_with_m5_m6(self):
        """
        Tier-3: M8 capability matrix matches M5+M6 frozen strings.
        Given: Frozen strings from M5+M6 (inject_systems, parse_copilot, etc.)
        When: compared to CANONICAL_CAPABILITY_MATRIX
        Then: all values match (no drift)
        """
        # Frozen strings from M5+M6 implementation
        expected_values = {
            "claude_code": {
                "mid_stream_inject": "stdin_json",
                "hooks": "native",
                "skills": "append_system_prompt",
                "system_prompt": "flag",
                "multi_turn": "native",
            },
            "codex": {
                "mid_stream_inject": "buffered",
                "hooks": "teb_brokered",
                "skills": "append_system_prompt",
                "system_prompt": "text_prefix",
            },
            "opencode": {
                "mid_stream_inject": "buffered",
                "hooks": "teb_brokered",
                "skills": "prompt_prefix",
                "system_prompt": "text_prefix",
            },
            "hermes": {
                "mid_stream_inject": "buffered",
                "hooks": "teb_brokered",
                "skills": "system_message",
                "system_prompt": "message_role",
                "mcp": None,
            },
            "copilot": {
                "mid_stream_inject": None,
                "hooks": "teb_brokered",
                "skills": "prompt_prefix",
                "system_prompt": "text_prefix",
                "mcp": "fcb_emulated",
                "multi_turn": "sequential_wrapper",
            },
        }

        # Verify no drift
        for engine_id, expected_caps in expected_values.items():
            canonical_caps = CANONICAL_CAPABILITY_MATRIX[engine_id]
            for cap_name, expected_value in expected_caps.items():
                assert canonical_caps[cap_name] == expected_value, \
                    f"{engine_id}.{cap_name}: expected {expected_value}, got {canonical_caps[cap_name]}"
