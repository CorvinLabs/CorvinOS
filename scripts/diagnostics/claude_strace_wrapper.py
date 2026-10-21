#!/usr/bin/env python3
"""strace wrapper for the claude CLI (diagnostic, Linux only).

Pin it with ``CORVIN_CLAUDE_BIN=<this file>`` and name the real CLI in
``CORVIN_CLAUDE_REAL_BIN``. ``corvin_operator/bridges/shared/helper_model.py``
(``resolve_claude_bin``) honours an absolute pin as-is, so every spawn goes
through this file.

What it does:
  * The CLI is a child of strace (``strace -f``); strace records the syscalls
    ``kill``, ``tgkill``, ``tkill``, ``exit_group`` and ``execve`` plus signal
    deliveries (``signal=all``) with timestamps and PIDs.
  * ``-s 0`` hides every string argument, so ``execve`` shows ``[...]`` and no
    prompt or secret reaches the log. The wrapper log records the target path.
  * stdin, stdout and stderr are inherited, never piped, so nothing is
    buffered and the CLI output is byte-identical to a direct run.
  * Signals sent to the wrapper are read with ``sigtimedwait`` (so the sender
    PID is known), logged to the wrapper log and forwarded to the CLI process.
    Terminal signals (``si_code == SI_KERNEL``) are not forwarded again: the
    terminal already delivered them to the CLI as a group member.
  * The exit status is passed through; a CLI killed by a signal re-raises that
    signal on the wrapper.
  * Fail-open: if strace is missing, not runnable or refused by ptrace, the
    wrapper writes a warning to the wrapper log and ``exec``s the real CLI
    directly. The wrapper never makes the CLI output empty.

Logs go to ``$CORVIN_HOME/diagnostics/strace/`` (default ``~/.corvin``), a
directory of mode 0700, with files of mode 0600, outside any worker cwd and
outside every session ledger. There are two files per run:
``<stamp>-<pid>.strace.log`` (strace output) and ``<stamp>-<pid>.wrapper.log``
(wrapper events). Neither is ever written to stdout or stderr.

Env knobs:
  CORVIN_CLAUDE_REAL_BIN   real claude binary (default: first ``claude`` on PATH
                           that is not this file)
  CORVIN_CLAUDE_STRACE_BIN strace binary (default: ``strace`` on PATH)

Not in scope: macOS and Windows (strace is Linux-only; the wrapper execs the
real CLI there with a warning in the wrapper log).
"""

import datetime
import os
import shutil
import signal
import subprocess
import sys
import time

ENV_REAL = "CORVIN_CLAUDE_REAL_BIN"
ENV_STRACE = "CORVIN_CLAUDE_STRACE_BIN"
TRACE_EXPR = "trace=kill,tgkill,tkill,exit_group,execve"
# Signals the wrapper takes over from the caller and forwards to the CLI.
FORWARDED = (
    signal.SIGTERM,
    signal.SIGINT,
    signal.SIGHUP,
    signal.SIGQUIT,
    signal.SIGUSR1,
    signal.SIGUSR2,
)
SI_KERNEL = 0x80  # si_code of a tty-generated signal (Ctrl-C, Ctrl-\)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="microseconds")


def _log(path, msg):
    fd = os.open(path, os.O_WRONLY | os.O_APPEND)
    try:
        os.write(fd, f"{_now()} {msg}\n".encode("utf-8", "replace"))
    finally:
        os.close(fd)


def _create_private(path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    os.chmod(path, 0o600)  # independent of umask


def _log_dir():
    root = os.environ.get("CORVIN_HOME", "").strip() or os.path.join(
        os.path.expanduser("~"), ".corvin"
    )
    return os.path.join(root, "diagnostics", "strace")


def _resolve_target(self_path):
    pinned = os.environ.get(ENV_REAL, "").strip()
    if pinned:
        if "/" not in pinned:
            return shutil.which(pinned) or pinned
        return pinned
    self_real = os.path.realpath(self_path)
    for part in os.environ.get("PATH", "").split(os.pathsep):
        cand = os.path.join(part, "claude")
        if (
            os.path.isfile(cand)
            and os.access(cand, os.X_OK)
            and os.path.realpath(cand) != self_real
        ):
            return cand
    return None


def _ptrace_scope():
    try:
        with open("/proc/sys/kernel/yama/ptrace_scope") as fh:
            return fh.read().strip()
    except OSError:
        return "n/a"


def _strace_usable(strace):
    """Probe ptrace with a throwaway run. Its stderr is captured so a refusal
    never reaches the CLI's stderr."""
    try:
        res = subprocess.run(
            [strace, "-o", os.devnull, "--", "true"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"probe failed: {exc}"
    if res.returncode != 0:
        detail = res.stderr.decode("utf-8", "replace").strip()[:300]
        return False, f"probe rc={res.returncode}: {detail}"
    return True, ""


def _fail_open(wlog, target, args, reason):
    _log(wlog, f"WARN strace inactive ({reason}); fail-open: CLI runs without trace")
    try:
        os.execv(target, [target, *args])
    except OSError as exc:
        sys.stderr.write(f"claude_strace_wrapper: cannot exec {target}: {exc}\n")
        return 127
    return 127  # not reached


def _tracee_pids(strace_pid):
    try:
        with open(f"/proc/{strace_pid}/task/{strace_pid}/children") as fh:
            return [int(x) for x in fh.read().split()]
    except OSError:
        return []


def _unblock_in_child():
    signal.pthread_sigmask(signal.SIG_SETMASK, set())


def main(argv):
    args = argv[1:]
    self_path = os.path.abspath(argv[0])
    log_dir = _log_dir()
    os.makedirs(log_dir, mode=0o700, exist_ok=True)
    os.chmod(log_dir, 0o700)
    stem = os.path.join(log_dir, f"{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}")
    wlog = stem + ".wrapper.log"
    slog = stem + ".strace.log"
    _create_private(wlog)

    target = _resolve_target(self_path)
    if not target or not os.access(target, os.X_OK):
        _log(wlog, f"ERROR no executable claude target ({ENV_REAL} unset or invalid)")
        sys.stderr.write(f"claude_strace_wrapper: no executable claude target ({ENV_REAL})\n")
        return 127

    _log(
        wlog,
        f"start wrapper_pid={os.getpid()} target={target} "
        f"ptrace_scope={_ptrace_scope()} args=<{len(args)} hidden>",
    )

    if not sys.platform.startswith("linux"):
        return _fail_open(wlog, target, args, f"platform {sys.platform} is not linux")

    strace = os.environ.get(ENV_STRACE, "").strip() or shutil.which("strace")
    if not strace:
        return _fail_open(wlog, target, args, "strace not found")
    ok, why = _strace_usable(strace)
    if not ok:
        return _fail_open(wlog, target, args, why)

    _create_private(slog)
    cmd = [
        strace, "-f", "-tt", "-s", "0", "-o", slog,
        "-e", TRACE_EXPR, "-e", "signal=all",
        "--", target, *args,
    ]
    _log(wlog, f"strace attached log={os.path.basename(slog)}")

    # Block the forwarded signals so sigtimedwait can read them with si_pid.
    # The child unblocks them again before it execs strace.
    signal.pthread_sigmask(signal.SIG_BLOCK, FORWARDED)
    try:
        proc = subprocess.Popen(cmd, preexec_fn=_unblock_in_child)
    except OSError as exc:
        signal.pthread_sigmask(signal.SIG_UNBLOCK, FORWARDED)
        _log(wlog, f"ERROR spawn failed: {exc}")
        return _fail_open(wlog, target, args, f"spawn failed: {exc}")

    while proc.poll() is None:
        info = signal.sigtimedwait(FORWARDED, 0.2)
        if info is None:
            continue
        name = signal.Signals(info.si_signo).name
        if info.si_code == SI_KERNEL:
            _log(wlog, f"{name} from terminal (already delivered to group; not forwarded)")
            continue
        targets = _tracee_pids(proc.pid)
        if not targets:
            _log(wlog, f"WARN no CLI child found, {name} sent to strace pid={proc.pid}")
            targets = [proc.pid]
        for pid in targets:
            try:
                os.kill(pid, info.si_signo)
                _log(wlog, f"{name} from pid={info.si_pid} si_code={info.si_code} forwarded to pid={pid}")
            except ProcessLookupError:
                _log(wlog, f"{name} from pid={info.si_pid}: pid={pid} already gone")

    rc = proc.returncode
    if rc < 0:
        sig = -rc
        _log(wlog, f"exit: killed by signal {sig}; re-raising")
        signal.signal(sig, signal.SIG_DFL)
        signal.pthread_sigmask(signal.SIG_UNBLOCK, FORWARDED)
        os.kill(os.getpid(), sig)
        return 128 + sig  # not reached
    _log(wlog, f"exit: rc={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
