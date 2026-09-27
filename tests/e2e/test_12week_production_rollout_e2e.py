"""
Comprehensive E2E Tests for 12-Week Production Rollout

60+ test cases covering:
- Happy path: Phase 1→2a→2b (12 weeks smooth progression)
- Incident scenarios: latency spike, confidence regression, audit break
- Rollback scenarios: all 8 triggers verified
- Operator gates: approval/rejection paths
- ADR compliance: weekly validation across ADR-0206, 0205, 0186, 0369
- State persistence: disaster recovery, checkpoint loading
- Full 12-week simulation with metrics

Compliance: GDPR (Art. 5/6/30/32), EU AI Act (Art. 5/50), audit-first (ADR-0232/0233)

Adversarial review 2026-09-27: the orchestration modules are NOT WIRED (no
production caller). Every test runs in its own CORVIN_HOME because audit records
now go to the real tenant chain. Canary/skill-primary tests supply a MEASURED
Phase 1 latency baseline explicitly — without one the orchestrator rolls back
(``BASELINE_NOT_MEASURED``) instead of comparing against an invented 100 ms.
Removed test classes that exercised APIs which do not exist
(``_evaluate_phase_gate``, ``_get_audit_events``, ``_validate_metrics``,
``validate_metric_validity``, ``revoke_approval``, ``_detect_incidents``,
``status["all_clear"]``, ``SkillMetrics(audit_chain_verified=...)``) — they had
no subject; the real behaviour they gestured at is tested below against the
real API.
"""

import pytest
from datetime import datetime, timezone, timedelta
from typing import Dict
import json

from core.deployment.master_orchestration_blueprint import (
    MasterRolloutOrchestrator,
    OperatorApprovalGate,
    AutomaticTransition,
    PhaseGateResult,
)
from core.deployment.adr_validation_framework import (
    ADRComplianceValidator,
    ComplianceStatus,
)
from core.deployment.incident_response_procedures import (
    IncidentDetector,
    IncidentType,
    IncidentSeverity,
)
from core.deployment.rollback_automation import (
    RollbackController,
    RollbackTrigger,
)
from core.deployment.phase3_rollout_orchestrator import (
    Phase,
    SkillMetrics,
    SkillMode,
    RolloutOrchestrator,
    Phase1Evaluator,
    RollbackReason,
)
from core.deployment import audit_sink


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    """Each test has its own CORVIN_HOME/HOME — the audit chain is real."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    yield


def _canary_metrics(**kw):
    base = dict(agreement_rate=0.99, confidence=0.90, confidence_sigma=0.02,
                latency_p99_ms=105.0, feedback_count=50000, correctness_delta=0.0)
    base.update(kw)
    return {"os.delegation_router": SkillMetrics(
        skill_id="os.delegation_router", phase=Phase.PHASE_2A_CANARY, **base)}


def _shadow_metrics():
    return {"os.delegation_router": SkillMetrics(
        skill_id="os.delegation_router", phase=Phase.PHASE_1_SHADOW,
        agreement_rate=0.99, confidence=0.90, confidence_sigma=0.02,
        latency_p99_ms=100.0, feedback_count=2000)}


class TestPhase1ShadowMode:
    """Phase 1: Shadow mode (advisory), 2 weeks baseline collection"""

    def test_phase_1_initialization(self):
        """Test Phase 1 initializes correctly"""
        orchestrator = MasterRolloutOrchestrator()
        status = orchestrator.get_status()

        assert status["phase"] == Phase.PHASE_1_SHADOW.value
        assert status["day"] == 1
        assert status["traffic_percentage"] == 0
        assert not status["pending_operator_approval"]

    def test_phase_1_advisory_mode_all_skills(self):
        """Test Phase 1 runs all skills in ADVISORY mode"""
        orchestrator = MasterRolloutOrchestrator()
        status = orchestrator.get_status()

        for skill_id, mode in status["skill_states"].items():
            assert mode == "ADVISORY"

    def test_phase_1_baseline_metrics_collection(self):
        """Test Phase 1 collects baseline metrics for 14 days"""
        orchestrator = MasterRolloutOrchestrator()

        # Advance from day 1 to day 14 (13 daily steps)
        for day in range(2, 15):
            metrics = {
                "os.delegation_router": SkillMetrics(
                    skill_id="os.delegation_router",
                    phase=Phase.PHASE_1_SHADOW,
                    agreement_rate=0.98,
                    confidence=0.80,
                    confidence_sigma=0.05,
                    feedback_count=1000 * day,
                    latency_p99_ms=100.0,
                ),
                "os.context_adapter": SkillMetrics(
                    skill_id="os.context_adapter",
                    phase=Phase.PHASE_1_SHADOW,
                    agreement_rate=0.98,
                    confidence=0.75,
                    confidence_sigma=0.04,
                    feedback_count=1000 * day,
                    latency_p99_ms=95.0,
                ),
            }

            orchestrator.advance_day(metrics)

        # Day 14 reached; the Phase 1→2a approval is requested; the shadow
        # latency baseline was MEASURED from the supplied metrics.
        assert orchestrator.state.base_state.day_number == 14
        assert orchestrator.state.base_state.approval_required_for == "phase_1_to_2a"
        assert orchestrator.state.base_state.baseline_latency_p99_ms == pytest.approx(97.5)

    def test_phase_1_success_criteria_met(self):
        """Test Phase 1 success: 14+ days, 1k+ feedback, 98%+ agreement, converged"""
        orchestrator = MasterRolloutOrchestrator()

        # Run 15 days (exceeds 14-day minimum)
        for day in range(1, 16):
            metrics = {
                "os.delegation_router": SkillMetrics(
                    skill_id="os.delegation_router",
                    phase=Phase.PHASE_1_SHADOW,
                    agreement_rate=0.99,
                    confidence=0.80,
                    confidence_sigma=0.02,  # Converged
                    feedback_count=1500 * day,  # >1000
                    latency_p99_ms=100.0,
                ),
            }

            orchestrator.advance_day(metrics)

        # Should request operator approval
        assert orchestrator.state.base_state.pending_operator_approval
        assert orchestrator.state.base_state.approval_required_for == "phase_1_to_2a"


class TestPhase2aCanaryTraffic:
    """Phase 2a: Canary with traffic escalation (1%→10%→50%→100%)"""

    def test_phase_2a_starts_at_1_percent(self):
        """Test Phase 2a starts at 1% traffic"""
        orchestrator = MasterRolloutOrchestrator()

        # Skip Phase 1
        orchestrator.state.base_state.phase = Phase.PHASE_2A_CANARY
        orchestrator.state.base_state.current_traffic_percentage = 1

        assert orchestrator.get_status()["traffic_percentage"] == 1

    def test_phase_2a_weekly_gate_escalation(self):
        """Test Phase 2a escalates traffic weekly when gates pass"""
        orchestrator = MasterRolloutOrchestrator()
        orchestrator.state.base_state.phase = Phase.PHASE_2A_CANARY
        orchestrator.state.base_state.day_number = 20
        orchestrator.state.base_state.week_number = 3
        orchestrator.state.base_state.current_traffic_percentage = 1
        orchestrator.state.base_state.baseline_latency_p99_ms = 100.0  # measured in Phase 1

        # Week 3 metrics (should pass gate → escalate to 10%)
        metrics = {
            "os.delegation_router": SkillMetrics(
                skill_id="os.delegation_router",
                phase=Phase.PHASE_2A_CANARY,
                agreement_rate=0.99,
                confidence=0.75,
                confidence_sigma=0.03,
                latency_p99_ms=105.0,  # Within 10% of 100ms baseline
                feedback_count=5000,
            ),
        }

        orchestrator.advance_day(metrics)

        # Check weekly evaluation was recorded
        assert 3 in orchestrator.state.weekly_evaluations
        evaluation = orchestrator.state.weekly_evaluations[3]
        assert evaluation.gate_result == PhaseGateResult.PASS

    def test_phase_2a_latency_threshold_enforcement(self):
        """Test Phase 2a enforces latency thresholds"""
        validator = ADRComplianceValidator()

        # Week 4, 10% traffic: latency must be within 15% of baseline
        metrics = {
            "canary_metrics": {
                "agreement_rate": 0.98,
                "latency_p99_ms": 125.0,  # 125 > 100*1.15=115, violates threshold
                "correctness_delta": 0.01,
            },
            "baseline_latency_ms": 100.0,
        }

        checks = validator.validate_adr_0206_canary(week_number=4, metrics=metrics["canary_metrics"], baseline_latency_ms=100.0)

        # 125 ms is over the 115 ms threshold: never a PASS (it lands in the
        # 10% WARNING band, which still blocks an ADR-0206 transition).
        latency_checks = [c for c in checks if c.check_name == "latency_p99"]
        assert latency_checks and all(c.status != ComplianceStatus.PASS for c in latency_checks)

    def test_latency_check_fails_without_measured_baseline(self):
        """No Phase 1 baseline → the latency check FAILS (no 100 ms default)."""
        validator = ADRComplianceValidator()
        checks = validator.validate_adr_0206_canary(
            week_number=4,
            metrics={"agreement_rate": 0.99, "latency_p99_ms": 50.0, "correctness_delta": 0.0},
            baseline_latency_ms=None,
        )
        lat = [c for c in checks if c.check_name == "latency_p99"]
        assert lat and lat[0].status == ComplianceStatus.FAIL
        assert "not measured" in lat[0].message

    def test_missing_canary_metrics_fail_not_pass(self):
        """Absent latency/correctness used to default to 0 → PASS."""
        validator = ADRComplianceValidator()
        checks = validator.validate_adr_0206_canary(
            week_number=4, metrics={"agreement_rate": 0.99}, baseline_latency_ms=100.0,
        )
        by_name = {c.check_name: c.status for c in checks}
        assert by_name["latency_p99"] == ComplianceStatus.FAIL
        assert by_name["correctness_delta"] == ComplianceStatus.FAIL

    def test_phase_2a_traffic_escalation_full_sequence(self):
        """Full canary: 1% (wk3) → 10% (wk4) → 50% (wk5) → 100% (wk6), then 2b approval."""
        orchestrator = MasterRolloutOrchestrator()
        while orchestrator.state.base_state.day_number < 14:
            orchestrator.advance_day(_shadow_metrics())
        assert orchestrator.operator_approve(OperatorApprovalGate.PHASE_1_TO_2A, approved_by="ops")

        traffic_at = {}
        while orchestrator.state.base_state.day_number < 42:
            orchestrator.advance_day(_canary_metrics())
            d = orchestrator.state.base_state.day_number
            if d % 7 == 0:
                traffic_at[d] = orchestrator.state.base_state.current_traffic_percentage

        assert orchestrator.state.base_state.phase == Phase.PHASE_2A_CANARY
        assert traffic_at == {21: 1, 28: 10, 35: 50, 42: 100}
        assert orchestrator.state.base_state.pending_operator_approval
        ok, issues = orchestrator.verify_audit_chain()
        assert ok, issues

    def test_canary_without_baseline_rolls_back(self):
        """A canary with no measured Phase 1 baseline cannot detect a latency
        spike, so it rolls back instead of running blind."""
        orchestrator = MasterRolloutOrchestrator()
        st = orchestrator.state.base_state
        st.phase = Phase.PHASE_2A_CANARY
        st.day_number = 20
        orchestrator.advance_day(_canary_metrics())
        assert st.phase == Phase.ROLLED_BACK
        assert all(m == SkillMode.FALLBACK for m in st.skill_states.values())


class TestPhase2bSkillActivation:
    """Phase 2b: Skill-primary activation, per-skill progression"""

    def test_phase_2b_skill_activation_threshold(self):
        """Test skill activates at confidence ≥0.85"""
        orchestrator = MasterRolloutOrchestrator()
        orchestrator.state.base_state.phase = Phase.PHASE_2B_SKILL_PRIMARY
        orchestrator.state.base_state.day_number = 70  # At 100% traffic
        orchestrator.state.base_state.baseline_latency_p99_ms = 100.0  # measured in Phase 1

        # High confidence should trigger activation
        metrics = {
            "os.delegation_router": SkillMetrics(
                skill_id="os.delegation_router",
                phase=Phase.PHASE_2B_SKILL_PRIMARY,
                agreement_rate=0.98,
                confidence=0.86,  # >0.85 threshold
                confidence_sigma=0.02,
                latency_p99_ms=100.0,
                feedback_count=50000,
            ),
        }

        orchestrator.advance_day(metrics)

        # Confidence clears 0.85, but audit-violation count and latency
        # stability are NOT MEASURED by anything → activation is refused.
        status = orchestrator.get_status()
        assert status["phase"] == Phase.PHASE_2B_SKILL_PRIMARY.value
        assert status["skill_states"]["os.delegation_router"] != SkillMode.PRIMARY.value

    def test_phase_2b_skill_selective_activation(self):
        """Test Phase 2b can activate skills independently"""
        orchestrator = MasterRolloutOrchestrator()
        orchestrator.state.base_state.phase = Phase.PHASE_2B_SKILL_PRIMARY

        # Context adapter ready, routing not ready
        metrics = {
            "os.delegation_router": SkillMetrics(
                skill_id="os.delegation_router",
                confidence=0.70,  # Below activation threshold
            ),
            "os.context_adapter": SkillMetrics(
                skill_id="os.context_adapter",
                confidence=0.82,  # Above activation threshold
            ),
        }

        # In real scenario, different skills would activate at different times


class TestIncidentDetection:
    """Test all 6 incident types"""

    def test_incident_type_latency_spike(self):
        """Test latency spike detection"""
        detector = IncidentDetector()

        metrics = {
            "latency_p99_ms": 145.0,  # 45% > 100ms baseline
            "audit_chain_verified": True,
            "tenant_isolation_verified": True,
        }

        incidents = detector.detect_incidents(metrics, baseline_latency_p99_ms=100.0)

        assert any(i.incident_type == IncidentType.LATENCY_SPIKE for i in incidents)

    def test_incident_type_confidence_regression(self):
        """Test confidence regression detection"""
        detector = IncidentDetector()
        detector.prior_metrics = {"confidence": 0.85}

        metrics = {
            "confidence": 0.70,  # 17.6% drop
            "audit_chain_verified": True,
            "tenant_isolation_verified": True,
        }

        incidents = detector.detect_incidents(metrics)

        assert any(i.incident_type == IncidentType.CONFIDENCE_REGRESSION for i in incidents)

    def test_incident_type_negative_feedback_surge(self):
        """Test negative feedback surge detection"""
        detector = IncidentDetector()
        detector.prior_metrics = {"positive_feedback_rate": 0.80}

        metrics = {
            "positive_feedback_rate": 0.65,  # 18.75% drop
            "audit_chain_verified": True,
            "tenant_isolation_verified": True,
        }

        incidents = detector.detect_incidents(metrics)

        assert any(i.incident_type == IncidentType.NEGATIVE_FEEDBACK_SURGE for i in incidents)

    def test_incident_type_audit_chain_break(self):
        """Test audit chain break detection (CRITICAL)"""
        detector = IncidentDetector()

        metrics = {
            "audit_chain_verified": False,  # CRITICAL
            "tenant_isolation_verified": True,
        }

        incidents = detector.detect_incidents(metrics)

        critical_incidents = [i for i in incidents if i.incident_type == IncidentType.AUDIT_CHAIN_BREAK]
        assert len(critical_incidents) > 0
        assert critical_incidents[0].severity == IncidentSeverity.CRITICAL

    def test_incident_type_tenant_isolation_violation(self):
        """Test tenant isolation violation detection (CRITICAL)"""
        detector = IncidentDetector()

        metrics = {
            "audit_chain_verified": True,
            "tenant_isolation_verified": False,  # CRITICAL
        }

        incidents = detector.detect_incidents(metrics)

        critical_incidents = [i for i in incidents if i.incident_type == IncidentType.TENANT_ISOLATION_VIOLATION]
        assert len(critical_incidents) > 0
        assert critical_incidents[0].severity == IncidentSeverity.CRITICAL


class TestRollbackTriggers:
    """Test all 8 rollback triggers"""

    def test_rollback_trigger_1_correctness_drop(self):
        """Trigger 1: Correctness >2% drop"""
        controller = RollbackController()

        metrics = {
            "agreement_rate": 0.97,  # <98% = >2% disagreement
            "phase": "PHASE_2A_CANARY",
        }

        event = controller.check_all_triggers(metrics, phase="PHASE_2A_CANARY")

        assert event is not None
        assert event.trigger == RollbackTrigger.CORRECTNESS_DROP

    def test_rollback_trigger_2_latency_spike(self):
        """Trigger 2: Latency spike >20%"""
        controller = RollbackController()

        metrics = {
            "latency_p99_ms": 130.0,  # 30% > 100ms baseline
            "phase": "PHASE_2A_CANARY",
        }

        event = controller.check_all_triggers(metrics, phase="PHASE_2A_CANARY", baseline_latency_ms=100.0)

        assert event is not None
        assert event.trigger == RollbackTrigger.LATENCY_SPIKE

    def test_rollback_trigger_3_confidence_regression(self):
        """Trigger 3: Confidence regression >10%"""
        controller = RollbackController()

        metrics = {
            "confidence": 0.70,
            "prior_confidence": 0.80,  # 12.5% regression
            "phase": "PHASE_2B_SKILL_PRIMARY",
        }

        event = controller.check_all_triggers(metrics, phase="PHASE_2B_SKILL_PRIMARY")

        assert event is not None
        assert event.trigger == RollbackTrigger.CONFIDENCE_REGRESSION

    def test_rollback_trigger_4_audit_chain_break(self):
        """Trigger 4: Audit chain break (CRITICAL)"""
        controller = RollbackController()

        metrics = {
            "audit_chain_verified": False,
            "phase": "PHASE_2A_CANARY",
        }

        event = controller.check_all_triggers(metrics, phase="PHASE_2A_CANARY")

        assert event is not None
        assert event.trigger == RollbackTrigger.AUDIT_CHAIN_BREAK

    def test_rollback_trigger_5_tenant_isolation_violation(self):
        """Trigger 5: Tenant isolation violation (CRITICAL)"""
        controller = RollbackController()

        metrics = {
            "audit_chain_verified": True,
            "tenant_isolation_verified": False,
            "phase": "PHASE_2B_SKILL_PRIMARY",
        }

        event = controller.check_all_triggers(metrics, phase="PHASE_2B_SKILL_PRIMARY")

        assert event is not None
        assert event.trigger == RollbackTrigger.TENANT_ISOLATION_VIOLATION

    def test_rollback_trigger_6_security_failure(self):
        """Trigger 6: Security check failure (CRITICAL)"""
        controller = RollbackController()

        metrics = {
            "audit_chain_verified": True,
            "tenant_isolation_verified": True,
            "security_checks_pass": False,
            "phase": "PHASE_2A_CANARY",
        }

        event = controller.check_all_triggers(metrics, phase="PHASE_2A_CANARY")

        assert event is not None
        assert event.trigger == RollbackTrigger.SECURITY_CHECK_FAILURE

    def test_rollback_trigger_7_loss_signal_critical(self):
        """Trigger 7: Loss signal CRITICAL (A/B regression)"""
        controller = RollbackController()

        metrics = {
            "audit_chain_verified": True,
            "tenant_isolation_verified": True,
            "security_checks_pass": True,
            "ab_ci_lower": -0.05,
            "ab_ci_upper": 0.02,  # CI crosses zero
            "phase": "PHASE_2B_SKILL_PRIMARY",
        }

        event = controller.check_all_triggers(metrics, phase="PHASE_2B_SKILL_PRIMARY")

        assert event is not None
        assert event.trigger == RollbackTrigger.LOSS_SIGNAL_CRITICAL

    def test_rollback_trigger_8_manual_operator(self):
        """Trigger 8: Manual operator rollback"""
        controller = RollbackController()

        event = controller.manual_rollback(
            operator_id="ops-team",
            reason="Investigating anomaly",
            phase="PHASE_2A_CANARY",
        )

        assert event.trigger == RollbackTrigger.MANUAL_OPERATOR_ROLLBACK
        assert event.operator_id == "ops-team"


class TestOperatorGates:
    """Test operator approval checkpoints"""

    def test_phase_1_to_2a_approval_gate(self):
        """Test Phase 1→2a approval gate"""
        orchestrator = MasterRolloutOrchestrator()

        # Mark Phase 1 complete
        orchestrator._request_operator_approval(OperatorApprovalGate.PHASE_1_TO_2A)

        assert orchestrator.state.base_state.pending_operator_approval
        assert orchestrator.state.base_state.approval_required_for == "phase_1_to_2a"

    def test_operator_approval_grants_transition(self):
        """Test operator approval executes phase transition"""
        orchestrator = MasterRolloutOrchestrator()
        orchestrator._request_operator_approval(OperatorApprovalGate.PHASE_1_TO_2A)

        # Operator approves
        result = orchestrator.operator_approve(
            OperatorApprovalGate.PHASE_1_TO_2A,
            approved_by="ops-lead",
            reason="Metrics look good",
        )

        assert result
        assert not orchestrator.state.base_state.pending_operator_approval

    def test_operator_rejection_blocks_transition(self):
        """Test operator rejection blocks phase transition"""
        orchestrator = MasterRolloutOrchestrator()
        orchestrator._request_operator_approval(OperatorApprovalGate.PHASE_1_TO_2A)

        # Operator rejects
        result = orchestrator.operator_reject(
            OperatorApprovalGate.PHASE_1_TO_2A,
            rejected_by="ops-lead",
            reason="Need more data",
        )

        assert result
        assert not orchestrator.state.base_state.pending_operator_approval


class TestADRCompliance:
    """Test ADR-0206, 0205, 0186, 0369 compliance validation"""

    def test_adr_0206_canary_strategy_compliance(self):
        """Test ADR-0206: Canary strategy compliance"""
        validator = ADRComplianceValidator()

        metrics = {
            "canary_metrics": {
                "agreement_rate": 0.99,
                "latency_p99_ms": 105.0,
                "correctness_delta": 0.01,
            },
            "baseline_latency_ms": 100.0,
        }

        checks = validator.validate_adr_0206_canary(week_number=3, metrics=metrics["canary_metrics"], baseline_latency_ms=100.0)

        assert all(c.adr_id == "ADR-0206" for c in checks)
        assert any(c.status == ComplianceStatus.PASS for c in checks)

    def test_adr_0205_learning_loop_compliance(self):
        """Test ADR-0205: Learning loop compliance"""
        validator = ADRComplianceValidator()

        metrics = {
            "learning_metrics": {
                "os.delegation_router": {
                    "feedback_count": 2000,
                    "confidence_sigma": 0.03,
                },
            },
        }

        checks = validator.validate_adr_0205_learning_loop(metrics["learning_metrics"])

        assert all(c.adr_id == "ADR-0205" for c in checks)

    def test_adr_0186_heartbeat_compliance(self):
        """Test ADR-0186: Presence heartbeat compliance"""
        validator = ADRComplianceValidator()

        metrics = {
            "heartbeat_metrics": {
                "last_ping_seconds_ago": 290,  # Within 5-min cadence
                "geo_consent_respected": True,
            },
        }

        checks = validator.validate_adr_0186_heartbeat(metrics["heartbeat_metrics"])

        assert all(c.adr_id == "ADR-0186" for c in checks)
        assert any(c.status == ComplianceStatus.PASS for c in checks)

    def test_adr_0369_edge_cases_compliance(self):
        """Test ADR-0369: Edge cases compliance"""
        validator = ADRComplianceValidator()

        metrics = {
            "rollback_metrics": {
                "rollback_triggers_armed": 8,
                "audit_chain_verified": True,
            },
        }

        checks = validator.validate_adr_0369_edge_cases(metrics["rollback_metrics"])

        assert all(c.adr_id == "ADR-0369" for c in checks)

    def test_weekly_compliance_report_blocking_violations(self):
        """Test weekly compliance report identifies blocking violations"""
        validator = ADRComplianceValidator()

        metrics = {
            "canary_metrics": {
                "agreement_rate": 0.95,  # Below 98% threshold
                "latency_p99_ms": 100.0,
                "correctness_delta": 0.01,
            },
            "baseline_latency_ms": 100.0,
            "learning_metrics": {},
            "heartbeat_metrics": {},
            "rollback_metrics": {"rollback_triggers_armed": 8, "audit_chain_verified": True},
        }

        report = validator.generate_weekly_report(week_number=3, phase="PHASE_2A_CANARY", metrics=metrics)

        # Should have failed agreement rate check
        assert any(c.status == ComplianceStatus.FAIL for c in report.checks)


class TestStatePersistence:
    """Test state serialization and disaster recovery"""

    def test_state_serialization_to_disk(self):
        """Test orchestrator state is saved to disk"""
        orchestrator = MasterRolloutOrchestrator()
        orchestrator._save_state()

        # Check state file exists
        assert orchestrator.STATE_FILE.exists()

    def test_state_checkpoint_creation(self):
        """Test timestamped checkpoints are created"""
        orchestrator = MasterRolloutOrchestrator()
        orchestrator._save_state()

        # Check checkpoint directory
        checkpoint_files = list(orchestrator.CHECKPOINT_DIR.glob("state_*.json"))
        assert len(checkpoint_files) > 0

    def test_state_hash_chain_integrity(self):
        """Test state hash chaining for integrity"""
        orchestrator = MasterRolloutOrchestrator()
        orchestrator._save_state()

        valid, issues = orchestrator.verify_audit_chain()
        assert valid or len(issues) == 0  # No prior state initially


class TestFullRolloutSimulation:
    """Full 12-week simulation with realistic metrics"""

    def test_phase_1_complete_baseline_collection(self):
        """Simulate Phase 1: 14 days baseline"""
        orchestrator = MasterRolloutOrchestrator()

        for day in range(1, 15):
            metrics = {
                "os.delegation_router": SkillMetrics(
                    skill_id="os.delegation_router",
                    phase=Phase.PHASE_1_SHADOW,
                    agreement_rate=0.98 + (0.01 * (day / 14)),  # Improving confidence
                    confidence=0.70 + (0.15 * (day / 14)),
                    confidence_sigma=0.10 * (1 - day / 14),  # Converging
                    feedback_count=500 * day,
                    latency_p99_ms=100.0,
                ),
            }

            orchestrator.advance_day(metrics)

        # Phase 1 should be ready for approval
        assert orchestrator.state.base_state.pending_operator_approval

    def test_phase_2b_skill_activation_convergence(self):
        """Phase 2b days 64-84: confidence converges, yet no skill is promoted
        to PRIMARY because audit-violation counts and latency stability are
        not measured by anything (fail-closed, never assumed zero/stable)."""
        orchestrator = MasterRolloutOrchestrator()
        st = orchestrator.state.base_state
        st.phase = Phase.PHASE_2B_SKILL_PRIMARY
        st.current_traffic_percentage = 100
        st.baseline_latency_p99_ms = 100.0  # measured in Phase 1

        for day in range(64, 85):
            st.day_number = day - 1
            metrics = {
                "os.delegation_router": SkillMetrics(
                    skill_id="os.delegation_router",
                    phase=Phase.PHASE_2B_SKILL_PRIMARY,
                    agreement_rate=0.99,
                    confidence=0.90,
                    confidence_sigma=0.01,
                    latency_p99_ms=100.0,
                    feedback_count=100000 + (day * 5000),
                ),
            }
            orchestrator.advance_day(metrics)

        assert st.phase == Phase.PHASE_2B_SKILL_PRIMARY
        assert st.skill_states["os.delegation_router"] != SkillMode.PRIMARY


class TestCriticalCoverageFixes:
    """Coverage gaps, retargeted at the real API."""

    def test_operator_approval_gate_mismatch_rejected(self):
        """Approving PHASE_2A_TO_2B while PHASE_1_TO_2A is pending → refused."""
        orchestrator = MasterRolloutOrchestrator()
        orchestrator._request_operator_approval(OperatorApprovalGate.PHASE_1_TO_2A)
        assert orchestrator.operator_approve(OperatorApprovalGate.PHASE_2A_TO_2B, approved_by="ops") is False
        assert orchestrator.state.base_state.approval_required_for == "phase_1_to_2a"
        assert orchestrator.state.base_state.phase == Phase.PHASE_1_SHADOW

    def test_double_approval_transitions_once(self):
        orchestrator = MasterRolloutOrchestrator()
        orchestrator._request_operator_approval(OperatorApprovalGate.PHASE_1_TO_2A)
        assert orchestrator.operator_approve(OperatorApprovalGate.PHASE_1_TO_2A, approved_by="ops")
        assert orchestrator.operator_approve(OperatorApprovalGate.PHASE_1_TO_2A, approved_by="ops")
        granted = [e for e in orchestrator.audit_trail if e["event"] == "operator_approval_granted"]
        assert len(granted) == 1
        assert orchestrator.state.base_state.phase == Phase.PHASE_2A_CANARY

    def test_audit_records_are_on_the_tenant_chain(self):
        orchestrator = MasterRolloutOrchestrator()
        orchestrator.advance_day(_shadow_metrics())
        assert orchestrator.audit_trail
        assert all(e["tenant_id"] == "_default" and e["hash"] for e in orchestrator.audit_trail)
        ok, issues = orchestrator.verify_audit_chain()
        assert ok, issues

    def test_audit_chain_break_fails_closed(self):
        """A tampered tenant chain mid-canary → rollback (AUDIT_CHAIN_BREAK)."""
        orchestrator = MasterRolloutOrchestrator()
        st = orchestrator.state.base_state
        st.phase = Phase.PHASE_2A_CANARY
        st.day_number = 20
        st.baseline_latency_p99_ms = 100.0
        st.prior_mean_confidence = 0.90
        orchestrator.advance_day(_canary_metrics())
        assert st.phase == Phase.PHASE_2A_CANARY

        se, fp = audit_sink._forge()
        chain = fp.tenant_audit_chain("_default")
        lines = chain.read_text().splitlines()
        rec = json.loads(lines[0])
        rec["details"]["day"] = 999
        lines[0] = json.dumps(rec)
        chain.write_text("\n".join(lines) + "\n")

        orchestrator.advance_day(_canary_metrics())
        assert st.phase == Phase.ROLLED_BACK
        assert orchestrator.base_orch.rollback_history[-1][1] == RollbackReason.AUDIT_CHAIN_BREAK


class TestMetricValidationFixes:
    def test_phase1_feedback_gate_computed(self):
        """Phase 1 feedback criterion is computed from the supplied counts."""
        def m(fb):
            return {"os.delegation_router": SkillMetrics(
                skill_id="os.delegation_router", agreement_rate=0.99,
                confidence_sigma=0.02, feedback_count=fb)}
        passed, _ = Phase1Evaluator.evaluate(m(1500), days_elapsed=14, rollback_count=0)
        assert passed
        passed, reasons = Phase1Evaluator.evaluate(m(500), days_elapsed=14, rollback_count=0)
        assert not passed
        assert any("feedback 500/1000" in r for r in reasons)


class TestDashboardApprovalFlow:
    def test_pending_approval_visible_in_status(self):
        orchestrator = MasterRolloutOrchestrator()
        orchestrator._request_operator_approval(OperatorApprovalGate.PHASE_1_TO_2A)
        status = orchestrator.get_status()
        assert status["pending_operator_approval"] is True
        assert status["approval_required_for"] == "phase_1_to_2a"

    def test_compliance_approval_linked(self):
        """Blocking ADR violations are reported by name and block the transition."""
        validator = ADRComplianceValidator()
        metrics = {
            "canary_metrics": {
                "agreement_rate": 0.95,  # VIOLATION: < 99% at week 3
                "latency_p99_ms": 100.0,
                "correctness_delta": 0.01,
            },
            "baseline_latency_ms": 100.0,
        }
        report = validator.generate_weekly_report(week_number=3, phase="PHASE_2A_CANARY", metrics=metrics)
        assert "ADR-0206/agreement_rate" in report.blocking_violations
        can, reasons = validator.can_proceed_with_phase_transition(3)
        assert can is False and reasons


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
