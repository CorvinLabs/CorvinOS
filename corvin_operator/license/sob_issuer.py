"""Test / development SOB issuer — simulates ``corvin-features.corvin-labs.com`` (ADR-0111).

Used by:
  * ``corvin-register`` CLI (dev mode, ``--self-issue``)
  * Unit and E2E tests
  * Local demos without a running features server

NEVER use in production.  The real SOB must be issued by the CorvinLabs
features server which holds the actual Ed25519 signing private key.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from .sob_crypto import (
    generate_ed25519_keypair,
    generate_x25519_keypair,
    seal_sob,
)

_NONCE_COUNT_DEFAULT = 30   # 30 days offline window
_NONCE_EPOCH_DEFAULT = 1    # increment when server rolls the epoch


class SobIssuer:
    """Issues Sealed Offline Bundles for development and testing.

    Usage::

        issuer = SobIssuer()         # generates ephemeral server keypair
        # or
        issuer = SobIssuer(signing_key_raw=my_priv_bytes)  # deterministic

        sob_bytes = issuer.issue(
            instance_id="abc",
            device_fp="def",
            client_pub_raw=x25519_pub,
            tier="member",
        )
        # The matching server_verify_key_raw is issuer.verify_key_raw
    """

    def __init__(self, signing_key_raw: bytes | None = None) -> None:
        if signing_key_raw is not None:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            priv = Ed25519PrivateKey.from_private_bytes(signing_key_raw)
            self._signing_key_raw = signing_key_raw
            self._verify_key_raw = priv.public_key().public_bytes_raw()
        else:
            self._signing_key_raw, self._verify_key_raw = generate_ed25519_keypair()

    @property
    def verify_key_raw(self) -> bytes:
        """Ed25519 public key — inject into the seal stub via ``_TEST_SERVER_VERIFY_KEY_RAW``."""
        return self._verify_key_raw

    def issue(
        self,
        *,
        instance_id: str,
        device_fp: str,
        client_pub_raw: bytes,
        tier: str = "member",
        limits: dict[str, Any] | None = None,
        features: dict[str, Any] | None = None,
        nonce_count: int = _NONCE_COUNT_DEFAULT,
        nonce_epoch: int = _NONCE_EPOCH_DEFAULT,
        valid_from: float | None = None,
        valid_until: float | None = None,
    ) -> bytes:
        """Issue a signed, encrypted SOB for the given client.

        Args:
            instance_id: UUID4 of the installation.
            device_fp:   Device fingerprint (32-char hex).
            client_pub_raw: Client's X25519 public key (32 bytes).
            tier:        License tier (e.g. "member", "pro").
            limits:      Per-customer limit overrides.  None = tier defaults.
            features:    Feature config dicts.  None = tier defaults.
            nonce_count: Offline validity window in days (default 30).
            nonce_epoch: Monotonic epoch — increment when server rotates.
            valid_from:  Unix timestamp for window start (default: now).
            valid_until: Unix timestamp for window end (default: valid_from + nonce_count days).

        Returns:
            Wire-format SOB bytes.
        """
        now = time.time()
        vf = valid_from if valid_from is not None else now
        vu = valid_until if valid_until is not None else vf + nonce_count * 86400

        nonce_seed = secrets.token_bytes(32).hex()

        plaintext: dict[str, Any] = {
            "schema":      2,
            "instance_id": instance_id,
            "device_fp":   device_fp,
            "tier":        tier,
            "limits":      limits if limits is not None else _default_limits(tier),
            "features":    features if features is not None else _default_features(tier),
            "valid_from":  int(vf),
            "valid_until": int(vu),
            "nonce_seed":  nonce_seed,
            "nonce_count": nonce_count,
            "nonce_epoch": nonce_epoch,
        }

        return seal_sob(
            plaintext,
            server_signing_key_raw=self._signing_key_raw,
            client_pub_raw=client_pub_raw,
        )


# ── Default tier configurations ───────────────────────────────────────────────

def _default_limits(tier: str) -> dict[str, Any]:
    return {
        "free": {
            "compute_units_per_day": 1,
            "a2a_peers_max": 1,
            "workflows_concurrent": 1,
            "tenants_max": 1,
        },
        "member": {
            "compute_units_per_day": None,
            "a2a_peers_max": None,
            "workflows_concurrent": None,
            "tenants_max": None,
            "data_residency": True,
            "audit_export": True,
            "sso_enabled": True,
            "enterprise_portal": True,
        },
        "pro": {
            "compute_units_per_day": 500,
            "a2a_peers_max": 10,
            "workflows_concurrent": 15,
            "tenants_max": 3,
            "data_residency": True,
            "audit_export": True,
        },
    }.get(tier, {"compute_units_per_day": 1})


def _default_features(tier: str) -> dict[str, Any]:
    if tier in ("member", "enterprise"):
        return {
            "data_residency": {"zones": ["eu-west-1"], "strict": True},
            "sso_enabled":    {"provider": "oidc"},
            "audit_export":   {"formats": ["jsonl", "csv"]},
        }
    if tier == "pro":
        return {
            "data_residency": {"zones": ["eu-west-1"], "strict": False},
            "audit_export":   {"formats": ["jsonl"]},
        }
    return {}


# ── Standalone registration helper (dev / corvin-register --self-issue) ───────

def register_local(
    corvin_home: Path,
    *,
    instance_id: str,
    device_fp: str,
    tier: str = "member",
    issuer: SobIssuer | None = None,
) -> tuple[bytes, bytes]:
    """Generate a client X25519 keypair, issue an SOB, and write both to disk.

    Returns (sob_bytes, verify_key_raw) — mainly useful for tests.
    ``verify_key_raw`` must be injected into the seal stub's
    ``_TEST_SERVER_VERIFY_KEY_RAW`` for verification to work in dev mode.
    """
    from .sob_crypto import generate_x25519_keypair
    sub_priv_raw, sub_pub_raw = generate_x25519_keypair()

    iss = issuer or SobIssuer()
    sob_bytes = iss.issue(
        instance_id=instance_id,
        device_fp=device_fp,
        client_pub_raw=sub_pub_raw,
        tier=tier,
    )

    license_dir = corvin_home / "global" / "license"
    license_dir.mkdir(parents=True, exist_ok=True)

    key_path = license_dir / "sub_private.key"
    sob_path = license_dir / "sob.enc"

    # Write private key as 32 raw bytes
    _atomic_write(key_path, sub_priv_raw, mode=0o600)
    _atomic_write(sob_path, sob_bytes, mode=0o600)

    return sob_bytes, iss.verify_key_raw


def _atomic_write(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    """Atomic replace via temp file in the same directory."""
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".sob.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
