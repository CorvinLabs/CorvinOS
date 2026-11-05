"""Windows compatibility shim.

CorvinOS (and the vendored ``corvin_operator/`` subtrees) do a module-level
``import fcntl`` / ``import resource`` in ~30 places for advisory file locking
and rlimit handling. Those stdlib modules do not exist on Windows, so a fresh
``pip install corvinos`` + ``corvin-serve`` would crash at import time on
Windows.

This module installs no-op stand-ins into ``sys.modules`` *before* any of those
imports run, so the console (a single-process FastAPI/uvicorn app) is importable
and runnable on Windows. It is imported as the very first statement of
``corvin_console/__init__`` so it wins the race against every submodule.

On POSIX this is a complete no-op — the real stdlib modules are used.

The Windows ``fcntl`` shim implements ``flock``/``lockf`` with real
``LockFileEx`` byte-range locks (see ``_win_flock``): console, bridge adapter,
forge MCP server, hooks and daemons are separate processes sharing the same
files, so a no-op lock lost updates. ``fcntl()``/``ioctl()`` stay no-ops.
"""
from __future__ import annotations

import errno as _errno
import sys
import types

# --- real cross-process flock() for Windows ------------------------------------
# The console, the bridge adapter, the forge MCP server, hooks, CLI tools and the
# daemons are SEPARATE processes that read-modify-write the same registries,
# quota counters, ledgers and lock sidecars on every platform, Windows included.
# A no-op flock therefore lost updates there (see forge/security_events.py for
# the audit-chain fork it caused). This emulates flock() with LockFileEx on one
# byte far past any real EOF: Windows byte-range locks are MANDATORY, so locking
# real data (offset 0) would make the locked file unreadable even to its own
# process; a sentinel byte at 1 TiB serialises lockers without blocking normal
# reads/writes. The offset matches security_events' msvcrt sentinel, so both
# lock flavours exclude each other on the same file exactly like POSIX flock.
_WIN_LOCK_OFFSET = 1 << 40
_win_lock_api = None


def _win_lock_api_load():
    global _win_lock_api
    if _win_lock_api is None:
        try:
            import ctypes  # noqa: PLC0415 - Windows-only path
            import msvcrt  # noqa: PLC0415
            from ctypes import wintypes  # noqa: PLC0415

            class _Overlapped(ctypes.Structure):
                _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                            ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                            ("hEvent", wintypes.HANDLE)]

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                        wintypes.DWORD, ctypes.POINTER(_Overlapped)]
            k32.LockFileEx.argtypes = argtypes
            k32.LockFileEx.restype = wintypes.BOOL
            k32.UnlockFileEx.argtypes = argtypes[:1] + argtypes[2:]
            k32.UnlockFileEx.restype = wintypes.BOOL
            _win_lock_api = (ctypes, msvcrt, _Overlapped, k32)
        except (ImportError, OSError, AttributeError):
            _win_lock_api = False  # simulated Windows on POSIX (tests): stay inert
    return _win_lock_api


def _win_flock(fd, operation):
    """``fcntl.flock`` for Windows: LOCK_SH/LOCK_EX/LOCK_UN, LOCK_NB raises
    ``BlockingIOError`` like the real one. Lock is per handle (= per open file
    description, as flock) and released when the file is closed."""
    api = _win_lock_api_load()
    if not api:
        return None
    ctypes, msvcrt, overlapped_t, k32 = api
    if not isinstance(fd, int):
        fd = fd.fileno()
    handle = msvcrt.get_osfhandle(fd)

    def _ov():
        ov = overlapped_t()
        ov.Offset = _WIN_LOCK_OFFSET & 0xFFFFFFFF
        ov.OffsetHigh = _WIN_LOCK_OFFSET >> 32
        return ov

    # flock converts a lock already held through this fd, and LOCK_UN on an
    # unlocked fd is not an error: drop any held lock first, ignore NOT_LOCKED.
    if not k32.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(_ov())):
        err = ctypes.get_last_error()
        if err != 158:  # ERROR_NOT_LOCKED
            raise ctypes.WinError(err)
    if operation & 8:  # LOCK_UN
        return None
    flags = 0x2 if operation & 2 else 0  # LOCK_EX -> LOCKFILE_EXCLUSIVE_LOCK
    if operation & 4:  # LOCK_NB -> LOCKFILE_FAIL_IMMEDIATELY
        flags |= 0x1
    if not k32.LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(_ov())):
        err = ctypes.get_last_error()
        if err in (33, 997):  # ERROR_LOCK_VIOLATION / ERROR_IO_PENDING
            raise BlockingIOError(_errno.EAGAIN, "Resource temporarily unavailable")
        raise ctypes.WinError(err)
    return None


def _win_lockf(fd, cmd, *_range):
    """``fcntl.lockf`` (whole-file use only in this codebase) -> ``_win_flock``."""
    return _win_flock(fd, cmd)



def install() -> None:
    """Idempotently register POSIX-only stdlib stand-ins on Windows."""
    if not sys.platform.startswith("win"):
        return

    if "fcntl" not in sys.modules:
        m = types.ModuleType("fcntl")
        # Constants used as flags by callers (values mirror Linux for sanity).
        m.LOCK_SH, m.LOCK_EX, m.LOCK_NB, m.LOCK_UN = 1, 2, 4, 8
        m.F_GETLK, m.F_SETLK, m.F_SETLKW = 5, 6, 7
        m.F_GETFD, m.F_SETFD, m.F_GETFL, m.F_SETFL = 1, 2, 3, 4

        def _noop(*_a, **_k):  # fcntl / ioctl
            return 0

        m.flock = _win_flock
        m.lockf = _win_lockf
        m.fcntl = _noop
        m.ioctl = _noop
        m._corvin_win_shim = True  # marker so self-tests can detect the shim
        sys.modules["fcntl"] = m

    if "resource" not in sys.modules:
        m = types.ModuleType("resource")
        m.RLIMIT_CPU, m.RLIMIT_DATA, m.RLIMIT_NOFILE, m.RLIMIT_AS = 0, 2, 7, 9
        m.RLIM_INFINITY = -1

        def _getrlimit(*_a, **_k):
            return (-1, -1)

        def _setrlimit(*_a, **_k):
            return None

        m.getrlimit = _getrlimit
        m.setrlimit = _setrlimit
        m._corvin_win_shim = True
        sys.modules["resource"] = m


# Windows: a console-subsystem child (cmd, node, claude.exe, python.exe, git ...)
# started from a parent that has NO console gets a brand-new, VISIBLE console
# window. Corvin's processes are console-less whenever they run hidden (VBS
# launchers, Start-Process -WindowStyle Hidden, detached daemons), so every
# one-shot spawn would flash a terminal. Per-call-site fixes kept missing sites,
# so the default is set here, once, for every spawn. A parent that HAS a console
# (the user's own terminal, or a hidden one) is left alone: its children share
# that console and open no window, and interactive foreground commands keep
# working.
_CREATE_NO_WINDOW = 0x08000000
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_CONSOLE = 0x00000010
_NO_WINDOW_MARK = "_corvin_no_window_default"


def _parent_has_console() -> bool:
    import ctypes  # noqa: PLC0415 — Windows-only path

    try:
        # GetConsoleCP, not GetConsoleWindow: a HIDDEN console (CREATE_NO_WINDOW, a
        # ConPTY) has no window but is attached; children share it invisibly, and
        # giving them CREATE_NO_WINDOW instead puts each in its own console, which a
        # CTRL_BREAK to their process group can no longer reach (graceful stop dies).
        return bool(ctypes.windll.kernel32.GetConsoleCP())
    except Exception:  # noqa: BLE001 — cannot tell: assume none, add the flag
        return False


def _no_window_flags(flags: int) -> int:
    if flags & _CREATE_NO_WINDOW:
        return flags
    if flags & (_DETACHED_PROCESS | _CREATE_NEW_CONSOLE):
        # Both make Windows open a visible console for the child later on.
        # CREATE_NO_WINDOW keeps the detachment without showing anything.
        return (flags & ~(_DETACHED_PROCESS | _CREATE_NEW_CONSOLE)) | _CREATE_NO_WINDOW
    if _parent_has_console():
        return flags
    return flags | _CREATE_NO_WINDOW


def install_no_console_window() -> None:
    """Make every ``subprocess.Popen`` on Windows open no visible console window.

    Patches ``Popen.__init__`` so ``subprocess.run``/``call``/``check_output``,
    ``shell=True`` and the asyncio subprocess transport all pass through it.
    Idempotent. A positional ``creationflags`` argument is left untouched.
    """
    if not sys.platform.startswith("win"):
        return
    import subprocess  # noqa: PLC0415

    original_init = subprocess.Popen.__init__
    if getattr(original_init, _NO_WINDOW_MARK, False):
        return
    creationflags_position = 13  # Popen(args, bufsize, executable, stdin, stdout,
    #   stderr, preexec_fn, close_fds, shell, cwd, env, universal_newlines,
    #   startupinfo, creationflags, ...) — positional args[0] is the command

    def __init__(self, *args, **kwargs):
        if len(args) <= creationflags_position:
            kwargs["creationflags"] = _no_window_flags(kwargs.get("creationflags") or 0)
        original_init(self, *args, **kwargs)

    setattr(__init__, _NO_WINDOW_MARK, True)
    subprocess.Popen.__init__ = __init__


# Windows: a list argv whose program is a .cmd/.bat (npm's claude.cmd, codex.cmd,
# npm.cmd, ...) is run by CreateProcess as `cmd.exe /c "<list2cmdline line>"`.
# list2cmdline escapes an inner quote as \" which cmd reads as a quote toggle, so
# an argument like  x" & calc & "y  runs `calc` (BatBadBut, CVE-2024-3566 class;
# measured: `echo PWN` executed). Many call sites spawn
# `[<claude CLI path>, <flag>, <prompt>]` - fine for claude.exe, host RCE for an
# npm-installed claude.cmd. Rewrite such argv once, here, into the cmd.exe-safe
# line built by agents/_win_shim.build_cmd_c_line (mirrored below: corvin_core
# cannot import the bridges tree). Lists whose program is not a batch file,
# string commands, shell=True and explicit executable= are left untouched.
_BATCH_ARGV_MARK = "_corvin_batch_argv_quoting"


def _cmd_quote(arg: str) -> str:
    """Mirror of ``agents._win_shim.cmd_quote`` (keep in sync)."""
    n_trailing = len(arg) - len(arg.rstrip("\\"))
    body = arg.replace('"', '""')
    if n_trailing:
        body = body[: len(body) - n_trailing] + ("\\" * (2 * n_trailing))
    return '"' + body + '"'


def batch_safe_command(args):
    """cmd.exe-safe command string for a list argv whose program is a
    ``.cmd``/``.bat``; any other value is returned unchanged."""
    import os  # noqa: PLC0415

    if not isinstance(args, (list, tuple)) or not args:
        return args
    try:
        argv = [os.fspath(a) for a in args]
    except TypeError:
        return args
    if not all(isinstance(a, str) for a in argv):
        return args
    if not argv[0].lower().endswith((".cmd", ".bat")):
        return args
    return 'cmd /d /s /c "' + " ".join(_cmd_quote(a) for a in argv) + '"'


# cmd.exe ends a command line at the first line break, caps it at 8191 characters
# and expands %VAR% inside quotes -- so a multi-line / long / %-bearing prompt or
# system prompt handed to an npm `claude.cmd` shim arrives truncated or rewritten
# (the A2A framed prompt arrived as just "User input:"). Mirror of
# agents/_win_shim.prepare_cli_argv for call sites that spawn the CLI directly
# (backstop; the engine uses the explicit helper): the system prompt moves to
# --<flag>-file, the positional prompt to a delete-on-close stdin file.
_SHIM_SYSPROMPT_FLAGS = ("--system-prompt", "--append-system-prompt")
_SHIM_VALUE_FLAGS = frozenset(_SHIM_SYSPROMPT_FLAGS) | {
    "--mcp-config", "--settings", "--json-schema", "--agents", "--plugin-dir",
    # Value flags whose value may be long (tool lists) or hold '%' (a path): such a
    # value is not the positional prompt and must stay right after its flag.
    "--model", "--fallback-model", "--resume", "-r", "--session-id",
    "--allowedTools", "--allowed-tools", "--disallowedTools", "--disallowed-tools",
    "--tools", "--add-dir", "--output-format", "--input-format", "--permission-mode",
    "--permission-prompt-tool", "--max-turns", "--max-budget-usd", "--setting-sources",
    "--betas", "--system-prompt-file", "--append-system-prompt-file",
}
_SHIM_MAX_INLINE = 1500


def _shim_unsafe(text: str) -> bool:
    return len(text) > _SHIM_MAX_INLINE or any(c in text for c in ("\n", "\r", "\0", "%"))


def shim_text_transport(args, stdin):
    """``(argv, stdin_file|None, cleanup|None)``. Identity unless *args* is a list
    whose program is a ``claude*.cmd``/``.bat`` shim holding text cmd.exe mangles."""
    import ntpath  # noqa: PLC0415
    import os  # noqa: PLC0415

    if not isinstance(args, (list, tuple)) or not args:
        return args, None, None
    try:
        argv = [os.fspath(a) for a in args]
    except TypeError:
        return args, None, None
    exe = argv[0]
    if not (all(isinstance(a, str) for a in argv)
            and exe.lower().endswith((".cmd", ".bat"))
            # ntpath, not os.path: a .cmd shim path is a Windows path, and on a
            # POSIX host os.path.basename("C:\\npm\\claude.cmd") is the whole string.
            and ntpath.basename(exe).lower().startswith("claude")):
        return args, None, None
    sys_idx = [i for i in range(len(argv) - 1)
               if argv[i] in _SHIM_SYSPROMPT_FLAGS and _shim_unsafe(argv[i + 1])]
    prompt_idx = None
    if stdin is None and ("-p" in argv or "--print" in argv):
        for i in range(len(argv) - 1, 0, -1):
            if (_shim_unsafe(argv[i]) and argv[i - 1] not in _SHIM_VALUE_FLAGS
                    and i - 1 not in sys_idx
                    and (not argv[i].startswith("-") or argv[i - 1] == "--")):
                prompt_idx = i
                break
    if not sys_idx and prompt_idx is None:
        return args, None, None
    import shutil  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    tmpdir = tempfile.mkdtemp(prefix="corvin-cli-")

    def cleanup() -> None:
        shutil.rmtree(tmpdir, ignore_errors=True)

    try:
        for i in sys_idx:
            path = os.path.join(tmpdir, f"sysprompt-{i}.txt")
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(argv[i + 1])
            argv[i], argv[i + 1] = argv[i] + "-file", path
        fh_in = None
        if prompt_idx is not None:
            text = argv[prompt_idx]
            path = os.path.join(tmpdir, "prompt.txt")
            fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_TEMPORARY", 0), 0o600)
            fh_in = os.fdopen(fd, "r+b")
            fh_in.write(text.encode("utf-8"))
            fh_in.flush()
            fh_in.seek(0)
            del argv[prompt_idx]
            if prompt_idx > 1 and argv[prompt_idx - 1] == "--":
                del argv[prompt_idx - 1]
    except BaseException:
        cleanup()
        raise
    return argv, fh_in, cleanup


def install_batch_argv_quoting() -> None:
    """Route every list-argv ``.cmd``/``.bat`` spawn through
    :func:`batch_safe_command` on Windows. Idempotent; no-op on POSIX."""
    if not sys.platform.startswith("win"):
        return
    import subprocess  # noqa: PLC0415

    original_init = subprocess.Popen.__init__
    if getattr(original_init, _BATCH_ARGV_MARK, False):
        return

    def __init__(self, args, *rest, **kwargs):
        stdin_fh = None
        cleanup = None
        if not rest and not kwargs.get("shell") and kwargs.get("executable") is None:
            args, stdin_fh, cleanup = shim_text_transport(args, kwargs.get("stdin"))
            if stdin_fh is not None:
                kwargs["stdin"] = stdin_fh
            args = batch_safe_command(args)
        try:
            original_init(self, args, *rest, **kwargs)
        except BaseException:
            if cleanup:
                cleanup()
            raise
        finally:
            if stdin_fh is not None:
                stdin_fh.close()
        if cleanup:
            import weakref  # noqa: PLC0415
            weakref.finalize(self, cleanup)

    setattr(__init__, _BATCH_ARGV_MARK, True)
    setattr(__init__, _NO_WINDOW_MARK, getattr(original_init, _NO_WINDOW_MARK, False))
    subprocess.Popen.__init__ = __init__


# Windows: os.replace() onto a file another process (or thread) currently has
# open fails with PermissionError (WinError 5 / 32) because Python opens files
# without FILE_SHARE_DELETE. Every temp-file + os.replace "atomic write" in the
# tree therefore failed sporadically whenever the console, adapter or a hook
# happened to be reading the target at that moment. The readers hold the file
# only for a moment, so a short bounded retry turns that race into a wait.
# Genuine denials (read-only file, ACL) still raise after the deadline.
_REPLACE_RETRY_MARK = "_corvin_replace_retry"
_REPLACE_RETRY_SECONDS = 1.0


def install_replace_retry() -> None:
    """Make ``os.replace`` (and ``Path.replace``) retry transient sharing
    violations on Windows. Idempotent; no-op on POSIX."""
    if not sys.platform.startswith("win"):
        return
    import os  # noqa: PLC0415
    import time  # noqa: PLC0415

    original = os.replace
    if getattr(original, _REPLACE_RETRY_MARK, False):
        return

    def replace(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        deadline = time.monotonic() + _REPLACE_RETRY_SECONDS
        delay = 0.005
        while True:
            try:
                return original(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
            except PermissionError as exc:
                if getattr(exc, "winerror", None) not in (5, 32) or time.monotonic() >= deadline:
                    raise
            time.sleep(delay)
            delay = min(delay * 2, 0.05)

    setattr(replace, _REPLACE_RETRY_MARK, True)
    replace.__doc__ = original.__doc__
    os.replace = replace


# Windows: os.open() without O_BINARY yields a CRT *text-mode* descriptor, so a
# raw os.write(fd, b"...\n") lands on disk as "\r\n" and os.read() drops "\r"
# and stops at 0x1A. Dozens of call sites append JSONL / hash-chained or signed
# records through os.open + os.write; on Windows their bytes silently differed
# from what was hashed. Default every os.open() to binary unless the caller
# asked for O_TEXT explicitly â€” byte-identical to POSIX, where no translation
# exists. builtin open()/io already passes O_BINARY, so it is unaffected.
_BINARY_OPEN_MARK = "_corvin_binary_os_open"


def install_binary_os_open() -> None:
    """Make ``os.open`` default to ``O_BINARY`` on Windows. Idempotent; no-op on POSIX."""
    if not sys.platform.startswith("win"):
        return
    import os  # noqa: PLC0415

    original = os.open
    if getattr(original, _BINARY_OPEN_MARK, False):
        return
    text_or_binary = os.O_TEXT | os.O_BINARY

    def open(path, flags, mode=0o777, *, dir_fd=None):  # noqa: A001 - mirrors os.open
        if not flags & text_or_binary:
            flags |= os.O_BINARY
        return original(path, flags, mode, dir_fd=dir_fd)

    setattr(open, _BINARY_OPEN_MARK, True)
    open.__doc__ = original.__doc__
    os.open = open


install()
install_no_console_window()
install_batch_argv_quoting()
install_replace_retry()
install_binary_os_open()

# Read-only-safe shutil.rmtree + read-open retry (transient EACCES while a writer
# os.replace()s the file) also for runs without the
# corvinOS_path_fix.pth (source checkout started via PYTHONPATH). Idempotent.
try:
    from corvinOS import _win_path_fix as _wpf  # noqa: E402

    _wpf._install_rmtree_readonly()
    _wpf._install_read_retry()
except Exception:  # noqa: BLE001 - package not importable: stdlib rmtree
    pass
