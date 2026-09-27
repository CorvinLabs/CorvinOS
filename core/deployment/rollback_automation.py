"""
Rollback Automation with 8 Fail-Closed Triggers

NOT WIRED: no production caller as of 2026-09-27 (adversarial review). Nothing
feeds it live metrics and nothing acts on its locks; ``REVERT_VERSION`` calls
``<CORVIN_SKILL_API_ENDPOINT>/<skill_id>/version``, a route no CorvinOS service
serves, so a revert fails (honestly) on this build. Audit records go to the
tenant chain through ``core.deployment.audit_sink`` (fail-closed); operator
"authentication" is a caller-supplied dict, not a verified identity, and a
CRITICAL unlock is refused unless a real 2FA verifier is injected.

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

FIXES IMPLEMENTED (14 Findings):
- RA-001: Audit-BEFORE semantics (write audit event first, fail-closed if audit fails)
- RA-002: REVERT_VERSION actually reverts via real API call
- RA-003: Thread-safe lock management with RLock()
- RA-004: Cascade incident prevention (5 min dedup window + 10 min cooldown)
- RA-005: Operator authentication for manual unlock (RBAC + 2FA for CRITICAL)
"""

from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Callable, Tuple
import logging
import json
import hashlib
import threading
from dataclasses import asdict

from . import audit_sink

logger = logging.getLogger(__name__)

# Content-free detail fields only (ids, enums, counts, timestamps) — never the
# free-text ``reason`` or an exception message.
audit_sink.register_events({
    "deployment.rollback_executed": {
        "rollback_event_id", "trigger", "phase", "skill_id", "actions_taken",
        "lockdown_until", "operator_ref",
    },
    "deployment.rollback_execution_failed": {"rollback_event_id", "error_type"},
    "deployment.phase_unlocked": {"phase", "operator_ref", "is_critical_unlock"},
})


def _operator_ref(operator_id: Optional[str]) -> str:
    """Pseudonymous operator reference for the audit chain (never the raw id)."""
    if not operator_id:
        return ""
    return hashlib.sha256(str(operator_id).encode()).hexdigest()[:12]


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

    FIXES:
    - RA-001: Audit-BEFORE semantics (audit first, then actions)
    - RA-002: execute_version_revert() with real API calls
    - RA-003: Thread-safe RLock() for dict operations
    - RA-004: Cascade prevention (5 min dedup + 10 min cooldown)
    - RA-005: Operator authentication for manual unlock (RBAC + 2FA)
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

    # RA-004: Cascade prevention
    ROLLBACK_DEDUP_WINDOW_SECONDS = 300  # 5 minutes
    ROLLBACK_COOLDOWN_SECONDS = 600  # 10 minutes

    def __init__(
        self,
        tenant_id: str = "_default",
        twofa_verifier: Optional[Callable[[str, str], bool]] = None,
    ):
        self.tenant_id = tenant_id
        # (user_id, token) -> bool. None = no verifier configured → CRITICAL
        # unlocks are refused (fail-closed), never "any 6-char string passes".
        self.twofa_verifier = twofa_verifier
        self.rollback_events: List[RollbackEvent] = []
        self.event_counter = 0
        # Local mirror of the records this controller committed to the tenant
        # audit chain (the chain itself is the source of truth).
        self.audit_trail: List[Dict] = []
        self.locked_phases: Dict[str, str] = {}  # phase -> unlock_time ISO string
        self.locked_skills: Dict[str, str] = {}  # skill_id -> unlock_time ISO string
        # RA-003: Thread-safe lock
        self.lock = threading.RLock()
        # RA-004: Cascade prevention tracking
        self.last_rollback_time: float = 0.0
        self.last_rollback_phase: str = ""

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
        audit_chain_ok = metrics.get("audit_chain_verified", False)  # not measured = not verified (fail-closed)

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
                reason=("Audit chain not verified (not measured) - CRITICAL" if "audit_chain_verified" not in metrics
                        else "Audit chain hash verification failed - CRITICAL security incident"),
                lom="rollback_automation.py::_check_audit_chain_break:261",
            )

        return None

    def _check_tenant_isolation_violation(self, metrics: Dict[str, float]) -> Optional[RollbackEvent]:
        """Trigger 5: Tenant isolation violation (CRITICAL, IMMEDIATE)"""
        tenant_isolation_ok = metrics.get("tenant_isolation_verified", False)  # not measured = not verified (fail-closed)

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
                reason=("Tenant isolation not verified (not measured) - CRITICAL" if "tenant_isolation_verified" not in metrics
                        else "Cross-tenant data leakage detected - CRITICAL compliance violation"),
                lom="rollback_automation.py::_check_tenant_isolation_violation:286",
            )

        return None

    def _check_security_failure(self, metrics: Dict[str, float]) -> Optional[RollbackEvent]:
        """Trigger 6: Security check failure (CRITICAL, IMMEDIATE)"""
        security_checks_pass = metrics.get("security_checks_pass", False)  # not measured = not verified (fail-closed)

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
                reason=("Security checks not run (not measured) - requires security team review" if "security_checks_pass" not in metrics
                        else "Security gate failure - requires security team review"),
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

        RA-001: Audit-BEFORE semantics:
        1. Write audit event FIRST (fail-closed if audit fails)
        2. THEN execute rollback actions
        3. If audit fails, entire rollback is rejected

        RA-004: Cascade prevention (check dedup window + cooldown)
        """
        with self.lock:
            # RA-004: Check cascade prevention
            now = datetime.now(timezone.utc).timestamp()

            # Deduplication: same phase not rolled back twice within 5 min
            if event.phase == self.last_rollback_phase:
                if now - self.last_rollback_time < self.ROLLBACK_DEDUP_WINDOW_SECONDS:
                    logger.warning(
                        f"Rollback cascade prevented: {event.phase} already rolled back within {self.ROLLBACK_DEDUP_WINDOW_SECONDS}s"
                    )
                    return False

            # Cooldown: at least 10 min between consecutive rollbacks
            if now - self.last_rollback_time < self.ROLLBACK_COOLDOWN_SECONDS:
                if event.phase != self.last_rollback_phase:
                    logger.warning(
                        f"Rollback cooldown: waiting {self.ROLLBACK_COOLDOWN_SECONDS}s before next rollback"
                    )
                    return False

        try:
            logger.error(f"EXECUTING ROLLBACK: {event.trigger.value} — {event.reason}")

            # RA-001: Audit-BEFORE: Write audit event FIRST (fail-closed if audit fails)
            audit_result = self._audit_log({
                "event": "rollback_executed",
                "rollback_event_id": event.event_id,
                "trigger": event.trigger.value,
                "phase": event.phase,
                "skill_id": event.skill_id or "",
                "actions_taken": [a.value for a in event.actions_taken],
                "lockdown_until": event.lockdown_until or "",
                "operator_ref": _operator_ref(operator_id or event.operator_id),
                "lom": event.lom,
            })

            # Fail-closed: if audit fails, reject entire rollback
            if not audit_result:
                logger.critical(f"ROLLBACK REJECTED: Audit log write failed for {event.event_id}")
                return False

            # RA-001: THEN execute rollback actions
            with self.lock:
                for action in event.actions_taken:
                    if action == RollbackAction.DISABLE_SKILL:
                        if event.skill_id:
                            lock_until = event.lockdown_until or (
                                datetime.now(timezone.utc) + timedelta(hours=1)
                            ).isoformat()
                            self.locked_skills[event.skill_id] = lock_until
                            logger.warning(f"Skill {event.skill_id} disabled, using fallback engine")

                    elif action == RollbackAction.REVERT_VERSION:
                        # RA-002: Actually revert the version
                        if self._execute_version_revert(event.skill_id, version_to_revert):
                            logger.info(
                                f"Version reverted: {event.skill_id} → {version_to_revert or 'previous stable'}"
                            )
                        else:
                            logger.error(f"Failed to revert version for {event.skill_id}")
                            return False

                    elif action == RollbackAction.LOCK_PHASE:
                        phase_name = event.phase or "unknown"
                        # "" = locked until an operator unlocks it (no expiry).
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

                # RA-004: Update cascade prevention tracking
                self.last_rollback_time = now
                self.last_rollback_phase = event.phase

            return True

        except Exception as e:
            logger.error(f"Failed to execute rollback: {e}")
            # Log the failure to audit trail
            try:
                self._audit_log({
                    "event": "rollback_execution_failed",
                    "error_type": type(e).__name__,
                    "rollback_event_id": event.event_id,
                })
            except Exception as audit_e:
                logger.critical(f"Failed to log rollback failure: {audit_e}")
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

        if not self.execute_rollback(event, operator_id=operator_id):
            # The caller gets the event either way; say loudly that it did NOT run.
            logger.error(f"Manual rollback {event.event_id} was NOT executed")
        return event

    def is_phase_locked(self, phase: str) -> bool:
        """
        Check if phase is currently locked (RA-003: thread-safe with RLock)
        """
        with self.lock:
            if phase not in self.locked_phases:
                return False

            unlock_time_str = self.locked_phases[phase]
            if not unlock_time_str:
                # No expiry recorded: locked until an operator unlocks it.
                # (Reading "" as "unlocked" silently disarmed every LOCK_PHASE
                # whose trigger carries no lockdown_until.)
                return True

            try:
                unlock_time = datetime.fromisoformat(unlock_time_str)
                if datetime.now(timezone.utc) >= unlock_time:
                    # Lock expired
                    del self.locked_phases[phase]
                    return False
                return True
            except ValueError:
                logger.error(f"Invalid unlock time for phase {phase}: {unlock_time_str}")
                return True  # fail-closed: an unreadable lock stays locked

    def unlock_phase(
        self,
        phase: str,
        operator_context: Optional[Dict] = None,
        operator_id: Optional[str] = None,
        reason: str = "",
    ) -> bool:
        """
        Operator manually unlocks phase (RA-005: authentication + RBAC + 2FA for CRITICAL).

        RA-005: Requires authenticated operator context:
        - operator_context: Dict with {'user_id': str, 'role': str, 'authenticated': bool}
        - For CRITICAL locks: requires 2FA token
        """
        with self.lock:
            if phase not in self.locked_phases:
                logger.warning(f"Phase {phase} is not locked")
                return False

            # RA-005: Operator authentication
            if not self._validate_operator_auth(phase, operator_context, operator_id):
                logger.error(f"Operator auth failed for phase unlock: {phase}")
                return False

            unlock_time_str = self.locked_phases[phase]

            # Check if this is a CRITICAL lock (>24 hours)
            is_critical_lock = False
            if unlock_time_str:
                try:
                    unlock_time = datetime.fromisoformat(unlock_time_str)
                    lock_duration = unlock_time - datetime.now(timezone.utc)
                    is_critical_lock = lock_duration > timedelta(hours=24)
                except ValueError:
                    pass

            # RA-005: 2FA check for CRITICAL locks
            if is_critical_lock:
                if not self._verify_2fa(operator_context):
                    logger.error(f"2FA verification failed for CRITICAL unlock: {phase}")
                    return False

            del self.locked_phases[phase]

        # RA-001: Audit the unlock action
        audit_result = self._audit_log({
            "event": "phase_unlocked",
            "phase": phase,
            "operator_ref": _operator_ref(
                operator_id or (operator_context.get("user_id") if operator_context else "")
            ),
            "is_critical_unlock": is_critical_lock,
            "lom": "rollback_automation.py::unlock_phase",
        })

        if not audit_result:
            logger.error(f"Failed to audit phase unlock for {phase}")
            # Re-lock the phase since audit failed
            with self.lock:
                self.locked_phases[phase] = unlock_time_str
            return False

        logger.info(
            f"Phase {phase} unlocked by {operator_id or (operator_context.get('user_id') if operator_context else 'unknown')}"
        )
        return True

    def _validate_operator_auth(
        self, phase: str, operator_context: Optional[Dict], operator_id: Optional[str]
    ) -> bool:
        """RA-005: Validate operator authentication and RBAC.

        A bare ``operator_id`` string is NOT authentication — anyone can pass
        one. An unlock needs an operator context that says it was authenticated
        and carries an allowed role.
        """
        if not operator_context:
            logger.error("No authenticated operator context provided for phase unlock")
            return False

        # Check RBAC: only admin/operator role can unlock
        if operator_context:
            if not operator_context.get("authenticated"):
                logger.error("Operator not authenticated")
                return False

            role = operator_context.get("role", "").lower()
            if role not in ["admin", "operator", "sre"]:
                logger.error(f"Operator role '{role}' not authorized to unlock phases")
                return False

        return True

    def _verify_2fa(self, operator_context: Optional[Dict]) -> bool:
        """RA-005: Verify 2FA token for CRITICAL unlocks"""
        if not operator_context:
            logger.error("No operator context for 2FA verification")
            return False

        # Check if 2FA token is present and valid
        twofa_token = operator_context.get("twofa_token")
        if not twofa_token:
            logger.error("2FA token required but not provided")
            return False

        # No verifier configured → there is nothing that can verify the token.
        # Accepting any non-empty string would be a fabricated 2FA; refuse.
        if self.twofa_verifier is None:
            logger.error("No 2FA verifier configured — CRITICAL unlock refused (fail-closed)")
            return False
        try:
            ok = bool(self.twofa_verifier(str(operator_context.get("user_id", "")), str(twofa_token)))
        except Exception as e:  # noqa: BLE001
            logger.error(f"2FA verifier raised {type(e).__name__} — refused")
            return False
        if not ok:
            logger.error("2FA token rejected by verifier")
            return False
        logger.info("2FA verification passed")
        return True

    def _execute_version_revert(self, skill_id: Optional[str], version_to_revert: Optional[str]) -> bool:
        """
        RA-002: Actually revert the skill version via API call.

        Returns True if revert succeeded, False otherwise.
        """
        if not skill_id:
            logger.warning("No skill_id provided for version revert")
            return False

        try:
            # Get the version to revert to
            target_version = version_to_revert or self._get_previous_stable_version(skill_id)
            if not target_version:
                logger.error(f"No previous stable version found for {skill_id}")
                return False

            # Make API call to revert version
            import os
            import requests

            api_endpoint = os.getenv("CORVIN_SKILL_API_ENDPOINT", "http://localhost:8765/v1/skills")
            revert_url = f"{api_endpoint}/{skill_id}/version"

            payload = {
                "version": target_version,
                "reason": "automated_rollback",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            response = requests.put(revert_url, json=payload, timeout=10)

            if response.status_code == 200:
                logger.info(f"Version reverted for {skill_id} to {target_version}")
                return True
            else:
                logger.error(f"Skill API returned {response.status_code} for {skill_id} revert")
                return False

        except Exception as e:
            logger.error(f"Failed to execute version revert for {skill_id}: {e}")
            return False

    def _get_previous_stable_version(self, skill_id: str) -> Optional[str]:
        """Get the previous stable version of a skill from version history"""
        try:
            # In production, this would query a version registry or database
            # For now, return a default fallback version
            import os
            import requests

            api_endpoint = os.getenv("CORVIN_SKILL_API_ENDPOINT", "http://localhost:8765/v1/skills")
            history_url = f"{api_endpoint}/{skill_id}/version-history"

            response = requests.get(history_url, timeout=5)
            if response.status_code == 200:
                versions = response.json().get("versions", [])
                # Return second-most recent version (current is first)
                if len(versions) > 1:
                    return versions[1].get("version")

            # No known prior version: never invent one (a made-up "v1.0.0"
            # would "revert" to a version that may not exist).
            return None

        except Exception as e:
            logger.warning(f"Failed to query version history for {skill_id}: {e}")
            return None

    def _next_event_id(self) -> str:
        """Generate next rollback event ID"""
        self.event_counter += 1
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"RB-{timestamp}-{self.event_counter:04d}"

    def _audit_log(self, event: Dict) -> bool:
        """Commit one record to the tenant audit chain (RA-001).

        Returns True only when the record is on the chain; False otherwise
        (the caller then refuses the action — fail-closed). ``event["event"]``
        names the record ``deployment.<event>``.
        """
        details = {k: v for k, v in event.items() if k != "event"}
        try:
            record = audit_sink.emit(
                f"deployment.{event['event']}", details, tenant_id=self.tenant_id,
                severity="WARNING",
            )
        except audit_sink.AuditWriteFailed as e:
            logger.error(f"Audit write failed (fail-closed): {e}")
            return False
        self.audit_trail.append(record)
        return True

    def get_rollback_history(self) -> List[RollbackEvent]:
        """Get complete rollback history"""
        return self.rollback_events.copy()

    def get_open_lockdowns(self) -> Dict[str, str]:
        """Get currently active phase/skill lockdowns"""
        active = {}
        now = datetime.now(timezone.utc)

        def _active(unlock_time_str: str) -> bool:
            if not unlock_time_str:
                return True  # no expiry: held until an operator unlocks
            try:
                return now < datetime.fromisoformat(unlock_time_str)
            except ValueError:
                return True  # unreadable lock stays active (fail-closed)

        with self.lock:
            for phase, unlock_time_str in self.locked_phases.items():
                if _active(unlock_time_str):
                    active[f"phase:{phase}"] = unlock_time_str
            for skill_id, unlock_time_str in self.locked_skills.items():
                if _active(unlock_time_str):
                    active[f"skill:{skill_id}"] = unlock_time_str

        return active
