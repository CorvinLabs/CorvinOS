"""Python reference implementation of the ``corvin_seal`` binary API (ADR-0111).

This module exposes the same three functions as the compiled Rust extension
(``_corvin_seal.so``).  It is used:

  * During development before the Rust binary is available.
  * In CI / unit tests where importing a native extension is impractical.
  * As a specification — the Rust implementation must match this behaviour
    exactly.

NEVER ship this file as a production license enforcement mechanism.  The
compiled binary (from ``corvinlabs/corvin-seal-private``) is the only
production-grade root of trust.  This file is Apache-2.0 open-source;
the Rust source is not.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .sob_crypto import unseal_sob

# ── Injected test overrides (module-level, for pytest only) ─────────────────
#
# Test code may set these before calling ``unseal()``:
#
#   from operator.license import _corvin_seal_stub as _seal
#   _seal._TEST_SUB_PRIVATE_KEY_RAW = my_priv_bytes
#   _seal._TEST_SERVER_VERIFY_KEY_RAW = my_pub_bytes
#
# Production code must never set these.
_TEST_SUB_PRIVATE_KEY_RAW: bytes | None = None
_TEST_SERVER_VERIFY_KEY_RAW: bytes | None = None

_STUB_VERSION = "0.1.0-stub"

# The compiled binary embeds the server's Ed25519 public verification key.
# This placeholder is replaced by tests via ``_TEST_SERVER_VERIFY_KEY_RAW``.
# In the real Rust binary the key is hardcoded at compile time.
_DEFAULT_SERVER_VERIFY_KEY_RAW: bytes | None = None


def _corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME", "").strip()
    return Path(env) if env else Path.home() / ".corvin"


def _load_sub_private_key() -> bytes | None:
    """Read the client sub-private key from disk. Returns None on failure."""
    if _TEST_SUB_PRIVATE_KEY_RAW is not None:
        return _TEST_SUB_PRIVATE_KEY_RAW
    key_path = _corvin_home() / "global" / "license" / "sub_private.key"
    try:
        raw = key_path.read_bytes().strip()
        # Accept both raw 32 bytes and hex-encoded 64 chars
        if len(raw) == 32:
            return raw
        import base64 as _b64
        try:
            decoded = _b64.b64decode(raw)
            if len(decoded) == 32:
                return decoded
        except Exception:  # noqa: BLE001
            pass
        if len(raw) == 64:
            return bytes.fromhex(raw.decode())
        return None
    except Exception:  # noqa: BLE001
        return None


def _load_server_verify_key() -> bytes | None:
    if _TEST_SERVER_VERIFY_KEY_RAW is not None:
        return _TEST_SERVER_VERIFY_KEY_RAW
    if _DEFAULT_SERVER_VERIFY_KEY_RAW is not None:
        return _DEFAULT_SERVER_VERIFY_KEY_RAW
    # In production the real binary has this compiled in.
    # Without an override this stub cannot verify server signatures.
    return None


def unseal(
    instance_id: str,
    device_fp: str,
    sob_bytes: bytes,
    manifest_nonce_epoch: int,
) -> dict[str, Any] | None:
    """Decrypt and validate a Sealed Offline Bundle.

    Mirrors ``corvin_seal::unseal`` from the Rust extension.
    Returns the claims dict on success, ``None`` on any failure.
    """
    sub_priv = _load_sub_private_key()
    if sub_priv is None:
        return None
    server_pub = _load_server_verify_key()
    if server_pub is None:
        return None
    return unseal_sob(
        sob_bytes,
        sub_private_key_raw=sub_priv,
        server_verify_key_raw=server_pub,
        instance_id=instance_id,
        device_fp=device_fp,
        manifest_nonce_epoch=manifest_nonce_epoch,
    )


def verify_manifest(manifest_json: bytes, sig_bytes: bytes) -> bool:
    """Verify a GitHub trust-manifest RS256 signature.

    Reads the RS256 public key from a2a_network_pubkey.pem alongside this
    module (same key the compiled Rust binary has hardcoded at compile time).
    Fail-closed: returns False if the key is absent, unreadable, or
    verification fails.  ADR-0138 M4 B2: no unconditional True.
    """
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        from cryptography.hazmat.primitives.asymmetric import padding as _pad
        from cryptography.hazmat.primitives import hashes as _hashes
        from cryptography.exceptions import InvalidSignature

        _key_path = Path(__file__).resolve().parent / "a2a_network_pubkey.pem"
        pub = load_pem_public_key(_key_path.read_bytes())
        pub.verify(sig_bytes, manifest_json, _pad.PKCS1v15(), _hashes.SHA256())
        return True
    except Exception:  # noqa: BLE001
        return False


def seal_version() -> str:
    """Return the stub version string."""
    return _STUB_VERSION
