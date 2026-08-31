"""Tests for memory.py — topic lifecycle, index rebuild, prompt formatting."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_SANDBOX = tempfile.mkdtemp(prefix="memory_test_")
os.environ["XDG_CONFIG_HOME"] = _SANDBOX

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import memory as mem  # noqa: E402


class NormaliseTests(unittest.TestCase):
    def test_lowercase_and_strip_extension(self):
        self.assertEqual(mem._normalise_topic("Travel.md"), "travel")
        self.assertEqual(mem._normalise_topic("  CodingStyle  "), "codingstyle")

    def test_spaces_become_hyphens(self):
        self.assertEqual(mem._normalise_topic("work email"), "work-email")

    def test_invalid_chars_rejected(self):
        with self.assertRaises(ValueError):
            mem._normalise_topic("../etc/passwd")
        with self.assertRaises(ValueError):
            mem._normalise_topic("a/b")
        with self.assertRaises(ValueError):
            mem._normalise_topic("")


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        # Wipe the sandbox between tests.
        if mem.MEMORY_DIR.exists():
            for p in mem.MEMORY_DIR.iterdir():
                p.unlink()
            mem.MEMORY_DIR.rmdir()

    def test_empty_listing(self):
        self.assertEqual(mem.list_topics(), [])

    def test_write_and_list(self):
        mem.write_topic("travel", "Lufthansa Senator. Prefers ICE first class.")
        mem.write_topic("coding", "Type hints always. Docstrings rare.")
        self.assertEqual(mem.list_topics(), ["coding", "travel"])

    def test_append(self):
        mem.write_topic("notes", "first")
        mem.write_topic("notes", "second", append=True)
        body = mem.read_topic("notes")
        self.assertIn("first", body)
        self.assertIn("second", body)
        # Append must preserve order.
        self.assertLess(body.index("first"), body.index("second"))

    def test_forget(self):
        mem.write_topic("scratch", "hello")
        self.assertTrue(mem.forget_topic("scratch"))
        self.assertEqual(mem.list_topics(), [])
        # Second forget is a no-op, returns False.
        self.assertFalse(mem.forget_topic("scratch"))

    def test_read_missing_returns_empty(self):
        self.assertEqual(mem.read_topic("does-not-exist"), "")


class IndexTests(unittest.TestCase):
    def setUp(self):
        if mem.MEMORY_DIR.exists():
            for p in mem.MEMORY_DIR.iterdir():
                p.unlink()
            mem.MEMORY_DIR.rmdir()

    def test_index_lists_topics(self):
        mem.write_topic("a", "Alpha summary line")
        mem.write_topic("b", "# Bravo header\n\nBravo summary line.")
        idx = mem.INDEX_FILE.read_text()
        self.assertIn("`a`", idx)
        self.assertIn("`b`", idx)
        self.assertIn("Alpha summary line", idx)
        self.assertIn("Bravo summary line", idx)

    def test_first_line_summary_long_text(self):
        long = "x" * 200
        s = mem.first_line_summary(long, max_chars=20)
        self.assertEqual(len(s), 20)
        self.assertTrue(s.endswith("…"))

    def test_first_line_summary_skips_headings(self):
        body = "# Heading 1\n\nBody line\n"
        self.assertEqual(mem.first_line_summary(body), "Body line")

    def test_first_line_summary_falls_back_to_heading(self):
        body = "# Only a heading\n"
        self.assertEqual(mem.first_line_summary(body), "Only a heading")


class PromptFormatTests(unittest.TestCase):
    def setUp(self):
        if mem.MEMORY_DIR.exists():
            for p in mem.MEMORY_DIR.iterdir():
                p.unlink()
            mem.MEMORY_DIR.rmdir()

    def test_empty_yields_empty_prompt(self):
        self.assertEqual(mem.for_system_prompt(), "")

    def test_with_topics(self):
        mem.write_topic("travel", "Lufthansa Senator")
        out = mem.for_system_prompt()
        self.assertIn("Long-term memory", out)
        self.assertIn("`travel`", out)
        self.assertIn("Lufthansa Senator", out)
        # Must be appendable to a system prompt.
        self.assertTrue(out.startswith("\n\n"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
