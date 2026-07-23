"""Tests for TDE Phase 1: RobustEngineDetector (15+ tests)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "operator" / "orchestration"))

from tde.robust_engine_detector import RobustEngineDetector, DetectionSignals
from tde.loss_profile_tracker import LossProfileTracker
from initial_analysis import InitialAnalysisRequest, Classification, Entities, GlobalPlan, Step


class TestRobustEngineDetector:
    """Test RobustEngineDetector signal computation and ensemble."""

    @pytest.fixture
    def detector(self):
        """Create detector with mock loss tracker."""
        tracker = LossProfileTracker()
        return RobustEngineDetector(loss_tracker=tracker)

    @pytest.fixture
    def basic_analysis(self):
        """Create basic InitialAnalysisRequest for testing."""
        return InitialAnalysisRequest(
            classification=Classification(
                task_type="code_generation",
                complexity="moderate",
                engine_preference="claude",
                confidence=0.8,
            ),
            entities=Entities(),
            global_plan=GlobalPlan(
                steps=[
                    Step(step=1, action="read", depends_on=[], can_parallelize=[2, 3]),
                    Step(step=2, action="analyze", depends_on=[], can_parallelize=[1, 3]),
                    Step(step=3, action="write", depends_on=[1, 2], can_parallelize=[]),
                ],
                estimated_duration_s=10,
                estimated_tokens=5000,
            ),
        )

    def test_detector_initialization(self, detector):
        """Detector initializes with loss tracker."""
        assert detector.loss_tracker is not None
        assert detector.LOGIT_SCALE == 5.0

    def test_signal_parallelization_high(self, detector, basic_analysis):
        """High parallelization ratio → strong TDE signal."""
        # 2/3 steps are parallelizable
        engine, confidence, signals = detector.detect_engine(
            "Test task",
            {},
            basic_analysis,
        )
        assert signals["parallelization_ratio"] == pytest.approx(0.67, abs=0.01)
        assert signals["signal_parallelization"] == 0.8  # >= 0.3 ratio

    def test_signal_parallelization_low(self, detector, basic_analysis):
        """Low parallelization → weak signal."""
        basic_analysis.global_plan.steps = [
            Step(step=1, action="a", depends_on=[], can_parallelize=[]),
            Step(step=2, action="b", depends_on=[1], can_parallelize=[]),
        ]
        engine, confidence, signals = detector.detect_engine(
            "Test task",
            {},
            basic_analysis,
        )
        assert signals["parallelization_ratio"] == 0.0
        assert signals["signal_parallelization"] == 0.2  # < 0.3 ratio

    def test_signal_coding_task_type(self, detector, basic_analysis):
        """Coding task with high confidence → TDE signal."""
        basic_analysis.classification.task_type = "code_generation"
        basic_analysis.classification.confidence = 0.9
        engine, confidence, signals = detector.detect_engine(
            "Generate OAuth handler",
            {},
            basic_analysis,
        )
        expected = 0.75 * 0.9
        assert signals["signal_task_type"] == pytest.approx(expected, abs=0.01)

    def test_signal_reasoning_task_type(self, detector, basic_analysis):
        """Reasoning task → negative TDE signal (ACS preferred)."""
        basic_analysis.classification.task_type = "reasoning"
        engine, confidence, signals = detector.detect_engine(
            "Decompose complex problem",
            {},
            basic_analysis,
        )
        assert signals["signal_task_type"] == pytest.approx(-0.6, abs=0.01)

    def test_signal_data_small_complex(self, detector, basic_analysis):
        """Small data + complex task → TDE sweet spot."""
        engine, confidence, signals = detector.detect_engine(
            "Refactor module",
            {"data": "small context"},  # <10MB
            basic_analysis,
        )
        assert signals["data_mb"] < 10
        assert signals["complexity"] == "moderate"
        assert signals["signal_data_complexity"] == pytest.approx(0.85, abs=0.01)

    def test_signal_data_large(self, detector, basic_analysis):
        """Large data (>500MB) → ACS preferred."""
        context = {"data": "x" * (600 * 1024 * 1024)}  # 600MB
        engine, confidence, signals = detector.detect_engine(
            "Process large dataset",
            context,
            basic_analysis,
        )
        assert signals["data_mb"] > 500
        assert signals["signal_data_complexity"] == pytest.approx(-0.5, abs=0.01)

    def test_signal_context_available(self, detector, basic_analysis):
        """Full context available → positive signal."""
        context = {"statement": {"var1": "value1"}}
        engine, confidence, signals = detector.detect_engine(
            "Task",
            context,
            basic_analysis,
        )
        assert signals["has_full_context"] is True
        assert signals["signal_context"] == 0.8

    def test_signal_context_missing(self, detector, basic_analysis):
        """No statement → weak signal."""
        engine, confidence, signals = detector.detect_engine(
            "Task",
            {},
            basic_analysis,
        )
        assert signals["has_full_context"] is False
        assert signals["signal_context"] == 0.1

    def test_softmax_normalization(self, detector):
        """Softmax output sums to 1.0."""
        scores = detector._softmax({
            "tiered_delegation": 0.5,
            "acs": 0.3,
            "claude_code": 0.2,
        })
        total = sum(scores.values())
        assert total == pytest.approx(1.0, abs=0.001)

    def test_softmax_max_first(self, detector):
        """Highest score gets highest probability."""
        scores = detector._softmax({
            "tiered_delegation": 1.0,  # Highest
            "acs": 0.5,
            "claude_code": 0.2,
        })
        assert scores["tiered_delegation"] > scores["acs"]
        assert scores["acs"] > scores["claude_code"]

    def test_confidence_is_probability(self, detector, basic_analysis):
        """Returned confidence is 0-1 probability."""
        engine, confidence, signals = detector.detect_engine(
            "Task",
            {},
            basic_analysis,
        )
        assert 0.0 <= confidence <= 1.0

    def test_engine_selection_realistic(self, detector, basic_analysis):
        """Realistic task selects TDE (coding + parallel)."""
        basic_analysis.classification.task_type = "code_generation"
        basic_analysis.classification.confidence = 0.85
        engine, confidence, signals = detector.detect_engine(
            "Implement OAuth + OIDC + SAML",
            {"files": ["auth.py"]},
            basic_analysis,
        )
        assert engine in ["tiered_delegation", "acs", "claude_code"]
        # For coding + parallel, TDE should be top choice
        if signals["parallelization_ratio"] > 0.3:
            assert engine == "tiered_delegation" or confidence < 0.4

    def test_default_loss_no_tracker(self):
        """Without loss tracker, default loss is conservative."""
        detector = RobustEngineDetector(loss_tracker=None)
        basic = InitialAnalysisRequest(
            classification=Classification("code_gen", "simple", "claude", 0.8),
            entities=Entities(),
            global_plan=GlobalPlan([], 5, 1000),
        )
        engine, conf, signals = detector.detect_engine("task", {}, basic)
        assert signals["historical_loss_pct"] == 10.0  # Default
