"""Tests for the web-chat permission argv (fresh-install permission-hang fix).

The web console has NO interactive permission-prompt UI, so the ``claude -p``
invocation it builds must not run in the CLI's default (interactive) permission
mode — otherwise every tool call that needs approval hangs / is auto-denied,
even for files inside the session's own working directory (the Windows
fresh-install bug).

These tests lock in that ``_build_args``:
  * skips permission prompts by default (parity with the bridge/task-worker),
  * always registers the session workdir as an allowed ``--add-dir`` directory
    (so the Bash/PowerShell working-dir sandbox agrees with the file tools),
  * honours an explicit ``spec.web_chat.permission_mode`` opt-in, and
  * inherits ``spec.web_chat.workspace_roots`` as extra ``--add-dir`` roots.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))


def _session(workdir: Path):
    from corvin_console import chat_runtime  # noqa: WPS433
    return chat_runtime.WebChatSession(
        sid="s1", tenant_id="_default",
        created_at=0.0, last_active_at=0.0, workdir=workdir,
    )


def _add_dirs(args: list[str]) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args) if a == "--add-dir"]


class PermissionFlagTests(unittest.TestCase):
    def setUp(self) -> None:
        from corvin_console import chat_runtime  # noqa: WPS433
        self.cr = chat_runtime
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tmp.name)
        self.sess = _session(self.workdir)
        # Isolate from any on-disk tenant.corvin.yaml.
        self._orig_spec = chat_runtime._tenant_spec
        self._spec: dict = {}
        chat_runtime._tenant_spec = lambda _tid: self._spec  # type: ignore[assignment]

    def tearDown(self) -> None:
        self.cr._tenant_spec = self._orig_spec  # type: ignore[assignment]
        self._tmp.cleanup()

    def test_default_skips_permissions(self) -> None:
        args = self.cr._build_args(self.sess, resume=False)
        self.assertIn("--dangerously-skip-permissions", args)
        self.assertNotIn("--permission-mode", args)

    def test_default_registers_session_workdir(self) -> None:
        args = self.cr._build_args(self.sess, resume=False)
        self.assertIn(str(self.workdir), _add_dirs(args))

    def test_explicit_permission_mode_opt_in(self) -> None:
        self._spec = {"web_chat": {"permission_mode": "acceptEdits"}}
        args = self.cr._build_args(self.sess, resume=False)
        self.assertNotIn("--dangerously-skip-permissions", args)
        idx = args.index("--permission-mode")
        self.assertEqual(args[idx + 1], "acceptEdits")
        # session cwd is still whitelisted even under a strict mode
        self.assertIn(str(self.workdir), _add_dirs(args))

    def test_bypass_permissions_maps_to_skip(self) -> None:
        self._spec = {"web_chat": {"permission_mode": "bypassPermissions"}}
        args = self.cr._build_args(self.sess, resume=False)
        self.assertIn("--dangerously-skip-permissions", args)
        self.assertNotIn("--permission-mode", args)

    def test_invalid_permission_mode_falls_back_to_skip(self) -> None:
        self._spec = {"web_chat": {"permission_mode": "nonsense"}}
        args = self.cr._build_args(self.sess, resume=False)
        self.assertIn("--dangerously-skip-permissions", args)

    def test_workspace_roots_added_as_dirs(self) -> None:
        self._spec = {"web_chat": {"workspace_roots": ["/tmp/projects", "/tmp/data"]}}
        args = self.cr._build_args(self.sess, resume=False)
        dirs = _add_dirs(args)
        self.assertIn("/tmp/projects", dirs)
        self.assertIn("/tmp/data", dirs)
        self.assertIn(str(self.workdir), dirs)

    def test_workspace_roots_accepts_single_string(self) -> None:
        self._spec = {"web_chat": {"workspace_roots": "/tmp/projects"}}
        args = self.cr._build_args(self.sess, resume=False)
        self.assertIn("/tmp/projects", _add_dirs(args))

    def test_additional_dirs_alias(self) -> None:
        self._spec = {"web_chat": {"additional_dirs": ["/tmp/alt"]}}
        args = self.cr._build_args(self.sess, resume=False)
        self.assertIn("/tmp/alt", _add_dirs(args))

    def test_no_cmd_c_prefix_in_argv(self) -> None:
        # BatBadBut RCE fix: _build_args must NOT prepend a `cmd /c` wrapper to
        # the argv. The untrusted --append-system-prompt content must never
        # reach cmd.exe's re-parser as a list2cmdline-quoted argv element; the
        # spawn site wraps a Windows .cmd shim through _win_shim quoting and
        # create_subprocess_shell instead.
        args = self.cr._build_args(self.sess, resume=False)
        self.assertNotIn("cmd", args[:1])
        self.assertNotIn("/c", args)

    def test_win_shim_line_neutralises_breakout_payload(self) -> None:
        # The exact expression the spawn site uses for a .cmd shim must quote a
        # cmd metachar breakout payload so cmd stays in quoted mode across the
        # whole token: the token is wrapped in outer quotes and every inner `"`
        # is DOUBLED (`""`) — cmd's literal-quote-inside-quotes form — so the
        # `&` can never act as a command separator.
        from agents._win_shim import cmd_quote  # noqa: WPS433
        payload = 'hello" & powershell -enc AAAA & "world'
        quoted = cmd_quote(payload)
        # Contract: outer-quoted, and each original `"` became `""`.
        self.assertTrue(quoted.startswith('"') and quoted.endswith('"'))
        self.assertEqual(quoted, '"' + payload.replace('"', '""') + '"')
        # No lone (undoubled) inner quote survives → cmd never leaves quoted mode.
        inner = quoted[1:-1]
        self.assertNotIn('"', inner.replace('""', ''))


if __name__ == "__main__":
    unittest.main()
