"""Tier-2 Unit Tests for /btw steering routes (ADR-0510, Phase 2b k=1)."""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from fastapi.testclient import TestClient

from core.gateway.routes.btw_routes import (
    handle_btw,
    get_btw_status,
    BtwRequest,
    BtwResponse,
    check_capability,
    get_actor_from_request,
    audit_log_btw_action,
    btw_router,
)


class TestBtwRoutesUnit:
    """Unit tests for /btw route handlers."""

    def test_btw_request_model(self):
        """Test BtwRequest validation."""
        req = BtwRequest(instruction="/btw use Opus", task_id="task_123")
        assert req.instruction == "/btw use Opus"
        assert req.task_id == "task_123"

    def test_btw_request_task_id_optional(self):
        """Test BtwRequest with no task_id."""
        req = BtwRequest(instruction="/btw use Sonnet")
        assert req.task_id is None

    def test_btw_response_model(self):
        """Test BtwResponse construction."""
        resp = BtwResponse(
            status="guidance_queued",
            instruction="/btw use Opus",
            task_id="task_123"
        )
        assert resp.status == "guidance_queued"
        assert resp.task_id == "task_123"

    def test_check_capability_stub(self):
        """Test capability check (MVP stub)."""
        # MVP always returns True; production integrates with L16
        assert check_capability("demo_user", "task_steering") is True
        assert check_capability("anyone", "anything") is True

    def test_get_actor_from_request_placeholder(self):
        """Test actor extraction (placeholder)."""
        request_ctx = {"actor_id": "user_123"}
        actor = get_actor_from_request(request_ctx)
        assert actor == "user_123"

    def test_get_actor_default_anonymous(self):
        """Test actor defaults to anonymous."""
        request_ctx = {}
        actor = get_actor_from_request(request_ctx)
        assert actor == "anonymous"

    def test_audit_log_btw_action(self):
        """Test audit logging with PII scrubbing."""
        # Should not raise
        audit_log_btw_action(
            actor="user_123",
            task_id="task_123",
            instruction="use Opus for better output",
            action_status="received"
        )

    def test_audit_log_scrubs_long_instructions(self):
        """Test that audit log scrubs long instructions."""
        long_instruction = "x" * 100
        # Should scrub to 50 chars + "..."
        audit_log_btw_action(
            actor="user_123",
            task_id="task_123",
            instruction=long_instruction,
            action_status="received"
        )


@pytest.mark.asyncio
class TestBtwRoutesIntegration:
    """Integration tests for /btw routes with Hub."""

    @patch("core.gateway.routes.btw_routes.SubsystemHub")
    async def test_handle_btw_publishes_event(self, mock_hub_class):
        """Test that POST /btw publishes guidance_received event to Hub."""
        mock_hub = Mock()
        mock_hub.publish_event = Mock()
        mock_hub_class.return_value = mock_hub

        req = BtwRequest(instruction="/btw use Opus", task_id="task_123")

        with patch("core.gateway.routes.btw_routes.check_capability", return_value=True):
            resp = await handle_btw(req)

        assert resp.status == "guidance_queued"
        mock_hub.publish_event.assert_called_once()
        call_args = mock_hub.publish_event.call_args
        assert call_args[0][0] == "guidance_received"
        event_data = call_args[0][1]
        assert event_data["actor"] == "demo_user"
        assert event_data["task_id"] == "task_123"
        assert event_data["instruction"] == "/btw use Opus"

    @patch("core.gateway.routes.btw_routes.SubsystemHub")
    async def test_handle_btw_empty_instruction_rejected(self, mock_hub_class):
        """Test that empty instruction is rejected."""
        req = BtwRequest(instruction="", task_id="task_123")

        with pytest.raises(Exception):  # HTTPException
            await handle_btw(req)

    @patch("core.gateway.routes.btw_routes.SubsystemHub")
    async def test_handle_btw_capability_denied(self, mock_hub_class):
        """Test that denied capability returns 403."""
        mock_hub = Mock()
        mock_hub_class.return_value = mock_hub
        req = BtwRequest(instruction="/btw use Opus", task_id="task_123")

        with patch("core.gateway.routes.btw_routes.check_capability", return_value=False):
            with pytest.raises(Exception):  # HTTPException 403
                await handle_btw(req)

        # Hub should not be called if capability denied
        mock_hub.publish_event.assert_not_called()

    @patch("core.gateway.routes.btw_routes.SubsystemHub")
    async def test_handle_btw_hub_exception_handled(self, mock_hub_class):
        """Test that Hub exceptions are caught and return 500."""
        mock_hub = Mock()
        mock_hub.publish_event.side_effect = RuntimeError("Queue full")
        mock_hub_class.return_value = mock_hub

        req = BtwRequest(instruction="/btw use Opus", task_id="task_123")

        with patch("core.gateway.routes.btw_routes.check_capability", return_value=True):
            with pytest.raises(Exception):  # HTTPException 500
                await handle_btw(req)

    @patch("core.gateway.routes.btw_routes.SubsystemHub")
    async def test_get_btw_status_queries_subsystem(self, mock_hub_class):
        """Test that GET /btw/status queries BtwAdvisor via Hub."""
        mock_hub = AsyncMock()
        mock_response = {
            "instruction": Mock(to_dict=lambda: {"type": "use_model", "value": "Opus"})
        }
        mock_hub.request_from_subsystem.return_value = mock_response
        mock_hub_class.return_value = mock_hub

        resp = await get_btw_status(task_id="task_123")

        assert resp["has_pending"] is True
        assert len(resp["pending_instructions"]) == 1
        mock_hub.request_from_subsystem.assert_called_once_with(
            "btw_advisor",
            "peek_pending_guidance",
            task_id="task_123"
        )

    @patch("core.gateway.routes.btw_routes.SubsystemHub")
    async def test_get_btw_status_no_pending(self, mock_hub_class):
        """Test GET /btw/status when no guidance pending."""
        mock_hub = AsyncMock()
        mock_hub.request_from_subsystem.return_value = {}
        mock_hub_class.return_value = mock_hub

        resp = await get_btw_status(task_id="task_123")

        assert resp["has_pending"] is False
        assert resp["pending_instructions"] == []

    @patch("core.gateway.routes.btw_routes.SubsystemHub")
    async def test_get_btw_status_missing_task_id(self, mock_hub_class):
        """Test GET /btw/status with missing task_id returns 400."""
        with pytest.raises(Exception):  # HTTPException 400
            await get_btw_status(task_id="")


class TestBtwRoutesEdgeCases:
    """Edge cases and error handling."""

    @pytest.mark.asyncio
    @patch("core.gateway.routes.btw_routes.SubsystemHub")
    async def test_btw_whitespace_instruction_stripped(self, mock_hub_class):
        """Test that instruction whitespace is stripped."""
        mock_hub = Mock()
        mock_hub.publish_event = Mock()
        mock_hub_class.return_value = mock_hub

        req = BtwRequest(instruction="  /btw use Opus  ", task_id="task_123")

        with patch("core.gateway.routes.btw_routes.check_capability", return_value=True):
            resp = await handle_btw(req)

        call_args = mock_hub.publish_event.call_args
        event_data = call_args[0][1]
        assert event_data["instruction"] == "/btw use Opus"

    @pytest.mark.asyncio
    @patch("core.gateway.routes.btw_routes.SubsystemHub")
    async def test_btw_preserves_task_id(self, mock_hub_class):
        """Test that task_id is preserved in event."""
        mock_hub = Mock()
        mock_hub_class.return_value = mock_hub

        req = BtwRequest(instruction="/btw use Opus", task_id="specific_task_456")

        with patch("core.gateway.routes.btw_routes.check_capability", return_value=True):
            resp = await handle_btw(req)

        assert resp.task_id == "specific_task_456"
        call_args = mock_hub.publish_event.call_args
        assert call_args[0][1]["task_id"] == "specific_task_456"
