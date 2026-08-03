"""2026-08-03, reported live: a real Windows install's audit.jsonl
accumulated 1092 scattered hash-chain breaks over its history and
eventually hit a hard, non-recoverable boot failure (ADR-0232/0233
tripwire, no override). Root cause: security_events.py's cross-process
write lock was fcntl.flock(), which forge's own Windows compat shim
(forge/_wincompat.py) intentionally degrades to a no-op — correct for
forge's registry files, but wrong for this specific cross-process,
append-only chain (voice-adapter, the console, and N bridge daemons are
all separate processes writing the same chain, on every platform,
Windows included). Two racing writers with no real lock can both read
the same prev_hash and both append a record claiming to follow it — a
silent, permanent hash-chain fork.

These tests pin the fix: security_events._lock_chain/_unlock_chain use
real msvcrt-based locking when msvcrt is importable (branches on
capability, not sys.platform, so the Windows path is testable here via a
fake msvcrt module) and fall back to the original fcntl.flock behaviour
otherwise (POSIX, unchanged).
"""
from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge import security_events as se  # noqa: E402


def _fake_msvcrt():
    """A minimal stand-in for the Windows-only msvcrt module, recording
    every locking() call so tests can assert on mode/byte-count without a
    real Windows box."""
    mod = types.ModuleType("msvcrt")
    mod.LK_LOCK = 1
    mod.LK_UNLCK = 3
    mod.calls = []

    def _locking(fd, mode, nbytes):
        mod.calls.append((fd, mode, nbytes))

    mod.locking = _locking
    return mod


def test_lock_chain_uses_msvcrt_when_available():
    fake = _fake_msvcrt()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "audit.jsonl"
        p.write_text("existing content\n")
        with p.open("r+b") as fh:
            with mock.patch.object(se, "msvcrt", fake):
                se._lock_chain(fh)
                se._unlock_chain(fh)
    assert len(fake.calls) == 2
    (fd1, mode1, n1), (fd2, mode2, n2) = fake.calls
    assert mode1 == fake.LK_LOCK
    assert mode2 == fake.LK_UNLCK
    # Always a single, fixed byte at a well-known offset — a pure mutex
    # indicator, not a lock over the actual data.
    assert n1 == 1 and n2 == 1


def test_lock_chain_restores_file_position_around_the_msvcrt_call():
    """The lock/unlock helpers must not perturb the caller's read/write
    position — critical since write_event() appends immediately after
    locking, and a stray seek(0) left in place would corrupt the chain by
    writing over existing records instead of appending."""
    fake = _fake_msvcrt()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "audit.jsonl"
        p.write_bytes(b"0123456789")
        with p.open("r+b") as fh:
            fh.seek(7)
            with mock.patch.object(se, "msvcrt", fake):
                se._lock_chain(fh)
                assert fh.tell() == 7, "position must be restored after lock"
                se._unlock_chain(fh)
                assert fh.tell() == 7, "position must be restored after unlock"


def test_lock_chain_works_on_an_empty_file():
    """A fresh/empty chain (nothing written yet) must not raise — locking
    byte 0 beyond EOF is the whole point of this idiom."""
    fake = _fake_msvcrt()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "audit.jsonl"
        p.touch()
        with p.open("r+b") as fh:
            with mock.patch.object(se, "msvcrt", fake):
                se._lock_chain(fh)
                se._unlock_chain(fh)
    assert len(fake.calls) == 2


def test_lock_chain_falls_back_to_fcntl_when_msvcrt_unavailable():
    """Regression guard: on a real POSIX box (msvcrt genuinely absent),
    behaviour must be byte-identical to before this fix — real
    fcntl.flock, not a silent no-op."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "audit.jsonl"
        p.write_text("x\n")
        with p.open("r+b") as fh:
            fd = fh.fileno()
            with mock.patch.object(se, "msvcrt", None), \
                 mock.patch.object(se, "fcntl") as fake_fcntl:
                fake_fcntl.LOCK_EX = 2
                fake_fcntl.LOCK_SH = 1
                fake_fcntl.LOCK_UN = 8
                se._lock_chain(fh)
                se._unlock_chain(fh)
            fake_fcntl.flock.assert_any_call(fd, 2)
            fake_fcntl.flock.assert_any_call(fd, 8)


def test_lock_chain_shared_still_uses_flock_lock_sh_on_posix():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "audit.jsonl"
        p.write_text("x\n")
        with p.open("r+b") as fh:
            fd = fh.fileno()
            with mock.patch.object(se, "msvcrt", None), \
                 mock.patch.object(se, "fcntl") as fake_fcntl:
                fake_fcntl.LOCK_EX = 2
                fake_fcntl.LOCK_SH = 1
                se._lock_chain(fh, shared=True)
            fake_fcntl.flock.assert_called_once_with(fd, 1)


def test_write_event_end_to_end_with_fake_msvcrt_lock_path():
    """Real write_event() call, forced through the msvcrt branch, must
    still produce a correctly hash-chained, verifiable record — proves the
    lock swap didn't change the actual write/verify contract, only which
    OS primitive guards it."""
    fake = _fake_msvcrt()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "audit.jsonl"
        with mock.patch.object(se, "msvcrt", fake):
            se.write_event(p, "test.event_one", details={"k": "v"})
            se.write_event(p, "test.event_two", details={"k": "v2"})
        ok, problems = se.verify_chain(p)
    assert ok, problems
    assert len(fake.calls) == 4  # lock+unlock per write, two writes


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
