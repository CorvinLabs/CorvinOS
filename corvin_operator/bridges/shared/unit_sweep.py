"""Terminate processes left in this service's cgroup before the adapter exits (PLAN bg-task P2).

Measured 2026-10-18: ``corvin-voice-bridge-adapter`` stopped with no run in flight, yet
background tool processes of an earlier turn (``npm run build``, a ``run1.sh``, ``node``)
were still alive after the unit stopped. ``KillMode=mixed`` then SIGKILLed whatever was
left, and systemd reported "remains running after unit stopped". Those processes belong to
no tracked engine run, so the adapter's own drain never reached them.

The sweep reads this unit's own cgroup (cgroup v2) and sends SIGTERM to every process in it
except the adapter itself, waits a grace period, then SIGKILLs the stragglers. It is limited
to the unit's cgroup on purpose: it never signals a process outside this service.

It runs ONLY when this process is the main process of its own systemd service (parent is the
systemd manager, cgroup leaf is a ``*.service``). An adapter started from a shell, a test
runner or a CI job shares its cgroup with the caller: until 2026-10-21 the sweep then
SIGTERMed pytest, the operator's shell and the Claude Code session that ran the test
(``test_adapter_skill_inject.py`` → ``proc.terminate()`` → drain → sweep; measured three
times, exit 143). ``running_as_service_main`` is that gate; a sweep outside it is refused and
logged, never attempted.
``KillMode`` stays ``mixed``: switching to ``control-group`` would send SIGTERM to live engine
runs at the start of the drain, which is the failure ``mixed`` was chosen to avoid.
"""
from __future__ import annotations

import logging
import os
import signal
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def own_cgroup_procs_file() -> Path | None:
    """``cgroup.procs`` of the cgroup this process runs in (cgroup v2), or None when unavailable."""
    try:
        for line in Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines():
            parts = line.split(":", 2)
            if len(parts) == 3 and parts[0] == "0" and parts[2]:
                procs = Path("/sys/fs/cgroup") / parts[2].lstrip("/") / "cgroup.procs"
                return procs if procs.exists() else None
    except OSError:
        return None
    return None


def own_cgroup_path() -> str:
    """The cgroup v2 path of this process (``/proc/self/cgroup`` line ``0::<path>``), or ``""``."""
    try:
        for line in Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines():
            parts = line.split(":", 2)
            if len(parts) == 3 and parts[0] == "0":
                return parts[2]
    except OSError:
        return ""
    return ""


def running_as_service_main(*, ppid: int | None = None, cgroup_path: str | None = None) -> bool:
    """True only when this process is the main process of its own systemd service.

    Two conditions, both required: the parent process is the systemd manager (``comm`` is
    ``systemd``: the user manager for ``systemctl --user`` units, pid 1 for system units) and
    the cgroup leaf is a ``*.service``. A process started by a shell, a test runner, a CI job
    or ``systemd-run --scope`` fails at least one of them and must never sweep: its cgroup is
    the caller's.
    """
    if ppid is None:
        ppid = os.getppid()
    try:
        comm = Path(f"/proc/{ppid}/comm").read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if comm != "systemd":
        return False
    if cgroup_path is None:
        cgroup_path = own_cgroup_path()
    leaf = cgroup_path.rstrip("/").rsplit("/", 1)[-1]
    return leaf.endswith(".service")


#: The verdict of running_as_service_main() taken when the adapter STARTED (note_startup()).
#: Evaluated at sweep time an orphaned adapter — its shell parent gone during the drain — is
#: reparented to the user manager and would pass the gate (REF-10).
_SERVICE_MAIN_AT_START: bool | None = None


def note_startup() -> bool:
    """Record, once, whether this process started as a service main process. Called by the
    adapter before it forks anything; the sweep reads this verdict."""
    global _SERVICE_MAIN_AT_START  # noqa: PLW0603
    if _SERVICE_MAIN_AT_START is None:
        _SERVICE_MAIN_AT_START = running_as_service_main()
    return _SERVICE_MAIN_AT_START


def _pids_in(procs_file: Path) -> set[int]:
    try:
        return {int(x) for x in procs_file.read_text(encoding="utf-8").split() if x.isdigit()}
    except OSError:
        return set()


def terminate_pids(pids: set[int], *, grace_s: float = 5.0) -> int:
    """SIGTERM *pids*, wait up to *grace_s*, SIGKILL whatever is still alive. Returns the count signalled.

    The caller decides which pids are safe to signal. Never called with our own pid.
    """
    me = os.getpid()
    targets = {p for p in pids if p > 1 and p != me}
    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:  # not ours: leave it alone
            targets.discard(pid)
    deadline = time.monotonic() + grace_s
    while targets and time.monotonic() < deadline:
        targets = {p for p in targets if _alive(p)}
        if targets:
            time.sleep(0.1)
    for pid in targets:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    return len(pids - {me})


def _alive(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    # field 3 is the state; a zombie is dead for our purposes
    return stat.rsplit(")", 1)[-1].split()[0] != "Z"


def sweep_unit_leftovers(*, grace_s: float = 5.0, procs_file: Path | None = None) -> int:
    """Terminate everything left in this unit's cgroup except this process. 0 when not on cgroup v2.

    *procs_file* is injectable so tests never touch a real cgroup (the test runner's own
    cgroup holds the operator's shell and CLI sessions). Without it the real cgroup is read,
    and only when :func:`running_as_service_main` holds — otherwise nothing is signalled.
    """
    if procs_file is None:
        verdict = _SERVICE_MAIN_AT_START if _SERVICE_MAIN_AT_START is not None else running_as_service_main()
        if not verdict:
            logger.info("unit sweep skipped: not the main process of a systemd service "
                        "(the cgroup is shared with whoever started this adapter)")
            return 0
        procs_file = own_cgroup_procs_file()
    if procs_file is None:
        return 0
    leftovers = _pids_in(procs_file) - {os.getpid()}
    if not leftovers:
        return 0
    logger.info("unit sweep: terminating %d leftover process(es) in this unit's cgroup", len(leftovers))
    return terminate_pids(leftovers, grace_s=grace_s)
