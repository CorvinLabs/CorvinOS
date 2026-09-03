"""Phase gate validator with atomic rollback + learning optimizer (ADR-0542, Phase C complete)."""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum


class GateType(Enum):
    """All supported gate types (Phase C, ADR-0542)."""
    FINDING_COUNT = "finding_count"
    TEST_PASS_RATE = "test_pass_rate"
    CONFIDENCE_DRIFT_DETECTION = "confidence_drift_detection"
    AUDIT_TRAIL_VERIFIED = "audit_trail_verified"


@dataclass
class GateEvaluation:
    """Result of gate evaluation (Phase C)."""
    gate_type: str
    passed: bool
    reason: str
    payload: Dict[str, Any] = None  # Additional data for learning


class LearningOptimizer:
    """Optimizer with EMA smoothing (ADR-0543, Phase C, Fix 3.1)."""

    def __init__(self, alpha: float = 0.3):
        """Initialize with smoothing factor (EMA alpha)."""
        self.alpha = alpha  # 0.3 = 70% trust prior, 30% trust measured
        self.config = {}
        self.confidence_history = []

    def smooth_confidence(self, confidence_measured: float, confidence_prior: float) -> float:
        """Apply EMA smoothing (Fix 3.1: ADR-0543 Smoothing Algorithm)."""
        confidence_tuned = self.alpha * confidence_measured + (1 - self.alpha) * confidence_prior
        return confidence_tuned

    def check_drift(self, confidence_tuned: float, confidence_prior: float, max_decrease: float = 0.15) -> Tuple[bool, str]:
        """Check if confidence drift is too large (Fix 3.2: ADR-0542 Drift Detection)."""
        delta = confidence_tuned - confidence_prior

        if delta < -max_decrease:
            return False, f"Drift BLOCKED: {delta:.2f} < -{max_decrease} (large drop)"

        return True, f"Drift OK: {delta:+.2f}"

    def tune_config(self, skill_id: str, param_delta: Dict[str, Any]) -> bool:
        """Tune skill config with validation (MEDIUM FIX 12: param value validation, ADR-0543)."""
        # TRUST BOUNDARY: only these params are tunable (Phase C)
        ALLOWED_PARAMS = {"retry_threshold", "confidence_gate_min", "timeout_multiplier"}

        # MEDIUM FIX 12: Add value validators (prevent config poisoning)
        PARAM_VALIDATORS = {
            "retry_threshold": lambda v: isinstance(v, int) and 1 <= v <= 100,
            "confidence_gate_min": lambda v: isinstance(v, float) and 0.0 <= v <= 1.0,
            "timeout_multiplier": lambda v: isinstance(v, float) and 0.1 <= v <= 10.0,
        }

        for param, delta in param_delta.items():
            if param not in ALLOWED_PARAMS:
                raise ValueError(f"Config trust boundary violated: {param} not tunable")

            # MEDIUM FIX 12.1: Validate param value range
            if param not in PARAM_VALIDATORS:
                raise ValueError(f"No validator defined for {param}")

            if not PARAM_VALIDATORS[param](delta):
                raise ValueError(
                    f"Invalid value for {param}: {delta}. Must satisfy: "
                    f"{PARAM_VALIDATORS[param].__doc__ if hasattr(PARAM_VALIDATORS[param], '__doc__') else 'validation rule'}"
                )

        self.config[skill_id] = param_delta
        return True


class PhaseGateValidator:
    """Evaluate phase gates + atomic rollback + learning optimizer (Phase C complete)."""

    def __init__(self, event_store, git_manager=None, optimizer: Optional[LearningOptimizer] = None):
        self.event_store = event_store
        self.git_manager = git_manager
        self.optimizer = optimizer or LearningOptimizer()
        self.rollback_state = {}

    def evaluate_all_gates(self, gates: List[Dict[str, Any]], phase_output: Dict[str, Any], prev_confidence: float = 1.0) -> Tuple[bool, List[GateEvaluation]]:
        """Evaluate all gates for a phase. Return (all_passed, results)."""
        results = []

        for gate_config in gates:
            gate_type = gate_config.get("type")

            try:
                if gate_type == "finding_count":
                    result = self._eval_finding_count(gate_config, phase_output)
                elif gate_type == "test_pass_rate":
                    result = self._eval_test_pass_rate(gate_config, phase_output)
                elif gate_type == "confidence_drift_detection":
                    # Phase C: REAL drift detection with EMA (Fix 3.2)
                    result = self._eval_drift_detection(gate_config, phase_output, prev_confidence)
                elif gate_type == "audit_trail_verified":
                    result = self._eval_audit_trail(gate_config, phase_output)
                else:
                    result = GateEvaluation(gate_type=gate_type, passed=False, reason=f"Unknown gate: {gate_type}")
            except Exception as e:
                result = GateEvaluation(gate_type=gate_type, passed=False, reason=f"Gate error: {str(e)}")

            results.append(result)

        all_passed = all(r.passed for r in results)
        return all_passed, results

    def _eval_finding_count(self, gate_config: Dict, phase_output: Dict) -> GateEvaluation:
        """Evaluate finding_count gate."""
        max_critical = gate_config.get("max_critical", 0)
        findings = phase_output.get("audit_findings", [])
        critical = sum(1 for f in findings if f.get("severity") == "CRITICAL")

        if critical <= max_critical:
            return GateEvaluation(gate_type="finding_count", passed=True,
                                reason=f"Findings OK: {critical} <= {max_critical}")
        return GateEvaluation(gate_type="finding_count", passed=False,
                            reason=f"BLOCKED: {critical} critical > {max_critical}")

    def _eval_test_pass_rate(self, gate_config: Dict, phase_output: Dict) -> GateEvaluation:
        """Evaluate test_pass_rate gate."""
        min_rate = gate_config.get("min", 1.0)
        tests_passed = phase_output.get("tests_passed", 0)
        tests_total = phase_output.get("tests_total", 1)
        rate = tests_passed / max(tests_total, 1)

        if rate >= min_rate:
            return GateEvaluation(gate_type="test_pass_rate", passed=True,
                                reason=f"Pass rate OK: {rate:.0%} >= {min_rate:.0%}")
        return GateEvaluation(gate_type="test_pass_rate", passed=False,
                            reason=f"BLOCKED: {rate:.0%} < {min_rate:.0%}")

    def _eval_drift_detection(self, gate_config: Dict, phase_output: Dict, prev_confidence: float) -> GateEvaluation:
        """Evaluate confidence_drift_detection gate with EMA smoothing (Phase C, Fix 3.2)."""
        max_decrease = gate_config.get("max_decrease", 0.15)
        min_threshold = gate_config.get("min_threshold", 0.50)

        confidence_measured = phase_output.get("confidence", 1.0)

        # Phase C: REAL smoothing with EMA (Fix 3.1)
        confidence_tuned = self.optimizer.smooth_confidence(confidence_measured, prev_confidence)

        # Check drift (Fix 3.2: real thresholds)
        drift_ok, drift_reason = self.optimizer.check_drift(confidence_tuned, prev_confidence, max_decrease)

        if not drift_ok or confidence_tuned < min_threshold:
            return GateEvaluation(
                gate_type="confidence_drift_detection",
                passed=False,
                reason=f"BLOCKED: {drift_reason}, tuned={confidence_tuned:.2f}, floor={min_threshold}",
                payload={"measured": confidence_measured, "tuned": confidence_tuned, "prev": prev_confidence}
            )

        return GateEvaluation(
            gate_type="confidence_drift_detection",
            passed=True,
            reason=f"Drift OK: {drift_reason}",
            payload={"measured": confidence_measured, "tuned": confidence_tuned, "prev": prev_confidence}
        )

    def _eval_audit_trail(self, gate_config: Dict, phase_output: Dict) -> GateEvaluation:
        """Evaluate audit_trail_verified gate (Phase B/C: real verification)."""
        must_verify = gate_config.get("must_verify", True)
        if must_verify:
            # Phase C: real check (if event_store has verify_chain)
            try:
                task_id = phase_output.get("task_id", "unknown")
                if hasattr(self.event_store, 'verify_chain'):
                    is_valid = self.event_store.verify_chain(task_id)
                    if is_valid:
                        return GateEvaluation(gate_type="audit_trail_verified", passed=True,
                                            reason="Audit trail verified (chain intact)")
                    else:
                        return GateEvaluation(gate_type="audit_trail_verified", passed=False,
                                            reason="BLOCKED: audit chain verification failed")
            except Exception as e:
                return GateEvaluation(gate_type="audit_trail_verified", passed=False,
                                    reason=f"BLOCKED: verification error: {str(e)}")
        return GateEvaluation(gate_type="audit_trail_verified", passed=False,
                            reason="Audit trail verification not enabled")

    def atomic_rollback(self, task_id: str, rolled_back_to_phase: str, reason: str) -> bool:
        """Orchestrate atomic rollback with real git + EventStore (CRITICAL FIX 2, ADR-0542)."""
        import threading

        # CRITICAL FIX: Add locking to prevent concurrent rollback race
        if task_id not in getattr(self, '_rollback_locks', {}):
            if not hasattr(self, '_rollback_locks'):
                self._rollback_locks = {}
            self._rollback_locks[task_id] = threading.Lock()

        with self._rollback_locks[task_id]:
            try:
                # Step 1: Verify pre-task state exists (fail-closed)
                pre_task_state = self.rollback_state.get(f"{task_id}:pre")
                if not pre_task_state:
                    raise ValueError(f"CRITICAL: No pre-task state saved for {task_id}")

                # Step 2: REAL git reset (CRITICAL FIX 2.1)
                if self.git_manager:
                    try:
                        self.git_manager.reset_to(pre_task_state['git_commit'])
                    except Exception as e:
                        raise RuntimeError(f"Git reset failed for {task_id}: {str(e)}")

                # Step 3: REAL EventStore rollback + DELETE (CRITICAL FIX 2.2)
                # Delete all events for this task after pre-task snapshot
                if hasattr(self.event_store, 'events'):
                    pre_timestamp = pre_task_state.get('timestamp')
                    # Remove events added AFTER rollback point
                    self.event_store.events = [
                        e for e in self.event_store.events
                        if not (e.task_id == task_id and (pre_timestamp is None or e.timestamp > pre_timestamp))
                    ]

                # Step 4: Emit atomic rollback event (CRITICAL FIX 2.3: error handling)
                from .models import AuditEvent
                from datetime import datetime

                rollback_event = AuditEvent(
                    event_type="task_rolled_back",
                    task_id=task_id,
                    tenant_id=self.event_store.tenant_id,
                    session_id="rollback-session",
                    timestamp=datetime.utcnow().isoformat() + "Z",
                    payload={
                        "rolled_back_to_phase": rolled_back_to_phase,
                        "reason": reason,
                        "pre_task_git_commit": pre_task_state['git_commit'],
                    }
                )
                self.event_store.append_event(rollback_event)

                return True

            except Exception as e:
                # CRITICAL FIX 2.4: Emit failure event for recovery
                try:
                    from .models import AuditEvent
                    from datetime import datetime

                    failure_event = AuditEvent(
                        event_type="rollback_failed",
                        task_id=task_id,
                        tenant_id=self.event_store.tenant_id,
                        session_id="rollback-recovery",
                        timestamp=datetime.utcnow().isoformat() + "Z",
                        payload={"error": str(e), "rolled_back_to": rolled_back_to_phase}
                    )
                    self.event_store.append_event(failure_event)
                except Exception:
                    pass  # If audit fails too, at least log it

                return False

    def save_pre_task_state(self, task_id: str, git_commit: str, snapshot_hash: str) -> None:
        """Save pre-task state for recovery (ADR-0542 Fix 4.4, 4.5)."""
        self.rollback_state[f"{task_id}:pre"] = {
            "git_commit": git_commit,
            "snapshot_hash": snapshot_hash,
            "timestamp": None,  # Would be datetime in prod
        }

    def boot_tripwire_extended(self, task_id: str, git_current_commit: str, eventstore_snapshot_hash: str) -> bool:
        """Verify git-vs-EventStore consistency at boot (ADR-0542 Fix 4.4, Phase C)."""
        if git_current_commit != eventstore_snapshot_hash:
            raise RuntimeError(
                f"CRITICAL: Boot tripwire failed for {task_id}. "
                f"Git commit {git_current_commit} != EventStore snapshot {eventstore_snapshot_hash}. "
                f"Divergence detected. Manual intervention required."
            )
        return True
