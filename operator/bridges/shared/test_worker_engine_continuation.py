"""Unit tests for worker_engine_continuation module (ADR-0087 M1)."""

from __future__ import annotations

import pytest

from worker_engine_continuation import create_continuation_prompt


def test_claude_code_returns_input_unchanged():
    """Claude Code uses native --resume; checkpoint not prepended."""
    checkpoint = {
        "turn_id": "t_1",
        "system_prompt_summary": "You are helpful",
        "last_message_summary": "What is AI?",
        "tool_results_digest": "[]",
        "conversation_length_tokens": 100,
    }
    user_input = "Continue explaining."
    result = create_continuation_prompt("claude_code", checkpoint, user_input)
    assert result == user_input


def test_codex_prepends_checkpoint():
    """Codex prepends checkpoint summary to input."""
    checkpoint = {
        "turn_id": "t_1",
        "system_prompt_summary": "You are helpful",
        "last_message_summary": "What is AI?",
        "tool_results_digest": "[search: found 3 results]",
        "conversation_length_tokens": 500,
    }
    user_input = "Explain further."
    result = create_continuation_prompt("codex_cli", checkpoint, user_input)

    # Should contain checkpoint summary
    assert "Prior Context" in result
    assert "You are helpful" in result
    assert "What is AI?" in result
    assert "[search: found 3 results]" in result
    assert "500 tokens" in result

    # Should end with user input
    assert result.endswith("Explain further.")


def test_opencode_prepends_checkpoint():
    """OpenCode prepends checkpoint summary (same as Codex)."""
    checkpoint = {
        "turn_id": "t_2",
        "system_prompt_summary": "Assistant",
        "last_message_summary": "Help me code",
        "tool_results_digest": "[bash: success]",
        "conversation_length_tokens": 300,
    }
    user_input = "Next step?"
    result = create_continuation_prompt("opencode_cli", checkpoint, user_input)

    assert "Prior Context" in result
    assert "Assistant" in result
    assert "Help me code" in result
    assert "Next step?" in result


def test_hermes_prepends_checkpoint():
    """Hermes prepends checkpoint summary."""
    checkpoint = {
        "turn_id": "t_3",
        "system_prompt_summary": "Local model",
        "last_message_summary": "Query",
        "tool_results_digest": "[]",
        "conversation_length_tokens": 200,
    }
    user_input = "Continue."
    result = create_continuation_prompt("hermes_engine", checkpoint, user_input)

    assert "Prior Context" in result
    assert "Local model" in result
    assert "Continue." in result


def test_copilot_raises_not_implemented():
    """Copilot raises NotImplementedError (single-turn limitation)."""
    checkpoint = {
        "turn_id": "t_4",
        "system_prompt_summary": "test",
        "last_message_summary": "test",
        "tool_results_digest": "[]",
        "conversation_length_tokens": 100,
    }
    user_input = "Next turn"

    with pytest.raises(NotImplementedError, match="single-turn limitation"):
        create_continuation_prompt("copilot_cli", checkpoint, user_input)


def test_unknown_engine_raises_value_error():
    """Unknown engine_id raises ValueError."""
    checkpoint = {"turn_id": "t_5"}
    user_input = "test"

    with pytest.raises(ValueError, match="Unknown engine_id"):
        create_continuation_prompt("unknown_engine", checkpoint, user_input)


def test_checkpoint_summary_includes_all_fields():
    """Checkpoint summary includes all context fields."""
    checkpoint = {
        "turn_id": "t_6",
        "system_prompt_summary": "You are a Python expert",
        "last_message_summary": "How do I debug?",
        "tool_results_digest": "[debugger: breakpoint set]",
        "conversation_length_tokens": 1200,
    }
    user_input = "Show me the code."
    result = create_continuation_prompt("codex_cli", checkpoint, user_input)

    assert "Python expert" in result
    assert "How do I debug?" in result
    assert "[debugger: breakpoint set]" in result
    assert "1200 tokens" in result
