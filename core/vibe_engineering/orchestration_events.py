"""Event bridge: TaskOrchestrator → NotificationRouter + VoiceCoordinator."""

import asyncio
from typing import Dict, Callable, Optional


class OrchestrationEventBridge:
    """Routes task orchestration events to downstream subsystems."""

    def __init__(self):
        self.subscribers: Dict[str, list] = {}

    def subscribe(self, event_type: str, handler: Callable):
        """Subscribe to event (phase.completed, phase.failed, task.completed, task.failed)."""
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(handler)

    async def emit(self, event_type: str, data: Dict):
        """Emit event to all subscribers (non-blocking)."""
        tasks = []
        for handler in self.subscribers.get(event_type, []):
            if asyncio.iscoroutinefunction(handler):
                tasks.append(asyncio.create_task(handler(data)))
            else:
                try:
                    handler(data)
                except Exception as e:
                    print(f"Handler error: {e}")

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


# Singleton bridge
_bridge = None


def get_orchestration_bridge() -> OrchestrationEventBridge:
    global _bridge
    if _bridge is None:
        _bridge = OrchestrationEventBridge()
    return _bridge


# Integration points (wire up in subsystem startup)

async def wire_notification_router(notification_router):
    """Wire NotificationRouter to orchestration events."""
    bridge = get_orchestration_bridge()

    async def on_phase_completed(data: Dict):
        await notification_router.on_phase_completed(data)

    async def on_task_completed(data: Dict):
        await notification_router.on_task_completed(data)

    bridge.subscribe("phase.completed", on_phase_completed)
    bridge.subscribe("task.completed", on_task_completed)


async def wire_voice_coordinator(voice_coordinator):
    """Wire VoiceChannelCoordinator to orchestration events."""
    bridge = get_orchestration_bridge()

    async def on_phase_completed(data: Dict):
        await voice_coordinator.queue_update(f"Phase {data['phase_id']} complete")

    bridge.subscribe("phase.completed", on_phase_completed)
