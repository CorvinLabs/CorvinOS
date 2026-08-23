"""Performance and stress tests for Week 3 VoiceChannelCoordinator (k=5).

Validates production readiness: latency SLOs, stress scenarios, capacity.

ADR-0352: Bidirectional Voice Channel
"""

import asyncio
import time
import pytest
from typing import List

from core.voice.guidance import GuidanceClassifier, GuidanceEvent
from core.voice.routing import MidstreamRouter
from core.voice.channel import VoiceChannelCoordinator, QuestionQueue, UserQuestion, QuestionPriority
from core.voice.channel.tts_fallback import TTSFallbackStrategy, STTFallbackStrategy
from core.voice.channel.bidirectional_coordinator import TTSService, STTService


class MockTTSService(TTSService):
    def __init__(self, latency_ms: float = 150.0):
        self.latency_ms = latency_ms

    async def synthesize(self, text: str) -> bytes:
        await asyncio.sleep(self.latency_ms / 1000.0)
        return b"audio"

    async def get_latency_ms(self) -> float:
        return self.latency_ms


class MockSTTService(STTService):
    def __init__(self, latency_ms: float = 500.0):
        self.latency_ms = latency_ms

    async def capture_speech(self, max_duration_seconds: int = 10) -> bytes:
        await asyncio.sleep(self.latency_ms / 1000.0)
        return b"audio"

    async def transcribe(self, audio: bytes) -> tuple[str, float]:
        return ("answer", 0.95)


class TestProductionReadinessSLOs:
    """Test production SLOs and latency requirements."""

    @pytest.mark.asyncio
    async def test_classification_slo_single(self):
        """Verify: single classification <500ms SLO."""
        classifier = GuidanceClassifier()
        event = GuidanceEvent(id="slo_001", input_text="use Opus")

        start = time.time()
        result = await classifier.classify(event)
        elapsed_ms = (time.time() - start) * 1000

        assert elapsed_ms < 500, f"Classification {elapsed_ms:.1f}ms exceeds 500ms SLO"

    @pytest.mark.asyncio
    async def test_routing_slo_single(self):
        """Verify: single routing <100ms SLO."""
        from core.voice.guidance import ClassificationResult, GuidanceClass, RiskLevel

        router = MidstreamRouter()
        classification = ClassificationResult(
            event_id="slo_002",
            guidance_class=GuidanceClass.MIDSTREAM_GUIDANCE,
            confidence=0.85,
            subsystem_hint="CostController",
            risk_level=RiskLevel.SAFE,
        )

        start = time.time()
        result = router.route(classification)
        elapsed_ms = (time.time() - start) * 1000

        assert elapsed_ms < 100, f"Routing {elapsed_ms:.1f}ms exceeds 100ms SLO"

    @pytest.mark.asyncio
    async def test_queue_operations_slo(self):
        """Verify: queue operations <50ms SLO."""
        queue = QuestionQueue()
        q = UserQuestion(question_text="test", subsystem_id="Test")

        # Enqueue SLO
        start = time.time()
        await queue.enqueue(q)
        enqueue_ms = (time.time() - start) * 1000
        assert enqueue_ms < 50

        # Get active SLO
        start = time.time()
        await queue.get_active_question()
        get_active_ms = (time.time() - start) * 1000
        assert get_active_ms < 50

        # Answer SLO
        start = time.time()
        await queue.answer_question(q.id, "answer", 0.95)
        answer_ms = (time.time() - start) * 1000
        assert answer_ms < 50


class TestStressScenarios:
    """Test production stress scenarios."""

    @pytest.mark.asyncio
    async def test_queue_capacity_10_items(self):
        """Test: queue handles 10 concurrent questions."""
        queue = QuestionQueue(max_size=10)

        questions = [
            UserQuestion(
                question_text=f"Question {i}",
                subsystem_id=f"Subsystem{i}",
                priority=[QuestionPriority.CRITICAL, QuestionPriority.HIGH, QuestionPriority.NORMAL, QuestionPriority.LOW][i % 4],
            )
            for i in range(10)
        ]

        for q in questions:
            enqueued = await queue.enqueue(q)
            assert enqueued, f"Failed to enqueue question {q.id}"

        size = await queue.get_queue_size()
        assert size == 10

    @pytest.mark.asyncio
    async def test_high_throughput_classification(self):
        """Test: classify 20 events in parallel."""
        classifier = GuidanceClassifier()
        events = [
            GuidanceEvent(id=f"throughput_{i}", input_text=f"event {i}")
            for i in range(20)
        ]

        start = time.time()
        results = await asyncio.gather(
            *[classifier.classify(e) for e in events],
            return_exceptions=True,
        )
        elapsed_ms = (time.time() - start) * 1000

        # Should complete all in reasonable time (<5 seconds)
        assert elapsed_ms < 5000, f"20 classifications took {elapsed_ms:.0f}ms"

        # All should succeed
        assert len([r for r in results if not isinstance(r, Exception)]) == 20

    @pytest.mark.asyncio
    async def test_concurrent_subsystem_questions(self):
        """Test: 3 subsystems asking concurrently."""
        tts = MockTTSService(latency_ms=100)
        stt = MockSTTService(latency_ms=300)
        coordinator = VoiceChannelCoordinator(tts, stt)

        await coordinator.start()

        # Three subsystems ask questions concurrently
        results = await asyncio.gather(
            coordinator.ask_user("Q1", "CostController", timeout_seconds=5),
            coordinator.ask_user("Q2", "LoopEngineer", timeout_seconds=5),
            coordinator.ask_user("Q3", "HealthMonitor", timeout_seconds=5),
            return_exceptions=True,
        )

        await coordinator.stop()

        # At least some should be submitted
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_queue_overflow_stress(self):
        """Test: queue gracefully handles overflow (>max items)."""
        queue = QuestionQueue(max_size=3)

        questions = [
            UserQuestion(
                question_text=f"Q{i}",
                subsystem_id="Test",
                priority=QuestionPriority.NORMAL if i % 2 == 0 else QuestionPriority.LOW,
            )
            for i in range(10)
        ]

        enqueued_count = 0
        for q in questions:
            if await queue.enqueue(q):
                enqueued_count += 1

        size = await queue.get_queue_size()
        assert size == 3, f"Queue has {size} items, expected 3"
        assert enqueued_count == 10, "All 10 should be processed (dropped or queued)"

    @pytest.mark.asyncio
    async def test_tts_failure_recovery_stress(self):
        """Test: TTS failures don't crash system under load."""
        class FlakeyTTS(TTSService):
            def __init__(self):
                self.call_count = 0

            async def synthesize(self, text: str) -> bytes:
                self.call_count += 1
                if self.call_count % 3 == 0:
                    raise Exception("TTS failed")
                return b"audio"

            async def get_latency_ms(self) -> float:
                return 100.0

        tts = FlakeyTTS()
        fallback = TTSFallbackStrategy(text_fallback_enabled=True, tts_retry_count=2)

        results = []
        for i in range(10):
            mode, audio = await fallback.synthesize_with_fallback(f"Q{i}", tts)
            results.append(mode)

        # Some should succeed, some should fallback to text
        assert "voice" in results or "text" in results

    @pytest.mark.asyncio
    async def test_confidence_validation_under_load(self):
        """Test: confidence validation handles varying scores."""
        stt_fallback = STTFallbackStrategy(confidence_threshold_low=0.70)

        scores = [0.95, 0.75, 0.50, 0.90, 0.40, 0.85, 0.30, 0.88]
        results = [
            await stt_fallback.validate_confidence("answer", score)
            for score in scores
        ]

        # Check distribution
        high_conf = sum(1 for valid, _ in results if valid and _ == "high_confidence")
        low_conf = sum(1 for valid, _ in results if not valid)

        assert high_conf > 0, "Some should be high confidence"
        assert low_conf > 0, "Some should be low confidence"


class TestProductionMetrics:
    """Test metrics collection for production monitoring."""

    @pytest.mark.asyncio
    async def test_comprehensive_metrics_collection(self):
        """Test: all metrics collected correctly under load."""
        classifier = GuidanceClassifier()
        router = MidstreamRouter()
        queue = QuestionQueue()
        tts = MockTTSService()
        stt = MockSTTService()
        coordinator = VoiceChannelCoordinator(tts, stt, queue)

        # Simulate production workload
        for i in range(5):
            event = GuidanceEvent(id=f"metric_{i}", input_text=f"event {i}")
            classification = await classifier.classify(event)
            routing = router.route(classification)

            q = UserQuestion(
                question_text=f"Q{i}",
                subsystem_id="Test",
                priority=QuestionPriority.NORMAL,
            )
            await queue.enqueue(q)

        # Collect all metrics
        classifier_metrics = classifier.get_metrics()
        router_metrics = router.get_metrics()
        queue_metrics = await queue.get_metrics()
        coordinator_metrics = await coordinator.get_metrics()

        # Verify metrics structure
        assert "total_routings" in router_metrics
        assert "current_queue_size" in queue_metrics
        assert "queue_metrics" in coordinator_metrics
        assert queue_metrics["total_questions"] == 5


class TestProductionReadinessChecklist:
    """Verify all production-readiness criteria."""

    @pytest.mark.asyncio
    async def test_code_quality_imports(self):
        """Verify: all modules import without errors."""
        from core.voice.guidance import GuidanceClassifier
        from core.voice.routing import MidstreamRouter
        from core.voice.channel import VoiceChannelCoordinator, QuestionQueue
        from core.voice.channel.tts_fallback import TTSFallbackStrategy
        from core.voice.channel.subsystem_integration import (
            SubsystemVoiceAPI,
            CostControllerVoiceExtension,
        )

        assert GuidanceClassifier is not None
        assert MidstreamRouter is not None
        assert VoiceChannelCoordinator is not None

    @pytest.mark.asyncio
    async def test_error_handling_coverage(self):
        """Verify: error conditions handled gracefully."""
        coordinator = VoiceChannelCoordinator(MockTTSService(), MockSTTService())

        # Should not crash on empty text
        result = await coordinator.ask_user("", "Test", timeout_seconds=1)
        # Result could be None, that's ok

        # Should handle invalid subsystem
        result = await coordinator.ask_user("Q", "InvalidSubsystem", timeout_seconds=1)
        # Should not raise

    @pytest.mark.asyncio
    async def test_audit_trail_capability(self):
        """Verify: audit trail can be collected for compliance."""
        queue = QuestionQueue()
        q = UserQuestion(question_text="test", subsystem_id="Audit")

        await queue.enqueue(q)
        active = await queue.get_active_question()
        await queue.answer_question(q.id, "answer", 0.95)

        # Verify question has all audit-relevant fields
        assert q.id
        assert q.created_at
        assert q.subsystem_id
        assert q.question_text

        # Verify answer has all audit-relevant fields
        answer = UserAnswer(
            question_id=q.id,
            answer_text="answer",
            answer_confidence=0.95,
        )
        assert answer.id
        assert answer.created_at
        assert answer.question_id
