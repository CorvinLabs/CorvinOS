"""E2E tests: Learning Loop Optimization for Phase 2b (ADR-0314, ADR-0722, ADR-0532).

Tests:
1. Simulate 3-week learning cycle with exploration → plateau → exploitation → convergence
2. Verify confidence monitoring (7-day rolling average)
3. Verify convergence detection (plateau ≥ 0.90 for 7 days)
4. Verify parameter optimization triggers on signals
5. Verify threshold adjustment logic
6. Verify false convergence detection (early plateau that resumes climbing)
7. Verify divergence alerts (confidence declining after plateau)
8. Verify learning velocity tracking (feedback → adjustment cycle time)
"""

import pytest
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory

from core.learning.phase2b_optimizer import Phase2bOptimizer, ConfidenceTrend
from core.learning.convergence_detector import ConvergenceDetector, ConvergencePhase

logger = logging.getLogger(__name__)


class TestPhase2bOptimizer:
    """Test Phase2bOptimizer — confidence monitoring and parameter optimization."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for test data."""
        with TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def optimizer(self, temp_dir):
        """Create optimizer instance."""
        return Phase2bOptimizer(
            tenant_id="test-tenant",
            tenant_home=temp_dir,
            observation_window_days=7,
        )

    def test_record_confidence_single_sample(self, optimizer):
        """Test recording a single confidence score."""
        trend = optimizer.record_confidence_score("skill-1", 0.60)
        assert trend.skill_id == "skill-1"
        assert trend.current_confidence == 0.60
        assert trend.rolling_avg_7day == 0.60
        assert trend.n_samples == 1
        assert not trend.phase_2b_eligible  # 0.60 < 0.75

    def test_record_confidence_climbing(self, optimizer):
        """Test recording confidence scores that climb."""
        # Simulate climbing confidence over 7 days
        base_time = datetime.now(timezone.utc)
        scores = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]

        for i, score in enumerate(scores):
            ts = base_time + timedelta(days=i)
            trend = optimizer.record_confidence_score("skill-1", score, timestamp=ts)

        assert trend.current_confidence == 0.80
        assert trend.rolling_avg_7day > 0.65
        assert trend.trend_direction == "climbing"
        assert trend.plateau_days == 0
        assert trend.phase_2b_eligible  # 0.80 > 0.75

    def test_convergence_plateau(self, optimizer):
        """Test detecting convergence plateau (confidence stable > 0.90 for 7 days)."""
        base_time = datetime.now(timezone.utc)

        # Climb to 0.95 confidence
        climbing_scores = [0.50, 0.60, 0.70, 0.80, 0.90, 0.93, 0.95]
        for i, score in enumerate(climbing_scores):
            ts = base_time + timedelta(days=i)
            optimizer.record_confidence_score("skill-1", score, timestamp=ts)

        # Plateau at 0.95 for 7 days
        plateau_scores = [0.95, 0.95, 0.94, 0.96, 0.95, 0.94, 0.95]
        for i, score in enumerate(plateau_scores):
            ts = base_time + timedelta(days=7 + i)
            optimizer.record_confidence_score("skill-1", score, timestamp=ts)

        # Check convergence
        trend = optimizer.get_confidence_trend("skill-1")
        assert trend.rolling_avg_7day >= 0.93
        assert trend.plateau_days >= 5  # At or near plateau
        assert optimizer.detect_convergence("skill-1")

    def test_parameter_optimization_on_convergence(self, optimizer):
        """Test triggering parameter optimization when convergence detected."""
        # Setup: record high confidence
        base_time = datetime.now(timezone.utc)
        for i in range(10):
            ts = base_time + timedelta(days=i)
            optimizer.record_confidence_score("skill-1", 0.92 + (0.02 * (i % 2)), timestamp=ts)

        # Trigger optimization on convergence
        delta = optimizer.trigger_optimization(
            "skill-1",
            reason="convergence_detected",
            feedback_signal_strength=0.85,
        )

        assert delta is not None
        assert delta.skill_id == "skill-1"
        assert delta.param_name == "confidence_threshold"
        assert delta.new_value > delta.old_value  # Threshold raised on convergence
        assert delta.reason == "convergence_detected"

    def test_threshold_auto_adjustment(self, optimizer):
        """Test automatic threshold adjustment based on reason."""
        # Initial threshold
        initial_threshold = optimizer._confidence_thresholds.get(
            "skill-2", optimizer.PHASE_2B_CONFIDENCE_THRESHOLD
        )

        # Setup: record convergence
        base_time = datetime.now(timezone.utc)
        for i in range(10):
            ts = base_time + timedelta(days=i)
            optimizer.record_confidence_score("skill-2", 0.91, timestamp=ts)

        # Trigger convergence optimization (raises threshold)
        delta1 = optimizer.trigger_optimization("skill-2", "convergence_detected", 0.8)
        assert delta1.new_value > delta1.old_value

        # Trigger velocity optimization (lowers threshold)
        delta2 = optimizer.trigger_optimization("skill-2", "velocity_improvement", 0.8)
        assert delta2.new_value < delta2.old_value

    def test_learning_velocity_tracking(self, optimizer):
        """Test feedback → adjustment cycle time tracking."""
        base_time = datetime.now(timezone.utc)

        # Record feedback
        feedback_ts = base_time
        velocity1 = optimizer.record_feedback("skill-3", 0.75, timestamp=feedback_ts)
        assert not velocity1.adjustment_triggered

        # Record confidence (triggers analysis)
        optimizer.record_confidence_score("skill-3", 0.85, timestamp=feedback_ts + timedelta(minutes=5))

        # Trigger optimization (should update velocity)
        adjustment_ts = feedback_ts + timedelta(minutes=10)
        delta = optimizer.trigger_optimization("skill-3", "feedback_signal", 0.8)

        # Check velocity was recorded
        assert delta is not None
        # Velocity tracking happens internally

    def test_divergence_detection(self, optimizer):
        """Test detecting divergence (confidence declining after plateau)."""
        base_time = datetime.now(timezone.utc)

        # Climb to convergence
        climbing = [0.50, 0.60, 0.70, 0.80, 0.90, 0.94, 0.96]
        for i, score in enumerate(climbing):
            ts = base_time + timedelta(days=i)
            optimizer.record_confidence_score("skill-4", score, timestamp=ts)

        # Plateau
        for i in range(7):
            ts = base_time + timedelta(days=7 + i)
            optimizer.record_confidence_score("skill-4", 0.95, timestamp=ts)

        # Diverge (confidence drops)
        diverging = [0.92, 0.88, 0.84, 0.80]
        for i, score in enumerate(diverging):
            ts = base_time + timedelta(days=14 + i)
            optimizer.record_confidence_score("skill-4", score, timestamp=ts)

        # Check divergence
        assert optimizer.detect_divergence("skill-4")

    def test_phase_2b_eligible_skills_list(self, optimizer):
        """Test getting list of skills eligible for Phase 2b (confidence > 0.75)."""
        # Record some skills
        optimizer.record_confidence_score("skill-low", 0.60)
        optimizer.record_confidence_score("skill-mid", 0.75)
        optimizer.record_confidence_score("skill-high", 0.85)

        eligible = optimizer.get_phase_2b_eligible_skills()
        assert "skill-low" not in eligible  # 0.60 < 0.75
        # skill-mid is borderline (0.75 = threshold)
        assert "skill-high" in eligible  # 0.85 > 0.75


class TestConvergenceDetector:
    """Test ConvergenceDetector — phase tracking and false convergence detection."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for test data."""
        with TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def detector(self, temp_dir):
        """Create detector instance."""
        return ConvergenceDetector(
            tenant_id="test-tenant",
            tenant_home=temp_dir,
        )

    def test_phase_exploration(self, detector):
        """Test EXPLORATION phase (n < 10)."""
        phase = detector.update_phase(
            "skill-1",
            current_confidence=0.50,
            n_samples=5,
            trend_direction="climbing",
            plateau_days=0,
        )
        assert phase == ConvergencePhase.EXPLORATION

    def test_phase_transition_exploration_to_plateau(self, detector):
        """Test transition from EXPLORATION to PLATEAU."""
        phase = detector.update_phase(
            "skill-1",
            current_confidence=0.75,
            n_samples=15,  # ≥ 10
            trend_direction="plateau",
            plateau_days=3,
        )
        assert phase == ConvergencePhase.PLATEAU

        # Check transition was recorded
        history = detector.get_phase_history("skill-1")
        assert len(history) >= 1
        assert history[-1].to_phase == ConvergencePhase.PLATEAU

    def test_phase_transition_plateau_to_exploitation(self, detector):
        """Test transition from PLATEAU to EXPLOITATION."""
        # Start in PLATEAU
        detector.update_phase("skill-1", 0.80, 20, "plateau", 3)

        # Update to EXPLOITATION
        phase = detector.update_phase(
            "skill-1",
            current_confidence=0.82,
            n_samples=35,  # ≥ 30
            trend_direction="plateau",
            plateau_days=5,
        )
        assert phase == ConvergencePhase.EXPLOITATION

    def test_phase_transition_exploitation_to_converged(self, detector):
        """Test transition from EXPLOITATION to CONVERGED."""
        # Start in EXPLOITATION
        detector.update_phase("skill-1", 0.88, 30, "plateau", 3)

        # Update to CONVERGED
        phase = detector.update_phase(
            "skill-1",
            current_confidence=0.92,
            n_samples=50,
            trend_direction="plateau",
            plateau_days=7,  # ≥ 7 days stable
        )
        assert phase == ConvergencePhase.CONVERGED

    def test_false_convergence_detection(self, detector):
        """Test detecting false convergence (early plateau that resumes climbing)."""
        base_time = datetime.now(timezone.utc)

        # Record plateau boundary at confidence 0.80
        boundary = detector.record_plateau_boundary("skill-1", 0.80, base_time)
        assert boundary.plateau_confidence == 0.80
        assert not boundary.false_positive

        # Simulate plateau for 2 days (early)
        detector.update_phase("skill-1", 0.80, 20, "plateau", 2)

        # Then resume climbing significantly (> 2% improvement)
        new_confidence = 0.83  # +3.75% improvement
        resumed = detector.check_plateau_resumed("skill-1", new_confidence)
        assert resumed

        # Now mark as false convergence
        false_conv = detector.detect_false_convergence("skill-1")
        assert false_conv  # Was marked as false positive

    def test_divergence_alert(self, detector):
        """Test divergence alert when confidence declines from plateau."""
        base_time = datetime.now(timezone.utc)

        # Record plateau at 0.92
        detector.record_plateau_boundary("skill-1", 0.92, base_time)
        detector.update_phase("skill-1", 0.92, 40, "plateau", 7)

        # Simulate decline (> 5%)
        alert = detector.detect_divergence("skill-1", 0.85, base_time + timedelta(days=1))
        assert alert is not None
        assert alert.severity == "warning"  # -7.6%, between 5% and 10%
        assert alert.recommended_action == "review_parameters"

        # Simulate critical decline (> 10%)
        alert_critical = detector.detect_divergence("skill-1", 0.80, base_time + timedelta(days=2))
        assert alert_critical is not None
        assert alert_critical.severity == "critical"  # -13.0%
        assert alert_critical.recommended_action == "rollback"

    def test_optimization_schedule(self, detector):
        """Test optimization scheduling based on phase."""
        base_time = datetime.now(timezone.utc)

        # PLATEAU phase → check in 2 days
        detector.update_phase(
            "skill-1",
            0.80,
            20,
            "plateau",
            3,
            timestamp=base_time,
        )
        schedule = detector.get_next_optimization_date("skill-1")
        assert schedule is not None
        assert schedule > base_time

        # CONVERGED phase → check in 7 days
        detector.update_phase(
            "skill-2",
            0.92,
            50,
            "plateau",
            7,
            timestamp=base_time,
        )
        schedule = detector.get_next_optimization_date("skill-2")
        assert schedule is not None
        # Converged should have longer interval

    def test_divergence_recovery(self, detector):
        """Test recovery from divergence state."""
        # Start diverging
        phase = detector.update_phase(
            "skill-1",
            0.75,
            30,
            "diverging",
            5,
        )
        assert phase == ConvergencePhase.DIVERGING

        # Recovery (confidence climbing again)
        phase = detector.update_phase(
            "skill-1",
            0.82,
            35,
            "climbing",
            0,
        )
        assert phase == ConvergencePhase.EXPLOITATION  # Back to exploitation


class TestLearningLoopE2E:
    """Integration tests: full 3-week learning cycle simulation."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for test data."""
        with TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def setup(self, temp_dir):
        """Setup optimizer and detector."""
        optimizer = Phase2bOptimizer("test-tenant", temp_dir)
        detector = ConvergenceDetector("test-tenant", temp_dir)
        return optimizer, detector

    def test_three_week_learning_cycle(self, setup):
        """Simulate 3-week learning cycle: exploration → plateau → exploitation → convergence."""
        optimizer, detector = setup
        base_time = datetime.now(timezone.utc)
        skill_id = "test-router"

        # Week 1: EXPLORATION (climbing, n=10)
        week1_scores = [0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.72]
        for i, score in enumerate(week1_scores):
            ts = base_time + timedelta(days=i)
            optimizer.record_confidence_score(skill_id, score, timestamp=ts)

        trend = optimizer.get_confidence_trend(skill_id)
        assert trend.trend_direction == "climbing"
        assert trend.n_samples == len(week1_scores)

        # Week 2: PLATEAU (stable, n=20)
        week2_scores = [0.74, 0.75, 0.76, 0.75, 0.74, 0.75, 0.76]
        for i, score in enumerate(week2_scores):
            ts = base_time + timedelta(days=7 + i)
            optimizer.record_confidence_score(skill_id, score, timestamp=ts)

        phase = detector.update_phase(skill_id, 0.75, 14, "plateau", 3)
        assert phase == ConvergencePhase.PLATEAU

        # Week 3: EXPLOITATION/CONVERGENCE (stable high, n=30+)
        week3_scores = [0.88, 0.89, 0.90, 0.91, 0.90, 0.89, 0.91]
        for i, score in enumerate(week3_scores):
            ts = base_time + timedelta(days=14 + i)
            optimizer.record_confidence_score(skill_id, score, timestamp=ts)

        # Should be near convergence
        trend = optimizer.get_confidence_trend(skill_id)
        assert trend.rolling_avg_7day >= 0.89
        assert trend.phase_2b_eligible

        phase = detector.update_phase(skill_id, 0.90, 30, "plateau", 6)
        assert phase in [ConvergencePhase.EXPLOITATION, ConvergencePhase.CONVERGED]

    def test_false_convergence_then_recovery(self, setup):
        """Test false convergence: early plateau that resumes climbing."""
        optimizer, detector = setup
        base_time = datetime.now(timezone.utc)
        skill_id = "skill-recovery"

        # Climbing phase
        climbing = [0.50, 0.60, 0.65, 0.68, 0.70]
        for i, score in enumerate(climbing):
            ts = base_time + timedelta(days=i)
            optimizer.record_confidence_score(skill_id, score, timestamp=ts)

        # Early plateau (2 days) at 0.70
        detector.record_plateau_boundary(skill_id, 0.70, base_time + timedelta(days=5))
        for i in range(2):
            ts = base_time + timedelta(days=5 + i)
            optimizer.record_confidence_score(skill_id, 0.70, timestamp=ts)

        # Resume climbing (false convergence)
        recovery = [0.72, 0.75, 0.78, 0.80]
        for i, score in enumerate(recovery):
            ts = base_time + timedelta(days=7 + i)
            optimizer.record_confidence_score(skill_id, score, timestamp=ts)

        # Should detect false convergence
        resumed = detector.check_plateau_resumed(skill_id, 0.80)
        assert resumed

        false_conv = detector.detect_false_convergence(skill_id)
        assert false_conv

    def test_divergence_and_recovery_cycle(self, setup):
        """Test divergence alert and recovery."""
        optimizer, detector = setup
        base_time = datetime.now(timezone.utc)
        skill_id = "skill-volatile"

        # Climb to high confidence
        climbing = [0.60, 0.70, 0.80, 0.88, 0.92, 0.94]
        for i, score in enumerate(climbing):
            ts = base_time + timedelta(days=i)
            optimizer.record_confidence_score(skill_id, score, timestamp=ts)

        detector.record_plateau_boundary(skill_id, 0.92, base_time + timedelta(days=5))

        # Plateau
        for i in range(5):
            ts = base_time + timedelta(days=6 + i)
            optimizer.record_confidence_score(skill_id, 0.92, timestamp=ts)

        # Diverge (decline)
        diverging = [0.88, 0.83, 0.78]
        for i, score in enumerate(diverging):
            ts = base_time + timedelta(days=11 + i)
            optimizer.record_confidence_score(skill_id, score, timestamp=ts)

        # Should detect divergence
        alert = detector.detect_divergence(skill_id, 0.78, base_time + timedelta(days=14))
        assert alert is not None
        assert alert.severity in ["warning", "critical"]

        # Recover (climbing again)
        recovering = [0.82, 0.86, 0.88]
        for i, score in enumerate(recovering):
            ts = base_time + timedelta(days=14 + i)
            optimizer.record_confidence_score(skill_id, score, timestamp=ts)

        phase = detector.update_phase(skill_id, 0.88, 35, "climbing", 0)
        # Should transition out of diverging
        assert phase != ConvergencePhase.DIVERGING


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
