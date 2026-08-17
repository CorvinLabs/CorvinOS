"""Token Instrumentation — Hook points for measuring token consumption (Phase 1: TMF).

Integration points:
- WorkerEngine.run(): capture input/output tokens from LLM responses
- ExecutionContext: measure context load overhead
- SkillForge: measure skill injection overhead
- Confidence scoring: measure early-exit savings
- Cache hits: measure decision history savings
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import time

from core.learning.event_schema import LearningEventType, LearningEvent, TokenMetricsPayload


@dataclass
class TokenCounter:
    """Tracks token consumption for a single turn."""

    turn_id: str
    engine: str
    engine_tier: str = "cloud"
    model_id: Optional[str] = None

    # Main metrics
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    # Subsystem breakdown
    subsystem_tokens: dict[str, int] = field(default_factory=dict)

    # Timestamps
    start_time: datetime = field(default_factory=datetime.utcnow)
    end_time: Optional[datetime] = None
    latency_ms: Optional[int] = None

    # Baseline (set by caller after inference)
    baseline_tokens: Optional[int] = None
    iterations: int = 1

    # Task metadata
    task_type: Optional[str] = None
    task_domain: Optional[str] = None
    task_complexity: Optional[str] = None

    # Outcome
    outcome_quality: Optional[str] = None
    required_followup: bool = False

    def record_llm_call(self, input_tokens: int, output_tokens: int) -> None:
        """Record actual tokens from LLM response."""
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = input_tokens + output_tokens

    def record_subsystem_usage(self, subsystem: str, tokens: int) -> None:
        """Record token usage by a subsystem (e.g., 'confidence', 'cache', 'skills')."""
        self.subsystem_tokens[subsystem] = self.subsystem_tokens.get(subsystem, 0) + tokens

    def finalize(self) -> None:
        """Mark turn as complete, calculate latency."""
        self.end_time = datetime.utcnow()
        self.latency_ms = int((self.end_time - self.start_time).total_seconds() * 1000)

    @property
    def savings_tokens(self) -> Optional[int]:
        """Tokens saved vs baseline (None if no baseline)."""
        if self.baseline_tokens is None:
            return None
        return max(0, self.baseline_tokens - self.total_tokens)

    @property
    def savings_percent(self) -> Optional[float]:
        """Percentage savings vs baseline."""
        if self.baseline_tokens is None or self.baseline_tokens == 0:
            return None
        return (self.savings_tokens / self.baseline_tokens) * 100

    def to_event(
        self,
        tenant_id: str,
        instance_id: str,
        session_id: str,
        user_id: Optional[str] = None,
        skill_name: Optional[str] = None,
    ) -> LearningEvent:
        """Convert to LearningEvent for storage."""
        payload = TokenMetricsPayload(
            turn_id=self.turn_id,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            total_tokens=self.total_tokens,
            engine=self.engine,
            engine_tier=self.engine_tier,
            model_id=self.model_id,
            baseline_tokens=self.baseline_tokens,
            savings_tokens=self.savings_tokens,
            savings_percent=self.savings_percent,
            task_type=self.task_type,
            task_domain=self.task_domain,
            task_complexity=self.task_complexity,
            subsystem_tokens=self.subsystem_tokens,
            iterations_count=self.iterations,
            latency_ms=self.latency_ms,
            outcome_quality=self.outcome_quality,
            required_followup=self.required_followup,
        )

        return LearningEvent(
            event_type=LearningEventType.TOKEN_METRICS,
            tenant_id=tenant_id,
            instance_id=instance_id,
            session_id=session_id,
            user_id=user_id,
            skill_name=skill_name,
            timestamp_utc=self.end_time or datetime.utcnow(),
            payload={"token_metrics": payload.__dict__},
            tags=["instrumentation", "phase1-tmf"],
        )


class TokenInstrumentationHooks:
    """Hook points for instrumentation (to be integrated into WorkerEngine)."""

    @staticmethod
    def on_worker_engine_start(turn_id: str, engine: str, engine_tier: str = "cloud") -> TokenCounter:
        """Called when WorkerEngine.run() starts.

        Usage:
            token_counter = TokenInstrumentationHooks.on_worker_engine_start(
                turn_id=session.current_turn_id,
                engine="claude-opus-5",
                engine_tier="cloud"
            )
        """
        return TokenCounter(
            turn_id=turn_id,
            engine=engine,
            engine_tier=engine_tier,
            start_time=datetime.utcnow(),
        )

    @staticmethod
    def on_llm_response(counter: TokenCounter, input_tokens: int, output_tokens: int) -> None:
        """Called when LLM returns response (with token counts).

        Usage (in WorkerEngine.run()):
            response = await claude_api.call(prompt)
            token_counter.record_llm_call(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens
            )
        """
        counter.record_llm_call(input_tokens, output_tokens)

    @staticmethod
    def on_subsystem_executed(counter: TokenCounter, subsystem: str, overhead_tokens: int) -> None:
        """Called by subsystems to record their token overhead.

        Usage (in ExecutionContext):
            TokenInstrumentationHooks.on_subsystem_executed(
                counter, "context_load", estimated_overhead_tokens=450
            )

        Usage (in SkillForge):
            TokenInstrumentationHooks.on_subsystem_executed(
                counter, "skill_injection", len(injected_skills) * 100
            )

        Usage (in Confidence):
            if confidence_exit:
                TokenInstrumentationHooks.on_subsystem_executed(
                    counter, "confidence_exit", tokens_saved_from_stopping_early
                )
        """
        counter.record_subsystem_usage(subsystem, overhead_tokens)

    @staticmethod
    def on_worker_engine_end(
        counter: TokenCounter,
        outcome_quality: Optional[str] = None,
        required_followup: bool = False,
    ) -> None:
        """Called when WorkerEngine.run() completes.

        Usage:
            token_counter.finalize()
            TokenInstrumentationHooks.on_worker_engine_end(
                counter,
                outcome_quality="good",
                required_followup=False
            )
        """
        counter.outcome_quality = outcome_quality
        counter.required_followup = required_followup
        counter.finalize()


# Global thread-local storage for current turn's token counter
# (simplified for Phase 1; will be replaced by proper context management in Phase 2)
_current_counter: Optional[TokenCounter] = None


def set_current_token_counter(counter: Optional[TokenCounter]) -> None:
    """Set the current turn's token counter (thread-unsafe, for Phase 1 only)."""
    global _current_counter
    _current_counter = counter


def get_current_token_counter() -> Optional[TokenCounter]:
    """Get the current turn's token counter."""
    return _current_counter
