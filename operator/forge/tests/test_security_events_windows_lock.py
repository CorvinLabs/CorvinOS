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

import os
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge import security_events as se  # noqa: E402


def _fake_msvcrt():
    """A minimal stand-in for the Windows-only msvcrt module, recording
    every locking() call so tests can assert on mode/byte-count without a
    real Windows box. Also records the fd's offset at call time, since
    msvcrt.locking() locks relative to the current position and WHICH byte
    gets locked is itself security-relevant (see the offset test below)."""
    mod = types.ModuleType("msvcrt")
    mod.LK_LOCK = 1
    mod.LK_UNLCK = 3
    mod.calls = []
    mod.offsets = []

    def _locking(fd, mode, nbytes):
        mod.offsets.append(os.lseek(fd, 0, os.SEEK_CUR))
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


def test_lock_chain_sentinel_byte_is_far_beyond_any_real_record():
    """2026-08-06, reported live (Discord, CLAG L22.engine_spawn
    audit_write_failed): the mutex byte must NOT sit inside the data.

    Windows LockFile ranges are MANDATORY and enforced per-HANDLE, so a
    locked byte is unreadable even by the locking process through a second
    handle. write_event() locks, then calls _last_hash(), which re-opens the
    chain and — on a chain smaller than _TAIL_BLOCK — starts its tail scan at
    offset 0. With the sentinel at offset 0 that read hits our own lock and
    raises PermissionError [Errno 13], so after the first record no
    hash-chained write can ever succeed again. Pin the sentinel past the EOF
    of any chain that could exist."""
    fake = _fake_msvcrt()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "audit.jsonl"
        p.write_bytes(b"x" * 4096)
        with p.open("r+b") as fh:
            with mock.patch.object(se, "msvcrt", fake):
                se._lock_chain(fh)
                se._unlock_chain(fh)
    assert fake.offsets == [se._LOCK_SENTINEL_OFFSET] * 2
    # Comfortably past _TAIL_BLOCK — the scan window _last_hash() reads while
    # the lock is held — and past any plausible chain size.
    assert se._LOCK_SENTINEL_OFFSET > se._TAIL_BLOCK
    assert se._LOCK_SENTINEL_OFFSET >= 1 << 40


@pytest.mark.skipif(sys.platform != "win32",
                    reason="mandatory byte-range locks are a Windows-only semantic")
def test_write_event_on_a_short_chain_does_not_self_deadlock_on_windows():
    """The live failure, end to end, against the REAL msvcrt: a chain with a
    single record (well under _TAIL_BLOCK) must accept another hash-chained
    append. Before the fix this raised PermissionError [Errno 13] from the
    read inside _last_hash(), while the same write succeeded on a chain
    larger than _TAIL_BLOCK and with hash_chain=False — which is exactly why
    it read as an intermittent permissions problem rather than a lock bug."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "audit.jsonl"
        se.write_event(p, "test.first", details={"k": "v"})   # empty chain
        assert p.stat().st_size < se._TAIL_BLOCK
        se.write_event(p, "test.second", details={"k": "v2"})  # short chain
        ok, problems = se.verify_chain(p)
    assert ok, problems


@pytest.mark.skipif(sys.platform != "win32",
                    reason="mandatory byte-range locks are a Windows-only semantic")
def test_verify_under_a_held_read_lock_can_still_read_the_chain_on_windows():
    """audit_health_check() takes _lock_chain(shared=True) on a read handle
    and then calls verify_chain(), which re-opens and reads the file. With
    the sentinel inside the data that re-read raised PermissionError and the
    bridge logged `verify_errored / PermissionError` on every boot."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "audit.jsonl"
        se.write_event(p, "test.first", details={"k": "v"})
        with p.open("r") as lf:
            se._lock_chain(lf, shared=True)
            try:
                ok, problems = se.verify_chain(p)
            finally:
                se._unlock_chain(lf)
    assert ok, problems


@pytest.mark.skipif(sys.platform != "win32",
                    reason="mandatory byte-range locks are a Windows-only semantic")
def test_sentinel_lock_still_excludes_a_second_handle_on_windows():
    """Moving the sentinel must not weaken what the lock is FOR: a second
    handle to the same chain still cannot take it while it is held."""
    import msvcrt as real_msvcrt
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "audit.jsonl"
        p.touch()
        with p.open("r+b") as held:
            se._lock_chain(held)
            try:
                with p.open("r+b") as other:
                    fd = other.fileno()
                    os.lseek(fd, se._LOCK_SENTINEL_OFFSET, os.SEEK_SET)
                    with pytest.raises(OSError):
                        real_msvcrt.locking(fd, real_msvcrt.LK_NBLCK, 1)
            finally:
                se._unlock_chain(held)


def test_lock_chain_works_on_an_empty_file():
    """A fresh/empty chain (nothing written yet) must not raise — locking a
    byte beyond EOF is the whole point of this idiom."""
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


def test_write_event_self_heals_from_a_stale_permission_denied():
    """2026-08-04, live report: LSAD (ADR-0132) and CLAG (ADR-0133) -- both
    callers of write_event() -- logged "Permission denied" on a real
    Windows install's audit.jsonl. os.chmod cannot set POSIX mode bits on
    Windows (only toggles the read-only attribute), so a file left behind
    read-only by an interrupted prior write / different security context /
    AV quarantine made every subsequent append fail forever with no
    self-heal. Simulated here with a real 0o400 (no write bit) file --
    genuinely triggers PermissionError on POSIX too -- with msvcrt faked
    present so the Windows retry branch is exercised."""
    fake = _fake_msvcrt()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "audit.jsonl"
        p.write_text("")
        os.chmod(p, 0o400)
        with mock.patch.object(se, "msvcrt", fake):
            rec = se.write_event(p, "test.event", details={"k": "v"})
        assert rec["event_type"] == "test.event"
        ok, problems = se.verify_chain(p)
    assert ok, problems


def test_write_event_does_not_retry_permission_denied_on_posix():
    """Regression guard: on a real POSIX box (msvcrt genuinely absent), a
    genuine permission problem must propagate unchanged -- no silent
    chmod-and-retry masking a real POSIX permissions issue that this
    self-heal was never meant to paper over."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "audit.jsonl"
        p.write_text("")
        os.chmod(p, 0o400)
        try:
            with mock.patch.object(se, "msvcrt", None):
                with pytest.raises(PermissionError):
                    se.write_event(p, "test.event", details={"k": "v"})
        finally:
            os.chmod(p, 0o600)  # so tempdir cleanup can remove it


def test_write_event_reraises_when_the_file_was_never_created():
    """The self-heal only ever chmods an EXISTING file -- if the target was
    never created at all (e.g. a genuine ACL/permissions problem on the
    parent directory, not a stale attribute on the file itself), there is
    nothing to heal and the original error must still surface, not loop or
    swallow it."""
    fake = _fake_msvcrt()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "never-created" / "audit.jsonl"
        with mock.patch.object(se, "msvcrt", fake), \
             mock.patch.object(se.os, "open", side_effect=PermissionError(13, "Permission denied")):
            with pytest.raises(PermissionError):
                se.write_event(p, "test.event", details={"k": "v"})


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
