"""Layer 42 CorvinOrg — org actor document, DSK issuance, agent endorsements.

Public API
----------
build_org_actor(...)     -> dict    Create a signed org actor document.
issue_dsk(...)           -> dict    Issue a Delegated Signing Key certificate.
build_endorsement(...)   -> dict    Create a signed agent affiliation endorsement.
verify_endorsement(...)  -> bool    Verify an endorsement's Ed25519 signature.
create_org(...)          -> OrgStore  Full org bootstrap (keypair + actor + config + owner).

Signing convention: same canonical JSON payload as L39 PostEnvelope and L41 grants
(json.dumps(doc_without_signature, sort_keys=True, separators=(",",":"),
ensure_ascii=True)).

CI AST lint: MUST NOT import anthropic.
"""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

try:
    from .org_store import OrgStore, OrgError, list_org_handles
    from .grant_issuer import validate_capabilities
    from .audit import audit_event
except ImportError:
    import sys

    _here = Path(__file__).resolve().parent
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    from org_store import OrgStore, OrgError, list_org_handles
    from grant_issuer import validate_capabilities
    from audit import audit_event


ORG_SCHEMA_VERSION = 1
DSK_SCHEMA_VERSION = 1
ENDORSEMENT_SCHEMA_VERSION = 1

_DEFAULT_DSK_TTL = 7 * 86_400  # 7 days


# ── Ed25519 helpers (same pattern as L39 social_envelope + L41 grant_issuer) ─


def _canonical_payload(doc: dict) -> bytes:
    return json.dumps(
        {k: v for k, v in doc.items() if k != "signature"},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _sign(doc: dict, private_key_hex: str) -> str:
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    return f"ed25519:{private_key.sign(_canonical_payload(doc)).hex()}"


def _verify(doc: dict, public_key_hex: str) -> bool:
    try:
        sig_str = doc.get("signature", "")
        if not isinstance(sig_str, str) or not sig_str.startswith("ed25519:"):
            return False
        sig_bytes = bytes.fromhex(sig_str.removeprefix("ed25519:"))
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        pub.verify(sig_bytes, _canonical_payload(doc))
        return True
    except Exception:
        return False


# ── Org actor document ────────────────────────────────────────────────────────


def build_org_actor(
    org_handle: str,
    display_name: str,
    public_key_hex: str,
    private_key_hex: str,
    *,
    summary: str = "",
    host: str | None = None,
    corvin_version: str = "1.0.0",
) -> dict:
    """Create and sign an org actor document.

    ``host`` is the federation hostname (e.g. ``corvin.sh``). When None (M1
    local-only mode), the actor_id is set to ``@<org_handle>`` without a host.
    Federation requires a host (M2+).
    """
    actor_id = f"@{org_handle}@{host}" if host else f"@{org_handle}"
    now = int(time.time())
    doc: dict = {
        "schema_version": ORG_SCHEMA_VERSION,
        "type": "Organization",
        "id": actor_id,
        "preferred_username": org_handle,
        "display_name": display_name,
        "summary": summary,
        "public_key_hex": public_key_hex,
        "verified_domain": None,
        "affiliated_actors": [],
        "corvin_version": corvin_version,
        "created_at": now,
        "signature": "",
    }
    doc["signature"] = _sign(doc, private_key_hex)
    return doc


def verify_org_actor(doc: dict, public_key_hex: str) -> bool:
    """Verify the self-signature on an org actor document."""
    return _verify(doc, public_key_hex)


# ── Delegated Signing Key ─────────────────────────────────────────────────────


def issue_dsk(
    store: OrgStore,
    root_private_key_hex: str,
    *,
    ttl_seconds: int = _DEFAULT_DSK_TTL,
) -> dict:
    """Issue a Delegated Signing Key (DSK) certificate for the org.

    M1 implementation: generates a new Ed25519 keypair, saves it as the org's
    active keypair, and returns a DSK certificate signed by the root key.
    The DSK certificate links the short-lived DSK public key back to the org's
    root identity so remote verifiers can trace:
        post/grant ← DSK ← DSK certificate ← root public key

    Caller MUST emit ``org.dsk_issued`` audit event BEFORE calling.
    M2 will add Shamir threshold sharding (n-of-m owners co-sign the DSK cert).
    """
    dsk_priv, dsk_pub = store.generate_keypair()  # saves new keypair to store
    now = int(time.time())
    dsk_id = "dsk_" + secrets.token_hex(8)

    cert: dict = {
        "schema_version": DSK_SCHEMA_VERSION,
        "dsk_id": dsk_id,
        "org_id": store.handle,
        "dsk_public_key_hex": dsk_pub,
        "issued_at": now,
        "expires_at": now + ttl_seconds,
        "issued_by": store.list_owners(),
        "quorum_size": 1,  # M1: single-owner
        "signature": "",
    }
    cert["signature"] = _sign(cert, root_private_key_hex)
    return cert


def verify_dsk_cert(cert: dict, root_public_key_hex: str) -> bool:
    """Verify a DSK certificate's signature against the org's root public key."""
    return _verify(cert, root_public_key_hex)


def is_dsk_expired(cert: dict) -> bool:
    expires_at = cert.get("expires_at")
    if expires_at is None:
        return False
    return int(time.time()) > int(expires_at)


# ── Agent endorsement ─────────────────────────────────────────────────────────


def build_endorsement(
    org_actor_id: str,
    agent_actor_id: str,
    scope: list[str],
    private_key_hex: str,
    *,
    ttl_seconds: int | None = None,
) -> dict:
    """Create a signed agent affiliation endorsement document.

    The endorsement proves that ``agent_actor_id`` is officially affiliated with
    the org identified by ``org_actor_id``. Remote parties verify the signature
    against the org's DSK public key.

    ``scope`` lists the capabilities the agent may exercise on behalf of the org.
    An empty scope means the affiliation is purely for identity attribution — the
    agent cannot exercise any org capabilities.
    """
    if scope:
        validate_capabilities(scope)

    now = int(time.time())
    endorsement_id = "end_" + secrets.token_hex(8)
    doc: dict = {
        "schema_version": ENDORSEMENT_SCHEMA_VERSION,
        "endorsement_id": endorsement_id,
        "org_actor_id": org_actor_id,
        "agent_actor_id": agent_actor_id,
        "scope": list(scope),
        "issued_at": now,
        "expires_at": (now + ttl_seconds) if ttl_seconds is not None else None,
        "revoked_at": None,
        "signature": "",
    }
    doc["signature"] = _sign(doc, private_key_hex)
    return doc


def verify_endorsement(doc: dict, org_public_key_hex: str) -> bool:
    """Verify an endorsement's Ed25519 signature against the org's public key."""
    return _verify(doc, org_public_key_hex)


def is_endorsement_valid(doc: dict, org_public_key_hex: str) -> bool:
    """Return True if the endorsement is structurally valid, not expired, not revoked,
    and has a correct signature."""
    if doc.get("revoked_at") is not None:
        return False
    expires_at = doc.get("expires_at")
    if expires_at is not None and int(time.time()) > int(expires_at):
        return False
    return verify_endorsement(doc, org_public_key_hex)


# ── Org bootstrap ─────────────────────────────────────────────────────────────


def create_org(
    org_handle: str,
    display_name: str,
    owner_actor_id: str,
    *,
    summary: str = "",
    host: str | None = None,
    responsible_party_actor_id: str | None = None,
    tenant_id: str | None = "_default",
) -> OrgStore:
    """Bootstrap a new organisation: keypair → actor doc → config → owner member.

    This is the single entry point for org creation. It:
    1. Creates the OrgStore directory layout.
    2. Generates the org keypair.
    3. Builds and persists the org actor document.
    4. Saves the org config (responsible party, default policy).
    5. Adds the owner member.
    6. Emits all required L16 audit events (audit-first for each step).

    Returns the populated OrgStore.
    """
    store = OrgStore(org_handle, tenant_id)
    if store.actor_exists():
        raise OrgError(f"org {org_handle!r} already exists")

    # Step 1: generate keypair (DSK = root key in M1)
    audit_event(
        "org.dsk_issued",
        details={"org_handle": org_handle, "quorum_size": 1},
    )
    priv_hex, pub_hex = store.generate_keypair()

    # Step 2: build and persist org actor document
    actor_doc = build_org_actor(
        org_handle=org_handle,
        display_name=display_name,
        public_key_hex=pub_hex,
        private_key_hex=priv_hex,
        summary=summary,
        host=host,
    )
    store.save_actor(actor_doc)

    # Step 3: save config
    responsible_party = responsible_party_actor_id or owner_actor_id
    config = {
        "org_handle": org_handle,
        "display_name": display_name,
        "host": host,
        "responsible_party": {
            "actor_id": responsible_party,
            "declared_at": int(time.time()),
        },
        "policy": {
            "dsk_quorum": 1,
            "broad_grant_quorum": 1,
        },
        "created_at": int(time.time()),
    }
    store.save_config(config)

    # Step 4: add owner member
    audit_event(
        "org.created",
        details={
            "org_handle": org_handle,
            "owner_prefix": owner_actor_id[:16],
        },
    )
    audit_event(
        "org.member_added",
        details={
            "org_handle": org_handle,
            "member_prefix": owner_actor_id[:16],
            "role": "owner",
        },
    )
    store.add_member(owner_actor_id, "owner")

    return store


# ── Agent affiliation helper ──────────────────────────────────────────────────


def affiliate_agent(
    store: OrgStore,
    agent_actor_id: str,
    scope: list[str],
    *,
    ttl_seconds: int | None = None,
) -> dict:
    """Create and persist an agent endorsement for the given org store.

    Caller must be an owner or admin of the org.
    Emits ``org.agent_affiliated`` audit event BEFORE persisting.
    Returns the endorsement document.
    """
    actor_doc = store.get_actor()
    org_actor_id = actor_doc["id"]
    priv_hex, _ = store.load_keypair()

    endorsement = build_endorsement(
        org_actor_id=org_actor_id,
        agent_actor_id=agent_actor_id,
        scope=scope,
        private_key_hex=priv_hex,
        ttl_seconds=ttl_seconds,
    )

    audit_event(
        "org.agent_affiliated",
        details={
            "org_handle": store.handle,
            "agent_prefix": agent_actor_id[:16],
            "endorsement_id": endorsement["endorsement_id"],
        },
    )
    store.save_endorsement(endorsement)

    # Update affiliated_actors list in actor.json
    actor_doc.setdefault("affiliated_actors", [])
    if agent_actor_id not in actor_doc["affiliated_actors"]:
        actor_doc["affiliated_actors"].append(agent_actor_id)
        store.save_actor(actor_doc)

    return endorsement


def deaffiliate_agent(store: OrgStore, endorsement_id: str) -> bool:
    """Revoke an agent endorsement. Emits ``org.agent_deaffiliated`` audit event."""
    doc = store.get_endorsement(endorsement_id)
    if doc is None:
        return False
    agent_actor_id = doc.get("agent_actor_id", "")
    audit_event(
        "org.agent_deaffiliated",
        details={
            "org_handle": store.handle,
            "agent_prefix": agent_actor_id[:16],
            "endorsement_id": endorsement_id,
        },
    )
    return store.revoke_endorsement(endorsement_id)
