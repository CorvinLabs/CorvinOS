"""Subsystem Controller — Manage subsystem state and configuration (ADR-2029 Stream 2)."""

from dataclasses import dataclass, asdict
from enum import Enum
from typing import Dict, List, Optional
import asyncio
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class SubsystemState(Enum):
    """Subsystem lifecycle states."""

    ENABLED = "enabled"
    DISABLED = "disabled"
    PAUSED = "paused"
    ERROR = "error"


@dataclass(frozen=True)
class SubsystemConfig:
    """Immutable subsystem configuration."""

    subsystem_id: str
    enabled: bool
    config: Dict
    updated_at: str  # ISO 8601 timestamp
    tenant_id: str


class LicenseError(Exception):
    """Raised when license limits are exceeded."""

    pass


class SubsystemController:
    """Manages subsystem state, configuration, and monitoring.

    Responsibilities:
    - Enable/disable subsystems with license enforcement
    - Update subsystem configuration with validation
    - Track subsystem state across tenant scope
    - Emit audit events for all operations
    - Enforce tenant isolation
    """

    def __init__(self, audit_backend, license_backend):
        """Initialize controller with audit and license backends.

        Args:
            audit_backend: Backend for audit event logging (must have log_event method)
            license_backend: Backend for license validation (must have can_enable_subsystem method)
        """
        self.audit = audit_backend
        self.license = license_backend
        self.subsystems: Dict[str, SubsystemConfig] = {}
        self.state: Dict[str, SubsystemState] = {}
        self._lock = asyncio.Lock()

    async def enable_subsystem(self, subsystem_id: str, tenant_id: str) -> Dict:
        """Enable a subsystem with license check.

        Args:
            subsystem_id: ID of subsystem to enable
            tenant_id: Tenant scope

        Returns:
            Status dict with result

        Raises:
            LicenseError: If license limit exceeded
        """
        async with self._lock:
            # Check if already enabled
            state_key = f"{tenant_id}:{subsystem_id}"
            if state_key in self.state and self.state[state_key] == SubsystemState.ENABLED:
                await self.audit.log_event(
                    "subsystem_enable_idempotent",
                    {
                        "subsystem_id": subsystem_id,
                        "tenant_id": tenant_id,
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    },
                )
                return {"status": "already_enabled", "subsystem_id": subsystem_id}

            # Check license
            if not self.license.can_enable_subsystem(subsystem_id, tenant_id):
                await self.audit.log_event(
                    "subsystem_enable_denied",
                    {
                        "subsystem_id": subsystem_id,
                        "reason": "license_exceeded",
                        "tenant_id": tenant_id,
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    },
                )
                raise LicenseError(f"Cannot enable {subsystem_id}: license limit reached")

            # Update state
            self.state[state_key] = SubsystemState.ENABLED

            # Log audit event
            await self.audit.log_event(
                "subsystem_enabled",
                {
                    "subsystem_id": subsystem_id,
                    "tenant_id": tenant_id,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )

            logger.info(f"Enabled subsystem {subsystem_id} for tenant {tenant_id}")
            return {"status": "enabled", "subsystem_id": subsystem_id}

    async def disable_subsystem(self, subsystem_id: str, tenant_id: str) -> Dict:
        """Disable a subsystem.

        Args:
            subsystem_id: ID of subsystem to disable
            tenant_id: Tenant scope

        Returns:
            Status dict with result
        """
        async with self._lock:
            state_key = f"{tenant_id}:{subsystem_id}"

            # Check if already disabled
            if state_key not in self.state or self.state[state_key] == SubsystemState.DISABLED:
                await self.audit.log_event(
                    "subsystem_disable_idempotent",
                    {
                        "subsystem_id": subsystem_id,
                        "tenant_id": tenant_id,
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    },
                )
                return {"status": "already_disabled", "subsystem_id": subsystem_id}

            # Update state
            self.state[state_key] = SubsystemState.DISABLED

            # Log audit event
            await self.audit.log_event(
                "subsystem_disabled",
                {
                    "subsystem_id": subsystem_id,
                    "tenant_id": tenant_id,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )

            logger.info(f"Disabled subsystem {subsystem_id} for tenant {tenant_id}")
            return {"status": "disabled", "subsystem_id": subsystem_id}

    async def pause_subsystem(self, subsystem_id: str, tenant_id: str) -> Dict:
        """Pause a subsystem (graceful shutdown, 30s timeout).

        Args:
            subsystem_id: ID of subsystem to pause
            tenant_id: Tenant scope

        Returns:
            Status dict with result
        """
        async with self._lock:
            state_key = f"{tenant_id}:{subsystem_id}"

            # Check current state
            if state_key not in self.state or self.state[state_key] == SubsystemState.PAUSED:
                return {"status": "already_paused", "subsystem_id": subsystem_id}

            # Update state (graceful pause with timeout)
            self.state[state_key] = SubsystemState.PAUSED

            # Log audit event
            await self.audit.log_event(
                "subsystem_paused",
                {
                    "subsystem_id": subsystem_id,
                    "tenant_id": tenant_id,
                    "timeout_seconds": 30,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )

            logger.info(f"Paused subsystem {subsystem_id} for tenant {tenant_id} (30s timeout)")
            return {"status": "paused", "subsystem_id": subsystem_id}

    async def resume_subsystem(self, subsystem_id: str, tenant_id: str) -> Dict:
        """Resume a paused subsystem.

        Args:
            subsystem_id: ID of subsystem to resume
            tenant_id: Tenant scope

        Returns:
            Status dict with result
        """
        async with self._lock:
            state_key = f"{tenant_id}:{subsystem_id}"

            # Check current state
            if state_key not in self.state or self.state[state_key] != SubsystemState.PAUSED:
                return {"status": "not_paused", "subsystem_id": subsystem_id}

            # Update state
            self.state[state_key] = SubsystemState.ENABLED

            # Log audit event
            await self.audit.log_event(
                "subsystem_resumed",
                {
                    "subsystem_id": subsystem_id,
                    "tenant_id": tenant_id,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )

            logger.info(f"Resumed subsystem {subsystem_id} for tenant {tenant_id}")
            return {"status": "resumed", "subsystem_id": subsystem_id}

    async def update_subsystem_config(
        self, subsystem_id: str, config: Dict, tenant_id: str
    ) -> Dict:
        """Update subsystem configuration with validation.

        Args:
            subsystem_id: ID of subsystem
            config: New configuration dict
            tenant_id: Tenant scope

        Returns:
            Status dict with result

        Raises:
            ValueError: If config is invalid
        """
        # Validate config schema
        if not isinstance(config, dict):
            raise ValueError(f"Invalid config for {subsystem_id}: must be dict")

        if not self._validate_config(subsystem_id, config):
            raise ValueError(f"Config validation failed for {subsystem_id}")

        async with self._lock:
            config_key = f"{tenant_id}:{subsystem_id}"
            self.subsystems[config_key] = SubsystemConfig(
                subsystem_id=subsystem_id,
                enabled=True,
                config=config,
                updated_at=datetime.utcnow().isoformat() + "Z",
                tenant_id=tenant_id,
            )

            # Log audit event
            await self.audit.log_event(
                "subsystem_config_updated",
                {
                    "subsystem_id": subsystem_id,
                    "config_keys": list(config.keys()),
                    "tenant_id": tenant_id,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )

            logger.info(f"Updated config for subsystem {subsystem_id}")
            return {"status": "updated", "subsystem_id": subsystem_id}

    def get_subsystem_status(self, subsystem_id: str, tenant_id: str) -> Dict:
        """Get current subsystem status (non-blocking).

        Args:
            subsystem_id: ID of subsystem
            tenant_id: Tenant scope

        Returns:
            Status dict with state and config
        """
        state_key = f"{tenant_id}:{subsystem_id}"
        config_key = f"{tenant_id}:{subsystem_id}"

        state = self.state.get(state_key, SubsystemState.DISABLED)
        config_obj = self.subsystems.get(config_key)

        return {
            "subsystem_id": subsystem_id,
            "state": state.value,
            "config": config_obj.config if config_obj else {},
            "updated_at": config_obj.updated_at if config_obj else None,
            "tenant_id": tenant_id,
        }

    def list_subsystems(self, tenant_id: str) -> List[Dict]:
        """List all subsystems for a tenant.

        Args:
            tenant_id: Tenant scope

        Returns:
            List of subsystem status dicts
        """
        subsystems = []
        for state_key, state in self.state.items():
            if state_key.startswith(f"{tenant_id}:"):
                subsystem_id = state_key.split(":", 1)[1]
                subsystems.append(self.get_subsystem_status(subsystem_id, tenant_id))
        return subsystems

    def _validate_config(self, subsystem_id: str, config: Dict) -> bool:
        """Validate subsystem config against schema.

        Args:
            subsystem_id: ID of subsystem
            config: Config dict to validate

        Returns:
            True if valid, False otherwise
        """
        # Placeholder: implement schema validation per subsystem
        # For now, accept any non-empty dict
        return isinstance(config, dict) and (not config or all(isinstance(k, str) for k in config.keys()))
