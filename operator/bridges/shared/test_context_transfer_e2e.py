"""E2E test: Context checkpoint transfer across engines (ADR-0087 M1).

Scenario: Claude Code completes a turn → checkpoint saved → Codex spawns next
          with prepended context → verify context flows through.

NOTE: This is an integration test that requires access to task queue and
      engine infrastructure. May be skipped if dependencies unavailable.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

# ADR-0215 F5: the dotted `from operator.bridges.shared import ...` can never
# resolve (stdlib `operator` always wins over the repo's `operator/` dir),
# so HAS_DEPS was always False and both tests below were permanently skipped
# since this file's inception — `core.console...` resolves fine on its own
# (no stdlib `core` module to collide with), it was never the actual blocker.
try:
    _shared_dir = Path(__file__).resolve().parent
    if str(_shared_dir) not in sys.path:
        sys.path.insert(0, str(_shared_dir))
    import context_checkpoint
    import worker_engine_continuation
    from core.console.corvin_console.task_queue import TaskQueueEntry, TaskStatus
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False


@pytest.mark.skipif(not HAS_DEPS, reason="Project dependencies not available")
def test_checkpoint_save_and_continuation():
    """Test full checkpoint lifecycle: save → load → continue."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)

        # Simulate: Claude Code completes turn t_1
        context_checkpoint.save_checkpoint(
            session_dir,
            engine_id="claude_code",
            turn_id="t_1",
            system_prompt_summary="You are a helpful assistant focused on Python coding.",
            last_message_summary="User asked: How do I implement a binary search tree?",
            tool_results_digest="[code_review: found 2 style issues; test_runner: all tests passed]",
            conversation_length_tokens=1250,
        )

        # Next turn: Codex should resume
        loaded = context_checkpoint.load_checkpoint(session_dir)
        assert loaded is not None

        # User provides new input
        new_input = "Can you explain the insert operation?"
        continuation_prompt = worker_engine_continuation.create_continuation_prompt(
            "codex_cli",
            loaded,
            new_input,
        )

        # Verify continuation includes context
        assert "Prior Context" in continuation_prompt
        assert "binary search tree" in continuation_prompt
        assert "insert operation" in continuation_prompt
        assert "1250 tokens" in continuation_prompt


@pytest.mark.skipif(not HAS_DEPS, reason="Project dependencies not available")
def test_checkpoint_per_engine():
    """Test that different engines use checkpoint appropriately."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)

        checkpoint_data = {
            "turn_id": "t_1",
            "system_prompt_summary": "You help with debugging.",
            "last_message_summary": "My code has a memory leak.",
            "tool_results_digest": "[profiler: memory usage at 500MB]",
            "conversation_length_tokens": 800,
        }

        # Claude Code: returns input unchanged (uses --resume)
        prompt_cc = worker_engine_continuation.create_continuation_prompt(
            "claude_code",
            checkpoint_data,
            "What next?",
        )
        assert prompt_cc == "What next?"

        # Codex: prepends checkpoint
        prompt_codex = worker_engine_continuation.create_continuation_prompt(
            "codex_cli",
            checkpoint_data,
            "What next?",
        )
        assert "Prior Context" in prompt_codex
        assert "memory leak" in prompt_codex

        # Hermes: prepends checkpoint
        prompt_hermes = worker_engine_continuation.create_continuation_prompt(
            "hermes_engine",
            checkpoint_data,
            "What next?",
        )
        assert "Prior Context" in prompt_hermes
        assert "debugging" in prompt_hermes

        # Copilot: raises error
        with pytest.raises(NotImplementedError):
            worker_engine_continuation.create_continuation_prompt(
                "copilot_cli",
                checkpoint_data,
                "What next?",
            )


@pytest.mark.skipif(not HAS_DEPS, reason="Project dependencies not available")
def test_taskqueue_entry_with_checkpoint():
    """Test that TaskQueueEntry can store context_checkpoint field."""
    entry = TaskQueueEntry(
        task_id="task_1",
        tenant_id="_default",
        chat_key="web:session_123",
        instruction="Implement quicksort",
        status=TaskStatus.COMPLETED,
        created_at=1234567890.0,
        started_at=1234567900.0,
        ended_at=1234567950.0,
        exit_code=0,
        context_checkpoint={
            "turn_id": "t_1",
            "system_prompt_summary": "You are helpful",
            "last_message_summary": "Implement quicksort",
            "tool_results_digest": "[code_gen: success]",
            "conversation_length_tokens": 500,
        },
    )

    # Verify checkpoint is stored
    assert entry.context_checkpoint is not None
    assert entry.context_checkpoint["turn_id"] == "t_1"

    # Verify to_dict includes checkpoint
    d = entry.to_dict()
    assert "context_checkpoint" in d
    assert d["context_checkpoint"]["system_prompt_summary"] == "You are helpful"


if __name__ == "__main__":
    # Quick sanity check without pytest
    print("✅ E2E test suite loaded (run with pytest for full execution)")
