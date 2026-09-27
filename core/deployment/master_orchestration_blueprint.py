"""
Master Orchestration Control Plane for 12-Week Production Rollout

Extends Phase3Orchestrator with complete 12-week phase management,
operator approval gates, automatic transitions, and state persistence.

ADRs: 0206 (canary), 0205 (learning), 0186 (heartbeat), 0369 (edge cases),
      0232 (audit tripwire), 0233 (audit integrity), 0537 (LoM binding)
Compliance: GDPR (Art. 5/6/30/32), EU AI Act (Art. 5/50), audit-first (ADR-0232/0233)

Load-bearing invariants:
1. State persistence — all transitions serializable for disaster recovery
2. Operator approval gates — Phase 1→2a (day 14) and Phase 2a→2b (day 42) require manual sign-off
3. Automatic phase progression — within-phase escalation (traffic 1%→10%→50%→100%) automatic
4. Fail-closed semantics — any gate failure locks phase until resolved
5. Immutable audit trail — every transition hash-chained with LoM binding (ADR-0537)
6. Tenant isolation — all state queries filtered by tenant_id (GDPR Art. 5/6)
7. Operator approval metrics — agreement_rate, confidence, latency, feedback_count snapshotted at approval time
8. Double-approval idempotency — UUID approval_id, no duplicate events
9. Audit trail persistent to disk — append-only, hash-chained (ADR-0232)
10. LoM cryptographic binding — sha256 of inspect.getsource for each operator_approve call

NOT WIRED: no production caller as of 2026-09-27 (adversarial review). No
service, route, timer or CLI constructs a MasterRolloutOrchestrator; its phase
and "traffic percentage" route nothing. Honesty contract:
- audit records go to ``tenant_audit_chain(tenant_id)`` through
  ``core.deployment.audit_sink`` BEFORE the decision they record is applied; a
  failed write raises ``AuditWriteFailed`` and the decision is not taken.
  ``audit_trail`` is only a local mirror of committed records (there is no
  second audit file any more — the old ``master_orchestrator_audit.jsonl`` was
  an unverified parallel chain).
- state lives under ``<corvin_home>/tenants/<tid>/global/orchestration/``
  (honours ``CORVIN_HOME``; one file per tenant), never a hard-wired ``~/.corvin``.
- a weekly gate without a MEASURED Phase 1 latency baseline FAILS (no 100 ms default).
- a persisted state whose hash does not verify is NOT loaded.
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Set
import json
import hashlib
import logging
from pathlib import Path
import threading
import time
import uuid
import inspect

from . import audit_sink
from .phase3_rollout_orchestrator import (
    RolloutOrchestrator,
    RolloutState,
    Phase,
    SkillMode,
    SkillMetrics,
    PhaseMetrics,
    RollbackReason,
)

logger = logging.getLogger(__name__)

_MASTER_FIELDS = frozenset({
    "gate", "approval_id", "day", "week", "phase", "traffic_pct", "operator_ref",
    "lom_hash", "decision", "transition", "agreement_rate", "confidence",
    "latency_p99_ms", "feedback_count", "sequence_number",
})
_MASTER_EVENTS = {
    f"deployment.master.{name}": _MASTER_FIELDS
    for name in (
        "day_advanced", "operator_approval_requested", "operator_approval_granted",
        "operator_approval_rejected", "automatic_transition", "approval_escalated_to_admin",
        "premature_phase_2a_reverted",
    )
}
audit_sink.register_events(_MASTER_EVENTS)


def _operator_ref(operator_id: Optional[str]) -> str:
    """Pseudonymous operator reference for the chain (never the raw id)."""
    return hashlib.sha256(str(operator_id).encode()).hexdigest()[:12] if operator_id else ""


class OperatorApprovalGate(Enum):
    """Operator approval checkpoints"""
    PHASE_1_TO_2A = "phase_1_to_2a"  # Day 14: proceed to canary? (F001)
    PHASE_2A_TO_2B = "phase_2a_to_2b"  # Day 42: 100% traffic for 7 days: proceed to skill-primary?


class AutomaticTransition(Enum):
    """Automatic (no operator approval) transitions"""
    TRAFFIC_1_PERCENT = "traffic_1_percent"  # Phase 2a Week 3: 1%
    TRAFFIC_10_PERCENT = "traffic_10_percent"  # Phase 2a Week 4: 10%
    TRAFFIC_50_PERCENT = "traffic_50_percent"  # Phase 2a Week 5: 50%
    TRAFFIC_100_PERCENT = "traffic_100_percent"  # Phase 2a Week 6+: 100%
    SKILL_ACTIVATION = "skill_activation"  # Phase 2b: activate skill to primary


class PhaseGateResult(Enum):
    """Phase gate evaluation result"""
    PASS = "pass"
    FAIL = "fail"
    HOLD = "hold"  # Awaiting metrics improvement


@dataclass
class WeeklyGateEvaluation:
    """Weekly gate evaluation snapshot"""
    week_number: int
    phase: Phase
    timestamp: str
    gate_result: PhaseGateResult
    metrics: Dict[str, float]
    criteria_passed: int
    criteria_total: int
    action_taken: str  # e.g., "escalate to 10%", "hold", "trigger_rollback"
    reason: str
    tenant_id: str = "_default"  # F023: Tenant isolation


@dataclass
class OperatorApprovalRecord:
    """Operator approval audit record (F002, F010, F011, F023)"""
    gate: OperatorApprovalGate
    requested_at: str
    approval_id: str = field(default_factory=lambda: str(uuid.uuid4()))  # F012: Idempotency
    approved_at: Optional[str] = None
    approved_by: Optional[str] = None
    decision: Optional[str] = None  # "approved", "rejected", "deferred"
    reason: str = ""
    audit_hash: str = ""
    lom_hash: str = ""  # F010: LoM cryptographic binding

    # F011: Operator Approval Metrics Snapshot
    agreement_rate_at_approval: Optional[float] = None
    confidence_at_approval: Optional[float] = None
    latency_p99_at_approval: Optional[float] = None
    feedback_count_at_approval: Optional[int] = None

    tenant_id: str = "_default"  # F023: Tenant isolation

    def is_idempotent_duplicate(self, other: 'OperatorApprovalRecord') -> bool:
        """F012: Check if this is an idempotent duplicate"""
        return (self.gate == other.gate and
                self.approval_id == other.approval_id and
                self.approved_by == other.approved_by)


@dataclass
class MasterOrchestratorState:
    """Extended state for 12-week master orchestration (F002)"""
    base_state: RolloutState

    # F023: Tenant isolation
    tenant_id: str = "_default"

    # Operator gates (F002, F012)
    operator_approvals: Dict[str, OperatorApprovalRecord] = field(default_factory=dict)
    processed_approval_ids: Set[str] = field(default_factory=set)  # F012: Track processed approval UUIDs

    # Weekly evaluations (indexed by week number)
    weekly_evaluations: Dict[int, WeeklyGateEvaluation] = field(default_factory=dict)

    # Automatic transitions executed
    automatic_transitions: List[Tuple[str, str, str]] = field(default_factory=list)  # (timestamp, transition, reason)

    # State serialization
    last_saved_time: str = ""
    last_saved_hash: str = ""
    state_version: int = 1  # F002: Version for state migration

    # Recovery checkpoint (disaster recovery)
    checkpoint_interval_hours: int = 1
    last_checkpoint_time: str = ""

    # F009: Audit trail persistence tracking
    audit_events_persisted: int = 0
    last_audit_persist_time: str = ""


class MasterRolloutOrchestrator:
    """
    Master Control Plane for 12-week production rollout.

    Manages complete orchestration lifecycle:
    - Phase 1 (Shadow): 14 days, advisory mode, baseline metrics collection
    - Phase 2a (Canary): 28 days, traffic escalation (1%→10%→50%→100%), weekly gates
    - Phase 2b (Skill-Primary): 42 days, per-skill activation, convergence
    - Production: stable, auto-optimization

    Extends Phase3Orchestrator with:
    - Operator approval checkpoints (F001, F019)
    - State persistence & recovery (F002, F009)
    - Weekly evaluation tracking
    - Automatic transition logging
    - Audit trail persistent to disk (F009)
    - LoM cryptographic binding (F010)
    - Operator approval metrics snapshot (F011)
    - Double-approval idempotency (F012, F014)
    - Phase 1 14-day minimum enforcement (F015)
    - Operator approval timeout (F019)
    - Tenant isolation (F023)
    """

    # F001: Phase gates at day 14 (Phase 1→2a) and day 42 (Phase 2a→2b)
    PHASE_1_APPROVAL_DAY = 14
    PHASE_2A_APPROVAL_DAY = 42

    # F019: Operator approval timeout at day 21
    APPROVAL_TIMEOUT_DAYS = 7

    def __init__(
        self,
        base_orchestrator: Optional[RolloutOrchestrator] = None,
        tenant_id: str = "_default",
        state_dir: Optional[Path] = None,
    ):
        self.tenant_id = tenant_id  # F023
        self.base_orch = base_orchestrator or RolloutOrchestrator(tenant_id=tenant_id)
        # Per-tenant state location under CORVIN_HOME (was a class-level
        # ``Path.home()/.corvin`` shared by every tenant and every test).
        if state_dir is None:
            from core.paths.tenant import tenant_home  # noqa: PLC0415
            state_dir = tenant_home(tenant_id) / "global" / "orchestration"
        self.STATE_FILE = Path(state_dir) / "master_orchestrator_state.json"
        self.CHECKPOINT_DIR = Path(state_dir) / "orchestrator_checkpoints"
        self.state = self._initialize_master_state()
        # Local mirror of records committed to the tenant chain.
        self.audit_trail: List[Dict] = []
        self.lock = threading.RLock()  # F014: Thread-safe audit trail
        self.audit_trail_lock = threading.RLock()  # F014: Separate lock for audit trail

        # F012: Track processed approval IDs to prevent duplicates
        self.processed_approval_ids: Set[str] = set()

        self.CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

        # F002: Load persisted state
        self._load_persisted_state()

        logger.info(f"MasterRolloutOrchestrator initialized, phase: {self.state.base_state.phase.value}, tenant: {self.tenant_id}")

    def _initialize_master_state(self) -> MasterOrchestratorState:
        """Initialize master orchestration state (F023)"""
        return MasterOrchestratorState(
            base_state=self.base_orch.state,
            tenant_id=self.tenant_id,
            last_saved_time=datetime.now(timezone.utc).isoformat(),
        )

    def advance_day(self, current_metrics: Dict[str, SkillMetrics]) -> None:
        """
        Advance rollout by one day (automated, run daily via cron/watchdog).

        - Check rollback triggers (fail-closed)
        - Evaluate phase gates (F001: day 14 and day 42)
        - Trigger automatic transitions if gates pass
        - Check approval timeouts (F019)
        - Save state to disk
        - Emit audit events (F009: persisted)
        """
        with self.lock:
            # F023: Tenant isolation check
            if not self._validate_tenant_isolation():
                logger.error(f"Tenant isolation validation failed for tenant {self.tenant_id}")
                return

            # F015: Phase 1 14-Day Minimum Enforcement. The old code only
            # returned early and left the rollout IN canary; now it is reverted.
            day_num = self.state.base_state.day_number
            if self.state.base_state.phase == Phase.PHASE_2A_CANARY and day_num < self.PHASE_1_APPROVAL_DAY:
                logger.warning("Phase 2a before day 14 — reverting to Phase 1 shadow")
                self._audit_log({
                    "event": "premature_phase_2a_reverted",
                    "day": day_num,
                    "phase": Phase.PHASE_1_SHADOW.value,
                    "lom": "core/deployment/master_orchestration_blueprint.py:advance_day",
                })
                self.state.base_state.phase = Phase.PHASE_1_SHADOW
                self.state.base_state.current_traffic_percentage = 0
                for skill_id in self.state.base_state.skill_states:
                    self.state.base_state.skill_states[skill_id] = SkillMode.ADVISORY
                self._save_state()
                return

            # Delegate base metrics to Phase3Orchestrator
            self.base_orch.advance_day(current_metrics)

            # Update extended state
            self.state.base_state = self.base_orch.state

            # Check for phase-specific gates
            day_num = self.state.base_state.day_number
            week_num = self.state.base_state.week_number
            phase = self.state.base_state.phase

            # F001: Phase 1→2a approval gate at day 14
            if phase == Phase.PHASE_1_SHADOW and day_num == self.PHASE_1_APPROVAL_DAY:
                self._request_operator_approval(OperatorApprovalGate.PHASE_1_TO_2A, current_metrics)

            # Phase 2a→2b approval gate at day 42
            if phase == Phase.PHASE_2A_CANARY and day_num == self.PHASE_2A_APPROVAL_DAY:
                if self.state.base_state.current_traffic_percentage >= 100:
                    self._request_operator_approval(OperatorApprovalGate.PHASE_2A_TO_2B, current_metrics)

            # Evaluate weekly gate if Phase 2a and end of week
            if phase == Phase.PHASE_2A_CANARY and day_num % 7 == 0:
                self._evaluate_weekly_gate(week_num, current_metrics)

            # F019: Check for approval timeout (day 21 for Phase 1→2a)
            self._check_approval_timeouts()

            # Check automatic transitions
            self._check_automatic_transitions()

            # Emit audit event, then persist state
            self._audit_log({
                "event": "day_advanced",
                "day": day_num,
                "week": week_num,
                "phase": phase.value,
                "traffic_pct": self.state.base_state.current_traffic_percentage,
            })
            self._save_state()

    def _evaluate_weekly_gate(self, week_num: int, metrics: Dict[str, SkillMetrics]) -> None:
        """
        Evaluate weekly gate for Phase 2a.

        If gate passes, prepare automatic transition for next traffic level.
        """
        from .phase3_rollout_orchestrator import Phase2aGateEvaluator

        # Phase 1 baseline as MEASURED by the base orchestrator in shadow mode.
        # Not measured → the gate FAILS (was an invented 100 ms).
        baseline_latency_ms = self.state.base_state.baseline_latency_p99_ms
        if baseline_latency_ms is None:
            passed, reasons = False, ["✗ Latency baseline: not_measured (no Phase 1 baseline recorded)"]
        else:
            passed, reasons = Phase2aGateEvaluator.evaluate_week_gate(
                week_num,
                metrics,
                baseline_latency_ms,
            )

        # Record evaluation
        gate_result = PhaseGateResult.PASS if passed else PhaseGateResult.FAIL
        criteria_passed = sum(1 for r in reasons if r.startswith("✓"))

        evaluation = WeeklyGateEvaluation(
            week_number=week_num,
            phase=Phase.PHASE_2A_CANARY,
            timestamp=datetime.now(timezone.utc).isoformat(),
            gate_result=gate_result,
            metrics={
                "agreement": sum(m.agreement_rate for m in metrics.values()) / len(metrics),
                "latency_p99": sum(m.latency_p99_ms for m in metrics.values()) / len(metrics),
                "confidence": sum(m.confidence for m in metrics.values()) / len(metrics),
            },
            criteria_passed=criteria_passed,
            criteria_total=4,
            action_taken="hold",
            reason="; ".join(reasons),
        )

        self.state.weekly_evaluations[week_num] = evaluation

        logger.info(f"Phase 2a Week {week_num} gate: {gate_result.value.upper()}")
        for reason in reasons:
            logger.info(f"  {reason}")

        if passed:
            # Schedule automatic transition
            next_traffic = self._get_next_traffic_escalation(week_num)
            if next_traffic != self.state.base_state.current_traffic_percentage:
                action = f"escalate_to_{next_traffic}_percent"
                evaluation.action_taken = action
                self._record_automatic_transition(action, f"Week {week_num} gate passed")

            # Check if ready for Phase 2b approval
            if next_traffic == 100 and week_num >= 6:
                self._request_operator_approval(OperatorApprovalGate.PHASE_2A_TO_2B, metrics)

    def _check_automatic_transitions(self) -> None:
        """Check and execute pending automatic transitions"""
        phase = self.state.base_state.phase
        week = self.state.base_state.week_number

        # Phase 2a traffic escalation (automatic, no approval needed)
        if phase == Phase.PHASE_2A_CANARY:
            current_traffic = self.state.base_state.current_traffic_percentage
            target_traffic = self._get_next_traffic_escalation(week)

            if target_traffic > current_traffic:
                # Check that weekly gate passed
                if week in self.state.weekly_evaluations:
                    eval_result = self.state.weekly_evaluations[week]
                    if eval_result.gate_result == PhaseGateResult.PASS:
                        self.state.base_state.current_traffic_percentage = target_traffic
                        self._record_automatic_transition(
                            f"escalate_to_{target_traffic}_percent",
                            f"Automatic escalation in week {week}",
                        )

    def _request_operator_approval(
        self,
        gate: OperatorApprovalGate,
        current_metrics: Optional[Dict[str, SkillMetrics]] = None,
    ) -> None:
        """
        Request operator approval for phase transition (F001, F011).

        Sets pending_operator_approval flag and records approval request with metrics snapshot (F011).
        """
        record = OperatorApprovalRecord(
            gate=gate,
            requested_at=datetime.now(timezone.utc).isoformat(),
            tenant_id=self.tenant_id,  # F023
            decision=None,
        )

        # F011: Capture metrics at approval request time
        if current_metrics:
            record.agreement_rate_at_approval = sum(m.agreement_rate for m in current_metrics.values()) / len(current_metrics) if current_metrics else None
            record.confidence_at_approval = sum(m.confidence for m in current_metrics.values()) / len(current_metrics) if current_metrics else None
            record.latency_p99_at_approval = sum(m.latency_p99_ms for m in current_metrics.values()) / len(current_metrics) if current_metrics else None
            record.feedback_count_at_approval = sum(int(m.feedback_count) for m in current_metrics.values()) if current_metrics else None

        # Audit-first: the request is recorded before it becomes pending.
        self._audit_log({
            "event": "operator_approval_requested",
            "gate": gate.value,
            "approval_id": record.approval_id,  # F012
            # F011 metrics snapshot (None = not measured at request time)
            "agreement_rate": record.agreement_rate_at_approval,
            "confidence": record.confidence_at_approval,
            "latency_p99_ms": record.latency_p99_at_approval,
            "feedback_count": record.feedback_count_at_approval,
            "lom": "core/deployment/master_orchestration_blueprint.py:_request_operator_approval",
        })
        self.state.operator_approvals[gate.value] = record
        self.state.base_state.pending_operator_approval = True
        self.state.base_state.approval_required_for = gate.value

        logger.warning(f"OPERATOR APPROVAL REQUESTED: {gate.value} (approval_id: {record.approval_id})")

    def operator_approve(self, gate: OperatorApprovalGate, approved_by: str, reason: str = "") -> bool:
        """
        Operator approves phase transition (manual action).

        Implements F010 (LoM cryptographic binding), F012 (idempotency), F014 (thread-safety).

        Returns True if approval was recorded successfully.
        """
        with self.lock:
            if gate.value not in self.state.operator_approvals:
                logger.error(f"No pending approval for gate {gate.value}")
                return False

            record = self.state.operator_approvals[gate.value]

            # F012: Check for idempotent duplicate
            if record.approval_id in self.processed_approval_ids:
                logger.warning(f"Idempotent duplicate approval detected: {record.approval_id}")
                return True  # Already processed

            # F023: Verify tenant isolation
            if record.tenant_id != self.tenant_id:
                logger.error(f"Tenant mismatch in operator_approve: {record.tenant_id} vs {self.tenant_id}")
                return False

            # A rejected (or otherwise decided) request is closed; approving it
            # would resurrect a transition the operator already turned down.
            if record.decision is not None:
                logger.error(f"Approval {record.approval_id} already decided ({record.decision})")
                return False

            if not approved_by:
                logger.error("operator_approve requires approved_by")
                return False

            # F010: LoM cryptographic binding
            lom_hash = hashlib.sha256(inspect.getsource(self.operator_approve).encode()).hexdigest()

            # Audit-FIRST (F014: under the audit lock): no committed record → no transition.
            with self.audit_trail_lock:
                self._audit_log({
                    "event": "operator_approval_granted",
                    "gate": gate.value,
                    "approval_id": record.approval_id,  # F012
                    "operator_ref": _operator_ref(approved_by),
                    "lom_hash": lom_hash,  # F010
                    "lom": "core/deployment/master_orchestration_blueprint.py:operator_approve",
                })

            record.approved_at = datetime.now(timezone.utc).isoformat()
            record.approved_by = approved_by
            record.decision = "approved"
            record.reason = reason
            record.lom_hash = lom_hash

            # Execute phase transition
            if gate == OperatorApprovalGate.PHASE_1_TO_2A:
                self._transition_phase_1_to_2a()
            elif gate == OperatorApprovalGate.PHASE_2A_TO_2B:
                self._transition_phase_2a_to_2b()

            # F012: Mark this approval ID as processed
            self.processed_approval_ids.add(record.approval_id)
            self.state.processed_approval_ids.add(record.approval_id)

            logger.info(f"✓ OPERATOR APPROVED: {gate.value} (by {approved_by}, approval_id: {record.approval_id})")
            return True

    def operator_reject(self, gate: OperatorApprovalGate, rejected_by: str, reason: str) -> bool:
        """
        Operator rejects phase transition.

        Returns True if rejection was recorded.
        """
        with self.lock:
            if gate.value not in self.state.operator_approvals:
                logger.error(f"No pending approval for gate {gate.value}")
                return False

            record = self.state.operator_approvals[gate.value]
            if record.decision is not None:
                logger.error(f"Approval {record.approval_id} already decided ({record.decision})")
                return False

            self._audit_log({
                "event": "operator_approval_rejected",
                "gate": gate.value,
                "approval_id": record.approval_id,
                "operator_ref": _operator_ref(rejected_by),
                "lom": "core/deployment/master_orchestration_blueprint.py:operator_reject",
            })
            record.approved_at = datetime.now(timezone.utc).isoformat()
            record.approved_by = rejected_by
            record.decision = "rejected"
            record.reason = reason

            self.state.base_state.pending_operator_approval = False
            self.state.base_state.approval_required_for = None

            logger.warning(f"OPERATOR REJECTED: {gate.value} ({reason})")
            return True

    def _transition_phase_1_to_2a(self) -> None:
        """Execute Phase 1 → Phase 2a transition"""
        # F037: Don't call base_orch.operator_approve (incompatible signature)
        # Instead, directly execute the transition in the master state

        self.state.base_state.phase = Phase.PHASE_2A_CANARY
        self.state.base_state.phase_start_time = datetime.now(timezone.utc).isoformat()
        self.state.base_state.current_traffic_percentage = 1  # Start canary at 1%
        self.state.base_state.pending_operator_approval = False
        self.state.base_state.approval_required_for = None
        self.state.base_state.last_decision_time = self.state.base_state.phase_start_time
        self.state.base_state.last_decision_reason = "Operator approved Phase 1→2a transition"

        # Enable dual-write for all skills
        for skill_id in self.state.base_state.skill_states:
            self.state.base_state.skill_states[skill_id] = SkillMode.DUAL_WRITE

        logger.info("Phase 1 → Phase 2a transition executed")

    def _transition_phase_2a_to_2b(self) -> None:
        """Execute Phase 2a → Phase 2b transition"""
        self.state.base_state.phase = Phase.PHASE_2B_SKILL_PRIMARY
        self.state.base_state.phase_start_time = datetime.now(timezone.utc).isoformat()
        self.state.base_state.pending_operator_approval = False
        self.state.base_state.approval_required_for = None

        logger.info("Phase 2a → Phase 2b transition executed")

    def _record_automatic_transition(self, transition: str, reason: str) -> None:
        """Record automatic transition in audit trail"""
        timestamp = datetime.now(timezone.utc).isoformat()
        self._audit_log({
            "event": "automatic_transition",
            "transition": transition,
            "lom": "core/deployment/master_orchestration_blueprint.py:_record_automatic_transition",
        })
        self.state.automatic_transitions.append((timestamp, transition, reason))

        logger.info(f"Automatic transition: {transition} ({reason})")

    @staticmethod
    def _get_next_traffic_escalation(week_number: int) -> int:
        """Get next traffic escalation target based on week"""
        if week_number <= 3:
            return 1
        elif week_number <= 4:
            return 10
        elif week_number <= 5:
            return 50
        else:
            return 100

    def _save_state(self) -> bool:
        """
        Persist master orchestrator state to disk (fail-closed, F002, F023).

        Saves JSON snapshot with hash chaining for integrity verification.
        Also creates timestamped checkpoint for disaster recovery.
        """
        try:
            # F002, F023: Serialize complete state with tenant isolation
            state_dict = {
                "tenant_id": self.tenant_id,
                "state_version": self.state.state_version,
                "base_state": asdict(self.state.base_state),
                "operator_approvals": {
                    k: asdict(v) for k, v in self.state.operator_approvals.items()
                },
                "weekly_evaluations": {
                    k: asdict(v) for k, v in self.state.weekly_evaluations.items()
                },
                "automatic_transitions": self.state.automatic_transitions,
                "processed_approval_ids": list(self.state.processed_approval_ids),  # F012
                "audit_events_persisted": self.state.audit_events_persisted,  # F009
                "last_audit_persist_time": self.state.last_audit_persist_time,  # F009
                "last_saved_time": datetime.now(timezone.utc).isoformat(),
            }

            # Calculate state hash
            state_json = json.dumps(state_dict, sort_keys=True, default=str)
            state_hash = hashlib.sha256(state_json.encode()).hexdigest()
            state_dict["state_hash"] = state_hash

            # Save to main file
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self.STATE_FILE, "w") as f:
                json.dump(state_dict, f, indent=2, default=str)

            # Create timestamped checkpoint
            checkpoint_path = self.CHECKPOINT_DIR / f"state_{state_dict['last_saved_time'].replace(':', '-')}.json"
            with open(checkpoint_path, "w") as f:
                json.dump(state_dict, f, indent=2, default=str)

            self.state.last_saved_time = state_dict["last_saved_time"]
            self.state.last_saved_hash = state_hash
            return True

        except Exception as e:
            logger.error(f"Failed to save state: {type(e).__name__}")
            return False

    def _load_persisted_state(self) -> None:
        """
        Load master orchestrator state from disk (F002, F023).

        Reconstructs complete state including RolloutState, operator_approvals,
        weekly_evaluations, and automatic_transitions.
        """
        if not self.STATE_FILE.exists():
            logger.info("No persisted state found, using initialized state")
            return

        try:
            with open(self.STATE_FILE, "r") as f:
                state_dict = json.load(f)

            # Verify hash
            stored_hash = state_dict.pop("state_hash", "")
            state_json = json.dumps(state_dict, sort_keys=True, default=str)
            calculated_hash = hashlib.sha256(state_json.encode()).hexdigest()

            if not stored_hash or stored_hash != calculated_hash:
                # Fail-closed: a state file that does not verify could claim any
                # phase (e.g. skill-primary). Do not load it.
                logger.error("Persisted state hash missing/mismatch — NOT loaded (integrity)")
                return

            # F023: Verify tenant isolation
            persisted_tenant = state_dict.get("tenant_id", "_default")
            if persisted_tenant != self.tenant_id:
                logger.warning(f"Tenant mismatch in persisted state: {persisted_tenant} vs {self.tenant_id}")
                return

            # F002, F033: Reconstruct complete state including base_state
            self.state.last_saved_time = state_dict.get("last_saved_time", "")
            self.state.last_saved_hash = stored_hash
            self.state.tenant_id = persisted_tenant

            # F033: Restore base_state (RolloutState) from persisted dictionary
            if "base_state" in state_dict:
                base_state_dict = state_dict["base_state"]
                # Reconstruct RolloutState from dictionary
                # Handle Phase enum (may be stored as "PHASE_1_SHADOW" or "Phase.PHASE_1_SHADOW")
                phase_str = base_state_dict.get("phase", "PHASE_1_SHADOW")
                if isinstance(phase_str, str) and phase_str.startswith("Phase."):
                    # Remove "Phase." prefix if present (from repr serialization)
                    phase_str = phase_str.replace("Phase.", "")
                self.state.base_state.phase = Phase(phase_str)
                self.state.base_state.day_number = base_state_dict.get("day_number", 1)
                self.state.base_state.week_number = base_state_dict.get("week_number", 1)
                self.state.base_state.current_traffic_percentage = base_state_dict.get("current_traffic_percentage", 0)
                self.state.base_state.start_date = base_state_dict.get("start_date", "")
                self.state.base_state.last_decision_time = base_state_dict.get("last_decision_time", "")
                self.state.base_state.last_decision_reason = base_state_dict.get("last_decision_reason", "")
                self.state.base_state.phase_start_time = base_state_dict.get("phase_start_time", "")
                self.state.base_state.rollback_count = base_state_dict.get("rollback_count", 0)
                self.state.base_state.pending_operator_approval = base_state_dict.get("pending_operator_approval", False)
                self.state.base_state.approval_required_for = base_state_dict.get("approval_required_for")

                # Restore skill_states (convert string values back to SkillMode enums)
                if "skill_states" in base_state_dict:
                    self.state.base_state.skill_states = {}
                    for skill_id, mode_str in base_state_dict["skill_states"].items():
                        # Handle SkillMode enum (may be stored as "ADVISORY" or "SkillMode.ADVISORY")
                        if isinstance(mode_str, str) and mode_str.startswith("SkillMode."):
                            mode_str = mode_str.replace("SkillMode.", "")
                        self.state.base_state.skill_states[skill_id] = SkillMode(mode_str)

            # Restore operator approvals with tenant isolation
            if "operator_approvals" in state_dict:
                for gate_key, approval_dict in state_dict["operator_approvals"].items():
                    if approval_dict.get("tenant_id") == self.tenant_id:
                        # F036: Handle enum deserialization (gate value may be stored as string)
                        gate_value = approval_dict.get("gate")
                        if isinstance(gate_value, str):
                            # Handle both formats: "phase_1_to_2a" and "OperatorApprovalGate.PHASE_1_TO_2A"
                            if gate_value.startswith("OperatorApprovalGate."):
                                gate_value = gate_value.replace("OperatorApprovalGate.", "").lower()
                            # Convert string to enum
                            gate_enum = OperatorApprovalGate(gate_value)
                        else:
                            gate_enum = OperatorApprovalGate(gate_value.value) if hasattr(gate_value, 'value') else OperatorApprovalGate(gate_value)

                        record = OperatorApprovalRecord(
                            gate=gate_enum,
                            requested_at=approval_dict["requested_at"],
                            approval_id=approval_dict.get("approval_id", str(uuid.uuid4())),
                            approved_at=approval_dict.get("approved_at"),
                            approved_by=approval_dict.get("approved_by"),
                            decision=approval_dict.get("decision"),
                            reason=approval_dict.get("reason", ""),
                            audit_hash=approval_dict.get("audit_hash", ""),
                            lom_hash=approval_dict.get("lom_hash", ""),
                            agreement_rate_at_approval=approval_dict.get("agreement_rate_at_approval"),
                            confidence_at_approval=approval_dict.get("confidence_at_approval"),
                            latency_p99_at_approval=approval_dict.get("latency_p99_at_approval"),
                            feedback_count_at_approval=approval_dict.get("feedback_count_at_approval"),
                            tenant_id=approval_dict.get("tenant_id", "_default"),
                        )
                        self.state.operator_approvals[gate_key] = record
                        self.processed_approval_ids.add(record.approval_id)

            # Restore weekly evaluations
            if "weekly_evaluations" in state_dict:
                for week_key, eval_dict in state_dict["weekly_evaluations"].items():
                    if eval_dict.get("tenant_id") == self.tenant_id:
                        # Handle Phase enum (may have "Phase." prefix)
                        phase_str = eval_dict.get("phase", "PHASE_1_SHADOW")
                        if isinstance(phase_str, str) and phase_str.startswith("Phase."):
                            phase_str = phase_str.replace("Phase.", "")

                        # Handle PhaseGateResult enum (may have "PhaseGateResult." prefix)
                        gate_result_str = eval_dict.get("gate_result", "pass")
                        if isinstance(gate_result_str, str) and gate_result_str.startswith("PhaseGateResult."):
                            gate_result_str = gate_result_str.replace("PhaseGateResult.", "")

                        evaluation = WeeklyGateEvaluation(
                            week_number=eval_dict["week_number"],
                            phase=Phase(phase_str),
                            timestamp=eval_dict["timestamp"],
                            gate_result=PhaseGateResult(gate_result_str),
                            metrics=eval_dict.get("metrics", {}),
                            criteria_passed=eval_dict.get("criteria_passed", 0),
                            criteria_total=eval_dict.get("criteria_total", 0),
                            action_taken=eval_dict.get("action_taken", ""),
                            reason=eval_dict.get("reason", ""),
                            tenant_id=eval_dict.get("tenant_id", "_default"),
                        )
                        self.state.weekly_evaluations[int(week_key)] = evaluation

            # Restore automatic transitions
            if "automatic_transitions" in state_dict:
                self.state.automatic_transitions = state_dict["automatic_transitions"]

            logger.info(f"Loaded persisted state from {self.STATE_FILE} (tenant: {self.tenant_id})")

        except Exception as e:
            logger.error(f"Failed to load persisted state: {e}")

    def _validate_tenant_isolation(self) -> bool:
        """
        Validate tenant isolation for all state queries (F023, F035).

        Ensures that all operations are scoped to the correct tenant.
        If tenant has changed, reset base_state completely.
        """
        # F035: Check if tenant_id in state matches current tenant
        if self.state.tenant_id != self.tenant_id:
            logger.warning(f"Tenant change detected: {self.state.tenant_id} → {self.tenant_id}")
            # F035: RESET base_state completely on tenant switch
            self.state.tenant_id = self.tenant_id
            # A fresh base orchestrator: the old code re-used ``self.base_orch.state``
            # (the very object it meant to reset), so nothing was reset.
            self.base_orch = RolloutOrchestrator(tenant_id=self.tenant_id)
            self.state.base_state = self.base_orch.state
            # Clear approvals and evaluations for old tenant
            self.state.operator_approvals.clear()
            self.state.weekly_evaluations.clear()
            self.state.automatic_transitions.clear()
            self.state.processed_approval_ids.clear()
            logger.info(f"Base state reset for tenant {self.tenant_id}")
            return True

        # Verify operator approvals are tenant-scoped
        for gate_key, record in self.state.operator_approvals.items():
            if record.tenant_id != self.tenant_id:
                logger.error(f"Tenant isolation violation in operator_approval: {record.tenant_id} vs {self.tenant_id}")
                return False

        # Verify weekly evaluations are tenant-scoped
        for week_key, evaluation in self.state.weekly_evaluations.items():
            if evaluation.tenant_id != self.tenant_id:
                logger.error(f"Tenant isolation violation in weekly_evaluation: {evaluation.tenant_id} vs {self.tenant_id}")
                return False

        return True

    def _check_approval_timeouts(self) -> None:
        """
        Check for approval timeouts (F019).

        If an approval has been pending for more than APPROVAL_TIMEOUT_DAYS, escalate to admin.
        """
        current_time = datetime.now(timezone.utc)

        for gate_key, record in self.state.operator_approvals.items():
            if record.decision is not None:
                continue  # Already decided

            # Check if approval has been pending for more than timeout
            requested_time = datetime.fromisoformat(record.requested_at.replace("Z", "+00:00"))
            elapsed = (current_time - requested_time).days

            if elapsed >= self.APPROVAL_TIMEOUT_DAYS and not record.reason.startswith("Auto-escalation"):
                logger.warning(f"Approval timeout for {gate_key} (elapsed: {elapsed} days), escalating to admin")
                self._escalate_approval_to_admin(record, gate_key)

    def _escalate_approval_to_admin(self, record: OperatorApprovalRecord, gate_key: str) -> None:
        """
        Escalate approval to admin if timeout exceeded (F019).
        """
        self._audit_log({
            "event": "approval_escalated_to_admin",
            "gate": gate_key,
            "approval_id": record.approval_id,
            "lom": "core/deployment/master_orchestration_blueprint.py:_escalate_approval_to_admin",
        })
        record.reason = f"Auto-escalation after {self.APPROVAL_TIMEOUT_DAYS} day timeout"

        logger.warning(f"APPROVAL ESCALATED TO ADMIN: {gate_key}")

    def _audit_log(self, event: Dict) -> Dict:
        """Commit one record to the tenant audit chain; mirror it locally.

        Raises ``audit_sink.AuditWriteFailed`` when the record does not commit
        (fail-closed): callers write BEFORE applying the decision, so a failed
        write leaves the decision untaken. Only ``_MASTER_FIELDS`` reach the
        chain (content-free); free text never does.
        """
        with self.audit_trail_lock:
            name = str(event["event"])
            details = {k: v for k, v in event.items() if k in _MASTER_FIELDS}
            details["sequence_number"] = len(self.audit_trail)
            details["lom"] = event.get("lom", "core/deployment/master_orchestration_blueprint.py:_audit_log")
            record = audit_sink.emit(
                f"deployment.master.{name}", details, tenant_id=self.tenant_id,
            )
            mirror = {"event": name, **details, "tenant_id": self.tenant_id,
                      "hash": record.get("hash", ""), "prev_hash": record.get("prev_hash", "")}
            self.audit_trail.append(mirror)
            return mirror

    def get_status(self) -> Dict:
        """Get current rollout status"""
        return {
            "phase": self.state.base_state.phase.value,
            "day": self.state.base_state.day_number,
            "week": self.state.base_state.week_number,
            "traffic_percentage": self.state.base_state.current_traffic_percentage,
            "pending_operator_approval": self.state.base_state.pending_operator_approval,
            "approval_required_for": self.state.base_state.approval_required_for,
            "skill_states": {k: v.value for k, v in self.state.base_state.skill_states.items()},
            "rollback_count": self.state.base_state.rollback_count,
        }

    def verify_audit_chain(self) -> Tuple[bool, List[str]]:
        """Verify the REAL tenant audit chain (forge ``verify_chain``).

        Unverifiable (writer/verifier unavailable) counts as broken.
        """
        try:
            se, fp = audit_sink._forge()
            chain = fp.tenant_audit_chain(self.tenant_id)
            if not chain.exists():
                return (not self.audit_trail), ([] if not self.audit_trail else ["chain missing"])
            ok, problems = se.verify_chain(chain)
            return bool(ok), [str(p) for p in (problems or [])]
        except Exception as exc:  # noqa: BLE001 — fail-closed
            return False, [f"verifier unavailable ({type(exc).__name__})"]
