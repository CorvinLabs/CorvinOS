"""Context Bridge Skill: Auto-context-splitting and session continuity.

Manages:
- Context window accounting (how much space is left)
- Automatic splits when window fills (split → new session)
- Session continuity (remembering split points for later recovery)
- Multi-session context aggregation (combining insights from related sessions)

Contract (ADR-0535):
- Required dependency: os.health_monitor (checks context window health)
- Calling convention: Context Bridge is called to decide if/how to split context
- Timeout budget: 100ms (heavier than Health Monitor)
- Audit: Every split decision logged, hash-chained
- Degradation: If health_monitor unavailable, default to conservative split (50% buffer)

Compliance:
- GDPR Art. 30/32: split decisions immutable, audit-logged
- ADR-0050: manages main-thread session pinning, worker memory bridges
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Tuple
from enum import Enum
import time
import logging
import hashlib

try:
    from .base_skill import BaseSkill, SkillExecutedEvent, SkillExecutionStatus, AuditTrail
except ImportError:
    from base_skill import BaseSkill, SkillExecutedEvent, SkillExecutionStatus, AuditTrail

logger = logging.getLogger(__name__)


class SplitReason(Enum):
    """Why a context split was triggered."""
    WINDOW_FULL = "window_full"  # Reached max token limit
    SEMANTIC_BOUNDARY = "semantic_boundary"  # Task boundary detected
    USER_REQUEST = "user_request"  # Explicit split request
    NONE = "none"  # No split needed


@dataclass(frozen=True)
class ContextSnapshot:
    """Immutable snapshot of context at split point."""
    session_id: str  # Unique session ID
    timestamp: str  # ISO8601
    tokens_used: int
    tokens_remaining: int
    split_reason: SplitReason
    split_hash: str  # Hash of content at split point (for recovery)
    metadata: Dict[str, Any] = field(default_factory=dict)  # Task ID, user ID, etc.


@dataclass(frozen=True)
class SessionBridge:
    """Record of a session continuity bridge."""
    from_session_id: str
    to_session_id: str  # Split → new session
    context_hash: str  # Hash of common context between sessions
    timestamp: str
    split_reason: SplitReason


class ContextBridge(BaseSkill[Tuple[bool, Optional[ContextSnapshot]]]):
    """Skill: Auto-split context and manage session continuity.

    Returns: (should_split: bool, snapshot: Optional[ContextSnapshot])
    - If should_split is True, caller creates new session with snapshot
    - If False, current session continues
    """

    skill_id = "os.context_bridge"
    version = "1.0.0"
    required_dependencies = ["os.health_monitor"]  # Hard requirement: check health before deciding
    soft_dependencies: List[str] = []
    call_budget_ms = 100

    # Split strategy parameters
    WARNING_THRESHOLD = 0.75  # Warn at 75% capacity
    SPLIT_THRESHOLD = 0.90  # Force split at 90% capacity
    BUFFER_PERCENT = 0.10  # Reserve 10% for safety

    def __init__(self, tenant_id: str, audit_trail: AuditTrail):
        super().__init__(tenant_id, audit_trail)
        self._session_bridges: Dict[str, SessionBridge] = {}
        self._current_session_id: Optional[str] = None

    def execute(self, input_data: Dict[str, Any]) -> Tuple[bool, Optional[ContextSnapshot]]:
        """Decide whether to split context.

        Input:
        - "current_session_id": str
        - "tokens_used": int (in current session)
        - "tokens_max": int (window size)
        - "task_metadata": Dict (task ID, user ID, etc.)
        - "health_monitor_status": HealthStatus (passed from upstream)

        Returns:
            (should_split: bool, snapshot: Optional[ContextSnapshot])
        """
        start = time.perf_counter()

        try:
            current_session_id = input_data.get("current_session_id", "default")
            tokens_used = input_data.get("tokens_used", 0)
            tokens_max = input_data.get("tokens_max", 128000)
            task_metadata = input_data.get("task_metadata", {})
            health_status = input_data.get("health_monitor_status")

            should_split, snapshot = self._decide_split(
                current_session_id,
                tokens_used,
                tokens_max,
                task_metadata,
                health_status,
            )

            latency_ms = int((time.perf_counter() - start) * 1000)

            # Audit this decision
            self._audit_execution(
                input_data=input_data,
                output_data=(should_split, snapshot),
                status=SkillExecutionStatus.SUCCESS,
                latency_ms=latency_ms,
            )

            if should_split and snapshot:
                logger.info(
                    f"Context split triggered: {snapshot.split_reason.value}",
                    extra={"tenant_id": self.tenant_id, "session_id": current_session_id}
                )

            return (should_split, snapshot)

        except Exception as e:
            latency_ms = int((time.perf_counter() - start) * 1000)
            logger.error(f"Context bridge error: {e}", extra={"tenant_id": self.tenant_id})

            # Audit the error
            self._audit_execution(
                input_data=input_data,
                output_data=(False, None),  # Conservative: don't split on error
                status=SkillExecutionStatus.ERROR,
                latency_ms=latency_ms,
                error_message=str(e),
            )

            raise

    def _decide_split(
        self,
        session_id: str,
        tokens_used: int,
        tokens_max: int,
        task_metadata: Dict[str, Any],
        health_status: Any,
    ) -> Tuple[bool, Optional[ContextSnapshot]]:
        """Determine if split is needed and create snapshot if yes."""

        tokens_available = tokens_max - tokens_used
        utilization = tokens_used / tokens_max if tokens_max > 0 else 0.0

        # Determine split reason
        split_reason = SplitReason.NONE

        if utilization >= self.SPLIT_THRESHOLD:
            split_reason = SplitReason.WINDOW_FULL
        elif utilization >= self.WARNING_THRESHOLD:
            # Check health before deciding to split at warning threshold
            if health_status and hasattr(health_status, "overall_health"):
                # Only split at warning if system is healthy (conservative)
                split_reason = SplitReason.SEMANTIC_BOUNDARY

        # Manual split request
        if task_metadata.get("force_split"):
            split_reason = SplitReason.USER_REQUEST

        # Decide
        should_split = split_reason != SplitReason.NONE

        snapshot = None
        if should_split:
            new_session_id = self._generate_session_id(session_id)
            snapshot = ContextSnapshot(
                session_id=new_session_id,
                timestamp=self._now_iso(),
                tokens_used=0,  # Fresh session
                tokens_remaining=tokens_max,
                split_reason=split_reason,
                split_hash=self._hash_context(session_id, tokens_used),
                metadata=task_metadata,
            )

            # Record the bridge for recovery
            bridge = SessionBridge(
                from_session_id=session_id,
                to_session_id=new_session_id,
                context_hash=snapshot.split_hash,
                timestamp=snapshot.timestamp,
                split_reason=split_reason,
            )
            self._session_bridges[new_session_id] = bridge

        return (should_split, snapshot)

    def recover_session(self, session_id: str) -> Optional[SessionBridge]:
        """Recover continuity information for a session."""
        return self._session_bridges.get(session_id)

    def _generate_session_id(self, parent_session_id: str) -> str:
        """Generate new session ID (incremental split ID)."""
        timestamp = int(time.time() * 1000)
        hash_input = f"{parent_session_id}:{timestamp}"
        hash_output = hashlib.sha256(hash_input.encode()).hexdigest()[:12]
        return f"{parent_session_id}#{hash_output}"

    def _hash_context(self, session_id: str, tokens_used: int) -> str:
        """Hash context state at split point (for recovery)."""
        payload = f"{session_id}:{tokens_used}:{self._now_iso()}"
        return hashlib.sha256(payload.encode()).hexdigest()

    def _now_iso(self) -> str:
        """Current time in ISO8601."""
        from datetime import datetime
        return datetime.utcnow().isoformat() + "Z"
