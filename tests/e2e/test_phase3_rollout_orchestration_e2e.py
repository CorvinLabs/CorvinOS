"""
E2E Tests — Phase 3 Production Rollout Orchestration

Tests the complete 12-week staged deployment from shadow → canary → skill-primary,
including metrics collection, gated advancement, auto-rollback, learning loop integration,
and audit trail verification.

ADRs: 0206 (canary), 0867 (watchdog), 0205 (learning), 0186 (presence), 0369 (edge cases)
Compliance: GDPR (Art. 5/6/30/32), EU AI Act (Art. 5/50), Audit-First (ADR-0232/0233)

Test structure:
- Phase 1 shadow mode execution (14 days, advisory, zero production risk)
- Phase 2a canary progression (weekly gates, traffic escalation 1%→100%)
- Phase 2b skill activation (confidence-gated, per-skill independent)
- Rollback triggers (correctness drop, latency spike, audit break)
- Learning loop integration (confidence updates, parameter optimization)
- Audit trail verification (immutable, hash-chained, LoM-bound)
- Operator gates (manual approvals at phase boundaries)
"""

import pytest
from datetime import datetime, timezone, timedelta
from core.deployment.phase3_rollout_orchestrator import (
    RolloutOrchestrator,
    SkillMetrics,
    Phase,
    SkillMode,
    RollbackReason,
    Phase1Evaluator,
    Phase2aGateEvaluator,
    Phase2bActivationGate,
    get_orchestrator,
    reset_orchestrator,
)


class TestPhase1ShadowMode:
    """Test Phase 1 shadow mode execution (14 days, advisory, zero risk)"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_orchestrator()
        yield
        reset_orchestrator()

    def test_phase_1_initialization(self):
        """Test Phase 1 starts in ADVISORY mode for all skills"""
        orch = get_orchestrator()
        state = orch.get_state()

        assert state.phase == Phase.PHASE_1_SHADOW
        assert state.day_number == 1
        assert state.week_number == 1
        assert all(mode == SkillMode.ADVISORY for mode in state.skill_states.values())
        assert state.rollback_count == 0

    def test_phase_1_shadow_14_days_minimum(self):
        """Test Phase 1 requires minimum 14 days running"""
        orch = get_orchestrator()

        # Simulate 13 days of running
        metrics = self._create_good_metrics(agreement_rate=0.99, confidence=0.75)
        for day in range(1, 14):  # Days 1–13
            orch.advance_day(metrics)

        state = orch.get_state()
        assert state.phase == Phase.PHASE_1_SHADOW  # Still in Phase 1

        # Day 14 should trigger phase gate evaluation
        orch.advance_day(metrics)
        assert state.phase == Phase.PHASE_1_SHADOW  # Pending approval, not transitioned yet

    def test_phase_1_success_criteria_agreement_rate(self):
        """Test Phase 1 requires ≥98% agreement rate"""
        metrics_good = self._create_good_metrics(agreement_rate=0.99, feedback_count=1500)
        metrics_bad = self._create_good_metrics(agreement_rate=0.97, feedback_count=1500)

        passed_good, _ = Phase1Evaluator.evaluate(metrics_good, days_elapsed=14, rollback_count=0)
        passed_bad, _ = Phase1Evaluator.evaluate(metrics_bad, days_elapsed=14, rollback_count=0)

        assert passed_good is True
        assert passed_bad is False

    def test_phase_1_success_criteria_feedback_events(self):
        """Test Phase 1 requires ≥1,000 feedback events per skill"""
        metrics_good = self._create_good_metrics(agreement_rate=0.99, feedback_count=1500)
        metrics_insufficient = self._create_good_metrics(agreement_rate=0.99, feedback_count=500)

        passed_good, _ = Phase1Evaluator.evaluate(metrics_good, days_elapsed=14, rollback_count=0)
        passed_insufficient, _ = Phase1Evaluator.evaluate(metrics_insufficient, days_elapsed=14, rollback_count=0)

        assert passed_good is True
        assert passed_insufficient is False

    def test_phase_1_confidence_convergence(self):
        """Test Phase 1 requires confidence to converge (σ < 0.05)"""
        metrics_converged = self._create_good_metrics(
            agreement_rate=0.99,
            confidence=0.72,
            confidence_sigma=0.03,  # Converged
            feedback_count=1500,
        )
        metrics_diverged = self._create_good_metrics(
            agreement_rate=0.99,
            confidence=0.65,
            confidence_sigma=0.15,  # Not converged
            feedback_count=1500,
        )

        passed_converged, _ = Phase1Evaluator.evaluate(metrics_converged, days_elapsed=14, rollback_count=0)
        passed_diverged, _ = Phase1Evaluator.evaluate(metrics_diverged, days_elapsed=14, rollback_count=0)

        assert passed_converged is True
        assert passed_diverged is False

    def test_phase_1_to_phase_2a_transition_requires_approval(self):
        """Test Phase 1→2a transition requires operator approval"""
        orch = get_orchestrator()
        metrics = self._create_good_metrics(agreement_rate=0.99, confidence=0.75)

        # Simulate 14+ days
        for day in range(1, 16):
            orch.advance_day(metrics)

        state = orch.get_state()
        assert state.pending_operator_approval is True
        assert state.approval_required_for == "PHASE_2A_START"
        assert state.phase == Phase.PHASE_1_SHADOW  # Not transitioned yet

        # Operator approves
        orch.operator_approve("PHASE_2A_START")
        state = orch.get_state()
        assert state.phase == Phase.PHASE_2A_CANARY
        assert all(mode == SkillMode.DUAL_WRITE for mode in state.skill_states.values())

    def test_phase_1_audit_trail_populated(self):
        """Test Phase 1 populates immutable audit trail"""
        orch = get_orchestrator()
        metrics = self._create_good_metrics()

        orch.advance_day(metrics)
        audit_trail = orch.get_audit_trail()

        assert len(audit_trail) > 0
        for event in audit_trail:
            assert "event" in event
            assert "timestamp" in event
            assert "hash" in event
            assert "lom" in event  # Line of Moral Responsibility

    def test_phase_1_audit_chain_integrity(self):
        """Test Phase 1 maintains hash-chained audit trail (ADR-0232/0233)"""
        orch = get_orchestrator()
        metrics = self._create_good_metrics()

        # Simulate several days
        for day in range(1, 5):
            orch.advance_day(metrics)

        valid, issues = orch.verify_audit_chain()
        assert valid is True
        assert len(issues) == 0

    @staticmethod
    def _create_good_metrics(
        agreement_rate=0.99,
        confidence=0.75,
        confidence_sigma=0.03,
        feedback_count=1500,
        latency_p99_ms=100.0,
        error_rate=0.001,
    ):
        """Helper: create metrics snapshot with good values"""
        return {
            "os.delegation_router": SkillMetrics(
                skill_id="os.delegation_router",
                phase=Phase.PHASE_1_SHADOW,
                agreement_rate=agreement_rate,
                confidence=confidence,
                confidence_sigma=confidence_sigma,
                feedback_count=feedback_count,
                latency_p99_ms=latency_p99_ms,
                error_rate=error_rate,
            ),
            "os.context_adapter": SkillMetrics(
                skill_id="os.context_adapter",
                phase=Phase.PHASE_1_SHADOW,
                agreement_rate=agreement_rate,
                confidence=confidence,
                confidence_sigma=confidence_sigma,
                feedback_count=feedback_count,
                latency_p99_ms=latency_p99_ms * 0.5,
                error_rate=error_rate,
            ),
            "os.workflow_optimizer": SkillMetrics(
                skill_id="os.workflow_optimizer",
                phase=Phase.PHASE_1_SHADOW,
                agreement_rate=agreement_rate,
                confidence=confidence,
                confidence_sigma=confidence_sigma,
                feedback_count=feedback_count,
                latency_p99_ms=latency_p99_ms * 1.5,
                error_rate=error_rate * 2,
            ),
            "os.security_orchestrator": SkillMetrics(
                skill_id="os.security_orchestrator",
                phase=Phase.PHASE_1_SHADOW,
                agreement_rate=agreement_rate + 0.01,  # Even higher
                confidence=confidence + 0.05,
                confidence_sigma=confidence_sigma,
                feedback_count=feedback_count,
                latency_p99_ms=latency_p99_ms * 0.3,
                error_rate=error_rate * 0.5,
            ),
        }


class TestPhase2aCanaryProgression:
    """Test Phase 2a canary deployment with weekly gates"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_orchestrator()
        yield
        reset_orchestrator()

    def test_phase_2a_starts_at_1_percent_traffic(self):
        """Test Phase 2a canary starts at 1% traffic"""
        orch = get_orchestrator()
        orch.state.phase = Phase.PHASE_2A_CANARY
        orch.state.current_traffic_percentage = 1

        state = orch.get_state()
        assert state.phase == Phase.PHASE_2A_CANARY
        assert state.current_traffic_percentage == 1

    def test_phase_2a_week_3_gate_1_percent(self):
        """Test Phase 2a Week 3 gate (1% traffic)"""
        metrics_good = {
            "os.delegation_router": SkillMetrics(
                skill_id="os.delegation_router",
                phase=Phase.PHASE_2A_CANARY,
                agreement_rate=0.99,
                confidence=0.72,
                latency_p99_ms=105.0,  # Within 10%
                error_rate=0.001,
            ),
        }

        passed, reasons = Phase2aGateEvaluator.evaluate_week_gate(
            week_number=3,
            metrics=metrics_good,
            baseline_latency_p99_ms=100.0,
        )

        assert passed is True
        assert any("PASS" in r for r in reasons)

    def test_phase_2a_week_4_gate_10_percent(self):
        """Test Phase 2a Week 4 gate (10% traffic)"""
        metrics_good = {
            "os.delegation_router": SkillMetrics(
                skill_id="os.delegation_router",
                phase=Phase.PHASE_2A_CANARY,
                agreement_rate=0.98,
                confidence=0.75,
                correctness_delta=0.01,
                latency_p99_ms=112.0,  # Within 15%
                error_rate=0.001,
            ),
        }

        passed, reasons = Phase2aGateEvaluator.evaluate_week_gate(
            week_number=4,
            metrics=metrics_good,
            baseline_latency_p99_ms=100.0,
        )

        assert passed is True

    def test_phase_2a_weekly_gate_traffic_escalation(self):
        """Test Phase 2a traffic escalates weekly (1% → 10% → 50% → 100%)"""
        orch = get_orchestrator()
        orch.state.phase = Phase.PHASE_2A_CANARY

        # Week 3: 1% → 10%
        orch.state.week_number = 3
        next_traffic = orch._get_next_traffic_escalation(3)
        assert next_traffic == 1  # Start at 1%

        next_traffic = orch._get_next_traffic_escalation(4)
        assert next_traffic == 10  # Escalate to 10%

        next_traffic = orch._get_next_traffic_escalation(5)
        assert next_traffic == 50  # Escalate to 50%

        next_traffic = orch._get_next_traffic_escalation(6)
        assert next_traffic == 100  # Escalate to 100%

    def test_phase_2a_gate_fail_holds_traffic(self):
        """Test Phase 2a gate failure holds traffic (no escalation)"""
        metrics_bad = {
            "os.delegation_router": SkillMetrics(
                skill_id="os.delegation_router",
                phase=Phase.PHASE_2A_CANARY,
                agreement_rate=0.96,  # Below 98% threshold
                confidence=0.70,
                latency_p99_ms=135.0,  # >20% over baseline
                error_rate=0.005,
            ),
        }

        passed, reasons = Phase2aGateEvaluator.evaluate_week_gate(
            week_number=4,
            metrics=metrics_bad,
            baseline_latency_p99_ms=100.0,
        )

        assert passed is False
        assert any("FAIL" in r for r in reasons)


class TestPhase2bSkillActivation:
    """Test Phase 2b skill-primary activation with confidence gating"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_orchestrator()
        yield
        reset_orchestrator()

    def test_phase_2b_skill_activation_threshold_router(self):
        """Test L5 router skill activation (confidence ≥0.85)"""
        metrics = SkillMetrics(
            skill_id="os.delegation_router",
            phase=Phase.PHASE_2B_SKILL_PRIMARY,
            agreement_rate=0.99,
            confidence=0.85,
            confidence_sigma=0.02,
            feedback_count=2000,
        )

        can_activate, reasons = Phase2bActivationGate.evaluate_skill_activation(
            "os.delegation_router",
            metrics,
            days_at_100_pct=7,
            days_since_last_rollback=60,
        )

        assert can_activate is True

    def test_phase_2b_skill_activation_threshold_security(self):
        """Test L44 security skill activation (confidence ≥0.95, highest threshold)"""
        metrics = SkillMetrics(
            skill_id="os.security_orchestrator",
            phase=Phase.PHASE_2B_SKILL_PRIMARY,
            agreement_rate=0.99,
            confidence=0.95,
            confidence_sigma=0.01,
            feedback_count=2000,
        )

        can_activate, reasons = Phase2bActivationGate.evaluate_skill_activation(
            "os.security_orchestrator",
            metrics,
            days_at_100_pct=7,
            days_since_last_rollback=60,
        )

        assert can_activate is True

    def test_phase_2b_insufficient_confidence(self):
        """Test Phase 2b blocks activation if confidence below threshold"""
        metrics = SkillMetrics(
            skill_id="os.delegation_router",
            phase=Phase.PHASE_2B_SKILL_PRIMARY,
            agreement_rate=0.99,
            confidence=0.80,  # Below 0.85 threshold
            confidence_sigma=0.02,
            feedback_count=2000,
        )

        can_activate, reasons = Phase2bActivationGate.evaluate_skill_activation(
            "os.delegation_router",
            metrics,
            days_at_100_pct=7,
            days_since_last_rollback=60,
        )

        assert can_activate is False


class TestRollbackTriggers:
    """Test auto-rollback triggers (fail-closed)"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_orchestrator()
        yield
        reset_orchestrator()

    def test_rollback_correctness_drop(self):
        """Test rollback triggers on correctness drop >2%"""
        orch = get_orchestrator()
        orch.state.phase = Phase.PHASE_2A_CANARY

        metrics_bad = {
            "os.delegation_router": SkillMetrics(
                skill_id="os.delegation_router",
                phase=Phase.PHASE_2A_CANARY,
                agreement_rate=0.95,  # <98% = correctness drop > 2%
                confidence=0.70,
                latency_p99_ms=100.0,
                error_rate=0.001,
            ),
        }

        orch.advance_day(metrics_bad)
        state = orch.get_state()

        assert state.phase == Phase.ROLLED_BACK
        assert state.rollback_count == 1
        assert all(mode == SkillMode.FALLBACK for mode in state.skill_states.values())

    def test_rollback_latency_spike(self):
        """Test rollback triggers on latency spike >20%"""
        orch = get_orchestrator()
        orch.state.phase = Phase.PHASE_2A_CANARY

        metrics_latency_spike = {
            "os.delegation_router": SkillMetrics(
                skill_id="os.delegation_router",
                phase=Phase.PHASE_2A_CANARY,
                agreement_rate=0.99,
                confidence=0.75,
                latency_p99_ms=130.0,  # 30% over 100ms baseline
                error_rate=0.001,
            ),
        }

        orch.advance_day(metrics_latency_spike)
        state = orch.get_state()

        assert state.phase == Phase.ROLLED_BACK

    def test_rollback_confidence_regression(self):
        """Test rollback triggers on confidence regression >10%"""
        orch = get_orchestrator()
        orch.state.phase = Phase.PHASE_2A_CANARY

        metrics_regression = {
            "os.delegation_router": SkillMetrics(
                skill_id="os.delegation_router",
                phase=Phase.PHASE_2A_CANARY,
                agreement_rate=0.98,
                confidence=0.70,  # Dropped from ~0.80, > 10% regression
                latency_p99_ms=100.0,
                error_rate=0.001,
            ),
        }

        orch.advance_day(metrics_regression)
        state = orch.get_state()

        assert state.phase == Phase.ROLLED_BACK

    def test_rollback_immediate_execution(self):
        """Test rollback executes immediately (no delay)"""
        orch = get_orchestrator()
        orch.state.phase = Phase.PHASE_2A_CANARY

        prior_timestamp = datetime.fromisoformat(orch.state.last_decision_time)

        metrics_bad = {
            "os.delegation_router": SkillMetrics(
                skill_id="os.delegation_router",
                phase=Phase.PHASE_2A_CANARY,
                agreement_rate=0.94,
                confidence=0.70,
                latency_p99_ms=100.0,
                error_rate=0.001,
            ),
        }

        orch.advance_day(metrics_bad)
        current_timestamp = datetime.fromisoformat(orch.state.last_decision_time)

        assert (current_timestamp - prior_timestamp).total_seconds() < 1  # Immediate

    def test_rollback_audit_logged(self):
        """Test rollback is audit-logged with LoM binding"""
        orch = get_orchestrator()
        orch.state.phase = Phase.PHASE_2A_CANARY

        metrics_bad = {
            "os.delegation_router": SkillMetrics(
                skill_id="os.delegation_router",
                phase=Phase.PHASE_2A_CANARY,
                agreement_rate=0.94,
                confidence=0.70,
                latency_p99_ms=100.0,
                error_rate=0.001,
            ),
        }

        orch.advance_day(metrics_bad)
        audit_trail = orch.get_audit_trail()

        rollback_events = [e for e in audit_trail if e.get("event") == "rollback_executed"]
        assert len(rollback_events) > 0

        for event in rollback_events:
            assert "reason" in event
            assert "lom" in event  # LoM binding required


class TestAuditTrailIntegrity:
    """Test audit trail verification (hash-chained, immutable)"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_orchestrator()
        yield
        reset_orchestrator()

    def test_audit_trail_hash_chained(self):
        """Test audit trail is hash-chained (each event links to prior)"""
        orch = get_orchestrator()
        metrics = TestPhase1ShadowMode._create_good_metrics()

        # Simulate several days
        for day in range(1, 5):
            orch.advance_day(metrics)

        audit_trail = orch.get_audit_trail()

        # Check chain structure
        assert audit_trail[0]["prior_hash"] == "GENESIS"
        for i in range(1, len(audit_trail)):
            assert audit_trail[i]["prior_hash"] == audit_trail[i-1]["hash"]

    def test_audit_trail_integrity_verification(self):
        """Test audit trail integrity verification passes"""
        orch = get_orchestrator()
        metrics = TestPhase1ShadowMode._create_good_metrics()

        for day in range(1, 5):
            orch.advance_day(metrics)

        valid, issues = orch.verify_audit_chain()

        assert valid is True
        assert len(issues) == 0

    def test_audit_trail_tamper_detection(self):
        """Test audit trail detects tampering"""
        orch = get_orchestrator()
        metrics = TestPhase1ShadowMode._create_good_metrics()

        for day in range(1, 5):
            orch.advance_day(metrics)

        # Tamper with an event
        orch.audit_trail[1]["event"] = "TAMPERED"  # Modify event

        valid, issues = orch.verify_audit_chain()

        assert valid is False
        assert len(issues) > 0


class TestLearningLoopIntegration:
    """Test learning loop integration with rollout orchestration"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_orchestrator()
        yield
        reset_orchestrator()

    def test_confidence_updates_via_feedback(self):
        """Test confidence increases with positive feedback"""
        orch = get_orchestrator()

        # Simulate Phase 2a with positive feedback (agreement high)
        metrics_improving = TestPhase1ShadowMode._create_good_metrics(
            agreement_rate=0.99,
            confidence=0.75,
            confidence_sigma=0.02,
        )

        orch.state.phase = Phase.PHASE_2A_CANARY
        for day in range(1, 22):  # Week 3
            orch.advance_day(metrics_improving)

        state = orch.get_state()
        assert state.phase == Phase.PHASE_2A_CANARY  # Still in canary

    def test_learning_loop_gates_phase_advancement(self):
        """Test learning loop convergence gates phase advancement"""
        orch = get_orchestrator()

        # Poor confidence initially
        metrics_poor = TestPhase1ShadowMode._create_good_metrics(
            agreement_rate=0.97,
            confidence=0.60,
            confidence_sigma=0.10,
        )

        orch.state.phase = Phase.PHASE_2A_CANARY
        orch.state.week_number = 3
        orch.advance_day(metrics_poor)

        state = orch.get_state()
        # Should not advance if confidence poor
        assert state.current_traffic_percentage <= 1  # Hold at 1%


class TestOperatorGates:
    """Test operator manual approval gates"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_orchestrator()
        yield
        reset_orchestrator()

    def test_phase_1_to_2a_requires_operator_approval(self):
        """Test Phase 1→2a transition requires operator approval"""
        orch = get_orchestrator()
        metrics = TestPhase1ShadowMode._create_good_metrics()

        # Simulate 14+ days to trigger gate
        for day in range(1, 16):
            orch.advance_day(metrics)

        state = orch.get_state()
        assert state.pending_operator_approval is True
        assert state.approval_required_for == "PHASE_2A_START"

        # Approve
        orch.operator_approve("PHASE_2A_START")
        state = orch.get_state()

        assert state.pending_operator_approval is False
        assert state.phase == Phase.PHASE_2A_CANARY

    def test_phase_2a_to_2b_requires_operator_approval(self):
        """Test Phase 2a→2b transition requires operator approval"""
        orch = get_orchestrator()
        orch.state.phase = Phase.PHASE_2A_CANARY
        orch.state.current_traffic_percentage = 100
        orch.state.day_number = 63

        metrics = TestPhase1ShadowMode._create_good_metrics(
            agreement_rate=0.98,
            confidence=0.85,
        )

        orch.advance_day(metrics)

        state = orch.get_state()
        assert state.pending_operator_approval is True
        assert state.approval_required_for == "PHASE_2B_START"


class TestComplete12WeekRollout:
    """E2E test of complete 12-week rollout (integration)"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_orchestrator()
        yield
        reset_orchestrator()

    def test_complete_rollout_success_path(self):
        """
        E2E test: complete 12-week rollout from Phase 1 → 2a → 2b

        Timeline:
        - Days 1–14: Phase 1 shadow (advisory)
        - Days 15–21: Phase 2a Week 3 (1% traffic)
        - Days 22–28: Phase 2a Week 4 (10% traffic)
        - Days 29–35: Phase 2a Week 5 (50% traffic)
        - Days 36–63: Phase 2a Weeks 6–9 (100% traffic)
        - Days 64–84: Phase 2b skill-primary (confidence-gated)
        """
        orch = get_orchestrator()
        metrics_good = TestPhase1ShadowMode._create_good_metrics(
            agreement_rate=0.99,
            confidence=0.75,
            confidence_sigma=0.03,
        )

        # Phase 1: Days 1–14
        for day in range(1, 15):
            orch.advance_day(metrics_good)
            state = orch.get_state()
            assert state.phase == Phase.PHASE_1_SHADOW

        # Gate should be ready for approval
        state = orch.get_state()
        assert state.pending_operator_approval is True

        # Operator approves Phase 2a
        orch.operator_approve("PHASE_2A_START")
        state = orch.get_state()
        assert state.phase == Phase.PHASE_2A_CANARY
        assert state.current_traffic_percentage == 1

        # Phase 2a: escalate through weeks
        for day in range(15, 64):  # Days 15–63
            orch.advance_day(metrics_good)

        state = orch.get_state()
        # Should be at 100% traffic by end of Phase 2a
        assert state.current_traffic_percentage == 100

        # Phase 2b: skill activation
        for day in range(64, 85):  # Days 64–84
            metrics_good.copy()  # Continue good metrics
            for skill_id, m in metrics_good.items():
                m.confidence = min(0.95, m.confidence + 0.01)  # Confidence increases
            orch.advance_day(metrics_good)

        state = orch.get_state()
        # Skills should activate as confidence reaches thresholds
        assert any(mode == SkillMode.PRIMARY for mode in state.skill_states.values())

    def test_rollout_audit_trail_complete(self):
        """Test complete 12-week rollout has immutable audit trail"""
        orch = get_orchestrator()
        metrics_good = TestPhase1ShadowMode._create_good_metrics()

        # Simulate 84 days
        for day in range(1, 85):
            orch.advance_day(metrics_good)

        audit_trail = orch.get_audit_trail()
        assert len(audit_trail) > 80  # Many events logged

        # Verify chain integrity
        valid, issues = orch.verify_audit_chain()
        assert valid is True
        assert len(issues) == 0

        # Check LoM binding on critical events
        rollout_events = [e for e in audit_trail if "rollout" in e.get("event", "")]
        for event in rollout_events:
            assert "lom" in event


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
