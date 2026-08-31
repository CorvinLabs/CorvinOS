"""ACO Layer 6 gate — the signed ``maintainer.commit`` capability (ADR-0178).

This is the cryptographic trust boundary between Tier LOCAL (every install, may
heal its environment) and Tier CONTRIBUTOR (may turn diagnoses into code commits
on ``main``). It is **deny-by-default**: an instance enters CONTRIBUTOR tier ONLY
if it presents a capability that is

  1. a well-formed token,
  2. signed by the Corvin-Labs key (Ed25519),
  3. not expired,
  4. of type ``maintainer.commit``,
  5. bound to THIS instance (instance_id match) — so a leaked token can't be
     replayed on another machine.

A config flag is deliberately NOT accepted as the gate — it would be forgeable by
any user. The capability is verified against a pinned public key; only Corvin Labs
holds the private key, so a normal user cannot mint one.

Pure-stdlib + ``cryptography`` (a base dependency). No network. The signing side
(``issue``) is for the issuer/tests; production installs only ever *verify*.
"""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from typing import Any

CAP_NAME = "maintainer.commit"


@dataclass
class CapabilityVerdict:
    allowed: bool
    reason: str               # "ok" | no_token | bad_format | bad_signature | expired
                              #  | wrong_instance | wrong_cap | no_pubkey | crypto_unavailable
    subject: str = ""
    instance_id: str = ""
    exp: int = 0


# ── canonical payload ─────────────────────────────────────────────────────────

def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ── issue (issuer / tests only) ───────────────────────────────────────────────

def issue(private_key_bytes: bytes, *, instance_id: str, subject: str,
          repo: str = "CorvinLabs/CorvinOS", ttl_seconds: int = 90 * 86400,
          now: int | None = None) -> str:
    """Mint a base64 capability token. ``private_key_bytes`` = raw Ed25519 private
    key (32 bytes). Used by Corvin Labs issuance + the test-suite, never on a
    normal install."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    now = int(now if now is not None else time.time())
    payload = {
        "cap": CAP_NAME, "instance_id": instance_id, "subject": subject,
        "repo": repo, "iat": now, "exp": now + int(ttl_seconds),
    }
    sk = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    sig = sk.sign(_canonical(payload))
    token = {"payload": payload, "sig": base64.b64encode(sig).decode()}
    return base64.b64encode(_canonical(token)).decode()


# ── public-key resolution ─────────────────────────────────────────────────────

def _pinned_public_key() -> bytes | None:
    """Raw Ed25519 public key (32 bytes) the verifier trusts. Resolution:
    CORVIN_MAINTAINER_PUBKEY (base64) env → pinned file under the install. Returns
    None when no key is pinned (⇒ deny-by-default: nobody is a maintainer)."""
    env = os.environ.get("CORVIN_MAINTAINER_PUBKEY", "").strip()
    if env:
        try:
            raw = base64.b64decode(env, validate=True)
        except Exception:  # noqa: BLE001
            return None
        # An Ed25519 public key is exactly 32 bytes; reject anything else (a
        # truncated/mangled copy-paste — common when the trailing '=' padding is
        # lost) so verify() can report bad_pubkey instead of a confusing deny.
        return raw if len(raw) == 32 else None
    return None


# ── verify (the gate) ─────────────────────────────────────────────────────────

def verify(token: str | None, *, instance_id: str,
           public_key_bytes: bytes | None = None,
           now: int | None = None) -> CapabilityVerdict:
    """Deny-by-default verification of a ``maintainer.commit`` token for THIS
    instance. Returns a CapabilityVerdict; only ``allowed=True`` may enter L6."""
    if not token:
        return CapabilityVerdict(False, "no_token")
    pub = public_key_bytes if public_key_bytes is not None else _pinned_public_key()
    if not pub:
        # Distinguish "set but invalid" (e.g. a copy-paste that dropped the base64
        # '=' padding, or a non-32-byte key) from "absent" — far less confusing.
        if public_key_bytes is None and os.environ.get("CORVIN_MAINTAINER_PUBKEY", "").strip():
            return CapabilityVerdict(False, "bad_pubkey")
        return CapabilityVerdict(False, "no_pubkey")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
    except Exception:  # noqa: BLE001
        return CapabilityVerdict(False, "crypto_unavailable")
    try:
        outer = json.loads(base64.b64decode(token))
        payload = outer["payload"]
        sig = base64.b64decode(outer["sig"])
    except Exception:  # noqa: BLE001
        return CapabilityVerdict(False, "bad_format")
    try:
        Ed25519PublicKey.from_public_bytes(pub).verify(sig, _canonical(payload))
    except InvalidSignature:
        return CapabilityVerdict(False, "bad_signature")
    except Exception:  # noqa: BLE001
        return CapabilityVerdict(False, "bad_signature")

    subj = str(payload.get("subject", ""))
    iid = str(payload.get("instance_id", ""))
    exp = int(payload.get("exp", 0) or 0)
    if payload.get("cap") != CAP_NAME:
        return CapabilityVerdict(False, "wrong_cap", subj, iid, exp)
    if exp and int(now if now is not None else time.time()) >= exp:
        return CapabilityVerdict(False, "expired", subj, iid, exp)
    if not iid or iid != instance_id:
        return CapabilityVerdict(False, "wrong_instance", subj, iid, exp)
    return CapabilityVerdict(True, "ok", subj, iid, exp)


# ── current-instance gate (convenience) ───────────────────────────────────────

def current_instance_id() -> str:
    """This machine's instance id (IBC/CorvinID if available, else env). Empty ⇒
    cannot match any capability ⇒ stays LOCAL."""
    try:
        from instance_identity import current_instance_id as _iid  # type: ignore
        v = _iid()
        if v:
            return str(v)
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get("CORVIN_INSTANCE_ID", "").strip()


def is_contributor(token: str | None = None, *, now: int | None = None) -> CapabilityVerdict:
    """Top-level gate: is THIS install authorised for Tier CONTRIBUTOR? Reads the
    token from the arg or CORVIN_MAINTAINER_CAP env. Deny-by-default."""
    tok = token if token is not None else os.environ.get("CORVIN_MAINTAINER_CAP", "").strip()
    return verify(tok or None, instance_id=current_instance_id(), now=now)
