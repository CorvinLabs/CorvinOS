"""Base classes and datastructures for Phase 2.2 Monitor Subsystems.

Provides:
- MonitorBase: Abstract base class for all monitors
- MonitorAlert: Alert dataclass
- AlertType: Enum of 5 monitor alert types
- MonitorConfig: Configuration for monitors
- MonitorState: Mutable state for monitors

ADR-0407: Session Manager Phase 2.2
Integrates with: SubsystemHub (ADR-0347), EventBus (ADR-0348)
GDPR Art. 5/30/32: All alerts are audit-logged
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class AlertType(str, Enum):
    """5 Monitor Alert Types (Phase 2.2)."""

    GOAL_DRIFT_DETECTED = "goal_drift_detected"
    ENTROPY_DETECTED = "entropy_detected"
    ASSUMPTION_UNVALIDATED = "assumption_unvalidated"
    LOCAL_OPTIMUM_SUSPECTED = "local_optimum_suspected"
    COGNITIVE_OVERLOAD = "cognitive_overload"


@dataclass(frozen=True)
class MonitorAlert:
    """Immutable alert from a monitor.

    Audit-logged per GDPR Art. 30, 32.
    """

    alert_type: AlertType
    session_id: str
    task_id: str
    tenant_id: str
    severity: str  # "info", "warning", "critical"
    reason: str
    timestamp_utc: datetime = field(default_factory=datetime.utcnow)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_audit_event(self) -> Dict[str, Any]:
        """Convert to audit.jsonl format."""
        return {
            "event_type": f"session.monitor.{self.alert_type.value}",
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "severity": self.severity,
            "timestamp": self.timestamp_utc.isoformat() + "Z",
            "event_id": self.event_id,
            "reason": self.reason,
            "metadata": self.metadata,
        }


@dataclass
class MonitorConfig:
    """Configuration for a monitor."""

    enabled: bool = True
    check_interval_seconds: int = 5
    alert_cooldown_seconds: int = 60
    max_alerts_per_session: int = 100
    tenant_id: str = "default"


@dataclass
class MonitorState:
    """Mutable state for a monitor (per session)."""

    session_id: str
    task_id: str
    tenant_id: str
    last_alert_timestamp: Optional[datetime] = None
    alert_count: int = 0
    alerts: List[MonitorAlert] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class MonitorBase(ABC):
    """Abstract base class for Phase 2.2 monitors.

    All monitors follow the same lifecycle:
    1. startup(hub) — register with SubsystemHub
    2. check(state) — evaluate monitor condition, return Optional[MonitorAlert]
    3. shutdown() — cleanup

    Monitors are non-blocking and fail-closed (errors log but don't crash).
    """

    def __init__(self, name: str, config: Optional[MonitorConfig] = None):
        """Initialize monitor.

        Args:
            name: Monitor name (e.g., "goal_alignment_monitor")
            config: Optional configuration
        """
        self.name = name
        self.version = "0.1.0"
        self.config = config or MonitorConfig()
        self.hub: Optional[Any] = None
        self.session_states: Dict[str, MonitorState] = {}

    def startup(self, hub: Any) -> None:
        """Register with SubsystemHub.

        Args:
            hub: SubsystemHub instance
        """
        self.hub = hub
        logger.info(f"Starting {self.name} v{self.version}")

    def shutdown(self) -> None:
        """Clean up on shutdown."""
        logger.info(f"Shutting down {self.name}")
        self.session_states.clear()

    @abstractmethod
    def check(self, state: MonitorState) -> Optional[MonitorAlert]:
        """Check monitor condition and return alert if triggered.

        Args:
            state: MonitorState for the session

        Returns:
            MonitorAlert if triggered, None otherwise
        """
        pass

    def evaluate_session(self, state: MonitorState) -> Optional[MonitorAlert]:
        """Evaluate monitor for a session.

        Handles cooldown, rate limiting, and audit logging.

        Args:
            state: MonitorState for the session

        Returns:
            MonitorAlert if triggered and not rate-limited, None otherwise
        """
        if not self.config.enabled:
            return None

        # Check cooldown
        if state.last_alert_timestamp is not None:
            elapsed = (datetime.utcnow() - state.last_alert_timestamp).total_seconds()
            if elapsed < self.config.alert_cooldown_seconds:
                logger.debug(
                    f"{self.name}: Alert cooldown active for {state.session_id} "
                    f"({elapsed:.1f}s / {self.config.alert_cooldown_seconds}s)"
                )
                return None

        # Check rate limit
        if state.alert_count >= self.config.max_alerts_per_session:
            logger.warning(
                f"{self.name}: Alert rate limit hit for {state.session_id} "
                f"({state.alert_count}/{self.config.max_alerts_per_session})"
            )
            return None

        # Run monitor check
        try:
            alert = self.check(state)
        except Exception as e:
            logger.error(f"{self.name}: Error during check: {e}", exc_info=True)
            return None

        if alert is None:
            return None

        # Update state
        state.last_alert_timestamp = alert.timestamp_utc
        state.alert_count += 1
        state.alerts.append(alert)

        # Audit log
        self._audit_log_alert(alert)

        # Publish event
        if self.hub:
            try:
                self.hub.publish_event(
                    f"monitor.{alert.alert_type.value}",
                    alert.to_audit_event(),
                )
            except Exception as e:
                logger.error(f"{self.name}: Error publishing event: {e}")

        return alert

    def _audit_log_alert(self, alert: MonitorAlert) -> None:
        """Log alert to audit trail (GDPR Art. 30, 32).

        Args:
            alert: MonitorAlert to log
        """
        logger.info(f"AUDIT: Monitor alert {alert.event_id} {alert.alert_type.value}")

    def create_or_get_state(
        self, session_id: str, task_id: str, tenant_id: str
    ) -> MonitorState:
        """Create or get MonitorState for a session.

        Args:
            session_id: Session ID
            task_id: Task ID
            tenant_id: Tenant ID

        Returns:
            MonitorState for the session
        """
        if session_id not in self.session_states:
            self.session_states[session_id] = MonitorState(
                session_id=session_id,
                task_id=task_id,
                tenant_id=tenant_id,
            )
        return self.session_states[session_id]
