"""Windows ``.cmd``/``.bat`` shim spawning — cmd.exe-safe argument quoting.

npm ships CLI tools (``claude``, ``codex``, ``opencode``, ``copilot``) as
``*.cmd`` shims, which ``CreateProcess`` cannot launch directly (WinError 193),
so they must run through ``cmd /c``. The historical code did this with a
*list*::

    subprocess.Popen(["cmd", "/c", binary, *args])

and a comment claiming "no ``shell=True`` → no shell-metachar injection". That
claim is **false**: on Windows, ``Popen`` with a list runs the args through
``subprocess.list2cmdline`` and ``cmd.exe`` then **re-parses** the resulting
line with its own rules. ``list2cmdline`` escapes an inner ``"`` as ``\\"``,
but ``cmd.exe`` treats ``\\"`` as a *quote toggle* (not an escape), so a
user-controlled argument like ``a" & powershell -enc <…> & "b`` breaks out of
the quotes and ``cmd`` executes the ``&``-separated payload — host RCE outside
the AI sandbox (bypassing L44/L10/audit). The user-controlled prompt reaches
every worker-engine spawn, so this was reachable on every Windows install.

The fix builds the command line **ourselves as a string** (bypassing
``list2cmdline``) with cmd.exe-correct quoting: every token is wrapped in
``"..."`` and inner ``"`` is doubled as ``""``. ``""`` is simultaneously:

  * cmd.exe's literal-quote-inside-quotes form — so ``cmd`` stays in quoted
    mode across the whole token and never interprets ``& | < > ^ ( )``; and
  * the ``CommandLineToArgvW`` literal-quote form — so the *target program*
    still receives the argument byte-for-byte.

Trailing backslashes are doubled before the closing quote (C-runtime rule) so a
path ending in ``\`` cannot escape the closing quote.

Residual caveat: ``cmd`` still expands ``%VAR%`` inside double quotes on the
command line. That is an information-disclosure edge (never RCE) and an
inherent cmd limitation with no clean command-line escape; the RCE breakout via
``&``/``|``/``"``-toggle — the actual vulnerability — is fully closed.

The helper is a no-op on POSIX and for non-``.cmd`` binaries, so it changes
nothing on Linux/macOS and nothing for direct ``.exe`` launches.
"""
from __future__ import annotations

import os


def cmd_quote(arg: str) -> str:
    """Quote a single argument for a ``cmd /c`` command line.

    Wraps in ``"..."``, doubles inner ``"`` as ``""``, and doubles a run of
    trailing backslashes so it cannot escape the closing quote.
    """
    # Double any backslashes that immediately precede the closing quote so the
    # C-runtime doesn't read `\"` as an escaped quote.
    n_trailing = len(arg) - len(arg.rstrip("\\"))
    body = arg.replace('"', '""')
    if n_trailing:
        # arg.replace above didn't touch backslashes; re-append the doubled run.
        body = body[: len(body) - n_trailing] + ("\\" * (2 * n_trailing))
    return '"' + body + '"'


def build_cmd_c_line(argv: list[str]) -> str:
    """Build a full, cmd.exe-safe ``cmd /c <program> <args…>`` command line.

    ``argv[0]`` is the program (typically the ``.cmd`` shim path); the rest are
    its arguments. Every element is quoted with :func:`cmd_quote`.
    """
    return "cmd /c " + " ".join(cmd_quote(a) for a in argv)


def _is_windows_cmd_shim(argv: list[str]) -> bool:
    return bool(
        os.name == "nt"
        and argv
        and isinstance(argv[0], str)
        and argv[0].lower().endswith((".cmd", ".bat"))
    )


def windows_shim_command(argv: list[str]):
    """Return the value to hand to ``subprocess.Popen``.

    * On POSIX, or when ``argv[0]`` is not a ``.cmd``/``.bat`` shim: returns the
      original ``argv`` **list** unchanged (Linux/macOS and direct-``.exe``
      launches are byte-for-byte identical to before).
    * On Windows with a ``.cmd``/``.bat`` shim: returns a cmd.exe-safe command
      **string** (bypassing ``list2cmdline``) that closes the metachar-breakout
      RCE. ``Popen`` accepts a string on Windows and passes it to
      ``CreateProcess`` directly.
    """
    if _is_windows_cmd_shim(argv):
        return build_cmd_c_line(argv)
    return argv
