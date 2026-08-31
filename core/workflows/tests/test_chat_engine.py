"""Tests for ADR-0188 M7 — orchestration.engine: chat.

Run directly:  python3 core/workflows/tests/test_chat_engine.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PKG_ROOT = _HERE.parent
sys.path.insert(0, str(_PKG_ROOT))

from corvin_workflows import WorkflowDoc, validate  # noqa: E402
from corvin_workflows.validator import WorkflowInvalid  # noqa: E402


def _doc(graph: list[dict], *, engine: str) -> WorkflowDoc:
    return WorkflowDoc(
        awp_version="1.1.0", name="chat_test", description="test",
        orchestration={"engine": engine, "graph": graph}, raw={},
    )


class ChatEngineValidationTests(unittest.TestCase):
    def test_chat_engine_accepted_by_r4(self) -> None:
        doc = _doc([{
            "id": "reply", "type": "answer", "depends_on": [],
            "channel": "discord", "chat_id": "1", "text": "hi",
        }], engine="chat")
        validate(doc)  # must not raise

    def test_dag_engine_still_accepted(self) -> None:
        doc = _doc([{"id": "n", "type": "agent", "agent": "a", "depends_on": []}], engine="dag")
        validate(doc)

    def test_unknown_engine_rejected(self) -> None:
        doc = _doc([{"id": "n", "type": "agent", "agent": "a", "depends_on": []}], engine="graphql")
        with self.assertRaises(WorkflowInvalid) as ctx:
            validate(doc)
        self.assertEqual(ctx.exception.code, "R4")

    def test_chat_engine_without_turn_node_rejected(self) -> None:
        """A chat-engine workflow with only agent nodes could never pause —
        that's almost certainly an authoring mistake (R11)."""
        doc = _doc([{"id": "n", "type": "agent", "agent": "a", "depends_on": []}], engine="chat")
        with self.assertRaises(WorkflowInvalid) as ctx:
            validate(doc)
        self.assertEqual(ctx.exception.code, "R11")

    def test_chat_engine_with_ask_human_passes_r11(self) -> None:
        doc = _doc([{
            "id": "confirm", "type": "ask_human", "depends_on": [],
            "channel": "discord", "chat_id": "1", "prompt": "?",
        }], engine="chat")
        validate(doc)

    def test_dag_engine_never_triggers_r11(self) -> None:
        """R11 is scoped to engine=='chat' only — a plain dag workflow with
        zero answer/ask_human nodes is completely normal and must pass."""
        doc = _doc([{"id": "n", "type": "agent", "agent": "a", "depends_on": []}], engine="dag")
        validate(doc)  # must not raise


if __name__ == "__main__":
    unittest.main(verbosity=2)
