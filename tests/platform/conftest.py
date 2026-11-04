"""Windows-like test mode (PLAN-0969 WP-5, CONCEPT-0125).

``windows_like`` runs the REAL code under the conditions a Windows install has, on a Linux CI host:

* ``fcntl`` / ``resource`` are the no-op stand-ins ``corvin_core._wincompat`` installs on Windows
  (not ``None``: a real Windows console has the shim, so a fresh import must see it);
* the POSIX-only attributes are gone from ``os`` (open flags, ``getuid``, ``killpg``, ``fork`` ...)
  and from ``signal`` (``SIGKILL``, ``SIGALRM`` ...), so a bare use raises ``AttributeError``;
* ``memory_bridge._is_windows`` is True, so session names are sanitised as on NTFS.

What it cannot simulate: ``os.name`` stays ``"posix"`` (pathlib would refuse to build paths), real
NTFS rules for other characters, Windows file locking and ACLs. A probe report from a real Windows
host (``GET /v1/console/aco/platform/probe``) is the proof for those.
"""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
for _p in (REPO / "core" / "console", REPO / "corvin_operator" / "forge",
           REPO / "corvin_operator" / "bridges" / "shared", REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

POSIX_OS_ATTRS = (
    "O_NOFOLLOW", "O_DIRECTORY", "O_CLOEXEC", "O_NOCTTY", "O_NONBLOCK", "O_NDELAY", "O_SYNC",
    "O_DSYNC", "O_RSYNC", "O_PATH", "O_NOATIME", "O_TMPFILE", "O_DIRECT", "O_ASYNC",
    "getuid", "geteuid", "getgid", "getegid", "getpgid", "getpgrp", "killpg", "setsid",
    "setpgrp", "fork", "forkpty", "uname", "getloadavg", "mkfifo", "chown", "lchown",
    "statvfs", "nice", "sync", "setuid", "setgid",
)
POSIX_SIGNALS = ("SIGKILL", "SIGHUP", "SIGUSR1", "SIGUSR2", "SIGALRM", "SIGCHLD", "SIGPIPE",
                 "SIGQUIT", "SIGTSTP", "SIGCONT", "SIGSTOP", "SIGWINCH")

#: Modules a test re-imports fresh inside the mode (so they bind the shim, not real fcntl).
FRESH_MODULES = ("conversation_recall", "memory_bridge", "core.skills.feedback.history_store",
                 "core.skills.prompt_skill_versions")


@pytest.fixture
def windows_like(monkeypatch, tmp_path):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("FORGE_ROOT", raising=False)

    # The real Windows stand-ins, installed exactly as on Windows.
    from corvin_core import _wincompat
    for name in ("fcntl", "resource"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    _wincompat.install()
    monkeypatch.setattr(sys, "platform", "linux")  # only the install sees win32
    assert getattr(sys.modules["fcntl"], "_corvin_win_shim", False)

    for name in POSIX_OS_ATTRS:
        monkeypatch.delattr(os, name, raising=False)
    for name in POSIX_SIGNALS:
        monkeypatch.delattr(signal, name, raising=False)
    for name in FRESH_MODULES:
        monkeypatch.delitem(sys.modules, name, raising=False)

    import memory_bridge  # fresh, under the shim
    monkeypatch.setattr(memory_bridge, "_is_windows", lambda: True)
    yield tmp_path / "home"
    for name in FRESH_MODULES:  # never leak shim-bound modules into later tests
        sys.modules.pop(name, None)
