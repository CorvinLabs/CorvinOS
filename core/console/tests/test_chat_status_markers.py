"""Chat status markers (CONCEPT-0114) reach BOTH prompt builders.

A unit test of ``status_markers`` would only prove the text exists. These tests
go through the two real call sites that put it in front of the model: the
console's ``_turn_system_prompt`` and the bridge adapter's ``system_prompt_for``
(the Discord/Telegram/WhatsApp path), so a surface that stops calling the
helper fails here.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "corvin_operator" / "bridges" / "shared"))

ICONS = ("✅", "❌", "⚠️", "ℹ️", "🔍", "⏳", "💡", "📌")


class StatusMarkerVocabularyTests(unittest.TestCase):
    def test_mapping_has_one_meaning_per_icon(self) -> None:
        import status_markers  # noqa: WPS433

        icons = [i for i, _ in status_markers.STATUS_MARKERS]
        self.assertEqual(tuple(icons), ICONS)
        self.assertEqual(len(set(icons)), len(icons))

    def test_rule_names_every_icon_and_the_hard_limits(self) -> None:
        import status_markers  # noqa: WPS433

        rule = status_markers.status_marker_rule()
        for icon in ICONS:
            self.assertIn(icon, rule)
        self.assertIn("NEVER inside code blocks", rule)
        self.assertIn("commit messages", rule)


class ConsolePromptTests(unittest.TestCase):
    def test_turn_system_prompt_carries_the_rule(self) -> None:
        from corvin_console import chat_runtime  # noqa: WPS433

        with tempfile.TemporaryDirectory() as tmp:
            sess = chat_runtime.WebChatSession(
                sid="s1", tenant_id="_default",
                created_at=0.0, last_active_at=0.0, workdir=Path(tmp),
            )
            prompt = chat_runtime._turn_system_prompt(sess, with_ledger=False)
        self.assertIn("Status markers", prompt)
        for icon in ICONS:
            self.assertIn(icon, prompt)


class BridgePromptTests(unittest.TestCase):
    def test_discord_prompt_carries_the_rule(self) -> None:
        import adapter  # noqa: WPS433

        prompt = adapter.system_prompt_for("discord")
        self.assertIn("Discord", prompt)
        self.assertIn("Status markers", prompt)
        for icon in ICONS:
            self.assertIn(icon, prompt)

    def test_every_channel_gets_the_same_block(self) -> None:
        import adapter  # noqa: WPS433
        import status_markers  # noqa: WPS433

        rule = status_markers.status_marker_rule()
        for channel in ("discord", "telegram", "whatsapp"):
            self.assertIn(rule, adapter.system_prompt_for(channel))


if __name__ == "__main__":
    unittest.main()
