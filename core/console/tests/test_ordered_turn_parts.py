"""A persisted assistant turn keeps text and tool cards in ARRIVAL order, so a reload
ends with the closing text exactly like the live stream does."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core/console"))

from corvin_console.chat_runtime import _ordered_turn_parts  # noqa: E402


def _t(text):
    return {"kind": "text", "text": text}


def _tool(name):
    return {"kind": "tool", "name": name, "input": {}}


class OrderedTurnPartsTest(unittest.TestCase):
    def test_closing_text_stays_after_the_tools(self) -> None:
        blocks = [_t("Ich lese zuerst. "), _tool("Read"), _t("Jetzt schreibe ich."),
                  _tool("Bash"), _tool("Bash"), _t("Fertig: alles gruen.\n")]
        got = _ordered_turn_parts(blocks, [b["text"] for b in blocks if b["kind"] == "text"])
        self.assertEqual([p["kind"] for p in got], ["text", "tool", "text", "tool", "tool", "text"])
        self.assertEqual(got[-1]["text"], "Fertig: alles gruen.")

    def test_adjacent_text_blocks_merge_like_the_live_reducer(self) -> None:
        blocks = [_t("a"), _t("b"), _tool("Read"), _t("c")]
        got = _ordered_turn_parts(blocks, ["a", "b", "c"])
        self.assertEqual([p.get("text") for p in got if p["kind"] == "text"], ["ab", "c"])

    def test_annotation_suffix_goes_to_the_end(self) -> None:
        blocks = [_t("Antwort"), _tool("Read")]
        got = _ordered_turn_parts(blocks, ["Antwort"], "Panel gebaut")
        self.assertEqual(got[-1], {"kind": "text", "text": "Panel gebaut"})

    def test_text_added_elsewhere_falls_back_to_the_legacy_layout(self) -> None:
        self.assertIsNone(_ordered_turn_parts([_t("a"), _tool("Read")], ["a", "\n\nextra"]))

    def test_tool_only_turn_has_no_text_part(self) -> None:
        self.assertEqual(_ordered_turn_parts([_tool("Read")], []), [_tool("Read")])


if __name__ == "__main__":
    unittest.main()
