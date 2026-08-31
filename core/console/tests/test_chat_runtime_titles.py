"""Tests for the web-chat session title flow (ADR-0037 § Iter 3a amendment).

Covers:
  * auto-title heuristic (`_derive_auto_title`) — boundaries + edge cases
  * `rename_session` — set, trim to max length, clear, 404 on unknown sid
  * `create_session` → first-turn auto-title fires only when title is empty
  * Manual rename survives subsequent turns (heuristic does NOT re-fire)
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Make the console package importable when running this file directly.
_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))


class DeriveAutoTitleTests(unittest.TestCase):
    def setUp(self) -> None:
        from corvin_console import chat_runtime  # noqa: WPS433
        self.derive = chat_runtime._derive_auto_title
        self.cap = chat_runtime._AUTO_TITLE_MAX_CHARS

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(self.derive(""), "")
        self.assertEqual(self.derive("   "), "")
        self.assertEqual(self.derive("\n\n\n"), "")

    def test_short_single_line_returned_verbatim_minus_trailing_punct(self) -> None:
        self.assertEqual(self.derive("Hallo Welt"), "Hallo Welt")
        self.assertEqual(self.derive("Hallo Welt."), "Hallo Welt")
        self.assertEqual(self.derive("Frage?"), "Frage")

    def test_internal_whitespace_collapsed(self) -> None:
        self.assertEqual(self.derive("  Hallo    \t  Welt  "), "Hallo Welt")

    def test_long_prompt_word_boundary_trimmed_with_ellipsis(self) -> None:
        prompt = (
            "Wie groß ist die Wahrscheinlichkeit, dass es morgen in Berlin regnet "
            "und übermorgen in Hamburg ebenfalls Niederschlag zu erwarten ist?"
        )
        out = self.derive(prompt)
        self.assertTrue(out.endswith("…"), msg=f"expected ellipsis, got {out!r}")
        self.assertLessEqual(len(out), self.cap + 1)  # +1 for the ellipsis
        # No partial word at the cut point.
        body = out[:-1]
        self.assertFalse(body.endswith(" "))
        # The trimmed body should be a prefix of the collapsed prompt.
        self.assertTrue(prompt.startswith(body.rstrip("…")))

    def test_multiline_picks_first_nonempty_line(self) -> None:
        prompt = "\n\n  Refactor the auth router\n\nDetails below…"
        self.assertEqual(self.derive(prompt), "Refactor the auth router")

    def test_single_long_token_hard_cuts(self) -> None:
        token = "x" * 200
        out = self.derive(token)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(len(out), self.cap + 1)


class RenameSessionTests(unittest.TestCase):
    """Round-trip rename_session against a temp corvin home."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CORVIN_HOME"] = self.tmp.name
        # Re-import to pick up the patched env.
        import importlib
        from corvin_console import chat_runtime
        importlib.reload(chat_runtime)
        # forge.paths caches the home; reload it too if already imported.
        try:
            import forge.paths as fp  # type: ignore[import]
            importlib.reload(fp)
            importlib.reload(chat_runtime)
        except ImportError:
            pass
        self.cr = chat_runtime

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.pop("CORVIN_HOME", None)

    def test_unknown_sid_returns_none(self) -> None:
        self.assertIsNone(self.cr.rename_session("_default", "does-not-exist", "x"))

    def test_set_title_and_reload(self) -> None:
        sess = self.cr.create_session("_default")
        self.assertEqual(sess.title, "")
        renamed = self.cr.rename_session("_default", sess.sid, "  My Project  ")
        self.assertIsNotNone(renamed)
        self.assertEqual(renamed.title, "My Project")
        # And it persists across a fresh read.
        again = self.cr.get_session("_default", sess.sid)
        self.assertIsNotNone(again)
        self.assertEqual(again.title, "My Project")

    def test_clear_title(self) -> None:
        sess = self.cr.create_session("_default", title="initial")
        self.assertEqual(sess.title, "initial")
        self.cr.rename_session("_default", sess.sid, "")
        again = self.cr.get_session("_default", sess.sid)
        self.assertEqual(again.title, "")

    def test_title_capped_at_max(self) -> None:
        sess = self.cr.create_session("_default")
        long = "a" * 500
        renamed = self.cr.rename_session("_default", sess.sid, long)
        self.assertEqual(len(renamed.title), self.cr._TITLE_MAX_CHARS)


class FirstTurnAutoTitleTests(unittest.TestCase):
    """The first-turn auto-title branch is gated by `not resume and not title`.

    Rather than spawning a real claude subprocess we exercise the gate
    directly — that's the only conditional that matters for the contract
    "manual rename survives, empty title gets auto-named once".
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CORVIN_HOME"] = self.tmp.name
        import importlib
        from corvin_console import chat_runtime
        importlib.reload(chat_runtime)
        try:
            import forge.paths as fp  # type: ignore[import]
            importlib.reload(fp)
            importlib.reload(chat_runtime)
        except ImportError:
            pass
        self.cr = chat_runtime

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.pop("CORVIN_HOME", None)

    def _apply_gate(self, sess, prompt: str) -> None:
        """Mirror the conditional from `stream_turn` so the test does not
        depend on subprocess spawn or asyncio plumbing."""
        resume = sess.turn_count > 0
        if not resume and not sess.title.strip():
            auto = self.cr._derive_auto_title(prompt)
            if auto:
                sess.title = auto
                self.cr._save(sess)

    def test_empty_title_gets_auto_named(self) -> None:
        sess = self.cr.create_session("_default")
        self._apply_gate(sess, "Schreib mir eine Python-Funktion für Primzahlen")
        reloaded = self.cr.get_session("_default", sess.sid)
        # Compact 4-word topic label (word-limit contract, see
        # _AUTO_TITLE_WORD_LIMIT) — not the full sentence.
        self.assertEqual(
            reloaded.title,
            "Schreib mir eine Python-Funktion…",
        )

    def test_manual_title_survives(self) -> None:
        sess = self.cr.create_session("_default", title="My Project")
        self._apply_gate(sess, "anything goes here")
        reloaded = self.cr.get_session("_default", sess.sid)
        self.assertEqual(reloaded.title, "My Project")

    def test_does_not_refire_on_later_turns(self) -> None:
        sess = self.cr.create_session("_default")
        self._apply_gate(sess, "First message that becomes the title")
        # Bump turn count as stream_turn would.
        self.cr.touch(sess, increment_turn=True)
        # A second invocation with the gate must be a no-op even with empty
        # title — but the title is no longer empty here so we test both
        # branches: clear title + non-zero turn_count → still no auto-name.
        sess.title = ""
        self.cr._save(sess)
        self._apply_gate(sess, "completely different second prompt")
        reloaded = self.cr.get_session("_default", sess.sid)
        self.assertEqual(reloaded.title, "")


if __name__ == "__main__":
    unittest.main()
