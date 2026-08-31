"""Tests for Layer 41 GrantChecker — capability validation pipeline.

Uses a real GrantStore (SQLite in tmp) and stubs the follower check and
signature verification so tests run without a full L39 social graph.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from grant_store import GrantStore
from grant_issuer import build_grant
from grant_checker import (
    GrantChecker,
    GrantCheckResult,
    OrgResolver,
    _capability_matches,
    _grant_covers_capability,
    _data_class_allowed,
)

# We generate a real Ed25519 keypair for signing in tests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


def _gen_keypair() -> tuple[str, str]:
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_hex = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()).hex()
    pub_hex = pub.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    return priv_hex, pub_hex


LOCAL_ACTOR = "@silvio@corvin.sh"
GRANTEE = "@alice@other.instance"


@pytest.fixture()
def keypair():
    return _gen_keypair()


@pytest.fixture()
def store(tmp_path):
    return GrantStore(tmp_path / "grants.db")


def _make_checker(store, priv_hex, pub_hex, *, is_follower=True, org_resolver=None):
    checker = GrantChecker(
        store=store,
        local_actor_id=LOCAL_ACTOR,
        local_public_key_hex=pub_hex,
        org_resolver=org_resolver,
        tenant_id="_test",
    )
    # Patch the follower check so tests don't need a real social registry
    with patch("grant_checker._is_follower", return_value=is_follower):
        yield checker


def _signed_grant(priv_hex, *, grantee=GRANTEE, capabilities=None, conditions=None):
    return build_grant(
        grantor_actor=LOCAL_ACTOR,
        grantee_actor=grantee,
        capabilities=capabilities or ["domain.research.read"],
        conditions=conditions,
        private_key_hex=priv_hex,
    )


# ── Capability matching helpers ───────────────────────────────────────────────


def test_capability_exact_match():
    assert _capability_matches("domain.research.read", "domain.research.read")


def test_capability_wildcard_middle():
    assert _capability_matches("domain.*.read", "domain.research.read")
    assert _capability_matches("domain.*.read", "domain.blog.read")
    assert not _capability_matches("domain.*.read", "domain.research.write")


def test_capability_wildcard_end():
    assert _capability_matches("agent.invoke.*", "agent.invoke.assistant")
    assert not _capability_matches("agent.invoke.*", "agent.read.assistant")


def test_capability_no_implicit_promotion():
    # A specific grant does NOT match a wildcard request
    assert not _capability_matches("domain.research.read", "domain.*.read")


def test_capability_segment_count_mismatch():
    assert not _capability_matches("domain.read", "domain.research.read")


def test_grant_covers_any_in_list():
    caps = ["domain.research.read", "agent.invoke.*"]
    assert _grant_covers_capability(caps, "domain.research.read")
    assert _grant_covers_capability(caps, "agent.invoke.assistant")
    assert not _grant_covers_capability(caps, "forge.exec.mytool")


# ── Data class ceiling ────────────────────────────────────────────────────────


def test_data_class_ceiling():
    assert _data_class_allowed("CONFIDENTIAL", "PUBLIC")
    assert _data_class_allowed("CONFIDENTIAL", "INTERNAL")
    assert _data_class_allowed("CONFIDENTIAL", "CONFIDENTIAL")
    assert not _data_class_allowed("CONFIDENTIAL", "SECRET")
    assert _data_class_allowed("INTERNAL", "PUBLIC")
    assert _data_class_allowed("INTERNAL", "INTERNAL")
    assert not _data_class_allowed("INTERNAL", "CONFIDENTIAL")
    assert not _data_class_allowed("PUBLIC", "INTERNAL")


# ── GrantChecker full pipeline ────────────────────────────────────────────────


def test_allow_basic(keypair, store):
    priv_hex, pub_hex = keypair
    doc = _signed_grant(priv_hex)
    store.save_grant(doc)

    checker = GrantChecker(
        store=store,
        local_actor_id=LOCAL_ACTOR,
        local_public_key_hex=pub_hex,
        tenant_id="_test",
    )
    with patch("grant_checker._is_follower", return_value=True):
        result = checker.check(GRANTEE, "domain.research.read")

    assert result.allowed is True
    assert result.grant_id == doc["grant_id"]


def test_deny_not_follower(keypair, store):
    priv_hex, pub_hex = keypair
    doc = _signed_grant(priv_hex)
    store.save_grant(doc)

    checker = GrantChecker(
        store=store,
        local_actor_id=LOCAL_ACTOR,
        local_public_key_hex=pub_hex,
        tenant_id="_test",
    )
    with patch("grant_checker._is_follower", return_value=False):
        result = checker.check(GRANTEE, "domain.research.read")

    assert result.allowed is False


def test_deny_capability_not_covered(keypair, store):
    priv_hex, pub_hex = keypair
    doc = _signed_grant(priv_hex, capabilities=["domain.research.read"])
    store.save_grant(doc)

    checker = GrantChecker(
        store=store,
        local_actor_id=LOCAL_ACTOR,
        local_public_key_hex=pub_hex,
        tenant_id="_test",
    )
    with patch("grant_checker._is_follower", return_value=True):
        result = checker.check(GRANTEE, "forge.exec.mytool")

    assert result.allowed is False


def test_deny_ttl_expired(keypair, store):
    priv_hex, pub_hex = keypair
    doc = _signed_grant(
        priv_hex,
        conditions={"valid_until": int(time.time()) - 1},
    )
    store.save_grant(doc)

    checker = GrantChecker(
        store=store,
        local_actor_id=LOCAL_ACTOR,
        local_public_key_hex=pub_hex,
        tenant_id="_test",
    )
    with patch("grant_checker._is_follower", return_value=True):
        result = checker.check(GRANTEE, "domain.research.read")

    assert result.allowed is False


def test_deny_rate_limit_exceeded(keypair, store):
    priv_hex, pub_hex = keypair
    doc = _signed_grant(priv_hex, conditions={"rate_limit": "2/day"})
    store.save_grant(doc)

    checker = GrantChecker(
        store=store,
        local_actor_id=LOCAL_ACTOR,
        local_public_key_hex=pub_hex,
        tenant_id="_test",
    )
    with patch("grant_checker._is_follower", return_value=True):
        r1 = checker.check(GRANTEE, "domain.research.read")
        r2 = checker.check(GRANTEE, "domain.research.read")
        r3 = checker.check(GRANTEE, "domain.research.read")

    assert r1.allowed is True
    assert r2.allowed is True
    assert r3.allowed is False


def test_deny_data_class_ceiling(keypair, store):
    priv_hex, pub_hex = keypair
    doc = _signed_grant(
        priv_hex,
        capabilities=["data.CONFIDENTIAL.read"],
        conditions={"data_class_ceiling": "INTERNAL"},
    )
    store.save_grant(doc)

    checker = GrantChecker(
        store=store,
        local_actor_id=LOCAL_ACTOR,
        local_public_key_hex=pub_hex,
        tenant_id="_test",
    )
    with patch("grant_checker._is_follower", return_value=True):
        result = checker.check(
            GRANTEE, "data.CONFIDENTIAL.read", data_class="CONFIDENTIAL"
        )

    assert result.allowed is False


def test_allow_wildcard_grantee(keypair, store):
    priv_hex, pub_hex = keypair
    doc = _signed_grant(priv_hex, grantee="*")
    store.save_grant(doc)

    checker = GrantChecker(
        store=store,
        local_actor_id=LOCAL_ACTOR,
        local_public_key_hex=pub_hex,
        tenant_id="_test",
    )
    with patch("grant_checker._is_follower", return_value=True):
        result = checker.check("@stranger@any.instance", "domain.research.read")

    assert result.allowed is True


def test_deny_invalid_signature(keypair, store):
    priv_hex, pub_hex = keypair
    doc = _signed_grant(priv_hex)
    doc["signature"] = "ed25519:deadbeefdeadbeef"  # tampered
    store.save_grant(doc)

    checker = GrantChecker(
        store=store,
        local_actor_id=LOCAL_ACTOR,
        local_public_key_hex=pub_hex,
        tenant_id="_test",
    )
    with patch("grant_checker._is_follower", return_value=True):
        result = checker.check(GRANTEE, "domain.research.read")

    assert result.allowed is False


def test_deny_wrong_grantor(keypair, store):
    priv_hex, pub_hex = keypair
    doc = _signed_grant(priv_hex)
    store.save_grant(doc)

    checker = GrantChecker(
        store=store,
        local_actor_id="@someone_else@corvin.sh",  # different local actor
        local_public_key_hex=pub_hex,
        tenant_id="_test",
    )
    with patch("grant_checker._is_follower", return_value=True):
        result = checker.check(GRANTEE, "domain.research.read")

    assert result.allowed is False


def test_no_grants_deny(keypair, store):
    _, pub_hex = keypair
    checker = GrantChecker(
        store=store,
        local_actor_id=LOCAL_ACTOR,
        local_public_key_hex=pub_hex,
        tenant_id="_test",
    )
    with patch("grant_checker._is_follower", return_value=True):
        result = checker.check(GRANTEE, "domain.research.read")

    assert result.allowed is False
    assert result.deny_reason == "no_active_grants"


# ── L42 OrgResolver hook ──────────────────────────────────────────────────────


def test_org_resolver_expansion(keypair, store):
    priv_hex, pub_hex = keypair
    # Grant to the org, not the agent directly
    doc = _signed_grant(priv_hex, grantee="@acme-corp@corvin.sh")
    store.save_grant(doc)

    class FakeOrgResolver:
        def resolve_to_org(self, agent_actor_id: str) -> str | None:
            if agent_actor_id == "@acme-bot@corvin.sh":
                return "@acme-corp@corvin.sh"
            return None

    checker = GrantChecker(
        store=store,
        local_actor_id=LOCAL_ACTOR,
        local_public_key_hex=pub_hex,
        org_resolver=FakeOrgResolver(),
        tenant_id="_test",
    )
    with patch("grant_checker._is_follower", return_value=True):
        result = checker.check("@acme-bot@corvin.sh", "domain.research.read")

    assert result.allowed is True


def test_org_resolver_no_affiliation_denied(keypair, store):
    priv_hex, pub_hex = keypair
    doc = _signed_grant(priv_hex, grantee="@acme-corp@corvin.sh")
    store.save_grant(doc)

    class FakeOrgResolver:
        def resolve_to_org(self, agent_actor_id: str) -> str | None:
            return None  # not affiliated

    checker = GrantChecker(
        store=store,
        local_actor_id=LOCAL_ACTOR,
        local_public_key_hex=pub_hex,
        org_resolver=FakeOrgResolver(),
        tenant_id="_test",
    )
    with patch("grant_checker._is_follower", return_value=True):
        result = checker.check("@random-bot@corvin.sh", "domain.research.read")

    assert result.allowed is False
