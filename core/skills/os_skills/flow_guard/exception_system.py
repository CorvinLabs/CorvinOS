"""Stream 3 Phase 3: Exception Request System (Days 5–10 of Week 3).

Operator override system for Flow Guard: when data flow blocks need exception,
create time-limited exception that overrides policy decision.

**Components:**
1. Exception Request API (HTTP routes)
2. TTL Manager (auto-expiry cleanup)
3. Classification Override (apply exception to decisions)
4. Audit Integration (log all exception lifecycle)

**Compliance:**
- GDPR Art. 30/32: All exceptions audit-logged with tenant_id
- ADR-0314: Exception usage emitted as LearningEvent (preference_feedback type)
- Fail-closed: missing exception_id → deny (revert to policy decision)
- TTL enforcement: expired exceptions ignored (automatic revert)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any
from uuid import uuid4

from core.learning.event_store import EventStore
from core.learning.learning_events import LearningEvent, EventType

logger = logging.getLogger(__name__)


class ExceptionStatus(str, Enum):
    """Exception request lifecycle status."""
    PENDING = "pending"        # Created, waiting for approval
    APPROVED = "approved"      # Approved, active override
    EXPIRED = "expired"        # TTL elapsed, auto-revoked
    REVOKED = "revoked"        # Manually revoked by operator
    DENIED = "denied"          # Approval denied


@dataclass(frozen=True)
class ExceptionRequest:
    """Immutable exception request (operator override for policy decision).

    When Flow Guard would normally DENY a flow, operator can request
    a time-limited exception that forces ALLOW for that data class.
    """
    exception_id: str = field(default_factory=lambda: str(uuid4()))
    flow_id: str = ""  # Flow that triggered exception request
    data_class: str = ""  # "pii", "financial", etc. — what policy is being overridden
    engine: str = ""  # Target engine (for context)
    destination: str = ""  # Where flow goes (for context)
    policy_decision: str = ""  # Original policy decision: "deny"
    reason: str = ""  # Why operator is requesting exception (PII is scrubbed in audit)
    created_by: str = ""  # Operator ID (for audit)
    ttl_hours: int = 1  # Time-to-live in hours (1–24)
    tenant_id: str = ""  # Tenant scope (GDPR Art. 32)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    expires_at: str = ""  # Auto-calculated: timestamp + ttl_hours

    def __post_init__(self):
        """Validate exception (frozen dataclass, fail-closed)."""
        if not self.flow_id:
            raise ValueError("flow_id required")
        if not self.data_class:
            raise ValueError("data_class required")
        if not self.engine:
            raise ValueError("engine required")
        if not self.destination:
            raise ValueError("destination required")
        if self.policy_decision not in ("allow", "deny"):
            raise ValueError(f"policy_decision must be 'allow' or 'deny', got {self.policy_decision!r}")
        if not self.created_by:
            raise ValueError("created_by (operator_id) required")
        if not self.tenant_id:
            raise ValueError("tenant_id required (GDPR Art. 32)")
        if not (1 <= self.ttl_hours <= 24):
            raise ValueError(f"ttl_hours must be 1–24, got {self.ttl_hours}")
        if not self.expires_at:
            # Auto-calculate expires_at
            import datetime as dt
            ts = datetime.fromisoformat(self.timestamp.replace("Z", "+00:00"))
            expires = ts + timedelta(hours=self.ttl_hours)
            object.__setattr__(self, "expires_at", expires.isoformat() + "Z")


class ExceptionManager:
    """Manages exception lifecycle: create, check validity, audit, reap expired (Phase 3).

    Integration points:
    - Operator creates exception via console API
    - Flow Guard classifier checks exception before applying policy
    - TTL manager reaps expired exceptions (async, periodic)
    - Audit trail logs all lifecycle events
    """

    def __init__(
        self,
        event_store: EventStore,
        tenant_id: str = "_default",
        skill_id: str = "os.flow_guard",
        skill_version: str = "1.0.0",
        storage_dir: Optional[Path] = None,
    ):
        """Initialize exception manager.

        Args:
            event_store: EventStore for audit logging (audit-first)
            tenant_id: Tenant scope (GDPR)
            skill_id: Skill identifier
            skill_version: Skill version
            storage_dir: Directory to persist exceptions (default: ~/.corvin/tenants/<tid>/global/)
        """
        self.event_store = event_store
        self.tenant_id = tenant_id
        self.skill_id = skill_id
        self.skill_version = skill_version

        # Storage directory
        if storage_dir is None:
            from core.paths.tenant import tenant_home
            storage_dir = Path(tenant_home(tenant_id)) / "flow_guard_exceptions"
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.exceptions_file = self.storage_dir / "exceptions.jsonl"
        self.index_file = self.storage_dir / "exception_index.json"

        # In-memory cache (reload from disk on init)
        self.exceptions: Dict[str, ExceptionRequest] = self._load_exceptions()

    def create_exception(self, request: ExceptionRequest) -> str:
        """Create exception request (audit-first).

        Operator submits exception to override policy decision for a data class.

        Args:
            request: ExceptionRequest with all required fields

        Returns:
            exception_id (uuid)

        Raises:
            ValueError: Invalid request
            RuntimeError: Storage write failed (audit-first fail-closed)
        """
        # Validate tenant isolation (GDPR Art. 32)
        if request.tenant_id != self.tenant_id:
            raise ValueError(
                f"Tenant mismatch: request tenant={request.tenant_id}, "
                f"manager tenant={self.tenant_id}"
            )

        # Validate TTL
        if not (1 <= request.ttl_hours <= 24):
            raise ValueError(f"ttl_hours must be 1–24, got {request.ttl_hours}")

        # Store exception (audit-first: chain write happens FIRST)
        audit_event = LearningEvent.create(
            event_type=EventType.PREFERENCE,  # preference feedback (policy override)
            skill_id=self.skill_id,
            tenant_id=self.tenant_id,
            signal={
                "action": "exception_created",
                "exception_id": request.exception_id,
                "flow_id": request.flow_id,
                "data_class": request.data_class,
                "ttl_hours": request.ttl_hours,
                "created_by": request.created_by,
                "expires_at": request.expires_at,
                # NOTE: reason is deliberately excluded (PII scrubbing)
            },
            skill_version=self.skill_version,
            lom="flow_guard.exception_system:create_exception:L120",
        )

        try:
            self.event_store.write_event(audit_event)
        except (RuntimeError, IOError) as e:
            logger.error(f"Failed to write exception creation event: {e}")
            raise RuntimeError(f"Exception creation rejected by audit chain: {e}") from e

        # Persist exception to disk
        try:
            self._persist_exception(request)
        except IOError as e:
            logger.error(f"Failed to persist exception: {e}")
            raise RuntimeError(f"Exception storage failed: {e}") from e

        # Update in-memory cache
        self.exceptions[request.exception_id] = request

        logger.info(
            f"Exception created: id={request.exception_id}, flow={request.flow_id}, "
            f"data_class={request.data_class}, ttl={request.ttl_hours}h, "
            f"expires={request.expires_at}"
        )

        return request.exception_id

    def get_exception(self, exception_id: str) -> Optional[ExceptionRequest]:
        """Retrieve exception request by ID.

        Args:
            exception_id: Exception identifier

        Returns:
            ExceptionRequest if found and not expired, None otherwise
        """
        exc = self.exceptions.get(exception_id)
        if not exc:
            return None

        # Check expiration
        if self._is_expired(exc):
            logger.info(f"Exception {exception_id} has expired, returning None")
            return None

        return exc

    def check_exception_valid(self, exception_id: str) -> bool:
        """Check if exception exists and is not expired.

        Used by Flow Guard classifier to determine if override applies.

        Args:
            exception_id: Exception identifier

        Returns:
            True if exception is valid (exists and not expired), False otherwise
        """
        exc = self.get_exception(exception_id)
        return exc is not None

    def list_active_exceptions(self, data_class: Optional[str] = None) -> List[ExceptionRequest]:
        """List all active (non-expired) exceptions, optionally filtered by data class.

        Args:
            data_class: Optional data class filter ("pii", "financial", etc.)

        Returns:
            List of active ExceptionRequest
        """
        active = []
        now = datetime.utcnow()

        for exc in self.exceptions.values():
            if self._is_expired(exc):
                continue
            if data_class and exc.data_class != data_class:
                continue
            active.append(exc)

        return active

    def revoke_exception(self, exception_id: str, reason: Optional[str] = None) -> None:
        """Manually revoke exception before TTL expires.

        Args:
            exception_id: Exception identifier
            reason: Optional revocation reason

        Raises:
            ValueError: Exception not found
            RuntimeError: Audit write failed (audit-first fail-closed)
        """
        exc = self.exceptions.get(exception_id)
        if not exc:
            raise ValueError(f"Exception {exception_id} not found")

        # Emit audit event
        revoke_event = LearningEvent.create(
            event_type=EventType.PREFERENCE,
            skill_id=self.skill_id,
            tenant_id=self.tenant_id,
            signal={
                "action": "exception_revoked",
                "exception_id": exception_id,
                "revocation_reason": reason or "operator_request",
            },
            skill_version=self.skill_version,
            lom="flow_guard.exception_system:revoke_exception:L216",
        )

        try:
            self.event_store.write_event(revoke_event)
        except (RuntimeError, IOError) as e:
            logger.error(f"Failed to write revoke event: {e}")
            raise RuntimeError(f"Revocation rejected by audit chain: {e}") from e

        # Remove from cache
        del self.exceptions[exception_id]

        logger.info(
            f"Exception revoked: id={exception_id}, reason={reason or 'unspecified'}"
        )

    def reap_expired_exceptions(self) -> int:
        """Remove expired exceptions (TTL cleanup, called periodically).

        Returns:
            Count of reaped exceptions

        Raises:
            RuntimeError: Audit write failed (audit-first fail-closed)
        """
        reaped = 0
        expired_ids = []

        for exc_id, exc in list(self.exceptions.items()):
            if self._is_expired(exc):
                expired_ids.append(exc_id)

        # Emit audit events for each reaped exception
        for exc_id in expired_ids:
            exc = self.exceptions[exc_id]

            reap_event = LearningEvent.create(
                event_type=EventType.PREFERENCE,
                skill_id=self.skill_id,
                tenant_id=self.tenant_id,
                signal={
                    "action": "exception_expired",
                    "exception_id": exc_id,
                    "expired_at": exc.expires_at,
                },
                skill_version=self.skill_version,
                lom="flow_guard.exception_system:reap_expired_exceptions:L262",
            )

            try:
                self.event_store.write_event(reap_event)
            except (RuntimeError, IOError) as e:
                logger.error(f"Failed to write expiry event for {exc_id}: {e}")
                raise RuntimeError(f"Expiry audit failed: {e}") from e

            del self.exceptions[exc_id]
            reaped += 1

        if reaped > 0:
            logger.info(f"Reaped {reaped} expired exceptions")

        return reaped

    def record_exception_usage(
        self,
        exception_id: str,
        flow_id: str,
        decision_before: str,
        decision_after: str,
    ) -> None:
        """Record that an exception was used to override a flow decision.

        Called by Flow Guard classifier when exception applies.

        Args:
            exception_id: Exception that was applied
            flow_id: Flow that was overridden
            decision_before: Original policy decision
            decision_after: Overridden decision

        Raises:
            RuntimeError: Audit write failed (audit-first fail-closed)
        """
        usage_event = LearningEvent.create(
            event_type=EventType.PREFERENCE,
            skill_id=self.skill_id,
            tenant_id=self.tenant_id,
            signal={
                "action": "exception_applied",
                "exception_id": exception_id,
                "flow_id": flow_id,
                "decision_before": decision_before,
                "decision_after": decision_after,
            },
            skill_version=self.skill_version,
            lom="flow_guard.exception_system:record_exception_usage:L303",
        )

        try:
            self.event_store.write_event(usage_event)
        except (RuntimeError, IOError) as e:
            logger.error(f"Failed to write exception usage event: {e}")
            raise RuntimeError(f"Exception usage audit failed: {e}") from e

        logger.info(
            f"Exception applied: id={exception_id}, flow={flow_id}, "
            f"decision={decision_before}→{decision_after}"
        )

    def _is_expired(self, exc: ExceptionRequest) -> bool:
        """Check if exception TTL has elapsed.

        Args:
            exc: ExceptionRequest to check

        Returns:
            True if expired, False if still active
        """
        expires = datetime.fromisoformat(exc.expires_at.replace("Z", "+00:00"))
        now = datetime.utcnow().replace(tzinfo=None)
        return now > expires.replace(tzinfo=None)

    def _persist_exception(self, exc: ExceptionRequest) -> None:
        """Persist exception to disk (JSONL append).

        Args:
            exc: ExceptionRequest to persist

        Raises:
            IOError: File write failed
        """
        data = {
            "exception_id": exc.exception_id,
            "flow_id": exc.flow_id,
            "data_class": exc.data_class,
            "engine": exc.engine,
            "destination": exc.destination,
            "policy_decision": exc.policy_decision,
            "created_by": exc.created_by,
            "ttl_hours": exc.ttl_hours,
            "tenant_id": exc.tenant_id,
            "timestamp": exc.timestamp,
            "expires_at": exc.expires_at,
            # NOTE: reason is NOT persisted (PII protection)
        }

        try:
            with open(self.exceptions_file, "a") as f:
                f.write(json.dumps(data) + "\n")
        except IOError as e:
            logger.error(f"Failed to persist exception to {self.exceptions_file}: {e}")
            raise

    def _load_exceptions(self) -> Dict[str, ExceptionRequest]:
        """Load all exceptions from disk into memory cache.

        Returns:
            Dict mapping exception_id to ExceptionRequest
        """
        exceptions = {}

        if not self.exceptions_file.exists():
            return exceptions

        try:
            with open(self.exceptions_file, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    exc = ExceptionRequest(
                        exception_id=data["exception_id"],
                        flow_id=data["flow_id"],
                        data_class=data["data_class"],
                        engine=data["engine"],
                        destination=data["destination"],
                        policy_decision=data["policy_decision"],
                        created_by=data["created_by"],
                        ttl_hours=data["ttl_hours"],
                        tenant_id=data["tenant_id"],
                        timestamp=data["timestamp"],
                        expires_at=data["expires_at"],
                        reason="",  # Not persisted
                    )
                    exceptions[exc.exception_id] = exc
        except (IOError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to load exceptions from {self.exceptions_file}: {e}")

        logger.info(f"Loaded {len(exceptions)} exceptions from disk")
        return exceptions
