"""VoiceChannelCoordinator subsystem for bidirectional voice interaction.

Manages TTS/STT services, question queue, and routing answers back to subsystems.

ADR-0352: Bidirectional Voice Channel
"""

import asyncio
import logging
from typing import Optional, Callable

from .question_queue import QuestionQueue
from .question_types import UserQuestion, UserAnswer, QuestionPriority

logger = logging.getLogger(__name__)


class TTSService:
    """Abstract TTS (Text-to-Speech) service."""

    async def synthesize(self, text: str) -> bytes:
        """Convert text to audio.

        Args:
            text: Text to synthesize

        Returns:
            Audio data (bytes)

        Raises:
            Exception: On TTS service failure
        """
        raise NotImplementedError

    async def get_latency_ms(self) -> float:
        """Estimated latency in milliseconds."""
        return 200.0  # Default estimate


class STTService:
    """Abstract STT (Speech-to-Text) service."""

    async def capture_speech(self, max_duration_seconds: int = 10) -> bytes:
        """Capture speech audio from microphone.

        Args:
            max_duration_seconds: Max recording time

        Returns:
            Audio data (bytes)

        Raises:
            asyncio.TimeoutError: On timeout
            Exception: On STT service failure
        """
        raise NotImplementedError

    async def transcribe(self, audio: bytes) -> tuple[str, float]:
        """Convert audio to text.

        Args:
            audio: Audio data

        Returns:
            (transcribed_text, confidence_score)

        Raises:
            Exception: On transcription failure
        """
        raise NotImplementedError


class VoiceChannelCoordinator:
    """Bidirectional voice channel for Brain subsystems to ask questions."""

    def __init__(
        self,
        tts_service: TTSService,
        stt_service: STTService,
        question_queue: Optional[QuestionQueue] = None,
    ):
        """Initialize voice channel coordinator.

        Args:
            tts_service: Text-to-speech service
            stt_service: Speech-to-text service
            question_queue: Question queue (default: new QuestionQueue)
        """
        self.tts = tts_service
        self.stt = stt_service
        self.question_queue = question_queue or QuestionQueue()
        self.answer_handlers: dict[str, Callable] = {}  # subsystem_id → handler
        self.name = "voice_channel_coordinator"
        self.active = False

    async def start(self) -> None:
        """Start the voice channel (begin processing question queue)."""
        self.active = True
        logger.info("Voice channel started")
        asyncio.create_task(self._process_question_loop())

    async def stop(self) -> None:
        """Stop the voice channel."""
        self.active = False
        logger.info("Voice channel stopped")

    async def ask_user(
        self,
        question_text: str,
        subsystem_id: str,
        priority: QuestionPriority = QuestionPriority.NORMAL,
        timeout_seconds: int = 10,
        default_answer: Optional[str] = None,
    ) -> Optional[UserAnswer]:
        """Ask user a question via voice; wait for answer.

        Args:
            question_text: Question to ask
            subsystem_id: ID of subsystem asking (e.g., "CostController")
            priority: Question priority
            timeout_seconds: Max time to wait for answer
            default_answer: Default if timeout

        Returns:
            UserAnswer if received, None on timeout/error
        """
        question = UserQuestion(
            question_text=question_text,
            subsystem_id=subsystem_id,
            priority=priority,
            timeout_seconds=timeout_seconds,
            default_answer=default_answer,
        )

        # Enqueue
        enqueued = await self.question_queue.enqueue(question)
        if not enqueued:
            logger.warning(f"Question {question.id} dropped due to queue full")
            return None

        # Wait for answer (with timeout)
        try:
            answer = await asyncio.wait_for(
                self._wait_for_answer(question.id),
                timeout=timeout_seconds + 5,  # 5s buffer for processing
            )
            return answer
        except asyncio.TimeoutError:
            logger.warning(f"Answer timeout for question {question.id}")
            await self.question_queue.expire_question(question.id)
            return None

    async def _wait_for_answer(self, question_id: str) -> Optional[UserAnswer]:
        """Wait for answer to a specific question (internal)."""
        # Poll for answer every 100ms
        max_wait = 300  # 30 seconds
        waited = 0

        while waited < max_wait:
            await asyncio.sleep(0.1)
            waited += 0.1

            # Check if answer was received
            # (In production, this would be event-driven, not polling)
            if question_id in self.answer_handlers:
                return self.answer_handlers[question_id]

        return None

    async def _process_question_loop(self) -> None:
        """Main loop: get question, ask user, handle answer."""
        while self.active:
            try:
                # Get next question
                question = await self.question_queue.get_active_question()
                if not question:
                    await asyncio.sleep(0.5)
                    continue

                # Ask via TTS
                logger.debug(f"Asking question {question.id}: {question.question_text[:50]}...")
                await self._ask_and_capture(question)

            except Exception as e:
                logger.error(f"Error in question loop: {e}")
                await asyncio.sleep(1)

    async def _ask_and_capture(self, question: UserQuestion) -> None:
        """Ask question via TTS and capture answer via STT."""
        answer = None

        # Step 1: TTS
        try:
            tts_latency = await self.tts.get_latency_ms()
            logger.debug(f"TTS estimated latency: {tts_latency}ms")
            await self.tts.synthesize(question.question_text)
        except Exception as e:
            logger.warning(f"TTS failed for question {question.id}: {e}")
            if question.allow_text_fallback:
                logger.info(f"Degrading to text fallback for question {question.id}")
                await self.question_queue.metrics.record_text_fallback()
                # In production: emit event for user to type answer
                await self.question_queue.expire_question(question.id)
                return
            else:
                # No fallback, expire and use default
                await self.question_queue.expire_question(question.id)
                return

        # Step 2: STT
        try:
            audio = await asyncio.wait_for(
                self.stt.capture_speech(max_duration_seconds=question.timeout_seconds),
                timeout=question.timeout_seconds,
            )
            answer_text, confidence = await self.stt.transcribe(audio)

            answer = UserAnswer(
                question_id=question.id,
                answer_text=answer_text,
                answer_confidence=confidence,
                channel="voice",
            )

            logger.debug(f"Captured answer: '{answer_text}' (confidence={confidence:.2f})")

            # Step 3: Validate confidence
            if not answer.is_confident(threshold=0.70):
                logger.warning(
                    f"Low STT confidence for question {question.id}: {confidence:.2f}"
                )
                # Ask clarification or use default
                await self.question_queue.expire_question(question.id)
                return

            # Step 4: Mark answered and route back
            await self.question_queue.answer_question(
                question.id, answer_text, confidence
            )

            # Route to subsystem (simple handler-based dispatch)
            if question.subsystem_id in self.answer_handlers:
                self.answer_handlers[question.subsystem_id](answer)

            logger.info(f"Answer routed to {question.subsystem_id}")

        except asyncio.TimeoutError:
            logger.warning(f"STT timeout for question {question.id}")
            await self.question_queue.expire_question(question.id)

        except Exception as e:
            logger.error(f"STT failed for question {question.id}: {e}")
            await self.question_queue.expire_question(question.id)

    def register_answer_handler(
        self, subsystem_id: str, handler: Callable[[UserAnswer], None]
    ) -> None:
        """Register a handler for answers to a subsystem's questions.

        Args:
            subsystem_id: Subsystem ID
            handler: Async callback (answer) → None
        """
        self.answer_handlers[subsystem_id] = handler
        logger.debug(f"Registered answer handler for {subsystem_id}")

    async def get_metrics(self) -> dict:
        """Return channel metrics."""
        return {
            "name": self.name,
            "active": self.active,
            "queue_metrics": await self.question_queue.get_metrics(),
        }
