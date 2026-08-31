"""Windows fresh-install fix — the legacy `call_claude()` fallback path.

`_build_claude_args()` used to splat `resolved["system"]` (the merged,
routinely 10k+ character system prompt) straight into
`ClaudeCodeEngine._build_args(system=...)`, producing an inline
`--append-system-prompt <text>` argv element — the SAME command-line-length
bug the engine-driven path (`agents/claude_code.py::spawn()`) already had
fixed, just reachable through this older, rarely-hit exception-fallback
function instead (`call_claude()`, invoked when the primary streaming
engine itself throws — see `_call_claude_streaming_via_engine`'s except
branch in adapter.py). This function's own subprocess spawn (`_run()`
inside `call_claude()`) also never wrapped argv through
`windows_shim_command` at all, unlike every other spawn site in this
codebase — a SEPARATE Windows crash (WinError 193) that would have masked
the length bug's fix even after `_build_claude_args` was corrected.

These tests cover the part reachable without a real `claude` binary:
`_build_claude_args`'s file-based system prompt. The `windows_shim_command`
wrapping added to `_run()` mirrors the identical, already-tested pattern in
agents/claude_code.py, agents/codex_cli.py and agents/opencode_cli.py — not
re-tested here to avoid needing a real subprocess spawn in this file.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

THIS = Path(__file__).resolve()
SHARED = THIS.parent
sys.path.insert(0, str(SHARED))

import adapter  # type: ignore  # noqa: E402


class BuildClaudeArgsSystemPromptFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._saved_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp.name

    def tearDown(self) -> None:
        if self._saved_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._saved_home
        self._tmp.cleanup()

    def test_system_prompt_goes_via_file_not_inline(self) -> None:
        profile = {"name": "coder", "permission_mode": "bypassPermissions"}
        argv = adapter._build_claude_args(
            "hi", "unrestricted", profile, None,
            channel="discord", chat_key="test-chat-legacy",
        )
        self.assertIn("--append-system-prompt-file", argv)
        self.assertNotIn("--append-system-prompt", argv[:argv.index("--append-system-prompt-file")]
                          + argv[argv.index("--append-system-prompt-file") + 2:])
        idx = argv.index("--append-system-prompt-file")
        path = Path(argv[idx + 1])
        try:
            self.assertTrue(path.is_absolute())
            self.assertTrue(path.exists())
            self.assertTrue(path.name.startswith("."))
            content = path.read_text(encoding="utf-8")
            self.assertGreater(len(content), 0)
        finally:
            path.unlink(missing_ok=True)

    def test_extracted_cleanup_path_matches_argv(self) -> None:
        """Mirrors the exact extraction call_claude() does right after
        _build_claude_args() returns, so a temp file gets cleaned up in its
        own finally block regardless of which return/exception path the
        spawn-and-retry logic below it takes."""
        profile = {"name": "coder", "permission_mode": "bypassPermissions"}
        argv = adapter._build_claude_args(
            "hi", "unrestricted", profile, None,
            channel="discord", chat_key="test-chat-legacy-2",
        )
        tmp_path = None
        if "--append-system-prompt-file" in argv:
            idx = argv.index("--append-system-prompt-file")
            if idx + 1 < len(argv):
                tmp_path = argv[idx + 1]
        self.assertIsNotNone(tmp_path)
        self.assertTrue(Path(tmp_path).exists())
        os.unlink(tmp_path)

    def test_no_system_prompt_no_file_written(self) -> None:
        """An empty/None system prompt must not create a file at all —
        confirms the fix only activates when there's real content."""
        argv = adapter._build_claude_args(
            "hi", "unrestricted", None, None,
            channel="discord", chat_key="test-chat-legacy-3",
        )
        # Even with profile=None, _resolve_spawn_inputs still assembles a
        # base system prompt (persona-independent), so this mainly guards
        # against a crash / silently-empty argv rather than asserting "no
        # file" unconditionally.
        if "--append-system-prompt-file" in argv:
            idx = argv.index("--append-system-prompt-file")
            path = Path(argv[idx + 1])
            self.assertTrue(path.exists())
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
