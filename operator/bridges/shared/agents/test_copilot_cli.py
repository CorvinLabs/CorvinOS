"""Tests for CopilotCliEngine (L22 WorkerEngine wrapping GitHub Copilot CLI).

Structure mirrors test_hermes_engine.py:
  - Fast unit tests (always run): protocol conformance, capabilities,
    AST lint for anthropic import, error handling, output parsing.
  - Live tests (skip when CORVIN_AGENTS_SKIP_LIVE=1 OR copilot binary absent):
    real subprocess round-trip with `copilot -p`.

The live tests require:
  - `copilot` binary in PATH (or CORVIN_COPILOT_BIN set)
  - A valid GitHub Copilot subscription authenticated via `copilot auth login`
    or GH_TOKEN / GITHUB_TOKEN env var.

Run:
    python3 operator/bridges/shared/agents/test_copilot_cli.py

Skip live tests:
    CORVIN_AGENTS_SKIP_LIVE=1 python3 operator/bridges/shared/agents/test_copilot_cli.py
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent
sys.path.insert(0, str(SHARED))

from agents import StreamEvent, WorkerEngine, collect  # noqa: E402
from agents.copilot_cli import (  # noqa: E402
    CopilotCliEngine,
    COPILOT_TASK_PREFIXES,
    COPILOT_TASK_TYPES,
    _strip_footer,
)


SKIP_LIVE = (
    os.environ.get("CORVIN_AGENTS_SKIP_LIVE") == "1"
    or os.environ.get("CORVIN_AGENTS_SKIP_LIVE") == "1"
)

_COPILOT_BIN = os.environ.get("CORVIN_COPILOT_BIN", "copilot")


def _copilot_available() -> bool:
    """True when the copilot binary is in PATH and responds to --version."""
    bin_path = shutil.which(_COPILOT_BIN)
    if not bin_path:
        return False
    try:
        result = subprocess.run(
            [bin_path, "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Fast unit tests (always run)
# ---------------------------------------------------------------------------


class ProtocolContractTests(unittest.TestCase):

    def test_copilot_engine_satisfies_protocol(self) -> None:
        engine = CopilotCliEngine()
        self.assertIsInstance(engine, WorkerEngine)
        self.assertEqual(engine.name, "copilot")

    def test_capabilities_declared_correctly(self) -> None:
        caps = CopilotCliEngine.capabilities
        self.assertFalse(caps["mid_stream_inject"])
        self.assertFalse(caps["hooks"])
        self.assertFalse(caps["skills_tool"])
        self.assertFalse(caps["mcp"])
        self.assertFalse(caps["stream_json"])
        self.assertFalse(caps["add_system_prompt"])
        self.assertFalse(caps["session_pinning"])
        self.assertIn("read_only", caps["permission_modes"])
        self.assertIn("shell", caps["task_types"])
        self.assertIn("git", caps["task_types"])
        self.assertIn("gh", caps["task_types"])

    def test_task_type_validation_in_init(self) -> None:
        for tt in ("shell", "git", "gh"):
            e = CopilotCliEngine(task_type=tt)
            self.assertEqual(e.task_type, tt)

    def test_unknown_task_type_falls_to_none(self) -> None:
        e = CopilotCliEngine(task_type="invalid")
        self.assertIsNone(e.task_type)

    def test_default_task_type_is_none(self) -> None:
        e = CopilotCliEngine()
        self.assertIsNone(e.task_type)

    def test_timeout_clamped_to_minimum(self) -> None:
        e = CopilotCliEngine(timeout_s=1)
        self.assertEqual(e.timeout_s, 10)

    def test_timeout_clamped_to_maximum(self) -> None:
        e = CopilotCliEngine(timeout_s=9999)
        self.assertEqual(e.timeout_s, 300)

    def test_binary_not_found_yields_error_not_raises(self) -> None:
        engine = CopilotCliEngine()
        with patch.dict(os.environ, {"CORVIN_COPILOT_BIN": "/nonexistent/copilot"}):
            events = list(engine.spawn("hello"))
        self.assertGreater(len(events), 0)
        # session_started fires first, then error
        types = [ev.type for ev in events]
        self.assertIn("error", types)

    def test_error_event_carries_message(self) -> None:
        engine = CopilotCliEngine()
        with patch.dict(os.environ, {"CORVIN_COPILOT_BIN": "/nonexistent/copilot"}):
            events = list(engine.spawn("hello"))
        err_events = [ev for ev in events if ev.type == "error"]
        self.assertTrue(err_events)
        self.assertTrue(all(isinstance(ev.error, str) and ev.error for ev in err_events))

    def test_cancel_before_spawn_does_not_raise(self) -> None:
        engine = CopilotCliEngine()
        engine.cancel()  # Must not raise

    def test_does_not_import_anthropic(self) -> None:
        source = (HERE / "copilot_cli.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(
                        "anthropic", alias.name or "",
                        msg="copilot_cli.py MUST NOT import anthropic",
                    )
            elif isinstance(node, ast.ImportFrom):
                self.assertNotIn(
                    "anthropic", node.module or "",
                    msg="copilot_cli.py MUST NOT import anthropic",
                )

    def test_task_type_map_completeness(self) -> None:
        for tt in COPILOT_TASK_TYPES:
            self.assertIn(tt, COPILOT_TASK_PREFIXES)
            prefix = COPILOT_TASK_PREFIXES[tt]
            self.assertIsInstance(prefix, str)
            self.assertTrue(prefix.strip())

    def test_spawn_model_overrides_task_type(self) -> None:
        """When model="shell" is passed to spawn(), it overrides the
        constructor-level task_type. We confirm via the session_started
        event which carries the resolved task_type."""
        engine = CopilotCliEngine(task_type="git")
        with patch.dict(os.environ, {"CORVIN_COPILOT_BIN": "/nonexistent/copilot"}):
            events = list(engine.spawn("test", model="shell"))
        started = [ev for ev in events if ev.type == "session_started"]
        if started:
            self.assertEqual(started[0].raw.get("task_type"), "shell")

    def test_spawn_model_none_uses_constructor_type(self) -> None:
        engine = CopilotCliEngine(task_type="git")
        with patch.dict(os.environ, {"CORVIN_COPILOT_BIN": "/nonexistent/copilot"}):
            events = list(engine.spawn("test", model=None))
        started = [ev for ev in events if ev.type == "session_started"]
        if started:
            self.assertEqual(started[0].raw.get("task_type"), "git")

    def test_working_dir_is_passed_through_as_subprocess_cwd(self) -> None:
        """Adversarial review finding: `working_dir` was a recognised
        parameter but was never passed to subprocess.Popen — delegation.py's
        hermetic 0700 tempdir promise silently never reached this engine."""
        engine = CopilotCliEngine()
        fake_proc = MagicMock()
        fake_proc.stdout.readline.return_value = b""
        fake_proc.stderr.read.return_value = b""
        fake_proc.wait.return_value = 0
        fake_proc.returncode = 0
        with patch("agents.copilot_cli.subprocess.Popen", return_value=fake_proc) as popen:
            list(engine.spawn("test", working_dir=Path("/tmp/some-hermetic-dir")))
        self.assertEqual(popen.call_args.kwargs.get("cwd"), "/tmp/some-hermetic-dir")

    def test_no_working_dir_passes_cwd_none(self) -> None:
        engine = CopilotCliEngine()
        fake_proc = MagicMock()
        fake_proc.stdout.readline.return_value = b""
        fake_proc.stderr.read.return_value = b""
        fake_proc.wait.return_value = 0
        fake_proc.returncode = 0
        with patch("agents.copilot_cli.subprocess.Popen", return_value=fake_proc) as popen:
            list(engine.spawn("test"))
        self.assertIsNone(popen.call_args.kwargs.get("cwd"))


class FooterStrippingTests(unittest.TestCase):

    def test_footer_stripped_correctly(self) -> None:
        raw = "`docker ps`\n\n\n\nChanges    +0 -0\nRequests   1 Premium (4s)\nTokens     ↑ 28.7k ↓ 7\n"
        self.assertEqual(_strip_footer(raw), "`docker ps`")

    def test_no_footer_passes_through_stripped(self) -> None:
        raw = "Some answer without footer"
        self.assertEqual(_strip_footer(raw), "Some answer without footer")

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(_strip_footer(""), "")

    def test_footer_with_two_newlines(self) -> None:
        raw = "answer\n\nChanges    +0 -0\nRequests   1\n"
        self.assertEqual(_strip_footer(raw), "answer")

    def test_trailing_whitespace_stripped(self) -> None:
        raw = "  result  \n\n\nChanges    +0 -0\n"
        self.assertEqual(_strip_footer(raw), "result")

    def test_multi_line_answer_preserved(self) -> None:
        raw = "line one\nline two\n\n\nChanges    +0 -0\n"
        self.assertEqual(_strip_footer(raw), "line one\nline two")


# ---------------------------------------------------------------------------
# Live tests (require copilot binary + subscription)
# ---------------------------------------------------------------------------


@unittest.skipIf(SKIP_LIVE, "CORVIN_AGENTS_SKIP_LIVE=1")
@unittest.skipUnless(_copilot_available(), f"`{_COPILOT_BIN}` not in PATH or not working")
class LiveCopilotEngineTests(unittest.TestCase):

    def setUp(self) -> None:
        self.engine = CopilotCliEngine(timeout_s=60)

    def test_basic_response(self) -> None:
        """Copilot should respond to a simple factual prompt."""
        result = collect(self.engine.spawn("Reply with exactly the single word: pong"))
        self.assertIsNone(result.error, f"expected no error, got: {result.error}")
        self.assertTrue(result.final_text.strip(), "expected non-empty response")

    def test_shell_task_type_response(self) -> None:
        """Shell task type should return a command, not prose."""
        result = collect(self.engine.spawn(
            "list all running processes",
            model="shell",
        ))
        self.assertIsNone(result.error, f"shell spawn error: {result.error}")
        text = result.final_text.strip()
        self.assertTrue(text, "expected non-empty shell command")
        # Should contain a typical process-listing command
        self.assertTrue(
            any(cmd in text for cmd in ("ps", "top", "htop", "pgrep")),
            f"expected process-listing command in: {text!r}",
        )

    def test_git_task_type_response(self) -> None:
        """Git task type should return a git command."""
        result = collect(self.engine.spawn(
            "undo the last commit without losing changes",
            model="git",
        ))
        self.assertIsNone(result.error, f"git spawn error: {result.error}")
        text = result.final_text.strip()
        self.assertTrue(text, "expected non-empty git command")
        self.assertIn("git", text, f"expected 'git' in response: {text!r}")

    def test_events_have_correct_shape(self) -> None:
        events = list(self.engine.spawn("Reply with: yes"))
        types = [ev.type for ev in events]
        self.assertIn("session_started", types)
        self.assertIn("text_delta", types)
        self.assertIn("turn_completed", types)

    def test_session_started_carries_task_type(self) -> None:
        events = list(self.engine.spawn("hello", model="shell"))
        started = [ev for ev in events if ev.type == "session_started"]
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0].raw.get("task_type"), "shell")

    def test_turn_completed_carries_same_text_as_delta(self) -> None:
        events = list(self.engine.spawn("Reply with: hello"))
        deltas = [ev.text for ev in events if ev.type == "text_delta"]
        completed = [ev.text for ev in events if ev.type == "turn_completed"]
        self.assertEqual(len(completed), 1)
        if deltas:
            self.assertEqual(deltas[0], completed[0])

    def test_footer_not_in_final_text(self) -> None:
        result = collect(self.engine.spawn("Reply with: yes"))
        self.assertIsNone(result.error)
        self.assertNotIn("Changes    ", result.final_text)
        self.assertNotIn("Requests   ", result.final_text)
        self.assertNotIn("Tokens     ", result.final_text)

    def test_chat_mode_no_task_type(self) -> None:
        """No task type = general chat; response should be prose."""
        result = collect(self.engine.spawn(
            "What programming language was Python named after? "
            "Reply in one sentence."
        ))
        self.assertIsNone(result.error)
        text = result.final_text.strip()
        self.assertTrue(text, "expected non-empty chat response")

    def test_spawn_timing_within_budget(self) -> None:
        start = time.monotonic()
        result = collect(self.engine.spawn("Reply with: pong"))
        elapsed = time.monotonic() - start
        self.assertIsNone(result.error)
        self.assertLess(elapsed, 90.0, "expected response within 90s")


if __name__ == "__main__":
    unittest.main()
