"""Phase gate validator with atomic rollback (ADR-0542)."""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional


@dataclass
class GateEvaluation:
    """Result of gate evaluation."""
    gate_type: str
    passed: bool
    reason: str


class PhaseGateValidator:
    """Evaluate phase gates and orchestrate atomic rollback (ADR-0542)."""

    def __init__(self, event_store, git_manager=None):
        self.event_store = event_store
        self.git_manager = git_manager

    def evaluate_all_gates(self, gates: List[Dict[str, Any]], phase_output: Dict[str, Any]) -> (bool, List[GateEvaluation]):
        """Evaluate all gates for a phase. Return (all_passed, results)."""
        results = []
        for gate_config in gates:
            gate_type = gate_config.get("type")

            if gate_type == "finding_count":
                result = self._eval_finding_count(gate_config, phase_output)
            elif gate_type == "test_pass_rate":
                result = self._eval_test_pass_rate(gate_config, phase_output)
            elif gate_type == "confidence_drift_detection":
                result = self._eval_drift_detection(gate_config, phase_output)
            elif gate_type == "audit_trail_verified":
                result = self._eval_audit_trail(gate_config, phase_output)
            else:
                result = GateEvaluation(gate_type=gate_type, passed=False, reason=f"Unknown gate type: {gate_type}")

            results.append(result)

        all_passed = all(r.passed for r in results)
        return all_passed, results

    def _eval_finding_count(self, gate_config: Dict, phase_output: Dict) -> GateEvaluation:
        """Evaluate finding_count gate (ADR-0542)."""
        max_critical = gate_config.get("max_critical", 0)
        findings = phase_output.get("audit_findings", [])
        critical = sum(1 for f in findings if f.get("severity") == "CRITICAL")

        if critical <= max_critical:
            return GateEvaluation(gate_type="finding_count", passed=True,
                                reason=f"Findings OK: {critical} <= {max_critical}")
        return GateEvaluation(gate_type="finding_count", passed=False,
                            reason=f"Too many critical findings: {critical} > {max_critical}")

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
                            reason=f"Pass rate too low: {rate:.0%} < {min_rate:.0%}")

    def _eval_drift_detection(self, gate_config: Dict, phase_output: Dict) -> GateEvaluation:
        """Evaluate confidence_drift_detection gate (ADR-0542 Fix 3.2)."""
        max_decrease = gate_config.get("max_decrease", 0.15)
        min_threshold = gate_config.get("min_threshold", 0.50)

        confidence = phase_output.get("confidence", 1.0)
        prev_confidence = phase_output.get("prev_confidence", 1.0)
        delta = confidence - prev_confidence

        if delta < -max_decrease:
            return GateEvaluation(gate_type="confidence_drift_detection", passed=False,
                                reason=f"Large confidence drop: {delta:.2f} < -{max_decrease}")
        if confidence < min_threshold:
            return GateEvaluation(gate_type="confidence_drift_detection", passed=False,
                                reason=f"Confidence below floor: {confidence:.2f} < {min_threshold}")

        return GateEvaluation(gate_type="confidence_drift_detection", passed=True,
                            reason=f"Drift OK: {confidence:.2f}, delta {delta:+.2f}")

    def _eval_audit_trail(self, gate_config: Dict, phase_output: Dict) -> GateEvaluation:
        """Evaluate audit_trail_verified gate."""
        # For Phase A: mock true. Phase B: will verify cryptographic signatures + chain.
        must_verify = gate_config.get("must_verify", True)
        if must_verify:
            return GateEvaluation(gate_type="audit_trail_verified", passed=True,
                                reason="Audit trail verified (Phase A mock)")
        return GateEvaluation(gate_type="audit_trail_verified", passed=False,
                            reason="Audit trail verification not enabled")

    def atomic_rollback(self, task_id: str, rolled_back_to_phase: str, reason: str) -> bool:
        """Orchestrate atomic rollback (ADR-0542 Fix 4.1).

        In production: uses database transaction or WAL for all-or-nothing semantics.
        For Phase A/B mock: simulates rollback.
        """
        try:
            # Step 1: Git reset (if git_manager available)
            if self.git_manager:
                # self.git_manager.reset("--hard", pre_task_commit)
                pass

            # Step 2: EventStore rollback (DELETE semantics, Fix 4.2)
            # Delete all events after the rollback point
            # In mock: log the rollback intent

            # Step 3: Emit rollback event
            self.event_store.append_event(
                event_type="task_rolled_back",
                task_id=task_id,
                payload={"rolled_back_to_phase": rolled_back_to_phase, "reason": reason}
            )

            return True
        except Exception as e:
            # Emit failure event (ADR-0542 Fix 4.3)
            self.event_store.append_event(
                event_type="rollback_failed",
                task_id=task_id,
                payload={"error": str(e)}
            )
            return False
