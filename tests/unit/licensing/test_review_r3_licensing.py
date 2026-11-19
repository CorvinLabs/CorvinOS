"""Third round of the 2026-11-19 licensing review (R3-CON-1..5, -14).

Each test names the defect it pins.

License: Apache-2.0
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading

import pytest

from core.licensing.a2a_verifier import (
    A2ADelegationVerifier,
    RevocationList,
    StoreLock,
    TaskReplayStore,
)
from core.licensing.authority_server import AuthorityServer
from core.licensing.member_credential import (
    RSAKeyPair,
    SignedTask,
    task_signing_bytes,
)

from .test_review_2026_11_19_licensing import _now_iso, _signed_task  # noqa: F401


@pytest.fixture
def home(tmp_path):
    return tmp_path / "tenant-root"


# ------------------------------------------- R3-CON-1 stale instance, no file

def test_instance_that_never_saw_the_list_fails_closed_once_it_is_deleted(home):
    verifier = A2ADelegationVerifier(str(home))        # no crl.json yet
    authority = AuthorityServer(str(home))
    res = authority.issue_credential("alice", "enterprise")
    assert authority.revoke_credential(res.credential.credential_id, "x")
    (home / "licensing" / "crl.json").unlink()

    verdict = verifier.verify_signed_task(
        _signed_task(res.private_key_pem, res.credential, task_id="stale"))
    assert verdict.is_valid is False                   # used to be True
    assert verifier.revocation_list.load_failed is True


def test_instance_built_before_the_first_list_sees_a_later_revocation(home):
    crl = RevocationList(str(home))
    assert crl.is_revoked("a") is False
    RevocationList(str(home)).revoke("a", "r")
    assert crl.is_revoked("a") is True


# ---------------------------------------------- R3-CON-2 siblings of a member

def test_downgrade_revokes_every_higher_credential_of_the_member(home):
    authority = AuthorityServer(str(home))
    verifier = A2ADelegationVerifier(str(home))
    first = authority.issue_credential("bob", "enterprise")
    second = authority.issue_credential("bob", "enterprise")   # independent sibling
    low = authority.issue_credential("bob", "free")            # not above the target
    other = authority.issue_credential("carol", "enterprise")  # another member
    a, b = first.credential, second.credential

    res = authority.downgrade_tier(b.credential_id, "free")
    assert res.success, res.error_message

    crl = authority.revocation_list
    assert crl.is_revoked(a.credential_id), "the sibling stayed enterprise"
    assert crl.is_revoked(b.credential_id)
    assert not crl.is_revoked(low.credential.credential_id)
    assert not crl.is_revoked(other.credential.credential_id)
    assert verifier.verify_signed_task(
        _signed_task(first.private_key_pem, a, task_id="sib")).is_valid is False
    again = authority.renew_credential(a.credential_id)
    assert again.success is False and "revoked" in again.error_message


def test_renew_revokes_the_credential_it_replaces(home):
    authority = AuthorityServer(str(home))
    verifier = A2ADelegationVerifier(str(home))
    first = authority.issue_credential("bob", "enterprise")
    a = first.credential
    renewed = authority.renew_credential(a.credential_id)
    assert renewed.success, renewed.error_message
    b = renewed.credential
    assert authority.revocation_list.is_revoked(a.credential_id)
    assert verifier.verify_signed_task(
        _signed_task(first.private_key_pem, b, task_id="new")).is_valid
    # The reviewer's sequence: A -> renew -> B, B -> free, renew A again.
    assert authority.downgrade_tier(b.credential_id, "free").success
    assert authority.renew_credential(a.credential_id).success is False
    tiers = {
        c["license_tier"] for c in authority.list_member_credentials("bob")
        if not c["is_revoked"]
    }
    assert tiers == {"free"}


def test_renew_with_a_bad_validity_does_not_revoke_the_old_credential(home):
    authority = AuthorityServer(str(home))
    cred = authority.issue_credential("bob", "member").credential
    res = authority.renew_credential(cred.credential_id, validity_days=-1)
    assert res.success is False
    assert not authority.revocation_list.is_revoked(cred.credential_id)


def test_concurrent_renew_and_downgrade_never_leave_the_old_tier(home):
    authority = AuthorityServer(str(home))
    cred = authority.issue_credential("bob", "enterprise").credential
    other = AuthorityServer(str(home))                 # e.g. a second worker
    barrier = threading.Barrier(2)
    out = {}

    def run(name, fn):
        barrier.wait()
        out[name] = fn()

    threads = [
        threading.Thread(target=run, args=("renew", lambda: authority.renew_credential(cred.credential_id))),
        threading.Thread(target=run, args=("down", lambda: other.downgrade_tier(cred.credential_id, "free"))),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    # Exactly one of them acted on the credential; the loser saw "revoked".
    assert sorted(r.success for r in out.values()) == [False, True]
    live = [
        c for c in authority.list_member_credentials("bob") if not c["is_revoked"]
    ]
    assert len(live) == 1
    if out["down"].success:
        assert live[0]["license_tier"] == "free"


# ------------------------------------------- R3-CON-3 honest partial failure

def test_downgrade_that_revoked_but_could_not_store_says_so(home, monkeypatch):
    authority = AuthorityServer(str(home))
    cred = authority.issue_credential("bob", "enterprise").credential

    def boom(_credential):
        raise OSError("disk full")

    monkeypatch.setattr(authority.credential_store, "save", boom)
    res = authority.downgrade_tier(cred.credential_id, "free")
    assert res.success is False
    assert "old credential is revoked; issue a new one" in res.error_message
    assert authority.revocation_list.is_revoked(cred.credential_id)


def test_downgrade_that_failed_before_revoking_does_not_claim_a_revocation(home, monkeypatch):
    authority = AuthorityServer(str(home))
    cred = authority.issue_credential("bob", "enterprise").credential

    def boom(*_a, **_k):
        raise RuntimeError("chain unavailable")

    monkeypatch.setattr(authority, "_audit_downgrade", boom)
    res = authority.downgrade_tier(cred.credential_id, "free")
    assert res.success is False
    assert "revoked" not in res.error_message
    assert not authority.revocation_list.is_revoked(cred.credential_id)


def test_renew_that_revoked_but_could_not_issue_says_so(home, monkeypatch):
    authority = AuthorityServer(str(home))
    cred = authority.issue_credential("bob", "member").credential

    def boom(_credential):
        raise OSError("disk full")

    monkeypatch.setattr(authority.credential_store, "save", boom)
    res = authority.renew_credential(cred.credential_id)
    assert res.success is False
    assert "old credential is revoked; issue a new one" in res.error_message


# ------------------------------------------------- R3-CON-4 lost revocations

_REVOKE_MANY = """
import sys
from core.licensing.a2a_verifier import RevocationList
crl = RevocationList(sys.argv[1])
for i in range(25):
    crl.revoke(f"{sys.argv[2]}-{i}", "r")
"""


def test_revocations_from_two_processes_are_all_kept(home):
    RevocationList(str(home)).revoke("seed", "r")
    procs = [
        subprocess.Popen([sys.executable, "-c", _REVOKE_MANY, str(home), name])
        for name in ("p", "q")
    ]
    for p in procs:
        assert p.wait(120) == 0
    stored = json.loads((home / "licensing" / "crl.json").read_text())["revoked"]
    assert len(stored) == 51, "a concurrent save dropped another writer's entry"


def test_revocations_from_two_instances_in_threads_are_all_kept(home):
    lists = [RevocationList(str(home)) for _ in range(4)]
    threads = [
        threading.Thread(target=lambda c=c, n=n: [c.revoke(f"t{n}-{i}", "r") for i in range(20)])
        for n, c in enumerate(lists)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert len(RevocationList(str(home)).revoked) == 80


def test_snapshot_is_a_copy(home):
    crl = RevocationList(str(home))
    crl.revoke("a", "r")
    failed, entries = crl.snapshot()
    assert failed is False and [k for k, _ in entries] == ["a"]
    entries[0][1]["reason"] = "edited"
    assert crl.get_revocation_reason("a") == "r"


# ------------------------------------------------------- R3-CON-5 inf TTL

@pytest.mark.parametrize("ttl", [float("inf"), float("-inf"), float("nan"), "x", None])
def test_unusable_ttl_is_zero_not_an_exception(home, ttl):
    task = SignedTask(
        task_id="t", member_id="m", credential_id="c", operation="op",
        payload={}, signed_at=_now_iso(), signature="", ttl_seconds=ttl,
    )
    assert task.effective_ttl_seconds() == 0
    assert task.is_expired() in (True, False)          # never raises


def test_verifier_denies_an_infinite_ttl_instead_of_raising(home):
    authority = AuthorityServer(str(home))
    verifier = A2ADelegationVerifier(str(home))
    res = authority.issue_credential("alice", "enterprise")
    task = _signed_task(res.private_key_pem, res.credential, task_id="inf")
    task.ttl_seconds = float("inf")
    key = RSAKeyPair.from_private_pem(res.private_key_pem.encode())
    task.signature = key.sign(task_signing_bytes(task)).hex()
    assert verifier.verify_signed_task(task).is_valid is False


# ------------------------------------- R3-CON-14 replay lock is per tenant

def test_replay_store_lock_is_per_store_not_process_wide(tmp_path):
    a = TaskReplayStore(str(tmp_path / "tenant-a"))
    b = TaskReplayStore(str(tmp_path / "tenant-b"))
    assert a._lock is not b._lock
    assert TaskReplayStore(str(tmp_path / "tenant-a"))._lock is a._lock
    assert not hasattr(TaskReplayStore, "_process_lock")

    done = threading.Event()
    with a._lock:                                      # tenant A is busy
        t = threading.Thread(target=lambda: (b.claim("c", "t", 4e9), done.set()))
        t.start()
        assert done.wait(10), "tenant B waited for tenant A's lock"
    t.join()
    assert a.claim("c", "t", 4e9) is True
    assert a.claim("c", "t", 4e9) is False


def test_store_lock_is_reentrant_for_its_holder(tmp_path):
    lock = StoreLock.for_file(tmp_path / ".l")
    with lock:
        with lock:
            assert lock._depth == 2
    assert lock._depth == 0 and lock._fd is None
