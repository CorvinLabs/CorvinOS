"""Tests for Layer 41 GrantStore — SQLite grant CRUD and rate limiting."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from grant_store import GrantStore, GrantStoreError, grant_db_path


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def store(tmp_path):
    return GrantStore(tmp_path / "grants.db")


def _doc(**overrides) -> dict:
    base = {
        "grant_id": "grnt_aabbccdd11223344",
        "schema_version": 1,
        "grantor_actor": "@silvio@corvin.sh",
        "grantee_actor": "@alice@other.instance",
        "capabilities": ["domain.research.read"],
        "conditions": {},
        "issued_at": int(time.time()),
        "revoked_at": None,
        "signature": "ed25519:deadbeef",
    }
    base.update(overrides)
    return base


# ── save_grant / get_grant ────────────────────────────────────────────────────


def test_save_and_get(store):
    doc = _doc()
    store.save_grant(doc)
    got = store.get_grant(doc["grant_id"])
    assert got is not None
    assert got["grant_id"] == doc["grant_id"]
    assert got["capabilities"] == ["domain.research.read"]


def test_get_missing_returns_none(store):
    assert store.get_grant("grnt_doesnotexist") is None


def test_save_collision_raises(store):
    doc = _doc()
    store.save_grant(doc)
    with pytest.raises(GrantStoreError, match="collision"):
        store.save_grant(doc)


# ── revocation ────────────────────────────────────────────────────────────────


def test_set_revoked(store):
    doc = _doc()
    store.save_grant(doc)
    result = store.set_revoked(doc["grant_id"])
    assert result is True
    # Should not appear in active list
    active = store.list_grants(grantee_actor="@alice@other.instance")
    assert not active


def test_set_revoked_missing_returns_false(store):
    assert store.set_revoked("grnt_doesnotexist") is False


def test_revoke_all_for_actor(store):
    store.save_grant(_doc(grant_id="grnt_0000000000000001"))
    store.save_grant(
        _doc(
            grant_id="grnt_0000000000000002",
            grantee_actor="@bob@other.instance",
        )
    )
    count = store.revoke_all_for_actor("@alice@other.instance")
    assert count == 1
    remaining = store.list_grants()
    assert len(remaining) == 1  # bob's grant still active


# ── list_grants ───────────────────────────────────────────────────────────────


def test_list_grants_by_grantee(store):
    store.save_grant(_doc(grant_id="grnt_0000000000000001"))
    store.save_grant(
        _doc(
            grant_id="grnt_0000000000000002",
            grantee_actor="@bob@other.instance",
        )
    )
    alice_grants = store.list_grants(grantee_actor="@alice@other.instance")
    assert len(alice_grants) == 1
    assert alice_grants[0]["grantee_actor"] == "@alice@other.instance"


def test_list_grants_include_revoked(store):
    doc = _doc()
    store.save_grant(doc)
    store.set_revoked(doc["grant_id"])

    active = store.list_grants()
    assert len(active) == 0

    all_grants = store.list_grants(include_revoked=True)
    assert len(all_grants) == 1


def test_wildcard_grantee_listed(store):
    store.save_grant(_doc(grant_id="grnt_0000000000000001", grantee_actor="*"))
    wildcards = store.list_grants(grantee_actor="*")
    assert len(wildcards) == 1


# ── rate limiting ─────────────────────────────────────────────────────────────


def test_parse_rate_limit_valid(store):
    assert store.parse_rate_limit("10/minute") == (10, 60)
    assert store.parse_rate_limit("5/hour") == (5, 3600)
    assert store.parse_rate_limit("100/day") == (100, 86400)
    assert store.parse_rate_limit("1/second") == (1, 1)


def test_parse_rate_limit_invalid(store):
    with pytest.raises(GrantStoreError):
        store.parse_rate_limit("10/week")
    with pytest.raises(GrantStoreError):
        store.parse_rate_limit("nope")
    with pytest.raises(GrantStoreError):
        store.parse_rate_limit("")


def test_rate_limit_allow_and_increment(store):
    doc = _doc(conditions={"rate_limit": "3/day"})
    store.save_grant(doc)
    gid = doc["grant_id"]

    for _ in range(3):
        assert store.check_rate_limit(gid, "3/day") is True
        store.increment_rate_counter(gid, "3/day")

    # Fourth request should be denied
    assert store.check_rate_limit(gid, "3/day") is False


# ── erasure ───────────────────────────────────────────────────────────────────


def test_purge_for_actor(store):
    store.save_grant(_doc(grant_id="grnt_0000000000000001"))
    store.save_grant(
        _doc(
            grant_id="grnt_0000000000000002",
            grantee_actor="@bob@other.instance",
        )
    )
    removed = store.purge_for_actor("@alice@other.instance")
    assert removed == 1
    assert store.get_grant("grnt_0000000000000001") is None
    assert store.get_grant("grnt_0000000000000002") is not None


def test_purge_all(store):
    store.save_grant(_doc(grant_id="grnt_0000000000000001"))
    store.save_grant(_doc(grant_id="grnt_0000000000000002"))
    removed = store.purge_all()
    assert removed == 2
    assert store.list_grants() == []


# ── mode 0600 ─────────────────────────────────────────────────────────────────


def test_db_mode_600(tmp_path):
    db = tmp_path / "grants.db"
    GrantStore(db)
    import stat

    mode = db.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"
