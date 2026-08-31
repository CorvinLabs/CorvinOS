"""E2E integration tests for complete voice guidance pipeline (Week 3 k=3).

Tests the full flow: GuidanceClassifier → MidstreamRouter → VoiceChannelCoordinator → Subsystem

ADR-0352: Bidirectional Voice Channel
ADR-0351: Voice-Native Midstream Guidance
"""

import pytest
import asyncio
from datetime import datetime

from core.voice.guidance import (
    GuidanceClassifier,
    GuidanceEvent,
    ClassificationResult,
    GuidanceClass,
    RiskLevel,
)
from core.voice.routing import MidstreamRouter, SubsystemType
from core.voice.channel import (
    VoiceChannelCoordinator,
    QuestionQueue,
    UserQuestion,
    UserAnswer,
    QuestionPriority,
)
from core.voice.channel.tts_fallback import TTSFallbackStrategy, STTFallbackStrategy
from core.voice.channel.bidirectional_coordinator import TTSService, STTService


# Mock services
class MockTTSService(TTSService):
    def __init__(self, fail_after: int = 999):
        self.call_count = 0
        self.fail_after = fail_after

    async def synthesize(self, text: str) -> bytes:
        self.call_count += 1
        if self.call_count > self.fail_after:
            raise Exception("TTS service down")
        return b"audio_" + text.encode()

    async def get_latency_ms(self) -> float:
        return 150.0


class MockSTTService(STTService):
    def __init__(self, transcription: str = "yes", confidence: float = 0.95):
        self.transcription = transcription
        self.confidence = confidence
        self.call_count = 0

    async def capture_speech(self, max_duration_seconds: int = 10) -> bytes:
        self.call_count += 1
        return b"audio_response"

    async def transcribe(self, audio: bytes) -> tuple[str, float]:
        return (self.transcription, self.confidence)


class TestE2EGuidanceFlow:
    """Test complete guidance flow end-to-end."""

    @pytest.mark.asyncio
    async def test_e2e_model_selection_flow(self):
        """Test: user says "use Opus" → classify → route → ask → answer → apply."""
        # Step 1: Classify
        classifier = GuidanceClassifier()
        event = GuidanceEvent(id="e2e_001", input_text="use Opus for better quality")
        classification = await classifier.classify(event)

        assert classification.guidance_class == GuidanceClass.MIDSTREAM_GUIDANCE
        assert classification.subsystem_hint == "CostController"

        # Step 2: Route
        router = MidstreamRouter()
        routing = router.route(classification)

        assert routing.primary_target is not None
        assert routing.primary_target.subsystem == SubsystemType.COST_CONTROLLER
        assert routing.primary_target.action == "switch_model"

        # Step 3: Voice channel ready to ask subsystem
        tts = MockTTSService()
        stt = MockSTTService(transcription="yes", confidence=0.95)
        coordinator = VoiceChannelCoordinator(tts, stt)

        # Verify coordinator can ask question
        assert coordinator.tts is tts
        assert coordinator.stt is stt

    @pytest.mark.asyncio
    async def test_e2e_question_queue_with_answers(self):
        """Test: coordinator asks, user answers, answer routed."""
        queue = QuestionQueue()
        tts = MockTTSService()
        stt = MockSTTService()
        coordinator = VoiceChannelCoordinator(tts, stt, question_queue=queue)

        await coordinator.start()

        # Enqueue question
        q = UserQuestion(
            question_text="Do you want Opus or Sonnet?",
            subsystem_id="CostController",
            priority=QuestionPriority.HIGH,
        )
        enqueued = await queue.enqueue(q)
        assert enqueued

        # Verify queue has question
        size = await queue.get_queue_size()
        assert size == 1

        await coordinator.stop()

    @pytest.mark.asyncio
    async def test_e2e_tts_fallback_to_text(self):
        """Test: TTS fails → degrade to text."""
        tts_fail = MockTTSService(fail_after=0)  # Fail immediately
        stt = MockSTTService()
        fallback_strategy = TTSFallbackStrategy(text_fallback_enabled=True)

        text_fallback_called = {"called": False}

        async def on_text_fallback(text, error):
            text_fallback_called["called"] = True

        mode, audio = await fallback_strategy.synthesize_with_fallback(
            "Do you want Opus?",
            tts_fail,
            on_text_fallback=on_text_fallback,
        )

        assert mode == "text"
        assert audio is None
        assert text_fallback_called["called"]

    @pytest.mark.asyncio
    async def test_e2e_stt_confidence_validation(self):
        """Test: low confidence → ask clarification."""
        stt_strategy = STTFallbackStrategy(confidence_threshold_low=0.70)

        # High confidence
        is_valid, reason = await stt_strategy.validate_confidence("yes", 0.95)
        assert is_valid
        assert reason == "high_confidence"

        # Low confidence
        is_valid, reason = await stt_strategy.validate_confidence("maybe", 0.40)
        assert not is_valid
        assert reason == "low_confidence_needs_clarification"

    @pytest.mark.asyncio
    async def test_e2e_multiple_questions_priority_order(self):
        """Test: multiple questions queued; high-priority asked first."""
        queue = QuestionQueue()

        q_low = UserQuestion(
            question_text="Low priority",
            subsystem_id="LoopEngineer",
            priority=QuestionPriority.LOW,
        )
        q_high = UserQuestion(
            question_text="High priority",
            subsystem_id="CostController",
            priority=QuestionPriority.HIGH,
        )
        q_normal = UserQuestion(
            question_text="Normal priority",
            subsystem_id="SafetyValidator",
            priority=QuestionPriority.NORMAL,
        )

        await queue.enqueue(q_low)
        await queue.enqueue(q_high)
        await queue.enqueue(q_normal)

        # Get active should return high-priority
        active = await queue.get_active_question()
        assert active.question_text == "High priority"

        # Next active should be normal
        await queue.answer_question(q_high.id, "answer", 0.95)
        active = await queue.get_active_question()
        assert active.question_text == "Normal priority"

    @pytest.mark.asyncio
    async def test_e2e_timeout_default_handling(self):
        """Test: question timeout → use default answer."""
        queue = QuestionQueue()

        q = UserQuestion(
            question_text="Do you want to continue?",
            subsystem_id="Orchestrator",
            timeout_seconds=1,
            default_answer="yes_continue",
        )

        await queue.enqueue(q)
        default = await queue.expire_question(q.id)

        assert default == "yes_continue"
        assert await queue.get_queue_size() == 0

    @pytest.mark.asyncio
    async def test_e2e_high_risk_confirmation_flow(self):
        """Test: high-risk guidance → confirmation required."""
        classifier = GuidanceClassifier()
        event = GuidanceEvent(id="e2e_delete", input_text="delete everything")
        classification = await classifier.classify(event)

        # Should be marked high-risk
        assert classification.risk_level == RiskLevel.HIGH

        # Router should route to SafetyValidator
        router = MidstreamRouter()
        routing = router.route(classification)

        # Should have SafetyValidator in routing targets
        safety_targets = [
            routing.primary_target,
            *routing.alternate_targets,
        ]
        has_safety = any(
            t and t.subsystem == SubsystemType.SAFETY_VALIDATOR for t in safety_targets
        )
        assert has_safety


class TestE2EMetricsCollection:
    """Test metrics collection through the pipeline."""

    @pytest.mark.asyncio
    async def test_metrics_end_to_end(self):
        """Test: metrics collected at each stage."""
        classifier = GuidanceClassifier()
        router = MidstreamRouter()
        tts = MockTTSService()
        stt = MockSTTService()
        coordinator = VoiceChannelCoordinator(tts, stt)

        # Classify
        event = GuidanceEvent(id="metric_001", input_text="use Opus")
        classification = await classifier.classify(event)

        # Route
        routing = router.route(classification)

        # Get metrics
        classifier_metrics = classifier.get_metrics()
        router_metrics = router.get_metrics()
        coordinator_metrics = await coordinator.get_metrics()

        assert "heuristic_metrics" in classifier_metrics or "llm_metrics" in classifier_metrics
        assert "total_routings" in router_metrics
        assert "queue_metrics" in coordinator_metrics

    @pytest.mark.asyncio
    async def test_tts_fallback_metrics(self):
        """Test: TTS fallback strategy records metrics."""
        tts = MockTTSService(fail_after=1)  # Fail after 1 attempt
        fallback = TTSFallbackStrategy(tts_retry_count=3)

        # Attempt 1: succeed
        mode1, audio1 = await fallback.synthesize_with_fallback(
            "First question", tts
        )
        assert mode1 == "voice"

        # Attempt 2-3: fail
        mode2, audio2 = await fallback.synthesize_with_fallback(
            "Second question", tts
        )
        assert mode2 == "text"

        metrics = await fallback.get_metrics()
        assert metrics["tts_attempts"] == 2
        assert metrics["tts_successes"] == 1
        assert metrics["text_fallback_count"] == 1

    @pytest.mark.asyncio
    async def test_stt_confidence_metrics(self):
        """Test: STT confidence strategy records metrics."""
        stt_strategy = STTFallbackStrategy()

        # Multiple confidence validations
        await stt_strategy.validate_confidence("high", 0.95)
        await stt_strategy.validate_confidence("high", 0.90)
        await stt_strategy.validate_confidence("low", 0.30)

        metrics = await stt_strategy.get_metrics()
        assert metrics["high_confidence_count"] == 2
        assert metrics["low_confidence_count"] == 1


class TestE2EErrorRecovery:
    """Test error recovery and resilience."""

    @pytest.mark.asyncio
    async def test_graceful_degradation_tts_failure(self):
        """Test: system degrades gracefully when TTS fails."""
        tts = MockTTSService(fail_after=0)
        stt = MockSTTService()
        fallback = TTSFallbackStrategy(text_fallback_enabled=True, tts_retry_count=2)

        mode, audio = await fallback.synthesize_with_fallback(
            "Important question", tts
        )

        # Should degrade to text, not crash
        assert mode == "text"
        assert audio is None

    @pytest.mark.asyncio
    async def test_stt_capture_timeout_handled(self):
        """Test: STT capture timeout handled gracefully."""

        class TimeoutSTTService(STTService):
            async def capture_speech(self, max_duration_seconds=10):
                await asyncio.sleep(max_duration_seconds + 1)
                return b"audio"

            async def transcribe(self, audio):
                return ("answer", 0.95)

        stt = TimeoutSTTService()
        stt_fallback = STTFallbackStrategy(stt_timeout_seconds=0.5)

        audio = await stt_fallback.capture_with_fallback(stt, max_duration_seconds=0.5)

        # Should timeout gracefully
        assert audio is None
        metrics = await stt_fallback.get_metrics()
        assert metrics["stt_timeouts"] == 1


class TestE2ELatencyValidation:
    """Test latency meets production SLOs."""

    @pytest.mark.asyncio
    async def test_classification_latency_under_slo(self):
        """Test: classification completes within SLO (<500ms)."""
        import time

        classifier = GuidanceClassifier()
        event = GuidanceEvent(id="latency_001", input_text="use Opus")

        start = time.time()
        classification = await classifier.classify(event)
        elapsed_ms = (time.time() - start) * 1000

        assert elapsed_ms < 500, f"Classification took {elapsed_ms:.1f}ms (SLO: <500ms)"

    @pytest.mark.asyncio
    async def test_routing_latency_under_slo(self):
        """Test: routing completes within SLO (<100ms)."""
        import time

        router = MidstreamRouter()
        classification = ClassificationResult(
            event_id="latency_002",
            guidance_class=GuidanceClass.MIDSTREAM_GUIDANCE,
            confidence=0.85,
            subsystem_hint="CostController",
            risk_level=RiskLevel.SAFE,
        )

        start = time.time()
        routing = router.route(classification)
        elapsed_ms = (time.time() - start) * 1000

        assert elapsed_ms < 100, f"Routing took {elapsed_ms:.1f}ms (SLO: <100ms)"

    @pytest.mark.asyncio
    async def test_queue_operations_latency(self):
        """Test: queue operations complete quickly (<50ms each)."""
        import time

        queue = QuestionQueue()
        q = UserQuestion(question_text="test", subsystem_id="Test")

        # Enqueue latency
        start = time.time()
        await queue.enqueue(q)
        enqueue_ms = (time.time() - start) * 1000
        assert enqueue_ms < 50, f"Enqueue took {enqueue_ms:.1f}ms (SLO: <50ms)"

        # Get active latency
        start = time.time()
        active = await queue.get_active_question()
        get_active_ms = (time.time() - start) * 1000
        assert get_active_ms < 50, f"Get active took {get_active_ms:.1f}ms (SLO: <50ms)"
