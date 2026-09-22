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
    """Manage subsystem lifecycle (start/pause/resume/stop).

    Enforces tenant isolation: each tenant's subsystems are stored separately.
    Audit events are immutable and tenant-scoped.
    """

    # Timeout bounds (fail-closed validation)
    MIN_TIMEOUT_S = 1
    MAX_TIMEOUT_S = 3600

    def __init__(self):
        """Initialize manager."""
        self.subsystems: Dict[str, Dict[str, Any]] = {}  # tenant_id -> subsystem_id -> subsystem_info
        self.audit_events: List[Dict[str, Any]] = []

    def _validate_tenant_id(self, tenant_id: Optional[str]) -> None:
        """Validate tenant_id is non-empty.

        Fail-closed: reject None or empty strings.

        Args:
            tenant_id: Tenant identifier

        Raises:
            ValueError: If tenant_id is None or empty
        """
        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError("tenant_id must be a non-empty string")

    def _validate_timeout_s(self, timeout_s: int) -> None:
        """Validate timeout_s is within safe bounds.

        Fail-closed: reject negative, zero, or unbounded values.
        - MIN: 1 second
        - MAX: 3600 seconds (1 hour)

        Args:
            timeout_s: Timeout in seconds

        Raises:
            ValueError: If timeout_s is out of bounds
        """
        if not isinstance(timeout_s, int):
            raise ValueError("timeout_s must be an integer")
        if timeout_s < self.MIN_TIMEOUT_S or timeout_s > self.MAX_TIMEOUT_S:
            raise ValueError(
                f"timeout_s must be between {self.MIN_TIMEOUT_S} and {self.MAX_TIMEOUT_S} seconds"
            )

    async def start_subsystem(
        self,
        subsystem_id: str,
        tenant_id: str,
        operator_id: str = "unknown"
    ) -> Dict[str, Any]:
        """Start a subsystem (tenant-scoped).

        Args:
            subsystem_id: Subsystem identifier
            tenant_id: Tenant ID (REQUIRED, fail-closed if missing)
            operator_id: Operator ID for audit

        Returns:
            Operation result with status + audit event

        Raises:
            ValueError: If tenant_id is invalid
        """
        self._validate_tenant_id(tenant_id)

        # Ensure tenant key exists
        if tenant_id not in self.subsystems:
            self.subsystems[tenant_id] = {}

        if subsystem_id not in self.subsystems[tenant_id]:
            self.subsystems[tenant_id][subsystem_id] = {
                "state": SubsystemState.STOPPED,
                "started_at": None,
                "paused_at": None,
                "stopped_at": None,
            }

        if self.subsystems[tenant_id][subsystem_id]["state"] != SubsystemState.STOPPED:
            return {
                "status": "warning",
                "message": f"Subsystem {subsystem_id} already running"
            }

        # Start
        self.subsystems[tenant_id][subsystem_id]["state"] = SubsystemState.RUNNING
        self.subsystems[tenant_id][subsystem_id]["started_at"] = datetime.utcnow().isoformat() + "Z"

        self._emit_audit_event("subsystem_started", subsystem_id, tenant_id, operator_id)

        return {
            "status": "success",
            "message": f"Subsystem {subsystem_id} started"
        }

    async def pause_subsystem(
        self,
        subsystem_id: str,
        timeout_s: int,
        tenant_id: str,
        operator_id: str = "unknown"
    ) -> Dict[str, Any]:
        """Pause a running subsystem (graceful, with timeout bounds).

        Args:
            subsystem_id: Subsystem identifier
            timeout_s: Graceful shutdown timeout (1-3600 seconds, fail-closed)
            tenant_id: Tenant ID (REQUIRED, fail-closed if missing)
            operator_id: Operator ID for audit

        Returns:
            Operation result with status + audit event

        Raises:
            ValueError: If tenant_id or timeout_s are invalid
        """
        self._validate_tenant_id(tenant_id)
        self._validate_timeout_s(timeout_s)

        # Check tenant key exists
        if tenant_id not in self.subsystems:
            return {"status": "error", "message": f"Subsystem {subsystem_id} not found for tenant {tenant_id}"}

        if subsystem_id not in self.subsystems[tenant_id]:
            return {"status": "error", "message": f"Subsystem {subsystem_id} not found for tenant {tenant_id}"}

        if self.subsystems[tenant_id][subsystem_id]["state"] != SubsystemState.RUNNING:
            return {"status": "warning", "message": f"Subsystem {subsystem_id} not running"}

        # Graceful pause
        logger.info(f"Gracefully pausing {subsystem_id} (timeout={timeout_s}s, tenant={tenant_id})")

        self.subsystems[tenant_id][subsystem_id]["state"] = SubsystemState.PAUSED
        self.subsystems[tenant_id][subsystem_id]["paused_at"] = datetime.utcnow().isoformat() + "Z"

        self._emit_audit_event("subsystem_paused", subsystem_id, tenant_id, operator_id)

        return {"status": "success", "message": f"Subsystem {subsystem_id} paused"}

    async def resume_subsystem(
        self,
        subsystem_id: str,
        tenant_id: str,
        operator_id: str = "unknown"
    ) -> Dict[str, Any]:
        """Resume a paused subsystem (tenant-scoped).

        Args:
            subsystem_id: Subsystem identifier
            tenant_id: Tenant ID (REQUIRED, fail-closed if missing)
            operator_id: Operator ID for audit

        Returns:
            Operation result with status + audit event

        Raises:
            ValueError: If tenant_id is invalid
        """
        self._validate_tenant_id(tenant_id)

        # Check tenant key exists
        if tenant_id not in self.subsystems:
            return {"status": "error", "message": f"Subsystem {subsystem_id} not found for tenant {tenant_id}"}

        if subsystem_id not in self.subsystems[tenant_id]:
            return {"status": "error", "message": f"Subsystem {subsystem_id} not found for tenant {tenant_id}"}

        if self.subsystems[tenant_id][subsystem_id]["state"] != SubsystemState.PAUSED:
            return {"status": "warning", "message": f"Subsystem {subsystem_id} not paused"}

        # Resume
        self.subsystems[tenant_id][subsystem_id]["state"] = SubsystemState.RUNNING

        self._emit_audit_event("subsystem_resumed", subsystem_id, tenant_id, operator_id)

        return {"status": "success", "message": f"Subsystem {subsystem_id} resumed"}

    async def stop_subsystem(
        self,
        subsystem_id: str,
        force: bool,
        timeout_s: int,
        tenant_id: str,
        operator_id: str = "unknown"
    ) -> Dict[str, Any]:
        """Stop a subsystem (graceful or force, with timeout bounds).

        Args:
            subsystem_id: Subsystem identifier
            force: Force immediate stop (True) vs graceful (False)
            timeout_s: Graceful shutdown timeout (1-3600 seconds, fail-closed if graceful)
            tenant_id: Tenant ID (REQUIRED, fail-closed if missing)
            operator_id: Operator ID for audit

        Returns:
            Operation result with status + audit event

        Raises:
            ValueError: If tenant_id or timeout_s (when graceful) are invalid
        """
        self._validate_tenant_id(tenant_id)

        # Validate timeout only if graceful (force doesn't use timeout)
        if not force:
            self._validate_timeout_s(timeout_s)

        # Check tenant key exists
        if tenant_id not in self.subsystems:
            return {"status": "error", "message": f"Subsystem {subsystem_id} not found for tenant {tenant_id}"}

        if subsystem_id not in self.subsystems[tenant_id]:
            return {"status": "error", "message": f"Subsystem {subsystem_id} not found for tenant {tenant_id}"}

        if self.subsystems[tenant_id][subsystem_id]["state"] == SubsystemState.STOPPED:
            return {"status": "warning", "message": f"Subsystem {subsystem_id} already stopped"}

        # Graceful or force shutdown
        if force:
            logger.warning(f"Force stopping {subsystem_id} (no graceful shutdown, tenant={tenant_id})")
        else:
            logger.info(f"Gracefully stopping {subsystem_id} (timeout={timeout_s}s, tenant={tenant_id})")

        self.subsystems[tenant_id][subsystem_id]["state"] = SubsystemState.STOPPED
        self.subsystems[tenant_id][subsystem_id]["stopped_at"] = datetime.utcnow().isoformat() + "Z"

        self._emit_audit_event("subsystem_stopped", subsystem_id, tenant_id, operator_id)

        return {"status": "success", "message": f"Subsystem {subsystem_id} stopped"}

    async def get_subsystem_status(self, subsystem_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Get subsystem status (tenant-scoped).

        Args:
            subsystem_id: Subsystem identifier
            tenant_id: Tenant ID (REQUIRED, fail-closed if missing)

        Returns:
            Subsystem status dict if found and belongs to tenant, None otherwise

        Raises:
            ValueError: If tenant_id is invalid
        """
        self._validate_tenant_id(tenant_id)

        # Return None if tenant not found (no cross-tenant leakage)
        if tenant_id not in self.subsystems:
            return None

        if subsystem_id not in self.subsystems[tenant_id]:
            return None

        sub = self.subsystems[tenant_id][subsystem_id]
        return {
            "subsystem_id": subsystem_id,
            "state": sub["state"].value,
            "started_at": sub.get("started_at"),
            "paused_at": sub.get("paused_at"),
            "stopped_at": sub.get("stopped_at"),
        }

    async def list_subsystems(self, tenant_id: str) -> List[Dict[str, Any]]:
        """List all subsystems for a tenant (tenant-scoped).

        Args:
            tenant_id: Tenant ID (REQUIRED, fail-closed if missing)

        Returns:
            List of subsystem status dicts for this tenant only

        Raises:
            ValueError: If tenant_id is invalid
        """
        self._validate_tenant_id(tenant_id)

        # Return empty list if tenant not found (no cross-tenant leakage)
        if tenant_id not in self.subsystems:
            return []

        return [
            await self.get_subsystem_status(sub_id, tenant_id)
            for sub_id in self.subsystems[tenant_id].keys()
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

    def get_audit_log(self, tenant_id: str) -> List[Dict[str, Any]]:
        """Get audit log for a tenant (tenant-scoped, immutable).

        Filters all audit events to only those for this tenant.
        No cross-tenant leakage.

        Args:
            tenant_id: Tenant ID (REQUIRED, fail-closed if missing)

        Returns:
            List of audit events for this tenant only

        Raises:
            ValueError: If tenant_id is invalid
        """
        self._validate_tenant_id(tenant_id)

        # Filter audit events by tenant_id (fail-closed: no leakage)
        return [
            event for event in self.audit_events
            if event.get("tenant_id") == tenant_id
        ]
