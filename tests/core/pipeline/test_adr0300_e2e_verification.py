"""
ADR-0300 E2E Verification: Dual-Gate Pipeline Integration Tests

Demonstrates both Flask route and async handler working correctly
with the dual-gate pipeline (Capability + Validation + Audit gates).
"""

import pytest
import asyncio
from unittest.mock import Mock, MagicMock, patch
import json

# Import pipeline components
from core.pipeline import (
    DualGatePipeline,
    PipelineContext,
    CapabilityGateError,
    ValidationGateError,
    PIIDetectionError,
)


# ============================================================================
# Flask E2E Test
# ============================================================================


class TestFlaskE2EIntegration:
    """E2E tests for Flask route integration."""

    @pytest.fixture
    def flask_app(self):
        """Create a minimal Flask app for testing."""
        try:
            from flask import Flask, g, request, jsonify
        except ImportError:
            pytest.skip("Flask not installed")

        app = Flask(__name__)

        # Initialize pipeline in app config
        mock_audit_chain = Mock()
        mock_capability_checker = Mock()
        mock_capability_checker.has_capability.return_value = True

        pipeline = DualGatePipeline(
            audit_chain=mock_audit_chain,
            capability_checker=mock_capability_checker,
            feature_flags={
                "dual_gate_pipeline_enabled": True,
                "dual_gate_pii_detection_enabled": True,
            },
        )

        app.config["pipeline"] = pipeline
        app.config["mock_audit_chain"] = mock_audit_chain
        app.config["mock_capability_checker"] = mock_capability_checker

        # Middleware to set user context
        @app.before_request
        def set_user_context():
            g.user_id = request.headers.get("X-User-ID", "user_123")
            g.tenant_id = request.headers.get("X-Tenant-ID", "tenant_1")

        # Protected route using dual-gate pipeline
        @app.route("/api/users/<user_id>", methods=["GET"])
        def get_user(user_id):
            """Fetch user - dual-gate protected."""
            from flask import current_app

            pipeline = current_app.config["pipeline"]

            ctx = PipelineContext(
                actor=g.user_id,
                capability="read_users",
                action=f"GET /api/users/{user_id}",
                resource=f"users:{user_id}",
                tenant_id=g.tenant_id,
            )

            def fetch_user_impl():
                return jsonify({"user_id": user_id, "name": "John Doe"})

            return pipeline.execute_guarded(ctx, fetch_user_impl)

        # Route with input validation
        @app.route("/api/users", methods=["POST"])
        def create_user():
            """Create user - with validation."""
            from flask import current_app

            pipeline = current_app.config["pipeline"]
            data = request.get_json() or {}

            ctx = PipelineContext(
                actor=g.user_id,
                capability="write_users",
                action="POST /api/users",
                resource="users",
                tenant_id=g.tenant_id,
                input_data=data,
            )

            def create_user_impl():
                return jsonify({"user_id": "new_user", "name": data.get("name")})

            return pipeline.execute_guarded(ctx, create_user_impl)

        return app

    def test_flask_route_success(self, flask_app):
        """Test successful Flask route execution."""
        client = flask_app.test_client()

        response = client.get(
            "/api/users/456",
            headers={
                "X-User-ID": "user_123",
                "X-Tenant-ID": "tenant_1",
            }
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["user_id"] == "456"
        assert data["name"] == "John Doe"

    def test_flask_route_capability_denied(self, flask_app):
        """Test Flask route with capability denial."""
        # Configure capability checker to deny access
        mock_checker = flask_app.config["mock_capability_checker"]
        mock_checker.has_capability.return_value = False

        client = flask_app.test_client()

        response = client.get(
            "/api/users/456",
            headers={
                "X-User-ID": "user_123",
                "X-Tenant-ID": "tenant_1",
            }
        )

        # Should get error response
        assert response.status_code == 500  # DualGatePipeline raises CapabilityGateError

    def test_flask_route_audit_recorded(self, flask_app):
        """Test that Flask route execution is audited."""
        mock_audit = flask_app.config["mock_audit_chain"]
        mock_audit.reset_mock()

        client = flask_app.test_client()

        response = client.get(
            "/api/users/456",
            headers={
                "X-User-ID": "user_123",
                "X-Tenant-ID": "tenant_1",
            }
        )

        # Verify audit was called
        assert response.status_code == 200
        assert mock_audit.record.call_count > 0  # At least one audit entry

    def test_flask_post_with_data(self, flask_app):
        """Test POST request with data."""
        client = flask_app.test_client()

        response = client.post(
            "/api/users",
            json={"name": "Jane Doe"},
            headers={
                "X-User-ID": "user_123",
                "X-Tenant-ID": "tenant_1",
            }
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["name"] == "Jane Doe"

    def test_flask_tenant_isolation(self, flask_app):
        """Test that Flask routes respect tenant isolation."""
        mock_checker = flask_app.config["mock_capability_checker"]
        mock_checker.reset_mock()

        client = flask_app.test_client()

        # Request from tenant_1
        client.get(
            "/api/users/456",
            headers={
                "X-User-ID": "user_123",
                "X-Tenant-ID": "tenant_1",
            }
        )

        # Get the tenant_id passed to capability checker
        call_args = mock_checker.has_capability.call_args
        assert call_args[1]["tenant_id"] == "tenant_1"

        mock_checker.reset_mock()

        # Request from tenant_2
        client.get(
            "/api/users/789",
            headers={
                "X-User-ID": "user_456",
                "X-Tenant-ID": "tenant_2",
            }
        )

        # Get the tenant_id passed to capability checker
        call_args = mock_checker.has_capability.call_args
        assert call_args[1]["tenant_id"] == "tenant_2"


# ============================================================================
# Async E2E Tests
# ============================================================================


class TestAsyncE2EIntegration:
    """E2E tests for async handler integration."""

    @pytest.fixture
    def async_pipeline(self):
        """Create pipeline for async testing."""
        mock_audit_chain = Mock()
        mock_capability_checker = Mock()
        mock_capability_checker.has_capability.return_value = True

        return DualGatePipeline(
            audit_chain=mock_audit_chain,
            capability_checker=mock_capability_checker,
            feature_flags={
                "dual_gate_pipeline_enabled": True,
                "dual_gate_pii_detection_enabled": True,
            },
        )

    @pytest.mark.asyncio
    async def test_async_task_success(self, async_pipeline):
        """Test successful async task execution."""
        async def background_task():
            await asyncio.sleep(0.01)  # Simulate async work
            return {"status": "complete"}

        ctx = PipelineContext(
            actor="system",
            capability="admin",
            action="background_sync",
            resource="system:sync",
            tenant_id="tenant_1",
        )

        result = await async_pipeline.execute_guarded_async(ctx, background_task)
        assert result["status"] == "complete"

    @pytest.mark.asyncio
    async def test_async_multiple_tasks(self, async_pipeline):
        """Test multiple async tasks through dual-gate pipeline."""
        async def task1():
            await asyncio.sleep(0.01)
            return {"task": 1, "result": "success"}

        async def task2():
            await asyncio.sleep(0.01)
            return {"task": 2, "result": "success"}

        ctx1 = PipelineContext(
            actor="system",
            capability="admin",
            action="task_1",
            resource="tasks:1",
            tenant_id="tenant_1",
        )

        ctx2 = PipelineContext(
            actor="system",
            capability="admin",
            action="task_2",
            resource="tasks:2",
            tenant_id="tenant_1",
        )

        # Execute both tasks
        result1 = await async_pipeline.execute_guarded_async(ctx1, task1)
        result2 = await async_pipeline.execute_guarded_async(ctx2, task2)

        assert result1["task"] == 1
        assert result2["task"] == 2

    @pytest.mark.asyncio
    async def test_async_capability_denied(self, async_pipeline):
        """Test async task with capability denial."""
        async_pipeline.capability_checker.has_capability.return_value = False

        async def task():
            return {"status": "should_not_execute"}

        ctx = PipelineContext(
            actor="unprivileged_user",
            capability="admin",
            action="dangerous_task",
            resource="system:critical",
            tenant_id="tenant_1",
        )

        with pytest.raises(CapabilityGateError):
            await async_pipeline.execute_guarded_async(ctx, task)

    @pytest.mark.asyncio
    async def test_async_validation_failed(self, async_pipeline):
        """Test async task with validation failure."""
        mock_validator = Mock()
        mock_validator.validate_string.return_value = Mock(
            is_valid=False,
            error_message="Invalid input"
        )
        async_pipeline.validator_factory = mock_validator
        async_pipeline._validation_enabled = True

        async def task():
            return {"status": "should_not_execute"}

        ctx = PipelineContext(
            actor="user_123",
            capability="write",
            action="write_task",
            resource="data:123",
            tenant_id="tenant_1",
            input_data={"data": ""},
            validator_rules={"data": {"type": "validate_string"}},
        )

        with pytest.raises(ValidationGateError):
            await async_pipeline.execute_guarded_async(ctx, task)

    @pytest.mark.asyncio
    async def test_async_pii_detection(self, async_pipeline):
        """Test async task with PII detection."""
        mock_pii_detector = Mock()
        pii_pattern = Mock()
        pii_pattern.pii_class = "email"
        pii_pattern.confidence = 0.95
        pii_pattern.source_pattern = "email_pattern"
        pii_pattern.sample_match = "user@*.com"
        mock_pii_detector.detect.return_value = pii_pattern

        async_pipeline.pii_detector = mock_pii_detector
        async_pipeline._pii_detection_enabled = True

        async def task():
            return {"status": "should_not_execute"}

        ctx = PipelineContext(
            actor="user_123",
            capability="write",
            action="save_data",
            resource="data:123",
            tenant_id="tenant_1",
            input_data={"email": "user@example.com"},
        )

        with pytest.raises(PIIDetectionError):
            await async_pipeline.execute_guarded_async(ctx, task)


# ============================================================================
# Cross-Transport Integration Tests
# ============================================================================


class TestCrossTransportIntegration:
    """Tests for consistency across sync (Flask) and async handlers."""

    def test_sync_and_async_same_pipeline(self):
        """Test that sync and async use the same pipeline instance."""
        mock_audit_chain = Mock()
        mock_capability_checker = Mock()
        mock_capability_checker.has_capability.return_value = True

        pipeline = DualGatePipeline(
            audit_chain=mock_audit_chain,
            capability_checker=mock_capability_checker,
            feature_flags={
                "dual_gate_pipeline_enabled": True,
            },
        )

        # Both sync and async should use same pipeline
        ctx = PipelineContext(
            actor="user_123",
            capability="read",
            action="read_action",
            resource="data:123",
            tenant_id="tenant_1",
        )

        # Sync execution
        result_sync = pipeline.execute_guarded(ctx, lambda: {"type": "sync"})
        assert result_sync["type"] == "sync"

        # Async execution
        async def async_func():
            return {"type": "async"}

        result_async = asyncio.run(
            pipeline.execute_guarded_async(ctx, async_func)
        )
        assert result_async["type"] == "async"

        # Both should have audited
        assert mock_audit_chain.record.call_count > 1

    def test_tenant_isolation_across_transports(self):
        """Test that tenant isolation works across sync and async."""
        mock_audit_chain = Mock()
        mock_capability_checker = Mock()
        mock_capability_checker.has_capability.return_value = True

        pipeline = DualGatePipeline(
            audit_chain=mock_audit_chain,
            capability_checker=mock_capability_checker,
        )

        # Sync call with tenant_1
        ctx1 = PipelineContext(
            actor="user_1",
            capability="read",
            action="read",
            resource="data",
            tenant_id="tenant_1",
        )
        pipeline.execute_guarded(ctx1, lambda: None)

        # Check tenant_id passed
        first_call = mock_capability_checker.has_capability.call_args
        assert first_call[1]["tenant_id"] == "tenant_1"

        mock_capability_checker.reset_mock()

        # Async call with tenant_2
        async def async_func():
            return None

        ctx2 = PipelineContext(
            actor="user_2",
            capability="read",
            action="read",
            resource="data",
            tenant_id="tenant_2",
        )
        asyncio.run(pipeline.execute_guarded_async(ctx2, async_func))

        # Check tenant_id passed
        second_call = mock_capability_checker.has_capability.call_args
        assert second_call[1]["tenant_id"] == "tenant_2"


# ============================================================================
# Feature Flag E2E Tests
# ============================================================================


class TestFeatureFlagE2E:
    """E2E tests for feature flag behavior."""

    def test_feature_flag_disables_all_gates(self):
        """Test that disabling all flags skips validation."""
        mock_audit_chain = Mock()
        mock_capability_checker = Mock()
        mock_capability_checker.has_capability.return_value = True

        pipeline = DualGatePipeline(
            audit_chain=mock_audit_chain,
            capability_checker=mock_capability_checker,
            feature_flags={
                "dual_gate_pipeline_enabled": False,
                "dual_gate_pii_detection_enabled": False,
                "dual_gate_queue_integrity_enabled": False,
            },
        )

        # This would normally fail validation, but flag is off
        ctx = PipelineContext(
            actor="user_123",
            capability="read",
            action="read",
            resource="data",
            tenant_id="tenant_1",
            input_data={"field": ""},  # Invalid
            validator_rules={"field": {"type": "validate_string"}},
        )

        # Should not raise ValidationGateError
        result = pipeline.execute_guarded(ctx, lambda: {"status": "ok"})
        assert result["status"] == "ok"

    def test_partial_flag_enablement(self):
        """Test with only some gates enabled."""
        mock_audit_chain = Mock()
        mock_capability_checker = Mock()
        mock_capability_checker.has_capability.return_value = True
        mock_pii_detector = Mock()
        mock_pii_detector.detect.return_value = None  # No PII detected

        # Enable only PII detection
        pipeline = DualGatePipeline(
            audit_chain=mock_audit_chain,
            capability_checker=mock_capability_checker,
            pii_detector=mock_pii_detector,
            feature_flags={
                "dual_gate_pipeline_enabled": False,  # Validation off
                "dual_gate_pii_detection_enabled": True,  # PII on
            },
        )

        # Validation would fail, but it's disabled
        # PII detection would pass (no pattern)
        ctx = PipelineContext(
            actor="user_123",
            capability="read",
            action="read",
            resource="data",
            tenant_id="tenant_1",
            input_data={"field": ""},  # Invalid, but validation is off
        )

        result = pipeline.execute_guarded(ctx, lambda: {"status": "ok"})
        assert result["status"] == "ok"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
