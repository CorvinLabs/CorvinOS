"""The tenant's default personal space: created on first use, opened by the device key alone, never twice."""
from __future__ import annotations

import threading

import pytest

from core.personal_kb import owner, paths
from core.personal_kb.store import PersonalStore


def test_first_use_creates_one_space_with_a_device_wrap_only(home):
    oid = owner.ensure_default_owner("_default")
    info = owner.owner_info("_default", oid)
    assert (info["default"], info["has_passphrase"], info["has_device"]) == (True, False, True)
    assert owner.find_default("_default") == oid
    assert owner.ensure_default_owner("_default") == oid          # idempotent


def test_concurrent_first_requests_create_exactly_one_space(home):
    ids: list[str] = []

    def go() -> None:
        ids.append(owner.ensure_default_owner("_default"))
    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(set(ids)) == 1
    assert owner.list_owner_ids("_default") == [ids[0]]


def test_unlock_default_opens_the_store_and_learning_starts_off(home):
    oid, keys = owner.unlock_default("_default")
    s = PersonalStore.open("_default", oid, keys)
    assert s.meta["auto_capture"] is False
    assert s.meta["consent"]["version"] == owner.DEFAULT_CONSENT_VERSION


def test_a_missing_device_key_is_a_refusal_not_a_new_space(home):
    oid = owner.ensure_default_owner("_default")
    for f in paths.device_key_files("_default", oid):
        f.unlink()
    with pytest.raises(owner.UnlockFailed):
        owner.unlock_default("_default")
    assert owner.list_owner_ids("_default") == [oid]               # no silent replacement of an unreadable space


def test_a_passphrase_space_is_never_taken_for_the_default(home):
    created = owner.create_owner("_default", "anna", "Correct-Horse-Battery-1", consent=True, unlock_mode="strict")
    assert owner.find_default("_default") is None
    oid = owner.ensure_default_owner("_default")
    assert oid != created.owner_id
    assert sorted(owner.list_owner_ids("_default")) == sorted([oid, created.owner_id])


def test_the_operator_cli_can_erase_the_default_space(home, capsys):
    from core.personal_kb import cli

    oid = owner.ensure_default_owner("_default")
    assert cli.main(["erase", "--tenant", "_default", "--default"]) == 0
    assert not paths.owner_dir("_default", oid).exists()
    assert cli.main(["erase", "--tenant", "_default", "--default"]) == 2     # nothing left to erase
