"""Windows process helpers for the forge runner (no-ops / unused on POSIX).

Two gaps the POSIX runner papers over with OS features Windows lacks:

* ``resolve_bash()`` — bash-runtime tools were launched as ``/bin/bash``, which
  does not exist on Windows. We resolve **Git Bash** with the same precedence as
  ``scripts/update-and-deploy.ps1``: ``<git.exe>/../../bin/bash.exe``, then
  ``C:\\Program Files\\Git\\bin\\bash.exe``, then any ``bash`` on PATH that is
  NOT the WSL launcher (``%WINDIR%\\System32\\bash.exe`` or the WindowsApps
  alias) — WSL bash runs the script inside the Linux distro, where the Windows
  impl path does not exist. No bash -> ``BashNotFoundError`` (typed, clear).

* ``WinJob`` — the POSIX path caps CPU with ``RLIMIT_CPU`` and kills the tree
  with ``killpg``. On Windows neither exists, so a CPU-spinning tool ran for the
  full wall-clock budget and a timeout kill (``TerminateProcess``) left
  grandchildren holding the stdout/stderr pipes, so the following
  ``communicate()`` could block forever. A Job Object gives both: a per-process
  user-CPU-time limit (the RLIMIT_CPU equivalent) and ``TerminateJobObject``
  for the whole tree. ``KILL_ON_JOB_CLOSE`` reaps anything left when we close it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class BashNotFoundError(RuntimeError):
    """No usable (non-WSL) bash on this Windows host."""


_GIT_BASH_DEFAULT = r"C:\Program Files\Git\bin\bash.exe"


def _is_wsl_launcher(path: str) -> bool:
    p = os.path.normcase(os.path.abspath(path))
    windir = os.path.normcase(os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows")
    return p.startswith(windir + os.sep) or (os.sep + "windowsapps" + os.sep) in p


def resolve_bash(*, which=shutil.which, exists=os.path.isfile) -> str:
    """Return the bash executable for bash-runtime tools.

    POSIX: ``/bin/bash`` (unchanged). Windows: Git Bash, never WSL's launcher.
    ``which``/``exists`` are injectable for tests.
    """
    if os.name != "nt":
        return "/bin/bash"
    git = which("git.exe") or which("git")
    if git:
        cand = str(Path(git).resolve().parent.parent / "bin" / "bash.exe")
        if exists(cand):
            return cand
    if exists(_GIT_BASH_DEFAULT):
        return _GIT_BASH_DEFAULT
    bash = which("bash.exe") or which("bash")
    if bash and not _is_wsl_launcher(bash):
        return bash
    raise BashNotFoundError(
        "bash-runtime forge tools need Git Bash on Windows (install Git for Windows); "
        "WSL's bash.exe cannot run tools from a Windows path"
    )


def kill_tree(pid: int) -> bool:
    """``taskkill /T /F`` the tree rooted at *pid* (shared corvin_core helper when present)."""
    try:
        from corvin_core.process_signals import kill_tree_windows  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - forge can be installed without corvin_core
        kill_tree_windows = None
    if kill_tree_windows is not None:
        return kill_tree_windows(pid)
    if os.name != "nt" or not pid:
        return False
    try:
        return subprocess.run(["taskkill", "/T", "/F", "/PID", str(int(pid))],
                              capture_output=True, timeout=10, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class WinJob:
    """Job Object holding one tool process tree. Every method is best-effort and
    a no-op off Windows or when the Win32 calls are unavailable."""

    _JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JobObjectExtendedLimitInformation = 9

    def __init__(self, cpu_seconds: int | None = None) -> None:
        self._handle = None
        self._k32 = None
        if os.name != "nt":
            return
        try:
            import ctypes  # noqa: PLC0415
            from ctypes import wintypes  # noqa: PLC0415

            class _Basic(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                            ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD),
                            ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t),
                            ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t),
                            ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class _Io(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class _Ext(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", _Io),
                            ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                    ctypes.c_void_p, wintypes.DWORD]
            k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            h = k32.CreateJobObjectW(None, None)
            if not h:
                return
            info = _Ext()
            flags = self._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if cpu_seconds and cpu_seconds > 0:
                flags |= self._JOB_OBJECT_LIMIT_PROCESS_TIME
                info.BasicLimitInformation.PerProcessUserTimeLimit = int(cpu_seconds) * 10_000_000
            info.BasicLimitInformation.LimitFlags = flags
            if not k32.SetInformationJobObject(h, self._JobObjectExtendedLimitInformation,
                                               ctypes.byref(info), ctypes.sizeof(info)):
                k32.CloseHandle(h)
                return
            self._handle, self._k32 = h, k32
        except (ImportError, OSError, AttributeError):
            self._handle = None

    @property
    def active(self) -> bool:
        return self._handle is not None

    def assign(self, proc: subprocess.Popen) -> bool:
        if self._handle is None:
            return False
        try:
            return bool(self._k32.AssignProcessToJobObject(self._handle, int(proc._handle)))
        except (OSError, AttributeError, ValueError):
            return False

    def terminate(self) -> bool:
        if self._handle is None:
            return False
        try:
            return bool(self._k32.TerminateJobObject(self._handle, 1))
        except OSError:
            return False

    def close(self) -> None:
        if self._handle is not None:
            try:
                self._k32.CloseHandle(self._handle)
            except OSError:
                pass
            self._handle = None


def kill_and_drain(proc: subprocess.Popen, job: WinJob | None, *, drain_timeout: float = 5.0):
    """Windows timeout path: kill the WHOLE tree, then drain with a bound.

    Returns ``(stdout, stderr)`` (possibly empty). Never blocks longer than
    ~2 x *drain_timeout*: if a stray holder keeps the pipes open we close our
    ends and give up instead of hanging the MCP server.
    """
    if job is None or not job.terminate():
        if not kill_tree(proc.pid):
            try:
                proc.kill()
            except OSError:
                pass
    try:
        return proc.communicate(timeout=drain_timeout)
    except subprocess.TimeoutExpired:
        pass
    kill_tree(proc.pid)
    try:
        proc.kill()
    except OSError:
        pass
    try:
        return proc.communicate(timeout=drain_timeout)
    except subprocess.TimeoutExpired:
        for f in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if f is not None:
                    f.close()
            except OSError:
                pass
        return b"", b""
