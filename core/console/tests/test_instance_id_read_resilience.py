"""load_or_create_instance_id must not fork the persistent instance identity
on a transient read failure (2026-08-02).

Live-investigated user report: "every new PyPI version adds a new entry in
the same region on corvin-labs.com/stats" -- i.e. the anonymous ping's
instance_id appears to change on upgrade. Two real, end-to-end version
upgrades (0.10.85 -> 0.10.97, same CORVIN_HOME, both with and without an
explicit CORVIN_HOME env var) reproduced a STABLE id -- the happy path is
correct. This file covers the one real gap found while investigating:
`except (OSError, ValueError): pass` treated "file exists but a transient
OS-level error prevented reading it right now" (e.g. Windows AV/indexer
locking a just-touched file -- most likely right after an install/upgrade
writes a burst of new files) identically to "no file yet", silently
overwriting the real instance_id with a fresh one and permanently forking
the identity from then on.

Run: python3 -m pytest core/console/tests/test_instance_id_read_resilience.py
"""
from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from corvin_console.aco.htrace_consent import load_or_create_instance_id


class TestFirstRunAndStability:
    def test_no_file_yet_creates_and_persists(self, tmp_path: Path) -> None:
        home = tmp_path
        iid = load_or_create_instance_id(home)
        uuid.UUID(iid, version=4)  # valid uuid4
        assert (home / "instance_id").read_text(encoding="utf-8").strip() == iid

    def test_second_call_returns_the_same_persisted_id(self, tmp_path: Path) -> None:
        home = tmp_path
        first = load_or_create_instance_id(home)
        second = load_or_create_instance_id(home)
        assert first == second

    def test_stable_across_a_real_version_boundary_simulation(self, tmp_path: Path) -> None:
        """The exact scenario reported: call once (simulates the pre-update
        version), then again (simulates the post-update version) against
        the SAME home dir -- must be the identical id both times."""
        home = tmp_path
        pre_update = load_or_create_instance_id(home)
        post_update = load_or_create_instance_id(home)
        assert pre_update == post_update


class TestCorruptContentSelfHeals:
    def test_garbage_content_mints_a_fresh_valid_id(self, tmp_path: Path) -> None:
        home = tmp_path
        (home / "instance_id").write_text("not-a-uuid-at-all", encoding="utf-8")
        iid = load_or_create_instance_id(home)
        uuid.UUID(iid, version=4)

    def test_wrong_uuid_version_mints_a_fresh_valid_id(self, tmp_path: Path) -> None:
        home = tmp_path
        # A version-1 (time-based) UUID is well-formed but not what this
        # function accepts -- must be treated as invalid, not trusted.
        v1 = uuid.uuid1()
        (home / "instance_id").write_text(str(v1), encoding="utf-8")
        iid = load_or_create_instance_id(home)
        assert iid != str(v1)
        uuid.UUID(iid, version=4)


class TestTransientReadFailureDoesNotForkIdentity:
    """The actual bug: a real file, unreadable for one call only, must
    never be overwritten with a new id -- it must self-heal back to the
    real one on the very next call."""

    def test_permission_error_does_not_overwrite_the_real_file(self, tmp_path: Path) -> None:
        home = tmp_path
        real_id = load_or_create_instance_id(home)  # establishes the real file

        with patch.object(Path, "read_text", side_effect=PermissionError("locked")):
            ephemeral = load_or_create_instance_id(home)

        # The ephemeral fallback is a valid uuid4 for this one call...
        uuid.UUID(ephemeral, version=4)
        # ...but the file on disk was NEVER touched by the failed call.
        on_disk = (home / "instance_id").read_text(encoding="utf-8").strip()
        assert on_disk == real_id

    def test_next_call_after_a_transient_failure_recovers_the_real_id(self, tmp_path: Path) -> None:
        home = tmp_path
        real_id = load_or_create_instance_id(home)

        with patch.object(Path, "read_text", side_effect=OSError("transient lock")):
            load_or_create_instance_id(home)  # one failed call

        recovered = load_or_create_instance_id(home)  # no patch -- real read
        assert recovered == real_id

    def test_generic_oserror_is_never_persisted(self, tmp_path: Path) -> None:
        """Windows file-locking surfaces as a bare OSError (or a
        PermissionError/WinError subclass), not necessarily
        FileNotFoundError -- the fix must catch the general case, not just
        one specific subclass."""
        home = tmp_path
        real_id = load_or_create_instance_id(home)

        with patch.object(Path, "read_text", side_effect=OSError(13, "Permission denied")):
            load_or_create_instance_id(home)

        on_disk = (home / "instance_id").read_text(encoding="utf-8").strip()
        assert on_disk == real_id

    def test_file_genuinely_absent_still_creates_and_persists(self, tmp_path: Path) -> None:
        """The FileNotFoundError path (genuinely no file yet) must still
        work exactly as before -- only the "exists but unreadable" case
        changed behaviour."""
        home = tmp_path
        assert not (home / "instance_id").exists()
        iid = load_or_create_instance_id(home)
        assert (home / "instance_id").exists()
        uuid.UUID(iid, version=4)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
