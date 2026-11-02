"""Windows ``fcntl`` stand-ins must provide REAL cross-process flock semantics.

The console, bridge adapter, forge MCP server, hooks and daemons are separate
processes doing read-modify-write on the same registries/quota/ledger files.
The three Windows shims (corvin_core/_wincompat, forge/_wincompat,
bridges/shared/_compat_fcntl) used to make ``flock`` a no-op, so concurrent
writers silently lost updates. They now emulate flock with LockFileEx on a
sentinel byte far past EOF. Each shim is exercised against a second process.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only shim")

_REPO = Path(__file__).resolve().parents[2]
_SHIMS = {
    "corvin_core": _REPO / "core" / "console" / "corvin_core" / "_wincompat.py",
    "forge": _REPO / "corvin_operator" / "forge" / "forge" / "_wincompat.py",
    "compat_fcntl": _REPO / "corvin_operator" / "bridges" / "shared" / "_compat_fcntl.py",
}

# A child process that loads the shim by file path, increments a counter file
# N times under LOCK_EX (read-modify-write with a sleep in the window).
_CHILD = textwrap.dedent(r"""
    import importlib.util, sys, time
    shim, counter, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
    spec = importlib.util.spec_from_file_location("_shim_under_test", shim)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    flock = mod._win_flock
    for _ in range(n):
        with open(counter, "r+") as fh:
            flock(fh, 2)                       # LOCK_EX
            v = int(fh.read() or 0)
            time.sleep(0.002)
            fh.seek(0); fh.write(str(v + 1)); fh.truncate(); fh.flush()
            flock(fh, 8)                       # LOCK_UN
""")


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"_shim_{path.stem}_{id(path)}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("name", sorted(_SHIMS))
def test_no_lost_updates_across_processes(tmp_path, name):
    counter = tmp_path / "counter"
    counter.write_text("0")
    n = 40
    procs = [
        subprocess.Popen([sys.executable, "-c", _CHILD, str(_SHIMS[name]), str(counter), str(n)])
        for _ in range(3)
    ]
    for p in procs:
        assert p.wait(timeout=120) == 0
    assert int(counter.read_text()) == 3 * n


@pytest.mark.parametrize("name", sorted(_SHIMS))
def test_flock_semantics(tmp_path, name):
    flock = _load(_SHIMS[name])._win_flock
    target = tmp_path / "data.json"
    target.write_text("payload")
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(r"""
            import importlib.util, sys
            spec = importlib.util.spec_from_file_location("s", sys.argv[1])
            m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
            fh = open(sys.argv[2], "r+"); m._win_flock(fh, 2)
            print("locked", flush=True); sys.stdin.readline()
        """), str(_SHIMS[name]), str(target)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "locked"
        with open(target, "r+") as fh:
            # LOCK_NB on a held lock raises BlockingIOError, like fcntl.flock.
            with pytest.raises(BlockingIOError):
                flock(fh, 2 | 4)
            with pytest.raises(BlockingIOError):
                flock(fh.fileno(), 1 | 4)
            # The lock must not make the data itself unreadable/unwritable
            # (Windows byte-range locks are mandatory).
            assert target.read_text() == "payload"
            with open(target, "a") as ap:
                ap.write("+more")
    finally:
        holder.stdin.write("\n")
        holder.stdin.close()
        holder.wait(timeout=30)
    with open(target, "r+") as fh:
        flock(fh, 1 | 4)          # shared, released by holder exit
        flock(fh, 2 | 4)          # converting an already-held lock works
        flock(fh, 2 | 4)          # re-locking through the same fd is idempotent
        flock(fh, 8)
        flock(fh, 8)              # LOCK_UN on an unlocked fd is not an error


def test_installed_module_uses_real_lock():
    for name in ("corvin_core", "forge"):
        mod = _load(_SHIMS[name])
        assert mod._win_flock is not None
    shared = _REPO / "corvin_operator" / "bridges" / "shared"
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, sys.argv[1]);"
         "from _compat_fcntl import fcntl;"
         "print(fcntl.flock.__name__)", str(shared)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.stdout.strip() == "_win_flock", proc.stderr


@pytest.mark.parametrize("name", ["corvin_core", "forge"])
def test_replace_retries_while_a_reader_holds_the_target(tmp_path, name):
    """os.replace onto a file another process has open raised PermissionError
    (no FILE_SHARE_DELETE) — every temp+replace atomic write raced the readers."""
    import os

    mod = _load(_SHIMS[name])
    original = os.replace
    try:
        import nt  # noqa: PLC0415

        os.replace = nt.replace  # start unpatched even if a shim was imported earlier
        target = tmp_path / "state.json"
        target.write_text("old")
        reader = subprocess.Popen(
            [sys.executable, "-c",
             "import sys, time; fh = open(sys.argv[1]); print('open', flush=True); time.sleep(0.4)",
             str(target)],
            stdout=subprocess.PIPE, text=True,
        )
        assert reader.stdout.readline().strip() == "open"
        tmp = tmp_path / "state.json.tmp"
        tmp.write_text("new")
        mod.install_replace_retry()
        os.replace(tmp, target)
        reader.wait(timeout=30)
        assert target.read_text() == "new"
        # A permanent denial still raises (target is a directory).
        (tmp_path / "d").mkdir()
        tmp.write_text("x")
        with pytest.raises(OSError):
            os.replace(tmp, tmp_path / "d")
    finally:
        os.replace = original


def test_posix_mode_checks_do_not_fail_closed_on_ntfs(tmp_path):
    """NTFS reports st_mode 0o666 for every file; mode-bit "tamper" checks
    used to exhaust the compute trial and reject every SOB on Windows."""
    import json

    sys.path.insert(0, str(_REPO / "core" / "compute"))
    sys.path.insert(0, str(_REPO / "corvin_operator"))
    from corvin_compute import license_gate  # noqa: PLC0415
    from license import instance_epoch  # noqa: PLC0415

    state = tmp_path / "global" / "license" / "trial_compute.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"iterations_used": 3}), encoding="utf-8")
    assert license_gate._load_trial_state(tmp_path).iterations_used == 3

    epoch = instance_epoch._epoch_path(tmp_path)
    epoch.parent.mkdir(parents=True, exist_ok=True)
    epoch.write_text(json.dumps({"nonce_epoch": 7}), encoding="utf-8")
    assert instance_epoch.read_instance_epoch(tmp_path) == 7


@pytest.mark.parametrize("name", ["corvin_core", "forge"])
def test_os_open_defaults_to_binary(tmp_path, name):
    """A text-mode CRT fd turned every os.write(b"...\n") into "\r\n" on disk."""
    import os
    import nt  # noqa: PLC0415

    mod = _load(_SHIMS[name])
    original = os.open
    try:
        os.open = nt.open
        mod.install_binary_os_open()
        p = tmp_path / "x.jsonl"
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.write(fd, b'{"a":1}\n\x1a\n')
        os.close(fd)
        assert p.read_bytes() == b'{"a":1}\n\x1a\n'
        fd = os.open(p, os.O_RDONLY)
        assert os.read(fd, 100) == b'{"a":1}\n\x1a\n'
        os.close(fd)
        fd = os.open(p, os.O_WRONLY | os.O_TRUNC | os.O_TEXT)  # explicit text mode kept
        os.write(fd, b"t\n")
        os.close(fd)
        assert p.read_bytes() == b"t\r\n"
    finally:
        os.open = original
