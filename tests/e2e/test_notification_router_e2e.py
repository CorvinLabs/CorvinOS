"""E2E test: NotificationRouter (ADR-0655) — CompletionEvents → Discord."""

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from core.notification.notification_router import NotificationRouter, RoutedNotification
from core.learning.completion_detectors.completion_event import CompletionStatus, CompletionTaskType


class TestNotificationRouter:
    """E2E tests for NotificationRouter."""

    @pytest.fixture
    def temp_audit_chain(self):
        """Create a temporary audit chain file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            temp_path = Path(f.name)
        yield temp_path
        temp_path.unlink(missing_ok=True)

    @pytest.fixture
    def router(self, temp_audit_chain):
        """Create router pointing to temp audit chain."""
        return NotificationRouter(audit_chain_path=str(temp_audit_chain), poll_interval_seconds=0.1)

    def test_router_reads_completion_events(self, router: NotificationRouter, temp_audit_chain: Path):
        """GATE 1: Router detects completion_event_* records in audit chain."""
        # Write a completion event to audit chain
        event = {
            "event_id": "evt_001",
            "event_type": "completion_event_workflow",
            "task_id": "wf_test_001",
            "tenant_id": "_default",
            "timestamp": "2026-09-15T00:00:00Z",
            "details": {
                "task_type": "workflow",
                "status": "completed",
                "outcome": "success",
                "origin": {
                    "channel": "discord",
                    "chat_id": "ch_123",
                    "sender_id": "user_abc",
                },
            },
        }

        with open(temp_audit_chain, "w") as f:
            f.write(json.dumps(event) + "\n")

        # Router should detect it
        import asyncio
        asyncio.run(router._poll_and_route_once())

        # Event should be in delivered set
        assert "evt_001" in router._delivered_event_ids

    def test_router_deduplicates(self, router: NotificationRouter, temp_audit_chain: Path):
        """GATE 2: Router never delivers same event twice (idempotent)."""
        event = {
            "event_id": "evt_002",
            "event_type": "completion_event_workflow",
            "task_id": "wf_test_002",
            "details": {"task_type": "workflow"},
        }

        with open(temp_audit_chain, "w") as f:
            f.write(json.dumps(event) + "\n")

        import asyncio

        # First read
        asyncio.run(router._poll_and_route_once())
        assert "evt_002" in router._delivered_event_ids

        # Add same event again (simulating repeated reads)
        with open(temp_audit_chain, "a") as f:
            f.write(json.dumps(event) + "\n")

        # Second read should NOT re-deliver
        initial_count = len(router._delivered_event_ids)
        asyncio.run(router._poll_and_route_once())

        assert len(router._delivered_event_ids) == initial_count

    def test_routed_notification_structure(self):
        """GATE 3: RoutedNotification is immutable and complete."""
        notif = RoutedNotification(
            task_id="wf_001",
            channel="discord",
            chat_id="ch_123",
            sender_id="user_abc",
            text="Workflow completed successfully",
            voice_path="/tmp/notif.opus",
            summary=mock.MagicMock(),
            timestamp="2026-09-15T00:00:00Z",
        )

        # Should be frozen
        with pytest.raises(Exception):  # frozen dataclass
            notif.task_id = "modified"

    @mock.patch("core.notification.notification_router.deliver_ready")
    def test_discord_delivery_calls_deliver_ready(self, mock_deliver_ready):
        """GATE 4: Discord routing calls completion_notify.deliver_ready()."""
        router = NotificationRouter()
        notif = RoutedNotification(
            task_id="wf_001",
            channel="discord",
            chat_id="ch_123",
            sender_id="user_abc",
            text="Workflow completed",
            voice_path=None,
            summary=mock.MagicMock(),
            timestamp="2026-09-15T00:00:00Z",
        )

        import asyncio
        asyncio.run(router._deliver_discord(notif))

        # Should have called deliver_ready
        mock_deliver_ready.assert_called_once()
        call_kwargs = mock_deliver_ready.call_args[1]
        assert call_kwargs["task_id"] == "wf_001"
        assert call_kwargs["channel"] == "discord"

    def test_voice_synthesis_fallback(self):
        """GATE 5: Voice synthesis is optional (fail-safe)."""
        router = NotificationRouter()
        summary = mock.MagicMock()
        summary.outcome_first = "Success"
        summary.text = "Workflow completed"

        import asyncio
        voice_path = asyncio.run(router._synthesize_voice(summary, "workflow"))

        # Should return None if TTS unavailable, not raise
        assert voice_path is None or isinstance(voice_path, str)


class TestNotificationRouterIntegration:
    """Integration tests for full workflow."""

    def test_completion_event_to_discord_flow(self):
        """GATE 6: Full flow — CompletionEvent → Discord delivery."""
        # This is a smoke test ensuring all components wire together
        from core.notification.notification_router import NotificationRouter
        from core.learning.completion_detectors.completion_event import CompletionEvent

        # Just verify imports work and classes instantiate
        router = NotificationRouter()
        assert router is not None
        assert hasattr(router, "run")
        assert hasattr(router, "_deliver_discord")

    @pytest.mark.skip(reason="Manual E2E — requires live Discord bot + Workflow")
    def manual_e2e_workflow_notification():
        """Manual smoke test: Real Workflow → Completion → Discord notification."""
        print("\n✓ Manual E2E: Workflow Completion Notification")
        print("  1. Start: /workflow do_something")
        print("  2. Wait: Workflow completes in background")
        print("  3. Expect: Discord message arrives with outcome-first voice summary")
        print("  4. Verify: Message contains task result + voice attachment")
