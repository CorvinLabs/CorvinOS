"""
E2E Tests for Canary Traffic Escalation — Phase 2a Automation

Tests: Week 3 (1%) → Week 3.5 (10%) → Week 4 (50%) → Week 4.75 (100%) → Phase 2b

Scenarios:
  1. Happy path: All gates pass, escalate at each stage
  2. Hold scenario: Metrics not ready, hold at current level
  3. Rollback scenario: Error rate spike, rollback to previous level
  4. Multi-week progression: Full 4-week journey

Related: ADR-0206, ADR-0532, ADR-0867
Compliance: GDPR Art. 30/32 (audit trail), EU AI Act Art. 50 (transparency + LoM)
"""

import pytest
import json
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any

# Import canary components
from core.deployment.canary_traffic_controller import (
    CanaryTrafficController,
    TrafficMetrics,
    CanaryState,
    CanaryHealth,
    EscalationDecision,
    create_controller,
)
from core.deployment.canary_validation import (
    CanaryValidationEngine,
    ValidationRunResult,
    GateEvaluation,
    GateStatus,
    AuditEventType,
    LocalFileAuditBackend,
    create_validation_engine,
)


@pytest.fixture
def temp_state_file():
    """Temporary canary state file for testing"""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
        return f.name


@pytest.fixture
def temp_audit_file():
    """Temporary audit file for testing"""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as f:
        return f.name


@pytest.fixture
def controller(temp_state_file):
    """Create a fresh canary controller for testing"""
    return CanaryTrafficController(state_file=temp_state_file)


@pytest.fixture
def validation_engine(temp_audit_file):
    """Create a fresh validation engine with local audit backend"""
    backend = LocalFileAuditBackend(audit_file=temp_audit_file)
    return CanaryValidationEngine(audit_backend=backend)


@pytest.fixture
def healthy_metrics_1pct():
    """Healthy metrics at 1% traffic"""
    return {
        "p99_latency_ms": 150.0,      # Well below 500ms pass threshold
        "error_rate_pct": 0.02,       # Well below 0.1% pass threshold
        "agreement_rate_pct": 98.5,   # Above 95% pass threshold
    }


@pytest.fixture
def healthy_metrics_10pct():
    """Healthy metrics at 10% traffic"""
    return {
        "p99_latency_ms": 250.0,      # Below 400ms pass threshold
        "error_rate_pct": 0.03,       # Below 0.05% pass threshold
        "agreement_rate_pct": 97.8,   # Above 97% pass threshold
    }


@pytest.fixture
def degraded_metrics():
    """Degraded metrics (failing gates)"""
    return {
        "p99_latency_ms": 750.0,      # Exceeds 500ms pass threshold
        "error_rate_pct": 0.08,       # Exceeds 0.1% pass threshold
        "agreement_rate_pct": 88.0,   # Below 95% pass threshold
    }


@pytest.fixture
def critical_metrics():
    """Critical metrics (rollback trigger)"""
    return {
        "p99_latency_ms": 2000.0,     # 4x baseline
        "error_rate_pct": 2.5,        # 2.5% — critical failure
        "agreement_rate_pct": 60.0,   # Severe disagreement
    }


class TestCanaryTrafficControllerBasics:
    """Test canary traffic controller basic operations"""

    def test_controller_initializes_with_zero_traffic(self, controller):
        """Controller should start at 0% traffic"""
        assert controller.state.current_traffic_percent == 0
        assert controller.state.status == CanaryHealth.UNKNOWN
        assert not controller.state.phase_2b_activated

    def test_start_canary_at_1_percent(self, controller):
        """Starting canary should set initial traffic"""
        state = controller.start_canary(initial_traffic=1)

        assert state.current_traffic_percent == 1
        assert state.status == CanaryHealth.HEALTHY
        assert state.started_at is not None
        assert len(state.decision_history) == 1
        assert state.decision_history[0]["decision"] == "escalate"

    def test_start_canary_persists_to_disk(self, controller, temp_state_file):
        """Starting canary should persist state to disk"""
        controller.start_canary(initial_traffic=1)

        # Verify file exists and contains correct data
        assert Path(temp_state_file).exists()
        with open(temp_state_file, "r") as f:
            data = json.load(f)
        assert data["current_traffic_percent"] == 1
        assert data["status"] == "healthy"

    def test_cannot_start_canary_twice(self, controller):
        """Starting canary twice should warn, not reset"""
        controller.start_canary(initial_traffic=1)
        initial_timestamp = controller.state.started_at

        # Try to start again
        controller.start_canary(initial_traffic=1)

        # Timestamp should not change
        assert controller.state.started_at == initial_timestamp


class TestCanaryValidationGates:
    """Test validation gate evaluation"""

    def test_evaluate_escalation_at_1pct_healthy(self, controller, healthy_metrics_1pct):
        """Healthy metrics at 1% should pass all gates"""
        controller.start_canary(initial_traffic=1)

        # Record metrics
        metrics = TrafficMetrics(
            timestamp=datetime.utcnow().isoformat() + "Z",
            traffic_percent=1,
            p99_latency_ms=healthy_metrics_1pct["p99_latency_ms"],
            error_rate_pct=healthy_metrics_1pct["error_rate_pct"],
            agreement_rate_pct=healthy_metrics_1pct["agreement_rate_pct"],
            total_requests=10000,
            error_count=2,
            agreement_count=9850,
        )
        controller.record_metrics(metrics)

        # Evaluate escalation
        decision, reason, latest_metrics = controller.evaluate_escalation()

        assert decision == EscalationDecision.ESCALATE
        assert "gates pass" in reason
        assert latest_metrics == metrics

    def test_evaluate_escalation_at_1pct_degraded(self, controller, degraded_metrics):
        """Degraded metrics at 1% should hold (not escalate)"""
        controller.start_canary(initial_traffic=1)

        # Record degraded metrics
        metrics = TrafficMetrics(
            timestamp=datetime.utcnow().isoformat() + "Z",
            traffic_percent=1,
            p99_latency_ms=degraded_metrics["p99_latency_ms"],
            error_rate_pct=degraded_metrics["error_rate_pct"],
            agreement_rate_pct=degraded_metrics["agreement_rate_pct"],
            total_requests=10000,
            error_count=8,
            agreement_count=8800,
        )
        controller.record_metrics(metrics)

        # Evaluate escalation
        decision, reason, _ = controller.evaluate_escalation()

        assert decision == EscalationDecision.HOLD
        assert "Gates failing" in reason

    def test_evaluate_escalation_no_metrics(self, controller):
        """Evaluating escalation without metrics should hold"""
        controller.start_canary(initial_traffic=1)

        # No metrics recorded
        decision, reason, metrics = controller.evaluate_escalation()

        assert decision == EscalationDecision.HOLD
        assert "No metrics" in reason
        assert metrics is None

    def test_rollback_on_high_error_rate(self, controller, critical_metrics):
        """Error rate > 1% should trigger rollback"""
        controller.start_canary(initial_traffic=1)

        # Record critical metrics
        metrics = TrafficMetrics(
            timestamp=datetime.utcnow().isoformat() + "Z",
            traffic_percent=1,
            p99_latency_ms=critical_metrics["p99_latency_ms"],
            error_rate_pct=critical_metrics["error_rate_pct"],
            agreement_rate_pct=critical_metrics["agreement_rate_pct"],
            total_requests=10000,
            error_count=250,
            agreement_count=6000,
        )
        controller.record_metrics(metrics)

        # Evaluate escalation
        decision, reason, _ = controller.evaluate_escalation()

        assert decision == EscalationDecision.ROLLBACK
        assert "Error rate" in reason and "ROLLBACK" in reason


class TestCanaryEscalation:
    """Test traffic escalation progression"""

    def test_escalate_1pct_to_10pct(self, controller, healthy_metrics_1pct):
        """Escalate from 1% to 10% traffic"""
        controller.start_canary(initial_traffic=1)

        # Record healthy metrics
        metrics = TrafficMetrics(
            timestamp=datetime.utcnow().isoformat() + "Z",
            traffic_percent=1,
            p99_latency_ms=healthy_metrics_1pct["p99_latency_ms"],
            error_rate_pct=healthy_metrics_1pct["error_rate_pct"],
            agreement_rate_pct=healthy_metrics_1pct["agreement_rate_pct"],
            total_requests=10000,
            error_count=2,
            agreement_count=9850,
        )
        controller.record_metrics(metrics)

        # Escalate
        success, reason, new_traffic = controller.escalate(skill_id="os.canary_router")

        assert success
        assert new_traffic == 10
        assert controller.state.current_traffic_percent == 10
        assert len(controller.state.decision_history) == 2  # start + escalate

    def test_escalate_10pct_to_50pct(self, controller, healthy_metrics_10pct):
        """Escalate from 10% to 50% traffic"""
        # Start and escalate to 10%
        controller.start_canary(initial_traffic=1)
        metrics_1pct = TrafficMetrics(
            timestamp=datetime.utcnow().isoformat() + "Z",
            traffic_percent=1,
            p99_latency_ms=100.0, error_rate_pct=0.01, agreement_rate_pct=99.0,
            total_requests=10000, error_count=1, agreement_count=9900,
        )
        controller.record_metrics(metrics_1pct)
        controller.escalate()

        # Now escalate to 50%
        metrics_10pct = TrafficMetrics(
            timestamp=datetime.utcnow().isoformat() + "Z",
            traffic_percent=10,
            p99_latency_ms=healthy_metrics_10pct["p99_latency_ms"],
            error_rate_pct=healthy_metrics_10pct["error_rate_pct"],
            agreement_rate_pct=healthy_metrics_10pct["agreement_rate_pct"],
            total_requests=100000,
            error_count=30,
            agreement_count=97800,
        )
        controller.record_metrics(metrics_10pct)

        success, reason, new_traffic = controller.escalate(skill_id="os.canary_router")

        assert success
        assert new_traffic == 50
        assert controller.state.current_traffic_percent == 50

    def test_escalate_50pct_to_100pct(self, controller):
        """Escalate from 50% to 100% and activate Phase 2b"""
        # Get to 50%
        controller.start_canary(initial_traffic=1)
        controller.state.current_traffic_percent = 50  # Skip ahead for this test

        # Record healthy metrics at 50%
        metrics = TrafficMetrics(
            timestamp=datetime.utcnow().isoformat() + "Z",
            traffic_percent=50,
            p99_latency_ms=200.0, error_rate_pct=0.01, agreement_rate_pct=99.0,
            total_requests=500000,
            error_count=50,
            agreement_count=495000,
        )
        controller.record_metrics(metrics)

        # Escalate to 100%
        success, reason, new_traffic = controller.escalate(skill_id="os.canary_router")

        assert success
        assert new_traffic == 100
        assert controller.state.phase_2b_activated
        assert "Phase 2b" in reason


class TestCanaryHoldScenario:
    """Test hold scenario (metrics not ready)"""

    def test_hold_when_metrics_not_ready(self, controller):
        """Hold decision when no metrics collected yet"""
        controller.start_canary(initial_traffic=1)

        # Don't record any metrics, just hold
        success, reason = controller.hold(reason="Waiting for stable metrics")

        assert success
        assert "Waiting for stable metrics" in reason
        assert controller.state.current_traffic_percent == 1  # No change
        assert len(controller.state.decision_history) == 2  # start + hold

    def test_hold_decision_persists(self, controller):
        """Hold decisions should be recorded and persisted"""
        controller.start_canary(initial_traffic=1)
        controller.hold(reason="Test hold")

        # Reload state
        state = controller.get_state()
        assert len(state.decision_history) == 2
        assert state.decision_history[1]["decision"] == "hold"


class TestCanaryRollbackScenario:
    """Test rollback scenario (error spike)"""

    def test_rollback_from_10pct_to_1pct(self, controller):
        """Rollback from 10% to 1% on degradation"""
        controller.start_canary(initial_traffic=1)
        controller.state.current_traffic_percent = 10

        # Rollback
        success, reason, rolled_back_to = controller.rollback(
            reason="Error rate spike detected",
            target_traffic=1
        )

        assert success
        assert rolled_back_to == 1
        assert controller.state.current_traffic_percent == 1
        assert controller.state.status == CanaryHealth.DEGRADED
        assert controller.state.rollback_reason == "Error rate spike detected"

    def test_rollback_fail_closed(self, controller, tmp_path):
        """Rollback fails gracefully if disk unavailable"""
        # Create a read-only file (simulate disk full)
        readonly_file = tmp_path / "readonly.json"
        readonly_file.write_text("{}")
        readonly_file.chmod(0o444)

        controller_ro = CanaryTrafficController(state_file=str(readonly_file))
        controller_ro.start_canary(initial_traffic=1)
        controller_ro.state.current_traffic_percent = 10

        # Attempt rollback (should fail gracefully)
        success, reason, rolled_back_to = controller_ro.rollback(
            reason="Test failure",
            target_traffic=1
        )

        # Fail-closed: returns false but doesn't crash
        assert not success
        assert rolled_back_to == 10  # Unchanged


class TestCanaryValidationEngine:
    """Test validation engine with audit logging"""

    def test_validation_engine_run_all_gates_pass(self, validation_engine, healthy_metrics_1pct):
        """Validation with all gates passing"""
        gates = [
            {"gate_name": "latency_ok", "metric_type": "p99_latency_ms", "threshold_pass": 500.0, "threshold_warn": 1000.0, "operator": "lt"},
            {"gate_name": "error_rate_ok", "metric_type": "error_rate_pct", "threshold_pass": 0.1, "threshold_warn": 1.0, "operator": "lt"},
            {"gate_name": "agreement_ok", "metric_type": "agreement_rate_pct", "threshold_pass": 95.0, "threshold_warn": 85.0, "operator": "gt"},
        ]

        result = validation_engine.run_validation(
            traffic_percent=1,
            gates=gates,
            metrics=healthy_metrics_1pct,
        )

        assert result.all_gates_pass
        assert result.overall_status == GateStatus.PASS
        assert result.decision_recommendation == "ESCALATE"
        assert len(result.gates_evaluated) == 3
        assert all(g.passed for g in result.gates_evaluated)

    def test_validation_engine_audit_events_emitted(self, validation_engine, healthy_metrics_1pct):
        """Validation engine should emit audit events"""
        gates = [
            {"gate_name": "latency_ok", "metric_type": "p99_latency_ms", "threshold_pass": 500.0, "threshold_warn": 1000.0, "operator": "lt"},
        ]

        result = validation_engine.run_validation(
            traffic_percent=1,
            gates=gates,
            metrics=healthy_metrics_1pct,
        )

        # Should have audit event hash
        assert result.audit_event_hash is not None
        assert len(result.audit_event_hash) == 64  # SHA256 hex string

    def test_decision_making_escalate(self, validation_engine):
        """Decision engine should recommend escalation"""
        result = ValidationRunResult(
            timestamp=datetime.utcnow().isoformat() + "Z",
            traffic_percent=1,
            gates_evaluated=[
                GateEvaluation("latency_ok", "latency", 150.0, 500.0, 1000.0, GateStatus.PASS, True),
                GateEvaluation("error_ok", "error", 0.02, 0.1, 1.0, GateStatus.PASS, True),
                GateEvaluation("agreement_ok", "agreement", 98.5, 95.0, 85.0, GateStatus.PASS, True),
            ],
            all_gates_pass=True,
            overall_status=GateStatus.PASS,
            metrics_snapshot={"error_rate_pct": 0.02},
        )

        decision, reason, record = validation_engine.make_decision(
            validation_result=result,
            current_traffic_percent=1,
        )

        assert decision == "ESCALATE"
        assert "gates pass" in reason

    def test_decision_making_hold(self, validation_engine):
        """Decision engine should recommend hold"""
        result = ValidationRunResult(
            timestamp=datetime.utcnow().isoformat() + "Z",
            traffic_percent=1,
            gates_evaluated=[
                GateEvaluation("latency_ok", "latency", 750.0, 500.0, 1000.0, GateStatus.FAIL, False),
            ],
            all_gates_pass=False,
            overall_status=GateStatus.FAIL,
            metrics_snapshot={"error_rate_pct": 0.02},
        )

        decision, reason, record = validation_engine.make_decision(
            validation_result=result,
            current_traffic_percent=1,
        )

        assert decision == "HOLD"
        assert "latency_ok" in reason

    def test_decision_making_rollback(self, validation_engine):
        """Decision engine should recommend rollback"""
        result = ValidationRunResult(
            timestamp=datetime.utcnow().isoformat() + "Z",
            traffic_percent=10,
            gates_evaluated=[],
            all_gates_pass=False,
            overall_status=GateStatus.FAIL,
            metrics_snapshot={"error_rate_pct": 2.5},  # Critical
        )

        decision, reason, record = validation_engine.make_decision(
            validation_result=result,
            current_traffic_percent=10,
        )

        assert decision == "ROLLBACK"
        assert "Error rate" in reason and "1%" in reason

    def test_skill_feedback_recording(self, validation_engine):
        """Skill feedback should be recorded with audit events"""
        success, hash_val = validation_engine.record_skill_feedback(
            decision="ESCALATE",
            actual_outcome="success",
            confidence_before=0.85,
            confidence_after=0.92,
            feedback_notes="Metrics stable, no issues observed",
            skill_id="os.canary_router",
        )

        assert success
        assert len(hash_val) == 64  # SHA256


class TestCanaryFullJourney:
    """Test complete 4-week canary progression"""

    def test_week_3_to_week_4_75_progression(self, controller, validation_engine):
        """
        Full journey: Week 3 (1%) → Week 3.5 (10%) → Week 4 (50%) → Week 4.75 (100%)

        This is the happy path where all gates pass at each stage.
        """
        # Week 3: Start at 1%
        controller.start_canary(initial_traffic=1)
        assert controller.state.current_traffic_percent == 1

        # Record healthy metrics at 1%
        metrics_1pct = TrafficMetrics(
            timestamp=datetime.utcnow().isoformat() + "Z",
            traffic_percent=1,
            p99_latency_ms=150.0, error_rate_pct=0.02, agreement_rate_pct=98.5,
            total_requests=10000, error_count=2, agreement_count=9850,
        )
        controller.record_metrics(metrics_1pct)

        # Validate and escalate
        decision, reason, _ = controller.evaluate_escalation()
        assert decision == EscalationDecision.ESCALATE
        success, _, new_traffic = controller.escalate()
        assert success and new_traffic == 10

        # Week 3.5: Validate at 10%
        metrics_10pct = TrafficMetrics(
            timestamp=datetime.utcnow().isoformat() + "Z",
            traffic_percent=10,
            p99_latency_ms=250.0, error_rate_pct=0.03, agreement_rate_pct=97.8,
            total_requests=100000, error_count=30, agreement_count=97800,
        )
        controller.record_metrics(metrics_10pct)

        decision, _, _ = controller.evaluate_escalation()
        assert decision == EscalationDecision.ESCALATE
        success, _, new_traffic = controller.escalate()
        assert success and new_traffic == 50

        # Week 4: Validate at 50%
        metrics_50pct = TrafficMetrics(
            timestamp=datetime.utcnow().isoformat() + "Z",
            traffic_percent=50,
            p99_latency_ms=200.0, error_rate_pct=0.01, agreement_rate_pct=99.0,
            total_requests=500000, error_count=50, agreement_count=495000,
        )
        controller.record_metrics(metrics_50pct)

        decision, _, _ = controller.evaluate_escalation()
        assert decision == EscalationDecision.ESCALATE
        success, _, new_traffic = controller.escalate()
        assert success and new_traffic == 100

        # Week 4.75: At 100% — Phase 2b activated
        assert controller.state.phase_2b_activated
        assert controller.state.current_traffic_percent == 100

        # Verify complete decision history
        assert len(controller.state.decision_history) == 4  # start + 3 escalations

    def test_week_4_hold_scenario(self, controller):
        """
        Hold scenario: Week 4 metrics not ready, hold at 50%
        """
        controller.start_canary(initial_traffic=1)
        controller.state.current_traffic_percent = 50  # Skip to week 4

        # No metrics recorded yet
        decision, _, _ = controller.evaluate_escalation()
        assert decision == EscalationDecision.HOLD
        assert controller.state.current_traffic_percent == 50  # Unchanged

    def test_emergency_rollback_flow(self, controller):
        """
        Emergency: Error spike at 50%, rollback to 10%
        """
        controller.start_canary(initial_traffic=1)
        controller.state.current_traffic_percent = 50

        # Record critical metrics
        metrics = TrafficMetrics(
            timestamp=datetime.utcnow().isoformat() + "Z",
            traffic_percent=50,
            p99_latency_ms=2000.0, error_rate_pct=3.5, agreement_rate_pct=45.0,
            total_requests=500000, error_count=17500, agreement_count=225000,
        )
        controller.record_metrics(metrics)

        # Evaluate: should detect rollback condition
        decision, reason, _ = controller.evaluate_escalation()
        assert decision == EscalationDecision.ROLLBACK

        # Execute rollback
        success, rb_reason, new_traffic = controller.rollback(
            reason="Critical error rate spike",
            target_traffic=10
        )
        assert success
        assert new_traffic == 10
        assert controller.state.status == CanaryHealth.DEGRADED


class TestCanaryMetricsAndReporting:
    """Test metrics collection and reporting"""

    def test_metrics_summary_generation(self, controller):
        """Generate metrics summary across all traffic levels"""
        controller.start_canary(initial_traffic=1)

        # Record metrics at 1%
        for i in range(3):
            metrics = TrafficMetrics(
                timestamp=datetime.utcnow().isoformat() + "Z",
                traffic_percent=1,
                p99_latency_ms=150.0 + i*10, error_rate_pct=0.02, agreement_rate_pct=98.0 + i,
                total_requests=10000, error_count=2, agreement_count=9800+i*100,
            )
            controller.record_metrics(metrics)

        summary = controller.get_metrics_summary()

        assert summary["current_traffic_percent"] == 1
        assert summary["status"] == "healthy"
        assert 1 in summary["metrics_by_traffic_level"]
        assert summary["metrics_by_traffic_level"][1]["count"] == 3

    def test_decision_history_tracking(self, controller):
        """Decision history should be tracked accurately"""
        controller.start_canary(initial_traffic=1)
        controller.hold(reason="Test hold")

        history = controller.state.decision_history

        assert len(history) == 2
        assert history[0]["decision"] == "escalate"  # start
        assert history[1]["decision"] == "hold"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
