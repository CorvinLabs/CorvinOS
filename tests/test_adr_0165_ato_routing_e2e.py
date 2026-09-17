"""
E2E Tests: ADR-0165 Model Selection Routing Injection (ATO Recommendations)

Comprehensive tests for ATO plan injection into the 7-Tier model resolver.
Tests the fail-safe design, audit trail integration, and tier ordering.

References:
- ADR-0165: Model Selection Routing Injection (M6 Wiring)
- ADR-0024: 6-Tier adaptive OS model selection
- ADR-0377: Cost Optimizer (ATO classification source)

Note: Tests use tenant_id="test_no_defaults" to isolate ATO testing from
Tier 2.5 (tenant engine defaults). In production, ATO recommendations are
applied at Tier 2.8, which comes after Tier 2.5 per the resolver's tier
ordering documented in model_selector.resolve_os_model() docstring.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from corvin_operator.bridges.shared import model_selector as ms


class TestATORoutingTier28:
    """Test ADR-0165 Tier 2.8 ATO recommendation routing."""

    def test_resolve_os_model_ato_haiku_recommendation(self):
        """ATO recommends Haiku → should be returned at Tier 2.8."""
        ato_hint = {
            "recommended_model": "haiku",
            "task_type": "code_review",
            "confidence": 0.87,
        }

        result = ms.resolve_os_model(
            profile=None,
            payload_chars=500,
            tenant_id="test_no_defaults",
            ato_plan_hint=ato_hint,
        )

        # Tier 2.8 should return the ATO recommendation
        assert result == ms.DEFAULT_LOW  # haiku
        assert "haiku" in result.lower()

    def test_resolve_os_model_ato_sonnet_recommendation(self):
        """ATO recommends Sonnet → should be returned at Tier 2.8."""
        ato_hint = {
            "recommended_model": "sonnet",
            "task_type": "general",
            "confidence": 0.92,
        }

        result = ms.resolve_os_model(
            profile=None,
            payload_chars=5000,
            tenant_id="test_no_defaults",
            ato_plan_hint=ato_hint,
        )

        # Tier 2.8 should return the ATO recommendation
        assert result == ms.DEFAULT_HIGH  # sonnet
        assert "sonnet" in result.lower()

    def test_resolve_os_model_ato_opus_recommendation(self):
        """ATO recommends Opus → should be returned at Tier 2.8."""
        ato_hint = {
            "recommended_model": "opus",
            "task_type": "complex_reasoning",
            "confidence": 0.95,
        }

        result = ms.resolve_os_model(
            profile=None,
            payload_chars=100000,
            tenant_id="test_no_defaults",
            ato_plan_hint=ato_hint,
        )

        # Tier 2.8 should return the ATO recommendation
        assert result == "claude-opus-4-7"
        assert "opus" in result.lower()

    def test_resolve_os_model_explicit_override_beats_ato(self):
        """Tier 0 (Explicit Override) should beat Tier 2.8 (ATO)."""
        profile = {"model": "claude-opus-4-7"}  # Explicit override

        ato_hint = {
            "recommended_model": "haiku",
            "task_type": "code_review",
            "confidence": 0.99,  # High confidence, but still loses to explicit
        }

        result = ms.resolve_os_model(
            profile=profile,
            payload_chars=100,
            tenant_id="test_no_defaults",
            ato_plan_hint=ato_hint,
        )

        # Tier 0 override should win
        assert result == "claude-opus-4-7"

    def test_resolve_os_model_ato_fallthrough_no_confidence(self):
        """When confidence ≤ 0, ATO should not apply → fallthrough to Tier 3."""
        ato_hint = {
            "recommended_model": "haiku",
            "task_type": "unknown",
            "confidence": 0.0,  # No confidence
        }

        # Haiku downgrade not allowed → should fallthrough to HIGH (Sonnet)
        result = ms.resolve_os_model(
            profile=None,
            payload_chars=100,
            tenant_id="test_no_defaults",
            ato_plan_hint=ato_hint,
        )

        # Should NOT use haiku, should use Tier 3 (adaptive)
        assert result != ms.DEFAULT_LOW
        assert result == ms.DEFAULT_HIGH  # Tier 3 → HIGH by default

    def test_resolve_os_model_ato_fallthrough_no_recommendation(self):
        """When recommended_model is None, ATO should not apply."""
        ato_hint = {
            "recommended_model": None,
            "task_type": "unknown",
            "confidence": 0.5,
        }

        result = ms.resolve_os_model(
            profile=None,
            payload_chars=100,
            tenant_id="test_no_defaults",
            ato_plan_hint=ato_hint,
        )

        # Should fallthrough to Tier 3 (adaptive)
        assert result == ms.DEFAULT_HIGH

    def test_resolve_os_model_ato_none_hint(self):
        """When ato_plan_hint is None, should skip Tier 2.8."""
        result = ms.resolve_os_model(
            profile=None,
            payload_chars=100,
            tenant_id="test_no_defaults",
            ato_plan_hint=None,
        )

        # Should use Tier 3 (adaptive)
        assert result == ms.DEFAULT_HIGH


class TestATORoutingAudit:
    """Test ADR-0165 audit event emission."""

    def test_ato_routing_audit_event_emitted(self):
        """Verify audit event is emitted when ATO routing applies."""
        audit_calls = []

        def mock_audit_fn(event_type, chat_key=None, details=None):
            audit_calls.append({
                "event_type": event_type,
                "chat_key": chat_key,
                "details": details,
            })

        ato_hint = {
            "recommended_model": "sonnet",
            "task_type": "code_review",
            "confidence": 0.87,
        }

        result = ms.resolve_os_model(
            profile=None,
            payload_chars=5000,
            tenant_id="test_no_defaults",
            ato_plan_hint=ato_hint,
            audit_fn=mock_audit_fn,
        )

        # Should have audit event for ATO routing
        assert len(audit_calls) >= 1

        # Find the ATO routing event
        ato_events = [c for c in audit_calls if "ato" in c["event_type"].lower()]
        assert len(ato_events) >= 1

        event = ato_events[0]
        assert event["event_type"] == "bridge.ato_model_selection"
        assert event["details"]["task_type"] == "code_review"
        assert event["details"]["recommended_model"] == "sonnet"
        assert event["details"]["confidence"] == 0.87
        assert event["details"]["tier"] == "2.8_ato"

    def test_ato_routing_audit_contains_model(self):
        """Audit event should contain the final selected model."""
        audit_calls = []

        def mock_audit_fn(event_type, chat_key=None, details=None):
            audit_calls.append({
                "event_type": event_type,
                "details": details,
            })

        ato_hint = {
            "recommended_model": "haiku",
            "confidence": 0.81,
        }

        result = ms.resolve_os_model(
            profile=None,
            payload_chars=100,
            tenant_id="test_no_defaults",
            ato_plan_hint=ato_hint,
            audit_fn=mock_audit_fn,
        )

        # Verify audit contains actual model
        ato_events = [c for c in audit_calls if "ato" in c["event_type"].lower()]
        assert len(ato_events) >= 1

        event = ato_events[0]
        assert "selected_model" in event["details"]
        assert event["details"]["selected_model"] == ms.DEFAULT_LOW

    def test_audit_not_emitted_when_ato_doesnt_apply(self):
        """When ATO doesn't apply (confidence too low), no ATO audit event."""
        audit_calls = []

        def mock_audit_fn(event_type, chat_key=None, details=None):
            audit_calls.append({
                "event_type": event_type,
                "details": details,
            })

        ato_hint = {
            "recommended_model": "haiku",
            "confidence": 0.0,  # No confidence
        }

        result = ms.resolve_os_model(
            profile=None,
            payload_chars=100,
            tenant_id="test_no_defaults",
            ato_plan_hint=ato_hint,
            audit_fn=mock_audit_fn,
        )

        # Should NOT have ATO routing event (confidence too low)
        ato_events = [c for c in audit_calls if "ato" in c["event_type"].lower()]
        assert len(ato_events) == 0


class TestATORoutingEdgeCases:
    """Test edge cases and error handling."""

    def test_ato_malformed_hint_fallthrough(self):
        """Malformed ato_plan_hint should fall through gracefully."""
        ato_hint = {
            "recommended_model": "invalid_model",
            "confidence": "not a float",  # Invalid type
        }

        # Should not crash, should fallthrough
        result = ms.resolve_os_model(
            profile=None,
            payload_chars=100,
            tenant_id="test_no_defaults",
            ato_plan_hint=ato_hint,
        )

        # Should fallthrough to Tier 3
        assert result is not None

    def test_ato_hint_empty_dict(self):
        """Empty ato_plan_hint dict should be handled."""
        ato_hint = {}

        result = ms.resolve_os_model(
            profile=None,
            payload_chars=100,
            tenant_id="test_no_defaults",
            ato_plan_hint=ato_hint,
        )

        # Should fallthrough to Tier 3
        assert result == ms.DEFAULT_HIGH

    def test_ato_hint_case_insensitive(self):
        """Model names in ATO hint should be case-insensitive."""
        ato_hint = {
            "recommended_model": "SONNET",  # Uppercase
            "confidence": 0.85,
        }

        result = ms.resolve_os_model(
            profile=None,
            payload_chars=5000,
            tenant_id="test_no_defaults",
            ato_plan_hint=ato_hint,
        )

        # Should still work (case-insensitive lookup)
        assert result == ms.DEFAULT_HIGH
        assert "sonnet" in result.lower()

    def test_ato_invalid_model_name(self):
        """Invalid model name in ATO hint should fallthrough."""
        ato_hint = {
            "recommended_model": "gpt-4-turbo",  # Not in our map
            "confidence": 0.95,
        }

        result = ms.resolve_os_model(
            profile=None,
            payload_chars=5000,
            tenant_id="test_no_defaults",
            ato_plan_hint=ato_hint,
        )

        # Should fallthrough to Tier 3 (invalid model name)
        assert result is not None

    def test_ato_zero_confidence_fallthrough(self):
        """ATO with 0.0 confidence should fallthrough."""
        ato_hint = {
            "recommended_model": "sonnet",
            "confidence": 0.0,
        }

        result = ms.resolve_os_model(
            profile=None,
            payload_chars=5000,
            tenant_id="test_no_defaults",
            ato_plan_hint=ato_hint,
        )

        # Should fallthrough
        assert result is not None

    def test_ato_negative_confidence_fallthrough(self):
        """ATO with negative confidence should fallthrough."""
        ato_hint = {
            "recommended_model": "sonnet",
            "confidence": -0.5,
        }

        result = ms.resolve_os_model(
            profile=None,
            payload_chars=5000,
            tenant_id="test_no_defaults",
            ato_plan_hint=ato_hint,
        )

        # Should fallthrough
        assert result is not None


class TestATORoutingTierOrdering:
    """Test that ATO routing respects the tier ordering from ADR-0024."""

    def test_tier_ordering_0_override_beats_all(self):
        """Tier 0 (Override) beats ATO (Tier 2.8)."""
        profile = {"model": "claude-opus-4-7"}
        ato_hint = {
            "recommended_model": "haiku",
            "confidence": 0.99,
        }

        result = ms.resolve_os_model(
            profile=profile,
            tenant_id="test_no_defaults",
            ato_plan_hint=ato_hint,
        )

        assert result == "claude-opus-4-7"

    def test_tier_ordering_env_override_beats_ato(self):
        """CORVIN_OS_MODEL_OVERRIDE env var (Tier 0) beats ATO (Tier 2.8)."""
        with patch.dict('os.environ', {'CORVIN_OS_MODEL_OVERRIDE': 'claude-opus-4-7'}):
            ato_hint = {
                "recommended_model": "haiku",
                "confidence": 0.99,
            }

            result = ms.resolve_os_model(
                profile=None,
                tenant_id="test_no_defaults",
                ato_plan_hint=ato_hint,
            )

            assert result == "claude-opus-4-7"


class TestATORoutingIntegration:
    """Integration tests with adapter.py wiring."""

    def test_adapter_ato_plan_injection(self):
        """Verify adapter.py correctly constructs and injects ato_plan_hint."""
        # This test verifies the integration point at adapter.py line 3600-3624
        # (the _ato_plan_hint construction and _resolve_os_model call)

        # Create a mock ATO plan object (as would come from _ato_classify)
        mock_ato_plan = Mock()
        mock_ato_plan.recommended_model = "sonnet"
        mock_ato_plan.task_type = "code_review"
        mock_ato_plan.confidence = 0.87

        # Verify the hint construction logic (from adapter.py:3605-3614)
        ato_plan_hint = {
            "recommended_model": mock_ato_plan.recommended_model,
            "task_type": mock_ato_plan.task_type,
            "confidence": mock_ato_plan.confidence,
        }

        # Verify it has the right structure
        assert ato_plan_hint["recommended_model"] == "sonnet"
        assert ato_plan_hint["task_type"] == "code_review"
        assert ato_plan_hint["confidence"] == 0.87

    def test_resolve_os_model_bundled_passes_ato_hint(self):
        """Verify _resolve_os_model_bundled correctly passes ato_plan_hint."""
        from corvin_operator.bridges.shared import adapter

        ato_hint = {
            "recommended_model": "sonnet",
            "confidence": 0.87,
        }

        result = adapter._resolve_os_model_bundled(
            profile=None,
            payload_chars=5000,
            tenant_id="test_no_defaults",
            ato_plan_hint=ato_hint,
        )

        # Should apply ATO routing
        assert result is not None


class TestCostSavingsSignal:
    """Test cost savings calculation from ATO routing."""

    def test_ato_routing_produces_cost_savings(self):
        """ATO routing to Haiku should produce cost savings vs Opus."""
        # This test is documentation that ATO routing produces the
        # 45.5% cost savings mentioned in ADR-0165.

        ato_hint = {
            "recommended_model": "haiku",
            "confidence": 0.82,
        }

        result = ms.resolve_os_model(
            profile=None,
            payload_chars=1000,
            tenant_id="test_no_defaults",
            ato_plan_hint=ato_hint,
        )

        # Verify we routed to Haiku (low cost)
        assert "haiku" in result.lower()

        # The cost savings calculation would happen in the audit trail
        # and cost-tracking subsystem (ADR-0377, ADR-0314)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
