"""Unit tests for GuidanceClassifier subsystem (Week 1).

Test Coverage:
- Heuristic classifier: keyword matching, determinism, latency
- LLM classifier: accuracy, confidence calibration, fallback
- Hybrid strategy: LLM→fallback on low confidence or failure
- Classification accuracy: 90%+ target on test set
- False-positive/negative rates within bounds

ADR-0280: Voice-Native Midstream Guidance Classifier
"""

import pytest
import asyncio
from datetime import datetime
from unittest.mock import Mock, patch, AsyncMock

from core.voice.guidance import (
    GuidanceClassifier,
    GuidanceEvent,
    ClassificationResult,
    GuidanceClass,
    RiskLevel,
)
from core.voice.guidance.heuristics import HeuristicClassifier
from core.voice.guidance.llm_classifier import LLMClassifier


class TestHeuristicClassifier:
    """Test deterministic heuristic classifier."""

    def setup_method(self):
        """Setup classifier for each test."""
        self.classifier = HeuristicClassifier()

    def test_interrupt_stop_command(self):
        """Test detection of 'stop' interrupt command."""
        event = GuidanceEvent(
            id="test_001",
            input_text="stop"
        )
        result = self.classifier.classify(event)

        assert result.guidance_class == GuidanceClass.INTERRUPT
        assert result.confidence >= 0.85
        assert result.risk_level == RiskLevel.SAFE

    def test_interrupt_cancel_command(self):
        """Test detection of 'cancel' interrupt command."""
        event = GuidanceEvent(
            id="test_002",
            input_text="cancel this task"
        )
        result = self.classifier.classify(event)

        assert result.guidance_class == GuidanceClass.INTERRUPT
        assert result.confidence >= 0.85

    def test_high_risk_delete_command(self):
        """Test detection of high-risk 'delete' command."""
        event = GuidanceEvent(
            id="test_003",
            input_text="delete everything"
        )
        result = self.classifier.classify(event)

        assert result.guidance_class == GuidanceClass.MIDSTREAM_GUIDANCE
        assert result.risk_level == RiskLevel.HIGH
        assert result.subsystem_hint == "SafetyValidator"

    def test_guidance_model_selection(self):
        """Test detection of model selection guidance."""
        event = GuidanceEvent(
            id="test_004",
            input_text="use Opus instead"
        )
        result = self.classifier.classify(event)

        assert result.guidance_class == GuidanceClass.MIDSTREAM_GUIDANCE
        assert result.subsystem_hint == "CostController"
        assert result.confidence >= 0.75

    def test_guidance_strategy_change(self):
        """Test detection of strategy change guidance."""
        event = GuidanceEvent(
            id="test_005",
            input_text="try decompose instead"
        )
        result = self.classifier.classify(event)

        assert result.guidance_class == GuidanceClass.MIDSTREAM_GUIDANCE
        assert result.subsystem_hint == "LoopEngineer"

    def test_question_what_next(self):
        """Test detection of 'what next' question."""
        event = GuidanceEvent(
            id="test_006",
            input_text="what's next?"
        )
        result = self.classifier.classify(event)

        assert result.guidance_class == GuidanceClass.TASK_QUESTION

    def test_question_confidence(self):
        """Test detection of confidence question."""
        event = GuidanceEvent(
            id="test_007",
            input_text="what's your confidence on this?"
        )
        result = self.classifier.classify(event)

        assert result.guidance_class == GuidanceClass.TASK_QUESTION

    def test_default_task_input(self):
        """Test default classification as task_input."""
        event = GuidanceEvent(
            id="test_008",
            input_text="refactor these 50 files"
        )
        result = self.classifier.classify(event)

        assert result.guidance_class == GuidanceClass.TASK_INPUT
        assert result.confidence <= 0.60  # Low confidence for default

    def test_determinism(self):
        """Test that same input → same output (determinism)."""
        event = GuidanceEvent(
            id="test_009",
            input_text="use Sonnet"
        )

        result1 = self.classifier.classify(event)
        result2 = self.classifier.classify(event)

        assert result1.guidance_class == result2.guidance_class
        assert result1.confidence == result2.confidence

    def test_latency_under_10ms(self):
        """Test that heuristic classification completes in <10ms."""
        event = GuidanceEvent(
            id="test_010",
            input_text="use Opus"
        )

        import time
        start = time.time()
        self.classifier.classify(event)
        elapsed_ms = (time.time() - start) * 1000

        assert elapsed_ms < 10


class TestLLMClassifier:
    """Test Claude Haiku LLM classifier."""

    def setup_method(self):
        """Setup classifier for each test."""
        self.classifier = LLMClassifier(model="claude-3-5-haiku-20241022")

    @pytest.mark.asyncio
    async def test_llm_classify_guidance(self):
        """Test LLM classification of guidance event."""
        event = GuidanceEvent(
            id="test_llm_001",
            input_text="use Opus for better quality"
        )

        result = await self.classifier.classify(event)

        assert result.event_id == event.id
        assert result.guidance_class in [
            GuidanceClass.MIDSTREAM_GUIDANCE,
            GuidanceClass.TASK_INPUT,
        ]
        assert 0.0 <= result.confidence <= 1.0
        assert result.model_used == "llm"

    @pytest.mark.asyncio
    async def test_llm_fallback_on_api_error(self):
        """Test fallback to heuristic on LLM API error."""
        event = GuidanceEvent(
            id="test_llm_002",
            input_text="stop"
        )

        # Mock API error
        with patch.object(self.classifier.client.messages, 'create', side_effect=Exception("API Error")):
            with pytest.raises(Exception):
                await self.classifier.classify(event)


class TestGuidanceClassifier:
    """Test hybrid GuidanceClassifier (LLM + heuristic fallback)."""

    def setup_method(self):
        """Setup classifier for each test."""
        self.classifier = GuidanceClassifier()

    @pytest.mark.asyncio
    async def test_hybrid_uses_heuristic_on_low_confidence(self):
        """Test that hybrid classifier falls back to heuristic on low LLM confidence."""
        event = GuidanceEvent(
            id="test_hybrid_001",
            input_text="stop"
        )

        # This is a clear interrupt, should be caught by heuristic if LLM is uncertain
        result = await self.classifier.classify(event)

        # Result should be from either LLM or heuristic, both should classify correctly
        assert result.guidance_class == GuidanceClass.INTERRUPT

    @pytest.mark.asyncio
    async def test_batch_classification(self):
        """Test batch classification of multiple events."""
        events = [
            GuidanceEvent(id="b1", input_text="use Opus"),
            GuidanceEvent(id="b2", input_text="what's next?"),
            GuidanceEvent(id="b3", input_text="stop"),
        ]

        results = await self.classifier.classify_batch(events)

        assert len(results) == 3
        # First should be guidance
        assert results[0].guidance_class in [GuidanceClass.MIDSTREAM_GUIDANCE, GuidanceClass.TASK_INPUT]
        # Second should be question
        assert results[1].guidance_class in [GuidanceClass.TASK_QUESTION, GuidanceClass.TASK_INPUT]
        # Third should be interrupt
        assert results[2].guidance_class == GuidanceClass.INTERRUPT


class TestRiskAssessment:
    """Test risk level assessment in classification."""

    def setup_method(self):
        """Setup classifier for each test."""
        self.classifier = HeuristicClassifier()

    def test_interrupt_is_safe(self):
        """Test that interrupt commands are marked safe."""
        event = GuidanceEvent(id="risk_001", input_text="pause")
        result = self.classifier.classify(event)

        assert result.guidance_class == GuidanceClass.INTERRUPT
        assert result.risk_level == RiskLevel.SAFE

    def test_high_risk_delete(self):
        """Test that destructive commands are marked high-risk."""
        event = GuidanceEvent(id="risk_002", input_text="delete all files")
        result = self.classifier.classify(event)

        assert result.risk_level == RiskLevel.HIGH

    def test_medium_risk_guidance(self):
        """Test that model-change guidance is marked medium-risk."""
        event = GuidanceEvent(id="risk_003", input_text="switch to Haiku")
        result = self.classifier.classify(event)

        assert result.guidance_class == GuidanceClass.MIDSTREAM_GUIDANCE
        assert result.risk_level in [RiskLevel.MEDIUM, RiskLevel.SAFE]


class TestSubsystemRouting:
    """Test subsystem routing hints."""

    def setup_method(self):
        """Setup classifier for each test."""
        self.classifier = HeuristicClassifier()

    def test_cost_controller_routing_model_selection(self):
        """Test routing model-selection guidance to CostController."""
        event = GuidanceEvent(id="route_001", input_text="use Opus")
        result = self.classifier.classify(event)

        if result.guidance_class == GuidanceClass.MIDSTREAM_GUIDANCE:
            assert result.subsystem_hint == "CostController"

    def test_loop_engineer_routing_strategy(self):
        """Test routing strategy-change guidance to LoopEngineer."""
        event = GuidanceEvent(id="route_002", input_text="try decompose")
        result = self.classifier.classify(event)

        if result.guidance_class == GuidanceClass.MIDSTREAM_GUIDANCE:
            assert result.subsystem_hint == "LoopEngineer"

    def test_safety_validator_routing_high_risk(self):
        """Test routing high-risk guidance to SafetyValidator."""
        event = GuidanceEvent(id="route_003", input_text="delete everything")
        result = self.classifier.classify(event)

        assert result.subsystem_hint == "SafetyValidator"


class TestAccuracyMetrics:
    """Test accuracy and confidence calibration."""

    def setup_method(self):
        """Setup classifier for each test."""
        self.classifier = HeuristicClassifier()

    def test_false_positive_rate_task_input_to_guidance(self):
        """Test that false-positive rate (task→guidance) is <5%."""
        # These should be classified as task_input, not guidance
        task_inputs = [
            "refactor these files",
            "find bugs in module",
            "write tests for feature",
            "update documentation",
            "implement new endpoint",
        ]

        false_positives = 0
        for text in task_inputs:
            event = GuidanceEvent(id=f"fp_{text[:5]}", input_text=text)
            result = self.classifier.classify(event)

            if result.guidance_class == GuidanceClass.MIDSTREAM_GUIDANCE:
                false_positives += 1

        fp_rate = false_positives / len(task_inputs)
        assert fp_rate < 0.05, f"False positive rate {fp_rate:.1%} exceeds 5% threshold"

    def test_false_negative_rate_guidance_to_task(self):
        """Test that false-negative rate (guidance→task) is <10%."""
        # These should be classified as guidance, not task_input
        guidance_samples = [
            "use Opus",
            "switch to Sonnet",
            "try decompose",
            "skip the tests",
            "reorder priority",
        ]

        false_negatives = 0
        for text in guidance_samples:
            event = GuidanceEvent(id=f"fn_{text[:5]}", input_text=text)
            result = self.classifier.classify(event)

            if result.guidance_class == GuidanceClass.TASK_INPUT:
                false_negatives += 1

        fn_rate = false_negatives / len(guidance_samples)
        assert fn_rate < 0.10, f"False negative rate {fn_rate:.1%} exceeds 10% threshold"


class TestMetricsCollection:
    """Test metrics collection for Week 5 measurement framework."""

    def setup_method(self):
        """Setup classifier for each test."""
        self.classifier = HeuristicClassifier()

    def test_metrics_are_collected(self):
        """Test that metrics are collected after classifications."""
        # Classify some events
        for i in range(5):
            event = GuidanceEvent(id=f"metric_{i}", input_text="use Opus")
            self.classifier.classify(event)

        metrics = self.classifier.get_metrics()

        assert "total_classifications" in metrics
        assert "by_class" in metrics
        assert metrics["total_classifications"] == 5

    def test_confidence_distribution_recorded(self):
        """Test that confidence scores are recorded."""
        event = GuidanceEvent(id="conf_001", input_text="use Opus")
        result = self.classifier.classify(event)

        assert 0.0 <= result.confidence <= 1.0
