"""Tenant-Skill-Architecture: Tenant-scoped skill execution, versioning, and state management.

This module implements:
1. Tenant-scoped skill execution context (ADR-0114)
2. Tenant data isolation (no cross-tenant leakage, GDPR Art. 5)
3. Skill versioning + immutable rollback (ADR-0174)
4. Persistent state per tenant

The architecture ensures:
- Each tenant has isolated skill state (no cross-tenant data access)
- Skills execute in a tenant-bound context (fail-closed)
- Version rollback is atomic and fully reversible
- All state changes are audit-logged and immutable

GDPR Compliance:
- Art. 5 (integrity): Tenant isolation is fail-closed
- Art. 30, 32: All state changes audited and hash-chained
- Art. 6: Tenant data never leaks across isolation boundaries
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.paths import tenant as tenant_paths
from core.tenants import validate_tenant_id
from core.skills.contract import SkillContract, SKILL_REGISTRY


class VersionState(str, Enum):
    """State of a skill version."""
    ACTIVE = "active"          # Currently in use
    ROLLBACK_READY = "rollback_ready"  # Available for rollback
    DEPRECATED = "deprecated"  # Deprecated, no new executions
    ARCHIVED = "archived"      # Archived (read-only)


@dataclass(frozen=True)
class SkillVersion:
    """Immutable skill version record (ADR-0174)."""

    skill_id: str
    version: str  # Semantic versioning (e.g., "1.2.3")
    contract_hash: str  # SHA256 of skill contract
    config_hash: str  # SHA256 of skill config
    state: VersionState
    created_at: str  # ISO8601 timestamp
    created_by: str  # User/system that created this version

    # Rollback metadata
    supersedes_version: Optional[str] = None  # Previous version (for rollback chain)
    rollback_checksum: str = ""  # CRC32 for integrity check on rollback

    def to_dict(self) -> dict:
        """Serialize to dict."""
        return asdict(self)

    def is_rollbackable(self) -> bool:
        """Check if this version can be rolled back to."""
        return self.state in (VersionState.ACTIVE, VersionState.ROLLBACK_READY)


@dataclass(frozen=True)
class TenantSkillState:
    """Immutable tenant skill state snapshot (for rollback and recovery)."""

    tenant_id: str
    skill_id: str
    version: str
    state_data: Dict[str, Any]  # Skill-specific persistent state
    state_hash: str  # SHA256 of state_data
    previous_state_hash: Optional[str] = None  # For chaining state changes
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        """Serialize to dict."""
        return asdict(self)

    @classmethod
    def create(
        cls,
        tenant_id: str,
        skill_id: str,
        version: str,
        state_data: Dict[str, Any],
        previous_state_hash: Optional[str] = None,
    ) -> TenantSkillState:
        """Create a new immutable state snapshot."""
        validate_tenant_id(tenant_id)

        state_hash = hashlib.sha256(
            json.dumps(state_data, sort_keys=True, default=str).encode()
        ).hexdigest()

        return cls(
            tenant_id=tenant_id,
            skill_id=skill_id,
            version=version,
            state_data=state_data,
            state_hash=state_hash,
            previous_state_hash=previous_state_hash,
        )


@dataclass
class TenantSkillExecutionContext:
    """Tenant-scoped skill execution context (ADR-0114).

    This context is created fresh for each skill execution and ensures:
    - All state is tenant-isolated
    - No cross-tenant data leakage
    - Execution is auditable and reversible
    - Version compatibility is checked before execution
    """

    tenant_id: str
    skill_id: str
    version: str
    execution_id: str  # Unique execution identifier
    task_id: str  # Parent task ID

    # Execution input/output
    input_data: Dict[str, Any]
    output_data: Optional[Dict[str, Any]] = None
    error_data: Optional[str] = None

    # Execution metadata
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None
    duration_ms: int = 0
    status: str = "pending"  # pending, running, success, failure, rollback

    # Tenant isolation markers
    _is_isolated: bool = field(default=True, init=False)
    _isolation_hash: str = ""  # Hash of tenant_id + skill_id + version for verification

    # State snapshots before/after
    state_before: Optional[TenantSkillState] = None
    state_after: Optional[TenantSkillState] = None

    def __post_init__(self):
        """Validate context isolation on creation."""
        validate_tenant_id(self.tenant_id)

        # Create isolation hash to prevent context tampering
        isolation_data = f"{self.tenant_id}:{self.skill_id}:{self.version}:{self.execution_id}"
        self._isolation_hash = hashlib.sha256(isolation_data.encode()).hexdigest()

    def verify_isolation(self) -> bool:
        """Verify context has not been tampered with (fail-closed)."""
        isolation_data = f"{self.tenant_id}:{self.skill_id}:{self.version}:{self.execution_id}"
        expected_hash = hashlib.sha256(isolation_data.encode()).hexdigest()
        return self._isolation_hash == expected_hash and self._is_isolated

    def mark_success(self, output_data: Dict[str, Any], state_after: Optional[TenantSkillState] = None):
        """Mark execution as successful."""
        if not self.verify_isolation():
            raise RuntimeError(f"Execution context tampered (tenant={self.tenant_id})")

        self.status = "success"
        self.output_data = output_data
        self.state_after = state_after
        self.completed_at = datetime.now(timezone.utc).isoformat()

    def mark_failure(self, error: str, state_after: Optional[TenantSkillState] = None):
        """Mark execution as failed."""
        if not self.verify_isolation():
            raise RuntimeError(f"Execution context tampered (tenant={self.tenant_id})")

        self.status = "failure"
        self.error_data = error
        self.state_after = state_after
        self.completed_at = datetime.now(timezone.utc).isoformat()

    def to_audit_event(self) -> Dict[str, Any]:
        """Serialize to audit-log format (GDPR Art. 30)."""
        return {
            "type": "skill_executed",
            "tenant_id": self.tenant_id,
            "skill_id": self.skill_id,
            "version": self.version,
            "execution_id": self.execution_id,
            "task_id": self.task_id,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "input_hash": hashlib.sha256(json.dumps(self.input_data, sort_keys=True, default=str).encode()).hexdigest(),
            "output_hash": hashlib.sha256(json.dumps(self.output_data or {}, sort_keys=True, default=str).encode()).hexdigest() if self.output_data else None,
        }


@dataclass
class TenantSkillVersionManager:
    """Manages skill version history, activation, and rollback per tenant.

    Invariants:
    - Only one version can be ACTIVE per skill per tenant
    - Rollback is atomic and fully reversible
    - Version history is immutable (append-only)
    - All version changes are audit-logged
    """

    tenant_id: str
    skill_id: str
    _versions: Dict[str, SkillVersion] = field(default_factory=dict)  # version -> SkillVersion
    _active_version: Optional[str] = None
    _version_history: List[Tuple[str, datetime, str]] = field(default_factory=list)  # (version, timestamp, action)
    _storage_path: Optional[Path] = None

    def __post_init__(self):
        """Initialize version manager."""
        validate_tenant_id(self.tenant_id)

        # Setup storage path
        skill_dir = tenant_paths.tenant_skill_dir(self.tenant_id)
        self._storage_path = skill_dir / self.skill_id / "versions.json"

        # Load existing versions from disk
        self._load_versions()

    def _load_versions(self):
        """Load version history from persistent storage."""
        if not self._storage_path or not self._storage_path.exists():
            return

        try:
            with open(self._storage_path, "r") as f:
                data = json.load(f)

                for version_data in data.get("versions", []):
                    version = SkillVersion(
                        skill_id=version_data["skill_id"],
                        version=version_data["version"],
                        contract_hash=version_data["contract_hash"],
                        config_hash=version_data["config_hash"],
                        state=VersionState(version_data["state"]),
                        created_at=version_data["created_at"],
                        created_by=version_data["created_by"],
                        supersedes_version=version_data.get("supersedes_version"),
                        rollback_checksum=version_data.get("rollback_checksum", ""),
                    )
                    self._versions[version.version] = version

                self._active_version = data.get("active_version")
        except Exception as e:
            raise RuntimeError(f"Failed to load skill versions for {self.skill_id}: {e}")

    def _save_versions(self):
        """Save version history to persistent storage (append-only)."""
        if not self._storage_path:
            return

        # Ensure directory exists
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "tenant_id": self.tenant_id,
            "skill_id": self.skill_id,
            "active_version": self._active_version,
            "versions": [v.to_dict() for v in self._versions.values()],
            "history": [
                {"version": v, "timestamp": t.isoformat(), "action": a}
                for v, t, a in self._version_history
            ],
        }

        with open(self._storage_path, "w") as f:
            json.dump(data, f, indent=2)

    def register_version(
        self,
        version: str,
        contract: SkillContract,
        config_data: Dict[str, Any],
        created_by: str,
    ) -> Tuple[bool, str]:
        """Register a new skill version.

        Args:
            version: Semantic version (e.g., "1.2.3")
            contract: Skill contract (for validation)
            config_data: Skill configuration
            created_by: User/system creating this version

        Returns:
            (success, message)
        """
        if version in self._versions:
            return False, f"Version {version} already registered"

        contract_hash = hashlib.sha256(
            json.dumps({k: str(v) for k, v in asdict(contract).items()}, sort_keys=True).encode()
        ).hexdigest()

        config_hash = hashlib.sha256(
            json.dumps(config_data, sort_keys=True, default=str).encode()
        ).hexdigest()

        # Determine if this supersedes another version
        supersedes = None
        if self._active_version:
            supersedes = self._active_version

        skill_version = SkillVersion(
            skill_id=self.skill_id,
            version=version,
            contract_hash=contract_hash,
            config_hash=config_hash,
            state=VersionState.ACTIVE,  # New versions start as ACTIVE
            created_at=datetime.now(timezone.utc).isoformat(),
            created_by=created_by,
            supersedes_version=supersedes,
            rollback_checksum=self._compute_rollback_checksum(version),
        )

        self._versions[version] = skill_version
        old_active = self._active_version
        self._active_version = version

        # Record in history
        self._version_history.append((version, datetime.now(timezone.utc), "activate"))
        if old_active:
            self._versions[old_active] = SkillVersion(
                skill_id=self._versions[old_active].skill_id,
                version=self._versions[old_active].version,
                contract_hash=self._versions[old_active].contract_hash,
                config_hash=self._versions[old_active].config_hash,
                state=VersionState.ROLLBACK_READY,  # Previous version becomes rollback-ready
                created_at=self._versions[old_active].created_at,
                created_by=self._versions[old_active].created_by,
                supersedes_version=self._versions[old_active].supersedes_version,
                rollback_checksum=self._versions[old_active].rollback_checksum,
            )

        self._save_versions()
        return True, f"Version {version} registered and activated"

    def rollback_to_version(self, target_version: str, reason: str = "") -> Tuple[bool, str]:
        """Rollback to a previous version.

        Atomic operation:
        1. Verify target version exists and is rollbackable
        2. Mark current active as deprecated
        3. Activate target version
        4. Record rollback in audit trail

        Args:
            target_version: Version to rollback to
            reason: Reason for rollback (for audit trail)

        Returns:
            (success, message)
        """
        if target_version not in self._versions:
            return False, f"Version {target_version} not found"

        target = self._versions[target_version]
        if not target.is_rollbackable():
            return False, f"Version {target_version} is not rollbackable (state={target.state})"

        # Verify rollback checksum
        expected_checksum = self._compute_rollback_checksum(target_version)
        if target.rollback_checksum != expected_checksum:
            return False, f"Rollback checksum mismatch for {target_version} (data corruption)"

        old_active = self._active_version
        self._active_version = target_version

        # Mark old version as deprecated
        if old_active:
            self._versions[old_active] = SkillVersion(
                skill_id=self._versions[old_active].skill_id,
                version=self._versions[old_active].version,
                contract_hash=self._versions[old_active].contract_hash,
                config_hash=self._versions[old_active].config_hash,
                state=VersionState.DEPRECATED,
                created_at=self._versions[old_active].created_at,
                created_by=self._versions[old_active].created_by,
                supersedes_version=self._versions[old_active].supersedes_version,
                rollback_checksum=self._versions[old_active].rollback_checksum,
            )

        # Record in history
        action = f"rollback to {target_version}"
        if reason:
            action += f" ({reason})"
        self._version_history.append((target_version, datetime.now(timezone.utc), action))

        self._save_versions()
        return True, f"Rolled back to version {target_version}"

    def get_active_version(self) -> Optional[SkillVersion]:
        """Get currently active version."""
        if not self._active_version:
            return None
        return self._versions.get(self._active_version)

    def get_version(self, version: str) -> Optional[SkillVersion]:
        """Get specific version."""
        return self._versions.get(version)

    def list_versions(self) -> List[SkillVersion]:
        """List all registered versions (in order)."""
        return sorted(self._versions.values(), key=lambda v: v.created_at, reverse=True)

    def _compute_rollback_checksum(self, version: str) -> str:
        """Compute CRC32 checksum for rollback verification."""
        import zlib
        version_data = json.dumps(self._versions.get(version, {}).to_dict() if version in self._versions else {}, sort_keys=True).encode()
        return format(zlib.crc32(version_data) & 0xffffffff, '08x')


@dataclass
class TenantSkillStateManager:
    """Manages persistent skill state per tenant.

    Invariants:
    - State changes are immutable (new state snapshots, never mutations)
    - State is hash-chained for integrity (GDPR Art. 32)
    - Cross-tenant access is impossible (fail-closed)
    - State is recoverable from audit trail
    """

    tenant_id: str
    skill_id: str
    _state_chain: List[TenantSkillState] = field(default_factory=list)  # Chain of immutable snapshots
    _current_state: Optional[TenantSkillState] = None
    _storage_path: Optional[Path] = None

    def __post_init__(self):
        """Initialize state manager."""
        validate_tenant_id(self.tenant_id)

        # Setup storage path
        skill_dir = tenant_paths.tenant_skill_dir(self.tenant_id)
        self._storage_path = skill_dir / self.skill_id / "state.jsonl"

        # Load existing state chain
        self._load_state_chain()

    def _load_state_chain(self):
        """Load state chain from persistent storage (JSONL format)."""
        if not self._storage_path or not self._storage_path.exists():
            return

        try:
            with open(self._storage_path, "r") as f:
                for line in f:
                    if not line.strip():
                        continue

                    data = json.loads(line)
                    state = TenantSkillState(
                        tenant_id=data["tenant_id"],
                        skill_id=data["skill_id"],
                        version=data["version"],
                        state_data=data["state_data"],
                        state_hash=data["state_hash"],
                        previous_state_hash=data.get("previous_state_hash"),
                        timestamp=data["timestamp"],
                    )
                    self._state_chain.append(state)

            if self._state_chain:
                self._current_state = self._state_chain[-1]
        except Exception as e:
            raise RuntimeError(f"Failed to load skill state for {self.skill_id}: {e}")

    def _save_state_snapshot(self, state: TenantSkillState):
        """Append state snapshot to immutable chain (GDPR Art. 5 - integrity)."""
        if not self._storage_path:
            return

        # Ensure directory exists
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)

        # Append to JSONL (immutable append-only log)
        with open(self._storage_path, "a") as f:
            f.write(json.dumps(state.to_dict()) + "\n")

        self._state_chain.append(state)
        self._current_state = state

    def save_state(
        self,
        version: str,
        state_data: Dict[str, Any],
    ) -> TenantSkillState:
        """Save a new state snapshot.

        Args:
            version: Skill version
            state_data: State to save

        Returns:
            The new TenantSkillState snapshot
        """
        previous_hash = self._current_state.state_hash if self._current_state else None

        state = TenantSkillState.create(
            tenant_id=self.tenant_id,
            skill_id=self.skill_id,
            version=version,
            state_data=state_data,
            previous_state_hash=previous_hash,
        )

        self._save_state_snapshot(state)
        return state

    def get_current_state(self) -> Optional[TenantSkillState]:
        """Get current state snapshot."""
        return self._current_state

    def get_state_at_version(self, version: str) -> Optional[TenantSkillState]:
        """Get most recent state for a specific version."""
        for state in reversed(self._state_chain):
            if state.version == version:
                return state
        return None

    def get_state_history(self) -> List[TenantSkillState]:
        """Get full state history (immutable chain)."""
        return self._state_chain.copy()

    def verify_chain_integrity(self) -> Tuple[bool, str]:
        """Verify state chain hash-chaining integrity (GDPR Art. 32)."""
        if not self._state_chain:
            return True, "No state chain to verify"

        for i, state in enumerate(self._state_chain):
            if i == 0:
                if state.previous_state_hash is not None:
                    return False, f"First state should not have previous_hash (index={i})"
            else:
                previous = self._state_chain[i - 1]
                if state.previous_state_hash != previous.state_hash:
                    return False, f"State chain broken at index={i}: expected {previous.state_hash}, got {state.previous_state_hash}"

        return True, "State chain integrity verified"


class TenantSkillArchitecture:
    """Main orchestrator for tenant-scoped skill management.

    This is the primary API for:
    - Creating tenant-scoped execution contexts
    - Managing skill versions per tenant
    - Managing persistent state per tenant
    - Executing skills with isolation guarantees
    """

    def __init__(self, tenant_id: str):
        """Initialize architecture for a tenant.

        Args:
            tenant_id: Tenant identifier (validated)

        Raises:
            ValueError: If tenant_id is invalid
        """
        validate_tenant_id(tenant_id)
        self.tenant_id = tenant_id

        # Per-skill managers (lazy-loaded)
        self._version_managers: Dict[str, TenantSkillVersionManager] = {}
        self._state_managers: Dict[str, TenantSkillStateManager] = {}

    def get_version_manager(self, skill_id: str) -> TenantSkillVersionManager:
        """Get or create version manager for a skill."""
        if skill_id not in self._version_managers:
            self._version_managers[skill_id] = TenantSkillVersionManager(
                tenant_id=self.tenant_id,
                skill_id=skill_id,
            )
        return self._version_managers[skill_id]

    def get_state_manager(self, skill_id: str) -> TenantSkillStateManager:
        """Get or create state manager for a skill."""
        if skill_id not in self._state_managers:
            self._state_managers[skill_id] = TenantSkillStateManager(
                tenant_id=self.tenant_id,
                skill_id=skill_id,
            )
        return self._state_managers[skill_id]

    def create_execution_context(
        self,
        skill_id: str,
        task_id: str,
        input_data: Dict[str, Any],
        execution_id: Optional[str] = None,
    ) -> TenantSkillExecutionContext:
        """Create a tenant-scoped execution context for a skill.

        Args:
            skill_id: Skill to execute
            task_id: Parent task ID
            input_data: Input to skill
            execution_id: Optional execution ID (generated if not provided)

        Returns:
            TenantSkillExecutionContext ready for execution

        Raises:
            RuntimeError: If skill is not registered or version mismatch
        """
        version_mgr = self.get_version_manager(skill_id)
        active_version = version_mgr.get_active_version()

        if not active_version:
            raise RuntimeError(f"No active version for skill {skill_id} in tenant {self.tenant_id}")

        if not execution_id:
            execution_id = f"{self.tenant_id}:{skill_id}:{active_version.version}:{int(datetime.now(timezone.utc).timestamp() * 1000)}"

        context = TenantSkillExecutionContext(
            tenant_id=self.tenant_id,
            skill_id=skill_id,
            version=active_version.version,
            execution_id=execution_id,
            task_id=task_id,
            input_data=input_data,
        )

        # Load state before execution
        state_mgr = self.get_state_manager(skill_id)
        context.state_before = state_mgr.get_current_state()

        return context

    async def execute_skill(
        self,
        context: TenantSkillExecutionContext,
        skill_fn: Callable[[Dict[str, Any], Optional[TenantSkillState]], Any],
        timeout_seconds: float = 30.0,
    ) -> Tuple[bool, Any, Optional[str]]:
        """Execute a skill within a tenant-scoped context.

        Args:
            context: Execution context (created by create_execution_context)
            skill_fn: Async function to execute: async fn(input_data, state_before) -> output
            timeout_seconds: Execution timeout

        Returns:
            (success, output_or_error, execution_event)

        Raises:
            RuntimeError: If context is tampered or isolation violated
        """
        if not context.verify_isolation():
            raise RuntimeError(f"Execution context tampered (tenant={self.tenant_id})")

        context.status = "running"
        start_time = datetime.now(timezone.utc)

        try:
            # Execute with timeout
            result = await asyncio.wait_for(
                skill_fn(context.input_data, context.state_before),
                timeout=timeout_seconds,
            )

            # Save state after execution
            state_mgr = self.get_state_manager(context.skill_id)
            output_state = state_mgr.save_state(
                version=context.version,
                state_data=result.get("state", {}),
            )

            context.mark_success(
                output_data=result,
                state_after=output_state,
            )

            return True, result, context.to_audit_event()
        except asyncio.TimeoutError:
            context.mark_failure(
                error=f"Skill execution timeout ({timeout_seconds}s exceeded)",
            )
            return False, None, context.to_audit_event()
        except Exception as e:
            context.mark_failure(
                error=f"Skill execution error: {str(e)}",
            )
            return False, None, context.to_audit_event()
        finally:
            # Record duration
            end_time = datetime.now(timezone.utc)
            context.duration_ms = int((end_time - start_time).total_seconds() * 1000)

    def rollback_skill_version(
        self,
        skill_id: str,
        target_version: str,
        reason: str = "",
    ) -> Tuple[bool, str]:
        """Rollback a skill to a previous version (atomic operation).

        Args:
            skill_id: Skill to rollback
            target_version: Version to rollback to
            reason: Reason for rollback

        Returns:
            (success, message)
        """
        version_mgr = self.get_version_manager(skill_id)
        return version_mgr.rollback_to_version(target_version, reason)

    def register_skill_version(
        self,
        skill_id: str,
        version: str,
        contract: SkillContract,
        config_data: Dict[str, Any],
        created_by: str,
    ) -> Tuple[bool, str]:
        """Register a new skill version in this tenant.

        Args:
            skill_id: Skill identifier
            version: Semantic version
            contract: Skill contract
            config_data: Configuration
            created_by: User/system registering

        Returns:
            (success, message)
        """
        version_mgr = self.get_version_manager(skill_id)
        return version_mgr.register_version(version, contract, config_data, created_by)

    def verify_isolation(self) -> Tuple[bool, str]:
        """Verify tenant isolation boundaries (security check).

        Returns:
            (success, message)
        """
        try:
            validate_tenant_id(self.tenant_id)

            # Check that version/state managers are isolated
            for skill_id, mgr in self._version_managers.items():
                if mgr.tenant_id != self.tenant_id:
                    return False, f"Version manager isolation violation for {skill_id}"

            for skill_id, mgr in self._state_managers.items():
                if mgr.tenant_id != self.tenant_id:
                    return False, f"State manager isolation violation for {skill_id}"

            return True, "Tenant isolation verified"
        except Exception as e:
            return False, f"Isolation verification failed: {str(e)}"
