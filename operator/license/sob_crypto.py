"""Sealed Offline Bundle — cryptographic primitives (ADR-0111).

This module implements what ``corvin_seal.so`` (Rust) does in production.
All operations are pure-Python so the test suite runs without the
compiled binary.  The API surface is identical to the compiled extension.

Wire format (SOB bytes):
  ephem_pub_x25519 (32 bytes)
  || chacha20_nonce (12 bytes)
  || ciphertext_with_poly1305_tag (N + 16 bytes)

The plaintext is canonical-JSON of the SOB claims dict, plus a
``server_sig`` field added by the issuer.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
from typing import Any

_EPHEM_PUB_LEN = 32
_CHACHA_NONCE_LEN = 12
_HEADER_LEN = _EPHEM_PUB_LEN + _CHACHA_NONCE_LEN
_MIN_SOB_LEN = _HEADER_LEN + 16  # 16 = Poly1305 tag


# ── X25519 / ECDH ─────────────────────────────────────────────────────────────

def generate_x25519_keypair() -> tuple[bytes, bytes]:
    """Return (priv_raw_32, pub_raw_32)."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    priv = X25519PrivateKey.generate()
    return priv.private_bytes_raw(), priv.public_key().public_bytes_raw()


def x25519_exchange(priv_raw: bytes, peer_pub_raw: bytes) -> bytes:
    """ECDH with Curve25519. Returns 32-byte shared secret."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey, X25519PublicKey,
    )
    priv = X25519PrivateKey.from_private_bytes(priv_raw)
    peer = X25519PublicKey.from_public_bytes(peer_pub_raw)
    return priv.exchange(peer)


def hkdf_derive(shared: bytes, info: bytes, length: int = 32) -> bytes:
    """HKDF-SHA256 with empty salt."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info).derive(shared)


# ── ChaCha20-Poly1305 ─────────────────────────────────────────────────────────

def chacha20_encrypt(key: bytes, plaintext: bytes) -> tuple[bytes, bytes]:
    """Encrypt and authenticate. Returns (nonce_12, ciphertext_with_tag)."""
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    nonce = os.urandom(12)
    ct = ChaCha20Poly1305(key).encrypt(nonce, plaintext, b"")
    return nonce, ct


def chacha20_decrypt(key: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    """Decrypt and verify. Raises ``InvalidTag`` on failure."""
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    return ChaCha20Poly1305(key).decrypt(nonce, ciphertext, b"")


# ── Ed25519 ───────────────────────────────────────────────────────────────────

def generate_ed25519_keypair() -> tuple[bytes, bytes]:
    """Return (priv_raw_32, pub_raw_32)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    priv = Ed25519PrivateKey.generate()
    return priv.private_bytes_raw(), priv.public_key().public_bytes_raw()


def ed25519_sign(priv_raw: bytes, message: bytes) -> bytes:
    """Return 64-byte Ed25519 signature."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    return Ed25519PrivateKey.from_private_bytes(priv_raw).sign(message)


def ed25519_verify(pub_raw: bytes, message: bytes, signature: bytes) -> bool:
    """Return True iff signature is valid. Never raises."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        Ed25519PublicKey.from_public_bytes(pub_raw).verify(signature, message)
        return True
    except (InvalidSignature, Exception):  # noqa: BLE001
        return False


# ── Nonce chain ───────────────────────────────────────────────────────────────

def derive_nonce_for_day(nonce_seed: bytes, day_offset: int) -> bytes:
    """Return HMAC-SHA256 chain value at ``day_offset``.

    nonce[0] = nonce_seed
    nonce[i] = HMAC-SHA256(nonce[i-1], i.to_bytes(4, 'little'))
    """
    current = nonce_seed
    for i in range(day_offset):
        current = _hmac.new(current, (i + 1).to_bytes(4, "little"), hashlib.sha256).digest()
    return current


# ── SOB seal / unseal ─────────────────────────────────────────────────────────

def _canonical(d: dict[str, Any]) -> bytes:
    """Deterministic JSON bytes (sorted keys, no whitespace)."""
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def seal_sob(
    plaintext: dict[str, Any],
    *,
    server_signing_key_raw: bytes,
    client_pub_raw: bytes,
) -> bytes:
    """Sign the SOB plaintext and encrypt it for the client.

    Returns wire-format bytes: ephem_pub || nonce || ciphertext.
    """
    # Sign the plaintext (without server_sig field)
    body = {k: v for k, v in plaintext.items() if k != "server_sig"}
    sig = ed25519_sign(server_signing_key_raw, _canonical(body))
    signed = {**body, "server_sig": sig.hex()}

    # ECDH encryption for the client's public key
    ephem_priv_raw, ephem_pub_raw = generate_x25519_keypair()
    shared = x25519_exchange(ephem_priv_raw, client_pub_raw)
    info = (plaintext["instance_id"] + ":" + plaintext["device_fp"]).encode()
    seal_key = hkdf_derive(shared, info)

    nonce, ct = chacha20_encrypt(seal_key, _canonical(signed))
    return ephem_pub_raw + nonce + ct


def unseal_sob(
    sob_bytes: bytes,
    *,
    sub_private_key_raw: bytes,
    server_verify_key_raw: bytes,
    instance_id: str,
    device_fp: str,
    manifest_nonce_epoch: int,
    now: float | None = None,
) -> dict[str, Any] | None:
    """Core unseal logic — mirrors what ``corvin_seal.so`` performs.

    Returns the validated claims dict on success, ``None`` on any failure.
    Never raises.
    """
    import time as _t
    now_ = now if now is not None else _t.time()

    if len(sob_bytes) < _MIN_SOB_LEN:
        return None

    ephem_pub_raw = sob_bytes[:_EPHEM_PUB_LEN]
    chacha_nonce = sob_bytes[_EPHEM_PUB_LEN:_HEADER_LEN]
    ciphertext = sob_bytes[_HEADER_LEN:]

    # ECDH + HKDF
    try:
        shared = x25519_exchange(sub_private_key_raw, ephem_pub_raw)
    except Exception:  # noqa: BLE001
        return None
    info = (instance_id + ":" + device_fp).encode()
    seal_key = hkdf_derive(shared, info)

    # Decrypt
    try:
        plaintext = chacha20_decrypt(seal_key, chacha_nonce, ciphertext)
    except Exception:  # noqa: BLE001
        return None

    # Parse
    try:
        claims: dict[str, Any] = json.loads(plaintext)
    except Exception:  # noqa: BLE001
        return None

    # Verify server signature
    sig_hex = claims.pop("server_sig", None)
    if not isinstance(sig_hex, str):
        return None
    try:
        sig = bytes.fromhex(sig_hex)
    except Exception:  # noqa: BLE001
        return None
    if not ed25519_verify(server_verify_key_raw, _canonical(claims), sig):
        return None
    claims["server_sig"] = sig_hex  # restore for callers

    # Verify binding claims
    if not _hmac.compare_digest(claims.get("instance_id") or "", instance_id or ""):
        return None
    if not _hmac.compare_digest(claims.get("device_fp") or "", device_fp or ""):
        return None

    # Reject SOBs whose epoch is older than what the manifest requires
    if claims.get("nonce_epoch", 0) < manifest_nonce_epoch:
        return None

    # Verify offline time window
    import math as _math
    valid_from = claims.get("valid_from", 0)
    nonce_count = int(claims.get("nonce_count", 0))
    if nonce_count <= 0:
        return None
    # Use floor so negative fractions (e.g. -0.999) correctly give day -1, not 0
    day_offset = _math.floor((now_ - valid_from) / 86400)
    if not (0 <= day_offset < nonce_count):
        return None

    # SOB-CRYPTO-02 (ADR-0146): enforce the issuer-signed absolute expiry.
    # valid_until is part of the Ed25519-signed claims (verified above) but was
    # never read, so absolute license expiry was cosmetic — the only ceiling was
    # valid_from + nonce_count*86400. A SOB at/past its signed valid_until must be
    # rejected. Fail-closed on a malformed value.
    valid_until = claims.get("valid_until")
    if valid_until is not None:
        try:
            if now_ >= float(valid_until):
                return None
        except (TypeError, ValueError):
            return None

    return claims
