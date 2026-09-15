"""
ADR-0087 M7 Tests — Copilot Multi-Turn Wrapper

Tier-1: Config validation, checkpoint serialization
Tier-2: Checkpoint injection, sequential execution, error handling
Tier-3: Full E2E with mock Copilot spawns
"""

import pytest
from agents.copilot_task_wrapper import (
    CopilotTaskWrapper,
    CheckpointSaver,
    PromptPrepender,
    TurnCheckpoint,
)


# ============================================================================
# TIER-1: Config & Serialization
# ============================================================================

class TestCheckpointSaverTier1:
    """Checkpoint serialization validation."""

    def test_checkpoint_structure(self):
        """
        Given: TurnCheckpoint dataclass
        When: instantiated with values
        Then: all fields present and correct type
        """
        cp = TurnCheckpoint(
            turn_number=1,
            prompt_summary="Test prompt",
            response_summary="Test response",
            tool_calls_summary="Test tool",
            token_count=100,
            timestamp=1234567890,
        )
        assert cp.turn_number == 1
        assert cp.prompt_summary == "Test prompt"
        assert cp.response_summary == "Test response"
        assert cp.tool_calls_summary == "Test tool"
        assert cp.token_count == 100
        assert cp.timestamp == 1234567890

    def test_checkpoint_max_size_warning(self, tmp_path, caplog):
        """
        Given: checkpoint size approaching 100 KB
        When: save_checkpoint() called
        Then: warning logged but checkpoint saved
        """
        saver = CheckpointSaver()
        large_prompt = "x" * (60 * 1024)  # 60 KB
        large_response = "y" * (50 * 1024)  # 50 KB
        # Total: 110 KB > 100 KB

        cp = saver.save_checkpoint(
            session_dir=str(tmp_path),
            turn_number=1,
            prompt=large_prompt,
            response=large_response,
        )

        assert cp is not None
        assert "token creep warning" in caplog.text.lower()


class TestPromptPrependerTier1:
    """Context prepending format validation."""

    def test_prepend_context_format(self):
        """
        Given: checkpoint from prior turn, new prompt
        When: prepend_context() called
        Then: returns formatted string with [Context] header
        """
        prepender = PromptPrepender()
        cp = TurnCheckpoint(
            turn_number=1,
            prompt_summary="User asked for a function",
            response_summary="Here is a function...",
            tool_calls_summary="",
            token_count=100,
            timestamp=1234567890,
        )

        result = prepender.prepend_context(cp, "Now add error handling")

        assert "[Context from turn 1]" in result
        assert "User asked for a function" in result
        assert "Here is a function..." in result
        assert "[Next turn]" in result
        assert "Now add error handling" in result


# ============================================================================
# TIER-2: Sequential Execution & Error Handling
# ============================================================================

class TestCopilotTaskWrapperTier2:
    """Sequential multi-turn wrapper logic."""

    def test_execute_sequential_single_turn(self, tmp_path):
        """
        Given: initial_prompt, no follow_ups
        When: execute_sequential() called
        Then: returns single turn response
        """
        wrapper = CopilotTaskWrapper(str(tmp_path))

        def mock_spawn(prompt):
            return "Response to: " + prompt

        result = wrapper.execute_sequential(
            initial_prompt="Hello",
            follow_ups=[],
            spawn_fn=mock_spawn,
        )

        assert result["status"] == "success"
        assert len(result["turns"]) == 1
        assert "Hello" in result["final_response"]

    def test_execute_sequential_two_turns(self, tmp_path):
        """
        Given: initial_prompt + one follow_up
        When: execute_sequential() called with mock spawn
        Then: executes two turns, checkpoints between them
        """
        wrapper = CopilotTaskWrapper(str(tmp_path))

        def mock_spawn(prompt):
            return "Response to: " + prompt

        result = wrapper.execute_sequential(
            initial_prompt="First prompt",
            follow_ups=["Second prompt"],
            spawn_fn=mock_spawn,
        )

        assert result["status"] == "success"
        assert len(result["turns"]) == 2
        assert result["turns"][0]["prompt"] == "First prompt"
        assert "First prompt" in result["turns"][1]["prompt"]  # Context prepended

    def test_checkpoint_persisted_between_turns(self, tmp_path):
        """
        Given: two sequential turns
        When: checkpoint saved after turn 1
        Then: checkpoint file exists and is loadable for turn 2
        """
        wrapper = CopilotTaskWrapper(str(tmp_path))
        saver = CheckpointSaver()

        cp = saver.save_checkpoint(
            session_dir=str(tmp_path),
            turn_number=1,
            prompt="Test prompt",
            response="Test response",
        )

        loaded = saver.load_checkpoint(str(tmp_path), turn_number=1)

        assert loaded is not None
        assert loaded.turn_number == 1
        assert loaded.response_summary == "Test response"

    def test_execute_sequential_error_handling(self, tmp_path):
        """
        Given: spawn_fn raises exception on turn 2
        When: execute_sequential() called
        Then: returns status="error" with error message
        """
        wrapper = CopilotTaskWrapper(str(tmp_path))
        call_count = [0]

        def mock_spawn_with_error(prompt):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("Spawn failed")
            return "Response"

        result = wrapper.execute_sequential(
            initial_prompt="First",
            follow_ups=["Second"],
            spawn_fn=mock_spawn_with_error,
        )

        assert result["status"] == "error"
        assert len(result["turns"]) == 1

    def test_max_turns_exceeded_warning(self, tmp_path, caplog):
        """
        Given: 15 follow_ups (> WARN_TURN_THRESHOLD)
        When: execute_sequential() called
        Then: warning logged about token creep
        """
        wrapper = CopilotTaskWrapper(str(tmp_path))

        def mock_spawn(prompt):
            return "Response"

        result = wrapper.execute_sequential(
            initial_prompt="First",
            follow_ups=["Turn " + str(i) for i in range(2, 16)],  # 15 total turns
            spawn_fn=mock_spawn,
        )

        assert result["status"] == "success"
        assert "exceeds threshold" in caplog.text.lower()


# ============================================================================
# TIER-3: E2E with Mock Copilot
# ============================================================================

class TestCopilotTaskWrapperE2E:
    """End-to-end multi-turn execution (mock Copilot)."""

    def test_e2e_full_three_turn_conversation(self, tmp_path):
        """
        Tier-3: Complete three-turn conversation.
        Given: initial prompt + 2 follow_ups, mock Copilot spawn
        When: execute_sequential() called
        Then: all turns executed, checkpoints passed, final response returned
        """
        wrapper = CopilotTaskWrapper(str(tmp_path))

        # Mock Copilot: return realistic responses
        turn_responses = {
            1: "Here's a Python function:\n\ndef greet(name):\n    return f'Hello, {name}!'",
            2: "Added error handling:\n\ndef greet(name):\n    if not name:\n        raise ValueError('Name cannot be empty')\n    return f'Hello, {name}!'",
            3: "Made it async:\n\nasync def greet(name):\n    if not name:\n        raise ValueError('Name cannot be empty')\n    return f'Hello, {name}!'",
        }

        turn_count = [0]

        def mock_copilot(prompt):
            turn_count[0] += 1
            return turn_responses.get(turn_count[0], "Error")

        result = wrapper.execute_sequential(
            initial_prompt="Write a greeting function",
            follow_ups=["Add error handling", "Make it async"],
            spawn_fn=mock_copilot,
        )

        # Verify all turns executed
        assert result["status"] == "success"
        assert len(result["turns"]) == 3

        # Verify turn 1
        assert "greeting function" in result["turns"][0]["prompt"].lower()
        assert "Hello" in result["turns"][0]["response"]

        # Verify turn 2 (should have context from turn 1)
        assert "Context from turn 1" in result["turns"][1]["prompt"]
        assert "error handling" in result["turns"][1]["prompt"].lower()
        assert "ValueError" in result["turns"][1]["response"]

        # Verify turn 3 (should have context from turn 2)
        assert "Context from turn 2" in result["turns"][2]["prompt"]
        assert "async" in result["turns"][2]["response"]

        # Verify final response
        assert "async def greet" in result["final_response"]

    def test_e2e_checkpoint_context_flow(self, tmp_path):
        """
        Tier-3: Verify context flows correctly across turns.
        Given: Turn 1 response mentions "function", Turn 2 asks for "error handling"
        When: checkpoint prepended to Turn 2 prompt
        Then: mock Copilot sees Turn 1 context in its input
        """
        wrapper = CopilotTaskWrapper(str(tmp_path))

        # Track what Copilot receives
        received_prompts = []

        def mock_copilot(prompt):
            received_prompts.append(prompt)
            if len(received_prompts) == 1:
                return "Function implementation: def process(data): return data"
            elif len(received_prompts) == 2:
                return "Added caching: @cache\ndef process(data): return data"
            return ""

        result = wrapper.execute_sequential(
            initial_prompt="Create a data processor",
            follow_ups=["Add caching"],
            spawn_fn=mock_copilot,
        )

        # Verify context was prepended to turn 2
        assert len(received_prompts) == 2

        # Turn 1: original prompt, no context
        assert "Create a data processor" in received_prompts[0]
        assert "[Context from turn 1]" not in received_prompts[0]

        # Turn 2: has context from turn 1
        assert "[Context from turn 1]" in received_prompts[1]
        assert "User said: Create a data processor" in received_prompts[1]
        assert "I responded: Function implementation" in received_prompts[1]
        assert "[Next turn]" in received_prompts[1]
        assert "Add caching" in received_prompts[1]
