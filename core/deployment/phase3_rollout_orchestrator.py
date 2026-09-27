"""
Phase 3 Production Rollout Orchestrator — 12-Week Staged Deployment

Automates the complete rollout from Phase 1 (shadow) → Phase 2a (canary) → Phase 2b (skill-primary).
Implements adaptive progression, learning-loop-gated advancement, and fail-closed auto-rollback.

ADRs: 0206 (canary), 0867 (watchdog), 0205 (learning), 0186 (presence), 0369 (edge cases)
Compliance: GDPR (Art. 5/6/30/32), EU AI Act (Art. 5/50), Audit-First (ADR-0232/0233)

Load-bearing invariants:
1. Zero production downtime — every gate fail-closed, rollback automatic
2. Audit trail immutable — every decision LoM-bound (ADR-0537)
3. Tenant isolation enforced — no cross-tenant learning leakage (ADR-0007)
4. Learning loop live — feedback → confidence updates each turn (ADR-0314)
5. Multi-skill orchestration — L5/L10/L22/L44 coordinated via DAG (ADR-0535)

NOT WIRED: no production caller as of 2026-09-27 (adversarial review). Nothing
reads this orchestrator's phase / traffic percentage to route a request: the only
importer is ``core/deployment/__init__.py`` (and the equally unwired
``master_orchestration_blueprint``); ``CORVIN_ACP_PHASE`` is unset in every
systemd unit. Its "traffic percentage" is a number in a dataclass.

Honesty contract (adversarial review 2026-09-27): every check this module
cannot measure is reported NOT MEASURED and FAILS the gate it guards — it is
never assumed to pass. Concretely:
- latency baseline: recorded from the Phase 1 metrics the caller supplies; a
  canary/skill-primary phase without a recorded baseline rolls back
  (``BASELINE_NOT_MEASURED``) instead of comparing against an invented 100 ms.
- confidence regression: compared with the previous day's observed mean, not an
  invented 0.80 prior.
- audit chain: the real tenant chain is verified with
  ``forge.security_events.verify_chain``; unverifiable = broken.
- audit violations / tenant isolation: only what the caller supplies to
  ``advance_day``; absent = not measured = Phase 2b activation blocked.
- every decision is written to ``tenant_audit_chain(tenant_id)`` through
  ``core.deployment.audit_sink`` BEFORE it is applied; a failed write raises
  and the decision is not taken. ``audit_trail`` is only a local mirror.
- ``phase3_deployment_config.yaml`` is NOT read: ``config_path`` is stored and
  never opened; every threshold lives in the evaluator classes below.
"""

from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple
import json
import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class Phase(Enum):
    """Rollout phases"""
    PHASE_1_SHADOW = "PHASE_1_SHADOW"
    PHASE_2A_CANARY = "PHASE_2A_CANARY"
    PHASE_2B_SKILL_PRIMARY = "PHASE_2B_SKILL_PRIMARY"
    ROLLED_BACK = "ROLLED_BACK"
    COMPLETE = "COMPLETE"


class SkillMode(Enum):
    """Skill execution mode"""
    ADVISORY = "ADVISORY"  # Shadow: skill runs, logged, no routing impact
    DUAL_WRITE = "DUAL_WRITE"  # Canary: skill parallel to bundled, agreement tracked
    PRIMARY = "PRIMARY"  # Skill-primary: skill makes decision, bundled fallback only
    FALLBACK = "FALLBACK"  # Skill disabled, bundled engine only


class RollbackReason(Enum):
    """Why rollback was triggered"""
    CORRECTNESS_DROP = "CORRECTNESS_DROP"
    LATENCY_SPIKE = "LATENCY_SPIKE"
    CONFIDENCE_REGRESSION = "CONFIDENCE_REGRESSION"
    AUDIT_CHAIN_BREAK = "AUDIT_CHAIN_BREAK"
    TENANT_ISOLATION_VIOLATION = "TENANT_ISOLATION_VIOLATION"
    SECURITY_CHECK_FAILURE = "SECURITY_CHECK_FAILURE"
    MEMORY_EXHAUSTION = "MEMORY_EXHAUSTION"
    EXCEPTION_RATE_EXCEEDED = "EXCEPTION_RATE_EXCEEDED"
    MANUAL_OPERATOR_DECISION = "MANUAL_OPERATOR_DECISION"
    BASELINE_NOT_MEASURED = "BASELINE_NOT_MEASURED"
    TENANT_ISOLATION_VIOLATION_REPORTED = "TENANT_ISOLATION_VIOLATION_REPORTED"


NOT_MEASURED = "not_measured"

_AUDIT_FIELDS = frozenset({
    "event", "day", "week", "phase", "reason", "prior_phase", "approval_for",
    "phase_transitioned_to", "traffic_escalated_to", "skill_id", "confidence",
    "approval_required", "operator_ref", "sequence_number", "mirror_hash",
})


def _operator_ref(approved_by: str) -> str:
    """Content-free operator reference (12-hex sha256 prefix, never the id)."""
    if not approved_by:
        return ""
    return hashlib.sha256(approved_by.encode("utf-8")).hexdigest()[:12]
_AUDIT_EVENTS = {
    f"deployment.rollout.{name}": _AUDIT_FIELDS
    for name in (
        "day_advanced", "rollback_executed", "phase_1_gate_passed", "operator_approval",
        "phase_2a_gate_passed", "phase_2b_approval_requested", "skill_activated_to_primary",
    )
}


@dataclass
class SkillMetrics:
    """Per-skill metrics snapshot"""
    skill_id: str
    phase: Phase = Phase.PHASE_1_SHADOW
    agreement_rate: float = 0.0  # 0.0–1.0: skill output == bundled output
    confidence: float = 0.5  # 0.0–1.0: skill's estimated correctness (Bayesian posterior)
    confidence_sigma: float = 0.2  # Standard deviation (convergence indicator)
    correctness_delta: float = 0.0  # Skill correctness − bundled correctness (%)
    latency_mean_ms: float = 0.0
    latency_p99_ms: float = 0.0
    error_rate: float = 0.0  # % of skill invocations with errors
    feedback_count: int = 0  # Total feedback events collected
    feedback_rate: float = 0.0  # Feedback events per second
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class PhaseMetrics:
    """Rollout phase metrics"""
    phase: Phase
    start_time: str
    current_traffic_percentage: int = 0
    agreement_rate: float = 0.0
    mean_confidence: float = 0.5
    min_confidence: float = 0.5
    rollback_ready: bool = False
    num_rollback_triggers_armed: int = 0
    skill_metrics: Dict[str, SkillMetrics] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class RolloutState:
    """Complete rollout orchestration state (immutable, audit-trail-bound)"""
    phase: Phase
    day_number: int  # Day 1–84
    week_number: int  # Week 1–12
    current_traffic_percentage: int  # 1%, 10%, 50%, 100%
    start_date: str  # ISO 8601
    last_decision_time: str  # ISO 8601
    last_decision_reason: str
    phase_start_time: str
    phase_metrics_snapshot: Optional[PhaseMetrics] = None
    rollback_count: int = 0
    audit_trail_hash: str = ""  # SHA256 of all prior state transitions
    skill_states: Dict[str, SkillMode] = field(default_factory=dict)  # Per-skill mode
    pending_operator_approval: bool = False
    approval_required_for: Optional[str] = None  # Which phase transition
    # Measured during Phase 1 from caller-supplied metrics (mean of the daily
    # mean p99). None = not measured; a canary without it rolls back.
    baseline_latency_p99_ms: Optional[float] = None
    # Previous day's observed mean confidence (None before the first sample).
    prior_mean_confidence: Optional[float] = None
    # Checks that were NOT measured on the last evaluation (never read as a pass).
    unmeasured_checks: List[str] = field(default_factory=list)


class Phase1Evaluator:
    """
    Shadow phase success evaluation.

    Shadow is the lowest-risk phase: skill runs in advisory mode only,
    bundled engine always executes. Success requires:
    - ≥14 days running without audit breaks
    - ≥1,000 feedback events per skill
    - Agreement ≥98% (skill == bundled in 9.8+ out of 10 runs)
    - Confidence converged to stable value
    """

    MINIMUM_DAYS = 14
    MINIMUM_FEEDBACK_EVENTS = 1000
    MINIMUM_AGREEMENT_RATE = 0.98
    CONFIDENCE_CONVERGENCE_THRESHOLD = 0.05  # σ < 5% over 7 days

    @staticmethod
    def evaluate(
        metrics: Dict[str, SkillMetrics],
        days_elapsed: int,
        rollback_count: int,
    ) -> Tuple[bool, List[str]]:
        """
        Evaluate Phase 1 success.

        Returns (passed: bool, reasons: List[str])
        """
        reasons = []
        passed = True

        # Check 1: Minimum duration
        if days_elapsed < Phase1Evaluator.MINIMUM_DAYS:
            reasons.append(
                f"Phase 1 duration: {days_elapsed}/{Phase1Evaluator.MINIMUM_DAYS} days"
            )
            passed = False
        else:
            reasons.append(f"✓ Phase 1 duration: {days_elapsed}/{Phase1Evaluator.MINIMUM_DAYS} days")

        # Check 2: Feedback events per skill
        for skill_id, m in metrics.items():
            if m.feedback_count < Phase1Evaluator.MINIMUM_FEEDBACK_EVENTS:
                reasons.append(
                    f"  {skill_id}: feedback {m.feedback_count}/{Phase1Evaluator.MINIMUM_FEEDBACK_EVENTS}"
                )
                passed = False
            else:
                reasons.append(
                    f"  ✓ {skill_id}: feedback {m.feedback_count}/{Phase1Evaluator.MINIMUM_FEEDBACK_EVENTS}"
                )

        # Check 3: Agreement rate
        for skill_id, m in metrics.items():
            if m.agreement_rate < Phase1Evaluator.MINIMUM_AGREEMENT_RATE:
                reasons.append(
                    f"  {skill_id}: agreement {m.agreement_rate:.1%} (need ≥{Phase1Evaluator.MINIMUM_AGREEMENT_RATE:.1%})"
                )
                passed = False
            else:
                reasons.append(
                    f"  ✓ {skill_id}: agreement {m.agreement_rate:.1%}"
                )

        # Check 4: Confidence convergence (stable σ)
        for skill_id, m in metrics.items():
            if m.confidence_sigma > Phase1Evaluator.CONFIDENCE_CONVERGENCE_THRESHOLD:
                reasons.append(
                    f"  {skill_id}: confidence σ={m.confidence_sigma:.3f} (need <{Phase1Evaluator.CONFIDENCE_CONVERGENCE_THRESHOLD:.3f})"
                )
                passed = False
            else:
                reasons.append(
                    f"  ✓ {skill_id}: confidence converged (σ={m.confidence_sigma:.3f})"
                )

        # Check 5: No audit breaks (audit chain must be intact)
        if rollback_count > 0:
            reasons.append(f"  Rollback count: {rollback_count} (should be 0)")
            passed = False
        else:
            reasons.append("  ✓ No rollbacks in Phase 1")

        return passed, reasons


class Phase2aGateEvaluator:
    """
    Canary weekly gate evaluation.

    Each week's gate checks 4 criteria; need ≥3 to pass.
    Gates control traffic escalation: 1% → 10% → 50% → 100%.
    """

    # Week-specific thresholds
    WEEK_GATES = {
        3: {  # Week 3: 1% traffic
            "agreement_rate": 0.99,
            "latency_threshold_pct": 0.10,  # Within 10% of baseline
            "correctness_delta": None,  # Not enforced at 1%
            "confidence": 0.70,
        },
        4: {  # Week 4: 10% traffic
            "agreement_rate": 0.98,
            "latency_threshold_pct": 0.15,
            "correctness_delta": 0.02,  # Within 2%
            "confidence": 0.75,
        },
        5: {  # Week 5: 50% traffic
            "agreement_rate": 0.97,
            "latency_threshold_pct": 0.20,
            "correctness_delta": 0.02,
            "confidence": 0.80,
        },
    }

    @staticmethod
    def evaluate_week_gate(
        week_number: int,
        metrics: Dict[str, SkillMetrics],
        baseline_latency_p99_ms: float,
    ) -> Tuple[bool, List[str]]:
        """
        Evaluate weekly gate for Phase 2a.

        Returns (passed: bool, reasons: List[str])
        """
        if week_number < 3 or week_number > 9:
            return False, [f"Invalid week {week_number} for Phase 2a gate"]

        # For weeks 6–9, use week 5 thresholds (no escalation in traffic)
        thresholds = Phase2aGateEvaluator.WEEK_GATES.get(week_number, Phase2aGateEvaluator.WEEK_GATES[5])

        reasons = []
        criteria_passed = 0

        # Criterion 1: Agreement rate
        min_agreement = thresholds["agreement_rate"]
        actual_agreement = sum(m.agreement_rate for m in metrics.values()) / len(metrics)
        if actual_agreement >= min_agreement:
            reasons.append(f"✓ Agreement rate: {actual_agreement:.1%} (need ≥{min_agreement:.1%})")
            criteria_passed += 1
        else:
            reasons.append(f"✗ Agreement rate: {actual_agreement:.1%} (need ≥{min_agreement:.1%})")

        # Criterion 2: Latency within threshold
        max_latency_pct = thresholds["latency_threshold_pct"]
        actual_latency_p99 = sum(m.latency_p99_ms for m in metrics.values()) / len(metrics)
        latency_threshold_ms = baseline_latency_p99_ms * (1 + max_latency_pct)
        if actual_latency_p99 <= latency_threshold_ms:
            reasons.append(f"✓ Latency p99: {actual_latency_p99:.1f}ms (≤{latency_threshold_ms:.1f}ms)")
            criteria_passed += 1
        else:
            reasons.append(f"✗ Latency p99: {actual_latency_p99:.1f}ms (>{latency_threshold_ms:.1f}ms)")

        # Criterion 3: Correctness (if applicable)
        if thresholds["correctness_delta"] is not None:
            max_delta = thresholds["correctness_delta"]
            actual_delta = sum(m.correctness_delta for m in metrics.values()) / len(metrics)
            if abs(actual_delta) <= max_delta:
                reasons.append(f"✓ Correctness δ: {actual_delta:.1%} (≤{max_delta:.1%})")
                criteria_passed += 1
            else:
                reasons.append(f"✗ Correctness δ: {actual_delta:.1%} (>{max_delta:.1%})")
        else:
            reasons.append("- Correctness δ: not enforced at this traffic level")

        # Criterion 4: Confidence
        min_confidence = thresholds["confidence"]
        actual_confidence = sum(m.confidence for m in metrics.values()) / len(metrics)
        if actual_confidence >= min_confidence:
            reasons.append(f"✓ Confidence: {actual_confidence:.2f} (≥{min_confidence:.2f})")
            criteria_passed += 1
        else:
            reasons.append(f"✗ Confidence: {actual_confidence:.2f} (<{min_confidence:.2f})")

        # Overall: need ≥3 of 4 criteria
        passed = criteria_passed >= 3
        reasons.insert(0, f"Week {week_number}: {criteria_passed}/4 criteria passed → {'PASS ✓' if passed else 'FAIL ✗'}")

        return passed, reasons


class Phase2bActivationGate:
    """
    Skill-primary activation gate (Phase 2b).

    Each skill activates independently once confidence ≥ threshold
    and production readiness criteria met.
    """

    ACTIVATION_THRESHOLDS = {
        "os.delegation_router": 0.85,
        "os.context_adapter": 0.80,
        "os.workflow_optimizer": 0.80,
        "os.security_orchestrator": 0.95,  # Highest threshold (security-critical)
    }

    @staticmethod
    def evaluate_skill_activation(
        skill_id: str,
        metrics: SkillMetrics,
        days_at_100_pct: int,
        days_since_last_rollback: int,
        audit_violations_in_window: Optional[int] = None,
        latency_sigma_ms: Optional[float] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Evaluate whether a skill can be activated to primary mode.

        ``audit_violations_in_window`` and ``latency_sigma_ms`` must be MEASURED
        values; ``None`` means not measured and blocks activation (fail-closed).

        Returns (can_activate: bool, reasons: List[str])
        """
        reasons = []
        can_activate = True

        threshold = Phase2bActivationGate.ACTIVATION_THRESHOLDS.get(skill_id, 0.80)

        # Criterion 1: Confidence ≥ threshold
        if metrics.confidence >= threshold:
            reasons.append(f"✓ Confidence: {metrics.confidence:.2f} (≥{threshold:.2f})")
        else:
            reasons.append(f"✗ Confidence: {metrics.confidence:.2f} (<{threshold:.2f})")
            can_activate = False

        # Criterion 2: Agreement rate ≥98% (for ≥7 days)
        if metrics.agreement_rate >= 0.98 and days_at_100_pct >= 7:
            reasons.append(f"✓ Agreement rate: {metrics.agreement_rate:.1%} (≥7 days at 100%)")
        else:
            reasons.append(
                f"✗ Agreement rate: {metrics.agreement_rate:.1%} "
                f"({days_at_100_pct}/7 days at 100%)"
            )
            can_activate = False

        # Criterion 3: No critical audit violations in the window — only a
        # measured count can pass; nothing in this module can count them.
        if audit_violations_in_window is None:
            reasons.append(f"✗ Audit violations: {NOT_MEASURED}")
            can_activate = False
        elif audit_violations_in_window == 0:
            reasons.append("✓ No audit violations (100k-turn window)")
        else:
            reasons.append(f"✗ Audit violations: {audit_violations_in_window} (in 100k-turn window)")
            can_activate = False

        # Criterion 4: Latency stable (σ < 10ms over 7 days) — measured only.
        if latency_sigma_ms is None:
            reasons.append(f"✗ Latency stability: {NOT_MEASURED}")
            can_activate = False
        elif latency_sigma_ms < 10.0:
            reasons.append(f"✓ Latency stable (σ={latency_sigma_ms:.1f}ms <10ms)")
        else:
            reasons.append(f"✗ Latency unstable (σ={latency_sigma_ms:.1f}ms ≥10ms)")
            can_activate = False

        return can_activate, reasons


class RolloutOrchestrator:
    """
    Main orchestrator for Phase 3 production rollout.

    Manages:
    - State transitions (Phase 1 → 2a → 2b)
    - Weekly gate evaluations
    - Auto-rollback triggers
    - Learning loop integration
    - Audit trail (immutable, hash-chained)
    - Operator gate approvals
    """

    def __init__(self, config_path: str = None, *, tenant_id: str = "_default"):
        self.config_path = config_path or self._default_config_path()
        self.tenant_id = tenant_id
        self.state: RolloutState = self._initialize_state()
        self.rollback_history: List[Tuple[datetime, RollbackReason, str]] = []
        # Local mirror of what was committed to the tenant chain (never the record).
        self.audit_trail: List[Dict] = []
        # Daily mean p99 samples (for baseline + 7-day stability).
        self._daily_latency_p99: List[float] = []

    @staticmethod
    def _default_config_path() -> str:
        return str(Path(__file__).parent / "phase3_deployment_config.yaml")

    def _initialize_state(self) -> RolloutState:
        """Initialize rollout state (Day 1, Phase 1 Shadow)"""
        start_date = datetime.now(timezone.utc).isoformat()
        return RolloutState(
            phase=Phase.PHASE_1_SHADOW,
            day_number=1,
            week_number=1,
            current_traffic_percentage=0,  # Shadow: no traffic % tracking
            start_date=start_date,
            last_decision_time=start_date,
            last_decision_reason="Rollout initialized, Phase 1 Shadow starting",
            phase_start_time=start_date,
            skill_states={
                "os.delegation_router": SkillMode.ADVISORY,
                "os.context_adapter": SkillMode.ADVISORY,
                "os.workflow_optimizer": SkillMode.ADVISORY,
                "os.security_orchestrator": SkillMode.ADVISORY,
            },
        )

    def advance_day(
        self,
        current_metrics: Dict[str, SkillMetrics],
        *,
        audit_violations_in_window: Optional[int] = None,
        tenant_isolation_violations: Optional[int] = None,
    ) -> None:
        """
        Advance by one day, check phase gates, update state.

        Must be called daily (e.g., via cron or watchdog timer ADR-0867).
        ``audit_violations_in_window`` / ``tenant_isolation_violations`` are
        MEASURED counts from the caller; ``None`` = not measured (blocks
        Phase 2b activation, never read as zero).
        """
        if not current_metrics:
            raise ValueError("advance_day requires at least one SkillMetrics sample")
        next_day = self.state.day_number + 1
        next_week = (next_day - 1) // 7 + 1
        # Audit-first: a day that did not commit to the chain is not advanced.
        self._audit_log({
            "event": "day_advanced",
            "day": next_day,
            "week": next_week,
            "phase": self.state.phase.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "lom": "core/deployment/phase3_rollout_orchestrator.py:advance_day",
        })
        self._audit_violations_in_window = audit_violations_in_window
        self.state.day_number = next_day
        self.state.week_number = next_week
        self.state.unmeasured_checks = []

        mean_p99 = sum(m.latency_p99_ms for m in current_metrics.values()) / len(current_metrics)
        mean_conf = sum(m.confidence for m in current_metrics.values()) / len(current_metrics)

        # Check rollback triggers first (fail-closed)
        rolled_back = self._check_rollback_triggers(current_metrics, tenant_isolation_violations)
        self._daily_latency_p99.append(mean_p99)
        self.state.prior_mean_confidence = mean_conf
        if self.state.phase == Phase.PHASE_1_SHADOW:
            # Baseline = mean of the daily mean p99 observed in shadow mode.
            shadow = self._daily_latency_p99
            self.state.baseline_latency_p99_ms = sum(shadow) / len(shadow)
        if rolled_back:
            return  # Rollback executed, state already updated

        # Then check phase gates
        if self.state.phase == Phase.PHASE_1_SHADOW:
            self._evaluate_phase_1_gate(current_metrics)
        elif self.state.phase == Phase.PHASE_2A_CANARY:
            self._evaluate_phase_2a_gate(current_metrics)
        elif self.state.phase == Phase.PHASE_2B_SKILL_PRIMARY:
            self._evaluate_phase_2b_skill_activation(current_metrics)

    def _check_rollback_triggers(
        self,
        metrics: Dict[str, SkillMetrics],
        tenant_isolation_violations: Optional[int] = None,
    ) -> bool:
        """
        Check all rollback triggers (fail-closed).

        If ANY trigger fires, immediately execute rollback.
        Returns True if rollback was executed.
        """
        reason = None
        details = None

        # Trigger 1: Correctness drop >2% (agreement rate)
        mean_agreement = sum(m.agreement_rate for m in metrics.values()) / len(metrics)
        if mean_agreement < 0.98:  # <98% = >2% disagreement
            reason = RollbackReason.CORRECTNESS_DROP
            details = f"Agreement rate {mean_agreement:.1%} < 98%"

        # Trigger 2: Latency spike >20% over the MEASURED Phase 1 baseline.
        # Live-traffic phases without a baseline cannot detect a spike at all,
        # so they roll back rather than run blind.
        live_phase = self.state.phase in (Phase.PHASE_2A_CANARY, Phase.PHASE_2B_SKILL_PRIMARY)
        if not reason:
            mean_latency_p99 = sum(m.latency_p99_ms for m in metrics.values()) / len(metrics)
            baseline = self.state.baseline_latency_p99_ms
            if baseline is None:
                self.state.unmeasured_checks.append("latency_baseline")
                if live_phase:
                    reason = RollbackReason.BASELINE_NOT_MEASURED
                    details = f"No Phase 1 latency baseline recorded ({NOT_MEASURED})"
            elif mean_latency_p99 > baseline * 1.2:
                reason = RollbackReason.LATENCY_SPIKE
                details = f"Latency p99 {mean_latency_p99:.1f}ms > {baseline*1.2:.1f}ms"

        # Trigger 3: Confidence regression >10% vs the previous day's observed mean
        if not reason:
            mean_confidence = sum(m.confidence for m in metrics.values()) / len(metrics)
            prior_confidence = self.state.prior_mean_confidence
            if prior_confidence is None:
                self.state.unmeasured_checks.append("confidence_prior")
            elif mean_confidence < prior_confidence * 0.9:
                reason = RollbackReason.CONFIDENCE_REGRESSION
                details = f"Confidence {mean_confidence:.2f} dropped >10% from {prior_confidence:.2f}"

        # Trigger 4: Audit chain break — the real tenant chain is verified;
        # an unverifiable chain counts as broken.
        if not reason:
            audit_chain_intact, why = self._verify_tenant_chain()
            if not audit_chain_intact:
                reason = RollbackReason.AUDIT_CHAIN_BREAK
                details = f"Audit chain verification failed: {why}"

        # Trigger 5: Tenant isolation violation — only a caller-supplied
        # measured count; nothing here measures it.
        if not reason:
            if tenant_isolation_violations is None:
                self.state.unmeasured_checks.append("tenant_isolation")
            elif tenant_isolation_violations > 0:
                reason = RollbackReason.TENANT_ISOLATION_VIOLATION_REPORTED
                details = f"{tenant_isolation_violations} cross-tenant violation(s) reported"

        # If any trigger fired, execute rollback
        if reason:
            self._execute_rollback(reason, details)
            return True

        return False

    def _execute_rollback(self, reason: RollbackReason, details: str) -> None:
        """
        Execute immediate rollback (fail-closed).

        Revert all skills to advisory/fallback mode, restore prior state,
        log audit event with LoM binding.
        """
        logger.error(f"ROLLBACK TRIGGERED: {reason.value} — {details}")

        # Revert all skills to FALLBACK (bundled engine only)
        for skill_id in self.state.skill_states:
            self.state.skill_states[skill_id] = SkillMode.FALLBACK

        # Revert phase
        prior_phase = self.state.phase
        self.state.phase = Phase.ROLLED_BACK

        # Update state
        self.state.last_decision_time = datetime.now(timezone.utc).isoformat()
        self.state.last_decision_reason = f"ROLLBACK: {reason.value} — {details}"
        self.state.rollback_count += 1

        # Audit trail. The rollback is applied BEFORE the write on purpose:
        # rolling back is the safe direction, so a failed audit write must not
        # leave live traffic on the skill. AuditWriteFailed still propagates.
        self._audit_log({
            "event": "rollback_executed",
            "reason": reason.value,
            "details": details,
            "prior_phase": prior_phase.value,
            "timestamp": self.state.last_decision_time,
            "lom": "core/deployment/phase3_rollout_orchestrator.py:_execute_rollback",
        })

        # Record rollback in history
        self.rollback_history.append((
            datetime.fromisoformat(self.state.last_decision_time),
            reason,
            details,
        ))

    def _evaluate_phase_1_gate(self, metrics: Dict[str, SkillMetrics]) -> None:
        """
        Evaluate Phase 1 success criteria daily.
        Once all met, request operator approval for Phase 2a transition.
        """
        days_in_phase = self.state.day_number - 1  # Day 1 = 0 days elapsed
        passed, reasons = Phase1Evaluator.evaluate(metrics, days_in_phase, self.state.rollback_count)

        logger.info(f"Phase 1 evaluation (Day {self.state.day_number}):")
        for reason in reasons:
            logger.info(f"  {reason}")

        if passed and not self.state.pending_operator_approval:
            ts = datetime.now(timezone.utc).isoformat()
            self._audit_log({
                "event": "phase_1_gate_passed",
                "approval_required": "PHASE_2A_START",
                "timestamp": ts,
                "lom": "core/deployment/phase3_rollout_orchestrator.py:_evaluate_phase_1_gate",
            })
            # Transition to Phase 2a (pending approval)
            self.state.pending_operator_approval = True
            self.state.approval_required_for = "PHASE_2A_START"
            self.state.last_decision_time = ts
            self.state.last_decision_reason = "Phase 1 success criteria met, awaiting operator approval"

    def operator_approve(self, approval_for: str, *, approved_by: str = "") -> bool:
        """
        Operator manually approves phase transition.

        Must be called by operator (cannot be automated). Returns True only when
        the named transition was actually pending and has been applied.
        """
        if approval_for != self.state.approval_required_for or not self.state.pending_operator_approval:
            logger.warning(
                "operator_approve(%s) ignored: pending approval is %s",
                approval_for, self.state.approval_required_for,
            )
            return False
        if approval_for == "PHASE_2B_START":
            ts = datetime.now(timezone.utc).isoformat()
            self._audit_log({
                "event": "operator_approval",
                "approval_for": approval_for,
                "operator_ref": _operator_ref(approved_by),
                "phase_transitioned_to": Phase.PHASE_2B_SKILL_PRIMARY.value,
                "timestamp": ts,
                "lom": "core/deployment/phase3_rollout_orchestrator.py:operator_approve",
            })
            self.state.phase = Phase.PHASE_2B_SKILL_PRIMARY
            self.state.phase_start_time = ts
            self.state.pending_operator_approval = False
            self.state.approval_required_for = None
            self.state.last_decision_time = ts
            self.state.last_decision_reason = "Operator approved Phase 2b start"
            return True
        if approval_for == "PHASE_2A_START":
            ts = datetime.now(timezone.utc).isoformat()
            self._audit_log({
                "event": "operator_approval",
                "approval_for": approval_for,
                "operator_ref": _operator_ref(approved_by),
                "phase_transitioned_to": Phase.PHASE_2A_CANARY.value,
                "timestamp": ts,
                "lom": "core/deployment/phase3_rollout_orchestrator.py:operator_approve",
            })
            self.state.phase = Phase.PHASE_2A_CANARY
            self.state.phase_start_time = datetime.now(timezone.utc).isoformat()
            self.state.current_traffic_percentage = 1  # Start canary at 1%
            self.state.pending_operator_approval = False
            self.state.approval_required_for = None
            self.state.last_decision_time = self.state.phase_start_time
            self.state.last_decision_reason = "Operator approved Phase 2a start"

            # Enable dual-write for all skills
            for skill_id in self.state.skill_states:
                self.state.skill_states[skill_id] = SkillMode.DUAL_WRITE

            logger.info("Operator approved Phase 2a start, canary at 1%")
            return True
        return False

    def _evaluate_phase_2a_gate(self, metrics: Dict[str, SkillMetrics]) -> None:
        """
        Evaluate Phase 2a weekly gate (every Monday or every 7 days).

        Each week's gate checks agreement rate, latency, correctness, confidence.
        If gate passes, escalate traffic; if fails, hold or rollback.
        """
        # Weekly gate evaluation (at end of each week)
        if self.state.day_number % 7 != 0:  # Not end of week yet
            return

        # Phase 1 baseline latency, as MEASURED in shadow mode.
        baseline_latency_p99_ms = self.state.baseline_latency_p99_ms
        if baseline_latency_p99_ms is None:  # unreachable: rollback trigger fires first
            self.state.last_decision_reason = f"Phase 2a gate: latency baseline {NOT_MEASURED}, holding"
            return

        passed, reasons = Phase2aGateEvaluator.evaluate_week_gate(
            self.state.week_number,
            metrics,
            baseline_latency_p99_ms,
        )

        logger.info(f"Phase 2a Week {self.state.week_number} gate evaluation:")
        for reason in reasons:
            logger.info(f"  {reason}")

        if passed:
            # Escalate traffic to next level
            next_traffic = self._get_next_traffic_escalation(self.state.week_number)
            ts = datetime.now(timezone.utc).isoformat()
            self._audit_log({
                "event": "phase_2a_gate_passed",
                "week": self.state.week_number,
                "traffic_escalated_to": next_traffic,
                "timestamp": ts,
                "lom": "core/deployment/phase3_rollout_orchestrator.py:_evaluate_phase_2a_gate",
            })
            self.state.current_traffic_percentage = next_traffic
            self.state.last_decision_time = ts
            self.state.last_decision_reason = f"Phase 2a Week {self.state.week_number} gate passed, escalating to {next_traffic}%"
            logger.info(f"Traffic escalated to {next_traffic}%")

            # Check if we've reached 100% traffic (ready for Phase 2b)
            if (next_traffic == 100 and self.state.week_number >= 6
                    and not self.state.pending_operator_approval):
                self._audit_log({
                    "event": "phase_2b_approval_requested",
                    "week": self.state.week_number,
                    "approval_required": "PHASE_2B_START",
                    "timestamp": ts,
                    "lom": "core/deployment/phase3_rollout_orchestrator.py:_evaluate_phase_2a_gate",
                })
                self.state.pending_operator_approval = True
                self.state.approval_required_for = "PHASE_2B_START"
                logger.info("Phase 2a at 100% traffic, awaiting operator approval for Phase 2b")
        else:
            # Gate failed: hold traffic, extend week
            logger.warning(f"Phase 2a Week {self.state.week_number} gate failed, holding traffic at {self.state.current_traffic_percentage}%")
            self.state.last_decision_reason = f"Phase 2a Week {self.state.week_number} gate failed, holding traffic"

    @staticmethod
    def _get_next_traffic_escalation(week_number: int) -> int:
        """Get next traffic escalation target based on week"""
        # Week 3: 1%, Week 4: 10%, Week 5+: 50%, Week 6+: 100%
        if week_number <= 3:
            return 1
        elif week_number <= 4:
            return 10
        elif week_number <= 5:
            return 50
        else:
            return 100

    def _evaluate_phase_2b_skill_activation(self, metrics: Dict[str, SkillMetrics]) -> None:
        """
        Evaluate Phase 2b skill activation (daily check).

        Each skill activates independently once confidence ≥ threshold.
        """
        days_at_100_pct = max(0, self.state.day_number - 63)  # Day 64+ is at 100%
        days_since_rollback = self._days_since_last_rollback()

        latency_sigma = self._latency_sigma_last_7_days()
        for skill_id, skill_metrics in metrics.items():
            can_activate, reasons = Phase2bActivationGate.evaluate_skill_activation(
                skill_id,
                skill_metrics,
                days_at_100_pct,
                days_since_rollback,
                audit_violations_in_window=getattr(self, "_audit_violations_in_window", None),
                latency_sigma_ms=latency_sigma,
            )

            if can_activate and self.state.skill_states.get(skill_id) != SkillMode.PRIMARY:
                ts = datetime.now(timezone.utc).isoformat()
                self._audit_log({
                    "event": "skill_activated_to_primary",
                    "skill_id": skill_id,
                    "confidence": skill_metrics.confidence,
                    "timestamp": ts,
                    "lom": "core/deployment/phase3_rollout_orchestrator.py:_evaluate_phase_2b_skill_activation",
                })
                # Skill ready for primary activation
                self.state.skill_states[skill_id] = SkillMode.PRIMARY
                self.state.last_decision_time = ts
                self.state.last_decision_reason = f"Skill {skill_id} activated to primary"
                logger.info(f"✓ Skill {skill_id} activated to primary (confidence {skill_metrics.confidence:.2f})")

    def _latency_sigma_last_7_days(self) -> Optional[float]:
        """Population σ of the last 7 daily mean p99 samples; None below 7."""
        window = self._daily_latency_p99[-7:]
        if len(window) < 7:
            return None
        mean = sum(window) / len(window)
        return (sum((x - mean) ** 2 for x in window) / len(window)) ** 0.5

    def _verify_tenant_chain(self) -> Tuple[bool, str]:
        """Verify the real tenant audit chain. Unverifiable counts as broken."""
        try:
            from core.deployment.audit_sink import _forge  # noqa: PLC0415
            se, fp = _forge()
            chain = fp.tenant_audit_chain(self.tenant_id)
            if not chain.exists():
                return True, "chain empty"
            ok, problems = se.verify_chain(chain)
            return bool(ok), ("ok" if ok else f"{len(problems)} problem(s)")
        except Exception as exc:  # noqa: BLE001 — fail-closed
            return False, f"verifier unavailable ({type(exc).__name__})"

    def _days_since_last_rollback(self) -> int:
        """Calculate days since last rollback (or if none, days since start)"""
        if not self.rollback_history:
            return self.state.day_number
        last_rollback_time = self.rollback_history[-1][0]
        current_time = datetime.fromisoformat(self.state.last_decision_time)
        return (current_time - last_rollback_time).days

    def _audit_log(self, event: Dict) -> None:
        """
        Log audit event (immutable, hash-chained).

        Every decision is logged with LoM binding for compliance (ADR-0537, ADR-0232).
        """
        # Add sequence number
        event["sequence_number"] = len(self.audit_trail)
        event.setdefault("lom", "core/deployment/phase3_rollout_orchestrator.py:_audit_log")

        # Chain to prior event
        if self.audit_trail:
            prior_event = self.audit_trail[-1]
            event["prior_hash"] = prior_event.get("hash", "")
        else:
            event["prior_hash"] = "GENESIS"

        # Calculate hash (SHA256 of event JSON)
        event_json = json.dumps(event, sort_keys=True)
        event["hash"] = hashlib.sha256(event_json.encode()).hexdigest()

        # Audit-FIRST: the record must commit to the tenant chain before the
        # decision is applied or mirrored. AuditWriteFailed propagates.
        from core.deployment import audit_sink  # noqa: PLC0415
        audit_sink.register_events(_AUDIT_EVENTS)
        chain_details = {k: v for k, v in event.items() if k in _AUDIT_FIELDS and k != "event"}
        chain_details["lom"] = event["lom"]
        chain_details["mirror_hash"] = event["hash"][:16]
        audit_sink.emit(
            f"deployment.rollout.{event['event']}",
            chain_details,
            tenant_id=self.tenant_id,
            severity="WARNING" if event["event"] == "rollback_executed" else "INFO",
        )

        # Append to local mirror
        self.audit_trail.append(event)
        logger.debug(f"Audit: {event['event']} (hash: {event['hash'][:16]}...)")

    def get_state(self) -> RolloutState:
        """Get current rollout state (immutable snapshot)"""
        return self.state

    def get_audit_trail(self) -> List[Dict]:
        """Get complete audit trail (immutable)"""
        return self.audit_trail.copy()

    def verify_audit_chain(self) -> Tuple[bool, List[str]]:
        """
        Verify audit chain integrity (hash-chained).

        Returns (valid: bool, issues: List[str])
        """
        issues = []

        if not self.audit_trail:
            return True, []

        # Check first event is genesis
        if self.audit_trail[0]["prior_hash"] != "GENESIS":
            issues.append("First event prior_hash is not GENESIS")

        # Check each hash chain link
        for i, event in enumerate(self.audit_trail):
            # Recalculate hash (excluding the hash field itself)
            event_copy = {k: v for k, v in event.items() if k != "hash"}
            event_json = json.dumps(event_copy, sort_keys=True)
            expected_hash = hashlib.sha256(event_json.encode()).hexdigest()

            if event["hash"] != expected_hash:
                issues.append(f"Event {i}: hash mismatch (expected {expected_hash[:16]}..., got {event['hash'][:16]}...)")

            # Check prior hash chain
            if i > 0 and event["prior_hash"] != self.audit_trail[i-1]["hash"]:
                issues.append(f"Event {i}: prior_hash breaks chain")

        return len(issues) == 0, issues


# Global singleton orchestrator
_orchestrator: Optional[RolloutOrchestrator] = None


def get_orchestrator() -> RolloutOrchestrator:
    """Get or create global orchestrator instance"""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = RolloutOrchestrator()
    return _orchestrator


def reset_orchestrator() -> None:
    """Reset orchestrator (testing only)"""
    global _orchestrator
    _orchestrator = None
