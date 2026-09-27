"""
Canary Validation Engine — Automated Decision Making + Audit Logging

Implements weekly validation gates, decision automation, and skill feedback integration.
All decisions are audit-logged (hash-chained) and attributed to OS-Skills.

Related: ADR-0532 (OS-Skills), ADR-0314 (Learning Infrastructure), ADR-0722 (Observability)
Compliance: GDPR Art. 30/32 (audit trail), EU AI Act Art. 50 (transparency + LoM binding)
"""

import json
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class GateStatus(Enum):
    """Validation gate status"""
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIPPED = "skipped"


class AuditEventType(Enum):
    """Audit event types for canary operations"""
    VALIDATION_RUN = "canary_validation_run"
    GATE_EVALUATED = "canary_gate_evaluated"
    DECISION_MADE = "canary_decision_made"
    ESCALATION_ALLOWED = "canary_escalation_allowed"
    ESCALATION_BLOCKED = "canary_escalation_blocked"
    ROLLBACK_TRIGGERED = "canary_rollback_triggered"
    SKILL_FEEDBACK = "skill_feedback"


@dataclass
class GateEvaluation:
    """Single gate evaluation result"""
    gate_name: str
    metric_type: str
    actual_value: float
    threshold_pass: float
    threshold_warn: float
    status: GateStatus
    passed: bool
    details: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = d["status"].value if isinstance(d["status"], GateStatus) else d["status"]
        return d


@dataclass
class ValidationRunResult:
    """Complete validation run result"""
    timestamp: str
    traffic_percent: int
    gates_evaluated: List[GateEvaluation] = field(default_factory=list)
    all_gates_pass: bool = False
    overall_status: GateStatus = GateStatus.FAIL
    decision_recommendation: str = "HOLD"
    metrics_snapshot: Optional[Dict[str, Any]] = None
    errors: List[str] = field(default_factory=list)
    audit_event_hash: Optional[str] = None
    tenant_id: str = "_default"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["overall_status"] = d["overall_status"].value if isinstance(d["overall_status"], GateStatus) else d["overall_status"]
        d["gates_evaluated"] = [g.to_dict() for g in self.gates_evaluated]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "ValidationRunResult":
        data["overall_status"] = GateStatus(data["overall_status"])
        data["gates_evaluated"] = [GateEvaluation(**g) for g in data.get("gates_evaluated", [])]
        return cls(**data)


@dataclass
class AuditEvent:
    """Immutable audit event for canary operations"""
    event_type: AuditEventType
    timestamp: str
    tenant_id: str
    skill_id: str
    description: str
    payload: Dict[str, Any] = field(default_factory=dict)
    prev_hash: Optional[str] = None
    hash: Optional[str] = None
    lom: str = ""  # Line of Moral Responsibility (code location)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["event_type"] = d["event_type"].value if isinstance(d["event_type"], AuditEventType) else d["event_type"]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "AuditEvent":
        data["event_type"] = AuditEventType(data["event_type"])
        return cls(**data)


class AuditBackend(ABC):
    """Abstract audit backend for pluggable storage"""

    @abstractmethod
    def write_event(self, event: AuditEvent) -> Tuple[bool, str]:
        """
        Write audit event (hash-chained, immutable, append-only).
        Returns: (success, event_hash)
        """
        pass

    @abstractmethod
    def get_last_hash(self) -> Optional[str]:
        """Get hash of last audit event (for chaining)"""
        pass


class LocalFileAuditBackend(AuditBackend):
    """Local file-based audit backend (for testing + standalone)"""

    def __init__(self, audit_file: str = "~/.corvin/audit_canary.jsonl"):
        self.audit_file = Path(audit_file).expanduser()
        self.audit_file.parent.mkdir(parents=True, exist_ok=True)
        self.last_hash: Optional[str] = None
        self._load_last_hash()

    def _load_last_hash(self) -> None:
        """Load hash of last event from file"""
        if not self.audit_file.exists():
            self.last_hash = None
            return

        try:
            with open(self.audit_file, "r") as f:
                lines = f.readlines()
            if lines:
                last_line = lines[-1].strip()
                if last_line:
                    event = json.loads(last_line)
                    self.last_hash = event.get("hash")
        except Exception as e:
            logger.warning(f"Cannot load last hash: {e}")
            self.last_hash = None

    def write_event(self, event: AuditEvent) -> Tuple[bool, str]:
        """Append event to audit file (fail-closed if unsuccessful)"""
        try:
            # Fail-closed: chain must be intact before writing
            event.prev_hash = self.last_hash

            # Calculate event hash (deterministic)
            event_json = json.dumps(event.to_dict(), sort_keys=True, separators=(",", ":"))
            import hashlib
            event.hash = hashlib.sha256(event_json.encode()).hexdigest()

            # Append to file (immutable)
            with open(self.audit_file, "a") as f:
                f.write(event_json + "\n")

            self.last_hash = event.hash
            logger.debug(f"Audit event written: {event.event_type.value} (hash={event.hash[:8]}...)")
            return True, event.hash

        except Exception as e:
            logger.error(f"❌ FAIL-CLOSED: Cannot write audit event: {e}")
            return False, ""

    def get_last_hash(self) -> Optional[str]:
        """Get hash of last audit event (for chaining)"""
        return self.last_hash


class CanaryValidationEngine:
    """
    Automated validation engine for canary escalation decisions.

    Responsibilities:
    - Weekly validation gate evaluation
    - Automated decision making (escalate/hold/rollback)
    - Audit logging (hash-chained, immutable)
    - Skill feedback integration (ADR-0314)
    """

    def __init__(self, audit_backend: Optional[AuditBackend] = None):
        """Initialize validation engine with optional audit backend"""
        self.audit_backend = audit_backend or LocalFileAuditBackend()
        self.validation_history: List[ValidationRunResult] = []
        self.decision_history: List[Dict[str, Any]] = []

    def run_validation(
        self,
        traffic_percent: int,
        gates: List[Dict[str, Any]],  # [{"gate_name": "latency_ok", "metric_type": "latency", ...}]
        metrics: Dict[str, float],  # {"p99_latency_ms": 250.0, "error_rate_pct": 0.05, ...}
        tenant_id: str = "_default",
    ) -> ValidationRunResult:
        """
        Run complete validation against gates.

        Args:
            traffic_percent: Current canary traffic percentage
            gates: List of gate definitions with thresholds
            metrics: Current metrics snapshot
            tenant_id: Tenant scope

        Returns: ValidationRunResult with gate evaluations

        Emits: VALIDATION_RUN audit event + per-gate GATE_EVALUATED events
        """
        logger.info(f"Running validation at {traffic_percent}% traffic...")

        result = ValidationRunResult(
            timestamp=datetime.utcnow().isoformat() + "Z",
            traffic_percent=traffic_percent,
            metrics_snapshot=metrics,
            tenant_id=tenant_id,
        )

        # Evaluate each gate
        gate_statuses = []
        for gate_def in gates:
            evaluation = self._evaluate_gate(gate_def, metrics)
            result.gates_evaluated.append(evaluation)
            gate_statuses.append(evaluation.status)

            # Emit per-gate audit event
            self._emit_audit_event(
                event_type=AuditEventType.GATE_EVALUATED,
                description=f"Gate '{evaluation.gate_name}' evaluated",
                payload=evaluation.to_dict(),
                tenant_id=tenant_id,
            )

        # Overall status: PASS if all gates pass, WARN if any warn, FAIL if any fail
        if all(s == GateStatus.PASS for s in gate_statuses):
            result.overall_status = GateStatus.PASS
            result.all_gates_pass = True
            result.decision_recommendation = "ESCALATE"
        elif any(s == GateStatus.FAIL for s in gate_statuses):
            result.overall_status = GateStatus.FAIL
            result.all_gates_pass = False
            result.decision_recommendation = "HOLD"
        else:
            result.overall_status = GateStatus.WARN
            result.all_gates_pass = False
            result.decision_recommendation = "HOLD"

        # Emit validation run audit event
        success, hash_val = self._emit_audit_event(
            event_type=AuditEventType.VALIDATION_RUN,
            description=f"Validation run at {traffic_percent}% ({result.overall_status.value})",
            payload=result.to_dict(),
            tenant_id=tenant_id,
        )

        result.audit_event_hash = hash_val if success else None
        self.validation_history.append(result)

        logger.info(f"✅ Validation complete: {result.overall_status.value} "
                   f"({len([g for g in result.gates_evaluated if g.passed])}/{len(gates)} gates pass)")

        return result

    def make_decision(
        self,
        validation_result: ValidationRunResult,
        current_traffic_percent: int,
        skill_id: str = "os.canary_router",
        tenant_id: str = "_default",
    ) -> Tuple[str, str, Dict[str, Any]]:
        """
        Make escalation decision based on validation result.

        Returns: (decision, reason, decision_record)
        Emits: DECISION_MADE audit event
        """
        decision = "HOLD"  # Default safe decision
        reason = ""

        # Check for escalation conditions
        if validation_result.all_gates_pass:
            # All gates pass — safe to escalate
            if current_traffic_percent < 100:
                decision = "ESCALATE"
                reason = "All gates pass. Ready for next level."
            else:
                decision = "ESCALATE_PHASE_2B"
                reason = "All gates pass at 100%. Activating Phase 2b."
        else:
            # Gates failing or warning — hold at current level
            failed_gates = [g.gate_name for g in validation_result.gates_evaluated if g.status == GateStatus.FAIL]
            if failed_gates:
                decision = "HOLD"
                reason = f"Gates failing: {', '.join(failed_gates)}"
            else:
                decision = "HOLD"
                reason = "Gates warning. Wait for stability."

        # Check for rollback conditions (fail-closed)
        error_rate = validation_result.metrics_snapshot.get("error_rate_pct", 0)
        if error_rate > 1.0:
            decision = "ROLLBACK"
            reason = f"Error rate {error_rate}% > 1% (ROLLBACK)"

        # Record decision
        decision_record = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "decision": decision,
            "reason": reason,
            "traffic_percent": current_traffic_percent,
            "validation_result_hash": validation_result.audit_event_hash,
            "skill_id": skill_id,
            "tenant_id": tenant_id,
        }

        self.decision_history.append(decision_record)

        # Emit decision audit event
        self._emit_audit_event(
            event_type=AuditEventType.DECISION_MADE,
            description=f"Decision: {decision}",
            payload=decision_record,
            skill_id=skill_id,
            tenant_id=tenant_id,
        )

        logger.info(f"🎯 Decision: {decision} — {reason}")

        return decision, reason, decision_record

    def record_skill_feedback(
        self,
        decision: str,
        actual_outcome: str,  # "success", "degradation", "rollback"
        confidence_before: float,  # 0.0 to 1.0
        confidence_after: float,
        feedback_notes: str = "",
        skill_id: str = "os.canary_router",
        tenant_id: str = "_default",
    ) -> Tuple[bool, str]:
        """
        Record skill feedback for learning loop (ADR-0314).

        Feedback is used by SkillForge optimizer to tune thresholds.

        Returns: (success, event_hash)
        Emits: SKILL_FEEDBACK audit event
        """
        feedback_record = {
            "decision": decision,
            "actual_outcome": actual_outcome,
            "confidence_before": confidence_before,
            "confidence_after": confidence_after,
            "feedback_notes": feedback_notes,
        }

        success, hash_val = self._emit_audit_event(
            event_type=AuditEventType.SKILL_FEEDBACK,
            description=f"Feedback on '{decision}': {actual_outcome}",
            payload=feedback_record,
            skill_id=skill_id,
            tenant_id=tenant_id,
        )

        if success:
            logger.info(f"✅ Skill feedback recorded: {decision} → {actual_outcome} "
                       f"(confidence {confidence_before:.1%} → {confidence_after:.1%})")
        else:
            logger.error(f"❌ Cannot record skill feedback (audit backend failed)")

        return success, hash_val

    def get_validation_history(self, limit: int = 20) -> List[ValidationRunResult]:
        """Get recent validation run results"""
        return self.validation_history[-limit:]

    def get_decision_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent decisions"""
        return self.decision_history[-limit:]

    def export_audit_trail(self) -> Dict[str, Any]:
        """Export complete audit trail (for compliance + debugging)"""
        return {
            "export_timestamp": datetime.utcnow().isoformat() + "Z",
            "validation_count": len(self.validation_history),
            "decision_count": len(self.decision_history),
            "validations": [v.to_dict() for v in self.validation_history[-100:]],  # Last 100
            "decisions": self.decision_history[-100:],
        }

    # ===== Private Helpers =====

    def _evaluate_gate(self, gate_def: Dict[str, Any], metrics: Dict[str, float]) -> GateEvaluation:
        """Evaluate a single gate against metrics"""
        gate_name = gate_def["gate_name"]
        metric_type = gate_def["metric_type"]
        threshold_pass = gate_def["threshold_pass"]
        threshold_warn = gate_def["threshold_warn"]
        operator = gate_def.get("operator", "lt")

        actual_value = metrics.get(metric_type, 0.0)

        # Compare actual to thresholds
        if operator == "lt":
            passed = actual_value < threshold_pass
            warned = actual_value < threshold_warn
        elif operator == "gt":
            passed = actual_value > threshold_pass
            warned = actual_value > threshold_warn
        else:
            raise ValueError(f"Unknown operator: {operator}")

        if passed:
            status = GateStatus.PASS
        elif warned:
            status = GateStatus.WARN
        else:
            status = GateStatus.FAIL

        evaluation = GateEvaluation(
            gate_name=gate_name,
            metric_type=metric_type,
            actual_value=actual_value,
            threshold_pass=threshold_pass,
            threshold_warn=threshold_warn,
            status=status,
            passed=passed,
            details=f"{metric_type}={actual_value} vs {operator} {threshold_pass}",
        )

        return evaluation

    def _emit_audit_event(
        self,
        event_type: AuditEventType,
        description: str,
        payload: Dict[str, Any] = None,
        skill_id: str = "os.canary_router",
        tenant_id: str = "_default",
    ) -> Tuple[bool, str]:
        """Emit audit event (hash-chained, immutable)"""
        event = AuditEvent(
            event_type=event_type,
            timestamp=datetime.utcnow().isoformat() + "Z",
            tenant_id=tenant_id,
            skill_id=skill_id,
            description=description,
            payload=payload or {},
            lom=f"canary_validation.py::{event_type.value}",
        )

        return self.audit_backend.write_event(event)


def create_validation_engine(
    audit_backend: Optional[AuditBackend] = None,
) -> CanaryValidationEngine:
    """Factory function to create a validation engine"""
    return CanaryValidationEngine(audit_backend)
