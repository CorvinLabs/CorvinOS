"""ModelSelector K=3 Integration — K=2 resolution + health monitor registration (ADR-2084).

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).

Model resolution is NOT decided here. It defers to THE single OS-model
resolver, ``corvin_operator/bridges/shared/model_selector.resolve_os_model``
(ADR-0952: one resolver for every surface). This module used to be a stub that
returned ``"sonnet"`` for every input — ignoring the operator's pins (Tier 1
override, profile / persona pin, tenant ``spec.engine_models`` pin) and the
Tier 2.9 classifier. K=3 only adds latency monitoring on top, and a PINNED
task is registered as pinned so the monitor never escalates it: an operator
pin is an instruction, not a starting point.
"""

from typing import Any, Dict, Optional
import logging

from .health_check_monitor import HealthCheckMonitor

logger = logging.getLogger(__name__)

# Global health monitor instance
_HEALTH_MONITOR: Optional[HealthCheckMonitor] = None


def _bridge_selector():
    """The canonical OS-model resolver module."""
    from corvin_operator.bridges.shared import model_selector as ms  # noqa: PLC0415

    return ms


async def initialize_health_monitor() -> HealthCheckMonitor:
    """Initialize and start the global health monitor (inside a running loop).

    This used to be a sync function calling ``asyncio.create_task`` — which
    raises ``RuntimeError: no running event loop`` from any sync call site.
    """
    global _HEALTH_MONITOR
    _HEALTH_MONITOR = HealthCheckMonitor(sla_target_ms=600, check_interval_ms=100)
    await _HEALTH_MONITOR.start()
    return _HEALTH_MONITOR


def _pinned_model(ms, profile: Optional[dict], engine_id: str, tenant_id: str) -> Optional[str]:
    """The operator pin in force, if any (Tiers 1 / 2 / 1.5 / 2.5 of the resolver)."""
    override = ms.os_model_override()
    if override:
        return override
    profile = profile or {}
    for key in ("model", "_persona_os_model"):
        val = profile.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    try:
        from corvin_operator.bridges.shared.engine_models import (  # noqa: PLC0415
            get_tenant_engine_model,
        )

        return get_tenant_engine_model(tenant_id, engine_id, "os_model") or None
    except Exception:  # noqa: BLE001 — no tenant config → no tenant pin
        return None


def resolve_os_model(
    task_input: Any,
    *,
    profile: Optional[dict] = None,
    engine_id: str = "claude_code",
    tenant_id: str = "_default",
    payload_chars: int = 0,
) -> Optional[str]:
    """Resolve the OS model via the canonical resolver, then register it for K=3.

    ``task_input`` may be the raw task text or an object with ``text`` /
    ``task_id`` attributes. Returns whatever the canonical resolver returns
    (a model id, or ``None`` = CLI subscription default).
    """
    ms = _bridge_selector()
    text = task_input if isinstance(task_input, str) else getattr(task_input, "text", None)
    model = ms.resolve_os_model(
        profile,
        payload_chars=payload_chars,
        engine_id=engine_id,
        tenant_id=tenant_id,
        task_input=text,
    )

    task_id = getattr(task_input, "task_id", None)
    if _HEALTH_MONITOR is not None and task_id and model:
        pinned = _pinned_model(ms, profile, engine_id, tenant_id) is not None
        _HEALTH_MONITOR.register_task(task_id, model, pinned=pinned)

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
