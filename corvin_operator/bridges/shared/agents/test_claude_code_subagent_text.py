"""A sub-agent's own narration is not the answer (2026-11-03, web chat).

The claude CLI streams the messages of a background sub-agent on the same stdout,
marked with ``parent_tool_use_id``. The engine passed their text on as
``text_delta``, so the chat showed the sub-agent's working notes as if the main
model had written them.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from agents.claude_code import ClaudeCodeEngine  # noqa: E402


def _assistant(text: str, parent: str | None, tool: bool = False) -> dict:
    content = [{"type": "text", "text": text}]
    if tool:
        content.append({"type": "tool_use", "id": "toolu_x", "name": "Bash", "input": {"command": "ls"}})
    return {"type": "assistant", "message": {"role": "assistant", "content": content},
            "parent_tool_use_id": parent}


def test_main_model_text_is_streamed():
    events = ClaudeCodeEngine._normalise_all(_assistant("Hallo", None))
    assert [e.type for e in events] == ["text_delta"]


def test_sub_agent_text_is_not_streamed_but_its_tool_call_still_is():
    events = ClaudeCodeEngine._normalise_all(_assistant("Now test_aco ...", "toolu_agent", tool=True))
    assert [e.type for e in events] == ["tool_call"]
