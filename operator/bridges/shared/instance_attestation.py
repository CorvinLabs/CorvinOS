"""ADR-0078 Phase 1 — CorvinOS Instance Attestation Certificate (IAC).

The CorvinCA is an Ed25519 key pair operated by CorvinOS Ltd:
  - Private key: HSM-backed at the operator; never in repo or any config file.
  - Public key:  pinned as CORVIN_CA_PUBKEY_HEX; overridable via environment.

IAC wire format (stored at <corvin_home>/global/instance_attestation.json,
mode 0600):

    {
      "cert": {
        "version":             1,
        "instance_id":         "<uuid4>",
        "corvin_version":      "2026.6",
        "tier":                "community",
        "registered_at":       1748390400.0,
        "expires_at":          1780012800.0,
        "ca_pubkey_fingerprint": "sha256:<16 hex chars>"
      },
      "sig": "<64-byte Ed25519 sig over canonical(cert), hex>"
    }

Runtime-path functions (called on every A2A receive):
  - load_attestation(corvin_home)         read local IAC → IAC dict | None
  - verify_attestation(iac_dict, pubkey)  verify IAC → TrustLevel
  - get_ca_pubkey_bytes()                 resolve active CA pubkey → bytes | None
  - parse_min_trust(s)                    "verified" → TrustLevel.VERIFIED

Operator-path functions (CA tooling only — require the CA private key):
  - generate_ca_keypair()       create a fresh Ed25519 CA keypair → (priv, pub) hex
  - sign_attestation(...)       issue a new IAC → dict{cert, sig}
  - save_attestation(iac, home) persist IAC to disk mode 0600

Attestation check in the A2A receiver:
  Step ⑥.⁵ (after HMAC verification):
    - If origin_config["min_trust"] > UNVERIFIED:
        - load sender_attestation from envelope
        - verify against CorvinCA pubkey
        - reject if trust_level < required

CA public-key resolution order:
  1. CORVIN_CA_PUBKEY_HEX  (env var, 64 hex chars)
  2. CORVIN_CA_PUBKEY_PATH (env var, path to file with hex content)
  3. CORVIN_CA_PUBKEY_HEX  constant in this module (set by CorvinOS Ltd at CA init)
  If none → CA not configured; attestation verification returns UNVERIFIED.

CI lint: MUST NOT ``import anthropic``.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from enum import IntEnum
from pathlib import Path

# ── CorvinCA public key ───────────────────────────────────────────────────
#
# This constant is set by CorvinOS Ltd at the CA initialization ceremony.
# It is None until the production CA keypair has been generated and the
# public key has been embedded in a CorvinOS release.
#
# Operators who run their own CA (e.g. for an enterprise deployment) may
# override this via CORVIN_CA_PUBKEY_HEX.
#
# Tests generate ephemeral keypairs and inject them via CORVIN_CA_PUBKEY_HEX.
CORVIN_CA_PUBKEY_HEX: str | None = None

# ── Trust levels ─────────────────────────────────────────────────────────

class TrustLevel(IntEnum):
    """Trust level assigned to a remote instance based on its IAC tier.

    The ordering is significant: higher value = stricter requirement.
    UNVERIFIED < COMMUNITY < VERIFIED < ENTERPRISE.
    """
    UNVERIFIED = 0
    COMMUNITY  = 1
    VERIFIED   = 2
    ENTERPRISE = 3


_TIER_TO_LEVEL: dict[str, TrustLevel] = {
    "community":  TrustLevel.COMMUNITY,
    "verified":   TrustLevel.VERIFIED,
    "enterprise": TrustLevel.ENTERPRISE,
}

_LEVEL_TO_TIER: dict[TrustLevel, str] = {v: k for k, v in _TIER_TO_LEVEL.items()}
_LEVEL_TO_TIER[TrustLevel.UNVERIFIED] = "unverified"


def parse_min_trust(value: str | None) -> TrustLevel:
    """Parse a min_trust string from an origin config into a TrustLevel.

    Accepts: "none" / "unverified" / "community" / "verified" / "enterprise".
    Unknown values are treated as UNVERIFIED (fail-open on misconfiguration).
    """
    if value is None:
        return TrustLevel.UNVERIFIED
    normalized = str(value).strip().lower()
    if normalized in ("none", "unverified", ""):
        return TrustLevel.UNVERIFIED
    return _TIER_TO_LEVEL.get(normalized, TrustLevel.UNVERIFIED)


def trust_level_name(level: TrustLevel) -> str:
    return _LEVEL_TO_TIER.get(level, "unverified")


# ── IAC file path ─────────────────────────────────────────────────────────

def attestation_path(corvin_home: Path | str | None = None) -> Path:
    """Return the path to instance_attestation.json.

    Respects CORVIN_ATTESTATION_PATH env override (for tests).
    """
    env_override = os.environ.get("CORVIN_ATTESTATION_PATH")
    if env_override:
        return Path(env_override)
    if corvin_home is None:
        corvin_home = Path(
            os.environ.get("CORVIN_HOME", Path.home() / ".corvin")
        )
    return Path(corvin_home) / "global" / "instance_attestation.json"


# ── CA public-key resolution ──────────────────────────────────────────────

def get_ca_pubkey_bytes() -> bytes | None:
    """Resolve the active CorvinCA public key.

    Resolution order:
      1. CORVIN_CA_PUBKEY_HEX  env var (64 hex chars)
      2. CORVIN_CA_PUBKEY_PATH env var (file containing hex)
      3. CORVIN_CA_PUBKEY_HEX  module constant (set at CA init ceremony)

    Returns None when no CA is configured — callers should treat this as
    "CA not yet operational; attestation check is best-effort."
    """
    env_hex = os.environ.get("CORVIN_CA_PUBKEY_HEX") or CORVIN_CA_PUBKEY_HEX
    if env_hex:
        try:
            return bytes.fromhex(env_hex.strip())
        except ValueError:
            pass

    path_env = os.environ.get("CORVIN_CA_PUBKEY_PATH")
    if path_env:
        try:
            hex_content = Path(path_env).read_text("utf-8").strip()
            return bytes.fromhex(hex_content)
        except Exception:  # noqa: BLE001
            pass

    return None


# ── Core: verify ──────────────────────────────────────────────────────────

def verify_attestation(
    iac_dict: dict,
    ca_pubkey_bytes: bytes | None = None,
) -> TrustLevel:
    """Verify an Instance Attestation Certificate against the CorvinCA pubkey.

    Returns the TrustLevel declared in the cert if:
      - The cert is structurally valid (all required fields present, types correct)
      - The Ed25519 signature verifies against the CA public key
      - The cert is not expired

    Returns TrustLevel.UNVERIFIED if any of the above fail, OR if
    ca_pubkey_bytes is None (CA not yet configured).

    Never raises.
    """
    if not isinstance(iac_dict, dict):
        return TrustLevel.UNVERIFIED
    if ca_pubkey_bytes is None:
        ca_pubkey_bytes = get_ca_pubkey_bytes()
    if ca_pubkey_bytes is None:
        return TrustLevel.UNVERIFIED

    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
        from cryptography.exceptions import InvalidSignature
    except ImportError:
        return TrustLevel.UNVERIFIED

    try:
        cert = iac_dict.get("cert")
        sig_hex = iac_dict.get("sig")
        if not isinstance(cert, dict) or not isinstance(sig_hex, str):
            return TrustLevel.UNVERIFIED

        # Required cert fields
        required = {"version", "instance_id", "tier", "registered_at",
                    "expires_at", "ca_pubkey_fingerprint"}
        if not required.issubset(cert.keys()):
            return TrustLevel.UNVERIFIED

        # Expiry check
        if float(cert["expires_at"]) < time.time():
            return TrustLevel.UNVERIFIED

        # CA pubkey fingerprint must match what we have
        expected_fp = _pubkey_fingerprint(ca_pubkey_bytes)
        if cert["ca_pubkey_fingerprint"] != expected_fp:
            return TrustLevel.UNVERIFIED

        # Verify Ed25519 signature over canonical cert payload
        canonical = _canonical(cert)
        sig_bytes = bytes.fromhex(sig_hex)
        pubkey = Ed25519PublicKey.from_public_bytes(ca_pubkey_bytes)
        pubkey.verify(sig_bytes, canonical)

        tier = str(cert["tier"]).lower()
        return _TIER_TO_LEVEL.get(tier, TrustLevel.UNVERIFIED)

    except (InvalidSignature, Exception):  # noqa: BLE001
        return TrustLevel.UNVERIFIED


# ── Core: load / save ─────────────────────────────────────────────────────

def load_attestation(corvin_home: Path | str | None = None) -> dict | None:
    """Read the local IAC from disk.

    Returns the raw dict {cert: {...}, sig: "..."} or None when absent.
    Mode 0600 is enforced — world-readable files are rejected.
    """
    path = attestation_path(corvin_home)
    if not path.exists():
        return None
    try:
        mode = path.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            return None  # world-readable — treat as absent, log elsewhere
        return json.loads(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def save_attestation(iac_dict: dict, corvin_home: Path | str | None = None) -> None:
    """Persist an IAC to disk at mode 0600 (atomic write).

    Raises OSError on permission failure.
    """
    path = attestation_path(corvin_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(iac_dict, sort_keys=True, indent=2) + "\n",
                   encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def effective_trust_level(
    iac_dict: dict | None,
    ca_pubkey_bytes: bytes | None = None,
) -> TrustLevel:
    """Shorthand: load + verify in one call."""
    if iac_dict is None:
        return TrustLevel.UNVERIFIED
    return verify_attestation(iac_dict, ca_pubkey_bytes)


# ── Operator tooling: generate + sign ─────────────────────────────────────

def generate_ca_keypair() -> tuple[str, str]:
    """Generate a fresh Ed25519 CA keypair.

    Returns (privkey_hex, pubkey_hex). The private key is 32 bytes (Ed25519
    seed); the public key is 32 bytes.

    CA OPERATOR USE ONLY — store the private key in an HSM; never commit it.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding, NoEncryption, PrivateFormat, PublicFormat,
    )

    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_bytes = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub_bytes = pub.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return priv_bytes.hex(), pub_bytes.hex()


def sign_attestation(
    *,
    instance_id: str,
    tier: str,
    ca_privkey_hex: str,
    corvin_version: str = "dev",
    ttl_days: int = 365,
) -> dict:
    """Issue a new IAC.  CA OPERATOR USE ONLY.

    Returns a dict {cert: {...}, sig: "<hex>"} ready for save_attestation().
    Raises ValueError on bad tier.  Raises ImportError if cryptography is absent.
    """
    if tier not in _TIER_TO_LEVEL:
        raise ValueError(f"unknown tier {tier!r}; valid: {list(_TIER_TO_LEVEL)}")

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding, NoEncryption, PublicFormat,
    )

    priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(ca_privkey_hex))
    pub_bytes = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    now = time.time()
    cert: dict = {
        "version":              1,
        "instance_id":          instance_id,
        "corvin_version":       corvin_version,
        "tier":                 tier,
        "registered_at":        now,
        "expires_at":           now + ttl_days * 86400,
        "ca_pubkey_fingerprint": _pubkey_fingerprint(pub_bytes),
    }
    sig_bytes = priv.sign(_canonical(cert))
    return {"cert": cert, "sig": sig_bytes.hex()}


# ── Audit projection ──────────────────────────────────────────────────────

def attestation_audit_fields(
    iac_dict: dict | None,
    trust_level: TrustLevel,
) -> dict:
    """Build the audit-allow-listed projection of an attestation check.

    Only tier, trust_level, and whether the cert is present go into audit.
    NEVER include instance_id, sig, or any key material.
    """
    if iac_dict is None:
        return {"attestation_present": False,
                "trust_level": trust_level_name(trust_level)}
    cert = iac_dict.get("cert") or {}
    return {
        "attestation_present": True,
        "trust_level":         trust_level_name(trust_level),
        "tier":                cert.get("tier", "unknown"),
        "corvin_version":      cert.get("corvin_version", "unknown"),
    }


# ── Internals ─────────────────────────────────────────────────────────────

def _canonical(d: dict) -> bytes:
    """Canonical JSON encoding for signing: sorted keys, no whitespace."""
    return json.dumps(d, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode()


def _pubkey_fingerprint(pubkey_bytes: bytes) -> str:
    """Short fingerprint: sha256:<first 16 hex chars of digest>."""
    digest = hashlib.sha256(pubkey_bytes).hexdigest()
    return f"sha256:{digest[:16]}"


__all__ = [
    "TrustLevel",
    "parse_min_trust",
    "trust_level_name",
    "attestation_path",
    "get_ca_pubkey_bytes",
    "verify_attestation",
    "load_attestation",
    "save_attestation",
    "effective_trust_level",
    "generate_ca_keypair",
    "sign_attestation",
    "attestation_audit_fields",
    "CORVIN_CA_PUBKEY_HEX",
]
