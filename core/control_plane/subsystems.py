"""Subsystem Registry — Central management of CorvinOS subsystems (ADR-2029)."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable
from enum import Enum
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class SubsystemType(Enum):
    """Subsystem type classification."""
    AUDIT = "audit"
    LEARNING = "learning"
    SKILLS = "skills"
    PLUGINS = "plugins"
    SECURITY = "security"
    DATA_FLOW = "data_flow"
    CONTEXT = "context"
    WORKFLOW = "workflow"


@dataclass(frozen=True)
class SubsystemInstance:
    """Immutable subsystem instance definition."""

    subsystem_id: str
    subsystem_type: SubsystemType
    display_name: str
    description: str
    enabled: bool
    version: str
    tenant_id: str
    created_at: str  # ISO 8601
    last_health_check: Optional[str] = None
    health_status: str = "unknown"  # "healthy", "degraded", "error", "unknown"
    dependencies: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        """Convert to dictionary representation."""
        return {
            "subsystem_id": self.subsystem_id,
            "subsystem_type": self.subsystem_type.value,
            "display_name": self.display_name,
            "description": self.description,
            "enabled": self.enabled,
            "version": self.version,
            "tenant_id": self.tenant_id,
            "created_at": self.created_at,
            "last_health_check": self.last_health_check,
            "health_status": self.health_status,
            "dependencies": self.dependencies,
        }


class SubsystemRegistry:
    """Central registry for managing all CorvinOS subsystems.

    Responsibilities:
    - Register/unregister subsystems with metadata
    - Track subsystem dependencies and ordering
    - Emit immutable audit events for all operations
    - Enforce tenant isolation
    - Provide health check status aggregation
    - Fail-closed error handling
    """

    def __init__(self, audit_backend):
        """Initialize registry with audit backend.

        Args:
            audit_backend: Backend for immutable audit event logging
        """
        self.audit = audit_backend
        self.subsystems: Dict[str, SubsystemInstance] = {}
        self.health_checks: Dict[str, Callable] = {}

    async def register_subsystem(
        self,
        subsystem_id: str,
        subsystem_type: SubsystemType,
        display_name: str,
        description: str,
        version: str,
        tenant_id: str,
        dependencies: Optional[List[str]] = None,
    ) -> Dict:
        """Register a new subsystem.

        Args:
            subsystem_id: Unique subsystem identifier
            subsystem_type: Type of subsystem (from SubsystemType enum)
            display_name: Human-readable name
            description: Subsystem description
            version: Version string
            tenant_id: Tenant scope
            dependencies: List of subsystem IDs this depends on

        Returns:
            Status dict with result

        Raises:
            ValueError: If subsystem already registered or invalid inputs
        """
        key = f"{tenant_id}:{subsystem_id}"

        # Fail-closed: validate inputs
        if not subsystem_id or not isinstance(subsystem_id, str):
            raise ValueError("subsystem_id must be non-empty string")
        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError("tenant_id must be non-empty string")
        if key in self.subsystems:
            raise ValueError(f"Subsystem {subsystem_id} already registered for tenant {tenant_id}")

        # Validate dependencies exist
        if dependencies:
            for dep_id in dependencies:
                dep_key = f"{tenant_id}:{dep_id}"
                if dep_key not in self.subsystems:
                    raise ValueError(f"Dependency {dep_id} not registered")

        now = datetime.utcnow().isoformat() + "Z"
        instance = SubsystemInstance(
            subsystem_id=subsystem_id,
            subsystem_type=subsystem_type,
            display_name=display_name,
            description=description,
            enabled=True,
            version=version,
            tenant_id=tenant_id,
            created_at=now,
            dependencies=dependencies or [],
        )

        self.subsystems[key] = instance

        # Emit immutable audit event
        await self.audit.log_event(
            "subsystem_registered",
            {
                "subsystem_id": subsystem_id,
                "subsystem_type": subsystem_type.value,
                "version": version,
                "tenant_id": tenant_id,
                "dependencies": dependencies or [],
                "timestamp": now,
            }
        )

        logger.info(f"Registered subsystem {subsystem_id} ({subsystem_type.value})")
        return {"status": "registered", "subsystem_id": subsystem_id}

    async def unregister_subsystem(
        self,
        subsystem_id: str,
        tenant_id: str,
    ) -> Dict:
        """Unregister a subsystem.

        Args:
            subsystem_id: Subsystem to unregister
            tenant_id: Tenant scope

        Returns:
            Status dict with result

        Raises:
            ValueError: If subsystem not found or has dependents
        """
        key = f"{tenant_id}:{subsystem_id}"

        if key not in self.subsystems:
            raise ValueError(f"Subsystem {subsystem_id} not found for tenant {tenant_id}")

        # Check for dependents
        for other_key, other_instance in self.subsystems.items():
            if other_instance.tenant_id == tenant_id and subsystem_id in other_instance.dependencies:
                raise ValueError(f"Cannot unregister: {other_instance.subsystem_id} depends on {subsystem_id}")

        instance = self.subsystems.pop(key)

        # Emit immutable audit event
        await self.audit.log_event(
            "subsystem_unregistered",
            {
                "subsystem_id": subsystem_id,
                "subsystem_type": instance.subsystem_type.value,
                "tenant_id": tenant_id,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
        )

        logger.info(f"Unregistered subsystem {subsystem_id}")
        return {"status": "unregistered", "subsystem_id": subsystem_id}

    def register_health_check(
        self,
        subsystem_id: str,
        check_fn: Callable,
    ) -> None:
        """Register a health check function for a subsystem.

        Args:
            subsystem_id: Subsystem to check
            check_fn: Async function that returns (healthy: bool, status: str)
        """
        self.health_checks[subsystem_id] = check_fn

    async def check_health(
        self,
        subsystem_id: str,
        tenant_id: str,
    ) -> Dict:
        """Check health status of a subsystem.

        Args:
            subsystem_id: Subsystem to check
            tenant_id: Tenant scope

        Returns:
            Health status dict
        """
        key = f"{tenant_id}:{subsystem_id}"

        if key not in self.subsystems:
            return {
                "subsystem_id": subsystem_id,
                "health_status": "unknown",
                "message": "subsystem not found",
            }

        # Run health check if registered
        health_status = "unknown"
        if subsystem_id in self.health_checks:
            try:
                is_healthy, status = await self.health_checks[subsystem_id]()
                health_status = "healthy" if is_healthy else status
            except Exception as e:
                health_status = f"error: {str(e)}"
                logger.error(f"Health check failed for {subsystem_id}: {e}")

        now = datetime.utcnow().isoformat() + "Z"
        instance = self.subsystems[key]

        # Update instance with new health status
        updated = SubsystemInstance(
            subsystem_id=instance.subsystem_id,
            subsystem_type=instance.subsystem_type,
            display_name=instance.display_name,
            description=instance.description,
            enabled=instance.enabled,
            version=instance.version,
            tenant_id=instance.tenant_id,
            created_at=instance.created_at,
            last_health_check=now,
            health_status=health_status,
            dependencies=instance.dependencies,
        )
        self.subsystems[key] = updated

        # Emit immutable audit event
        await self.audit.log_event(
            "subsystem_health_checked",
            {
                "subsystem_id": subsystem_id,
                "health_status": health_status,
                "tenant_id": tenant_id,
                "timestamp": now,
            }
        )

        return {
            "subsystem_id": subsystem_id,
            "health_status": health_status,
            "last_check": now,
        }

    async def get_subsystem(
        self,
        subsystem_id: str,
        tenant_id: str,
    ) -> Optional[SubsystemInstance]:
        """Get subsystem instance by ID.

        Args:
            subsystem_id: Subsystem ID
            tenant_id: Tenant scope

        Returns:
            SubsystemInstance or None if not found
        """
        key = f"{tenant_id}:{subsystem_id}"
        return self.subsystems.get(key)

    def list_subsystems_by_tenant(self, tenant_id: str) -> List[SubsystemInstance]:
        """List all subsystems for a tenant.

        Args:
            tenant_id: Tenant scope

        Returns:
            List of SubsystemInstance objects
        """
        return [
            instance for instance in self.subsystems.values()
            if instance.tenant_id == tenant_id
        ]

    def list_subsystems_by_type(
        self,
        subsystem_type: SubsystemType,
        tenant_id: str,
    ) -> List[SubsystemInstance]:
        """List subsystems by type for a tenant.

        Args:
            subsystem_type: Type to filter on
            tenant_id: Tenant scope

        Returns:
            List of matching SubsystemInstance objects
        """
        return [
            instance for instance in self.subsystems.values()
            if instance.subsystem_type == subsystem_type and instance.tenant_id == tenant_id
        ]
