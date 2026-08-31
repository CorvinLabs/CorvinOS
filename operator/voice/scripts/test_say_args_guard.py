#!/usr/bin/env python3
"""Guard against inverted say.py positional args (found 2026-07-17).

say.py's contract is ``say.py <out_path> <text> [...]``. A caller who swaps
the two silently synthesised audio into a file literally NAMED after the
spoken sentence — the repo root grew Ogg files called "Das ist Test Nummer
1." (trailing dot: Windows-illegal, tooling-hostile). main() now refuses
argv[1] values that carry no known audio extension AND read like a sentence
(whitespace + terminal punctuation), exiting 2 before any provider runs.

Legit callers (routes/voice.py::_say_cmd, daemon.js) always pass
extension-carrying paths — the guard must stay invisible to them.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS))

import say as _say  # noqa: E402


class LooksLikeSwappedArgsTests(unittest.TestCase):
    def test_sentence_shaped_arg_is_flagged(self):
        for text in (
            "Das ist Test Nummer 1.",
            "Testnachricht mit Nova.",
            "Is the build green?",
        ):
            self.assertTrue(_say._looks_like_swapped_args(text), text)

    def test_audio_paths_are_never_flagged(self):
        for path in (
            "/tmp/voice/deadbeef.opus",
            "out.ogg",
            "reply.mp3",
            "~/x.wav",
            # Even a space-carrying DIRECTORY stays legal — the extension
            # short-circuits before the sentence heuristic runs.
            "/tmp/My Recordings/reply number one.opus",
        ):
            self.assertFalse(_say._looks_like_swapped_args(path), path)

    def test_extensionless_but_word_shaped_arg_passes(self):
        """A bare tmp path without extension is unusual but not sentence-
        shaped — must not be rejected (no whitespace / no terminal
        punctuation)."""
        self.assertFalse(_say._looks_like_swapped_args("/tmp/voice-out"))
        self.assertFalse(_say._looks_like_swapped_args("Fertig!"))  # no whitespace

    def test_main_exits_2_and_writes_no_file_on_swapped_args(self):
        """End-to-end: the historical accident must die before synthesis."""
        bad_name = "Das ist Test Nummer 9."
        proc = subprocess.run(
            [sys.executable, str(_SCRIPTS / "say.py"),
             bad_name, "/tmp/never-written.opus"],
            capture_output=True, text=True, timeout=30,
            cwd="/tmp",
        )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("swapped", proc.stderr)
        self.assertIn("usage:", proc.stderr)
        self.assertFalse((Path("/tmp") / bad_name).exists(),
                         "guard fired but the file was still created")


if __name__ == "__main__":
    unittest.main()
