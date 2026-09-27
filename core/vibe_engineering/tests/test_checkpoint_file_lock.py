"""Regression tests for CheckpointManager.acquire_lock / release_lock (ADR-0892).

Before the fix, release_lock() only unlinked the lock file and never closed the
descriptor, so the flock stayed held for the life of the process: a second
process waiting on the lock timed out even after the holder "released" it.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from core.vibe_engineering.checkpoint_manager import (
    CheckpointKeyUnavailable,
    CheckpointManager,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock semantics")


def _child_code(checkpoint_dir, checkpoint_id, timeout_s) -> str:
    return textwrap.dedent(
        f"""
        import sys
        from core.vibe_engineering.checkpoint_manager import (
            CheckpointManager, CheckpointKeyUnavailable)
        m = CheckpointManager({str(checkpoint_dir)!r}, tenant_id="_default")
        try:
            m.acquire_lock({checkpoint_id!r}, timeout_s={timeout_s})
        except CheckpointKeyUnavailable:
            sys.exit(3)
        m.release_lock({checkpoint_id!r})
        sys.exit(0)
        """
    )


def _other_process_try_lock(checkpoint_dir, checkpoint_id, timeout_s=0.3) -> bool:
    """Try to take the lock from a separate process; True iff it succeeded."""
    code = _child_code(checkpoint_dir, checkpoint_id, timeout_s)
    rc = subprocess.run([sys.executable, "-c", code], timeout=60).returncode
    assert rc in (0, 3), f"child crashed rc={rc}"
    return rc == 0


def test_lock_excludes_other_process_and_release_frees_it(tmp_path):
    m = CheckpointManager(tmp_path, tenant_id="_default")
    assert m.acquire_lock("cp1", timeout_s=1.0) is True
    assert _other_process_try_lock(tmp_path, "cp1") is False  # held → excluded

    m.release_lock("cp1")
    assert _other_process_try_lock(tmp_path, "cp1") is True  # released → free


def test_waiter_gets_lock_after_release(tmp_path):
    """A process already WAITING on the lock must obtain it once the holder
    releases. The old release_lock unlinked the file without closing the
    descriptor, so the waiter kept spinning on the still-locked old inode and
    timed out."""
    import time

    m = CheckpointManager(tmp_path, tenant_id="_default")
    m.acquire_lock("cpw", timeout_s=1.0)
    waiter = subprocess.Popen(
        [sys.executable, "-c", _child_code(tmp_path, "cpw", 4.0)]
    )
    try:
        time.sleep(1.0)  # waiter has opened the lock file and is spinning
        m.release_lock("cpw")
        assert waiter.wait(timeout=30) == 0
    finally:
        if waiter.poll() is None:
            waiter.kill()


def test_release_is_idempotent_and_reacquire_works(tmp_path):
    m = CheckpointManager(tmp_path, tenant_id="_default")
    m.release_lock("never-held")  # no-op
    m.acquire_lock("cp2", timeout_s=1.0)
    m.release_lock("cp2")
    m.release_lock("cp2")
    assert m.acquire_lock("cp2", timeout_s=1.0) is True
    m.release_lock("cp2")


def test_double_acquire_in_same_manager_fails_fast(tmp_path):
    m = CheckpointManager(tmp_path, tenant_id="_default")
    m.acquire_lock("cp3", timeout_s=1.0)
    with pytest.raises(CheckpointKeyUnavailable):
        m.acquire_lock("cp3", timeout_s=5.0)
    m.release_lock("cp3")


@pytest.mark.parametrize("bad", ["../../escape", "a/b", "..", "", "x\x00y"])
def test_checkpoint_id_cannot_escape_dir(tmp_path, bad):
    m = CheckpointManager(tmp_path / "cps", tenant_id="_default")
    with pytest.raises(ValueError):
        m.acquire_lock(bad, timeout_s=0.1)
