"""Unit tests for Phase 1: Token Instrumentation (K=1).

Tests:
- TokenCounter initialization and basic recording
- Subsystem overhead tracking
- Savings calculation (vs baseline)
- Conversion to LearningEvent
"""

import pytest
from datetime import datetime
from uuid import uuid4

from core.learning.token_instrumentation import (
    TokenCounter,
    TokenInstrumentationHooks,
    set_current_token_counter,
    get_current_token_counter,
)
from core.learning.event_schema import LearningEventType, TokenMetricsPayload


class TestTokenCounter:
    """Test TokenCounter basic functionality."""

    def test_init(self):
        """Test TokenCounter initialization."""
        counter = TokenCounter(
            turn_id="turn_001",
            engine="claude-opus-5",
            engine_tier="cloud",
        )

        assert counter.turn_id == "turn_001"
        assert counter.engine == "claude-opus-5"
        assert counter.engine_tier == "cloud"
        assert counter.input_tokens == 0
        assert counter.output_tokens == 0
        assert counter.total_tokens == 0
        assert counter.subsystem_tokens == {}

    def test_record_llm_call(self):
        """Test recording LLM response tokens."""
        counter = TokenCounter(turn_id="turn_001", engine="claude-opus-5")

        counter.record_llm_call(input_tokens=1200, output_tokens=850)

        assert counter.input_tokens == 1200
        assert counter.output_tokens == 850
        assert counter.total_tokens == 2050

    def test_record_subsystem_usage(self):
        """Test recording subsystem overhead."""
        counter = TokenCounter(turn_id="turn_001", engine="claude-opus-5")

        counter.record_subsystem_usage("confidence", 200)
        counter.record_subsystem_usage("skill_injection", 320)
        counter.record_subsystem_usage("cache", 150)

        assert counter.subsystem_tokens["confidence"] == 200
        assert counter.subsystem_tokens["skill_injection"] == 320
        assert counter.subsystem_tokens["cache"] == 150

    def test_record_subsystem_accumulation(self):
        """Test that subsystem tokens accumulate."""
        counter = TokenCounter(turn_id="turn_001", engine="claude-opus-5")

        counter.record_subsystem_usage("overhead", 100)
        counter.record_subsystem_usage("overhead", 50)  # Same subsystem

        assert counter.subsystem_tokens["overhead"] == 150

    def test_savings_calculation(self):
        """Test savings calculation vs baseline."""
        counter = TokenCounter(turn_id="turn_001", engine="claude-opus-5")
        counter.record_llm_call(input_tokens=1200, output_tokens=800)  # 2000 total

        # Set baseline to 2800 (simulating Native engine)
        counter.baseline_tokens = 2800

        assert counter.savings_tokens == 800
        assert pytest.approx(counter.savings_percent, abs=0.1) == 28.6

    def test_savings_with_no_baseline(self):
        """Test that savings are None when no baseline."""
        counter = TokenCounter(turn_id="turn_001", engine="claude-opus-5")
        counter.record_llm_call(input_tokens=1200, output_tokens=800)

        assert counter.savings_tokens is None
        assert counter.savings_percent is None

    def test_finalize_calculates_latency(self):
        """Test finalize() calculates latency."""
        counter = TokenCounter(turn_id="turn_001", engine="claude-opus-5")
        counter.start_time = datetime.utcnow()

        # Simulate time passing
        import time
        time.sleep(0.1)

        counter.finalize()

        assert counter.end_time is not None
        assert counter.latency_ms is not None
        assert counter.latency_ms >= 100  # At least 100ms

    def test_to_event(self):
        """Test conversion to LearningEvent."""
        counter = TokenCounter(turn_id="turn_001", engine="claude-opus-5")
        counter.record_llm_call(input_tokens=1200, output_tokens=800)
        counter.record_subsystem_usage("confidence", 200)
        counter.baseline_tokens = 2800
        counter.task_type = "code"
        counter.task_domain = "backend"
        counter.outcome_quality = "excellent"
        counter.finalize()

        event = counter.to_event(
            tenant_id="test-tenant",
            instance_id="inst-001",
            session_id="sess-001",
            user_id="user-001",
        )

        assert event.event_type == LearningEventType.TOKEN_METRICS
        assert event.tenant_id == "test-tenant"
        assert event.instance_id == "inst-001"
        assert event.session_id == "sess-001"
        assert event.user_id == "user-001"
        assert "token_metrics" in event.payload

        # Check payload content
        payload_dict = event.payload["token_metrics"]
        assert payload_dict["turn_id"] == "turn_001"
        assert payload_dict["input_tokens"] == 1200
        assert payload_dict["output_tokens"] == 800
        assert payload_dict["total_tokens"] == 2000
        assert payload_dict["savings_tokens"] == 800
        assert payload_dict["task_type"] == "code"
        assert payload_dict["outcome_quality"] == "excellent"


class TestTokenInstrumentationHooks:
    """Test instrumentation hook points."""

    def test_on_worker_engine_start(self):
        """Test creating counter at engine start."""
        counter = TokenInstrumentationHooks.on_worker_engine_start(
            turn_id="turn_123",
            engine="claude-opus-5",
            engine_tier="cloud",
        )

        assert counter.turn_id == "turn_123"
        assert counter.engine == "claude-opus-5"
        assert counter.engine_tier == "cloud"
        assert counter.start_time is not None

    def test_on_llm_response(self):
        """Test recording LLM response."""
        counter = TokenInstrumentationHooks.on_worker_engine_start(
            turn_id="turn_123",
            engine="claude-opus-5",
        )

        TokenInstrumentationHooks.on_llm_response(
            counter,
            input_tokens=1500,
            output_tokens=920,
        )

        assert counter.input_tokens == 1500
        assert counter.output_tokens == 920
        assert counter.total_tokens == 2420

    def test_on_subsystem_executed(self):
        """Test recording subsystem overhead."""
        counter = TokenInstrumentationHooks.on_worker_engine_start(
            turn_id="turn_123",
            engine="claude-opus-5",
        )

        TokenInstrumentationHooks.on_subsystem_executed(
            counter, "skill_injection", 320
        )
        TokenInstrumentationHooks.on_subsystem_executed(
            counter, "context_load", 150
        )

        assert counter.subsystem_tokens["skill_injection"] == 320
        assert counter.subsystem_tokens["context_load"] == 150

    def test_on_worker_engine_end(self):
        """Test finalizing counter at engine end."""
        counter = TokenInstrumentationHooks.on_worker_engine_start(
            turn_id="turn_123",
            engine="claude-opus-5",
        )

        TokenInstrumentationHooks.on_worker_engine_end(
            counter,
            outcome_quality="good",
            required_followup=False,
        )

        assert counter.outcome_quality == "good"
        assert counter.required_followup is False
        assert counter.end_time is not None
        assert counter.latency_ms is not None

    def test_full_instrumentation_flow(self):
        """Test complete instrumentation flow (realistic scenario)."""
        # 1. Engine starts
        counter = TokenInstrumentationHooks.on_worker_engine_start(
            turn_id="turn_realistic",
            engine="claude-opus-5",
            engine_tier="cloud",
        )

        # 2. Subsystems execute and report overhead
        TokenInstrumentationHooks.on_subsystem_executed(counter, "context_load", 200)
        TokenInstrumentationHooks.on_subsystem_executed(
            counter, "skill_injection", 280
        )

        # 3. LLM responds
        TokenInstrumentationHooks.on_llm_response(
            counter, input_tokens=1400, output_tokens=850
        )

        # 4. More subsystems report (e.g., confidence scoring happened)
        TokenInstrumentationHooks.on_subsystem_executed(counter, "confidence", 150)

        # 5. Engine completes
        TokenInstrumentationHooks.on_worker_engine_end(
            counter,
            outcome_quality="excellent",
            required_followup=False,
        )

        # Verify complete state
        assert counter.input_tokens == 1400
        assert counter.output_tokens == 850
        assert counter.total_tokens == 2250
        assert counter.subsystem_tokens["context_load"] == 200
        assert counter.subsystem_tokens["skill_injection"] == 280
        assert counter.subsystem_tokens["confidence"] == 150
        assert counter.outcome_quality == "excellent"
        assert counter.latency_ms is not None

        # Convert to event
        event = counter.to_event(
            tenant_id="test",
            instance_id="inst1",
            session_id="sess1",
        )
        assert event.event_type == LearningEventType.TOKEN_METRICS


class TestTokenCounterGlobals:
    """Test thread-local token counter storage."""

    def test_set_and_get_current_counter(self):
        """Test setting and getting current token counter."""
        counter = TokenCounter(turn_id="turn_global", engine="claude-opus-5")

        set_current_token_counter(counter)
        retrieved = get_current_token_counter()

        assert retrieved is counter
        assert retrieved.turn_id == "turn_global"

    def test_clear_current_counter(self):
        """Test clearing token counter."""
        counter = TokenCounter(turn_id="turn_001", engine="claude-opus-5")
        set_current_token_counter(counter)

        set_current_token_counter(None)
        retrieved = get_current_token_counter()

        assert retrieved is None
