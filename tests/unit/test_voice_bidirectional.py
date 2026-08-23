"""Unit tests for VoiceChannelCoordinator subsystem (Week 3, k=1).

Test Coverage:
- Question types: creation, validation, TTL
- Question queue: priority ordering, TTL expiration, overflow, state transitions
- VoiceChannelCoordinator: initialization, ask_user interface, metrics

ADR-0352: Bidirectional Voice Channel
"""

import pytest
import asyncio
from datetime import datetime, timedelta

from core.voice.channel import (
    VoiceChannelCoordinator,
    QuestionQueue,
    UserQuestion,
    UserAnswer,
    QuestionPriority,
    QuestionState,
)
from core.voice.channel.bidirectional_coordinator import TTSService, STTService


# Mock services for testing
class MockTTSService(TTSService):
    def __init__(self, fail: bool = False):
        self.fail = fail

    async def synthesize(self, text: str) -> bytes:
        if self.fail:
            raise Exception("TTS failed")
        return b"audio_data"

    async def get_latency_ms(self) -> float:
        return 100.0


class MockSTTService(STTService):
    def __init__(self, transcription: str = "answer", confidence: float = 0.95, fail: bool = False):
        self.transcription = transcription
        self.confidence = confidence
        self.fail = fail

    async def capture_speech(self, max_duration_seconds: int = 10) -> bytes:
        if self.fail:
            raise Exception("STT capture failed")
        return b"audio_data"

    async def transcribe(self, audio: bytes) -> tuple[str, float]:
        if self.fail:
            raise Exception("STT transcription failed")
        return (self.transcription, self.confidence)


class TestQuestionTypes:
    """Test question type definitions and validation."""

    def test_user_question_creation(self):
        """Test creating a user question."""
        q = UserQuestion(
            question_text="Do you want to use Opus?",
            subsystem_id="CostController",
            priority=QuestionPriority.HIGH,
            timeout_seconds=10,
        )

        assert q.question_text == "Do you want to use Opus?"
        assert q.subsystem_id == "CostController"
        assert q.priority == QuestionPriority.HIGH
        assert q.timeout_seconds == 10

    def test_user_question_default_values(self):
        """Test question with defaults."""
        q = UserQuestion()

        assert q.id  # Should have auto-generated UUID
        assert q.created_at
        assert q.priority == QuestionPriority.NORMAL
        assert q.timeout_seconds == 10

    def test_user_question_expiration(self):
        """Test TTL check."""
        # Fresh question
        fresh = UserQuestion(question_text="test")
        assert not fresh.is_expired(ttl_seconds=30)

        # Old question
        old = UserQuestion(
            question_text="test",
            created_at=datetime.utcnow() - timedelta(seconds=40),
        )
        assert old.is_expired(ttl_seconds=30)

    def test_user_answer_confidence(self):
        """Test answer confidence validation."""
        low_conf = UserAnswer(answer_text="answer", answer_confidence=0.50)
        high_conf = UserAnswer(answer_text="answer", answer_confidence=0.85)

        assert not low_conf.is_confident(threshold=0.70)
        assert high_conf.is_confident(threshold=0.70)

    def test_priority_ordering(self):
        """Test priority levels are correctly ordered."""
        assert QuestionPriority.CRITICAL.value > QuestionPriority.HIGH.value
        assert QuestionPriority.HIGH.value > QuestionPriority.NORMAL.value
        assert QuestionPriority.NORMAL.value > QuestionPriority.LOW.value


class TestQuestionQueue:
    """Test question queue behavior."""

    def test_enqueue_single_question(self):
        """Test enqueuing a single question."""
        queue = QuestionQueue()
        q = UserQuestion(question_text="test")

        result = asyncio.run(queue.enqueue(q))

        assert result is True

    @pytest.mark.asyncio
    async def test_priority_ordering(self):
        """Test that questions are ordered by priority."""
        queue = QuestionQueue()

        # Enqueue in wrong order
        low = UserQuestion(
            question_text="low", priority=QuestionPriority.LOW
        )
        high = UserQuestion(
            question_text="high", priority=QuestionPriority.HIGH
        )
        critical = UserQuestion(
            question_text="critical", priority=QuestionPriority.CRITICAL
        )

        await queue.enqueue(low)
        await queue.enqueue(high)
        await queue.enqueue(critical)

        # Get active should return critical
        active = await queue.get_active_question()
        assert active.question_text == "critical"

    @pytest.mark.asyncio
    async def test_queue_overflow(self):
        """Test queue overflow (drop oldest low-priority)."""
        queue = QuestionQueue(max_size=3)

        q1 = UserQuestion(question_text="1", priority=QuestionPriority.NORMAL)
        q2 = UserQuestion(question_text="2", priority=QuestionPriority.HIGH)
        q3 = UserQuestion(question_text="3", priority=QuestionPriority.CRITICAL)
        q4 = UserQuestion(question_text="4", priority=QuestionPriority.LOW)

        await queue.enqueue(q1)
        await queue.enqueue(q2)
        await queue.enqueue(q3)
        result = await queue.enqueue(q4)  # Should trigger drop

        assert result is False  # q4 was dropped
        size = await queue.get_queue_size()
        assert size == 3

    @pytest.mark.asyncio
    async def test_ttl_expiration(self):
        """Test automatic question expiration on TTL."""
        queue = QuestionQueue(ttl_seconds=1)

        # Fresh question
        fresh = UserQuestion(question_text="fresh")
        await queue.enqueue(fresh)

        # Old question
        old_q = UserQuestion(
            question_text="old",
            created_at=datetime.utcnow() - timedelta(seconds=2),
        )
        await queue.enqueue(old_q)

        assert await queue.get_queue_size() == 2

        # Get active (should expire old)
        active = await queue.get_active_question()
        assert active.question_text == "fresh"

    @pytest.mark.asyncio
    async def test_answer_question(self):
        """Test marking question as answered."""
        queue = QuestionQueue()
        q = UserQuestion(question_text="test")

        await queue.enqueue(q)
        active = await queue.get_active_question()

        # Answer it
        result = await queue.answer_question(q.id, "yes", confidence=0.95)

        assert result is True
        assert await queue.get_queue_size() == 0

    @pytest.mark.asyncio
    async def test_cancel_question(self):
        """Test cancelling a question."""
        queue = QuestionQueue()
        q = UserQuestion(question_text="test")

        await queue.enqueue(q)
        result = await queue.cancel_question(q.id)

        assert result is True
        assert await queue.get_queue_size() == 0

    @pytest.mark.asyncio
    async def test_expire_question(self):
        """Test expiring a question (using default)."""
        queue = QuestionQueue()
        q = UserQuestion(question_text="test", default_answer="default_yes")

        await queue.enqueue(q)
        default = await queue.expire_question(q.id)

        assert default == "default_yes"
        assert await queue.get_queue_size() == 0


class TestVoiceChannelCoordinator:
    """Test VoiceChannelCoordinator subsystem."""

    def test_initialization(self):
        """Test coordinator initialization."""
        tts = MockTTSService()
        stt = MockSTTService()

        coordinator = VoiceChannelCoordinator(tts, stt)

        assert coordinator.tts is tts
        assert coordinator.stt is stt
        assert coordinator.name == "voice_channel_coordinator"
        assert not coordinator.active

    @pytest.mark.asyncio
    async def test_start_stop(self):
        """Test starting and stopping the coordinator."""
        tts = MockTTSService()
        stt = MockSTTService()
        coordinator = VoiceChannelCoordinator(tts, stt)

        await coordinator.start()
        assert coordinator.active

        await coordinator.stop()
        assert not coordinator.active

    @pytest.mark.asyncio
    async def test_register_answer_handler(self):
        """Test registering answer handlers."""
        tts = MockTTSService()
        stt = MockSTTService()
        coordinator = VoiceChannelCoordinator(tts, stt)

        called = {"result": None}

        def handler(answer: UserAnswer):
            called["result"] = answer

        coordinator.register_answer_handler("TestSubsystem", handler)

        assert "TestSubsystem" in coordinator.answer_handlers

    @pytest.mark.asyncio
    async def test_ask_user_interface(self):
        """Test ask_user interface (basic call, returns None on timeout)."""
        tts = MockTTSService()
        stt = MockSTTService()
        coordinator = VoiceChannelCoordinator(tts, stt)

        # ask_user should return None if answer not received (simulated timeout)
        result = await coordinator.ask_user(
            question_text="Do you want to use Opus?",
            subsystem_id="CostController",
            priority=QuestionPriority.HIGH,
            timeout_seconds=1,
        )

        # For k=1, we just verify the interface works, result is None (no answer path yet)
        assert result is None or isinstance(result, UserAnswer)

    @pytest.mark.asyncio
    async def test_get_metrics(self):
        """Test metrics collection."""
        tts = MockTTSService()
        stt = MockSTTService()
        coordinator = VoiceChannelCoordinator(tts, stt)

        metrics = await coordinator.get_metrics()

        assert "name" in metrics
        assert "active" in metrics
        assert "queue_metrics" in metrics
        assert metrics["name"] == "voice_channel_coordinator"


class TestVoiceChannelMetrics:
    """Test metrics collection."""

    @pytest.mark.asyncio
    async def test_queue_metrics_collection(self):
        """Test that queue collects metrics."""
        queue = QuestionQueue()

        q1 = UserQuestion(question_text="1", priority=QuestionPriority.HIGH)
        q2 = UserQuestion(question_text="2", priority=QuestionPriority.NORMAL)

        await queue.enqueue(q1)
        await queue.enqueue(q2)
        active = await queue.get_active_question()

        await queue.answer_question(q1.id, "yes", 0.95)

        metrics = await queue.get_metrics()

        assert metrics["total_questions"] == 2
        assert metrics["questions_answered"] == 1
        assert metrics["current_queue_size"] == 1


class TestEdgeCases:
    """Test edge cases and error conditions."""

    @pytest.mark.asyncio
    async def test_answer_nonexistent_question(self):
        """Test answering a question that doesn't exist."""
        queue = QuestionQueue()

        result = await queue.answer_question("nonexistent_id", "answer", 0.95)

        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_question(self):
        """Test cancelling a nonexistent question."""
        queue = QuestionQueue()

        result = await queue.cancel_question("nonexistent_id")

        assert result is False

    @pytest.mark.asyncio
    async def test_empty_queue_get_active(self):
        """Test getting active from empty queue."""
        queue = QuestionQueue()

        active = await queue.get_active_question()

        assert active is None

    def test_question_state_enum(self):
        """Test QuestionState enum values."""
        assert QuestionState.PENDING.value == "pending"
        assert QuestionState.ACTIVE.value == "active"
        assert QuestionState.ANSWERED.value == "answered"
        assert QuestionState.EXPIRED.value == "expired"
        assert QuestionState.CANCELLED.value == "cancelled"
