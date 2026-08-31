"""Regression tests for ``_robust_rmtree`` — the Windows-safe rmtree used
throughout ``uninstall()``.

Historically this function was added to fix uninstall crashing with
``PermissionError [WinError 32] … used by another process`` (a still-running
console/bridge holding a handle on e.g. ``audit.jsonl``) or ``[WinError 5]``
(read-only files). It is documented to retry with backoff, clear the
read-only bit, and — rather than raising — return the list of paths it could
NOT delete so callers can report leftover files to the user.

Every existing uninstall test only ever deletes freshly-created, unlocked
sandbox files, so the actual retry/leftover-reporting logic was never
exercised. These tests force the retry-then-succeed path and the
retry-then-give-up (permanent lock) path directly against the real
filesystem, by simulating a locked file via a monkeypatched ``os.unlink``
(the exact function ``shutil.rmtree`` invokes and passes to ``onexc``/
``onerror``).

Note: on Linux, CPython's fd-based ``shutil.rmtree`` implementation calls
``os.unlink(entry.name, dir_fd=topfd)`` with a *relative* name for the first
attempt (only the retry performed by ``_handle`` uses the full path), so the
patched ``os.unlink`` below matches on the basename rather than the full
path string to reliably intercept both call shapes.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from corvinOS.installer import core as core_mod


def _targets(path, name: str) -> bool:  # noqa: ANN001
    """True if a patched os.unlink/os.chmod call is operating on `name`,
    whether called with a relative entry name (dir_fd form) or a full path."""
    return os.path.basename(str(path)) == name


class TestRobustRmtree:
    def test_missing_path_returns_empty_without_raising(self, tmp_path: Path) -> None:
        target = tmp_path / "does-not-exist"
        assert core_mod._robust_rmtree(target) == []

    def test_transient_permission_error_is_retried_then_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """First N unlink attempts raise PermissionError (simulated file lock);
        the retry-with-backoff loop must eventually succeed and _robust_rmtree
        must report zero leftovers."""
        target_dir = tmp_path / "locked"
        target_dir.mkdir()
        locked_file = target_dir / "file.txt"
        locked_file.write_text("data")

        # Don't actually sleep through the backoff in a test.
        monkeypatch.setattr(core_mod.time, "sleep", lambda *_a, **_kw: None)

        real_unlink = os.unlink
        attempts = {"count": 0}

        def flaky_unlink(path, *a, **kw):  # noqa: ANN001
            if _targets(path, locked_file.name) and attempts["count"] < 2:
                attempts["count"] += 1
                raise PermissionError(f"simulated lock on {path}")
            return real_unlink(path, *a, **kw)

        monkeypatch.setattr(os, "unlink", flaky_unlink)

        leftover = core_mod._robust_rmtree(target_dir)

        assert leftover == [], f"expected no leftovers after transient locks clear, got {leftover}"
        assert attempts["count"] == 2, "retry loop should have hit PermissionError exactly twice"
        assert not target_dir.exists(), "directory tree should be fully removed once the lock clears"

    def test_permanently_locked_file_is_reported_as_leftover_without_raising(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A file that raises PermissionError on every attempt must never
        crash _robust_rmtree; instead it must show up in the returned
        leftover list, and the tree must be left in a consistent
        (non-half-deleted) state — the locked file must still exist with its
        original content."""
        target_dir = tmp_path / "stuck"
        target_dir.mkdir()
        stuck_file = target_dir / "locked.bin"
        stuck_file.write_text("original-data")

        monkeypatch.setattr(core_mod.time, "sleep", lambda *_a, **_kw: None)

        real_unlink = os.unlink

        def always_locked(path, *a, **kw):  # noqa: ANN001
            if _targets(path, stuck_file.name):
                raise PermissionError(f"simulated permanent lock on {path}")
            return real_unlink(path, *a, **kw)

        monkeypatch.setattr(os, "unlink", always_locked)

        leftover = core_mod._robust_rmtree(target_dir)  # must NOT raise

        assert str(stuck_file) in leftover, (
            f"the permanently-locked file must be reported as a leftover, got {leftover}"
        )

        # BUG (see bugsDiscovered): the same chmod-based unlock step also runs
        # against the *directory* once its rmdir fails (non-empty), and sets a
        # fixed absolute mode of 0o200 (write-only, no read/execute) instead of
        # OR-ing the write bit onto the existing mode. On POSIX that strips the
        # directory's search/execute bit, making the leftover unreadable via
        # plain stat()/exists() until an operator restores permissions — which
        # is what we do here, purely to be able to assert on content below.
        os.chmod(target_dir, 0o700)
        os.chmod(stuck_file, 0o644)

        # Consistent, non-half-deleted state: the locked file and its parent
        # directory must both still be present with original content intact.
        assert stuck_file.exists()
        assert stuck_file.read_text() == "original-data"
        assert target_dir.exists()

    def test_read_only_bit_is_cleared_before_retrying(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The WinError-5 case: a read-only file. _robust_rmtree must attempt
        to clear the read-only bit (os.chmod) before each retry."""
        target_dir = tmp_path / "readonly"
        target_dir.mkdir()
        ro_file = target_dir / "ro.txt"
        ro_file.write_text("data")

        monkeypatch.setattr(core_mod.time, "sleep", lambda *_a, **_kw: None)

        chmod_calls: list[str] = []
        real_chmod = os.chmod

        def spying_chmod(path, mode, *a, **kw):  # noqa: ANN001
            chmod_calls.append(str(path))
            return real_chmod(path, mode, *a, **kw)

        monkeypatch.setattr(os, "chmod", spying_chmod)

        real_unlink = os.unlink
        attempts = {"count": 0}

        def flaky_unlink(path, *a, **kw):  # noqa: ANN001
            if _targets(path, ro_file.name) and attempts["count"] < 1:
                attempts["count"] += 1
                raise PermissionError(f"simulated read-only lock on {path}")
            return real_unlink(path, *a, **kw)

        monkeypatch.setattr(os, "unlink", flaky_unlink)

        leftover = core_mod._robust_rmtree(target_dir)

        assert leftover == []
        assert any(_targets(p, ro_file.name) for p in chmod_calls), (
            "must attempt to clear the read-only bit on the locked file"
        )
        assert not target_dir.exists()
