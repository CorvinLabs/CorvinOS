"""
Subsystem Manager — Start/pause/resume/stop subsystems.

Stream 2 of Control Plane (Phase 9b).

ADR-2029: User-Centric CorvinOS Control Plane — Stream 2
"""

import logging
from typing import Optional, Dict, Any, List
from enum import Enum
from datetime import datetime

logger = logging.getLogger(__name__)


class SubsystemState(Enum):
    """Subsystem lifecycle states."""
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


class SubsystemManager:
    """Manage subsystem lifecycle (start/pause/resume/stop)."""

    def __init__(self):
        """Initialize manager."""
        self.subsystems: Dict[str, Dict[str, Any]] = {}
        self.audit_events: List[Dict[str, Any]] = []

    async def start_subsystem(
        self,
        subsystem_id: str,
        tenant_id: str = "default",
        operator_id: str = "unknown"
    ) -> Dict[str, Any]:
        """Start a subsystem."""
        if subsystem_id not in self.subsystems:
            self.subsystems[subsystem_id] = {
                "state": SubsystemState.STOPPED,
                "started_at": None,
                "paused_at": None,
                "stopped_at": None,
            }

        if self.subsystems[subsystem_id]["state"] != SubsystemState.STOPPED:
            return {
                "status": "warning",
                "message": f"Subsystem {subsystem_id} already running"
            }

        # Start
        self.subsystems[subsystem_id]["state"] = SubsystemState.RUNNING
        self.subsystems[subsystem_id]["started_at"] = datetime.utcnow().isoformat() + "Z"

        self._emit_audit_event("subsystem_started", subsystem_id, tenant_id, operator_id)

        return {
            "status": "success",
            "message": f"Subsystem {subsystem_id} started"
        }

    async def pause_subsystem(
        self,
        subsystem_id: str,
        timeout_s: int = 30,
        tenant_id: str = "default",
        operator_id: str = "unknown"
    ) -> Dict[str, Any]:
        """Pause a running subsystem (graceful, 30s timeout)."""
        if subsystem_id not in self.subsystems:
            return {"status": "error", "message": f"Subsystem {subsystem_id} not found"}

        if self.subsystems[subsystem_id]["state"] != SubsystemState.RUNNING:
            return {"status": "warning", "message": f"Subsystem {subsystem_id} not running"}

        # Graceful pause
        logger.info(f"Gracefully pausing {subsystem_id} (timeout={timeout_s}s)")

        self.subsystems[subsystem_id]["state"] = SubsystemState.PAUSED
        self.subsystems[subsystem_id]["paused_at"] = datetime.utcnow().isoformat() + "Z"

        self._emit_audit_event("subsystem_paused", subsystem_id, tenant_id, operator_id)

        return {"status": "success", "message": f"Subsystem {subsystem_id} paused"}

    async def resume_subsystem(
        self,
        subsystem_id: str,
        tenant_id: str = "default",
        operator_id: str = "unknown"
    ) -> Dict[str, Any]:
        """Resume a paused subsystem."""
        if subsystem_id not in self.subsystems:
            return {"status": "error", "message": f"Subsystem {subsystem_id} not found"}

        if self.subsystems[subsystem_id]["state"] != SubsystemState.PAUSED:
            return {"status": "warning", "message": f"Subsystem {subsystem_id} not paused"}

        # Resume
        self.subsystems[subsystem_id]["state"] = SubsystemState.RUNNING

        self._emit_audit_event("subsystem_resumed", subsystem_id, tenant_id, operator_id)

        return {"status": "success", "message": f"Subsystem {subsystem_id} resumed"}

    async def stop_subsystem(
        self,
        subsystem_id: str,
        force: bool = False,
        timeout_s: int = 30,
        tenant_id: str = "default",
        operator_id: str = "unknown"
    ) -> Dict[str, Any]:
        """Stop a subsystem (graceful or force)."""
        if subsystem_id not in self.subsystems:
            return {"status": "error", "message": f"Subsystem {subsystem_id} not found"}

        if self.subsystems[subsystem_id]["state"] == SubsystemState.STOPPED:
            return {"status": "warning", "message": f"Subsystem {subsystem_id} already stopped"}

        # Graceful or force shutdown
        if force:
            logger.warning(f"Force stopping {subsystem_id} (no graceful shutdown)")
        else:
            logger.info(f"Gracefully stopping {subsystem_id} (timeout={timeout_s}s)")

        self.subsystems[subsystem_id]["state"] = SubsystemState.STOPPED
        self.subsystems[subsystem_id]["stopped_at"] = datetime.utcnow().isoformat() + "Z"

        self._emit_audit_event("subsystem_stopped", subsystem_id, tenant_id, operator_id)

        return {"status": "success", "message": f"Subsystem {subsystem_id} stopped"}

    async def get_subsystem_status(self, subsystem_id: str) -> Optional[Dict[str, Any]]:
        """Get subsystem status."""
        if subsystem_id not in self.subsystems:
            return None

        sub = self.subsystems[subsystem_id]
        return {
            "subsystem_id": subsystem_id,
            "state": sub["state"].value,
            "started_at": sub.get("started_at"),
            "paused_at": sub.get("paused_at"),
            "stopped_at": sub.get("stopped_at"),
        }

    async def list_subsystems(self) -> List[Dict[str, Any]]:
        """List all subsystems."""
        return [
            await self.get_subsystem_status(sub_id)
            for sub_id in self.subsystems.keys()
        ]

    def _emit_audit_event(self, event_type: str, subsystem_id: str, tenant_id: str, operator_id: str) -> None:
        """Emit audit event."""
        event = {
            "tenant_id": tenant_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event_type": event_type,
            "subsystem_id": subsystem_id,
            "operator_id": operator_id,
            "status": "success",
        }
        self.audit_events.append(event)
        logger.info(f"Subsystem audit event: {event_type} {subsystem_id}")

    def get_audit_log(self) -> List[Dict[str, Any]]:
        """Get audit log."""
        return list(self.audit_events)
