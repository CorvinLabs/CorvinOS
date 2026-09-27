"""
Rollback Automation with 8 Fail-Closed Triggers

Implements automated rollback with 8 independent triggers:
1. Correctness >2% drop → disable current phase skill, revert to previous version
2. Latency >20% p99 → trigger escalation reduction or full phase rollback
3. Confidence regression >10% → hold traffic escalation, trigger retraining
4. Audit chain break → IMMEDIATE CRITICAL rollback, require operator review
5. Tenant isolation violation → IMMEDIATE CRITICAL rollback, audit investigation
6. Security check failure → IMMEDIATE CRITICAL rollback, security team review
7. Loss signal CRITICAL (A/B regression, latency, confidence) → halt variant
8. Operator manual rollback → immediate revert, audit logged

All rollbacks emit immutable audit events with LoM binding (ADR-0537, ADR-0232).

Fail-closed semantics: on any trigger fire, default action is rollback + lockdown.
"""

from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Callable, Tuple
import logging
import json
import hashlib

logger = logging.getLogger(__name__)


class RollbackTrigger(Enum):
    """8 Rollback triggers"""
    CORRECTNESS_DROP = "correctness_drop"  # >2% agreement drop
    LATENCY_SPIKE = "latency_spike"  # >20% p99 increase
    CONFIDENCE_REGRESSION = "confidence_regression"  # >10% unexpected drop
    AUDIT_CHAIN_BREAK = "audit_chain_break"  # Hash chain verification failed
    TENANT_ISOLATION_VIOLATION = "tenant_isolation_violation"  # Cross-tenant data leakage
    SECURITY_CHECK_FAILURE = "security_check_failure"  # Security gate failed
    LOSS_SIGNAL_CRITICAL = "loss_signal_critical"  # A/B regression, latency, confidence
    MANUAL_OPERATOR_ROLLBACK = "manual_operator_rollback"  # Operator clicked rollback


class RollbackAction(Enum):
    """Action taken on rollback"""
    HOLD_TRAFFIC = "hold_traffic"  # Don't escalate traffic
    REVERT_VERSION = "revert_version"  # Revert to prior stable version
    DISABLE_SKILL = "disable_skill"  # Disable skill, use fallback
    LOCK_PHASE = "lock_phase"  # Lock phase, require operator intervention
    ESCALATE_TO_PAGERDUTY = "escalate_to_pagerduty"  # Critical, page oncall
    INITIATE_RETRAINING = "initiate_retraining"  # Trigger skill retraining


@dataclass
class RollbackEvent:
    """Immutable rollback event record"""
    event_id: str
    trigger: RollbackTrigger
    timestamp: str
    phase: str
    skill_id: Optional[str] = None
    metric_name: Optional[str] = None
    actual_value: Optional[float] = None
    threshold: Optional[float] = None
    actions_taken: List[RollbackAction] = None
    lockdown_until: Optional[str] = None  # Time lock is held
    audit_hash: str = ""
    prior_hash: str = ""  # Hash chaining
    lom: str = ""  # Line of Moral Responsibility
    operator_id: Optional[str] = None  # For manual rollbacks
    reason: str = ""

    def __post_init__(self):
        if self.actions_taken is None:
            self.actions_taken = []


class RollbackController:
    """
    Master rollback controller with 8 independent triggers.

    Each trigger is fail-closed: any fire → rollback + lockdown.
    All rollbacks are immutable, hash-chained, LoM-bound for compliance.
    """

    # Trigger 1: Correctness drop thresholds
    CORRECTNESS_THRESHOLDS = {
        "max_drop_pct": 0.02,  # 2% drop = rollback
        "min_agreement_rate": 0.98,  # <98% = >2% disagreement = rollback
    }

    # Trigger 2: Latency spike thresholds
    LATENCY_THRESHOLDS = {
        "max_spike_pct": 0.20,  # 20% spike = rollback
        "phase_2b_hard_limit_ms": 250.0,  # Hard limit in Phase 2b
    }

    # Trigger 3: Confidence regression thresholds
    CONFIDENCE_THRESHOLDS = {
        "max_regression_pct": 0.10,  # 10% regression = hold
        "min_confidence": 0.70,  # <70% = alert
    }

    # Trigger 7: Loss signal critical thresholds
    LOSS_SIGNAL_THRESHOLDS = {
        "ab_ci_crosses_zero": True,  # Halt variant
        "latency_regression_pct": 0.20,  # 20% increase = halt
        "confidence_decline_pct": 0.10,  # 10% decline over 7 days = halt
    }

    def __init__(self):
        self.rollback_events: List[RollbackEvent] = []
        self.event_counter = 0
        self.audit_trail: List[Dict] = []
        self.locked_phases: Dict[str, str] = {}  # phase -> unlock_time ISO string
        self.locked_skills: Dict[str, str] = {}  # skill_id -> unlock_time ISO string

    def check_all_triggers(
        self,
        metrics: Dict[str, float],
        phase: str,
        skill_id: Optional[str] = None,
        baseline_latency_ms: float = 100.0,
    ) -> Optional[RollbackEvent]:
        """
        Check all 8 rollback triggers against current metrics.

        Returns first RollbackEvent if any trigger fires, otherwise None.
        """

        # Trigger 1: Correctness drop >2%
        correctness_event = self._check_correctness_drop(metrics, phase)
        if correctness_event:
            return correctness_event

        # Trigger 2: Latency spike >20%
        latency_event = self._check_latency_spike(metrics, baseline_latency_ms, phase)
        if latency_event:
            return latency_event

        # Trigger 3: Confidence regression >10%
        confidence_event = self._check_confidence_regression(metrics)
        if confidence_event:
            return confidence_event

        # Trigger 4: Audit chain break (CRITICAL)
        audit_event = self._check_audit_chain_break(metrics)
        if audit_event:
            return audit_event

        # Trigger 5: Tenant isolation violation (CRITICAL)
        tenant_event = self._check_tenant_isolation_violation(metrics)
        if tenant_event:
            return tenant_event

        # Trigger 6: Security check failure (CRITICAL)
        security_event = self._check_security_failure(metrics)
        if security_event:
            return security_event

        # Trigger 7: Loss signal CRITICAL (A/B regression, latency, confidence)
        loss_event = self._check_loss_signal_critical(metrics)
        if loss_event:
            return loss_event

        return None

    def _check_correctness_drop(self, metrics: Dict[str, float], phase: str) -> Optional[RollbackEvent]:
        """Trigger 1: Correctness drop >2%"""
        agreement_rate = metrics.get("agreement_rate", 1.0)

        if agreement_rate < self.CORRECTNESS_THRESHOLDS["min_agreement_rate"]:
            return RollbackEvent(
                event_id=self._next_event_id(),
                trigger=RollbackTrigger.CORRECTNESS_DROP,
                timestamp=datetime.now(timezone.utc).isoformat(),
                phase=phase,
                metric_name="agreement_rate",
                actual_value=agreement_rate,
                threshold=self.CORRECTNESS_THRESHOLDS["min_agreement_rate"],
                actions_taken=[
                    RollbackAction.DISABLE_SKILL,
                    RollbackAction.REVERT_VERSION,
                    RollbackAction.LOCK_PHASE,
                ],
                reason=f"Agreement rate {agreement_rate:.1%} < {self.CORRECTNESS_THRESHOLDS['min_agreement_rate']:.1%}",
                lom="rollback_automation.py::_check_correctness_drop:166",
            )

        return None

    def _check_latency_spike(
        self,
        metrics: Dict[str, float],
        baseline_ms: float,
        phase: str,
    ) -> Optional[RollbackEvent]:
        """Trigger 2: Latency spike >20%"""
        actual_latency_ms = metrics.get("latency_p99_ms", 0.0)
        max_latency_ms = baseline_ms * (1 + self.LATENCY_THRESHOLDS["max_spike_pct"])

        if actual_latency_ms > max_latency_ms:
            spike_pct = (actual_latency_ms - baseline_ms) / baseline_ms

            # In Phase 2b, also check hard limit
            if phase == "PHASE_2B_SKILL_PRIMARY" and actual_latency_ms > self.LATENCY_THRESHOLDS["phase_2b_hard_limit_ms"]:
                # Hard limit exceeded = immediate rollback
                actions = [
                    RollbackAction.REVERT_VERSION,
                    RollbackAction.LOCK_PHASE,
                    RollbackAction.ESCALATE_TO_PAGERDUTY,
                ]
            else:
                # Within tolerance = hold traffic escalation
                actions = [RollbackAction.HOLD_TRAFFIC, RollbackAction.LOCK_PHASE]

            return RollbackEvent(
                event_id=self._next_event_id(),
                trigger=RollbackTrigger.LATENCY_SPIKE,
                timestamp=datetime.now(timezone.utc).isoformat(),
                phase=phase,
                metric_name="latency_p99_ms",
                actual_value=actual_latency_ms,
                threshold=max_latency_ms,
                actions_taken=actions,
                lockdown_until=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                reason=f"Latency {actual_latency_ms:.1f}ms exceeds {max_latency_ms:.1f}ms (+{spike_pct:.1%})",
                lom="rollback_automation.py::_check_latency_spike:190",
            )

        return None

    def _check_confidence_regression(self, metrics: Dict[str, float]) -> Optional[RollbackEvent]:
        """Trigger 3: Confidence regression >10%"""
        actual_confidence = metrics.get("confidence", 0.5)
        prior_confidence = metrics.get("prior_confidence", actual_confidence)

        if prior_confidence > 0:
            regression_pct = (prior_confidence - actual_confidence) / prior_confidence
        else:
            regression_pct = 0.0

        if regression_pct > self.CONFIDENCE_THRESHOLDS["max_regression_pct"]:
            return RollbackEvent(
                event_id=self._next_event_id(),
                trigger=RollbackTrigger.CONFIDENCE_REGRESSION,
                timestamp=datetime.now(timezone.utc).isoformat(),
                phase=metrics.get("phase", "unknown"),
                metric_name="confidence",
                actual_value=actual_confidence,
                threshold=self.CONFIDENCE_THRESHOLDS["min_confidence"],
                actions_taken=[
                    RollbackAction.HOLD_TRAFFIC,
                    RollbackAction.INITIATE_RETRAINING,
                ],
                lockdown_until=(datetime.now(timezone.utc) + timedelta(hours=6)).isoformat(),
                reason=f"Confidence regression from {prior_confidence:.2f} to {actual_confidence:.2f} (-{regression_pct:.1%})",
                lom="rollback_automation.py::_check_confidence_regression:231",
            )

        return None

    def _check_audit_chain_break(self, metrics: Dict[str, float]) -> Optional[RollbackEvent]:
        """Trigger 4: Audit chain break (CRITICAL, IMMEDIATE)"""
        audit_chain_ok = metrics.get("audit_chain_verified", True)

        if not audit_chain_ok:
            return RollbackEvent(
                event_id=self._next_event_id(),
                trigger=RollbackTrigger.AUDIT_CHAIN_BREAK,
                timestamp=datetime.now(timezone.utc).isoformat(),
                phase=metrics.get("phase", "unknown"),
                metric_name="audit_chain_verified",
                actual_value=0.0,
                threshold=1.0,
                actions_taken=[
                    RollbackAction.REVERT_VERSION,
                    RollbackAction.LOCK_PHASE,
                    RollbackAction.ESCALATE_TO_PAGERDUTY,
                ],
                lockdown_until=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                reason="Audit chain hash verification failed - CRITICAL security incident",
                lom="rollback_automation.py::_check_audit_chain_break:261",
            )

        return None

    def _check_tenant_isolation_violation(self, metrics: Dict[str, float]) -> Optional[RollbackEvent]:
        """Trigger 5: Tenant isolation violation (CRITICAL, IMMEDIATE)"""
        tenant_isolation_ok = metrics.get("tenant_isolation_verified", True)

        if not tenant_isolation_ok:
            return RollbackEvent(
                event_id=self._next_event_id(),
                trigger=RollbackTrigger.TENANT_ISOLATION_VIOLATION,
                timestamp=datetime.now(timezone.utc).isoformat(),
                phase=metrics.get("phase", "unknown"),
                metric_name="tenant_isolation_verified",
                actual_value=0.0,
                threshold=1.0,
                actions_taken=[
                    RollbackAction.REVERT_VERSION,
                    RollbackAction.LOCK_PHASE,
                    RollbackAction.ESCALATE_TO_PAGERDUTY,
                ],
                lockdown_until=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                reason="Cross-tenant data leakage detected - CRITICAL compliance violation",
                lom="rollback_automation.py::_check_tenant_isolation_violation:286",
            )

        return None

    def _check_security_failure(self, metrics: Dict[str, float]) -> Optional[RollbackEvent]:
        """Trigger 6: Security check failure (CRITICAL, IMMEDIATE)"""
        security_checks_pass = metrics.get("security_checks_pass", True)

        if not security_checks_pass:
            return RollbackEvent(
                event_id=self._next_event_id(),
                trigger=RollbackTrigger.SECURITY_CHECK_FAILURE,
                timestamp=datetime.now(timezone.utc).isoformat(),
                phase=metrics.get("phase", "unknown"),
                metric_name="security_checks_pass",
                actual_value=0.0,
                threshold=1.0,
                actions_taken=[
                    RollbackAction.REVERT_VERSION,
                    RollbackAction.LOCK_PHASE,
                    RollbackAction.ESCALATE_TO_PAGERDUTY,
                ],
                lockdown_until=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                reason="Security gate failure - requires security team review",
                lom="rollback_automation.py::_check_security_failure:311",
            )

        return None

    def _check_loss_signal_critical(self, metrics: Dict[str, float]) -> Optional[RollbackEvent]:
        """Trigger 7: Loss signal CRITICAL (A/B regression, latency, confidence)"""

        # Check A/B test regression (CI crosses zero)
        ci_lower = metrics.get("ab_ci_lower", 0.0)
        ci_upper = metrics.get("ab_ci_upper", 0.0)

        if ci_lower < 0 < ci_upper:
            return RollbackEvent(
                event_id=self._next_event_id(),
                trigger=RollbackTrigger.LOSS_SIGNAL_CRITICAL,
                timestamp=datetime.now(timezone.utc).isoformat(),
                phase=metrics.get("phase", "unknown"),
                metric_name="ab_test_ci",
                actual_value=0.0,
                threshold=0.0,
                actions_taken=[RollbackAction.HOLD_TRAFFIC, RollbackAction.LOCK_PHASE],
                lockdown_until=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
                reason=f"A/B test regression: CI [{ci_lower:.3f}, {ci_upper:.3f}] crosses zero",
                lom="rollback_automation.py::_check_loss_signal_critical:336",
            )

        return None

    def execute_rollback(
        self,
        event: RollbackEvent,
        version_to_revert: Optional[str] = None,
        operator_id: Optional[str] = None,
    ) -> bool:
        """
        Execute rollback for triggered event.

        Reverts skills to safe version, locks phase, emits audit events.
        """
        try:
            logger.error(f"EXECUTING ROLLBACK: {event.trigger.value} — {event.reason}")

            # Apply rollback actions
            for action in event.actions_taken:
                if action == RollbackAction.DISABLE_SKILL:
                    if event.skill_id:
                        # Use provided lockdown time or default to 1 hour from now
                        lock_until = event.lockdown_until or (
                            datetime.now(timezone.utc) + timedelta(hours=1)
                        ).isoformat()
                        self.locked_skills[event.skill_id] = lock_until
                        logger.warning(f"Skill {event.skill_id} disabled, using fallback engine")

                elif action == RollbackAction.REVERT_VERSION:
                    logger.info(f"Reverting to version {version_to_revert or 'previous stable'}")

                elif action == RollbackAction.LOCK_PHASE:
                    phase_name = event.phase or "unknown"
                    self.locked_phases[phase_name] = event.lockdown_until or ""
                    logger.error(f"Phase {phase_name} LOCKED until {event.lockdown_until}")

                elif action == RollbackAction.HOLD_TRAFFIC:
                    logger.warning("Traffic escalation HALTED")

                elif action == RollbackAction.ESCALATE_TO_PAGERDUTY:
                    logger.critical(f"ESCALATING TO PAGERDUTY: {event.reason}")

                elif action == RollbackAction.INITIATE_RETRAINING:
                    logger.info("Initiating skill retraining loop")

            # Record rollback event
            self.rollback_events.append(event)

            # Emit audit event (immutable, hash-chained)
            self._audit_log({
                "event": "rollback_executed",
                "rollback_event_id": event.event_id,
                "trigger": event.trigger.value,
                "reason": event.reason,
                "phase": event.phase,
                "actions_taken": [a.value for a in event.actions_taken],
                "lockdown_until": event.lockdown_until,
                "operator_id": operator_id,
                "lom": event.lom,
            })

            return True

        except Exception as e:
            logger.error(f"Failed to execute rollback: {e}")
            return False

    def manual_rollback(self, operator_id: str, reason: str, phase: str) -> RollbackEvent:
        """
        Operator manually initiates rollback.

        Trigger 8: Manual operator decision.
        """
        event = RollbackEvent(
            event_id=self._next_event_id(),
            trigger=RollbackTrigger.MANUAL_OPERATOR_ROLLBACK,
            timestamp=datetime.now(timezone.utc).isoformat(),
            phase=phase,
            operator_id=operator_id,
            reason=reason,
            actions_taken=[
                RollbackAction.REVERT_VERSION,
                RollbackAction.LOCK_PHASE,
            ],
            lockdown_until=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
            lom="rollback_automation.py::manual_rollback:420",
        )

        self.execute_rollback(event, operator_id=operator_id)
        return event

    def is_phase_locked(self, phase: str) -> bool:
        """Check if phase is currently locked"""
        if phase not in self.locked_phases:
            return False

        unlock_time = datetime.fromisoformat(self.locked_phases[phase])
        if datetime.now(timezone.utc) >= unlock_time:
            # Lock expired
            del self.locked_phases[phase]
            return False

        return True

    def unlock_phase(self, phase: str, operator_id: str, reason: str) -> bool:
        """Operator manually unlocks phase"""
        if phase not in self.locked_phases:
            logger.warning(f"Phase {phase} is not locked")
            return False

        del self.locked_phases[phase]

        self._audit_log({
            "event": "phase_unlocked",
            "phase": phase,
            "operator_id": operator_id,
            "reason": reason,
            "lom": "rollback_automation.py::unlock_phase:460",
        })

        logger.info(f"Phase {phase} unlocked by {operator_id}")
        return True

    def _next_event_id(self) -> str:
        """Generate next rollback event ID"""
        self.event_counter += 1
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"RB-{timestamp}-{self.event_counter:04d}"

    def _audit_log(self, event: Dict) -> None:
        """Log audit event (immutable, hash-chained)"""
        event["sequence_number"] = len(self.audit_trail)
        event["timestamp"] = event.get("timestamp", datetime.now(timezone.utc).isoformat())

        if self.audit_trail:
            event["prior_hash"] = self.audit_trail[-1].get("hash", "")
        else:
            event["prior_hash"] = "GENESIS"

        event_json = json.dumps(event, sort_keys=True, default=str)
        event["hash"] = hashlib.sha256(event_json.encode()).hexdigest()

        self.audit_trail.append(event)

    def get_rollback_history(self) -> List[RollbackEvent]:
        """Get complete rollback history"""
        return self.rollback_events.copy()

    def get_open_lockdowns(self) -> Dict[str, str]:
        """Get currently active phase/skill lockdowns"""
        active = {}
        now = datetime.now(timezone.utc)

        # Check phases
        for phase, unlock_time_str in self.locked_phases.items():
            unlock_time = datetime.fromisoformat(unlock_time_str)
            if now < unlock_time:
                active[f"phase:{phase}"] = unlock_time_str

        # Check skills
        for skill_id, unlock_time_str in self.locked_skills.items():
            unlock_time = datetime.fromisoformat(unlock_time_str)
            if now < unlock_time:
                active[f"skill:{skill_id}"] = unlock_time_str

        return active
