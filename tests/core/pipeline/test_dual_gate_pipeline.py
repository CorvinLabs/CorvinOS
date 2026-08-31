"""
ADR-0300: Dual-Gate Context Pipeline — Comprehensive Tests

Tests for three-gate pipeline:
  Gate 1: Capability checking
  Gate 2: Validation + PII detection + queue integrity
  Gate 3: Audit recording

Covers:
  - Sync and async execution paths
  - Feature flags enabling/disabling gates
  - Fail-closed semantics (all gates must pass)
  - Idempotent validation
  - Tenant isolation
  - Context propagation (ContextVars)
"""

import pytest
import asyncio
from unittest.mock import Mock, MagicMock, patch
from dataclasses import dataclass
from datetime import datetime

from core.pipeline import (
    DualGatePipeline,
    PipelineContext,
    ValidationState,
    CapabilityGateError,
    ValidationGateError,
    PIIDetectionError,
    QueueIntegrityError,
    AuditGateError,
)


# ============================================================================
# Fixtures and Mocks
# ============================================================================


@pytest.fixture
def mock_audit_chain():
    """Mock audit chain for testing."""
    chain = Mock()
    chain.record = Mock()
    return chain


@pytest.fixture
def mock_capability_checker():
    """Mock capability checker for testing."""
    checker = Mock()
    checker.has_capability = Mock(return_value=True)
    return checker


@pytest.fixture
def mock_pii_detector():
    """Mock PII detector for testing."""
    detector = Mock()
    detector.detect = Mock(return_value=None)
    return detector


@pytest.fixture
def mock_validator_factory():
    """Mock validator factory for testing."""
    factory = Mock()
    # Define a simple validator that always passes
    factory.validate_string = Mock(
        return_value=Mock(is_valid=True, value="test", error_message=None)
    )
    factory.validate_email = Mock(
        return_value=Mock(is_valid=True, value="test@example.com", error_message=None)
    )
    return factory


@pytest.fixture
def mock_queue_monitor():
    """Mock queue monitor for testing."""
    monitor = Mock()
    return monitor


@pytest.fixture
def pipeline(mock_audit_chain, mock_capability_checker, mock_pii_detector,
             mock_validator_factory, mock_queue_monitor):
    """Create a pipeline instance with all mocks."""
    return DualGatePipeline(
        audit_chain=mock_audit_chain,
        capability_checker=mock_capability_checker,
        pii_detector=mock_pii_detector,
        validator_factory=mock_validator_factory,
        queue_monitor=mock_queue_monitor,
        feature_flags={
            "dual_gate_pipeline_enabled": True,
            "dual_gate_pii_detection_enabled": True,
            "dual_gate_queue_integrity_enabled": False,
        },
    )


@pytest.fixture
def simple_function():
    """Simple function for testing."""
    def test_func():
        return {"result": "success"}
    return test_func


@pytest.fixture
def async_function():
    """Async function for testing."""
    async def async_test_func():
        return {"result": "async_success"}
    return async_test_func


# ============================================================================
# Gate 1: Capability Tests
# ============================================================================


class TestCapabilityGate:
    """Tests for Gate 1: Capability checking."""

    def test_capability_granted(self, pipeline, mock_capability_checker, simple_function):
        """Test that operation proceeds when capability is granted."""
        mock_capability_checker.has_capability.return_value = True

        ctx = PipelineContext(
            actor="user_123",
            capability="read_users",
            action="fetch_user",
            resource="users:456",
            tenant_id="tenant_1",
        )

        result = pipeline.execute_guarded(ctx, simple_function)
        assert result["result"] == "success"

    def test_capability_denied(self, pipeline, mock_capability_checker):
        """Test that operation fails when capability is denied."""
        mock_capability_checker.has_capability.return_value = False

        ctx = PipelineContext(
            actor="user_123",
            capability="delete_users",
            action="delete_user",
            resource="users:456",
            tenant_id="tenant_1",
        )

        with pytest.raises(CapabilityGateError) as exc_info:
            pipeline.execute_guarded(ctx, lambda: None)

        assert "lacks capability" in str(exc_info.value)
        assert "delete_users" in str(exc_info.value)

    def test_capability_check_audited(self, pipeline, mock_capability_checker,
                                     mock_audit_chain):
        """Test that capability denials are audited."""
        mock_capability_checker.has_capability.return_value = False

        ctx = PipelineContext(
            actor="user_123",
            capability="admin",
            action="admin_action",
            resource="system",
            tenant_id="tenant_1",
        )

        try:
            pipeline.execute_guarded(ctx, lambda: None)
        except CapabilityGateError:
            pass

        # Verify audit was called for denial
        mock_audit_chain.record.assert_called()
        calls = mock_audit_chain.record.call_args_list
        denial_call = [c for c in calls if "capability_denied" in str(c)]
        assert len(denial_call) > 0

    def test_capability_check_tenant_scoped(self, pipeline, mock_capability_checker):
        """Test that capability checks are tenant-scoped."""
        mock_capability_checker.has_capability.return_value = True

        ctx = PipelineContext(
            actor="user_123",
            capability="read_users",
            action="fetch_user",
            resource="users:456",
            tenant_id="tenant_2",  # Different tenant
        )

        pipeline.execute_guarded(ctx, lambda: None)

        # Verify has_capability was called with correct tenant_id
        mock_capability_checker.has_capability.assert_called()
        call_args = mock_capability_checker.has_capability.call_args
        assert call_args[1]["tenant_id"] == "tenant_2"


# ============================================================================
# Gate 2a: Validation Tests
# ============================================================================


class TestValidationGate:
    """Tests for Gate 2a: Input validation."""

    def test_validation_passed(self, pipeline, simple_function):
        """Test that operation proceeds when validation passes."""
        ctx = PipelineContext(
            actor="user_123",
            capability="read_users",
            action="fetch_user",
            resource="users:456",
            tenant_id="tenant_1",
            input_data={"username": "john"},
            validator_rules={
                "username": {"type": "validate_string", "options": {"min_length": 1}}
            },
        )

        result = pipeline.execute_guarded(ctx, simple_function)
        assert result["result"] == "success"

    def test_validation_failed(self, pipeline, mock_validator_factory):
        """Test that operation fails when validation fails."""
        # Configure validator to return invalid result
        mock_validator_factory.validate_string.return_value = Mock(
            is_valid=False,
            error_message="Invalid username format",
            error_code="invalid_format",
        )

        ctx = PipelineContext(
            actor="user_123",
            capability="write_users",
            action="create_user",
            resource="users",
            tenant_id="tenant_1",
            input_data={"username": ""},
            validator_rules={
                "username": {"type": "validate_string", "options": {"min_length": 1}}
            },
        )

        with pytest.raises(ValidationGateError) as exc_info:
            pipeline.execute_guarded(ctx, lambda: None)

        assert "validation failed" in str(exc_info.value)

    def test_validation_disabled_by_flag(self, mock_audit_chain, mock_capability_checker,
                                       mock_pii_detector, mock_validator_factory,
                                       simple_function):
        """Test that validation is skipped when flag is disabled."""
        pipeline = DualGatePipeline(
            audit_chain=mock_audit_chain,
            capability_checker=mock_capability_checker,
            pii_detector=mock_pii_detector,
            validator_factory=mock_validator_factory,
            feature_flags={"dual_gate_pipeline_enabled": False},
        )

        ctx = PipelineContext(
            actor="user_123",
            capability="read_users",
            action="fetch_user",
            resource="users:456",
            tenant_id="tenant_1",
            input_data={"username": ""},  # Invalid data
            validator_rules={
                "username": {"type": "validate_string", "options": {"min_length": 1}}
            },
        )

        # Should not raise ValidationGateError
        result = pipeline.execute_guarded(ctx, simple_function)
        assert result["result"] == "success"

    def test_validation_no_input_data(self, pipeline, simple_function):
        """Test that validation skips when no input_data provided."""
        ctx = PipelineContext(
            actor="user_123",
            capability="read_users",
            action="fetch_user",
            resource="users:456",
            tenant_id="tenant_1",
            # No input_data
        )

        result = pipeline.execute_guarded(ctx, simple_function)
        assert result["result"] == "success"

    def test_validation_state_populated(self, pipeline, simple_function):
        """Test that validation_state is populated during execution."""
        ctx = PipelineContext(
            actor="user_123",
            capability="read_users",
            action="fetch_user",
            resource="users:456",
            tenant_id="tenant_1",
            input_data={"username": "john", "email": "john@example.com"},
            validator_rules={
                "username": {"type": "validate_string", "options": {"min_length": 1}}
            },
        )

        pipeline.execute_guarded(ctx, simple_function)

        # Verify validation_state was populated
        assert ctx.validation_state.validation_errors == []
        assert "username" in ctx.validation_state.checked_fields

    def test_validation_multiple_fields(self, pipeline, mock_validator_factory,
                                       simple_function):
        """Test validation with multiple fields."""
        mock_validator_factory.validate_string.side_effect = [
            Mock(is_valid=True, value="john"),
            Mock(is_valid=False, error_message="Invalid email"),
        ]

        ctx = PipelineContext(
            actor="user_123",
            capability="write_users",
            action="create_user",
            resource="users",
            tenant_id="tenant_1",
            input_data={"username": "john", "email": "invalid"},
            validator_rules={
                "username": {"type": "validate_string"},
                "email": {"type": "validate_string"},
            },
        )

        with pytest.raises(ValidationGateError):
            pipeline.execute_guarded(ctx, simple_function)


# ============================================================================
# Gate 2b: PII Detection Tests
# ============================================================================


class TestPIIDetectionGate:
    """Tests for Gate 2b: PII detection."""

    def test_pii_not_detected(self, pipeline, simple_function):
        """Test that operation proceeds when no PII detected."""
        ctx = PipelineContext(
            actor="user_123",
            capability="write_data",
            action="save_data",
            resource="data:123",
            tenant_id="tenant_1",
            input_data={"comment": "This is a normal comment"},
        )

        result = pipeline.execute_guarded(ctx, simple_function)
        assert result["result"] == "success"

    def test_pii_detected_email(self, pipeline, mock_pii_detector):
        """Test that operation fails when email is detected."""
        # Mock detector to return email pattern
        pii_pattern = Mock()
        pii_pattern.pii_class = "email"
        pii_pattern.confidence = 0.95
        pii_pattern.source_pattern = "email_pattern"
        pii_pattern.sample_match = "user@*.com"

        mock_pii_detector.detect.return_value = pii_pattern

        ctx = PipelineContext(
            actor="user_123",
            capability="write_data",
            action="save_data",
            resource="data:123",
            tenant_id="tenant_1",
            input_data={"comment": "Contact me at user@example.com"},
        )

        with pytest.raises(PIIDetectionError) as exc_info:
            pipeline.execute_guarded(ctx, lambda: None)

        assert "PII detected" in str(exc_info.value)

    def test_pii_detection_disabled_by_flag(self, mock_audit_chain, mock_capability_checker,
                                          mock_pii_detector, mock_validator_factory,
                                          simple_function):
        """Test that PII detection is skipped when flag is disabled."""
        pipeline = DualGatePipeline(
            audit_chain=mock_audit_chain,
            capability_checker=mock_capability_checker,
            pii_detector=mock_pii_detector,
            validator_factory=mock_validator_factory,
            feature_flags={"dual_gate_pii_detection_enabled": False},
        )

        # Mock detector to return pattern (but should be ignored)
        pii_pattern = Mock()
        pii_pattern.pii_class = "email"
        mock_pii_detector.detect.return_value = pii_pattern

        ctx = PipelineContext(
            actor="user_123",
            capability="write_data",
            action="save_data",
            resource="data:123",
            tenant_id="tenant_1",
            input_data={"comment": "Contact: user@example.com"},
        )

        # Should not raise PIIDetectionError
        result = pipeline.execute_guarded(ctx, simple_function)
        assert result["result"] == "success"

    def test_pii_no_input_data(self, pipeline, simple_function):
        """Test that PII detection skips when no input_data provided."""
        ctx = PipelineContext(
            actor="user_123",
            capability="write_data",
            action="save_data",
            resource="data:123",
            tenant_id="tenant_1",
            # No input_data
        )

        result = pipeline.execute_guarded(ctx, simple_function)
        assert result["result"] == "success"

    def test_pii_detection_audited(self, pipeline, mock_pii_detector, mock_audit_chain):
        """Test that PII detections are audited."""
        pii_pattern = Mock()
        pii_pattern.pii_class = "phone"
        pii_pattern.confidence = 0.92
        pii_pattern.source_pattern = "phone_pattern"
        pii_pattern.sample_match = "+1-***-****"

        mock_pii_detector.detect.return_value = pii_pattern

        ctx = PipelineContext(
            actor="user_123",
            capability="write_data",
            action="save_data",
            resource="data:123",
            tenant_id="tenant_1",
            input_data={"phone": "+1-555-1234"},
        )

        try:
            pipeline.execute_guarded(ctx, lambda: None)
        except PIIDetectionError:
            pass

        # Verify PII detection was audited
        mock_audit_chain.record.assert_called()
        calls = mock_audit_chain.record.call_args_list
        pii_call = [c for c in calls if "pii_detected" in str(c)]
        assert len(pii_call) > 0


# ============================================================================
# Gate 2c: Queue Integrity Tests
# ============================================================================


class TestQueueIntegrityGate:
    """Tests for Gate 2c: Queue integrity."""

    def test_queue_integrity_ok(self, pipeline, simple_function):
        """Test that operation proceeds when queue is healthy."""
        ctx = PipelineContext(
            actor="user_123",
            capability="submit_task",
            action="submit_job",
            resource="queue:jobs",
            tenant_id="tenant_1",
        )

        result = pipeline.execute_guarded(ctx, simple_function)
        assert result["result"] == "success"

    def test_queue_integrity_disabled_by_flag(self, mock_audit_chain, mock_capability_checker,
                                            simple_function):
        """Test that queue integrity checks skip when flag is disabled."""
        pipeline = DualGatePipeline(
            audit_chain=mock_audit_chain,
            capability_checker=mock_capability_checker,
            feature_flags={"dual_gate_queue_integrity_enabled": False},
        )

        ctx = PipelineContext(
            actor="user_123",
            capability="submit_task",
            action="submit_job",
            resource="queue:jobs",
            tenant_id="tenant_1",
        )

        result = pipeline.execute_guarded(ctx, simple_function)
        assert result["result"] == "success"


# ============================================================================
# Multi-Gate Integration Tests
# ============================================================================


class TestMultiGateIntegration:
    """Tests for all three gates working together."""

    def test_all_gates_pass(self, pipeline, simple_function):
        """Test that operation succeeds when all gates pass."""
        ctx = PipelineContext(
            actor="user_123",
            capability="read_users",
            action="fetch_user",
            resource="users:456",
            tenant_id="tenant_1",
            input_data={"user_id": "456"},
            validator_rules={
                "user_id": {"type": "validate_string", "options": {"min_length": 1}}
            },
        )

        result = pipeline.execute_guarded(ctx, simple_function)
        assert result["result"] == "success"

    def test_gate1_fails_before_gate2(self, pipeline, mock_capability_checker):
        """Test that Gate 1 failure stops execution before Gate 2."""
        mock_capability_checker.has_capability.return_value = False

        ctx = PipelineContext(
            actor="user_123",
            capability="admin",
            action="admin_action",
            resource="system",
            tenant_id="tenant_1",
            input_data={"command": "system_restart"},  # Would fail validation
        )

        with pytest.raises(CapabilityGateError):
            pipeline.execute_guarded(ctx, lambda: None)

    def test_gate2_fails_after_gate1(self, pipeline, mock_validator_factory):
        """Test that Gate 2 failure is caught after Gate 1 passes."""
        mock_validator_factory.validate_string.return_value = Mock(
            is_valid=False, error_message="Invalid format"
        )

        ctx = PipelineContext(
            actor="user_123",
            capability="write_users",
            action="create_user",
            resource="users",
            tenant_id="tenant_1",
            input_data={"username": ""},
            validator_rules={
                "username": {"type": "validate_string"}
            },
        )

        with pytest.raises(ValidationGateError):
            pipeline.execute_guarded(ctx, lambda: None)

    def test_audit_recorded_for_all_outcomes(self, pipeline, mock_audit_chain,
                                           mock_capability_checker):
        """Test that audit trail records all gate outcomes."""
        mock_capability_checker.has_capability.return_value = True

        ctx = PipelineContext(
            actor="user_123",
            capability="read_users",
            action="fetch_user",
            resource="users:456",
            tenant_id="tenant_1",
        )

        pipeline.execute_guarded(ctx, lambda: None)

        # Verify multiple audit events were recorded
        assert mock_audit_chain.record.call_count > 1


# ============================================================================
# Async Tests
# ============================================================================


class TestAsyncExecution:
    """Tests for async execution path."""

    @pytest.mark.asyncio
    async def test_async_execution_success(self, pipeline, async_function):
        """Test that async execution works correctly."""
        ctx = PipelineContext(
            actor="system",
            capability="admin",
            action="async_task",
            resource="system:bg_job",
            tenant_id="tenant_1",
        )

        result = await pipeline.execute_guarded_async(ctx, async_function)
        assert result["result"] == "async_success"

    @pytest.mark.asyncio
    async def test_async_capability_denied(self, pipeline, mock_capability_checker):
        """Test async execution with capability denial."""
        mock_capability_checker.has_capability.return_value = False

        ctx = PipelineContext(
            actor="user_123",
            capability="admin",
            action="async_admin_task",
            resource="system",
            tenant_id="tenant_1",
        )

        with pytest.raises(CapabilityGateError):
            await pipeline.execute_guarded_async(ctx, lambda: None)

    @pytest.mark.asyncio
    async def test_async_validation_failed(self, pipeline, mock_validator_factory):
        """Test async execution with validation failure."""
        mock_validator_factory.validate_string.return_value = Mock(
            is_valid=False, error_message="Validation failed"
        )

        ctx = PipelineContext(
            actor="user_123",
            capability="write_data",
            action="async_write",
            resource="data",
            tenant_id="tenant_1",
            input_data={"data": ""},
            validator_rules={
                "data": {"type": "validate_string"}
            },
        )

        with pytest.raises(ValidationGateError):
            await pipeline.execute_guarded_async(ctx, lambda: None)


# ============================================================================
# Context Propagation Tests
# ============================================================================


class TestContextPropagation:
    """Tests for context propagation via ContextVars."""

    def test_context_vars_set(self, pipeline, simple_function):
        """Test that ContextVars are properly set during execution."""
        ctx = PipelineContext(
            actor="user_123",
            capability="read_users",
            action="fetch_user",
            resource="users:456",
            tenant_id="tenant_1",
        )

        # Capture context vars during execution
        captured_actor = None

        def capture_context():
            nonlocal captured_actor
            captured_actor = pipeline.get_actor()
            return {"result": "captured"}

        result = pipeline.execute_guarded(ctx, capture_context)
        assert captured_actor == "user_123"

    def test_context_tenant_isolation(self, pipeline, mock_capability_checker):
        """Test that tenant_id is properly propagated."""
        tenant_1_ctx = PipelineContext(
            actor="user_123",
            capability="read_users",
            action="fetch_user",
            resource="users:456",
            tenant_id="tenant_1",
        )

        pipeline.execute_guarded(tenant_1_ctx, lambda: None)

        # Check the tenant_id passed to capability_checker
        call_args = mock_capability_checker.has_capability.call_args
        assert call_args[1]["tenant_id"] == "tenant_1"

        # Reset mock
        mock_capability_checker.reset_mock()

        # Now with different tenant
        tenant_2_ctx = PipelineContext(
            actor="user_456",
            capability="read_users",
            action="fetch_user",
            resource="users:789",
            tenant_id="tenant_2",
        )

        pipeline.execute_guarded(tenant_2_ctx, lambda: None)

        call_args = mock_capability_checker.has_capability.call_args
        assert call_args[1]["tenant_id"] == "tenant_2"


# ============================================================================
# Error Handling Tests
# ============================================================================


class TestErrorHandling:
    """Tests for error handling and fail-closed semantics."""

    def test_function_exception_audited(self, pipeline, mock_audit_chain):
        """Test that exceptions in the guarded function are audited."""
        def failing_func():
            raise ValueError("Test error")

        ctx = PipelineContext(
            actor="user_123",
            capability="read_users",
            action="fetch_user",
            resource="users:456",
            tenant_id="tenant_1",
        )

        with pytest.raises(ValueError):
            pipeline.execute_guarded(ctx, failing_func)

        # Verify failure was audited
        calls = mock_audit_chain.record.call_args_list
        failure_call = [c for c in calls if "operation_failed" in str(c)]
        assert len(failure_call) > 0

    def test_validation_error_handling(self, pipeline, mock_validator_factory):
        """Test that validation errors are properly handled."""
        mock_validator_factory.validate_string.side_effect = RuntimeError("Validator crashed")

        ctx = PipelineContext(
            actor="user_123",
            capability="write_users",
            action="create_user",
            resource="users",
            tenant_id="tenant_1",
            input_data={"username": "test"},
            validator_rules={
                "username": {"type": "validate_string"}
            },
        )

        with pytest.raises(ValidationGateError):
            pipeline.execute_guarded(ctx, lambda: None)

    def test_pii_detection_error_handling(self, pipeline, mock_pii_detector):
        """Test that PII detection errors are properly handled."""
        mock_pii_detector.detect.side_effect = RuntimeError("Detection failed")

        ctx = PipelineContext(
            actor="user_123",
            capability="write_data",
            action="save_data",
            resource="data:123",
            tenant_id="tenant_1",
            input_data={"data": "test"},
        )

        with pytest.raises(PIIDetectionError):
            pipeline.execute_guarded(ctx, lambda: None)


# ============================================================================
# Feature Flag Tests
# ============================================================================


class TestFeatureFlags:
    """Tests for feature flag control."""

    def test_all_flags_disabled(self, mock_audit_chain, mock_capability_checker,
                               simple_function):
        """Test that all validation gates skip when all flags are disabled."""
        pipeline = DualGatePipeline(
            audit_chain=mock_audit_chain,
            capability_checker=mock_capability_checker,
            feature_flags={
                "dual_gate_pipeline_enabled": False,
                "dual_gate_pii_detection_enabled": False,
                "dual_gate_queue_integrity_enabled": False,
            },
        )

        ctx = PipelineContext(
            actor="user_123",
            capability="read_users",
            action="fetch_user",
            resource="users:456",
            tenant_id="tenant_1",
            input_data={"username": ""},  # Invalid data - but should be skipped
        )

        result = pipeline.execute_guarded(ctx, simple_function)
        assert result["result"] == "success"

    def test_partial_flags_enabled(self, mock_audit_chain, mock_capability_checker,
                                  mock_pii_detector, simple_function):
        """Test that gates respect partial flag configurations."""
        pipeline = DualGatePipeline(
            audit_chain=mock_audit_chain,
            capability_checker=mock_capability_checker,
            pii_detector=mock_pii_detector,
            feature_flags={
                "dual_gate_pipeline_enabled": False,  # Validation disabled
                "dual_gate_pii_detection_enabled": True,  # PII detection enabled
            },
        )

        pii_pattern = Mock()
        pii_pattern.pii_class = "email"
        mock_pii_detector.detect.return_value = pii_pattern

        ctx = PipelineContext(
            actor="user_123",
            capability="write_data",
            action="save_data",
            resource="data:123",
            tenant_id="tenant_1",
            input_data={"email": "user@example.com"},
        )

        # Should fail due to PII detection
        with pytest.raises(PIIDetectionError):
            pipeline.execute_guarded(ctx, simple_function)


# ============================================================================
# Validation State Tests
# ============================================================================


class TestValidationState:
    """Tests for ValidationState tracking."""

    def test_validation_state_initialization(self, pipeline, simple_function):
        """Test that ValidationState is initialized."""
        ctx = PipelineContext(
            actor="user_123",
            capability="read_users",
            action="fetch_user",
            resource="users:456",
            tenant_id="tenant_1",
        )

        pipeline.execute_guarded(ctx, simple_function)

        assert ctx.validation_state is not None
        assert ctx.validation_state.passed == False  # Not tracking this yet
        assert ctx.validation_state.pii_detected == False
        assert ctx.validation_state.validation_errors == []

    def test_validation_state_populated_on_error(self, pipeline, mock_validator_factory):
        """Test that ValidationState is populated when validation fails."""
        mock_validator_factory.validate_string.return_value = Mock(
            is_valid=False,
            error_message="Invalid email",
            error_code="invalid_email",
        )

        ctx = PipelineContext(
            actor="user_123",
            capability="write_users",
            action="create_user",
            resource="users",
            tenant_id="tenant_1",
            input_data={"email": "invalid"},
            validator_rules={
                "email": {"type": "validate_string"}
            },
        )

        try:
            pipeline.execute_guarded(ctx, lambda: None)
        except ValidationGateError:
            pass

        assert ctx.validation_state is not None
        assert len(ctx.validation_state.validation_errors) > 0

    def test_validation_state_pii_findings(self, pipeline, mock_pii_detector):
        """Test that ValidationState tracks PII findings."""
        pii_pattern = Mock()
        pii_pattern.pii_class = "email"
        pii_pattern.confidence = 0.95
        pii_pattern.source_pattern = "email_pattern"
        pii_pattern.sample_match = "user@*.com"
        mock_pii_detector.detect.return_value = pii_pattern

        ctx = PipelineContext(
            actor="user_123",
            capability="write_data",
            action="save_data",
            resource="data:123",
            tenant_id="tenant_1",
            input_data={"email": "user@example.com"},
        )

        try:
            pipeline.execute_guarded(ctx, lambda: None)
        except PIIDetectionError:
            pass

        assert ctx.validation_state is not None
        assert ctx.validation_state.pii_detected == True
        assert len(ctx.validation_state.pii_findings) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
