"""Phase 3 Adversarial Tests: Comprehensive attack vectors (all phases)."""

import pytest
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
import sys
import time

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "skills" / "os_skills"))

from definition_of_done_verifier.skill import DoD_VerifierSkill
from definition_of_done_verifier.checks.reachability import ReachabilityCheck
from definition_of_done_verifier.weight_optimizer import DoD_WeightOptimizer
from definition_of_done_verifier.hardening import CircuitBreaker, ConcurrencyHardeningMonitor


# ============================================================================
# PHASE 1 ADVERSARIAL TESTS (Reachability, Audit, Scoring)
# ============================================================================

class TestAdversarialPhase1:
    """Attack vectors on Phase 1 (5 checks + scoring)."""

    @pytest.mark.adversarial
    def test_halt_attack_grep_hangs(self):
        """Attack: Attacker makes grep hang → timeout should trigger."""
        check = ReachabilityCheck()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("grep", 5)
            result = check.run("symbol", Path("/repo"), timeout_s=5)

            # Must fail (fail-closed)
            assert result.passed == False
            assert "timeout" in result.evidence.lower()

    @pytest.mark.adversarial
    def test_audit_bypass_no_events_logged(self):
        """Attack: Audit event not emitted → skill should fail."""
        from definition_of_done_verifier.checks.audit_trail import AuditTrailCheck

        check = AuditTrailCheck()
        # Mock: no matching events in audit trail
        with patch("builtins.open"):
            with patch("pathlib.Path.exists", return_value=True):
                result = check.run("task_xyz", Path("/audit.jsonl"))

                # Must fail (no events = fail-closed)
                assert result.passed == False

    @pytest.mark.adversarial
    def test_false_positive_all_checks_fail(self):
        """Attack: Hallucinated task tries to pass → all checks fail → score < 0.80."""
        from definition_of_done_verifier.scoring import ScoringEngine

        engine = ScoringEngine()
        checks = {
            "reachability": False,
            "audit_trail": False,
            "test_evidence": False,
            "docs_sync": False,
            "reproducibility": False,
        }
        score = engine.compute_score(checks)

        # Score must be 0.0 (all checks failed)
        assert score == 0.0
        assert not engine.is_passed(score)

    @pytest.mark.adversarial
    def test_privilege_escalation_no_shell_injection(self):
        """Attack: Attacker injects shell command → shell=False prevents it."""
        check = ReachabilityCheck()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""

            # Pass malicious input
            check.run("'; rm -rf /; echo '", Path("/repo"))

            # Verify shell=False was used (no shell=True in call)
            call_args = mock_run.call_args
            assert call_args[1].get("shell") == False or "shell" not in call_args[1]


# ============================================================================
# PHASE 2 ADVERSARIAL TESTS (Learning, Weights, Persistence)
# ============================================================================

class TestAdversarialPhase2:
    """Attack vectors on Phase 2 (learning loop, optimizer)."""

    @pytest.mark.adversarial
    def test_weight_divergence_detection(self):
        """Attack: Operator gives wrong feedback repeatedly → convergence detected + mitigated."""
        opt = DoD_WeightOptimizer()

        # Simulate 10 feedback events saying "too harsh" (all same direction)
        for _ in range(10):
            opt.observe_feedback(
                "api_endpoint",
                delta=0.20,  # Always harsh feedback
                affected_checks=["audit_trail"]
            )

        # Weights should be adjusted, but confidence should cap
        conf = opt.confidence["api_endpoint"]["w_audit"]
        assert conf <= 1.0  # Confidence capped at 1.0
        assert opt.weights["api_endpoint"]["w_audit"] < 1.0  # Weight bounded

    @pytest.mark.adversarial
    def test_learning_poisoning_single_malicious_feedback(self):
        """Attack: One malicious feedback tries to poison weights → single feedback has limited impact."""
        opt = DoD_WeightOptimizer()
        old_w = opt.weights["api_endpoint"]["w_audit"]

        # Single extreme feedback
        opt.observe_feedback("api_endpoint", delta=0.99, affected_checks=["audit_trail"])

        new_w = opt.weights["api_endpoint"]["w_audit"]
        delta_w = abs(new_w - old_w)

        # Single feedback should cause small change (not massive)
        assert delta_w < 0.1  # Bounded adjustment

    @pytest.mark.adversarial
    def test_cascading_divergence_prevented(self):
        """Attack: Cascading feedback loop → confidence gates prevent runaway."""
        opt = DoD_WeightOptimizer()

        for _ in range(20):
            opt.observe_feedback(
                "api_endpoint",
                delta=0.15,
                affected_checks=["audit_trail", "reachability", "test_evidence"]
            )

        # Even after 20 feedbacks, weights should be bounded [0, 1]
        for w_name, w_val in opt.weights["api_endpoint"].items():
            assert 0.0 <= w_val <= 1.0

        # Confidence should be capped
        for conf_val in opt.confidence["api_endpoint"].values():
            assert conf_val <= 1.0


# ============================================================================
# PHASE 3 ADVERSARIAL TESTS (Timeouts, Concurrency, Monitoring)
# ============================================================================

class TestAdversarialPhase3:
    """Attack vectors on Phase 3 (hardening, production)."""

    @pytest.mark.adversarial
    def test_timeout_abuse_circuit_breaker_opens(self):
        """Attack: Attacker stalls checks → circuit breaker should open."""
        breaker = CircuitBreaker(threshold_ms=100)
        breaker.start()

        # Simulate work that takes too long
        time.sleep(0.15)  # 150ms > 100ms threshold

        # Circuit should be open
        assert not breaker.check()
        assert breaker.is_open == True

    @pytest.mark.adversarial
    def test_concurrency_race_condition_monitor(self):
        """Attack: Too many concurrent tasks → monitor should reject."""
        monitor = ConcurrencyHardeningMonitor()

        # Fill all slots
        for _ in range(5):
            assert monitor.acquire() == True

        # 6th should be rejected (bounded concurrency)
        assert monitor.acquire() == False

    @pytest.mark.adversarial
    def test_monitoring_bypass_metrics_tracked(self):
        """Attack: Attacker tries to bypass monitoring → all metrics recorded."""
        from definition_of_done_verifier.hardening import MonitoringMetrics

        metrics = MonitoringMetrics(
            total_checks=100,
            failed_checks=5,
            timeout_checks=2,
            error_checks=1,
        )

        error_rate = metrics.compute_error_rate()
        # (5 + 2 + 1) / 100 = 0.08 = 8%
        assert error_rate == 0.08
        assert error_rate < 0.2  # Should be low

    @pytest.mark.adversarial
    def test_malformed_json_in_audit_trail_handled(self):
        """Attack: Corrupt audit.jsonl with malformed JSON → parser should skip gracefully."""
        from definition_of_done_verifier.checks.audit_trail import AuditTrailCheck

        check = AuditTrailCheck()
        # JSON with malformed lines mixed in
        audit_content = (
            '{"task_id": "t1", "event_type": "dod_verified"}\n'
            'THIS IS NOT JSON AT ALL\n'
            '{"task_id": "t1", "event_type": "feedback"}\n'
        )

        with patch("builtins.open", create=True) as mock_file:
            mock_file.return_value.__enter__.return_value.read = MagicMock(
                return_value=audit_content
            )
            with patch("pathlib.Path.exists", return_value=True):
                result = check.run("t1", Path("/audit.jsonl"))

                # Should still count valid events (2 found)
                assert result.event_count == 2

    @pytest.mark.adversarial
    def test_permission_denied_on_audit_file_fail_closed(self):
        """Attack: Attacker removes read permissions on audit.jsonl → check should fail."""
        from definition_of_done_verifier.checks.audit_trail import AuditTrailCheck

        check = AuditTrailCheck()
        with patch("builtins.open") as mock_file:
            mock_file.side_effect = PermissionError("Access denied")
            with patch("pathlib.Path.exists", return_value=True):
                result = check.run("t1", Path("/audit.jsonl"))

                # Must fail (fail-closed)
                assert result.passed == False
                assert "error" in result.evidence.lower()


# ============================================================================
# CROSS-PHASE INTEGRATION ATTACKS
# ============================================================================

class TestAdversarialCrossPhase:
    """Attack vectors that span multiple phases."""

    @pytest.mark.adversarial
    def test_phase1_phase2_feedback_poisoning(self):
        """Attack: Phase 1 score artificially low → Phase 2 feedback tries to compensate wrong."""
        from definition_of_done_verifier.api_handlers import DoD_FeedbackCollector

        collector = DoD_FeedbackCollector()

        # Simulate: Phase 1 gives score 0.3 (artificially low)
        # Operator corrects to 0.85 (delta = 0.55)
        result = collector.submit_feedback(
            task_id="t1",
            dod_score_automatic=0.30,
            dod_score_operator=0.85,
            reason="too_harsh",
            affected_checks=["audit_trail", "reachability"],
            task_type="api_endpoint",
        )

        # Optimizer should cap adjustments (not diverge)
        assert result["success"] == True
        # New weights should still be bounded
        for w_name, w_val in result["new_weights"].items():
            assert 0.0 <= w_val <= 1.0

    @pytest.mark.adversarial
    def test_phase2_phase3_weight_persistence_corruption(self):
        """Attack: Corrupted weights.json on disk → Phase 3 should handle gracefully."""
        from definition_of_done_verifier.weight_persistence import WeightPersistence
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            persist = WeightPersistence(storage_path=Path(tmpdir) / "weights.json")

            # Write corrupted JSON
            with open(persist.storage_path, "w") as f:
                f.write("{ INVALID JSON }")

            # Load should handle gracefully (fail-closed)
            loaded = persist.load_weights()
            # Should return empty dict (fallback), not crash
            assert isinstance(loaded, dict)


# ============================================================================
# PHASE 3 GATE: HARDENING VERIFICATION
# ============================================================================

class TestPhase3Gate:
    """Gate: Phase 3 hardening must be verified."""

    @pytest.mark.gate
    def test_circuit_breaker_exists(self):
        """Circuit breaker implemented for timeout protection."""
        breaker = CircuitBreaker()
        assert hasattr(breaker, 'check')
        assert hasattr(breaker, 'start')
        assert hasattr(breaker, 'stop')

    @pytest.mark.gate
    def test_concurrency_monitor_exists(self):
        """Concurrency monitor implemented for thread safety."""
        monitor = ConcurrencyHardeningMonitor()
        assert hasattr(monitor, 'acquire')
        assert hasattr(monitor, 'release')
        assert monitor.max_concurrent == 5

    @pytest.mark.gate
    def test_monitoring_metrics_exist(self):
        """Monitoring metrics implemented for observability."""
        from definition_of_done_verifier.hardening import MonitoringMetrics
        metrics = MonitoringMetrics()
        assert hasattr(metrics, 'compute_error_rate')


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
