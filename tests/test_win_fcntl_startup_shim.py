"""Regression: on Windows every Corvin Python process gets a real ``fcntl.flock``
from interpreter start (corvinOS_path_fix.pth -> corvinOS._win_path_fix), not
only processes that happened to import corvin_core / forge first. Before, CLIs,
MCP servers and workers crashed on bare ``import fcntl`` (quota_counter,
event_store, skill_adapter, routing_ledger ...) or silently skipped the lock
(``fcntl = None`` fallbacks) while the console still locked."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only shim")


def _shim_module():
    from corvinOS import _win_path_fix

    saved = sys.modules.pop("fcntl", None)
    try:
        _win_path_fix._install_fcntl_shim()  # installs a fresh shim into sys.modules
        return sys.modules["fcntl"]
    finally:
        if saved is not None:
            sys.modules["fcntl"] = saved
        else:
            sys.modules.pop("fcntl", None)


def test_startup_shim_excludes_corvin_core_shim_across_processes(tmp_path):
    fcntl = _shim_module()
    assert fcntl._corvin_win_shim is True
    lock = tmp_path / "x.lock"
    probe = (
        "import sys; sys.path.insert(0, sys.argv[2])\n"
        "import corvin_core._wincompat as w\n"
        "f = open(sys.argv[1], 'a+')\n"
        "try:\n    w._win_flock(f.fileno(), 2 | 4); print('ACQUIRED')\n"
        "except BlockingIOError:\n    print('BLOCKED')\n"
    )
    args = [sys.executable, "-c", probe, str(lock), str(REPO / "core" / "console")]
    with open(lock, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert subprocess.run(args, capture_output=True, text=True, timeout=120).stdout.strip() == "BLOCKED"
        fcntl.flock(fh, fcntl.LOCK_UN)
        assert subprocess.run(args, capture_output=True, text=True, timeout=120).stdout.strip() == "ACQUIRED"


def test_startup_shim_lock_nb_raises_blocking_io_error(tmp_path):
    fcntl = _shim_module()
    lock = tmp_path / "y.lock"
    with open(lock, "a+") as a, open(lock, "a+") as b:
        fcntl.flock(a, fcntl.LOCK_EX)
        with pytest.raises(BlockingIOError):
            fcntl.flock(b, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(a, fcntl.LOCK_UN)
        fcntl.flock(b, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(b, fcntl.LOCK_UN)


def test_rmtree_deletes_readonly_tree(tmp_path):
    """Sealed audit segments (chmod 0o444) and git pack files are READ-ONLY on
    NTFS; GDPR erasure/uninstall via shutil.rmtree must still remove them."""
    import os
    import shutil
    import stat

    from corvinOS import _win_path_fix

    _win_path_fix._install_rmtree_readonly()
    root = tmp_path / "tree"
    (root / "sub").mkdir(parents=True)
    f = root / "sub" / "audit.jsonl.age"
    f.write_bytes(b"x")
    os.chmod(f, 0o444)
    assert not os.access(f, os.W_OK)
    shutil.rmtree(root)
    assert not root.exists()
    # explicit ignore_errors keeps its meaning
    g = tmp_path / "g"
    g.mkdir()
    shutil.rmtree(g, ignore_errors=True)
    assert not g.exists()


def test_read_open_survives_concurrent_replace(tmp_path):
    """A reader must not see transient PermissionError while a writer os.replace()s the file."""
    import os
    import threading
    import time

    from corvinOS import _win_path_fix

    _win_path_fix._install_read_retry()
    p = tmp_path / "c.json"
    p.write_text("{}", encoding="utf-8")
    stop = threading.Event()
    errors: list = []

    def writer():
        i = 0
        while not stop.is_set():
            i += 1
            t = tmp_path / "c.tmp"
            t.write_text("{}", encoding="utf-8")
            try:
                os.replace(t, p)
            except OSError:
                pass

    th = threading.Thread(target=writer)
    th.start()
    try:
        end = time.monotonic() + 1.5
        while time.monotonic() < end:
            try:
                with open(p, encoding="utf-8") as f:
                    f.read()
            except OSError as exc:
                errors.append(exc)
    finally:
        stop.set()
        th.join()
    assert errors == []
    # a directory / missing file still fails fast with the original error
    with pytest.raises(PermissionError):
        open(tmp_path)
    with pytest.raises(FileNotFoundError):
        open(tmp_path / "nope")


def _run_fresh(code: str) -> str:
    """Run ``code`` in a fresh interpreter (the .pth hook installs the patches; the
    repo conftest fences builtins.open in-process, so introspect out-of-process)."""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_patched_open_stays_introspectable_and_picklable():
    out = _run_fresh(
        "import builtins, io, pickle, inspect\n"
        "assert io.open is builtins.open\n"
        "assert pickle.loads(pickle.dumps(builtins.open)) is builtins.open\n"
        "print('buffering' in inspect.signature(builtins.open).parameters)\n"
    )
    assert out == "True"


def test_denied_read_does_not_stall_every_probe(tmp_path):
    """A genuinely denied path pays the retry window once, then fails fast."""
    p = tmp_path / "locked.json"
    p.write_text("{}", encoding="utf-8")
    out = _run_fresh(
        "import builtins, time\n"
        "p = %r\n"
        "w = builtins.open\n"
        "cell = next(c for c in w.__closure__ if c.cell_contents is w.__wrapped__)\n"
        "def deny(f, *a, **k):\n    raise PermissionError(13, 'denied')\n"
        "cell.cell_contents = deny\n"
        "for _ in range(2):\n"
        "    try: w(p)\n    except PermissionError: pass\n"
        "t = time.monotonic()\n"
        "for _ in range(20):\n"
        "    try: w(p)\n    except PermissionError: pass\n"
        "print(time.monotonic() - t < 0.25)\n" % str(p)
    )
    assert out == "True"
