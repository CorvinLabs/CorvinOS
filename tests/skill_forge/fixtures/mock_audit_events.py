"""
Mock Audit Events for Skill Forge E2E Testing

Provides:
- Loss signal creation
- Audit event emission
- Event schema validation
"""

from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any
from uuid import uuid4
import hashlib
import json


class AuditEventType(str, Enum):
    """Canonical audit event types for Skill Forge."""

    # Skill lifecycle
    SKILL_LOADED = "skill_loaded"
    SKILL_LOSS_DETECTED = "skill_loss_detected"
    SKILL_CODE_GENERATED = "skill_code_generated"
    SKILL_EXECUTED = "skill_executed"
    SKILL_VALIDATION_FAILED = "skill_validation_failed"
    SKILL_CANARY_DEPLOYED = "skill_canary_deployed"
    SKILL_CANARY_VERDICT = "skill_canary_verdict"
    SKILL_CANARY_FAILED = "skill_canary_failed"
    SKILL_ROLLED_BACK = "skill_rolled_back"
    SKILL_ROLLOUT_COMPLETED = "skill_rollout_completed"

    # Validation
    VALIDATION_LAYER_1_PASSED = "validation_layer_1_passed"
    VALIDATION_LAYER_2_PASSED = "validation_layer_2_passed"


@dataclass
class LossSignal:
    """Loss signal indicating skill degradation."""
    skill_id: str
    confidence: float  # Current confidence score (0.0-1.0)
    confidence_target: float  # Target confidence (0.75+)
    signal_type: str = "confidence_drift"  # What triggered the signal
    timestamp_utc: str = None
    signal_id: str = None

    def __post_init__(self):
        if self.timestamp_utc is None:
            self.timestamp_utc = datetime.utcnow().isoformat() + "Z"
        if self.signal_id is None:
            self.signal_id = str(uuid4())

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict."""
        return asdict(self)


@dataclass
class AuditEvent:
    """Audit event with hash-chaining (ADR-0232)."""

    event_type: str
    tenant_id: str
    payload: Dict[str, Any]
    timestamp_utc: str = None
    event_id: str = None
    hash: str = None
    prev_hash: Optional[str] = None
    lom: Optional[str] = None  # Line of Moral Responsibility

    def __post_init__(self):
        if self.timestamp_utc is None:
            self.timestamp_utc = datetime.utcnow().isoformat() + "Z"
        if self.event_id is None:
            self.event_id = str(uuid4())
        if self.hash is None:
            self.hash = self._compute_hash()

    def _compute_hash(self) -> str:
        """Compute SHA256 hash of event + previous hash."""
        prev = self.prev_hash or "0"
        data = json.dumps({
            "event_type": self.event_type,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp_utc,
            "payload": self.payload,
            "prev_hash": prev,
        }, sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict."""
        return asdict(self)


class MockAuditEventEmitter:
    """Mock audit event emitter for testing."""

    def __init__(self, tenant_id: str = "_test"):
        self.tenant_id = tenant_id
        self.events: List[AuditEvent] = []

    def emit(
        self,
        event_type: str,
        payload: Dict[str, Any],
        lom: Optional[str] = None,
    ) -> AuditEvent:
        """
        Emit audit event with hash-chaining.

        Args:
            event_type: Type of event (use AuditEventType enum)
            payload: Event payload (dict)
            lom: Line of Moral Responsibility (optional)

        Returns:
            Created AuditEvent
        """
        prev_hash = self.events[-1].hash if self.events else None

        event = AuditEvent(
            event_type=event_type,
            tenant_id=self.tenant_id,
            payload=payload,
            prev_hash=prev_hash,
            lom=lom,
        )

        self.events.append(event)
        return event

    def emit_skill_loaded(self, skill_id: str, version: str) -> AuditEvent:
        """Emit skill_loaded event."""
        return self.emit(
            AuditEventType.SKILL_LOADED,
            {"skill_id": skill_id, "version": version},
            lom="skill_forge.load_skill",
        )

    def emit_loss_detected(
        self, skill_id: str, confidence: float
    ) -> AuditEvent:
        """Emit skill_loss_detected event."""
        return self.emit(
            AuditEventType.SKILL_LOSS_DETECTED,
            {"skill_id": skill_id, "confidence": confidence},
            lom="loss_signals.detect_loss",
        )

    def emit_code_generated(
        self, skill_id: str, old_version: str, new_version: str
    ) -> AuditEvent:
        """Emit skill_code_generated event."""
        return self.emit(
            AuditEventType.SKILL_CODE_GENERATED,
            {
                "skill_id": skill_id,
                "old_version": old_version,
                "new_version": new_version,
            },
            lom="autonomous_skill_forge.generate",
        )

    def emit_validation_passed(
        self, skill_id: str, version: str, layer: str = "1"
    ) -> AuditEvent:
        """Emit validation_layer_N_passed event."""
        event_type = f"validation_layer_{layer}_passed"
        return self.emit(
            event_type,
            {"skill_id": skill_id, "version": version, "layer": layer},
            lom="skill_validator.validate",
        )

    def emit_skill_executed(
        self, skill_id: str, version: str, latency_ms: float
    ) -> AuditEvent:
        """Emit skill_executed event."""
        return self.emit(
            AuditEventType.SKILL_EXECUTED,
            {
                "skill_id": skill_id,
                "version": version,
                "latency_ms": latency_ms,
            },
            lom="skill_executor.run",
        )

    def emit_canary_deployed(self, canary_id: str) -> AuditEvent:
        """Emit skill_canary_deployed event."""
        return self.emit(
            AuditEventType.SKILL_CANARY_DEPLOYED,
            {"canary_id": canary_id},
            lom="canary_deployer.deploy",
        )

    def emit_canary_verdict(
        self, canary_id: str, error_rate: float, success: bool
    ) -> AuditEvent:
        """Emit skill_canary_verdict event."""
        return self.emit(
            AuditEventType.SKILL_CANARY_VERDICT,
            {
                "canary_id": canary_id,
                "error_rate": error_rate,
                "success": success,
            },
            lom="canary_monitor.verdict",
        )

    def emit_rollout_completed(self, skill_id: str, version: str) -> AuditEvent:
        """Emit skill_rollout_completed event."""
        return self.emit(
            AuditEventType.SKILL_ROLLOUT_COMPLETED,
            {"skill_id": skill_id, "version": version},
            lom="rollout_deployer.deploy",
        )

    def verify_chain_integrity(self) -> bool:
        """Verify hash-chain integrity."""
        for i in range(1, len(self.events)):
            event = self.events[i]
            prev_event = self.events[i - 1]
            if event.prev_hash != prev_event.hash:
                return False
        return True

    def get_chain_as_dict(self) -> List[Dict[str, Any]]:
        """Get audit chain as list of dicts."""
        return [e.to_dict() for e in self.events]


def create_loss_signal(
    skill_id: str,
    confidence: float = 0.68,
    confidence_target: float = 0.75,
) -> LossSignal:
    """
    Create a loss signal.

    Args:
        skill_id: ID of skill experiencing loss
        confidence: Current confidence (below target triggers signal)
        confidence_target: Target confidence threshold

    Returns:
        LossSignal instance
    """
    return LossSignal(
        skill_id=skill_id,
        confidence=confidence,
        confidence_target=confidence_target,
        signal_type="confidence_drift",
    )


def create_multiple_loss_signals(
    skill_ids: List[str],
    confidence_range: tuple = (0.65, 0.70),
) -> List[LossSignal]:
    """
    Create multiple loss signals for different skills.

    Args:
        skill_ids: List of skill IDs
        confidence_range: (min, max) confidence values

    Returns:
        List of LossSignal instances
    """
    signals = []
    import random
    for skill_id in skill_ids:
        confidence = random.uniform(*confidence_range)
        signals.append(create_loss_signal(skill_id, confidence))
    return signals
