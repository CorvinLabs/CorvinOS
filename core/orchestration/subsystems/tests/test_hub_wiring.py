"""Tier-2 Unit Tests for Hub Wiring (k=2 implementation).

Verify that subsystems correctly inherit Subsystem, implement startup(),
and subscribe to events via SubsystemHub.

Tests:
- BtwAdvisor startup + subscription to guidance_received
- VoiceCoordinator startup + subscriptions to voice events
- TaskManager startup + subscriptions to task events
- Hub.publish_event() correctly routes to subscribers
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, MagicMock

from core.orchestration.hub import SubsystemHub
from core.orchestration.subsystems.btw_advisor import BtwAdvisor
from core.orchestration.subsystems.voice_coordinator import VoiceCoordinator
from core.orchestration.subsystems.task_manager import TaskManager


class TestBtwAdvisorHubWiring:
    """Test BtwAdvisor Hub integration."""

    def test_btw_advisor_inherits_subsystem(self):
        """BtwAdvisor inherits from Subsystem base class."""
        advisor = BtwAdvisor()
        assert hasattr(advisor, 'name')
        assert hasattr(advisor, 'version')
        assert hasattr(advisor, 'startup')
        assert hasattr(advisor, 'shutdown')
        assert hasattr(advisor, 'on_event')
        assert hasattr(advisor, 'handle_request')

    def test_btw_advisor_name_and_version(self):
        """BtwAdvisor has correct name and version."""
        advisor = BtwAdvisor()
        assert advisor.name == "btw_advisor"
        assert advisor.version == "1.0.0"

    def test_btw_advisor_startup_stores_hub(self):
        """startup() stores hub reference."""
        advisor = BtwAdvisor()
        hub = SubsystemHub()

        advisor.startup(hub)

        assert advisor.hub is hub

    def test_btw_advisor_startup_subscribes_to_guidance_received(self):
        """startup() subscribes to guidance_received event."""
        advisor = BtwAdvisor()
        hub = SubsystemHub()

        # Track subscriptions
        original_subscribe = hub.subscribe
        subscriptions = []

        def track_subscribe(event_name, handler):
            subscriptions.append((event_name, handler))
            original_subscribe(event_name, handler)

        hub.subscribe = track_subscribe
        advisor.startup(hub)

        # Verify subscription
        assert len(subscriptions) == 1
        assert subscriptions[0][0] == "guidance_received"
        assert subscriptions[0][1] == advisor.on_event

    def test_btw_advisor_shutdown_clears_state(self):
        """shutdown() clears pending guidance."""
        advisor = BtwAdvisor()
        advisor.pending_guidance["task_1"] = Mock()

        advisor.shutdown()

        assert len(advisor.pending_guidance) == 0


class TestVoiceCoordinatorHubWiring:
    """Test VoiceCoordinator Hub integration."""

    def test_voice_coordinator_inherits_subsystem(self):
        """VoiceCoordinator inherits from Subsystem base class."""
        coordinator = VoiceCoordinator()
        assert hasattr(coordinator, 'name')
        assert hasattr(coordinator, 'version')
        assert hasattr(coordinator, 'startup')
        assert hasattr(coordinator, 'shutdown')
        assert hasattr(coordinator, 'on_event')
        assert hasattr(coordinator, 'handle_request')

    def test_voice_coordinator_name_and_version(self):
        """VoiceCoordinator has correct name and version."""
        coordinator = VoiceCoordinator()
        assert coordinator.name == "voice_coordinator"
        assert coordinator.version == "1.0.0"

    def test_voice_coordinator_startup_stores_hub(self):
        """startup() stores hub reference."""
        coordinator = VoiceCoordinator()
        hub = SubsystemHub()

        coordinator.startup(hub)

        assert coordinator.hub is hub

    def test_voice_coordinator_startup_subscribes_to_voice_events(self):
        """startup() subscribes to all voice-related events."""
        coordinator = VoiceCoordinator()
        hub = SubsystemHub()

        subscriptions = []
        original_subscribe = hub.subscribe

        def track_subscribe(event_name, handler):
            subscriptions.append(event_name)
            original_subscribe(event_name, handler)

        hub.subscribe = track_subscribe
        coordinator.startup(hub)

        # Verify subscriptions
        assert len(subscriptions) == 3
        assert "user_said" in subscriptions
        assert "interrupt_received" in subscriptions
        assert "response_ready" in subscriptions

    def test_voice_coordinator_shutdown_clears_state(self):
        """shutdown() clears active channels and TTS queue."""
        coordinator = VoiceCoordinator()
        coordinator.active_channels["ch_1"] = Mock()
        coordinator.tts_queue.append({"text": "hello"})

        coordinator.shutdown()

        assert len(coordinator.active_channels) == 0
        assert len(coordinator.tts_queue) == 0


class TestTaskManagerHubWiring:
    """Test TaskManager Hub integration."""

    def test_task_manager_inherits_subsystem(self):
        """TaskManager inherits from Subsystem base class."""
        manager = TaskManager()
        assert hasattr(manager, 'name')
        assert hasattr(manager, 'version')
        assert hasattr(manager, 'startup')
        assert hasattr(manager, 'shutdown')
        assert hasattr(manager, 'on_event')
        assert hasattr(manager, 'handle_request')

    def test_task_manager_name_and_version(self):
        """TaskManager has correct name and version."""
        manager = TaskManager()
        assert manager.name == "task_manager"
        assert manager.version == "1.0.0"

    def test_task_manager_startup_stores_hub(self):
        """startup() stores hub reference."""
        manager = TaskManager()
        hub = SubsystemHub()

        manager.startup(hub)

        assert manager.hub is hub

    def test_task_manager_startup_subscribes_to_task_events(self):
        """startup() subscribes to all task-related events."""
        manager = TaskManager()
        hub = SubsystemHub()

        subscriptions = []
        original_subscribe = hub.subscribe

        def track_subscribe(event_name, handler):
            subscriptions.append(event_name)
            original_subscribe(event_name, handler)

        hub.subscribe = track_subscribe
        manager.startup(hub)

        # Verify subscriptions
        assert len(subscriptions) == 3
        assert "task_started" in subscriptions
        assert "task_completed" in subscriptions
        assert "loss_signal" in subscriptions

    def test_task_manager_shutdown_clears_state(self):
        """shutdown() clears active tasks."""
        manager = TaskManager()
        manager.active_tasks["task_1"] = {"id": "task_1"}

        manager.shutdown()

        assert len(manager.active_tasks) == 0


class TestHubEventRouting:
    """Test Hub event routing to subsystems."""

    @pytest.mark.asyncio
    async def test_hub_publish_event_routes_to_subscriber(self):
        """Hub.publish_event() routes to subscribed handler."""
        hub = SubsystemHub()
        advisor = BtwAdvisor()
        advisor.startup(hub)

        # Track on_event calls
        original_on_event = advisor.on_event
        call_count = [0]
        call_args = [None]

        async def track_on_event(event_name, event_data):
            call_count[0] += 1
            call_args[0] = (event_name, event_data)
            await original_on_event(event_name, event_data)

        advisor.on_event = track_on_event

        # Publish event
        event_data = {
            "task_id": "task_123",
            "actor": "user_1",
            "instruction": "/btw use Opus",
        }
        hub.publish_event("guidance_received", event_data)

        # Process one event
        await hub.process_events(timeout_s=1.0)

        # Verify handler was called
        assert call_count[0] == 1
        assert call_args[0][0] == "guidance_received"
        assert call_args[0][1] == event_data

    def test_hub_request_from_subsystem(self):
        """Hub.request_from_subsystem() queries subsystem handler."""
        hub = SubsystemHub()
        advisor = BtwAdvisor()
        advisor.startup(hub)

        # Query subsystem (should return empty initially)
        response = asyncio.run(hub.request_from_subsystem(
            "btw_advisor",
            "peek_pending_guidance",
            task_id="task_123"
        ))

        # Should return None (no pending guidance)
        assert response is None or response.get("instruction") is None
