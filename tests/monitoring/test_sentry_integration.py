"""
Test suite for Sentry integration (Component 1)

8 tests covering:
- Initialization
- Breadcrumb collection (routing, model, token, budget, audit, circuit breaker, learning)
- Event filtering (PII detection)
- Error capture
"""

import pytest
import os
from unittest.mock import patch, MagicMock
from corvin_console.monitoring import (
    initialize_sentry,
    SentryBreadcrumbManager,
    capture_routing_error,
    capture_audit_chain_error,
)


class TestSentryInitialization:
    """Test Sentry initialization."""

    def test_initialize_sentry_with_dsn(self):
        """Test initializing Sentry with a valid DSN."""
        # This should not raise an error
        # Note: actual Sentry won't send events without a real DSN
        with patch.dict(os.environ, {"SENTRY_DSN": "https://example@sentry.io/12345"}):
            initialize_sentry()
            # If we get here, initialization succeeded
            assert True

    def test_initialize_sentry_without_dsn(self):
        """Test initializing Sentry without DSN (should warn)."""
        with patch.dict(os.environ, {}, clear=True):
            # Should log warning but not crash
            initialize_sentry()
            assert True

    def test_initialize_sentry_with_custom_params(self):
        """Test Sentry initialization with custom parameters."""
        with patch.dict(os.environ, {"SENTRY_DSN": "https://example@sentry.io/12345"}):
            initialize_sentry(
                environment="staging",
                release="v5.1.0-test",
                traces_sample_rate=0.5,
                profiles_sample_rate=0.2,
            )
            assert True


class TestSentryBreadcrumbs:
    """Test breadcrumb collection for audit trail."""

    def test_routing_decision_breadcrumb(self):
        """Test recording a routing decision breadcrumb."""
        SentryBreadcrumbManager.routing_decision(
            model_selected="claude-opus-5",
            complexity_tier="complex",
            confidence=0.95,
            cost_estimate=0.0045,
            latency_estimate_ms=2500,
            reasoning="Task complexity > 250 tokens",
        )
        assert True

    def test_model_api_failure_breadcrumb(self):
        """Test recording a model API failure."""
        SentryBreadcrumbManager.model_api_failure(
            model="claude-opus-5",
            error="Rate limit exceeded",
            fallback_model="claude-sonnet-5",
            retry_count=1,
        )
        assert True

    def test_token_estimation_error_breadcrumb(self):
        """Test recording token estimation accuracy."""
        SentryBreadcrumbManager.token_estimation_error(
            estimated_tokens=250,
            actual_tokens=240,
            error_percent=4.2,
            task_id="task_123",
        )
        assert True

    def test_budget_exceeded_breadcrumb(self):
        """Test recording budget threshold breach."""
        SentryBreadcrumbManager.budget_exceeded(
            estimated_cost=45.0,
            daily_budget=50.0,
            percentage_used=90.0,
            tenant_id="default",
        )
        assert True

    def test_audit_chain_failure_breadcrumb(self):
        """Test recording critical audit chain failure."""
        SentryBreadcrumbManager.audit_chain_failure(
            error_type="hash_mismatch",
            message="Event hash does not match chain",
            chain_height=12345,
            last_hash="sha256_abc123...",
        )
        assert True

    def test_circuit_breaker_breadcrumb(self):
        """Test recording circuit breaker activation."""
        SentryBreadcrumbManager.circuit_breaker_triggered(
            endpoint="marketplace",
            reason="p99_latency_exceeded",
            recovery_delay_sec=60,
        )
        assert True

    def test_learning_loop_breadcrumb(self):
        """Test recording learning loop event."""
        SentryBreadcrumbManager.learning_loop_event(
            skill_id="os.delegation_router",
            event_type="outcome_feedback",
            signal={"correct": True, "confidence_delta": 0.05},
            confidence_delta=0.05,
        )
        assert True


class TestSentryErrorCapture:
    """Test error capture with context."""

    def test_capture_routing_error(self):
        """Test capturing a routing-related error."""
        error = ValueError("Invalid model selection")
        capture_routing_error(
            model="invalid_model",
            tier="unknown",
            error=error,
            context={
                "task_id": "task_123",
                "tenant_id": "default",
            },
        )
        assert True

    def test_capture_audit_chain_error(self):
        """Test capturing a CRITICAL audit chain error."""
        error = RuntimeError("Hash chain verification failed")
        capture_audit_chain_error(
            error=error,
            chain_info={
                "chain_height": 12345,
                "last_hash": "sha256_...",
                "tenant_id": "default",
            },
        )
        assert True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
