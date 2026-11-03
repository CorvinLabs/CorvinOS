r"""Windows ``.cmd``/``.bat`` shim spawning — cmd.exe-safe argument quoting.

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

    The whole line is wrapped in ONE extra pair of quotes and run with ``/s``.
    Plain ``cmd /c`` applies its legacy rule "more than two quotes -> strip the
    FIRST and the LAST quote", which for ``cmd /c "C:\\x.cmd" "--flag"`` eats
    the program's opening quote and flips the quote parity of every following
    token. Measured on Windows 11: EVERY shim spawn failed with
    ``'C:\\x.cmd" "--flag' is not recognized``, and an argument ``x" & echo PWN``
    executed ``echo PWN`` -- the exact breakout this module exists to close.
    With ``/s`` cmd strips exactly the outer pair added here, so the per-token
    quoting survives intact. ``/d`` skips registry AutoRun commands (they
    would run before the shim and can pollute the child's stdout).

    Limitation: cmd ends a command line at a newline, so an argument holding
    a line break is truncated there (no injection -- the rest is dropped).
    Pass multi-line text via stdin or a file (``--append-system-prompt-file``).
    """
    return 'cmd /d /s /c "' + " ".join(cmd_quote(a) for a in argv) + '"'


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


def resolve_cli_argv(argv: list[str]):
    """Spawn-ready form of ``argv`` for an external CLI (npm, claude, ...).

    * POSIX: returns ``argv`` unchanged (Linux/macOS behaviour identical).
    * Windows: a bare ``argv[0]`` is resolved through ``shutil.which`` first --
      ``CreateProcess`` only appends ``.exe`` and never consults ``PATHEXT``,
      so a bare ``"npm"`` / ``"npx"`` / npm-installed ``claude`` (``*.cmd``
      shims) raises ``FileNotFoundError`` although it works in every shell.
      The result then goes through :func:`windows_shim_command`, so a
      ``.cmd``/``.bat`` target becomes the cmd.exe-safe command string
      (BatBadBut-class injection closed) and an ``.exe`` stays a list.
      An unresolvable name is kept so ``Popen`` raises ``FileNotFoundError``.
    """
    if os.name != "nt" or not argv:
        return argv
    exe = argv[0]
    if isinstance(exe, str) and not os.path.dirname(exe):
        import shutil  # noqa: PLC0415 - only needed on the Windows branch
        found = shutil.which(exe)
        if found:
            argv = [found, *argv[1:]]
    return windows_shim_command(list(argv))


def no_console_window_flags() -> int:
    """Windows-only ``creationflags`` addend: ``CREATE_NO_WINDOW``.

    2026-08-02, reported live: every engine spawn on Windows (``cmd /c``
    wrapping a ``.cmd``/``.bat`` shim like ``claude.cmd``, or a direct
    ``.exe``) is a console-subsystem process. Launched from a console-less
    parent — the web console, a bridge daemon, an A2A worker, all started
    hidden themselves — Windows allocates it a BRAND NEW, VISIBLE console
    unless told otherwise. This happens on every single turn, not just at
    boot, and A2A hits it identically to a bridge/console turn since it
    spawns the same engine the same way. The visible window's title is the
    full command line, which is why the flashed-up terminal shows the
    ``.cmd`` shim's path (typically under ``%APPDATA%\\npm``) rather than
    anything CorvinOS-specific-looking.

    Always safe to OR into any existing ``creationflags`` value: returns 0
    on POSIX, and 0 if ``subprocess.CREATE_NO_WINDOW`` is unavailable (any
    non-Windows interpreter — including CI running these call sites under
    mocks on Linux/macOS, where the real stdlib has no such attribute).
    """
    if os.name != "nt":
        return 0
    import subprocess  # noqa: PLC0415 — only needed on the Windows branch
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------------------------------------------------------------------
# Text transport for a ``.cmd``/``.bat`` shim: keep prompts OFF the command line
# ---------------------------------------------------------------------------
# cmd.exe (a) ends the command line at the first line break, (b) caps it at 8191
# characters and (c) expands ``%VAR%`` even inside double quotes. A multi-line
# prompt / system prompt on argv therefore arrives truncated ("User input:" and
# nothing else), a long one fails outright, and ``%PATH%``-style text is rewritten.
# A native ``claude.exe`` takes argv verbatim through CreateProcess, so this only
# engages when the resolved executable is a shim on Windows -- POSIX and ``.exe``
# argv stay byte-identical.

import contextlib  # noqa: E402
import dataclasses  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402

_SYSTEM_PROMPT_FLAGS = ("--system-prompt", "--append-system-prompt")
_UNSAFE_CHARS = ("\n", "\r", "\x00", "%")
_MAX_INLINE = 1500  # well below cmd's 8191 even with several such args


def _unsafe_for_cmdline(text: str) -> bool:
    return len(text) > _MAX_INLINE or any(c in text for c in _UNSAFE_CHARS)


def is_cmd_shim(exe: str | None) -> bool:
    """True when *exe* (bare names resolved via PATH/PATHEXT) is a ``.cmd``/``.bat`` on Windows."""
    if os.name != "nt" or not isinstance(exe, str) or not exe:
        return False
    if not exe.lower().endswith((".cmd", ".bat")) and not os.path.dirname(exe):
        exe = shutil.which(exe) or exe
    return exe.lower().endswith((".cmd", ".bat"))


@dataclasses.dataclass
class ShimArgs:
    """Result of :func:`prepare_cli_argv`. ``argv`` is spawn-ready *before*
    :func:`resolve_cli_argv`/:func:`windows_shim_command`; ``stdin_text`` (or ``None``)
    must be fed to the child's stdin by the caller (see :func:`feed_stdin`);
    ``cleanup()`` removes the private temp dir and is safe to call repeatedly."""

    argv: list
    stdin_text: str | None = None
    _tmpdir: str | None = None

    def cleanup(self) -> None:
        d, self._tmpdir = self._tmpdir, None
        if d:
            shutil.rmtree(d, ignore_errors=True)


def prepare_cli_argv(argv: list, prompt: str | None = None) -> ShimArgs:
    """Move text that cmd.exe would mangle off the command line.

    Only when ``argv[0]`` is a Windows ``.cmd``/``.bat`` shim:

    * *prompt* (the positional prompt: the element equal to it, plus a directly
      preceding ``--``) is removed from argv and returned as ``stdin_text`` -- ``claude -p``
      reads the prompt from stdin when no positional prompt is given (always moved,
      so quotes, ``%``, newlines and length are all irrelevant);
    * ``--system-prompt``/``--append-system-prompt`` values that hold a line
      break, ``%`` or are long become ``<flag>-file <path>`` in a private temp dir.

    Elsewhere returns ``argv`` unchanged with no stdin and nothing to clean up.
    """
    argv = list(argv)
    if not argv or not is_cmd_shim(argv[0]):
        return ShimArgs(argv)
    res = ShimArgs(argv)
    try:
        for i in range(len(argv) - 1):
            flag = argv[i]
            if flag in _SYSTEM_PROMPT_FLAGS and isinstance(argv[i + 1], str) and _unsafe_for_cmdline(argv[i + 1]):
                if res._tmpdir is None:
                    res._tmpdir = tempfile.mkdtemp(prefix="corvin-cli-")
                path = os.path.join(res._tmpdir, f"sysprompt-{i}.txt")
                with open(path, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(argv[i + 1])
                argv[i], argv[i + 1] = flag + "-file", path
        if prompt is not None:
            for j in range(len(argv) - 1, 0, -1):
                if argv[j] == prompt:
                    del argv[j]
                    if j > 1 and argv[j - 1] == "--":
                        del argv[j - 1]
                    res.stdin_text = prompt
                    break
    except BaseException:
        res.cleanup()
        raise
    res.argv = argv
    return res


def feed_stdin(proc, text: str | None) -> None:
    """Write *text* to ``proc.stdin`` and close it (text or binary pipe). No-op for ``None``."""
    if text is None or proc.stdin is None:
        return
    try:
        # A text-mode pipe translates LF to CRLF on Windows: write the raw
        # UTF-8 bytes to the underlying buffer so the child sees the prompt verbatim.
        out = getattr(proc.stdin, "buffer", None)
        if out is not None:
            proc.stdin.flush()
            out.write(text.encode("utf-8"))
            out.flush()
        else:
            proc.stdin.write(text.encode("utf-8"))
            proc.stdin.flush()
    except (BrokenPipeError, OSError):
        pass
    finally:
        with contextlib.suppress(OSError, ValueError):
            proc.stdin.close()
