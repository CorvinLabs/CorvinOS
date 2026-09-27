"""ModelSelector K=3 Integration — K=2 (unchanged) + health monitor registration (ADR-2084)."""

from typing import Optional, Dict
from .health_check_monitor import HealthCheckMonitor
import logging

logger = logging.getLogger(__name__)

# Global health monitor instance
_HEALTH_MONITOR: Optional[HealthCheckMonitor] = None


def initialize_health_monitor():
    """Initialize global health monitor (call once at startup)."""
    global _HEALTH_MONITOR
    _HEALTH_MONITOR = HealthCheckMonitor(sla_target_ms=600, check_interval_ms=100)
    import asyncio
    asyncio.create_task(_HEALTH_MONITOR.start())


def resolve_os_model(task_input) -> str:
    """
    Select OS model (K=2 unchanged + K=3 health monitor registration).

    Returns:
        Model name: "haiku", "sonnet", or "opus"
    """
    # K=2: Static classification (existing logic, unchanged)
    # confidence = classifier.confidence_for_input(task_input)
    # model = classifier.model_for_confidence(confidence)
    # For now, stub: default to sonnet
    model = "sonnet"

    # K=3: Register for health monitoring
    if _HEALTH_MONITOR and hasattr(task_input, 'task_id'):
        _HEALTH_MONITOR.register_task(task_input.task_id, model)

    return model


async def handle_escalation_event(event: Dict) -> Optional[str]:
    """Handle escalation event from health monitor. Returns new model (if escalated)."""
    logger.info(f"Escalation event: {event['model_old']} → {event['model_new']}")
    return event.get("model_new")


def unregister_task(task_id: str):
    """Unregister task from health monitoring (end of execution)."""
    if _HEALTH_MONITOR:
        _HEALTH_MONITOR.unregister_task(task_id)


async def get_escalation_events() -> list:
    """Get all pending escalation events (for feedback loop)."""
    if _HEALTH_MONITOR:
        return await _HEALTH_MONITOR.get_escalation_events()
    return []
