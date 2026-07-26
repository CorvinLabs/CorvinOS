#!/usr/bin/env python3
"""VOICE-F9 — which summarizer build_voice_summary spawns, and with which argv.

Three regressions, all observed live on 2026-07-26:

1. `--task` was appended OUTSIDE the `if use_smart` branch, so it also went to
   `summarize_smart.py`, which has no such argument. argparse exited 2 on every
   turn that carried a question — i.e. effectively always — and the resulting
   CalledProcessError degraded the voice note to "head of answer", the verbatim
   readout the whole path exists to prevent.
2. `summarize_smart.py` was the PRIMARY summarizer although it bypasses the LLM
   and emits English template scaffolding, breaking the documented contract
   "the spoken voice note is ALWAYS an LLM summary rendered in the profile
   language" (adapter-runtime.md).
3. The error log echoed the summarizer's raw stderr — and argparse quotes the
   offending argv, which carries `--task <the user's question>`. The user's
   prompt landed in corvin.log in clear text.

Run: python3 operator/bridges/shared/test_adapter_voice_summarizer_choice.py
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
os.environ.setdefault("CORVIN_ADAPTER_SANDBOX", "0")

import adapter  # noqa: E402


class SummarizerChoiceTest(unittest.TestCase):
    """Read the source: spawning the real summarizer costs an LLM call."""

    @classmethod
    def setUpClass(cls):
        cls.src = (_HERE / "adapter.py").read_text(encoding="utf-8")
        head = cls.src.split("def build_voice_summary", 1)
        assert len(head) == 2, "build_voice_summary not found"
        # Bound the slice at the next top-level def so we only read this function.
        body = head[1]
        cls.body = body.split("\ndef ", 1)[0]

    def test_llm_summarizer_is_the_primary_choice(self):
        first = self.body.index('SCRIPTS_DIR / "summarize')
        chosen = self.body[first:first + 60]
        self.assertIn('"summarize.py"', chosen,
                      "the LLM summarizer must be the primary path — "
                      "summarize_smart.py bypasses the LLM and answers in "
                      "English templates (adapter-runtime.md contract)")

    def test_smart_summarizer_remains_available_as_fallback(self):
        self.assertIn('summarize_smart.py', self.body,
                      "the rule-based generator must stay reachable for an "
                      "install that ships without summarize.py")

    def test_task_flag_is_scoped_to_the_llm_summarizer(self):
        """`--task` must live in the `else:` (non-smart) branch of `use_smart`.

        Asserted over the AST, not over indentation counts: the first version
        of this test compared indent >= 12 and the reintroduced bug lands on
        exactly 12, so the mutation passed. Structure is the invariant here.
        """
        import ast

        tree = ast.parse((_HERE / "adapter.py").read_text(encoding="utf-8"))
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "build_voice_summary"),
                  None)
        self.assertIsNotNone(fn, "build_voice_summary not found")

        def _appends_task(node) -> bool:
            return any(
                isinstance(c, ast.Constant) and c.value == "--task"
                for c in ast.walk(node)
            )

        # Every place that appends --task …
        holders = [n for n in ast.walk(fn)
                   if isinstance(n, (ast.AugAssign, ast.Expr)) and _appends_task(n)]
        self.assertTrue(holders, "no --task append found at all")

        # … must be reachable ONLY through the else-branch of `if use_smart:`.
        smart_ifs = [n for n in ast.walk(fn)
                     if isinstance(n, ast.If)
                     and isinstance(n.test, ast.Name) and n.test.id == "use_smart"]
        self.assertTrue(smart_ifs, "the `if use_smart:` branch disappeared")

        in_else = {id(n) for sif in smart_ifs
                   for stmt in sif.orelse for n in ast.walk(stmt)}
        in_then = {id(n) for sif in smart_ifs
                   for stmt in sif.body for n in ast.walk(stmt)}

        for h in holders:
            self.assertIn(
                id(h), in_else,
                "--task is appended outside the summarize.py branch — "
                "summarize_smart.py has no such argument and exits 2, which "
                "degrades every voice note with a question to verbatim")
            self.assertNotIn(id(h), in_then,
                             "--task must never reach the smart branch")


class StderrScrubTest(unittest.TestCase):
    """The exact stderr that leaked a user's prompt into corvin.log."""

    LEAK = (
        "usage: summarize_smart.py [-h] [--lang {de,en}] [--max-chars MAX_CHARS]\n"
        "                          [--tone {warm,formal,casual}] [--user-name USER_NAME]\n"
        "summarize_smart.py: error: unrecognized arguments: --task erzeuge mal ein "
        "konzept für ein system wo neue features über eine schnittstelle hinzu "
        "gefügt werden können und unter settings an und ausgeschalten werden können"
    )

    def test_user_text_never_survives(self):
        out = adapter._scrub_cli_stderr(self.LEAK)
        for leaked in ("erzeuge", "konzept", "schnittstelle", "settings an"):
            self.assertNotIn(leaked, out.lower(),
                             f"user text {leaked!r} leaked into the log line")

    def test_diagnostic_value_is_kept(self):
        out = adapter._scrub_cli_stderr(self.LEAK)
        self.assertIn("summarize_smart.py", out, "which script failed must survive")
        self.assertIn("--task", out, "which flag was rejected must survive")
        self.assertIn("error", out)

    def test_empty_and_noise_are_safe(self):
        self.assertEqual(adapter._scrub_cli_stderr(""), "")
        # A bare traceback line quoting the input must be dropped entirely.
        self.assertEqual(
            adapter._scrub_cli_stderr("  some prose that quotes the user  "), "")

    def test_output_is_length_bounded(self):
        long_leak = "summarize.py: error: " + ("--flag " * 500)
        self.assertLessEqual(len(adapter._scrub_cli_stderr(long_leak)), 200)

    def test_the_error_path_actually_calls_the_scrubber(self):
        """Wiring, not just the unit: a scrubber nobody calls scrubs nothing.

        (Mutation-checked: removing the call while keeping the function left
        the unit tests above green.)
        """
        src = (_HERE / "adapter.py").read_text(encoding="utf-8")
        fn = src.split("def build_voice_summary", 1)[1].split("\ndef ", 1)[0]
        handler = fn.split("except (subprocess.CalledProcessError", 1)
        self.assertEqual(len(handler), 2, "the summarizer error handler moved")
        handler_body = handler[1]
        self.assertIn("_scrub_cli_stderr", handler_body,
                      "the summarizer failure path must scrub stderr before "
                      "logging — argparse echoes argv, which carries --task "
                      "<the user's question>")
        self.assertNotIn('getattr(e, "stderr", "") or "").strip()[-', handler_body,
                         "raw stderr slice is back in the log line")
        self.assertNotIn("getattr(e, 'stderr', '') or '').strip()[-", handler_body,
                         "raw stderr slice is back in the log line")


if __name__ == "__main__":
    unittest.main(verbosity=2)
