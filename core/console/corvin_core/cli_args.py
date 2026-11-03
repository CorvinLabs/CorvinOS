"""Argument helpers for spawning the ``claude`` CLI on native Windows.

An npm-installed ``claude`` is a ``claude.cmd`` shim, run through ``cmd /c``; cmd.exe ends
the command line at the first line break, so a multi-line argument (system prompt, workflow
YAML) reaches the CLI cut off after its first line. These helpers keep POSIX argv
byte-identical and, on Windows, move such text into a file the CLI reads itself.
"""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile


def system_prompt_args(text: str, flag: str = "--system-prompt") -> tuple[list[str], str | None]:
    """``([flag, text], None)`` on POSIX; on Windows ``([flag + "-file", path], path)`` for a
    multi-line *text*. The caller removes *path* with :func:`cleanup` when the child is done."""
    if sys.platform != "win32" or "\n" not in text:
        return [flag, text], None
    fd, path = tempfile.mkstemp(prefix="corvin-sysprompt-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return [flag + "-file", path], path


def cleanup(path: str | None) -> None:
    if path:
        with contextlib.suppress(OSError):
            os.unlink(path)
