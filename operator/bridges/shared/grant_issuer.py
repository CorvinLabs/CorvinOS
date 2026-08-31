"""Layer 41 Social Capability Grants — grant creation and Ed25519 signing.

Public API
----------
build_grant(...)          -> dict   Create and sign a new grant document.
verify_grant(...)         -> bool   Verify a grant document's Ed25519 signature.
validate_capabilities(...) -> None  Validate capability identifier syntax.
validate_conditions(...)   -> None  Validate the conditions dict.

All functions are stateless. The caller is responsible for:
  - Emitting ``grant.issued`` to the L16 audit chain BEFORE persisting.
  - Persisting to GrantStore.

Canonical signing payload: json.dumps(doc_without_signature,
sort_keys=True, separators=(",",":"), ensure_ascii=True) — identical to the
L39 PostEnvelope convention so remote verifiers can use the same code path.

CI AST lint: MUST NOT import anthropic.
"""
from __future__ import annotations

import json
import re
import secrets
import time

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

GRANT_SCHEMA_VERSION = 1
_GRANT_ID_PREFIX = "grnt_"

VALID_CONDITION_KEYS = frozenset({"valid_until", "rate_limit", "data_class_ceiling"})
VALID_DATA_CLASSES = frozenset({"PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET"})
VALID_PERIODS = frozenset({"second", "minute", "hour", "day"})

_CAP_SEGMENT_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")


class GrantError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ── Signing / verification ────────────────────────────────────────────────────


def _canonical_payload(doc: dict) -> bytes:
    """Canonical JSON signing payload — same convention as L39 PostEnvelope."""
    return json.dumps(
        {k: v for k, v in doc.items() if k != "signature"},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def sign_grant(doc: dict, private_key_hex: str) -> str:
    """Sign a grant document dict. Returns ``'ed25519:<hex>'`` string."""
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    sig_bytes = private_key.sign(_canonical_payload(doc))
    return f"ed25519:{sig_bytes.hex()}"


def verify_grant(doc: dict, public_key_hex: str) -> bool:
    """Verify a grant document's Ed25519 signature. Returns False on any failure."""
    try:
        sig_str = doc.get("signature", "")
        if not isinstance(sig_str, str) or not sig_str.startswith("ed25519:"):
            return False
        sig_bytes = bytes.fromhex(sig_str.removeprefix("ed25519:"))
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        public_key.verify(sig_bytes, _canonical_payload(doc))
        return True
    except Exception:
        return False


# ── Validation ────────────────────────────────────────────────────────────────


def validate_capabilities(capabilities: list[str]) -> None:
    """Raise ``GrantError`` if any capability identifier is malformed.

    Valid format: dot-separated segments, each segment either ``*`` or
    ``[a-z][a-z0-9_-]*``. At least two segments required.
    """
    if not capabilities:
        raise GrantError("capabilities list must not be empty")
    for cap in capabilities:
        if not isinstance(cap, str) or not cap:
            raise GrantError(f"invalid capability (not a non-empty string): {cap!r}")
        parts = cap.split(".")
        if len(parts) < 2:
            raise GrantError(
                f"capability must have at least two dot-separated parts: {cap!r}"
            )
        for part in parts:
            if part != "*" and not _CAP_SEGMENT_RE.match(part):
                raise GrantError(
                    f"invalid capability segment {part!r} in {cap!r}"
                )


def validate_conditions(conditions: dict) -> None:
    """Raise ``GrantError`` if the conditions dict is structurally invalid."""
    unknown = set(conditions) - VALID_CONDITION_KEYS
    if unknown:
        raise GrantError(f"unknown condition keys: {sorted(unknown)}")
    if "valid_until" in conditions:
        if not isinstance(conditions["valid_until"], int):
            raise GrantError("conditions.valid_until must be an integer Unix timestamp")
    if "rate_limit" in conditions:
        rl = conditions["rate_limit"]
        try:
            count_str, period = str(rl).split("/", 1)
            count = int(count_str)
            if count <= 0:
                raise ValueError("count must be positive")
            if period not in VALID_PERIODS:
                raise ValueError(f"unknown period: {period!r}")
        except (ValueError, AttributeError) as exc:
            raise GrantError(
                f"invalid rate_limit {rl!r} — expected 'N/period' where period ∈ "
                f"{sorted(VALID_PERIODS)}"
            ) from exc
    if "data_class_ceiling" in conditions:
        dcc = conditions["data_class_ceiling"]
        if dcc not in VALID_DATA_CLASSES:
            raise GrantError(
                f"invalid data_class_ceiling {dcc!r} — must be one of "
                f"{sorted(VALID_DATA_CLASSES)}"
            )


# ── Grant construction ────────────────────────────────────────────────────────


def build_grant(
    *,
    grantor_actor: str,
    grantee_actor: str,
    capabilities: list[str],
    conditions: dict | None = None,
    private_key_hex: str,
) -> dict:
    """Create a signed grant document (ADR-0054 §grant-document).

    The caller MUST:
      1. Emit ``grant.issued`` to the L16 audit chain BEFORE persisting.
      2. Call ``GrantStore.save_grant(doc)`` to persist.

    ``grantee_actor`` may be:
      - An ActivityPub actor ID (``@user@instance``)
      - The literal string ``"*"`` (all current followers)
      - A group reference ``"group:<tag>"`` (local follower group, L42 M2+)

    Returns the complete signed grant dict with ``signature`` set.
    """
    if not grantor_actor:
        raise GrantError("grantor_actor must not be empty")
    if not grantee_actor:
        raise GrantError("grantee_actor must not be empty")

    validate_capabilities(capabilities)
    conds = dict(conditions) if conditions else {}
    validate_conditions(conds)

    now = int(time.time())
    grant_id = _GRANT_ID_PREFIX + secrets.token_hex(8)

    doc: dict = {
        "grant_id": grant_id,
        "schema_version": GRANT_SCHEMA_VERSION,
        "grantor_actor": grantor_actor,
        "grantee_actor": grantee_actor,
        "capabilities": list(capabilities),
        "conditions": conds,
        "issued_at": now,
        "revoked_at": None,
        "signature": "",
    }
    doc["signature"] = sign_grant(doc, private_key_hex)
    return doc
