"""Layer 42 CorvinOrg — unit tests and E2E integration test.

E2E scenario:
  1. Create org "acme-corp" with owner "@alice@local"
  2. Generate DSK (M1: single-owner)
  3. Affiliate agent "@acme-bot@partner.instance" with scope ["domain.*.read"]
  4. Issue a personal-actor grant: local personal actor grants "@acme-corp" capability
  5. CorvinOrgResolver: "@acme-bot@partner.instance" → "@acme-corp" actor_id
  6. GrantChecker (personal actor): check "@acme-bot@partner.instance" for "domain.blog.read"
     → OrgResolver expands → grant found for "@acme-corp" → ALLOW

Also tests:
  - OrgStore CRUD (members, endorsements, keypair, actor doc)
  - org_actor: build/verify actor, build/verify endorsement, is_endorsement_valid
  - CorvinOrgResolver: resolve_to_org
  - L42OrgHandler: erasure (member removal, endorsement revocation, dissolution)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from org_store import OrgStore, OrgError, list_org_handles
from org_actor import (
    build_org_actor,
    verify_org_actor,
    build_endorsement,
    verify_endorsement,
    is_endorsement_valid,
    create_org,
    affiliate_agent,
    deaffiliate_agent,
    issue_dsk,
    verify_dsk_cert,
    is_dsk_expired,
)
from org_resolver import CorvinOrgResolver
from grant_store import GrantStore
from grant_issuer import build_grant
from grant_checker import GrantChecker

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


# ── OrgStore unit tests ───────────────────────────────────────────────────────


def test_create_org_store(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    store = OrgStore("test-corp", "_test")
    assert store.directory.is_dir()
    assert (store.directory / "endorsements").is_dir()
    assert (store.directory / "grants").is_dir()


def test_orgstore_invalid_handle(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    with pytest.raises(OrgError, match="invalid org handle"):
        OrgStore("INVALID_UPPER", "_test")


def test_keypair_generate_and_load(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    store = OrgStore("test-corp", "_test")
    priv, pub = store.generate_keypair()
    assert len(priv) == 64
    assert len(pub) == 64

    priv2, pub2 = store.load_keypair()
    assert priv2 == priv
    assert pub2 == pub


def test_keypair_mode_600(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    store = OrgStore("test-corp", "_test")
    store.generate_keypair()
    kp = store.directory / "keypair.json"
    assert oct(kp.stat().st_mode & 0o777) == "0o600"


def test_member_crud(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    store = OrgStore("test-corp", "_test")

    store.add_member("@alice@local", "owner")
    store.add_member("@bob@local", "admin")

    members = store.get_members()
    assert len(members) == 2
    assert store.get_member("@alice@local")["role"] == "owner"

    # Update role
    store.add_member("@bob@local", "editor")
    assert store.get_member("@bob@local")["role"] == "editor"

    # Remove
    assert store.remove_member("@bob@local") is True
    assert store.remove_member("@nonexistent@local") is False
    assert len(store.get_members()) == 1


def test_member_invalid_role(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    store = OrgStore("test-corp", "_test")
    with pytest.raises(OrgError, match="invalid role"):
        store.add_member("@alice@local", "superadmin")


def test_endorsement_crud(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    store = OrgStore("test-corp", "_test")
    priv, pub = store.generate_keypair()

    end = build_endorsement(
        org_actor_id="@test-corp",
        agent_actor_id="@bot@partner.instance",
        scope=["domain.*.read"],
        private_key_hex=priv,
    )
    store.save_endorsement(end)

    got = store.get_endorsement(end["endorsement_id"])
    assert got is not None
    assert got["agent_actor_id"] == "@bot@partner.instance"

    # find_endorsement_for_agent
    found = store.find_endorsement_for_agent("@bot@partner.instance")
    assert found is not None
    assert found["endorsement_id"] == end["endorsement_id"]

    assert store.find_endorsement_for_agent("@other@somewhere") is None


def test_endorsement_revoke(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    store = OrgStore("test-corp", "_test")
    priv, _ = store.generate_keypair()
    end = build_endorsement("@test-corp", "@bot@p.i", [], priv)
    store.save_endorsement(end)

    assert store.revoke_endorsement(end["endorsement_id"]) is True
    assert store.revoke_endorsement(end["endorsement_id"]) is False  # already revoked

    # Should not appear in active list
    assert store.find_endorsement_for_agent("@bot@p.i") is None
    # But should appear with include_revoked
    all_ends = store.list_endorsements(include_revoked=True)
    assert any(e["endorsement_id"] == end["endorsement_id"] for e in all_ends)


# ── org_actor unit tests ──────────────────────────────────────────────────────


def test_build_and_verify_org_actor():
    priv, pub = _gen_keypair()
    doc = build_org_actor("acme-corp", "ACME Corp", pub, priv)
    assert doc["type"] == "Organization"
    assert doc["id"] == "@acme-corp"
    assert doc["signature"].startswith("ed25519:")
    assert verify_org_actor(doc, pub)


def test_org_actor_with_host():
    priv, pub = _gen_keypair()
    doc = build_org_actor("acme-corp", "ACME", pub, priv, host="corvin.sh")
    assert doc["id"] == "@acme-corp@corvin.sh"


def test_org_actor_tampered_fails():
    priv, pub = _gen_keypair()
    doc = build_org_actor("acme-corp", "ACME Corp", pub, priv)
    doc["display_name"] = "TAMPERED"
    assert not verify_org_actor(doc, pub)


def test_build_and_verify_endorsement():
    priv, pub = _gen_keypair()
    end = build_endorsement(
        org_actor_id="@acme@corvin.sh",
        agent_actor_id="@acme-bot@corvin.sh",
        scope=["domain.*.read", "a2a.send"],
        private_key_hex=priv,
    )
    assert end["endorsement_id"].startswith("end_")
    assert verify_endorsement(end, pub)
    assert is_endorsement_valid(end, pub)


def test_endorsement_expired():
    priv, pub = _gen_keypair()
    end = build_endorsement("@org", "@bot", [], priv, ttl_seconds=1)
    end["expires_at"] = int(time.time()) - 1  # force expired
    assert not is_endorsement_valid(end, pub)


def test_endorsement_revoked_invalid():
    priv, pub = _gen_keypair()
    end = build_endorsement("@org", "@bot", [], priv)
    end["revoked_at"] = int(time.time())
    assert not is_endorsement_valid(end, pub)


def test_endorsement_tampered_fails():
    priv, pub = _gen_keypair()
    end = build_endorsement("@org", "@bot", ["domain.*.read"], priv)
    end["scope"] = ["agent.invoke.*"]  # tampered
    assert not verify_endorsement(end, pub)


def test_issue_dsk(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    store = OrgStore("test-corp", "_test")
    store.add_member("@alice@local", "owner")
    root_priv, root_pub = store.generate_keypair()

    cert = issue_dsk(store, root_priv, ttl_seconds=3600)
    assert cert["dsk_id"].startswith("dsk_")
    assert verify_dsk_cert(cert, root_pub)
    assert not is_dsk_expired(cert)


# ── create_org bootstrap ──────────────────────────────────────────────────────


def test_create_org_bootstrap(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    store = create_org(
        "test-corp",
        "Test Corporation",
        "@alice@local",
        summary="E2E test org",
        tenant_id="_test",
    )
    assert store.actor_exists()
    assert store.get_member("@alice@local") is not None
    assert store.get_member("@alice@local")["role"] == "owner"
    cfg = store.get_config()
    assert cfg["responsible_party"]["actor_id"] == "@alice@local"


def test_create_org_duplicate_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    create_org("test-corp", "Test", "@alice@local", tenant_id="_test")
    with pytest.raises(OrgError, match="already exists"):
        create_org("test-corp", "Test", "@alice@local", tenant_id="_test")


def test_list_org_handles(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    create_org("alpha-corp", "Alpha", "@a@local", tenant_id="_test")
    create_org("beta-corp", "Beta", "@b@local", tenant_id="_test")
    handles = list_org_handles("_test")
    assert "alpha-corp" in handles
    assert "beta-corp" in handles


# ── CorvinOrgResolver tests ──────────────────────────────────────────────────


def test_resolver_returns_org_for_affiliated_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    store = create_org("acme", "ACME", "@alice@local", tenant_id="_test")
    affiliate_agent(store, "@acme-bot@partner.instance", scope=["domain.*.read"])

    resolver = CorvinOrgResolver(tenant_id="_test")
    result = resolver.resolve_to_org("@acme-bot@partner.instance")
    assert result == "@acme"


def test_resolver_returns_none_for_unknown_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    create_org("acme", "ACME", "@alice@local", tenant_id="_test")

    resolver = CorvinOrgResolver(tenant_id="_test")
    assert resolver.resolve_to_org("@stranger@somewhere.else") is None


def test_resolver_returns_none_after_deaffiliate(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    store = create_org("acme", "ACME", "@alice@local", tenant_id="_test")
    end = affiliate_agent(store, "@acme-bot@partner.instance", scope=[])

    resolver = CorvinOrgResolver(tenant_id="_test")
    assert resolver.resolve_to_org("@acme-bot@partner.instance") is not None

    deaffiliate_agent(store, end["endorsement_id"])
    assert resolver.resolve_to_org("@acme-bot@partner.instance") is None


# ── Full E2E: org affiliation + GrantChecker ──────────────────────────────────


def test_e2e_org_agent_grant_allow(tmp_path, monkeypatch):
    """Full E2E:
    1. Create local org "acme" with owner "@alice@local"
    2. Affiliate "@acme-bot@partner.instance" with scope ["domain.*.read"]
    3. Personal actor (@alice) grants "@acme" capability "domain.blog.read"
    4. GrantChecker with CorvinOrgResolver:
       - presenting_actor = "@acme-bot@partner.instance"
       - OrgResolver → "@acme"
       - Grant for "@acme" found → ALLOW
    """
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

    # ── 1. Create org and affiliate agent
    org_store = create_org("acme", "ACME Corp", "@alice@local", tenant_id="_test")
    affiliate_agent(org_store, "@acme-bot@partner.instance", scope=["domain.*.read"])

    # ── 2. Personal actor grants the ORG the capability
    personal_priv, personal_pub = _gen_keypair()
    personal_actor_id = "@alice@corvin.sh"

    org_actor_id = org_store.get_actor()["id"]  # "@acme"

    grant_db = tmp_path / "personal_grants.db"
    grant_store = GrantStore(grant_db)

    grant_doc = build_grant(
        grantor_actor=personal_actor_id,
        grantee_actor=org_actor_id,
        capabilities=["domain.*.read"],
        private_key_hex=personal_priv,
    )
    grant_store.save_grant(grant_doc)

    # ── 3. Build GrantChecker with OrgResolver
    resolver = CorvinOrgResolver(tenant_id="_test")
    checker = GrantChecker(
        store=grant_store,
        local_actor_id=personal_actor_id,
        local_public_key_hex=personal_pub,
        org_resolver=resolver,
        tenant_id="_test",
    )

    # ── 4. Check: agent presents → OrgResolver maps → grant found → ALLOW
    with patch("grant_checker._is_follower", return_value=True):
        result = checker.check("@acme-bot@partner.instance", "domain.blog.read")

    assert result.allowed is True
    assert result.grant_id == grant_doc["grant_id"]


def test_e2e_org_agent_no_grant_deny(tmp_path, monkeypatch):
    """Same setup but no grant issued → DENY."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

    org_store = create_org("acme", "ACME Corp", "@alice@local", tenant_id="_test")
    affiliate_agent(org_store, "@acme-bot@partner.instance", scope=["domain.*.read"])

    personal_priv, personal_pub = _gen_keypair()
    personal_actor_id = "@alice@corvin.sh"

    grant_db = tmp_path / "personal_grants_empty.db"
    grant_store = GrantStore(grant_db)

    resolver = CorvinOrgResolver(tenant_id="_test")
    checker = GrantChecker(
        store=grant_store,
        local_actor_id=personal_actor_id,
        local_public_key_hex=personal_pub,
        org_resolver=resolver,
        tenant_id="_test",
    )

    with patch("grant_checker._is_follower", return_value=True):
        result = checker.check("@acme-bot@partner.instance", "domain.blog.read")

    assert result.allowed is False


def test_e2e_scope_ceiling_deny(tmp_path, monkeypatch):
    """Grant covers domain.*.read but agent scope only allows a2a.send → capability mismatch."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

    org_store = create_org("acme", "ACME", "@alice@local", tenant_id="_test")
    # Agent endorsed with scope a2a.send only — but the GrantChecker doesn't
    # enforce endorsement scope in M1 (that's a capability filter at L38 level).
    # This test verifies that the GRANT capability is checked, not the endorsement scope.
    affiliate_agent(org_store, "@acme-bot@p.i", scope=["a2a.send"])

    personal_priv, personal_pub = _gen_keypair()
    personal_actor_id = "@alice@corvin.sh"
    org_actor_id = org_store.get_actor()["id"]

    grant_db = tmp_path / "pg.db"
    grant_store = GrantStore(grant_db)
    # Grant only covers a2a.send
    grant_doc = build_grant(
        grantor_actor=personal_actor_id,
        grantee_actor=org_actor_id,
        capabilities=["a2a.send"],
        private_key_hex=personal_priv,
    )
    grant_store.save_grant(grant_doc)

    resolver = CorvinOrgResolver(tenant_id="_test")
    checker = GrantChecker(
        store=grant_store,
        local_actor_id=personal_actor_id,
        local_public_key_hex=personal_pub,
        org_resolver=resolver,
        tenant_id="_test",
    )

    with patch("grant_checker._is_follower", return_value=True):
        result = checker.check("@acme-bot@p.i", "domain.blog.read")

    assert result.allowed is False


# ── L42OrgHandler erasure ─────────────────────────────────────────────────────


def test_erasure_removes_member(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    create_org("acme", "ACME", "@alice@local", tenant_id="_test")
    store = OrgStore("acme", "_test")
    store.add_member("@bob@local", "admin")
    assert store.get_member("@bob@local") is not None

    from erasure_handlers import L42OrgHandler
    from erasure_orchestrator import LayerStatus

    handler = L42OrgHandler(tenant_id="_test")
    result = handler.purge("@bob@local", "req_test")

    assert result.count >= 1
    assert OrgStore("acme", "_test").get_member("@bob@local") is None


def test_erasure_revokes_endorsement(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    org_store = create_org("acme", "ACME", "@alice@local", tenant_id="_test")
    affiliate_agent(org_store, "@bot@partner.instance", scope=[])

    from erasure_handlers import L42OrgHandler

    handler = L42OrgHandler(tenant_id="_test")
    result = handler.purge("@bot@partner.instance", "req_test")

    assert result.count >= 1
    store = OrgStore("acme", "_test")
    assert store.find_endorsement_for_agent("@bot@partner.instance") is None


def test_erasure_dissolves_org(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    create_org("acme", "ACME", "@alice@local", tenant_id="_test")
    org_d = OrgStore("acme", "_test").directory
    assert org_d.exists()

    from erasure_handlers import L42OrgHandler

    handler = L42OrgHandler(tenant_id="_test")
    # Pass the org handle as subject_id
    result = handler.purge("acme", "req_test")

    assert result.count > 0
    assert not org_d.exists()
