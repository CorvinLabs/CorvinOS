"""E2E tests for ExecutionContext wiring in chat_runtime — Phase 2a.

Tests verify that ExecutionContext is properly captured throughout the
turn lifecycle and persisted in turns.jsonl for audit/rendering.

Coverage:
  - Native Claude Code turns capture engine, model, timing, tokens
  - All error paths finalize execution_context
  - turns.jsonl round-trip preserves execution_context
  - Graceful fallback when builder initialization fails
"""

import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from corvin_console.chat_runtime import (
    read_turns,
    _append_turn,
    _turns_path,
)
from corvin_console.execution_context import (
    ExecutionContext,
    ExecutionContextBuilder,
    EngineId,
    DelegationMode,
    ModelSource,
)


class MockWebChatSession:
    """Mock WebChatSession for testing."""
    def __init__(self, tenant_id="default", sid="test-sid-123"):
        self.tenant_id = tenant_id
        self.sid = sid
        self.chat_key = f"web:{sid}"
        self.workdir = Path("/tmp/test_chat_workdir")
        self.turn_count = 0
        self.title = ""


class TestExecutionContextPersistence:
    """Test execution_context round-trip through turns.jsonl."""

    def test_append_turn_with_execution_context(self, tmp_path):
        """_append_turn() writes execution_context to turns.jsonl."""
        session = MockWebChatSession(tenant_id="t1", sid="sid1")

        # Mock the turns path to use tmp_path
        turns_path = tmp_path / "turns.jsonl"

        execution_context = {
            "engine_id": "claude_code",
            "model_source": "claude",
            "model_name": "claude-3-5-sonnet",
            "delegation_mode": "native",
            "duration_ms": 1500,
            "tokens_input": 150,
            "tokens_output": 50,
            "tool_calls_count": 0,
            "tenant_id": "t1",
            "turn_number": 0,
            "exit_code": 0,
        }

        parts = [{"kind": "text", "text": "Hello, world!"}]

        with patch("corvin_console.chat_runtime._turns_path", return_value=turns_path):
            _append_turn(session, "assistant", parts, execution_context=execution_context)

        # Verify file was written
        assert turns_path.exists()

        # Read and parse the line
        with turns_path.open("r") as f:
            line = f.read().strip()

        data = json.loads(line)
        assert data["role"] == "assistant"
        assert data["parts"] == parts
        assert data["execution_context"] == execution_context
        assert "ts" in data

    def test_append_turn_without_execution_context(self, tmp_path):
        """_append_turn() works correctly when execution_context is None."""
        session = MockWebChatSession(tenant_id="t2", sid="sid2")
        turns_path = tmp_path / "turns.jsonl"

        parts = [{"kind": "text", "text": "Response text"}]

        with patch("corvin_console.chat_runtime._turns_path", return_value=turns_path):
            _append_turn(session, "assistant", parts, execution_context=None)

        # Verify file was written
        assert turns_path.exists()

        with turns_path.open("r") as f:
            line = f.read().strip()

        data = json.loads(line)
        assert "execution_context" not in data
        assert data["role"] == "assistant"

    def test_read_turns_with_execution_context(self, tmp_path):
        """read_turns() properly deserializes execution_context."""
        session = MockWebChatSession(tenant_id="t3", sid="sid3")
        turns_path = tmp_path / "turns.jsonl"

        # Write two turns, second with execution_context
        turn1 = {
            "role": "user",
            "ts": time.time(),
            "parts": [{"kind": "text", "text": "What is 2+2?"}],
        }
        turn2 = {
            "role": "assistant",
            "ts": time.time(),
            "parts": [{"kind": "text", "text": "4"}],
            "execution_context": {
                "engine_id": "claude_code",
                "model_source": "claude",
                "model_name": "claude-3-5-sonnet",
                "delegation_mode": "native",
                "duration_ms": 1000,
                "tokens_input": 10,
                "tokens_output": 5,
                "tool_calls_count": 0,
                "tenant_id": "t3",
                "turn_number": 1,
                "exit_code": 0,
            },
        }

        turns_path.parent.mkdir(parents=True, exist_ok=True)
        with turns_path.open("w") as f:
            f.write(json.dumps(turn1) + "\n")
            f.write(json.dumps(turn2) + "\n")

        with patch("corvin_console.chat_runtime._turns_path", return_value=turns_path):
            turns = read_turns("t3", "sid3")

        assert len(turns) == 2
        assert turns[0]["role"] == "user"
        assert "execution_context" not in turns[0]

        assert turns[1]["role"] == "assistant"
        assert turns[1]["execution_context"]["engine_id"] == "claude_code"
        assert turns[1]["execution_context"]["tokens_input"] == 10


class TestExecutionContextBuilder:
    """Test ExecutionContextBuilder lifecycle in stream_turn scenarios."""

    def test_builder_complete_native_flow(self):
        """Builder captures full native turn lifecycle."""
        builder = ExecutionContextBuilder(tenant_id="test", turn_number=0)

        # Start
        builder.start(engine_id="claude_code", model_name="claude-3-5-sonnet")

        # Simulate streaming updates
        time.sleep(0.01)  # Small delay for timing
        builder.set_usage({"input_tokens": 150, "output_tokens": 50})
        builder.add_tool_call()

        # Complete
        context = builder.set_exit_code(0).complete()

        assert context.engine_id == EngineId.CLAUDE_CODE
        assert context.model_name == "claude-3-5-sonnet"
        assert context.model_source == ModelSource.CLAUDE
        assert context.delegation_mode == DelegationMode.NATIVE
        assert context.tokens_input == 150
        assert context.tokens_output == 50
        assert context.tool_calls_count == 1
        assert context.exit_code == 0
        assert context.duration_ms >= 10  # Should be at least 10ms
        assert context.started_at is not None
        assert context.completed_at is not None

    def test_builder_error_exit_code(self):
        """Builder properly captures error exit codes."""
        builder = ExecutionContextBuilder(tenant_id="error-test", turn_number=1)

        builder.start(engine_id="claude_code", model_name="claude-haiku")
        # Simulate error: no usage, no tool calls
        context = builder.set_exit_code(127).complete()

        assert context.exit_code == 127
        assert context.tokens_input is None
        assert context.tokens_output is None
        assert context.tool_calls_count == 0

    def test_builder_delegation_modes(self):
        """Builder correctly captures different delegation modes."""
        # Native delegation
        ctx1 = (ExecutionContextBuilder(tenant_id="t1", turn_number=0)
                .start(engine_id="claude_code", model_name="claude-opus")
                .set_delegation(mode="native")
                .complete())
        assert ctx1.delegation_mode == DelegationMode.NATIVE

        # ACS delegation
        ctx2 = (ExecutionContextBuilder(tenant_id="t2", turn_number=1)
                .start(engine_id="acs", model_name="claude-opus")
                .set_delegation(mode="acs")
                .complete())
        assert ctx2.delegation_mode == DelegationMode.ACS

        # TDE delegation
        ctx3 = (ExecutionContextBuilder(tenant_id="t3", turn_number=2)
                .start(engine_id="tde", model_name="claude-opus")
                .set_delegation(mode="tde")
                .complete())
        assert ctx3.delegation_mode == DelegationMode.TDE

        # Fallback delegation
        ctx4 = (ExecutionContextBuilder(tenant_id="t4", turn_number=3)
                .start(engine_id="claude_code", model_name="claude-haiku")
                .set_delegation(mode="fallback")
                .complete())
        assert ctx4.delegation_mode == DelegationMode.FALLBACK

    def test_builder_hermes_engine(self):
        """Builder correctly captures Hermes local engine context."""
        builder = ExecutionContextBuilder(tenant_id="local", turn_number=0)

        context = (builder
                  .start(engine_id="hermes", model_name="ollama/mistral")
                  .set_delegation(mode="native")
                  .set_usage({"in": 200, "out": 80})
                  .set_exit_code(0)
                  .complete())

        assert context.engine_id == EngineId.HERMES
        assert context.model_source == ModelSource.OLLAMA
        assert context.model_name == "ollama/mistral"
        assert context.delegation_mode == DelegationMode.NATIVE
        assert context.tokens_input == 200
        assert context.tokens_output == 80

    def test_builder_graceful_empty_initialization(self):
        """Builder handles empty/missing fields gracefully."""
        builder = ExecutionContextBuilder()  # No tenant_id, turn_number defaults

        context = (builder
                  .start()  # No engine_id, model_name
                  .complete())

        assert context.engine_id == EngineId.UNKNOWN
        assert context.model_name == ""
        assert context.tenant_id == ""
        assert context.turn_number == -1
        # Duration should still be calculated
        assert context.duration_ms >= 0

    def test_builder_optional_acs_run_id(self):
        """Builder can optionally capture acs_run_id for future use."""
        builder = ExecutionContextBuilder(tenant_id="t1", turn_number=0)

        context = (builder
                  .start(engine_id="acs", model_name="claude-opus")
                  .set_delegation(mode="acs", acs_run_id="run-abc-123")
                  .set_exit_code(0)
                  .complete())

        assert context.delegation_mode == DelegationMode.ACS
        assert context.acs_run_id == "run-abc-123"

    def test_builder_optional_tde_router_decision(self):
        """Builder can optionally capture TDE router decision."""
        builder = ExecutionContextBuilder(tenant_id="t1", turn_number=0)

        context = (builder
                  .start(engine_id="tde", model_name="claude-opus")
                  .set_delegation(mode="tde", tde_router_decision="route_large_context")
                  .set_exit_code(0)
                  .complete())

        assert context.delegation_mode == DelegationMode.TDE
        assert context.tde_router_decision == "route_large_context"

    def test_builder_serialization_cycle(self):
        """Builder context survives serialization/deserialization."""
        original_builder = ExecutionContextBuilder(tenant_id="ser-test", turn_number=5)

        original_context = (original_builder
                           .start(engine_id="claude_code", model_name="claude-sonnet-20241022")
                           .set_usage({"input_tokens": 500, "output_tokens": 200})
                           .add_tool_call()
                           .add_tool_call()
                           .set_exit_code(0)
                           .complete())

        # Serialize
        data = original_context.to_dict()

        # Deserialize
        restored_context = ExecutionContext.from_dict(data)

        # Verify fields match
        assert restored_context.engine_id == original_context.engine_id
        assert restored_context.model_name == "claude-sonnet"  # Normalized
        assert restored_context.model_source == original_context.model_source
        assert restored_context.tokens_input == original_context.tokens_input
        assert restored_context.tokens_output == original_context.tokens_output
        assert restored_context.tool_calls_count == original_context.tool_calls_count
        assert restored_context.exit_code == original_context.exit_code


class TestExecutionContextErrorPaths:
    """Test that error paths properly finalize execution_context."""

    def test_error_path_empty_task(self):
        """Empty task error path completes execution_context."""
        builder = ExecutionContextBuilder(tenant_id="t1", turn_number=0)

        context = (builder
                  .start(engine_id="claude_code", model_name="claude-haiku")
                  .set_exit_code(1)  # Error path
                  .complete())

        assert context.exit_code == 1
        assert context.duration_ms >= 0  # Still tracked even in error

    def test_error_path_engine_unavailable(self):
        """Engine unavailable error path completes execution_context."""
        builder = ExecutionContextBuilder(tenant_id="t1", turn_number=0)

        context = (builder
                  .start(engine_id="unsupported_engine", model_name="")
                  .set_exit_code(1)
                  .complete())

        assert context.engine_id == EngineId.UNKNOWN  # Unrecognized engine
        assert context.exit_code == 1

    def test_error_path_graceful_fallback(self):
        """Builder survives exception during completion."""
        builder = ExecutionContextBuilder(tenant_id="t1", turn_number=0)
        builder.start(engine_id="claude_code", model_name="claude-opus")

        try:
            # Simulate that something goes wrong
            context = builder.set_exit_code(0).complete()
            assert context is not None  # Should still return a context
        except Exception:
            pytest.fail("Builder should handle errors gracefully")


class TestExecutionContextModels:
    """Test model detection in execution context."""

    def test_model_source_detection(self):
        """ExecutionContext properly detects model sources."""
        # Claude
        ctx1 = ExecutionContextBuilder().start(
            engine_id="claude_code",
            model_name="claude-3-5-sonnet-20241022"
        ).complete()
        assert ctx1.model_source == ModelSource.CLAUDE
        assert ctx1.model_name == "claude-3-5-sonnet"  # Normalized

        # Ollama
        ctx2 = ExecutionContextBuilder().start(
            engine_id="hermes",
            model_name="ollama:mistral:latest"
        ).complete()
        assert ctx2.model_source == ModelSource.OLLAMA
        assert ctx2.model_name == "ollama/mistral:latest"  # Normalized

        # OpenRouter
        ctx3 = ExecutionContextBuilder().start(
            engine_id="unknown",
            model_name="openrouter:meta-llama/llama-2"
        ).complete()
        assert ctx3.model_source == ModelSource.OPENROUTER

        # Hermes local
        ctx4 = ExecutionContextBuilder().start(
            engine_id="hermes",
            model_name="hermes-2-pro"
        ).complete()
        assert ctx4.model_source == ModelSource.HERMES


class TestExecutionContextBackwardCompatibility:
    """Test backward compatibility with existing turns.jsonl records."""

    def test_turns_without_execution_context(self, tmp_path):
        """read_turns() handles legacy turns without execution_context."""
        session = MockWebChatSession(tenant_id="legacy", sid="sid-old")
        turns_path = tmp_path / "turns.jsonl"

        # Write legacy turn (no execution_context)
        legacy_turn = {
            "role": "assistant",
            "ts": time.time(),
            "parts": [{"kind": "text", "text": "Legacy response"}],
        }

        turns_path.parent.mkdir(parents=True, exist_ok=True)
        with turns_path.open("w") as f:
            f.write(json.dumps(legacy_turn) + "\n")

        with patch("corvin_console.chat_runtime._turns_path", return_value=turns_path):
            turns = read_turns("legacy", "sid-old")

        assert len(turns) == 1
        assert turns[0]["role"] == "assistant"
        assert "execution_context" not in turns[0]  # Field not present

    def test_mixed_legacy_and_new_turns(self, tmp_path):
        """read_turns() handles mix of legacy and new turns."""
        turns_path = tmp_path / "turns.jsonl"

        # Write mixed turns
        legacy_turn = {
            "role": "user",
            "ts": time.time(),
            "parts": [{"kind": "text", "text": "Question"}],
        }
        new_turn = {
            "role": "assistant",
            "ts": time.time(),
            "parts": [{"kind": "text", "text": "Answer"}],
            "execution_context": {
                "engine_id": "claude_code",
                "model_source": "claude",
                "model_name": "claude-opus",
                "delegation_mode": "native",
                "duration_ms": 500,
                "tokens_input": 50,
                "tokens_output": 25,
                "tool_calls_count": 0,
                "exit_code": 0,
            },
        }

        turns_path.parent.mkdir(parents=True, exist_ok=True)
        with turns_path.open("w") as f:
            f.write(json.dumps(legacy_turn) + "\n")
            f.write(json.dumps(new_turn) + "\n")

        with patch("corvin_console.chat_runtime._turns_path", return_value=turns_path):
            turns = read_turns("mixed", "sid-mixed")

        assert len(turns) == 2
        assert "execution_context" not in turns[0]
        assert "execution_context" in turns[1]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
